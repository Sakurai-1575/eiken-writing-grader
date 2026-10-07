"""採点（API 呼び出し 1 回）と後処理。

AI の出力は鵜呑みにせず、以下を Python 側で保証する:
- 観点の過不足チェック（欠落はエラー、重複・未知の観点は除外）と rubric 順への並べ替え
- 得点を 0〜満点にクランプし、合計点は Python で再計算
- 語数・段落数はローカル計測値を正とする
- 添削の original が答案に実在するか検証し、実在しないものは「参考」として末尾へ
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from eiken_grader.config import GradeRubric, TaskRubric
from eiken_grader.errors import SchemaError
from eiken_grader.models.schemas import (
    AiGradingOutput,
    GradingReport,
    ScoredCriterion,
    VerifiedCorrection,
)
from eiken_grader.services.ocr import JsonGenerator
from eiken_grader.services.prompts import GRADING_SYSTEM, build_grading_prompt
from eiken_grader.services.text_stats import count_paragraphs, count_words, strip_markers

JST = timezone(timedelta(hours=9))


def input_hash(model: str, grade_id: str, task_id: str, question: str, answer: str) -> str:
    """同一入力の再採点を検出するためのハッシュ（セッション内キャッシュのキー）。"""
    h = hashlib.sha256()
    for part in (model, grade_id, task_id, question.strip(), answer.strip()):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def grade_answer(
    client: JsonGenerator,
    *,
    model_name: str,
    grade: GradeRubric,
    task: TaskRubric,
    score_levels: dict[str, str],
    question: str,
    answer: str,
    temperature: float = 0.2,
    max_corrections: int = 10,
    now: datetime | None = None,
) -> GradingReport:
    word_count = count_words(answer)
    prompt = build_grading_prompt(grade, task, score_levels, question, answer, word_count)
    raw = client.generate_json(
        parts=[{"text": prompt}],
        schema=AiGradingOutput,
        system_instruction=GRADING_SYSTEM,
        temperature=temperature,
    )
    # フォールバックが働いた場合は、実際に応答したモデル名を記録する
    used_model = getattr(client, "last_model", None) or model_name
    return postprocess(
        raw,
        model_name=used_model,
        grade=grade,
        task=task,
        question=question,
        answer=answer,
        max_corrections=max_corrections,
        now=now,
    )


def postprocess(
    raw: AiGradingOutput,
    *,
    model_name: str,
    grade: GradeRubric,
    task: TaskRubric,
    question: str,
    answer: str,
    max_corrections: int = 10,
    now: datetime | None = None,
) -> GradingReport:
    # --- 観点 ---------------------------------------------------------------
    by_name: dict[str, ScoredCriterion] = {}
    for s in raw.scores:
        name = s.criterion.strip()
        if name not in task.criteria or name in by_name:
            continue  # 未知の観点・重複は除外
        score = max(0, min(s.score, task.max_per_criterion))
        by_name[name] = ScoredCriterion(
            criterion=name,
            score=score,
            max_score=task.max_per_criterion,
            rationale=s.rationale.strip(),
            good_points=[p.strip() for p in s.good_points if p.strip()][:3],
            improvements=[p.strip() for p in s.improvements if p.strip()][:3],
        )
    missing = [c for c in task.criterion_names if c not in by_name]
    if missing:
        raise SchemaError(f"AI の採点結果に観点（{'・'.join(missing)}）が含まれていませんでした。再採点してください。")
    scores = [by_name[c] for c in task.criterion_names]

    # --- 添削 ---------------------------------------------------------------
    answer_norm = _normalize(strip_markers(answer))
    verified: list[VerifiedCorrection] = []
    unverified: list[VerifiedCorrection] = []
    for c in raw.corrections:
        original = c.original.strip()
        corrected = c.corrected.strip()
        if not original or _normalize(original) == _normalize(corrected):
            continue
        ok = _normalize(original) in answer_norm
        item = VerifiedCorrection(
            original=original,
            corrected=corrected,
            category=c.category,
            explanation=c.explanation.strip(),
            verified=ok,
        )
        (verified if ok else unverified).append(item)
    corrections = (verified + unverified)[:max_corrections]

    return GradingReport(
        grade_label=grade.label,
        task_label=task.label,
        question=question.strip(),
        answer=answer.strip(),
        word_count=count_words(answer),
        word_range=task.word_range,
        paragraph_count=count_paragraphs(answer),
        scores=scores,
        total_score=sum(s.score for s in scores),
        max_total=task.max_total,
        off_topic=raw.off_topic,
        corrections=corrections,
        improved_answer=raw.improved_answer.strip(),
        model_answer=raw.model_answer.strip(),
        overall_comment=raw.overall_comment.strip(),
        next_steps=[s.strip() for s in raw.next_steps if s.strip()][:5],
        model=model_name,
        graded_at=(now or datetime.now(JST)),
    )
