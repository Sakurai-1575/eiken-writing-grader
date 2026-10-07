"""採点結果 → Markdown 文字列。

API や Streamlit に依存しない純関数。入力は保存済みの ``GradingReport`` のみ。
"""

from __future__ import annotations

import re

from eiken_grader.models.schemas import GradingReport

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def md_escape(text: str) -> str:
    """Markdown の書式として解釈される記号をエスケープする。"""
    return _MD_SPECIAL.sub(r"\\\1", text)


def _cell(text: str) -> str:
    return md_escape(text).replace("\r", "").replace("\n", "<br>")


def _quote(text: str) -> str:
    lines = text.strip().splitlines() or [""]
    return "\n".join(f"> {md_escape(line)}" if line.strip() else ">" for line in lines)


def report_filename(report: GradingReport, ext: str) -> str:
    return f"eiken_writing_report_{report.graded_at:%Y%m%d_%H%M}.{ext}"


def render_markdown(report: GradingReport) -> str:
    r = report
    out: list[str] = []
    out.append("# 英検ライティング 採点レポート\n")
    out.append(f"- 級・形式: 英検{r.grade_label} {r.task_label}")
    out.append(f"- 採点日時: {r.graded_at:%Y-%m-%d %H:%M}")
    out.append(f"- 語数: {r.word_count_comment}")
    out.append(f"- 採点モデル: {r.model}")
    out.append("")
    out.append(f"## 総合得点: {r.total_score} / {r.max_total}\n")
    if r.off_topic:
        out.append("> **注意:** 答案が課題の指示に沿っていないと判定されました。\n")

    out.append("## 観点別スコア\n")
    out.append("| 観点 | 得点 | 採点の根拠 |")
    out.append("|---|:---:|---|")
    for s in r.scores:
        out.append(f"| {_cell(s.criterion)} | {s.score} / {s.max_score} | {_cell(s.rationale)} |")
    out.append("")
    for s in r.scores:
        out.append(f"### {md_escape(s.criterion)}（{s.score} / {s.max_score}）\n")
        if s.good_points:
            out.append("**良かった点**\n")
            out.extend(f"- {md_escape(p)}" for p in s.good_points)
            out.append("")
        if s.improvements:
            out.append("**改善点**\n")
            out.extend(f"- {md_escape(p)}" for p in s.improvements)
            out.append("")

    out.append("## 問題\n")
    out.append(_quote(r.question))
    out.append("")
    out.append("## あなたの答案\n")
    out.append(_quote(r.answer))
    out.append("")

    out.append("## 添削\n")
    if r.corrections:
        out.append("| # | 元の表現 | 修正案 | 種類 | 解説 |")
        out.append("|:-:|---|---|:-:|---|")
        for i, c in enumerate(r.corrections, 1):
            note = "" if c.verified else "（参考）"
            out.append(
                f"| {i} | {_cell(c.original)} | {_cell(c.corrected)} | {c.category} | "
                f"{_cell(c.explanation)}{note} |"
            )
        if any(not c.verified for c in r.corrections):
            out.append("\n※「参考」は答案中に同じ表現が見つからなかった指摘です。")
    else:
        out.append("大きな誤りは見つかりませんでした。")
    out.append("")

    out.append("## 改善版の答案\n")
    out.append(_quote(r.improved_answer))
    out.append("")
    out.append("## 模範解答\n")
    out.append(_quote(r.model_answer))
    out.append("")
    out.append("## 総評\n")
    out.append(md_escape(r.overall_comment))
    out.append("")
    out.append("## 次のステップ\n")
    out.extend(f"{i}. {md_escape(s)}" for i, s in enumerate(r.next_steps, 1))
    out.append("")
    out.append("---")
    out.append("※ AI による参考採点です。実際の英検の得点を保証するものではありません。")
    return "\n".join(out) + "\n"
