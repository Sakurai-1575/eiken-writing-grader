from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from eiken_grader.config import GeminiSettings, load_rubrics
from eiken_grader.models.schemas import AiGradingOutput, GradingReport
from eiken_grader.services.grading import postprocess

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE_QUESTION = (
    "TOPIC: Some people say that more people will use electric cars in the future. "
    "Do you agree with this opinion?\nPOINTS: Environment / Cost / Technology"
)
SAMPLE_ANSWER = (
    "I agree that more people will use electric cars in the future becuase of two reasons.\n\n"
    "First, electric cars are good for the enviroment. They do not make gas, so it is good for earth.\n\n"
    "Second, the price is going down. Now many people uses electric cars in my town, "
    "and my father wants to buy one too.\n\n"
    "In conclusion, I agree that more people will use electric cars in the future."
)


@pytest.fixture
def rubrics():
    return load_rubrics()


@pytest.fixture
def g2_opinion(rubrics):
    return rubrics.get_task("g2", "opinion")


@pytest.fixture
def ai_output() -> AiGradingOutput:
    return AiGradingOutput.model_validate_json((FIXTURES / "sample_grading.json").read_text(encoding="utf-8"))


@pytest.fixture
def ai_output_json() -> str:
    return (FIXTURES / "sample_grading.json").read_text(encoding="utf-8")


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 10, 7, 15, 30, tzinfo=timezone(timedelta(hours=9)))


@pytest.fixture
def report(ai_output, g2_opinion, fixed_now) -> GradingReport:
    grade, task = g2_opinion
    return postprocess(
        ai_output,
        model_name="gemini-2.5-flash",
        grade=grade,
        task=task,
        question=SAMPLE_QUESTION,
        answer=SAMPLE_ANSWER,
        now=fixed_now,
    )


@pytest.fixture
def gemini_settings() -> GeminiSettings:
    return GeminiSettings(max_retries=2, backoff_base_sec=0.0, timeout_sec=5)


def gemini_response(payload: dict | str, finish_reason: str = "STOP") -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason}]
    }


def make_image_bytes(size=(400, 300), fmt="JPEG", color=(255, 255, 255), exif_orientation=None) -> bytes:
    img = Image.new("RGB" if fmt != "PNG" else "RGBA", size, color)
    buf = io.BytesIO()
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation  # Orientation
        exif[0x010F] = "TestCamera"  # Make
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()
