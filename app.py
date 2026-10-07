"""手書き英検ライティング自動採点・学習支援アプリ（Streamlit エントリポイント）。

このファイルはページ設定・ステップ遷移・依存オブジェクトの組み立てのみを行う。
ロジックは src/eiken_grader 以下に分離している。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:  # Streamlit Community Cloud ではパッケージをインストールしないため
    sys.path.insert(0, str(SRC))

import streamlit as st  # noqa: E402

from eiken_grader.config import (  # noqa: E402
    AppSettings,
    get_api_key,
    is_debug_mode,
    load_rubrics,
    load_settings,
    read_secrets,
)
from eiken_grader.core.gemini_client import GeminiClient  # noqa: E402
from eiken_grader.core.rate_guard import RateGuard  # noqa: E402
from eiken_grader.errors import AppError  # noqa: E402
from eiken_grader.ui import components  # noqa: E402
from eiken_grader.ui.state import ACTION_OCR, Controller, K, init_state  # noqa: E402
from eiken_grader.ui.styles import CSS  # noqa: E402

# 本番では答案本文・画像・API キーをログに出さない（ステータスコード・エラー種別のみ）。
# 開発モードでは eiken_grader のログを DEBUG まで出し、エラーの詳細とトレースバックも出力する。
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app_logger = logging.getLogger("eiken_grader")

BUSY_MESSAGES = {
    ACTION_OCR: "手書きの文字を読み取っています…（10〜30 秒ほどかかります）",
}
DEFAULT_BUSY_MESSAGE = "AI が採点しています…（20〜60 秒ほどかかります）"


@st.cache_resource
def get_rate_guard(rpm: int, rpd: int) -> RateGuard:
    """全セッションで共有するレートガード（1 プロセスに 1 個）。"""
    return RateGuard(rpm=rpm, rpd=rpd)


@st.cache_resource
def log_startup(models: tuple[str, ...], debug: bool, has_key: bool) -> bool:
    """起動時の構成を 1 回だけ端末ログに出す（キーの値は出さない）。"""
    app_logger.info(
        "Startup: models=%s debug=%s api_key=%s",
        " -> ".join(models),
        debug,
        "set" if has_key else "MISSING",
    )
    return True


def configure_logging(debug: bool) -> None:
    app_logger.setLevel(logging.DEBUG if debug else logging.INFO)


def main() -> None:
    st.set_page_config(
        page_title="英検ライティング採点",
        page_icon="✍️",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state
    init_state(ss)

    secrets = read_secrets()
    debug = False
    try:
        settings = load_settings(secrets=secrets)
        debug = is_debug_mode(settings, secrets)
        configure_logging(debug)
        log_startup(tuple(settings.gemini.model_chain), debug, bool(secrets.get("GEMINI_API_KEY")))
        rubrics = load_rubrics()
        get_api_key(secrets)  # 起動時にキーの有無だけ確認する
    except AppError as e:
        # 設定不備は原因を端末にも必ず出す（値そのものは出さない）
        app_logger.error("Startup configuration error: %s: %s", type(e).__name__, e.user_message)
        components.render_title()
        st.error(e.user_message)
        if debug or is_debug_mode(AppSettings(), secrets):
            components.render_debug_detail(f"{type(e).__name__}: {e.user_message}")
        st.stop()

    components.render_title()
    if not components.passcode_gate(secrets.get("APP_PASSCODE")):
        st.stop()

    def client_factory() -> GeminiClient:
        guard = get_rate_guard(settings.rate_limit.rpm, settings.rate_limit.rpd)
        return GeminiClient(get_api_key(secrets), settings.gemini, guard=guard)

    controller = Controller(
        ss=ss, settings=settings, rubrics=rubrics, client_factory=client_factory, debug=debug
    )

    components.render_steps(ss[K.STEP])
    status_slot = st.container()
    with status_slot:
        components.render_messages(debug)

    # 処理中はこの描画でボタンが disabled になる（連打・二重送信の防止）
    components.render_step(ss[K.STEP], settings, rubrics)

    if ss.get(K.PENDING):
        with status_slot:
            with st.spinner(BUSY_MESSAGES.get(ss[K.PENDING], DEFAULT_BUSY_MESSAGE), show_time=True):
                controller.run_pending()
        st.rerun()
    elif ss.get(K.IS_BUSY):
        controller.run_pending()  # 取り残された処理中フラグを解除
        st.rerun()


main()
