"""答案テキストのローカル集計（API を使わない）。"""

from __future__ import annotations

import re

ILLEGIBLE_MARK = "[?]"
_UNCERTAIN_RE = re.compile(r"\{([^{}]*?)\?\}")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


def strip_markers(text: str) -> str:
    """OCR のマーカーを除去する。``{word?}`` → ``word``、``[?]`` → 空。"""
    text = _UNCERTAIN_RE.sub(r"\1", text)
    return text.replace(ILLEGIBLE_MARK, " ")


def count_words(text: str) -> int:
    """英検の語数カウントに近い方法で語数を数える。

    - 空白・句読点で区切られた英数字のかたまりを 1 語とする
    - 縮約形（don't）・ハイフン語（well-known）は 1 語
    - 判読不能マーカー ``[?]`` は数えない
    """
    return len(_WORD_RE.findall(strip_markers(text)))


def count_paragraphs(text: str) -> int:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return len(blocks)


def illegible_count(text: str) -> int:
    return text.count(ILLEGIBLE_MARK)


def uncertain_marked_words(text: str) -> list[str]:
    """``{word?}`` で示された要確認語の一覧。"""
    return [w for w in _UNCERTAIN_RE.findall(text) if w.strip()]


def has_markers(text: str) -> bool:
    return illegible_count(text) > 0 or bool(uncertain_marked_words(text))


def word_range_status(count: int, word_range: tuple[int, int]) -> str:
    lo, hi = word_range
    if count < lo:
        return "不足"
    if count > hi:
        return "超過"
    return "範囲内"
