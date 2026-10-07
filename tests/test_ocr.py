from __future__ import annotations

import json

import pytest
import responses

from eiken_grader.core.gemini_client import GeminiClient
from eiken_grader.models.schemas import OcrResult
from eiken_grader.services.image_utils import prepare_image
from eiken_grader.services.ocr import transcribe
from eiken_grader.services.prompts import OCR_SYSTEM
from tests.conftest import gemini_response, make_image_bytes


@pytest.fixture
def images():
    return [prepare_image(make_image_bytes()), prepare_image(make_image_bytes(color=(0, 0, 0)))]


@responses.activate
def test_transcribe_sends_images_and_returns_result(gemini_settings, images):
    url = f"{gemini_settings.api_base}/models/{gemini_settings.model}:generateContent"
    ocr_json = {"text": "I thinks {becuase?} it is good.\r\n\r\nSecond [?].  ", "uncertain_words": ["becuase"],
                "notes": ""}
    responses.post(url, json=gemini_response(ocr_json))
    client = GeminiClient("k", gemini_settings, sleep=lambda s: None)

    result = transcribe(client, images)

    assert isinstance(result, OcrResult)
    # 誤りはそのまま・改行のみ正規化
    assert result.text == "I thinks {becuase?} it is good.\n\nSecond [?]."
    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    parts = body["contents"][0]["parts"]
    assert [("inline_data" in p) for p in parts] == [True, True, False]
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert body["generationConfig"]["temperature"] == 0.0
    assert "NEVER fix spelling" in body["systemInstruction"]["parts"][0]["text"]


def test_ocr_prompt_forbids_corrections():
    assert "NEVER fix spelling" in OCR_SYSTEM
    assert "[?]" in OCR_SYSTEM and "{word?}" in OCR_SYSTEM


def test_transcribe_requires_images():
    with pytest.raises(ValueError):
        transcribe(object(), [])
