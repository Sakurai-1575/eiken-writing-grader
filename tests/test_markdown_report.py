from __future__ import annotations

from eiken_grader.reports.markdown_report import md_escape, render_markdown, report_filename


def test_all_sections_present(report):
    md = render_markdown(report)
    for heading in (
        "# 英検ライティング 採点レポート",
        "## 総合得点: 10 / 16",
        "## 観点別スコア",
        "## 問題",
        "## あなたの答案",
        "## 添削",
        "## 改善版の答案",
        "## 模範解答",
        "## 総評",
        "## 次のステップ",
    ):
        assert heading in md
    assert "英検2級 英作文（意見論述）" in md
    assert "| 内容 | 3 / 4 |" in md
    assert "gemini-2.5-flash" in md
    assert "（参考）" in md  # 未検証の添削


def test_special_characters_escaped(report):
    report = report.model_copy(
        update={
            "overall_comment": "**bold** | pipe <script>alert(1)</script>",
            "answer": "a | b\n\n*c*",
        }
    )
    md = render_markdown(report)
    assert r"\*\*bold\*\* \| pipe \<script\>" in md
    assert "<script>" not in md
    assert r"> a \| b" in md


def test_md_escape():
    assert md_escape("a_b*c|d") == r"a\_b\*c\|d"
    assert md_escape("don't") == "don't"


def test_filename_has_no_personal_info(report):
    assert report_filename(report, "md") == "eiken_writing_report_20261007_1530.md"
