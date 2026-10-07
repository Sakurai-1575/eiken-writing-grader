from __future__ import annotations

import json
from unittest import mock

import pytest
import requests
import responses
from pydantic import BaseModel

from eiken_grader.config import GeminiSettings
from eiken_grader.core.gemini_client import NO_PROXY, GeminiClient, build_session, to_gemini_schema
from eiken_grader.core.rate_guard import RateGuard
from eiken_grader.errors import ApiError, RateLimitedError, SchemaError
from eiken_grader.models.schemas import AiGradingOutput
from tests.conftest import gemini_response

API_KEY = "test-secret-key-123"


class Simple(BaseModel):
    value: str
    n: int


def endpoint(settings: GeminiSettings) -> str:
    return f"{settings.api_base}/models/{settings.model}:generateContent"


def make_client(settings, **kw) -> GeminiClient:
    return GeminiClient(API_KEY, settings, sleep=lambda s: None, **kw)


def call(client):
    return client.generate_json(
        parts=[{"text": "hi"}], schema=Simple, system_instruction="sys", temperature=0.0
    )


# --- プロキシ回避 ------------------------------------------------------------
def test_session_ignores_environment_proxies():
    s = build_session()
    assert s.trust_env is False
    assert s.proxies == {"http": None, "https": None}


def test_no_proxy_constant():
    assert NO_PROXY == {"http": None, "https": None}


def test_post_receives_explicit_no_proxy(gemini_settings):
    session = mock.Mock(spec=requests.Session)
    resp = mock.Mock(status_code=200)
    resp.json.return_value = gemini_response({"value": "ok", "n": 1})
    session.post.return_value = resp
    client = GeminiClient(API_KEY, gemini_settings, session=session)
    assert call(client).value == "ok"
    kwargs = session.post.call_args.kwargs
    assert kwargs["proxies"] == {"http": None, "https": None}
    assert kwargs["timeout"] == gemini_settings.timeout_sec


@responses.activate
def test_env_proxy_is_not_used(monkeypatch, gemini_settings):
    # 到達不能なプロキシを環境変数に設定しても、直接通信されること
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "direct", "n": 2}))
    assert call(make_client(gemini_settings)).value == "direct"


# --- リクエスト内容 ----------------------------------------------------------
@responses.activate
def test_api_key_sent_in_header_not_url(gemini_settings):
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "x", "n": 1}))
    call(make_client(gemini_settings))
    req = responses.calls[0].request
    assert req.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in req.url
    assert "key=" not in req.url


@responses.activate
def test_payload_structure(gemini_settings):
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "x", "n": 1}))
    call(make_client(gemini_settings))
    body = json.loads(responses.calls[0].request.body)
    gc = body["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert gc["responseJsonSchema"]["properties"]["value"]["type"] == "string"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0]["parts"] == [{"text": "hi"}]


@responses.activate
def test_openapi_schema_mode_uses_response_schema():
    settings = GeminiSettings(schema_mode="openapi")
    responses.post(endpoint(settings), json=gemini_response({"value": "x", "n": 1}))
    call(make_client(settings))
    gc = json.loads(responses.calls[0].request.body)["generationConfig"]
    assert "responseJsonSchema" not in gc
    assert gc["responseSchema"]["type"] == "OBJECT"


def test_model_name_used_in_endpoint():
    client = make_client(GeminiSettings(model="gemini-2.5-flash"))
    assert client.endpoint.endswith("/models/gemini-2.5-flash:generateContent")


# --- スキーマ変換 ------------------------------------------------------------
def test_to_gemini_schema_inlines_refs_and_strips_titles():
    schema = to_gemini_schema(AiGradingOutput)
    dumped = json.dumps(schema, ensure_ascii=False)
    assert "$ref" not in dumped and "$defs" not in dumped
    assert '"title"' not in dumped and '"default"' not in dumped
    item = schema["properties"]["scores"]["items"]
    assert item["type"] == "object"
    assert set(item["required"]) >= {"criterion", "score", "rationale"}
    category = schema["properties"]["corrections"]["items"]["properties"]["category"]
    assert category["enum"] == ["文法", "語彙", "スペル", "構成", "その他"]


# --- 応答の解析 --------------------------------------------------------------
@responses.activate
def test_code_fenced_json_is_accepted(gemini_settings):
    responses.post(endpoint(gemini_settings), json=gemini_response('```json\n{"value": "f", "n": 3}\n```'))
    assert call(make_client(gemini_settings)).n == 3


@responses.activate
def test_thought_parts_are_ignored(gemini_settings):
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"value":"a","n":1}'}]},
                "finishReason": "STOP",
            }
        ]
    }
    responses.post(endpoint(gemini_settings), json=body)
    assert call(make_client(gemini_settings)).value == "a"


@responses.activate
def test_invalid_json_raises_schema_error_without_retry(gemini_settings):
    responses.post(endpoint(gemini_settings), json=gemini_response("not json at all"))
    with pytest.raises(SchemaError):
        call(make_client(gemini_settings))
    assert len(responses.calls) == 1  # API を再度呼ばない（無料枠の節約）


@responses.activate
def test_schema_mismatch_raises_schema_error_without_retry(gemini_settings):
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "x"}))
    with pytest.raises(SchemaError):
        call(make_client(gemini_settings))
    assert len(responses.calls) == 1


@responses.activate
def test_max_tokens_empty_raises_schema_error(gemini_settings):
    body = {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
    responses.post(endpoint(gemini_settings), json=body)
    with pytest.raises(SchemaError, match="長すぎ"):
        call(make_client(gemini_settings))


@responses.activate
@pytest.mark.parametrize(
    "body",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
    ],
)
def test_safety_block_raises_api_error(gemini_settings, body):
    responses.post(endpoint(gemini_settings), json=body)
    with pytest.raises(ApiError, match="安全基準"):
        call(make_client(gemini_settings))


# --- リトライ・エラー分類 ----------------------------------------------------
@responses.activate
def test_retry_on_429_then_success(gemini_settings):
    url = endpoint(gemini_settings)
    responses.post(url, status=429, json={"error": {"message": "quota"}})
    responses.post(url, json=gemini_response({"value": "ok", "n": 1}))
    sleeps: list[float] = []
    client = GeminiClient(API_KEY, gemini_settings, sleep=sleeps.append)
    assert call(client).value == "ok"
    assert len(responses.calls) == 2
    assert len(sleeps) == 1


@responses.activate
def test_retry_after_header_respected():
    settings = GeminiSettings(max_retries=1, backoff_max_sec=30)
    url = endpoint(settings)
    responses.post(url, status=503, headers={"Retry-After": "7"})
    responses.post(url, json=gemini_response({"value": "ok", "n": 1}))
    sleeps: list[float] = []
    call(GeminiClient(API_KEY, settings, sleep=sleeps.append))
    assert sleeps == [7.0]


@responses.activate
def test_repeated_429_raises_rate_limited(gemini_settings):
    responses.post(endpoint(gemini_settings), status=429, json={"error": {"message": "quota"}})
    with pytest.raises(RateLimitedError):
        call(make_client(gemini_settings))
    assert len(responses.calls) == gemini_settings.max_retries + 1


@responses.activate
@pytest.mark.parametrize(
    ("status", "pattern"),
    [(400, "不正"), (403, "API キー"), (404, "gemini-flash-latest"), (500, "一時的")],
)
def test_error_classification(status, pattern):
    settings = GeminiSettings(max_retries=0)
    responses.post(endpoint(settings), status=status, json={"error": {"message": "boom"}})
    with pytest.raises(ApiError, match=pattern) as ei:
        call(make_client(settings))
    assert ei.value.status_code == status
    assert len(responses.calls) == 1  # 4xx は再試行しない / 再試行 0 回設定


@responses.activate
def test_api_key_never_in_error_message():
    settings = GeminiSettings(max_retries=0)
    responses.post(endpoint(settings), status=400, json={"error": {"message": f"bad key {API_KEY}"}})
    with pytest.raises(ApiError) as ei:
        call(make_client(settings))
    assert API_KEY not in ei.value.user_message
    assert API_KEY not in str(ei.value)


@responses.activate
def test_timeout_retried_once_then_error():
    settings = GeminiSettings(max_retries=1)
    responses.post(endpoint(settings), body=requests.Timeout())
    with pytest.raises(ApiError, match="タイムアウト"):
        call(make_client(settings))
    assert len(responses.calls) == 2


@responses.activate
def test_connection_error(gemini_settings):
    responses.post(endpoint(gemini_settings), body=requests.ConnectionError("nope"))
    with pytest.raises(ApiError, match="接続"):
        call(make_client(gemini_settings))


# --- レートガード連携 --------------------------------------------------------
@responses.activate
def test_rate_guard_blocks_before_http(gemini_settings):
    guard = RateGuard(rpm=1, rpd=100, clock=lambda: 0.0)
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "x", "n": 1}))
    client = make_client(gemini_settings, guard=guard)
    call(client)
    with pytest.raises(RateLimitedError, match="秒後"):
        call(client)
    assert len(responses.calls) == 1  # 2 回目は HTTP を送らない


@responses.activate
def test_rate_guard_daily_limit_message(gemini_settings):
    guard = RateGuard(rpm=100, rpd=1, clock=lambda: 0.0)
    responses.post(endpoint(gemini_settings), json=gemini_response({"value": "x", "n": 1}))
    client = make_client(gemini_settings, guard=guard)
    call(client)
    with pytest.raises(RateLimitedError, match="本日"):
        call(client)


@pytest.mark.live
def test_live_gemini_roundtrip():  # pragma: no cover - 手動実行専用（pytest -m live）
    import tomllib
    from pathlib import Path

    secrets = tomllib.loads(Path(".streamlit/secrets.toml").read_text(encoding="utf-8"))
    from eiken_grader.config import load_settings

    settings = load_settings(secrets=secrets)
    client = GeminiClient(secrets["GEMINI_API_KEY"], settings.gemini)
    out = client.generate_json(
        parts=[{"text": 'Return value="pong" and n=1.'}],
        schema=Simple,
        system_instruction="Return JSON only.",
        temperature=0.0,
    )
    assert out.n == 1
