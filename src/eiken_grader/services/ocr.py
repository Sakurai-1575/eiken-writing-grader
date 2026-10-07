"""画像の文字起こし（いずれも API 呼び出し 1 回）。

- ``transcribe``          … 手書きの答案（誤りは直さずにそのまま書き起こす）
- ``transcribe_question`` … 問題用紙（印刷された問題文・課題英文）
"""

from __future__ import annotations

from typing import Protocol

from eiken_grader.models.schemas import OcrResult
from eiken_grader.services.image_utils import PreparedImage
from eiken_grader.services.prompts import OCR_SYSTEM, OCR_USER, QUESTION_OCR_SYSTEM, QUESTION_OCR_USER


class JsonGenerator(Protocol):
    def generate_json(self, *, parts, schema, system_instruction, temperature): ...


def _run(
    client: JsonGenerator, images: list[PreparedImage], system: str, user: str, temperature: float
) -> OcrResult:
    if not images:
        raise ValueError("images must not be empty")
    parts: list[dict] = [img.to_inline_part() for img in images]
    parts.append({"text": user})
    result = client.generate_json(parts=parts, schema=OcrResult, system_instruction=system, temperature=temperature)
    # 改行コードの正規化と前後空白の除去のみ行う（内容は一切変更しない）
    text = result.text.replace("\r\n", "\n").strip()
    return result.model_copy(update={"text": text})


def transcribe(client: JsonGenerator, images: list[PreparedImage], temperature: float = 0.0) -> OcrResult:
    """手書きの答案を書き起こす。"""
    return _run(client, images, OCR_SYSTEM, OCR_USER, temperature)


def transcribe_question(
    client: JsonGenerator, images: list[PreparedImage], temperature: float = 0.0
) -> OcrResult:
    """問題用紙の問題文（指示文・TOPIC・POINTS・課題英文・Eメール本文など）を書き起こす。"""
    return _run(client, images, QUESTION_OCR_SYSTEM, QUESTION_OCR_USER, temperature)
