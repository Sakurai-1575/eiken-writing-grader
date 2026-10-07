"""手書き答案の文字起こし（API 呼び出し 1 回）。"""

from __future__ import annotations

from typing import Protocol

from eiken_grader.models.schemas import OcrResult
from eiken_grader.services.image_utils import PreparedImage
from eiken_grader.services.prompts import OCR_SYSTEM, OCR_USER


class JsonGenerator(Protocol):
    def generate_json(self, *, parts, schema, system_instruction, temperature): ...


def transcribe(client: JsonGenerator, images: list[PreparedImage], temperature: float = 0.0) -> OcrResult:
    if not images:
        raise ValueError("images must not be empty")
    parts: list[dict] = [img.to_inline_part() for img in images]
    parts.append({"text": OCR_USER})
    result = client.generate_json(
        parts=parts, schema=OcrResult, system_instruction=OCR_SYSTEM, temperature=temperature
    )
    # 改行コードの正規化と前後空白の除去のみ行う（内容は一切変更しない）
    text = result.text.replace("\r\n", "\n").strip()
    return result.model_copy(update={"text": text})
