"""Streamlit AppTest による画面遷移テスト（Gemini API は responses でモック）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import responses
from streamlit.testing.v1 import AppTest

from eiken_grader.config import load_settings
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
    at.session_state["w_mode"] = "直接入力する"
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
    assert any("10 <span" in m.value for m in at.markdown)  # 総合得点

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
