"""セッション状態の定義と、API を呼ぶ操作の制御（連打防止・クールダウン・結果キャッシュ）。

このモジュールは Streamlit を import しない。``st.session_state`` は MutableMapping として
受け取るため、通常の dict で単体テストできる。

連打防止の流れ:
1. ボタンの on_click で ``request_action`` → is_busy=True, pending_action を設定
   （Streamlit はコールバックを再描画の前に実行するため、次の描画ではボタンが disabled になる）
2. 描画後に ``run_pending`` が 1 回だけ処理を実行し、finally で is_busy を必ず解除する
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from eiken_grader.config import AppSettings, Rubrics
from eiken_grader.errors import AppError, RateLimitedError, ReportError
from eiken_grader.models.schemas import GradingReport, OcrResult
from eiken_grader.reports.markdown_report import render_markdown
from eiken_grader.reports.pdf_report import render_pdf
from eiken_grader.services.grading import grade_answer, input_hash
from eiken_grader.services.image_utils import images_digest
from eiken_grader.services.ocr import JsonGenerator, transcribe, transcribe_question

logger = logging.getLogger(__name__)

State = MutableMapping[str, Any]

# --- 画面ステップ -----------------------------------------------------------
STEP_SETUP = 1
STEP_CAPTURE = 2
STEP_REVIEW = 3
STEP_RESULT = 4

# --- アクション -------------------------------------------------------------
ACTION_OCR = "ocr"  # 答案の読み取り
ACTION_QUESTION_OCR = "question_ocr"  # 問題文の読み取り（画像から取り込む場合のみ）
ACTION_GRADE = "grade"


# --- セッションキー ---------------------------------------------------------
class K:
    STEP = "step"
    GRADE_ID = "grade_id"
    TASK_ID = "task_id"
    QUESTION = "question"
    QUESTION_IMAGES = "question_images"  # list[PreparedImage]（問題用紙）
    IMAGES = "images"  # list[PreparedImage]（答案）
    OCR_RESULT = "ocr_result"  # OcrResult | None
    ANSWER = "answer"
    REPORT = "report"  # GradingReport | None
    PDF_BYTES = "pdf_bytes"
    MD_TEXT = "md_text"
    REPORT_ERROR = "report_error"
    IS_BUSY = "is_busy"
    PENDING = "pending_action"
    LAST_CALL_AT = "last_call_at"
    OCR_CACHE = "ocr_cache"  # dict[digest, OcrResult]
    GRADE_CACHE = "grade_cache"  # dict[hash, GradingReport]
    ERROR = "error_message"
    ERROR_DETAIL = "error_detail"  # 開発モードでのみ画面に表示する詳細
    NOTICE = "notice_message"
    AUTHED = "authed"
    AUTH_FAILS = "auth_fails"
    AUTH_LOCK_UNTIL = "auth_lock_until"
    UPLOADER_NONCE = "uploader_nonce"
    # 次回の描画開始時にウィジェットへ反映する値 {widget_key: value}
    # （描画済みのウィジェットの値は同じ実行中に変更できないため、いったん保留する）
    WIDGET_SYNC = "widget_sync"
    # 入力ウィジェットのキー（ステップ切替で消えないよう、保存用キーと分けている）
    W_GRADE = "w_grade"
    W_TASK = "w_task"
    W_QUESTION = "w_question"
    W_ANSWER = "w_answer"
    W_CONFIRM_MARKERS = "w_confirm_markers"


DEFAULTS: dict[str, Any] = {
    K.STEP: STEP_SETUP,
    K.GRADE_ID: None,
    K.TASK_ID: None,
    K.QUESTION: "",
    K.QUESTION_IMAGES: [],
    K.IMAGES: [],
    K.OCR_RESULT: None,
    K.ANSWER: "",
    K.REPORT: None,
    K.PDF_BYTES: None,
    K.MD_TEXT: None,
    K.REPORT_ERROR: None,
    K.IS_BUSY: False,
    K.PENDING: None,
    K.LAST_CALL_AT: None,
    K.OCR_CACHE: {},
    K.GRADE_CACHE: {},
    K.ERROR: None,
    K.ERROR_DETAIL: None,
    K.NOTICE: None,
    K.AUTHED: False,
    K.AUTH_FAILS: 0,
    K.AUTH_LOCK_UNTIL: 0.0,
    K.UPLOADER_NONCE: 0,
    K.WIDGET_SYNC: {},
}

# リセット後も残すキー（認証状態のみ。答案・結果・画像はすべて消去する）
KEEP_ON_RESET = {K.AUTHED, K.AUTH_FAILS, K.AUTH_LOCK_UNTIL, K.UPLOADER_NONCE}


def init_state(ss: State) -> None:
    for key, value in DEFAULTS.items():
        if key not in ss:
            ss[key] = value.copy() if isinstance(value, (dict, list)) else value


def reset_session(ss: State) -> None:
    """答案・画像・結果を含むセッションデータをすべて消去する（認証状態のみ保持）。"""
    nonce = ss.get(K.UPLOADER_NONCE, 0)
    for key in list(ss.keys()):
        if key not in KEEP_ON_RESET:
            del ss[key]
    init_state(ss)
    # file_uploader / camera_input のキーを変えて、前回の画像を確実に破棄する
    ss[K.UPLOADER_NONCE] = nonce + 1


def clear_error(ss: State) -> None:
    ss[K.ERROR] = None
    ss[K.ERROR_DETAIL] = None


def go(ss: State, step: int) -> None:
    ss[K.STEP] = step
    clear_error(ss)


def cooldown_remaining(ss: State, cooldown_sec: float, now: float) -> float:
    last = ss.get(K.LAST_CALL_AT)
    if last is None:
        return 0.0
    return max(0.0, cooldown_sec - (now - last))


def request_action(ss: State, action: str) -> bool:
    """ボタンの on_click から呼ぶ。処理中なら何もしない（二重送信防止）。"""
    if ss.get(K.IS_BUSY) or ss.get(K.PENDING):
        return False
    ss[K.IS_BUSY] = True
    ss[K.PENDING] = action
    clear_error(ss)
    return True


def sync_widget(ss: State, store_key: str, widget_key: str) -> None:
    """保存用キーの値をウィジェットに反映する（ウィジェット生成前に呼ぶ）。"""
    if widget_key not in ss:
        ss[widget_key] = ss.get(store_key)


def queue_widget_value(ss: State, widget_key: str, value: Any) -> None:
    """ウィジェットの値を、次回の描画開始時（ウィジェット生成前）に書き換えるよう予約する。"""
    ss.setdefault(K.WIDGET_SYNC, {})[widget_key] = value


def apply_widget_sync(ss: State) -> None:
    """予約されたウィジェット値を反映する。毎回の描画の最初（ウィジェット生成前）に呼ぶ。"""
    pending = ss.get(K.WIDGET_SYNC) or {}
    for key, value in pending.items():
        ss[key] = value
    ss[K.WIDGET_SYNC] = {}


@dataclass
class Controller:
    """API を呼ぶ操作の実行を担う。client_factory は API 呼び出しが必要になった時点で初めて呼ぶ。"""

    ss: State
    settings: AppSettings
    rubrics: Rubrics
    client_factory: Callable[[], JsonGenerator]
    clock: Callable[[], float] = time.time
    debug: bool = False  # True: エラーの詳細を端末ログ（トレースバック付き）と画面に出す

    def run_pending(self) -> str | None:
        """保留中のアクションを 1 回だけ実行する。実行したアクション名を返す。"""
        action = self.ss.get(K.PENDING)
        if not action:
            if self.ss.get(K.IS_BUSY):  # 異常終了で残ったフラグの回復
                self.ss[K.IS_BUSY] = False
            return None
        try:
            if action == ACTION_OCR:
                self._run_ocr()
            elif action == ACTION_QUESTION_OCR:
                self._run_question_ocr()
            elif action == ACTION_GRADE:
                self._run_grade()
        except AppError as e:
            self.ss[K.ERROR] = e.user_message
            detail = self._describe(e)
            if self.debug:
                logger.error("Action %r failed: %s", action, detail, exc_info=True)
                self.ss[K.ERROR_DETAIL] = detail
            else:
                logger.warning("Action %r failed: %s", action, type(e).__name__)
        except Exception as e:  # 想定外のエラーでも処理中フラグは必ず解除する
            self.ss[K.ERROR] = "予期しないエラーが発生しました。もう一度お試しください。"
            # 想定外のエラーは原因調査のため常にトレースバックを端末ログに出す
            logger.exception("Unexpected error in action %r", action)
            if self.debug:
                self.ss[K.ERROR_DETAIL] = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        finally:
            self.ss[K.IS_BUSY] = False
            self.ss[K.PENDING] = None
        return action

    # -- 内部 ---------------------------------------------------------------
    @staticmethod
    def _describe(e: AppError) -> str:
        parts = [type(e).__name__]
        status = getattr(e, "status_code", None)
        if status:
            parts.append(f"HTTP {status}")
        if e.debug_detail:
            parts.append(e.debug_detail)
        return " / ".join(parts)

    def _check_cooldown(self) -> None:
        remaining = cooldown_remaining(self.ss, self.settings.rate_limit.cooldown_sec, self.clock())
        if remaining > 0:
            raise RateLimitedError(
                f"連続した操作を防ぐため、あと {int(remaining + 0.999)} 秒お待ちください。",
                retry_after=remaining,
            )

    def _mark_called(self) -> None:
        self.ss[K.LAST_CALL_AT] = self.clock()

    def _cached_ocr(self, kind: str, images: list, run: Callable[..., OcrResult]) -> OcrResult:
        """同じ画像の読み取り結果はセッション内で再利用する（API を呼ばない）。"""
        key = f"{kind}:{images_digest(images)}"
        cache: dict[str, OcrResult] = self.ss[K.OCR_CACHE]
        result = cache.get(key)
        if result is None:
            self._check_cooldown()
            client = self.client_factory()
            self._mark_called()
            result = run(client, images, self.settings.gemini.temperature_ocr)
            cache[key] = result
        return result

    def _run_ocr(self) -> None:
        images = self.ss.get(K.IMAGES) or []
        if not images:
            raise AppError("答案の画像がありません。撮影またはアップロードしてください。")
        result = self._cached_ocr("answer", images, transcribe)
        self.ss[K.OCR_RESULT] = result
        self.ss[K.ANSWER] = result.text
        queue_widget_value(self.ss, K.W_ANSWER, result.text)
        self.ss.pop(K.W_CONFIRM_MARKERS, None)
        go(self.ss, STEP_REVIEW)

    def _run_question_ocr(self) -> None:
        images = self.ss.get(K.QUESTION_IMAGES) or []
        if not images:
            raise AppError("問題用紙の画像がありません。撮影またはアップロードしてください。")
        result = self._cached_ocr("question", images, transcribe_question)
        if not result.text.strip():
            raise AppError("問題文を読み取れませんでした。明るい場所で真上から撮影し直すか、手入力してください。")
        self.ss[K.QUESTION] = result.text
        # 問題文の入力欄は同じ画面（Step 1）に表示中のため、次の描画開始時に反映する
        queue_widget_value(self.ss, K.W_QUESTION, result.text)
        note = f"（読み取りメモ: {result.notes}）" if result.notes else ""
        self.ss[K.NOTICE] = f"問題文を読み取りました。誤りがあれば下の入力欄で修正してください。{note}"

    def _run_grade(self) -> None:
        answer = (self.ss.get(K.ANSWER) or "").strip()
        question = (self.ss.get(K.QUESTION) or "").strip()
        if not answer:
            raise AppError("答案が空です。答案の英文を入力してください。")
        if not question:
            raise AppError("問題文が入力されていません。設定画面で問題文を入力してください。")
        grade, task = self.rubrics.get_task(self.ss[K.GRADE_ID], self.ss[K.TASK_ID])
        model = self.settings.gemini.model
        key = input_hash(model, grade.id, task.id, question, answer)
        cache: dict[str, GradingReport] = self.ss[K.GRADE_CACHE]
        report = cache.get(key)
        if report is None:
            self._check_cooldown()
            client = self.client_factory()
            self._mark_called()
            report = grade_answer(
                client,
                model_name=model,
                grade=grade,
                task=task,
                score_levels=self.rubrics.score_levels,
                question=question,
                answer=answer,
                temperature=self.settings.gemini.temperature_grading,
                max_corrections=self.settings.grading.max_corrections,
            )
            cache[key] = report
        else:
            self.ss[K.NOTICE] = "同じ答案の採点結果を表示しています（AI への再送信は行っていません）。"
        self.ss[K.REPORT] = report
        build_downloads(self.ss, report)
        go(self.ss, STEP_RESULT)


def build_downloads(ss: State, report: GradingReport) -> None:
    """採点結果から PDF / Markdown を 1 回だけ生成して保存する（API は呼ばない）。"""
    ss[K.MD_TEXT] = render_markdown(report)
    try:
        ss[K.PDF_BYTES] = render_pdf(report)
        ss[K.REPORT_ERROR] = None
    except ReportError as e:
        logger.error("PDF generation failed: %s", e.user_message, exc_info=True)
        ss[K.PDF_BYTES] = None
        ss[K.REPORT_ERROR] = e.user_message
