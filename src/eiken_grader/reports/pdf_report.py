"""採点結果 → PDF（A4・2 ページ固定、日本語フォント埋め込み）。

API や Streamlit に依存しない純関数。入力は保存済みの ``GradingReport`` のみ。

2 ページに収める仕組み:
1. ページごとに描画ブロックの列を組み立てる
2. ``multi_cell(dry_run=True)`` で必要な高さを事前計測する
3. 収まらなければ文字サイズを段階的に縮小（SCALES）
4. 最小サイズでも収まらなければ、優先度の高いブロックから文章を切り詰める
5. 自動改ページは無効化し、ページ追加は明示的に 2 回だけ行う
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import WrapMode, XPos, YPos

from eiken_grader.config import FONTS_DIR
from eiken_grader.errors import ReportError
from eiken_grader.models.schemas import GradingReport

FONT = "NotoSansJP"
FONT_REGULAR = FONTS_DIR / "NotoSansJP-Regular.ttf"
FONT_BOLD = FONTS_DIR / "NotoSansJP-Bold.ttf"

PAGE_W, PAGE_H = 210.0, 297.0
MARGIN_X = 14.0
MARGIN_TOP = 12.0
FOOTER_Y = PAGE_H - 11.0
CONTENT_BOTTOM = FOOTER_Y - 3.0
CONTENT_W = PAGE_W - 2 * MARGIN_X
PT_TO_MM = 0.3528

SCALES = (1.0, 0.95, 0.9, 0.85)  # 本文 9.5pt → 最小約 8.1pt
TRUNCATION_NOTE = " …（以下省略・全文は Markdown 版を参照）"
MAX_PAGES = 2

# モノクロ印刷用パレット（白黒複合機で見やすく、トナーを節約する）
# 大面積の塗りつぶしは使わず、塗りは薄いグレーのみ。強調は太字・記号・罫線で表現する。
BLACK = (0, 0, 0)  # #000000 本文・見出し・強調
DARK = (51, 51, 51)  # #333333 補足文・スコアバーの塗り・罫線（濃）
LINE = (229, 229, 229)  # #E5E5E5 枠線・区切り線（薄）
FILL = (245, 245, 245)  # #F5F5F5 背景の塗りつぶし
PALETTE = (BLACK, DARK, LINE, FILL)

TEXT = BLACK
MUTED = DARK

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text: str) -> str:
    return _CTRL.sub("", text.replace("\r\n", "\n").replace("\t", " ")).strip()


# ---------------------------------------------------------------------------
# ブロック定義
# ---------------------------------------------------------------------------
@dataclass
class TextBlock:
    text: str
    size: float = 9.5
    bold: bool = False
    color: tuple[int, int, int] = TEXT
    english: bool = False  # True: 単語単位で折り返し / False: 文字単位（日本語向け）
    indent: float = 0.0
    boxed: bool = False
    space_before: float = 0.0
    space_after: float = 1.2
    line_factor: float = 1.45
    priority: int = 0  # >0 なら切り詰め可能。大きいほど切り詰められやすい（高さとの積で判定）
    truncated: bool = False

    def _width(self) -> float:
        return CONTENT_W - self.indent - (6.0 if self.boxed else 0.0)

    def _line_h(self, scale: float) -> float:
        return self.size * scale * PT_TO_MM * self.line_factor

    def _wrap(self) -> WrapMode:
        return WrapMode.WORD if self.english else WrapMode.CHAR

    def _set_font(self, pdf: FPDF, scale: float) -> None:
        pdf.set_font(FONT, "B" if self.bold else "", self.size * scale)

    def _lines(self, pdf: FPDF, scale: float) -> int:
        self._set_font(pdf, scale)
        lines = pdf.multi_cell(
            self._width(), self._line_h(scale), self.text, align="L", dry_run=True, output="LINES",
            wrapmode=self._wrap(),
        )
        return max(1, len(lines))

    def height(self, pdf: FPDF, scale: float) -> float:
        h = self._lines(pdf, scale) * self._line_h(scale)
        if self.boxed:
            h += 4.0
        return (self.space_before + self.space_after) * scale + h

    def draw(self, pdf: FPDF, scale: float) -> None:
        pdf.set_y(pdf.get_y() + self.space_before * scale)
        x = MARGIN_X + self.indent
        y = pdf.get_y()
        if self.boxed:
            h = self._lines(pdf, scale) * self._line_h(scale) + 4.0
            pdf.set_fill_color(*FILL)
            pdf.set_draw_color(*LINE)
            pdf.set_line_width(0.3)
            pdf.rect(x, y, CONTENT_W - self.indent, h, style="DF")
            x += 3.0
            y += 2.0
        self._set_font(pdf, scale)
        pdf.set_text_color(*self.color)
        pdf.set_xy(x, y)
        pdf.multi_cell(
            self._width(),
            self._line_h(scale),
            self.text,
            align="L",
            wrapmode=self._wrap(),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        if self.boxed:
            pdf.set_y(pdf.get_y() + 2.0)
        pdf.set_y(pdf.get_y() + self.space_after * scale)

    def shortened(self, ratio: float = 0.8) -> TextBlock:
        """本文を ratio 倍の長さに切り詰めたブロックを返す（単語境界優先）。"""
        body = self.text[: -len(TRUNCATION_NOTE)] if self.truncated else self.text
        target = int(len(body) * ratio)
        if target < 20:
            return replace(self, text=TRUNCATION_NOTE.strip(), truncated=True, priority=0)
        cut = body[:target]
        space = cut.rfind(" ")
        if self.english and space > target * 0.6:
            cut = cut[:space]
        return replace(self, text=cut.rstrip() + TRUNCATION_NOTE, truncated=True)


@dataclass
class SectionTitle:
    title: str
    space_before: float = 3.0

    def height(self, pdf: FPDF, scale: float) -> float:
        return (self.space_before + 7.0) * scale

    def draw(self, pdf: FPDF, scale: float) -> None:
        # 見出し: 左に黒の短いバー + 見出し下に薄グレーの区切り線（塗り面積を最小限に）
        y = pdf.get_y() + self.space_before * scale
        pdf.set_fill_color(*BLACK)
        pdf.rect(MARGIN_X, y + 0.8 * scale, 1.0, 4.6 * scale, style="F")
        pdf.set_font(FONT, "B", 11 * scale)
        pdf.set_text_color(*BLACK)
        pdf.set_xy(MARGIN_X + 3.0, y)
        pdf.cell(CONTENT_W - 3.0, 6.2 * scale, self.title)
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.3)
        pdf.line(MARGIN_X + 3.0, y + 6.4 * scale, PAGE_W - MARGIN_X, y + 6.4 * scale)
        pdf.set_y(y + 7.0 * scale)


@dataclass
class ScoreSummary:
    report: GradingReport
    priority: int = 0

    def height(self, pdf: FPDF, scale: float) -> float:
        return max(30.0, len(self.report.scores) * 6.5 + 8.0) * scale + 2.0

    def draw(self, pdf: FPDF, scale: float) -> None:
        r = self.report
        y0 = pdf.get_y()
        h = self.height(pdf, scale) - 2.0
        # 外枠は濃いグレーの細線、背景は塗らない（トナー節約）
        pdf.set_draw_color(*DARK)
        pdf.set_line_width(0.3)
        pdf.rect(MARGIN_X, y0, CONTENT_W, h, style="D", round_corners=True, corner_radius=2)

        # 左: 総合得点（薄グレーの面 + 黒の太字）
        left_w = 52.0
        pdf.set_fill_color(*FILL)
        pdf.rect(MARGIN_X + 2, y0 + 2, left_w - 4, h - 4, style="F", round_corners=True, corner_radius=1.5)
        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9 * scale)
        pdf.set_xy(MARGIN_X, y0 + 3 * scale)
        pdf.cell(left_w, 5 * scale, "総合得点", align="C")
        pdf.set_text_color(*BLACK)
        pdf.set_font(FONT, "B", 26 * scale)
        pdf.set_xy(MARGIN_X, y0 + 9 * scale)
        pdf.cell(left_w, 11 * scale, f"{r.total_score}", align="C")
        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9 * scale)
        pdf.set_xy(MARGIN_X, y0 + 20 * scale)
        pdf.cell(left_w, 5 * scale, f"/ {r.max_total} 点", align="C")
        in_range = r.word_count_status == "範囲内"
        pdf.set_text_color(*(DARK if in_range else BLACK))
        pdf.set_font(FONT, "" if in_range else "B", 8 * scale)  # 範囲外は太字で強調
        pdf.set_xy(MARGIN_X, y0 + h - 6 * scale)
        pdf.cell(left_w, 4 * scale, r.word_count_comment, align="C")

        # 右: 観点別バー
        #   背景 #F5F5F5 + 枠 #333333、得点部分は #333333 で塗り、1 点ごとに白の目盛り線で区切る
        bx = MARGIN_X + left_w + 4
        label_w, value_w = 16.0, 14.0
        bar_w = CONTENT_W - left_w - 4 - label_w - value_w - 6
        bar_h = 3.0 * scale
        y = y0 + 4 * scale
        for s in r.scores:
            pdf.set_text_color(*BLACK)
            pdf.set_font(FONT, "B", 9.5 * scale)
            pdf.set_xy(bx, y)
            pdf.cell(label_w, 5 * scale, s.criterion)
            x_bar, y_bar = bx + label_w, y + 1.1 * scale
            pdf.set_fill_color(*FILL)
            pdf.set_draw_color(*DARK)
            pdf.set_line_width(0.25)
            pdf.rect(x_bar, y_bar, bar_w, bar_h, style="DF")
            ratio = s.score / s.max_score if s.max_score else 0
            if ratio > 0:
                pdf.set_fill_color(*DARK)
                pdf.rect(x_bar, y_bar, bar_w * ratio, bar_h, style="F")
            if 1 < s.max_score <= 10:
                pdf.set_line_width(0.35)
                for k in range(1, s.max_score):
                    tx = x_bar + bar_w * k / s.max_score
                    # 塗り部分の上は白線、未塗り部分の上は薄グレー線で区切る
                    pdf.set_draw_color(*((255, 255, 255) if k < s.score else LINE))
                    pdf.line(tx, y_bar + 0.3, tx, y_bar + bar_h - 0.3)
            pdf.set_text_color(*BLACK)
            pdf.set_xy(x_bar + bar_w + 2, y)
            pdf.cell(value_w, 5 * scale, f"{s.score} / {s.max_score}", align="R")
            y += 6.5 * scale
        if r.off_topic:
            pdf.set_text_color(*BLACK)
            pdf.set_font(FONT, "B", 8 * scale)
            pdf.set_xy(bx, y)
            pdf.cell(0, 4 * scale, "※ 課題の指示に沿っていないと判定されました")
        pdf.set_y(y0 + h + 2.0)


Block = TextBlock | SectionTitle | ScoreSummary


@dataclass
class PageSpec:
    header: str
    blocks: list[Block] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ページ内容の組み立て
# ---------------------------------------------------------------------------
def _page1(r: GradingReport) -> list[Block]:
    blocks: list[Block] = [ScoreSummary(r), SectionTitle("観点別の評価", space_before=1.0)]
    for s in r.scores:
        blocks.append(
            TextBlock(f"■ {s.criterion}　{s.score} / {s.max_score}", size=10, bold=True, color=BLACK,
                      space_before=0.8, space_after=0.4)
        )
        blocks.append(TextBlock(_clean(s.rationale), indent=3, priority=2, space_after=0.6))
        # 良い点・改善点は色ではなく記号（◎ / △）と「良い点」「改善点」の文字で区別する
        if s.good_points:
            blocks.append(TextBlock("◎ 良い点: " + " / ".join(_clean(p) for p in s.good_points), size=8.8,
                                    color=DARK, indent=3, priority=3, space_after=0.3))
        if s.improvements:
            blocks.append(TextBlock("△ 改善点: " + " / ".join(_clean(p) for p in s.improvements), size=8.8,
                                    color=DARK, indent=3, priority=3, space_after=0.6))
    blocks.append(SectionTitle("問題"))
    blocks.append(TextBlock(_clean(r.question), size=8.5, color=MUTED, english=True, priority=5))
    blocks.append(SectionTitle("あなたの答案"))
    blocks.append(TextBlock(_clean(r.answer), size=10, english=True, boxed=True, priority=1, line_factor=1.55))
    return blocks


def _page2(r: GradingReport) -> list[Block]:
    blocks: list[Block] = [SectionTitle("添削", space_before=0.0)]
    if r.corrections:
        for i, c in enumerate(r.corrections, 1):
            ref = "（参考）" if not c.verified else ""
            blocks.append(
                TextBlock(f"{i}. [{c.category}]{ref}  {_clean(c.original)}  →  {_clean(c.corrected)}",
                          size=9.3, bold=True, english=True, space_after=0.2, priority=1)
            )
            blocks.append(TextBlock(_clean(c.explanation), size=8.6, color=MUTED, indent=5,
                                    priority=2, space_after=0.9))
        if any(not c.verified for c in r.corrections):
            blocks.append(TextBlock("※（参考）は答案中に同じ表現が見つからなかった指摘です。",
                                    size=7.5, color=MUTED))
    else:
        blocks.append(TextBlock("大きな誤りは見つかりませんでした。"))
    blocks.append(SectionTitle("改善版の答案"))
    blocks.append(TextBlock(_clean(r.improved_answer), english=True, boxed=True, priority=3))
    blocks.append(SectionTitle("模範解答"))
    blocks.append(TextBlock(_clean(r.model_answer), english=True, boxed=True, priority=3))
    blocks.append(SectionTitle("総評"))
    blocks.append(TextBlock(_clean(r.overall_comment), priority=2))
    if r.next_steps:
        blocks.append(SectionTitle("次のステップ"))
        for s in r.next_steps:
            blocks.append(TextBlock("・" + _clean(s), indent=1, priority=2, space_after=0.6))
    return blocks


# ---------------------------------------------------------------------------
# 収まり調整
# ---------------------------------------------------------------------------
def _total_height(pdf: FPDF, blocks: list[Block], scale: float) -> float:
    return sum(b.height(pdf, scale) for b in blocks)


def fit_blocks(pdf: FPDF, blocks: list[Block], available: float) -> tuple[list[Block], float]:
    """available(mm) に収まるようにスケールを選び、必要なら切り詰めたブロック列を返す。"""
    for scale in SCALES:
        if _total_height(pdf, blocks, scale) <= available:
            return blocks, scale
    scale = SCALES[-1]
    blocks = list(blocks)
    for _ in range(200):
        if _total_height(pdf, blocks, scale) <= available:
            return blocks, scale
        # 「優先度 × 高さ」が最大のブロックを切り詰める（大きく場所を取るものから少しずつ削る）
        candidates = [
            (b.priority * b.height(pdf, scale), i)
            for i, b in enumerate(blocks)
            if isinstance(b, TextBlock) and b.priority > 0
        ]
        if not candidates:
            break
        _, idx = max(candidates)
        blocks[idx] = blocks[idx].shortened()
    if _total_height(pdf, blocks, scale) > available:
        raise ReportError("レポートの内容が多すぎて PDF に収まりませんでした。")
    return blocks, scale


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
def _new_pdf() -> FPDF:
    for path in (FONT_REGULAR, FONT_BOLD):
        if not Path(path).exists():
            raise ReportError(f"PDF 用フォントが見つかりません: {Path(path).name}")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(MARGIN_X, MARGIN_TOP, MARGIN_X)
    pdf.add_font(FONT, "", str(FONT_REGULAR))
    pdf.add_font(FONT, "B", str(FONT_BOLD))
    pdf.set_title("英検ライティング 採点レポート")
    pdf.set_creator("eiken-writing-grader")
    return pdf


def _draw_header(pdf: FPDF, r: GradingReport, page_no: int) -> float:
    # ベタ塗りの帯は使わず、黒の太罫線 + 細罫線の二重線で区切る（トナー節約）
    if page_no == 1:
        pdf.set_text_color(*BLACK)
        pdf.set_font(FONT, "B", 15)
        pdf.set_xy(MARGIN_X, 7)
        pdf.cell(CONTENT_W, 7, "英検ライティング 採点レポート")
        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9)
        pdf.set_xy(MARGIN_X, 14.5)
        pdf.cell(
            CONTENT_W, 5,
            f"英検{r.grade_label}  {r.task_label}    採点日時 {r.graded_at:%Y-%m-%d %H:%M}",
        )
        pdf.set_draw_color(*BLACK)
        pdf.set_line_width(0.8)
        pdf.line(MARGIN_X, 21, PAGE_W - MARGIN_X, 21)
        pdf.set_line_width(0.2)
        pdf.line(MARGIN_X, 22.2, PAGE_W - MARGIN_X, 22.2)
        return 26.0
    pdf.set_draw_color(*BLACK)
    pdf.set_line_width(0.5)
    pdf.line(MARGIN_X, 15, PAGE_W - MARGIN_X, 15)
    pdf.set_text_color(*DARK)
    pdf.set_font(FONT, "", 8.5)
    pdf.set_xy(MARGIN_X, 9)
    pdf.cell(CONTENT_W, 5, f"英検{r.grade_label} {r.task_label}  —  添削・模範解答")
    return 18.0


def _draw_footer(pdf: FPDF, r: GradingReport, page_no: int) -> None:
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN_X, FOOTER_Y - 1.5, PAGE_W - MARGIN_X, FOOTER_Y - 1.5)
    pdf.set_text_color(*MUTED)
    pdf.set_font(FONT, "", 7.5)
    pdf.set_xy(MARGIN_X, FOOTER_Y)
    pdf.cell(CONTENT_W - 20, 4, f"AI による参考採点です（{r.model}）。実際の英検の得点を保証するものではありません。")
    pdf.set_xy(PAGE_W - MARGIN_X - 20, FOOTER_Y)
    pdf.cell(20, 4, f"{page_no} / {MAX_PAGES}", align="R")


def render_pdf(report: GradingReport) -> bytes:
    """GradingReport から A4・2 ページの PDF を生成して bytes で返す。"""
    try:
        pdf = _new_pdf()
        for page_no, blocks in ((1, _page1(report)), (2, _page2(report))):
            pdf.add_page()
            top = _draw_header(pdf, report, page_no)
            fitted, scale = fit_blocks(pdf, blocks, CONTENT_BOTTOM - top)
            pdf.set_y(top)
            for b in fitted:
                b.draw(pdf, scale)
            _draw_footer(pdf, report, page_no)
        return bytes(pdf.output())
    except ReportError:
        raise
    except Exception as e:  # fpdf2 内部エラーなど
        raise ReportError(f"PDF の作成に失敗しました（{type(e).__name__}）。") from e
