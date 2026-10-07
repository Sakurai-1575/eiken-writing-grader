"""モデルのフォールバックと、開発モードの詳細エラー出力のテスト。"""

from __future__ import annotations

import logging

import pytest
import responses

from eiken_grader.config import AppSettings, DebugSettings, GeminiSettings, is_debug_mode, load_settings
from eiken_grader.core.gemini_client import GeminiClient
from eiken_grader.errors import ApiError, RateLimitedError, SchemaError
from eiken_grader.services.grading import grade_answer
from eiken_grader.ui import state as S
from eiken_grader.ui.state import K
from tests.conftest import SAMPLE_ANSWER, SAMPLE_QUESTION, gemini_response
from tests.test_gemini_client import Simple, call

API_KEY = "test-secret-key-123"
PRIMARY = "gemini-2.5-flash"
FALLBACK = "gemini-2.5-flash-lite"


@pytest.fixture
def settings() -> GeminiSettings:
    return GeminiSettings(model=PRIMARY, fallback_models=[FALLBACK], max_retries=1, backoff_base_sec=0)


def url(s: GeminiSettings, model: str) -> str:
    return f"{s.api_base}/models/{model}:generateContent"


def client(s: GeminiSettings) -> GeminiClient:
    return GeminiClient(API_KEY, s, sleep=lambda _: None)


def overloaded() -> dict:
    return {"error": {"code": 503, "status": "UNAVAILABLE", "message": "The model is overloaded."}}


# --- 設定 -------------------------------------------------------------------
def test_settings_file_default_and_fallback():
    g = load_settings().gemini
    assert g.model_chain == ["gemini-flash-latest", "gemini-3.5-flash-lite"]
    # 新規利用者に提供終了（404）済みのモデルは使わない
    for retired in ("gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        assert retired not in g.model_chain
    assert g.max_retries == 1  # 混雑時は早めにフォールバック
    assert g.backoff_base_sec == 3.0 and g.backoff_max_sec == 20.0


def test_model_chain_dedupes_and_secret_override_keeps_fallbacks():
    g = GeminiSettings(model="a", fallback_models=["b", "a", " ", "c"])
    assert g.model_chain == ["a", "b", "c"]
    s = load_settings(secrets={"GEMINI_MODEL": "gemini-x"})
    assert s.gemini.model_chain == ["gemini-x", "gemini-3.5-flash-lite"]


# --- フォールバック -----------------------------------------------------------
@responses.activate
def test_503_falls_back_to_next_model(settings):
    responses.post(url(settings, PRIMARY), status=503, json=overloaded())
    responses.post(url(settings, FALLBACK), json=gemini_response({"value": "lite", "n": 1}))
    c = client(settings)
    assert call(c).value == "lite"
    assert c.last_model == FALLBACK
    called = [r.request.url for r in responses.calls]
    assert called.count(url(settings, PRIMARY)) == 2  # 主モデルで再試行してから切替
    assert called[-1] == url(settings, FALLBACK)


@responses.activate
def test_primary_success_does_not_touch_fallback(settings):
    responses.post(url(settings, PRIMARY), json=gemini_response({"value": "main", "n": 1}))
    c = client(settings)
    assert call(c).value == "main"
    assert c.last_model == PRIMARY
    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.parametrize("status", [429, 500, 404])
def test_other_unavailable_statuses_fall_back(settings, status):
    responses.post(url(settings, PRIMARY), status=status, json={"error": {"message": "x"}})
    responses.post(url(settings, FALLBACK), json=gemini_response({"value": "ok", "n": 1}))
    assert call(client(settings)).value == "ok"


@responses.activate
@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_do_not_fall_back(settings, status):
    responses.post(url(settings, PRIMARY), status=status, json={"error": {"message": "bad"}})
    with pytest.raises(ApiError):
        call(client(settings))
    assert all(r.request.url == url(settings, PRIMARY) for r in responses.calls)
    assert len(responses.calls) == 1


@responses.activate
def test_all_models_overloaded_reports_every_attempt(settings):
    responses.post(url(settings, PRIMARY), status=503, json=overloaded())
    responses.post(url(settings, FALLBACK), status=503, json=overloaded())
    with pytest.raises(ApiError, match="混雑") as ei:
        call(client(settings))
    assert ei.value.status_code == 503
    detail = ei.value.debug_detail
    assert f"[{PRIMARY}]" in detail and f"[{FALLBACK}]" in detail
    assert "UNAVAILABLE" in detail and "overloaded" in detail
    assert len(responses.calls) == 4  # (1 + 再試行 1) × 2 モデル


@responses.activate
def test_timeout_falls_back(settings):
    import requests

    responses.post(url(settings, PRIMARY), body=requests.Timeout())
    responses.post(url(settings, FALLBACK), json=gemini_response({"value": "ok", "n": 1}))
    assert call(client(settings)).value == "ok"


@responses.activate
def test_schema_error_does_not_fall_back(settings):
    responses.post(url(settings, PRIMARY), json=gemini_response({"value": "only"}))
    with pytest.raises(SchemaError) as ei:
        call(client(settings))
    assert len(responses.calls) == 1
    assert "validation errors" in ei.value.debug_detail and "n: Field required" in ei.value.debug_detail


@responses.activate
def test_backoff_wait_times(settings):
    s = settings.model_copy(update={"backoff_base_sec": 3.0, "backoff_max_sec": 20.0, "max_retries": 2})
    responses.post(url(s, PRIMARY), status=503, json=overloaded())
    responses.post(url(s, FALLBACK), json=gemini_response({"value": "ok", "n": 1}))
    waits: list[float] = []
    call(GeminiClient(API_KEY, s, sleep=waits.append))
    assert len(waits) == 2
    assert 3.0 <= waits[0] <= 4.0 and 6.0 <= waits[1] <= 7.0


@responses.activate
def test_report_records_model_actually_used(settings, rubrics, ai_output_json):
    responses.post(url(settings, PRIMARY), status=503, json=overloaded())
    responses.post(url(settings, FALLBACK), json=gemini_response(ai_output_json))
    grade, task = rubrics.get_task("g2", "opinion")
    report = grade_answer(
        client(settings), model_name=PRIMARY, grade=grade, task=task, score_levels=rubrics.score_levels,
        question=SAMPLE_QUESTION, answer=SAMPLE_ANSWER,
    )
    assert report.model == FALLBACK


# --- エラー詳細 ---------------------------------------------------------------
@responses.activate
def test_error_detail_has_status_model_and_google_message(caplog):
    s = GeminiSettings(max_retries=0)
    responses.post(url(s, s.model), status=403,
                   json={"error": {"status": "PERMISSION_DENIED", "message": f"API key {API_KEY} invalid"}})
    with caplog.at_level(logging.WARNING, logger="eiken_grader"):
        with pytest.raises(ApiError) as ei:
            call(client(s))
    detail = ei.value.debug_detail
    assert "HTTP 403 PERMISSION_DENIED" in detail and f"model={s.model}" in detail
    assert API_KEY not in detail and "***" in detail  # キーはマスク
    assert "HTTP 403" in caplog.text and API_KEY not in caplog.text


@responses.activate
def test_retry_and_status_logged_to_console(settings, caplog):
    responses.post(url(settings, PRIMARY), status=503, json=overloaded())
    responses.post(url(settings, FALLBACK), json=gemini_response({"value": "ok", "n": 1}))
    with caplog.at_level(logging.INFO, logger="eiken_grader"):
        call(client(settings))
    assert "status=503" in caplog.text
    assert "UNAVAILABLE -> retry" in caplog.text
    assert f"falling back to {FALLBACK}" in caplog.text
    assert "fallback succeeded" in caplog.text


def test_rate_guard_error_has_detail():
    from eiken_grader.core.rate_guard import RateGuard

    s = GeminiSettings()
    guard = RateGuard(rpm=1, rpd=1, clock=lambda: 0.0)
    guard.try_acquire()
    with pytest.raises(RateLimitedError) as ei:
        GeminiClient("k", s, guard=guard).generate_json(
            parts=[], schema=Simple, system_instruction="", temperature=0
        )
    assert "RateGuard denied" in ei.value.debug_detail


# --- 開発モード判定 -----------------------------------------------------------
def test_debug_mode_auto_local_vs_cloud():
    s = AppSettings()
    assert is_debug_mode(s, {}, cwd="C:\\desktop\\app") is True
    assert is_debug_mode(s, {}, cwd="/mount/src/eiken-app") is False


def test_debug_mode_explicit_and_secret_override():
    on = AppSettings(debug=DebugSettings(mode="on"))
    off = AppSettings(debug=DebugSettings(mode="off"))
    assert is_debug_mode(on, {}, cwd="/mount/src/x") is True
    assert is_debug_mode(off, {}, cwd="C:\\x") is False
    assert is_debug_mode(off, {"DEBUG": True}) is True
    assert is_debug_mode(on, {"DEBUG": False}) is False


# --- コントローラーの詳細保存 ---------------------------------------------------
class FailingClient:
    def __init__(self, exc):
        self.exc = exc

    def generate_json(self, **kw):
        raise self.exc


def run_grade(rubrics, exc, debug):
    ss: dict = {}
    S.init_state(ss)
    ss.update({K.GRADE_ID: "g2", K.TASK_ID: "opinion", K.QUESTION: "Q", K.ANSWER: "A b c"})
    ctrl = S.Controller(ss=ss, settings=load_settings(), rubrics=rubrics,
                        client_factory=lambda: FailingClient(exc), debug=debug)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    return ss


def test_controller_stores_detail_only_in_debug(rubrics, caplog):
    err = ApiError("混雑", 503, debug_detail="HTTP 503 UNAVAILABLE model=m: overloaded")
    with caplog.at_level(logging.ERROR, logger="eiken_grader"):
        ss = run_grade(rubrics, err, debug=True)
    assert ss[K.ERROR] == "混雑"
    assert ss[K.ERROR_DETAIL] == "ApiError / HTTP 503 / HTTP 503 UNAVAILABLE model=m: overloaded"
    assert "overloaded" in caplog.text and "Traceback" in caplog.text

    ss = run_grade(rubrics, ApiError("混雑", 503, debug_detail="secret detail"), debug=False)
    assert ss[K.ERROR] == "混雑" and ss[K.ERROR_DETAIL] is None


def test_unexpected_error_traceback(rubrics, caplog):
    with caplog.at_level(logging.ERROR, logger="eiken_grader"):
        ss = run_grade(rubrics, KeyError("boom"), debug=True)
    assert "KeyError" in ss[K.ERROR_DETAIL] and "Traceback" in ss[K.ERROR_DETAIL]
    assert "Unexpected error" in caplog.text
    ss = run_grade(rubrics, KeyError("boom"), debug=False)
    assert ss[K.ERROR_DETAIL] is None
    assert "boom" not in ss[K.ERROR]


def test_error_detail_cleared_on_next_action(rubrics):
    ss = run_grade(rubrics, ApiError("x", debug_detail="d"), debug=True)
    assert ss[K.ERROR_DETAIL]
    S.go(ss, S.STEP_SETUP)
    assert ss[K.ERROR] is None and ss[K.ERROR_DETAIL] is None
