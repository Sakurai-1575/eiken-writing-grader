from __future__ import annotations

import pytest

from eiken_grader.config import load_settings
from eiken_grader.errors import RateLimitedError, SchemaError
from eiken_grader.models.schemas import OcrResult
from eiken_grader.services.image_utils import prepare_image
from eiken_grader.ui import state as S
from eiken_grader.ui.state import K
from tests.conftest import SAMPLE_ANSWER, SAMPLE_QUESTION, make_image_bytes


class FakeClient:
    """generate_json の呼び出し回数を数える偽クライアント。"""

    def __init__(self, ai_output, fail: Exception | None = None):
        self.ai_output = ai_output
        self.fail = fail
        self.calls: list[str] = []

    def generate_json(self, *, parts, schema, system_instruction, temperature):
        self.calls.append(schema.__name__)
        if self.fail:
            raise self.fail
        if schema is OcrResult:
            return OcrResult(text="I thinks {becuase?} it is good.", uncertain_words=["becuase"])
        return self.ai_output


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def ss():
    d: dict = {}
    S.init_state(d)
    return d


@pytest.fixture
def ctx(ss, rubrics, ai_output):
    settings = load_settings()
    client = FakeClient(ai_output)
    clock = Clock()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return client

    ctrl = S.Controller(ss=ss, settings=settings, rubrics=rubrics, client_factory=factory, clock=clock)
    return ctrl, client, clock, factory_calls


def ready_for_grading(ss):
    ss[K.GRADE_ID], ss[K.TASK_ID] = "g2", "opinion"
    ss[K.QUESTION] = SAMPLE_QUESTION
    ss[K.ANSWER] = SAMPLE_ANSWER


def test_init_and_reset(ss):
    ss[K.REPORT] = "something"
    ss[K.ANSWER] = "secret answer"
    ss[K.IMAGES] = [b"x"]
    ss["prep_cache"] = {"a": 1}
    ss["w_answer"] = "x"
    ss[K.AUTHED] = True
    S.reset_session(ss)
    assert ss[K.REPORT] is None and ss[K.ANSWER] == "" and ss[K.IMAGES] == []
    assert "prep_cache" not in ss and "w_answer" not in ss
    assert ss[K.AUTHED] is True  # 認証状態のみ保持
    assert ss[K.UPLOADER_NONCE] == 1  # アップローダーを作り直して画像を破棄
    assert ss[K.STEP] == S.STEP_SETUP


def test_double_click_runs_api_once(ss, ctx):
    ctrl, client, _, _ = ctx
    ready_for_grading(ss)
    assert S.request_action(ss, S.ACTION_GRADE) is True
    assert S.request_action(ss, S.ACTION_GRADE) is False  # 処理中の 2 回目は無視
    assert ss[K.IS_BUSY] is True
    ctrl.run_pending()
    ctrl.run_pending()  # 保留がなければ何もしない
    assert client.calls == ["AiGradingOutput"]
    assert ss[K.IS_BUSY] is False and ss[K.PENDING] is None
    assert ss[K.STEP] == S.STEP_RESULT


def test_grading_builds_downloads_without_extra_api_calls(ss, ctx):
    ctrl, client, _, _ = ctx
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert ss[K.PDF_BYTES].startswith(b"%PDF")
    assert ss[K.MD_TEXT].startswith("# 英検ライティング")
    # ダウンロード用データの再生成も API を呼ばない
    S.build_downloads(ss, ss[K.REPORT])
    assert client.calls == ["AiGradingOutput"]


def test_same_input_uses_cache(ss, ctx):
    ctrl, client, clock, factory_calls = ctx
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    first = ss[K.REPORT]
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()  # クールダウン中でもキャッシュならエラーにならない
    assert ss[K.REPORT] is first
    assert client.calls == ["AiGradingOutput"]
    assert len(factory_calls) == 1
    assert "再送信は行っていません" in ss[K.NOTICE]


def test_cooldown_blocks_new_api_call(ss, ctx):
    ctrl, client, clock, _ = ctx
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    ss[K.ANSWER] = SAMPLE_ANSWER + " Another sentence."
    clock.t += 3
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert "秒お待ちください" in ss[K.ERROR]
    assert len(client.calls) == 1
    clock.t += 10
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert len(client.calls) == 2 and ss[K.ERROR] is None


def test_busy_flag_released_after_error(ss, rubrics, ai_output):
    client = FakeClient(ai_output, fail=SchemaError())
    ctrl = S.Controller(ss=ss, settings=load_settings(), rubrics=rubrics, client_factory=lambda: client)
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert ss[K.IS_BUSY] is False and ss[K.PENDING] is None
    assert ss[K.ERROR] == SchemaError.default_message
    assert ss[K.STEP] == S.STEP_SETUP  # 画面は遷移しない


def test_busy_flag_released_after_unexpected_exception(ss, rubrics, ai_output):
    client = FakeClient(ai_output, fail=RuntimeError("boom"))
    ctrl = S.Controller(ss=ss, settings=load_settings(), rubrics=rubrics, client_factory=lambda: client)
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert ss[K.IS_BUSY] is False
    assert "boom" not in ss[K.ERROR]


def test_rate_limited_error_shown(ss, rubrics, ai_output):
    client = FakeClient(ai_output, fail=RateLimitedError("混雑中"))
    ctrl = S.Controller(ss=ss, settings=load_settings(), rubrics=rubrics, client_factory=lambda: client)
    ready_for_grading(ss)
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert ss[K.ERROR] == "混雑中"


def test_empty_answer_does_not_call_api(ss, ctx):
    ctrl, client, _, factory_calls = ctx
    ready_for_grading(ss)
    ss[K.ANSWER] = "   "
    S.request_action(ss, S.ACTION_GRADE)
    ctrl.run_pending()
    assert client.calls == [] and factory_calls == []
    assert "答案が空" in ss[K.ERROR]


def test_ocr_flow_and_cache(ss, ctx):
    ctrl, client, clock, _ = ctx
    ss[K.IMAGES] = [prepare_image(make_image_bytes())]
    S.request_action(ss, S.ACTION_OCR)
    ctrl.run_pending()
    assert ss[K.STEP] == S.STEP_REVIEW
    assert ss[K.ANSWER] == ss[K.W_ANSWER] == "I thinks {becuase?} it is good."
    # 同じ画像の再読み取りは API を呼ばない
    S.request_action(ss, S.ACTION_OCR)
    ctrl.run_pending()
    assert client.calls == ["OcrResult"]


def test_ocr_without_images(ss, ctx):
    ctrl, client, _, _ = ctx
    S.request_action(ss, S.ACTION_OCR)
    ctrl.run_pending()
    assert client.calls == [] and "画像" in ss[K.ERROR]


def test_cooldown_remaining():
    ss = {K.LAST_CALL_AT: 100.0}
    assert S.cooldown_remaining(ss, 10, 105) == 5
    assert S.cooldown_remaining(ss, 10, 200) == 0
    assert S.cooldown_remaining({}, 10, 0) == 0


def test_sync_widget_does_not_overwrite(ss):
    ss[K.QUESTION] = "stored"
    S.sync_widget(ss, K.QUESTION, K.W_QUESTION)
    assert ss[K.W_QUESTION] == "stored"
    ss[K.W_QUESTION] = "typing"
    S.sync_widget(ss, K.QUESTION, K.W_QUESTION)
    assert ss[K.W_QUESTION] == "typing"
