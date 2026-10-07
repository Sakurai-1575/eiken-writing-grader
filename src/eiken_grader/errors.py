"""アプリ共通の例外。

UI はこれらの例外の ``user_message`` をそのまま利用者に表示する。
メッセージに API キーや答案本文を含めてはならない。
"""

from __future__ import annotations


class AppError(Exception):
    """利用者に表示できるメッセージを持つ基底例外。

    ``debug_detail`` は開発者向けの詳細（HTTP ステータス、モデル名、Google のエラー本文、
    スキーマ検証エラーなど）。開発モードでのみ端末ログと画面に表示する。API キーは含めない。
    """

    default_message = "予期しないエラーが発生しました。"

    def __init__(self, user_message: str | None = None, *, debug_detail: str | None = None) -> None:
        self.user_message = user_message or self.default_message
        self.debug_detail = debug_detail
        super().__init__(self.user_message)


class ConfigError(AppError):
    default_message = "設定ファイルの読み込みに失敗しました。"


class ApiKeyMissingError(AppError):
    default_message = (
        "Gemini API キーが設定されていません。"
        ".streamlit/secrets.toml（Cloud では Secrets 設定）に GEMINI_API_KEY を設定してください。"
    )


class RateLimitedError(AppError):
    default_message = "現在アクセスが集中しています。しばらく待ってから再試行してください。"

    def __init__(
        self,
        user_message: str | None = None,
        retry_after: float | None = None,
        *,
        status_code: int | None = None,
        debug_detail: str | None = None,
    ) -> None:
        super().__init__(user_message, debug_detail=debug_detail)
        self.retry_after = retry_after
        self.status_code = status_code


class ApiError(AppError):
    default_message = "AI サービスとの通信でエラーが発生しました。"

    def __init__(
        self, user_message: str | None = None, status_code: int | None = None, *, debug_detail: str | None = None
    ) -> None:
        super().__init__(user_message, debug_detail=debug_detail)
        self.status_code = status_code


class SchemaError(AppError):
    default_message = "AI の応答を解析できませんでした。お手数ですが、もう一度お試しください。"


class ImageError(AppError):
    default_message = "画像を読み込めませんでした。JPEG / PNG / HEIC 形式の画像をお使いください。"


class ReportError(AppError):
    default_message = "レポートの作成に失敗しました。"
