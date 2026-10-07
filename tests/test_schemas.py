from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from eiken_grader.models.schemas import AiGradingOutput, OcrResult


def test_sample_fixture_is_valid(ai_output):
    assert len(ai_output.scores) == 4
    assert ai_output.corrections[0].category == "スペル"


def test_negative_score_rejected(ai_output_json):
    data = json.loads(ai_output_json)
    data["scores"][0]["score"] = -1
    with pytest.raises(ValidationError):
        AiGradingOutput.model_validate(data)


def test_unknown_correction_category_rejected(ai_output_json):
    data = json.loads(ai_output_json)
    data["corrections"][0]["category"] = "発音"
    with pytest.raises(ValidationError):
        AiGradingOutput.model_validate(data)


def test_missing_required_field_rejected(ai_output_json):
    data = json.loads(ai_output_json)
    del data["model_answer"]
    with pytest.raises(ValidationError):
        AiGradingOutput.model_validate(data)


def test_ocr_defaults():
    r = OcrResult.model_validate({"text": "hello"})
    assert r.uncertain_words == [] and r.notes == ""


def test_report_word_count_comment(report):
    assert report.word_range == (80, 100)
    assert report.word_count_comment.startswith(f"{report.word_count}語")
    assert report.word_count_status == "不足"
