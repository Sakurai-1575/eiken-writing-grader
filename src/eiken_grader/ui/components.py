"""各ステップの画面描画（Streamlit 依存部分）。

AI が生成したテキストを HTML に埋め込む箇所は必ず ``html.escape`` する。
各ステップの主要部分は ``_panel``（key 付き st.container）で囲み、CSS でカード表示にする。
"""

from __future__ import annotations

import hmac
import html
import re
import time
from contextlib import contextmanager

import streamlit as st

from eiken_grader.config import AppSettings, Rubrics
from eiken_grader.errors import AppError
from eiken_grader.models.schemas import GradingReport
from eiken_grader.reports.markdown_report import report_filename
from eiken_grader.services import text_stats
from eiken_grader.services.image_utils import ACCEPTED_TYPES, PreparedImage, prepare_image
from eiken_grader.ui.state import (
    ACTION_GRADE,
    ACTION_OCR,
    ACTION_QUESTION_OCR,
    STEP_CAPTURE,
    STEP_RESULT,
    STEP_REVIEW,
    STEP_SETUP,
    K,
    go,
    request_action,
    reset_session,
    sync_widget,
)

STEP_LABELS = ["問題設定", "答案取込", "確認・修正", "採点結果"]

# 画像入力（問題用紙 / 答案）ごとのウィジェットキー接頭辞
PREFIX_QUESTION = "q_"
PREFIX_ANSWER = ""

# 入力方法の選択肢。先頭（index=0）が初期選択で、問題文・答案とも OCR（写真）を第一優先にする
W_MODE = "w_mode"
MODE_IMAGE = "📷 写真から読み取る"
MODE_TYPE = "⌨️ 直接入力"
MODE_OPTIONS = [MODE_IMAGE, MODE_TYPE]

W_Q_MODE = "w_q_mode"
Q_MODE_IMAGE = "📷 問題用紙を撮影・アップロード"
Q_MODE_TYPE = "⌨️ 手入力"
Q_MODE_OPTIONS = [Q_MODE_IMAGE, Q_MODE_TYPE]

AUTH_MAX_FAILS = 5
AUTH_LOCK_SEC = 60


def _esc(text: str) -> str:
    return html.escape(text or "")


def _busy() -> bool:
    return bool(st.session_state.get(K.IS_BUSY))


@contextmanager
def _panel(name: str):
    """カード風のパネル（白背景・枠線・控えめな影）。CSS は ``.st-key-eg-panel-*`` で指定。"""
    with st.container(key=f"eg-panel-{name}"):
        yield


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
def render_title() -> None:
    st.markdown(
        '<div class="eg-header"><div class="eg-logo">✍️</div>'
        '<div><div class="eg-title">英検ライティング 採点アシスタント</div>'
        '<div class="eg-subtitle">手書き答案の読み取り・4観点採点・添削</div></div></div>',
        unsafe_allow_html=True,
    )


def render_steps(step: int) -> None:
    items = []
    for i, label in enumerate(STEP_LABELS, 1):
        state = "active" if i == step else ("done" if i < step else "todo")
        mark = "✓" if state == "done" else str(i)
        current = ' aria-current="step"' if state == "active" else ""
        items.append(
            f'<li class="eg-stp {state}"{current}><span class="dot">{mark}</span>'
            f'<span class="lbl">{label}</span></li>'
        )
    st.markdown(f'<ol class="eg-stepper">{"".join(items)}</ol>', unsafe_allow_html=True)


def render_debug_detail(detail: str) -> None:
    """開発モード専用: エラーの詳細（HTTP ステータス・Google のエラー本文など）を表示する。"""
    with st.expander("🔧 開発者向け詳細（開発モードのみ表示）", expanded=True):
        st.code(detail, language=None, wrap_lines=True)
        st.caption("同じ内容が端末（streamlit を起動したコンソール）のログにも出力されています。")


def render_messages(debug: bool = False) -> None:
    ss = st.session_state
    if ss.get(K.ERROR):
        st.error(ss[K.ERROR], icon="⚠️")
        if debug and ss.get(K.ERROR_DETAIL):
            render_debug_detail(ss[K.ERROR_DETAIL])
    if ss.get(K.NOTICE):
        st.info(ss[K.NOTICE], icon="ℹ️")
        ss[K.NOTICE] = None


def render_privacy_notice() -> None:
    with st.expander("ご利用にあたっての注意"):
        st.markdown(
            "- 答案・問題用紙に**氏名・学校名を写さない**でください。\n"
            "- 答案・画像・結果は**保存されません**。ページを閉じるか「新しい答案を採点する」で消去されます。\n"
            "- 採点には Gemini API（無料枠）を使用します。"
            "送信内容が Google のサービス改善に利用される場合があります。\n"
            "- AI による**参考採点**です。実際の英検の得点を保証するものではありません。"
        )


def passcode_gate(passcode: str | None) -> bool:
    """APP_PASSCODE が設定されている場合のみ、簡易パスコード画面を表示する。"""
    ss = st.session_state
    if not passcode or ss.get(K.AUTHED):
        return True
    st.markdown("#### 🔒 パスコードを入力してください")
    locked_for = ss.get(K.AUTH_LOCK_UNTIL, 0.0) - time.time()
    if locked_for > 0:
        st.error(f"入力の失敗が続いたため、{int(locked_for) + 1} 秒後に再度お試しください。")
        return False
    with st.form("passcode_form", clear_on_submit=True):
        entered = st.text_input("パスコード", type="password", autocomplete="off")
        submitted = st.form_submit_button("開く", type="primary", width="stretch")
    if submitted:
        if hmac.compare_digest(entered.encode(), str(passcode).encode()):
            ss[K.AUTHED] = True
            ss[K.AUTH_FAILS] = 0
            st.rerun()
        ss[K.AUTH_FAILS] = ss.get(K.AUTH_FAILS, 0) + 1
        if ss[K.AUTH_FAILS] >= AUTH_MAX_FAILS:
            ss[K.AUTH_LOCK_UNTIL] = time.time() + AUTH_LOCK_SEC
            ss[K.AUTH_FAILS] = 0
        st.error("パスコードが違います。")
    return False


def _copy(widget_key: str, store_key: str) -> None:
    st.session_state[store_key] = st.session_state.get(widget_key)


def _mode_selector(label: str, key: str, options: list[str]) -> str:
    """入力方法の切替（ピル型）。初期値・不正な値は必ず先頭の選択肢（index=0）にする。"""
    ss = st.session_state
    if ss.get(key) not in options:  # 初回表示・旧バージョンの値が残っている場合など
        ss[key] = options[0]
    st.segmented_control(
        label, options, required=True, key=key, disabled=_busy(), label_visibility="collapsed"
    )
    return ss.get(key) or options[0]


# ---------------------------------------------------------------------------
# 画像入力（問題用紙・答案で共通）
# ---------------------------------------------------------------------------
def _uploader_key(prefix: str) -> str:
    return f"{prefix}uploader_{st.session_state.get(K.UPLOADER_NONCE, 0)}"


def _camera_key(prefix: str) -> str:
    return f"{prefix}camera_{st.session_state.get(K.UPLOADER_NONCE, 0)}"


def _collect_images(settings: AppSettings, prefix: str = PREFIX_ANSWER) -> tuple[list[PreparedImage], list[str]]:
    """アップロード／撮影された画像を前処理する（ファイル単位でセッション内キャッシュ）。"""
    ss = st.session_state
    files = list(ss.get(_uploader_key(prefix)) or [])
    shot = ss.get(_camera_key(prefix))
    if shot is not None:
        files.append(shot)
    cache: dict[str, PreparedImage] = ss.setdefault(f"{prefix}prep_cache", {})
    limit_bytes = settings.image.max_upload_mb * 1024 * 1024
    images: list[PreparedImage] = []
    errors: list[str] = []
    for f in files:
        if len(images) >= settings.image.max_images:
            n = settings.image.max_images
            errors.append(f"画像は {n} 枚までです。最初の {n} 枚を使用します。")
            break
        if f.size > limit_bytes:
            errors.append(f"画像が大きすぎます（上限 {settings.image.max_upload_mb}MB）。")
            continue
        fid = getattr(f, "file_id", None) or f"{f.name}:{f.size}"
        if fid not in cache:
            try:
                cache[fid] = prepare_image(
                    f.getvalue(), settings.image.max_long_edge_px, settings.image.jpeg_quality
                )
            except AppError as e:
                errors.append(e.user_message)
                continue
        images.append(cache[fid])
    # 現在選択されていない画像の前処理結果はメモリから破棄する
    active = {getattr(f, "file_id", None) or f"{f.name}:{f.size}" for f in files}
    for fid in list(cache):
        if fid not in active:
            del cache[fid]
    return images, errors


def _image_input(settings: AppSettings, prefix: str, label: str) -> list[PreparedImage]:
    """アップローダー + （任意）ブラウザカメラ + プレビュー。前処理済み画像を返す。"""
    st.file_uploader(
        f"{label}（{settings.image.max_images} 枚まで）",
        type=ACCEPTED_TYPES,
        accept_multiple_files=True,
        key=_uploader_key(prefix),
        help="iPad では「写真を撮る」で背面カメラを使えます。真上から明るい場所で撮影してください。",
        disabled=_busy(),
    )
    nonce = st.session_state.get(K.UPLOADER_NONCE, 0)
    if st.toggle("ブラウザのカメラで撮影", key=f"{prefix}camtoggle_{nonce}", disabled=_busy()):
        st.camera_input("撮影", key=_camera_key(prefix), disabled=_busy(), label_visibility="collapsed")
    images, errors = _collect_images(settings, prefix)
    for msg in dict.fromkeys(errors):
        st.warning(msg)
    if images:
        # 画面を占有しないよう、プレビューは初期状態では折りたたむ
        with st.expander(f"📷 取り込んだ写真を確認する（{len(images)} 枚）", expanded=False):
            for i, img in enumerate(images, 1):
                st.image(img.data, caption=f"{i} 枚目", width="stretch")
    return images


# ---------------------------------------------------------------------------
# Step 1: 問題設定
# ---------------------------------------------------------------------------
def _on_setup_next() -> None:
    ss = st.session_state
    _copy(K.W_QUESTION, K.QUESTION)
    if not (ss.get(K.QUESTION) or "").strip():
        ss[K.ERROR] = "問題文を入力してください（採点の精度に必要です）。"
        return
    go(ss, STEP_CAPTURE)


def _on_question_ocr(settings: AppSettings) -> None:
    images, _ = _collect_images(settings, PREFIX_QUESTION)
    if not images:
        st.session_state[K.ERROR] = "問題用紙の画像を撮影またはアップロードしてください。"
        return
    st.session_state[K.QUESTION_IMAGES] = images
    request_action(st.session_state, ACTION_QUESTION_OCR)


def render_setup(rubrics: Rubrics, settings: AppSettings) -> None:
    ss = st.session_state
    with _panel("setup"):
        grades = rubrics.enabled_grades()
        grade_ids = [g.id for g in grades]
        if ss.get(K.GRADE_ID) not in grade_ids:
            ss[K.GRADE_ID] = grade_ids[0]
        if ss.get(K.W_GRADE) not in grade_ids:
            ss.pop(K.W_GRADE, None)
        sync_widget(ss, K.GRADE_ID, K.W_GRADE)

        grade = rubrics.grades[ss[K.GRADE_ID]]
        task_ids = list(grade.tasks)
        if ss.get(K.TASK_ID) not in task_ids:
            ss[K.TASK_ID] = task_ids[0]
        if ss.get(K.W_TASK) not in task_ids:
            ss.pop(K.W_TASK, None)
        sync_widget(ss, K.TASK_ID, K.W_TASK)

        c1, c2 = st.columns(2)
        c1.selectbox(
            "級",
            grade_ids,
            format_func=lambda gid: f"英検{rubrics.grades[gid].label}",
            key=K.W_GRADE,
            on_change=_copy,
            args=(K.W_GRADE, K.GRADE_ID),
            disabled=_busy(),
        )
        c2.selectbox(
            "問題形式",
            task_ids,
            format_func=lambda tid: f"{grade.tasks[tid].label}（{grade.tasks[tid].word_range[0]}〜"
            f"{grade.tasks[tid].word_range[1]}語）",
            key=K.W_TASK,
            on_change=_copy,
            args=(K.W_TASK, K.TASK_ID),
            disabled=_busy(),
        )
        task = grade.tasks[ss[K.TASK_ID]]

    with _panel("question"):
        st.markdown('<div class="eg-panel-title">問題文</div>', unsafe_allow_html=True)
        # 初期値は OCR（index=0）。画像モードでも「読み取る」ボタンを押したときだけ API を 1 回呼び、
        # 手入力モードでは呼ばない
        q_mode = _mode_selector("問題文の入力方法", W_Q_MODE, Q_MODE_OPTIONS)
        if q_mode == Q_MODE_IMAGE:
            images = _image_input(settings, PREFIX_QUESTION, "問題用紙の写真")
            st.button(
                "問題文を読み取る（AI）",
                type="primary",
                width="stretch",
                on_click=_on_question_ocr,
                args=(settings,),
                disabled=_busy() or not images,
            )

        sync_widget(ss, K.QUESTION, K.W_QUESTION)
        st.text_area(
            "問題文",
            key=K.W_QUESTION,
            height=240,
            placeholder=task.question_hint,
            on_change=_copy,
            args=(K.W_QUESTION, K.QUESTION),
            disabled=_busy(),
            label_visibility="collapsed",
        )

    st.button("次へ：答案を取り込む →", type="primary", width="stretch", on_click=_on_setup_next,
              disabled=_busy())
    render_privacy_notice()


# ---------------------------------------------------------------------------
# Step 2: 答案の取り込み
# ---------------------------------------------------------------------------
def _on_ocr(settings: AppSettings) -> None:
    images, _ = _collect_images(settings, PREFIX_ANSWER)
    if not images:
        st.session_state[K.ERROR] = "答案の画像を撮影またはアップロードしてください。"
        return
    st.session_state[K.IMAGES] = images
    request_action(st.session_state, ACTION_OCR)


def _on_direct_input() -> None:
    ss = st.session_state
    ss[K.IMAGES] = []
    ss[K.OCR_RESULT] = None
    ss[K.W_ANSWER] = ss.get(K.ANSWER) or ""
    go(ss, STEP_REVIEW)


def render_capture(settings: AppSettings) -> None:
    ss = st.session_state
    with _panel("capture"):
        st.markdown('<div class="eg-panel-title">答案</div>', unsafe_allow_html=True)
        if _mode_selector("取り込み方法", W_MODE, MODE_OPTIONS) == MODE_TYPE:
            st.caption("答案の英文をキーボードで入力して採点します。")
            st.button("答案を入力する →", type="primary", width="stretch", on_click=_on_direct_input,
                      disabled=_busy())
        else:
            images = _image_input(settings, PREFIX_ANSWER, "答案の写真")
            st.button(
                "文字を読み取る（AI）",
                type="primary",
                width="stretch",
                on_click=_on_ocr,
                args=(settings,),
                disabled=_busy() or not images,
            )
    st.button("← 問題設定に戻る", width="stretch", on_click=go, args=(ss, STEP_SETUP), disabled=_busy())


# ---------------------------------------------------------------------------
# Step 3: 確認・修正
# ---------------------------------------------------------------------------
def _on_grade() -> None:
    ss = st.session_state
    _copy(K.W_ANSWER, K.ANSWER)
    request_action(ss, ACTION_GRADE)


def _highlight_markers(text: str) -> str:
    escaped = _esc(text)
    escaped = escaped.replace("[?]", '<span class="eg-mark">[?]</span>')
    return re.sub(r"\{([^{}]*?)\?\}", r'<span class="eg-mark">\1?</span>', escaped)


def word_count_badge(words: int, word_range: tuple[int, int]) -> str:
    """「語数: ○○語（目安 ○○〜○○語・範囲内）」の表示（段落数は表示しない）。"""
    status = text_stats.word_range_status(words, word_range)
    lo, hi = word_range
    cls = "ok" if status == "範囲内" else "warn"
    return (
        f'<div class="eg-wc {cls}"><b>語数: {words}語</b>'
        f'<span>（目安 {lo}〜{hi}語・{status}）</span></div>'
    )


def render_review(rubrics: Rubrics) -> None:
    ss = st.session_state
    _, task = rubrics.get_task(ss[K.GRADE_ID], ss[K.TASK_ID])
    images: list[PreparedImage] = ss.get(K.IMAGES) or []
    ocr = ss.get(K.OCR_RESULT)

    with _panel("review"):
        st.markdown('<div class="eg-panel-title">答案の確認・修正</div>', unsafe_allow_html=True)
        if images:
            st.caption("画像と見比べて読み取りミスだけを直してください。スペル・文法の誤りは採点対象なのでそのままにします。")
            with st.expander("📷 答案画像を確認する", expanded=False):
                for i, img in enumerate(images, 1):
                    st.image(img.data, caption=f"{i} 枚目", width="stretch")
        if ocr is not None and ocr.notes:
            st.caption(f"読み取りメモ: {ocr.notes}")

        sync_widget(ss, K.ANSWER, K.W_ANSWER)
        st.text_area(
            "答案の英文",
            key=K.W_ANSWER,
            height=360,
            placeholder="答案の英文を入力してください。",
            on_change=_copy,
            args=(K.W_ANSWER, K.ANSWER),
            disabled=_busy(),
            label_visibility="collapsed",
        )
        text = ss.get(K.W_ANSWER) or ""
        st.markdown(word_count_badge(text_stats.count_words(text), task.word_range), unsafe_allow_html=True)

        has_markers = text_stats.has_markers(text)
        if has_markers:
            n_illegible = text_stats.illegible_count(text)
            uncertain = text_stats.uncertain_marked_words(text)
            st.warning(
                f"要確認の箇所があります（判読不能 [?] {n_illegible} 箇所・要確認語 {len(uncertain)} 語）。"
                "`{word?}` は確認後に `word` へ書き換えてください。"
            )
            with st.expander("要確認箇所をハイライト表示"):
                st.markdown(f'<div class="eg-answer">{_highlight_markers(text)}</div>', unsafe_allow_html=True)
            st.checkbox("要確認の印が残ったまま採点する", key=K.W_CONFIRM_MARKERS, disabled=_busy())

    can_grade = bool(text.strip()) and (not has_markers or ss.get(K.W_CONFIRM_MARKERS, False))
    st.button("この内容で採点する（AI）", type="primary", width="stretch", on_click=_on_grade,
              disabled=_busy() or not can_grade)
    st.button("← 答案の取り込みに戻る", width="stretch", on_click=go, args=(ss, STEP_CAPTURE),
              disabled=_busy())


# ---------------------------------------------------------------------------
# Step 4: 採点結果
# ---------------------------------------------------------------------------
def _score_card(criterion: str, score: int, max_score: int, rationale: str) -> str:
    pct = int(100 * score / max_score) if max_score else 0
    return (
        f'<div class="eg-card eg-score"><div class="head"><span>{_esc(criterion)}</span>'
        f'<span class="score">{score}<small> / {max_score}</small></span></div>'
        f'<div class="eg-bar"><div style="width:{pct}%"></div></div>'
        f'<div class="eg-note">{_esc(rationale)}</div></div>'
    )


def _section(num: int, title: str) -> None:
    st.markdown(
        f'<div class="eg-section"><span class="no">{num}</span>{_esc(title)}</div>', unsafe_allow_html=True
    )


def _on_reset() -> None:
    reset_session(st.session_state)


def render_result() -> None:
    ss = st.session_state
    report: GradingReport | None = ss.get(K.REPORT)
    if report is None:
        go(ss, STEP_SETUP)
        st.rerun()
        return
    r = report

    st.markdown(
        f'<div class="eg-total"><div class="sub">英検{_esc(r.grade_label)}・{_esc(r.task_label)}</div>'
        f'<div class="num">{r.total_score}<small> / {r.max_total}</small></div>'
        f'<div class="sub">語数 {_esc(r.word_count_comment)}</div></div>',
        unsafe_allow_html=True,
    )
    if r.off_topic:
        st.error("答案が課題の指示に沿っていないと判定されました。問題文の指示をもう一度確認しましょう。")

    # 生徒と画面を見ながら上から順に指導できるよう、タブを使わず 1 画面に縦に並べる
    # 1. 観点別スコアカード
    _section(1, "観点別スコア")
    cols = st.columns(2)
    for i, s in enumerate(r.scores):
        cols[i % 2].markdown(_score_card(s.criterion, s.score, s.max_score, s.rationale), unsafe_allow_html=True)

    # 2. 添削一覧
    _section(2, f"添削（{len(r.corrections)} 件）")
    if not r.corrections:
        st.success("大きな誤りは見つかりませんでした。")
    for i, c in enumerate(r.corrections, 1):
        ref = "（参考）" if not c.verified else ""
        st.markdown(
            f'<div class="eg-corr"><div><span class="cat">{i}. {_esc(c.category)}{ref}</span></div>'
            f'<div class="pair"><span class="orig">{_esc(c.original)}</span>'
            f'<span class="arrow">→</span><span class="new">{_esc(c.corrected)}</span></div>'
            f'<div class="exp">{_esc(c.explanation)}</div></div>',
            unsafe_allow_html=True,
        )
    if any(not c.verified for c in r.corrections):
        st.caption("※（参考）は答案中に同じ表現が見つからなかった指摘です。")

    # 3. 改善版の答案 & 模範解答
    _section(3, "改善版の答案・模範解答")
    st.markdown('<div class="eg-label">改善版（あなたの答案をもとに）</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="eg-card eg-answer">{_esc(r.improved_answer)}</div>', unsafe_allow_html=True)
    st.markdown('<div class="eg-label">模範解答</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="eg-card eg-answer">{_esc(r.model_answer)}</div>', unsafe_allow_html=True)

    # 4. 総評・観点別の詳しい評価・次のステップ
    _section(4, "総評・アドバイス")
    st.markdown(f'<div class="eg-card eg-text">{_esc(r.overall_comment)}</div>', unsafe_allow_html=True)
    st.markdown('<div class="eg-label">観点別の詳しい評価</div>', unsafe_allow_html=True)
    for s in r.scores:
        with st.expander(f"{s.criterion}（{s.score} / {s.max_score}）"):
            items = [f"<p>{_esc(s.rationale)}</p>"]
            items += [f"<li>◎ {_esc(p)}</li>" for p in s.good_points]
            items += [f"<li>△ {_esc(p)}</li>" for p in s.improvements]
            st.markdown(f'<div class="eg-text">{items[0]}<ul>{"".join(items[1:])}</ul></div>',
                        unsafe_allow_html=True)
    if r.next_steps:
        st.markdown('<div class="eg-label">次のステップ</div>', unsafe_allow_html=True)
        steps = "".join(f"<li>{_esc(step)}</li>" for step in r.next_steps)
        st.markdown(f'<ol class="eg-card eg-text eg-steps-list">{steps}</ol>', unsafe_allow_html=True)

    # 5. 提出された答案（確定テキスト）
    _section(5, "提出された答案")
    st.markdown(f'<div class="eg-card eg-answer">{_esc(r.answer)}</div>', unsafe_allow_html=True)

    # 6. ダウンロード
    _section(6, "レポートを保存")
    if ss.get(K.REPORT_ERROR):
        st.warning(ss[K.REPORT_ERROR])
    c1, c2 = st.columns(2)
    if ss.get(K.PDF_BYTES):
        c1.download_button(
            "📄 PDF（A4・2ページ）",
            data=ss[K.PDF_BYTES],
            file_name=report_filename(r, "pdf"),
            mime="application/pdf",
            on_click="ignore",
            width="stretch",
            type="primary",
        )
    if ss.get(K.MD_TEXT):
        c2.download_button(
            "📝 Markdown",
            data=ss[K.MD_TEXT].encode("utf-8"),
            file_name=report_filename(r, "md"),
            mime="text/markdown",
            on_click="ignore",
            width="stretch",
        )
    st.caption("iPad では「共有」→「ファイルに保存」で保存できます。")

    st.button("答案を修正して再採点する", width="stretch", on_click=go, args=(ss, STEP_REVIEW),
              disabled=_busy())
    st.button("🗑️ 新しい答案を採点する（データを消去）", width="stretch", on_click=_on_reset,
              disabled=_busy())
    st.caption(f"採点モデル: {r.model} ／ AI による参考採点です。")


def render_step(step: int, settings: AppSettings, rubrics: Rubrics) -> None:
    if step == STEP_SETUP:
        render_setup(rubrics, settings)
    elif step == STEP_CAPTURE:
        render_capture(settings)
    elif step == STEP_REVIEW:
        render_review(rubrics)
    elif step == STEP_RESULT:
        render_result()
