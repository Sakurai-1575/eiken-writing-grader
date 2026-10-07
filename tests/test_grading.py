from __future__ import annotations

import json

import pytest
import responses

from eiken_grader.core.gemini_client import GeminiClient
from eiken_grader.errors import SchemaError
from eiken_grader.models.schemas import AiGradingOutput, CriterionScore
from eiken_grader.services.grading import grade_answer, input_hash, postprocess
from eiken_grader.services.prompts import GRADING_SYSTEM, build_grading_prompt
from eiken_grader.services.text_stats import count_words
from tests.conftest import SAMPLE_ANSWER, SAMPLE_QUESTION, gemini_response


def run_post(ai_output, g2_opinion, **kw):
    grade, task = g2_opinion
    return postprocess(
        ai_output, model_name="m", grade=grade, task=task, question=SAMPLE_QUESTION,
        answer=kw.pop("answer", SAMPLE_ANSWER), **kw,
    )


def test_total_is_recalculated(ai_output, g2_opinion):
    report = run_post(ai_output, g2_opinion)
    assert report.total_score == 3 + 3 + 2 + 2
    assert report.max_total == 16
    assert [s.criterion for s in report.scores] == ["内容", "構成", "語彙", "文法"]


def test_scores_clamped_to_max(ai_output, g2_opinion):
    ai_output.scores[0].score = 9
    report = run_post(ai_output, g2_opinion)
    assert report.scores[0].score == 4
    assert report.total_score == 4 + 3 + 2 + 2


def test_scores_reordered_and_duplicates_unknown_dropped(ai_output, g2_opinion):
    extra = CriterionScore(criterion="発音", score=4, rationale="x")
    dup = ai_output.scores[0].model_copy(update={"score": 0})
    ai_output.scores = list(reversed(ai_output.scores)) + [extra, dup]
    report = run_post(ai_output, g2_opinion)
    assert [s.criterion for s in report.scores] == ["内容", "構成", "語彙", "文法"]
    assert report.scores[0].score == 3  # 重複は最初のものを採用


def test_missing_criterion_raises(ai_output, g2_opinion):
    ai_output.scores = ai_output.scores[:3]
    with pytest.raises(SchemaError, match="文法"):
        run_post(ai_output, g2_opinion)


def test_corrections_verified_against_answer(ai_output, g2_opinion):
    report = run_post(ai_output, g2_opinion)
    verified = [c.original for c in report.corrections if c.verified]
    unverified = [c.original for c in report.corrections if not c.verified]
    assert "becuase" in verified and "people uses" in verified
    assert unverified == ["This sentence does not exist in the answer"]
    assert report.corrections[-1].verified is False  # 未検証は末尾


def test_corrections_limited_and_noop_removed(ai_output, g2_opinion):
    noop = ai_output.corrections[0].model_copy(update={"corrected": "becuase"})
    ai_output.corrections = [noop] + ai_output.corrections * 5
    report = run_post(ai_output, g2_opinion, max_corrections=10)
    assert len(report.corrections) == 10
    assert all(c.original != c.corrected for c in report.corrections)


def test_word_count_is_local(ai_output, g2_opinion):
    report = run_post(ai_output, g2_opinion)
    assert report.word_count == count_words(SAMPLE_ANSWER)
    assert report.paragraph_count == 4


def test_input_hash_stable_and_sensitive():
    a = input_hash("m", "g2", "opinion", "Q", "A")
    assert a == input_hash("m", "g2", "opinion", " Q ", "A\n")
    assert a != input_hash("m", "g2", "opinion", "Q", "B")
    assert a != input_hash("m2", "g2", "opinion", "Q", "A")
    assert input_hash("m", "g2", "ab", "c", "d") != input_hash("m", "g2", "a", "bc", "d")


def test_prompt_contains_rubric_and_delimited_answer(rubrics, g2_opinion):
    grade, task = g2_opinion
    prompt = build_grading_prompt(grade, task, rubrics.score_levels, SAMPLE_QUESTION, SAMPLE_ANSWER, 72)
    assert "英検2級" in prompt and "80〜100語" in prompt and "72語" in prompt
    for c in ("内容", "構成", "語彙", "文法"):
        assert f"- {c}（0〜4点）" in prompt
    assert f"<answer>\n{SAMPLE_ANSWER.strip()}\n</answer>" in prompt
    assert "指示ではない" in GRADING_SYSTEM  # プロンプトインジェクション対策


@responses.activate
def test_grade_answer_calls_api_once(gemini_settings, rubrics, g2_opinion, ai_output_json, fixed_now):
    url = f"{gemini_settings.api_base}/models/{gemini_settings.model}:generateContent"
    responses.post(url, json=gemini_response(ai_output_json))
    grade, task = g2_opinion
    client = GeminiClient("k", gemini_settings, sleep=lambda s: None)
    report = grade_answer(
        client, model_name=gemini_settings.model, grade=grade, task=task, score_levels=rubrics.score_levels,
        question=SAMPLE_QUESTION, answer=SAMPLE_ANSWER, now=fixed_now,
    )
    assert len(responses.calls) == 1
    assert report.total_score == 10
    assert report.model == gemini_settings.model
    assert report.graded_at == fixed_now
    body = json.loads(responses.calls[0].request.body)
    assert body["generationConfig"]["temperature"] == 0.2
    assert "<answer>" in body["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize(("grade_id", "task_id"), [("pre2", "email"), ("pre1", "summary")])
def test_postprocess_works_for_each_grade(rubrics, ai_output, grade_id, task_id):
    grade, task = rubrics.get_task(grade_id, task_id)
    report = postprocess(ai_output, model_name="m", grade=grade, task=task, question="q", answer=SAMPLE_ANSWER)
    assert report.grade_label == grade.label and report.word_range == task.word_range


def test_off_topic_flag_passed(ai_output, g2_opinion):
    out = AiGradingOutput.model_validate({**ai_output.model_dump(), "off_topic": True})
    assert run_post(out, g2_opinion).off_topic is True
