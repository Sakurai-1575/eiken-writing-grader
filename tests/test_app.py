"""Streamlit AppTest による画面遷移テスト（Gemini API は responses でモック）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import responses
from streamlit.testing.v1 import AppTest

from eiken_grader.config import load_settings
from eiken_grader.ui import components
from tests.conftest import SAMPLE_ANSWER, SAMPLE_QUESTION, gemini_response

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def new_app(**secrets) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in {"GEMINI_API_KEY": "test-key", **secrets}.items():
        at.secrets[k] = v
    return at


def button(at: AppTest, label_part: str):
    matches = [b for b in at.button if label_part in b.label]
    assert matches, f"button containing {label_part!r} not found: {[b.label for b in at.button]}"
    return matches[0]


@pytest.fixture
def api(ai_output_json):
    settings = load_settings()
    url = f"{settings.gemini.api_base}/models/{settings.gemini.model}:generateContent"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.post(url, json=gemini_response(ai_output_json))
        yield rsps


def test_missing_api_key_shows_error():
    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets["OTHER"] = "x"
    at.run()
    assert not at.exception
    assert any("GEMINI_API_KEY" in e.value for e in at.error)


def test_setup_screen_lists_initial_grades():
    at = new_app().run()
    assert not at.exception
    grade_box = at.selectbox(key="w_grade")
    assert list(grade_box.options) == ["英検準2級", "英検2級", "英検準1級"]


def test_setup_requires_question():
    at = new_app().run()
    button(at, "次へ").click().run()
    assert any("問題文を入力" in e.value for e in at.error)
    at.text_area(key="w_question").input(SAMPLE_QUESTION).run()
    button(at, "次へ").click().run()
    assert at.session_state["step"] == 2


def test_full_grading_flow_calls_api_once(api):
    at = new_app().run()
    at.selectbox(key="w_grade").set_value("g2").run()
    at.selectbox(key="w_task").set_value("opinion").run()
    at.text_area(key="w_question").input(SAMPLE_QUESTION).run()
    button(at, "次へ").click().run()
    # 直接入力モードで答案を入力
    at.session_state["w_mode"] = components.MODE_TYPE
    at.run()
    button(at, "答案を入力する").click().run()
    assert at.session_state["step"] == 3
    at.text_area(key="w_answer").input(SAMPLE_ANSWER).run()
    assert any("語数" in m.value for m in at.markdown)

    button(at, "この内容で採点する").click().run()
    assert not at.exception
    assert at.session_state["step"] == 4
    assert len(api.calls) == 1
    assert at.session_state["is_busy"] is False
    assert at.session_state["pdf_bytes"].startswith(b"%PDF")
    assert any('class="num">10<small> / 16</small>' in m.value for m in at.markdown)  # 総合得点

    # 結果画面での再描画・操作では API を呼ばない
    at.run()
    button(at, "答案を修正して再採点").click().run()
    button(at, "この内容で採点する").click().run()  # 同一入力 → キャッシュ
    assert len(api.calls) == 1
    assert any("再送信は行っていません" in i.value for i in at.info)

    # 新しい答案 → データ消去
    button(at, "新しい答案を採点する").click().run()
    assert at.session_state["step"] == 1
    assert at.session_state["report"] is None
    assert at.session_state["answer"] == ""
    assert at.session_state["question"] == ""


def test_markers_require_confirmation():
    at = new_app().run()
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["answer"] = "I {thnik?} it is [?] good."
    at.session_state["step"] = 3
    at.run()
    assert button(at, "この内容で採点する").disabled
    at.checkbox(key="w_confirm_markers").check().run()
    assert not button(at, "この内容で採点する").disabled


def test_passcode_gate():
    at = new_app(APP_PASSCODE="open-sesame").run()
    assert not at.selectbox  # 認証前は本体を表示しない
    at.text_input[0].input("wrong")
    at.button[0].click().run()
    assert any("パスコードが違います" in e.value for e in at.error)
    at.text_input[0].input("open-sesame")
    at.button[0].click().run()
    assert at.session_state["authed"] is True
    assert at.selectbox(key="w_grade")


@pytest.mark.parametrize("debug", [True, False])
def test_debug_detail_panel(debug):
    settings = load_settings()
    url = f"{settings.gemini.api_base}/models/{settings.gemini.model}:generateContent"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.post(url, status=403, json={"error": {"status": "PERMISSION_DENIED", "message": "key invalid"}})
        at = new_app(DEBUG=debug).run()
        at.session_state["grade_id"] = "g2"
        at.session_state["task_id"] = "opinion"
        at.session_state["question"] = SAMPLE_QUESTION
        at.session_state["answer"] = SAMPLE_ANSWER
        at.session_state["step"] = 3
        at.run()
        button(at, "この内容で採点する").click().run()
    assert any("API キーが無効" in e.value for e in at.error)
    codes = [c.value for c in at.code]
    if debug:
        assert any("HTTP 403 PERMISSION_DENIED" in c and "key invalid" in c for c in codes)
    else:
        assert not codes


def test_result_screen_is_single_vertical_list(api):
    at = new_app().run()
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["answer"] = SAMPLE_ANSWER
    at.session_state["step"] = 3
    at.run()
    button(at, "この内容で採点する").click().run()
    assert at.session_state["step"] == 4
    assert len(at.tabs) == 0  # タブは使わない
    sections = [m.value for m in at.markdown if 'class="eg-section"' in m.value]
    titles = ["観点別スコア", "添削", "改善版の答案・模範解答", "総評・アドバイス", "提出された答案", "レポートを保存"]
    assert len(sections) == len(titles)
    for i, (html, title) in enumerate(zip(sections, titles, strict=True), 1):
        assert f'<span class="no">{i}</span>{title}' in html
    # 観点ごとの詳しい評価は expander、ダウンロードボタンは最後のセクションの後
    assert [e.label for e in at.expander if "/ 4）" in e.label] == [
        "内容（3 / 4）", "構成（3 / 4）", "語彙（2 / 4）", "文法（2 / 4）"
    ]
    corr_cards = [m.value for m in at.markdown if 'class="eg-corr"' in m.value]
    assert len(corr_cards) == 5 and "becuase" in corr_cards[0]


def test_review_shows_word_count_without_paragraphs():
    at = new_app().run()
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["answer"] = SAMPLE_ANSWER
    at.session_state["step"] = 3
    at.run()
    page = " ".join(m.value for m in at.markdown)
    assert "語数: 72語" in page and "目安 80〜100語・不足" in page
    assert "段落" not in page


def test_question_ocr_mode_is_default():
    assert components.Q_MODE_OPTIONS.index(components.Q_MODE_IMAGE) == 0  # 先頭（index=0）が OCR
    assert components.MODE_OPTIONS.index(components.MODE_IMAGE) == 0
    at = new_app().run()
    assert at.session_state["w_q_mode"] == components.Q_MODE_IMAGE  # 答案と同じく OCR を第一優先の動線に
    pills = at.get("button_group")
    # 先頭の絵文字はアイコンとして分離表示されるため、ラベル本文で並び順を確認する
    shown = [pills[0].proto.options[i].content for i in range(2)]
    expected = ["問題用紙を撮影・アップロード", "手入力"]
    assert shown == [o.split(" ", 1)[1] for o in components.Q_MODE_OPTIONS] == expected
    assert at.get("file_uploader")
    assert button(at, "問題文を読み取る").disabled  # 画像が無いうちは押せない（API も呼ばない）


def test_question_manual_mode_calls_no_api():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        at = new_app().run()
        at.session_state["w_q_mode"] = components.Q_MODE_TYPE
        at.run()
        assert not at.get("file_uploader")  # 手入力モードではアップローダーを出さない
        at.text_area(key="w_question").input(SAMPLE_QUESTION).run()
        button(at, "次へ").click().run()
        assert at.session_state["step"] == 2
        assert len(rsps.calls) == 0


def test_question_image_mode_shows_uploader_and_read_button():
    at = new_app().run()
    at.session_state["w_q_mode"] = components.Q_MODE_IMAGE
    at.run()
    assert at.get("file_uploader")
    read_btn = button(at, "問題文を読み取る")
    assert read_btn.disabled  # 画像が無いうちは押せない


def test_question_ocr_result_is_reflected_in_editable_textarea():
    """OCR 結果が問題文の入力欄に反映され、そのまま編集できること（ウィジェット更新エラーが出ないこと）。"""
    from eiken_grader.services.image_utils import prepare_image
    from tests.conftest import make_image_bytes

    settings = load_settings()
    url = f"{settings.gemini.api_base}/models/{settings.gemini.model}:generateContent"
    passage = "TOPIC: Should schools ban smartphones?\n\nPOINTS\nSafety\nStudy"
    with responses.RequestsMock() as rsps:
        rsps.post(url, json=gemini_response({"text": passage, "uncertain_words": [], "notes": ""}))
        at = new_app().run()
        at.session_state["w_q_mode"] = components.Q_MODE_IMAGE
        at.text_area(key="w_question").input("old text").run()
        # 「問題文を読み取る」ボタン押下時と同じ状態にする（画像を保持して処理を予約）
        at.session_state["question_images"] = [prepare_image(make_image_bytes())]
        at.session_state["is_busy"] = True
        at.session_state["pending_action"] = "question_ocr"
        at.run()
        assert not at.exception
        assert len(rsps.calls) == 1
    assert at.text_area(key="w_question").value == passage
    assert at.session_state["question"] == passage
    assert at.session_state["step"] == 1
    assert any("問題文を読み取りました" in i.value for i in at.info)
    # 読み取り後に手で修正でき、その内容で次へ進める
    at.text_area(key="w_question").input(passage + "\nedited").run()
    button(at, "次へ").click().run()
    assert at.session_state["question"].endswith("edited")
    assert at.session_state["step"] == 2


def test_stepper_marks_current_and_done_steps():
    at = new_app().run()
    stepper = next(m.value for m in at.markdown if m.value.startswith('<ol class="eg-stepper"'))
    assert stepper.count('class="eg-stp active"') == 1 and "aria-current" in stepper
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["step"] = 3
    at.run()
    stepper = next(m.value for m in at.markdown if m.value.startswith('<ol class="eg-stepper"'))
    assert stepper.count('class="eg-stp done"') == 2 and "✓" in stepper


def test_busy_messages_have_no_time_estimates():
    import ast

    tree = ast.parse((Path(APP)).read_text(encoding="utf-8"))
    texts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    busy = [t for t in texts if t.endswith("ています…")]
    assert set(busy) == {"手書きの文字を読み取っています…", "問題文を読み取っています…", "AI が採点しています…"}
    assert not any("秒" in t for t in busy)


def test_header_has_enough_top_padding():
    import re

    from eiken_grader.ui.styles import CSS

    # Streamlit Cloud の固定ヘッダーの下からコンテンツが始まるよう 5rem 以上の上余白を強制
    m = re.search(r"padding-top: calc\(([\d.]+)rem \+ env\(safe-area-inset-top, 0px\)\) !important;", CSS)
    assert m and float(m.group(1)) >= 5
    for selector in (".block-container", ".main .block-container", '[data-testid="stMainBlockContainer"]'):
        assert selector in CSS
    # 標準ヘッダーは背景を透明に
    assert re.search(r'header\[data-testid="stHeader"\] \{\s*background: transparent !important;', CSS)


def test_review_answer_images_are_collapsed():
    from eiken_grader.services.image_utils import prepare_image
    from tests.conftest import make_image_bytes

    at = new_app().run()
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["answer"] = SAMPLE_ANSWER
    at.session_state["images"] = [prepare_image(make_image_bytes())]
    at.session_state["step"] = 3
    at.run()
    exp = [e for e in at.expander if e.label == "📷 答案画像を確認する"]
    assert len(exp) == 1
    assert exp[0].proto.expanded is False  # 初期状態は折りたたみ
    assert len(exp[0].get("image")) == 1  # 画像は折りたたみの中にだけある
    assert len(at.get("image")) == 1


@pytest.mark.parametrize("stale", [None, "⌨️ 手入力（旧ラベル）", "unknown"])
def test_question_mode_falls_back_to_ocr_for_stale_values(stale):
    at = new_app()
    at.session_state["w_q_mode"] = stale
    at.run()
    assert not at.exception
    assert at.session_state["w_q_mode"] == components.Q_MODE_IMAGE
    assert at.get("file_uploader")


def test_answer_mode_default_is_ocr():
    at = new_app().run()
    at.session_state["grade_id"] = "g2"
    at.session_state["task_id"] = "opinion"
    at.session_state["question"] = SAMPLE_QUESTION
    at.session_state["step"] = 2
    at.run()
    assert at.session_state["w_mode"] == components.MODE_IMAGE
    assert at.get("file_uploader")


def test_correction_card_uses_highlight_badges_not_strikethrough():
    from eiken_grader.models.schemas import VerifiedCorrection
    from eiken_grader.ui.styles import CSS

    c = VerifiedCorrection(original="people <uses>", corrected="people use", category="文法",
                           explanation="三単現の s は不要", verified=True)
    html = components.correction_card(2, c)
    assert '<span class="orig" title="修正前">people &lt;uses&gt;</span>' in html  # エスケープ済み
    assert '<span class="arrow" aria-label="を修正">➔</span>' in html
    assert '<span class="new" title="修正後">people use</span>' in html
    assert "（参考）" not in html
    assert "（参考）" in components.correction_card(1, c.model_copy(update={"verified": False}))
    # 打消し線は使わず、背景ハイライトのバッジで表示する
    assert "line-through" not in CSS
    assert "background-color: #fee2e2; color: #dc2626;" in CSS
    assert "background-color: #dcfce7; color: #16a34a; font-weight: 700;" in CSS
    assert "box-decoration-break: clone" in CSS
