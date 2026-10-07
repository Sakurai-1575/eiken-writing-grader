"""データモデル。

- ``OcrResult`` / ``AiGradingOutput`` … Gemini の構造化出力スキーマ（AI に返させる形）
- ``GradingReport`` … 後処理で検証・再計算した最終結果。画面表示・PDF・Markdown の唯一の入力
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from eiken_grader.services.text_stats import word_range_status

CorrectionCategory = Literal["文法", "語彙", "スペル", "構成", "その他"]


# ---------------------------------------------------------------------------
# AI 出力スキーマ
# ---------------------------------------------------------------------------
class OcrResult(BaseModel):
    text: str = Field(description="手書き英文の書き起こし。誤りは直さず原文どおり。段落は空行で区切る")
    uncertain_words: list[str] = Field(
        default_factory=list, description="読み取りに自信がない語（書き起こし上の表記のまま）"
    )
    notes: str = Field(default="", description="画像の状態についての補足（切れている、かすれている等）。日本語")


class CriterionScore(BaseModel):
    criterion: str = Field(description="観点名。指定された観点名のいずれかを正確に記載")
    score: int = Field(ge=0, description="得点（0 以上、観点の満点以下の整数）")
    rationale: str = Field(description="採点の根拠（日本語、150字程度）")
    good_points: list[str] = Field(default_factory=list, description="良かった点（日本語、最大2件）")
    improvements: list[str] = Field(default_factory=list, description="改善点（日本語、最大2件）")


class Correction(BaseModel):
    original: str = Field(description="答案中の誤りを含む表現（答案から正確に抜き出す）")
    corrected: str = Field(description="修正後の英語表現")
    category: CorrectionCategory = Field(description="誤りの種類")
    explanation: str = Field(description="修正理由の解説（日本語、80字程度）")


class AiGradingOutput(BaseModel):
    off_topic: bool = Field(description="答案が課題・指示に沿っていない場合 true")
    scores: list[CriterionScore] = Field(description="観点ごとの採点。指定された全観点を1件ずつ")
    corrections: list[Correction] = Field(description="重要度の高い順の添削（最大10件）")
    improved_answer: str = Field(description="学習者の答案の内容を活かして改善した英文")
    model_answer: str = Field(description="同じ課題に対する模範解答（語数目安を守った英文）")
    overall_comment: str = Field(description="総評（日本語、励ましを含め200字程度）")
    next_steps: list[str] = Field(description="次回に向けた具体的な学習アドバイス（日本語、3件）")


# ---------------------------------------------------------------------------
# 最終結果（後処理済み）
# ---------------------------------------------------------------------------
class ScoredCriterion(CriterionScore):
    max_score: int


class VerifiedCorrection(Correction):
    verified: bool = Field(description="original が答案中に実在することを確認できたか")


class GradingReport(BaseModel):
    grade_label: str
    task_label: str
    question: str
    answer: str
    word_count: int
    word_range: tuple[int, int]
    paragraph_count: int
    scores: list[ScoredCriterion]
    total_score: int
    max_total: int
    off_topic: bool
    corrections: list[VerifiedCorrection]
    improved_answer: str
    model_answer: str
    overall_comment: str
    next_steps: list[str]
    model: str
    graded_at: datetime

    @property
    def word_count_status(self) -> str:
        return word_range_status(self.word_count, self.word_range)

    @property
    def word_count_comment(self) -> str:
        lo, hi = self.word_range
        return f"{self.word_count}語（目安 {lo}〜{hi}語・{self.word_count_status}）"
