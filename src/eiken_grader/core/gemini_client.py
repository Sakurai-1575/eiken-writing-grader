"""Google AI Studio（Gemini API）の REST クライアント。

設計上の要点:
- SDK ではなく ``requests`` で REST API を直接呼ぶ。
- Windows のプロキシ設定（環境変数・レジストリ）を確実に回避するため、
  ``Session.trust_env = False`` と ``proxies={"http": None, "https": None}`` を併用し、
  さらに各リクエストでも ``proxies=NO_PROXY`` を明示する。
- API キーは URL ではなく ``x-goog-api-key`` ヘッダで送る。例外メッセージには含めない。
- 再試行は 429 / 5xx / タイムアウトのみ。応答のスキーマ不一致では再試行しない（無料枠の節約）。
- 同じモデルで再試行しても混雑が解消しない場合は、``fallback_models`` の別モデルに切り替える。
- エラーには開発者向けの ``debug_detail``（HTTP ステータス・Google のエラー本文等）を付与する。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from eiken_grader.config import GeminiSettings
from eiken_grader.core.rate_guard import RateGuard
from eiken_grader.errors import ApiError, AppError, RateLimitedError, SchemaError

logger = logging.getLogger(__name__)

NO_PROXY: dict[str, str | None] = {"http": None, "https": None}
# 同じモデルで再試行するステータス
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 再試行しても失敗したとき、次のモデルへ切り替えるステータス（404 = モデル未提供）
FALLBACK_STATUS = RETRYABLE_STATUS | {404}
DETAIL_MAX = 500

T = TypeVar("T", bound=BaseModel)


def build_session() -> requests.Session:
    """プロキシを一切使わない HTTP セッションを生成する。"""
    session = requests.Session()
    session.trust_env = False  # HTTP(S)_PROXY 環境変数や Windows のプロキシ設定を読まない
    session.proxies = dict(NO_PROXY)
    return session


# ---------------------------------------------------------------------------
# Pydantic → Gemini 用スキーマ変換
# ---------------------------------------------------------------------------
_ALLOWED_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "description",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "format",
    "nullable",
    "anyOf",
}


def to_gemini_schema(model: type[BaseModel], mode: str = "json_schema") -> dict[str, Any]:
    """Pydantic モデルの JSON Schema を Gemini が受け付ける形に整形する。

    - ``$ref`` / ``$defs`` をインライン展開する
    - ``title`` / ``default`` など非対応キーを除去する
    - mode="openapi"（responseSchema 用）では type を大文字にする
    """
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _ALLOWED_SCHEMA_KEYS:
                continue
            if key == "properties":
                out[key] = {name: resolve(prop) for name, prop in value.items()}
            elif key in ("items", "anyOf"):
                out[key] = resolve(value)
            elif key == "type" and mode == "openapi" and isinstance(value, str):
                out[key] = value.upper()
            else:
                out[key] = value
        return out

    return resolve(raw)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


# ---------------------------------------------------------------------------
# クライアント本体
# ---------------------------------------------------------------------------
class _TryNextModel(Exception):
    """このモデルは現在使えない（混雑・枠超過・未提供）。次のモデルで再試行してよい。"""

    def __init__(self, error: AppError) -> None:
        super().__init__(error.user_message)
        self.error = error


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        settings: GeminiSettings,
        session: requests.Session | None = None,
        guard: RateGuard | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self.settings = settings
        self._session = session or build_session()
        self._guard = guard
        self._sleep = sleep
        self.last_model: str | None = None  # 直近の成功時に実際に使われたモデル

    @property
    def model(self) -> str:
        return self.settings.model

    def endpoint_for(self, model: str) -> str:
        return f"{self.settings.api_base}/models/{model}:generateContent"

    @property
    def endpoint(self) -> str:
        return self.endpoint_for(self.settings.model)

    # -- public -------------------------------------------------------------
    def generate_json(
        self,
        *,
        parts: list[dict[str, Any]],
        schema: type[T],
        system_instruction: str,
        temperature: float,
    ) -> T:
        """generateContent を呼び、応答 JSON を ``schema`` で検証して返す。

        主モデルが混雑等で使えない場合は ``fallback_models`` を順に試す。
        """
        payload = self._build_payload(parts, schema, system_instruction, temperature)
        chain = self.settings.model_chain
        failures: list[str] = []
        for i, model in enumerate(chain):
            try:
                body = self._post_with_retry(model, payload)
            except _TryNextModel as e:
                failures.append(f"[{model}] {e.error.debug_detail or e.error.user_message}")
                if i + 1 < len(chain):
                    logger.warning("Gemini model %s unavailable -> falling back to %s", model, chain[i + 1])
                    continue
                e.error.debug_detail = " | ".join(failures)
                raise e.error from None
            self.last_model = model
            if failures:
                logger.info("Gemini fallback succeeded with %s (failed: %s)", model, ", ".join(chain[:i]))
            text = self._extract_text(body, model)
            return self._validate(schema, text, model)
        raise ApiError()  # pragma: no cover - chain は必ず 1 件以上

    # -- internals ----------------------------------------------------------
    def _validate(self, schema: type[T], text: str, model: str) -> T:
        try:
            return schema.model_validate_json(_strip_code_fence(text))
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
            )
            detail = f"model={model} schema={schema.__name__} validation errors ({e.error_count()}): {problems}"
        except (json.JSONDecodeError, ValueError) as e:
            detail = f"model={model} schema={schema.__name__} invalid JSON: {type(e).__name__}: {e}"
        logger.warning("Gemini response failed schema validation: %s", detail)
        # 応答本文には答案の内容が含まれ得るため DEBUG レベル（開発モードのみ出力）
        logger.debug("Gemini raw response head: %s", text[:DETAIL_MAX])
        raise SchemaError(debug_detail=f"{detail} / response head: {text[:300]!r}")

    def _build_payload(
        self,
        parts: list[dict[str, Any]],
        schema: type[BaseModel],
        system_instruction: str,
        temperature: float,
    ) -> dict[str, Any]:
        mode = self.settings.schema_mode
        schema_key = "responseJsonSchema" if mode == "json_schema" else "responseSchema"
        return {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": self.settings.max_output_tokens,
                "responseMimeType": "application/json",
                schema_key: to_gemini_schema(schema, mode),
            },
        }

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

    def _mask(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _acquire(self) -> None:
        if self._guard is None:
            return
        result = self._guard.try_acquire()
        if not result.ok:
            wait = max(1, int(result.wait_seconds + 0.999))
            if result.reason == "rpd":
                msg = "本日の利用上限に達しました。明日以降に再度お試しください。"
            else:
                msg = f"現在アクセスが集中しています。約 {wait} 秒後に再試行してください。"
            raise RateLimitedError(
                msg, retry_after=wait, debug_detail=f"app RateGuard denied ({result.reason}), wait={wait}s"
            )

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.settings.backoff_max_sec)
            except ValueError:
                pass
        base = self.settings.backoff_base_sec * (2**attempt)
        return min(base + random.uniform(0, 1), self.settings.backoff_max_sec)

    def _post_with_retry(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        max_retries = self.settings.max_retries
        url = self.endpoint_for(model)
        for attempt in range(max_retries + 1):
            self._acquire()
            started = time.monotonic()
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.settings.timeout_sec,
                    proxies=NO_PROXY,
                )
            except requests.Timeout:
                logger.warning(
                    "Gemini %s timed out after %.0fs (attempt %d/%d)",
                    model,
                    self.settings.timeout_sec,
                    attempt + 1,
                    max_retries + 1,
                )
                if attempt < max_retries:
                    continue
                raise _TryNextModel(
                    ApiError(
                        "AI の応答がタイムアウトしました。時間をおいて再試行してください。",
                        debug_detail=(
                            f"model={model} timeout after {self.settings.timeout_sec}s x{max_retries + 1} attempts"
                        ),
                    )
                ) from None
            except requests.RequestException as e:
                detail = self._mask(f"model={model} {type(e).__name__}: {e}")[:DETAIL_MAX]
                logger.warning("Gemini connection error: %s", detail)
                raise ApiError(
                    "AI サービスに接続できませんでした。ネットワーク接続を確認してください。",
                    debug_detail=detail,
                ) from None

            logger.info(
                "Gemini %s status=%s elapsed=%.1fs attempt=%d/%d",
                model,
                resp.status_code,
                time.monotonic() - started,
                attempt + 1,
                max_retries + 1,
            )
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    raise SchemaError(debug_detail=f"model={model} HTTP 200 but body is not JSON") from None
            if resp.status_code in RETRYABLE_STATUS and attempt < max_retries:
                wait = self._backoff(attempt, resp.headers.get("Retry-After"))
                logger.warning(
                    "Gemini %s HTTP %s %s -> retry in %.1fs",
                    model,
                    resp.status_code,
                    self._google_error(resp)[1] or "",
                    wait,
                )
                self._sleep(wait)
                continue
            error = self._error_from_response(resp, model)
            if resp.status_code in FALLBACK_STATUS:
                raise _TryNextModel(error)
            raise error
        raise ApiError()  # pragma: no cover - ループは必ず return / raise で抜ける

    def _google_error(self, resp: requests.Response) -> tuple[str, str]:
        """(Google のエラーメッセージ, エラー種別 例: UNAVAILABLE / RESOURCE_EXHAUSTED)"""
        try:
            err = resp.json().get("error", {}) or {}
            return self._mask(str(err.get("message", "")))[:DETAIL_MAX], str(err.get("status", ""))
        except ValueError:
            return self._mask(resp.text or "")[:DETAIL_MAX], ""

    def _error_from_response(self, resp: requests.Response, model: str) -> AppError:
        status = resp.status_code
        message, kind = self._google_error(resp)
        detail = f"HTTP {status}{' ' + kind if kind else ''} model={model}: {message or '(no message)'}"
        logger.warning("Gemini error %s", detail)
        if status == 429:
            return RateLimitedError(
                "AI の無料利用枠の上限に達しました。1 分ほど待ってから再試行してください。",
                status_code=status,
                debug_detail=detail,
            )
        if status in (401, 403):
            return ApiError(
                "API キーが無効か、利用が許可されていません。設定を確認してください。", status, debug_detail=detail
            )
        if status == 404:
            return ApiError(
                f"モデル「{model}」が見つかりません。config/settings.toml のモデル名を確認してください。",
                status,
                debug_detail=detail,
            )
        if status == 400:
            return ApiError(
                f"AI へのリクエストが不正です。（{message[:200] or 'Bad Request'}）", status, debug_detail=detail
            )
        if status >= 500:
            return ApiError(
                "AI サービスが混雑しているため、一時的に利用できません。時間をおいて再試行してください。",
                status,
                debug_detail=detail,
            )
        return ApiError(f"AI サービスでエラーが発生しました（HTTP {status}）。", status, debug_detail=detail)

    @staticmethod
    def _extract_text(body: dict[str, Any], model: str = "") -> str:
        feedback = body.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise ApiError(
                "入力内容が AI の安全基準によりブロックされました。内容を確認してください。",
                debug_detail=f"model={model} promptFeedback.blockReason={feedback.get('blockReason')}",
            )
        candidates = body.get("candidates") or []
        if not candidates:
            raise SchemaError(debug_detail=f"model={model} response has no candidates: keys={list(body)}")
        cand = candidates[0]
        reason = cand.get("finishReason", "")
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            raise ApiError(
                "AI の安全基準により応答が生成されませんでした。内容を確認してください。",
                debug_detail=f"model={model} finishReason={reason}",
            )
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            detail = f"model={model} empty text, finishReason={reason or '-'}, usage={body.get('usageMetadata')}"
            if reason == "MAX_TOKENS":
                raise SchemaError(
                    "AI の応答が長すぎて途中で切れました。もう一度お試しください。", debug_detail=detail
                )
            raise SchemaError(debug_detail=detail)
        return text
