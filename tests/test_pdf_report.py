from __future__ import annotations

import io
import re

import pytest
from pypdf import PdfReader

from eiken_grader.models.schemas import VerifiedCorrection
from eiken_grader.reports import pdf_report
from eiken_grader.reports.pdf_report import render_pdf
from tests.conftest import SAMPLE_ANSWER

A4_PT = (595.28, 841.89)


def read(pdf: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(pdf))


def variant(report, kind: str):
    if kind == "normal":
        return report
    if kind == "minimal":
        return report.model_copy(
            update={"corrections": [], "next_steps": [], "answer": "I agree.", "question": "Q",
                    "improved_answer": "I agree.", "model_answer": "I agree.", "overall_comment": "良い。"}
        )
    if kind == "max_corrections":
        many = [
            VerifiedCorrection(original=f"wrong phrase number {i} " * 3, corrected=f"right phrase number {i} " * 3,
                               category="文法", explanation="とても長い解説です。" * 8, verified=i % 2 == 0)
            for i in range(10)
        ]
        return report.model_copy(update={"corrections": many})
    if kind == "very_long":
        return report.model_copy(
            update={
                "answer": "\n\n".join([SAMPLE_ANSWER] * 5),
                "question": "This is a long passage to summarize. " * 60,
                "improved_answer": "An improved sentence that is quite long. " * 60,
                "model_answer": "A model answer sentence that keeps going. " * 60,
                "overall_comment": "総評がとても長い場合のテストです。" * 40,
                "next_steps": ["次のステップの説明がとても長い場合。" * 10] * 5,
            }
        )
    if kind == "long_token":
        return report.model_copy(update={"answer": "a" * 600 + " " + "日本語の長い文章" * 50})
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["normal", "minimal", "max_corrections", "very_long", "long_token"])
def test_always_two_a4_pages(report, kind):
    pdf = render_pdf(variant(report, kind))
    assert pdf.startswith(b"%PDF")
    reader = read(pdf)
    assert len(reader.pages) == 2
    for page in reader.pages:
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        assert w == pytest.approx(A4_PT[0], abs=1) and h == pytest.approx(A4_PT[1], abs=1)


def test_japanese_font_embedded_and_text_extractable(report):
    reader = read(render_pdf(report))
    fonts = set()
    for page in reader.pages:
        for font in page["/Resources"]["/Font"].values():
            obj = font.get_object()
            fonts.add(str(obj["/BaseFont"]))
            desc = obj["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
            assert "/FontFile2" in desc  # TrueType が埋め込まれている
    assert any("NotoSansJP" in f for f in fonts)
    p1 = reader.pages[0].extract_text()
    p2 = reader.pages[1].extract_text()
    assert "採点レポート" in p1 and "観点別の評価" in p1 and "becuase" in p1
    assert "添削" in p2 and "模範解答" in p2


def test_truncation_note_only_when_needed(report):
    assert pdf_report.TRUNCATION_NOTE.strip() not in "".join(
        p.extract_text() for p in read(render_pdf(report)).pages
    )
    long_text = "".join(p.extract_text() for p in read(render_pdf(variant(report, "very_long"))).pages)
    assert "以下省略" in long_text


def test_fit_blocks_scales_before_truncating(report):
    pdf = pdf_report._new_pdf()
    pdf.add_page()
    blocks = pdf_report._page1(report)
    fitted, scale = pdf_report.fit_blocks(pdf, blocks, 1000)
    assert scale == 1.0 and fitted == blocks
    needed = pdf_report._total_height(pdf, blocks, 1.0)
    _, scale = pdf_report.fit_blocks(pdf, blocks, needed * 0.93)
    assert scale < 1.0


# --- モノクロ印刷 ------------------------------------------------------------
_RGB_OP = re.compile(rb"([\d.]+) ([\d.]+) ([\d.]+) (rg|RG)\b")


def test_palette_is_grayscale_only():
    assert pdf_report.PALETTE == ((0, 0, 0), (51, 51, 51), (229, 229, 229), (245, 245, 245))
    for r, g, b in pdf_report.PALETTE:
        assert r == g == b


@pytest.mark.parametrize("kind", ["normal", "very_long"])
def test_pdf_uses_no_chromatic_color(report, kind):
    """PDF 内のすべての色指定がグレー（R=G=B）であること（白黒プリンタ向け）。"""
    off_topic = variant(report, kind).model_copy(update={"off_topic": True, "word_count": 10})
    reader = read(render_pdf(off_topic))
    for page in reader.pages:
        data = page.get_contents().get_data()
        for m in _RGB_OP.finditer(data):
            r, g, b = (float(x) for x in m.groups()[:3])
            assert r == g == b, f"chromatic color found: {m.group(0)!r}"
