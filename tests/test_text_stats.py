from __future__ import annotations

import pytest

from eiken_grader.services import text_stats as ts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("I agree with this opinion.", 5),
        ("I don't think so.", 4),  # 縮約形は 1 語
        ("It is a well-known fact.", 5),  # ハイフン語は 1 語
        ("I have 2 reasons, first and second!", 7),  # 数字も 1 語
        ("Hello,world", 2),
        ("It’s fine", 2),  # 曲がった引用符
        ("This is [?] good", 3),  # 判読不能マーカーは数えない
        ("This is {gooood?} idea", 4),  # 要確認語は語として数える
        ("Line one\nline two\n\nline three", 6),
    ],
)
def test_count_words(text, expected):
    assert ts.count_words(text) == expected


def test_paragraphs():
    assert ts.count_paragraphs("a\n\nb\n  \nc") == 3
    assert ts.count_paragraphs("one paragraph\nwith a line break") == 1
    assert ts.count_paragraphs("   ") == 0


def test_markers():
    text = "I {thnik?} it is [?] and {beautifull?}."
    assert ts.illegible_count(text) == 1
    assert ts.uncertain_marked_words(text) == ["thnik", "beautifull"]
    assert ts.has_markers(text)
    assert not ts.has_markers("clean text?")
    assert ts.strip_markers(text) == "I thnik it is   and beautifull."


@pytest.mark.parametrize(("count", "status"), [(79, "不足"), (80, "範囲内"), (100, "範囲内"), (101, "超過")])
def test_word_range_status(count, status):
    assert ts.word_range_status(count, (80, 100)) == status
