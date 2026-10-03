"""Feedback statistics: good/bad counts, Top 负反馈类目与词 (简单统计起步).

Pure functions over already-fetched :class:`~shishi.store.FeedbackRecord`
rows — no store access, no learning library (PRD: 简单统计起步,不引入学习库).
Word extraction is deliberately naive and explainable: latin/digit tokens
plus overlapping bigrams inside CJK runs (Chinese titles carry no spaces, so
whitespace tokenization would find nothing; bigrams are the cheapest
substring candidates the mute matcher can later confirm by title hit).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from shishi.store import FEEDBACK_BAD, FEEDBACK_GOOD, FeedbackRecord

__all__ = [
    "DEFAULT_TOP_N",
    "FeedbackStats",
    "candidate_words",
    "compute_feedback_stats",
]

#: How many Top entries stats reports by default (CLI/stats 消费).
DEFAULT_TOP_N = 5

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def candidate_words(title: str | None) -> list[str]:
    """Extract mute-candidate words from one title (latin tokens + CJK bigrams).

    - latin/digit tokens of length >= 2, lowercased (``RTX-4090`` stays one
      token);
    - each CJK run of length >= 2 becomes overlapping bigrams (``显卡降价`` →
      显卡 / 卡降 / 降价) — single CJK characters are too short for the mute
      matcher (``MIN_MUTE_LENGTH`` = 2) and are dropped.
    """
    if not title or not title.strip():
        return []
    words: list[str] = []
    for token in _LATIN_TOKEN_RE.findall(title):
        folded = token.casefold()
        if len(folded) >= 2:
            words.append(folded)
    for run in _CJK_RUN_RE.findall(title):
        for index in range(len(run) - 1):
            words.append(run[index : index + 2])
    return words


@dataclass
class FeedbackStats:
    """Aggregate feedback counts over one window (CLI stats / tuner 消费).

    ``category_totals`` / ``word_totals`` count good+bad per key so the tuner
    can compute a bad *ratio* (2 bad out of 2 total ≠ 2 bad out of 50).
    """

    total: int = 0
    good: int = 0
    bad: int = 0
    by_channel: Counter[str] = field(default_factory=Counter)
    #: 负反馈 Top 类目: [(category, bad_count)] 降序。
    top_bad_categories: list[tuple[str, int]] = field(default_factory=list)
    #: 负反馈 Top 词条: [(word, bad_count)] 降序(同频按词条字母序,结果稳定)。
    top_bad_words: list[tuple[str, int]] = field(default_factory=list)
    #: 全量负反馈计数(不截 Top-N):调参释放判定必须看全量 —— 被挤出榜单的
    #: 活跃降权键也要按真实计数评估,而不是被 top_n 截断误判「已恢复」。
    category_bad_counts: dict[str, int] = field(default_factory=dict)
    word_bad_counts: dict[str, int] = field(default_factory=dict)
    #: bad/(good+bad) 分母,供调参比值判定(仅 Top 键)。
    category_totals: dict[str, int] = field(default_factory=dict)
    word_totals: dict[str, int] = field(default_factory=dict)

    @property
    def bad_ratio(self) -> float:
        """Overall bad share (0.0 when no feedback yet)."""
        return self.bad / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form (``myia feedback stats --json``)."""
        return {
            "total": self.total,
            "good": self.good,
            "bad": self.bad,
            "bad_ratio": round(self.bad_ratio, 4),
            "by_channel": dict(self.by_channel),
            "top_bad_categories": [
                {"key": key, "bad": count} for key, count in self.top_bad_categories
            ],
            "top_bad_words": [{"key": key, "bad": count} for key, count in self.top_bad_words],
        }


def compute_feedback_stats(rows: Sequence[FeedbackRecord], *, top_n: int = DEFAULT_TOP_N) -> FeedbackStats:
    """Aggregate feedback rows (already window-filtered by the caller).

    Rows with no category/title snapshot (dangling channel callbacks) are
    counted in the totals but cannot contribute category/word keys.
    """
    stats = FeedbackStats()
    category_bad: Counter[str] = Counter()
    category_all: Counter[str] = Counter()
    word_bad: Counter[str] = Counter()
    word_all: Counter[str] = Counter()
    for row in rows:
        stats.total += 1
        if row.verdict == FEEDBACK_GOOD:
            stats.good += 1
        elif row.verdict == FEEDBACK_BAD:
            stats.bad += 1
        stats.by_channel[row.channel] += 1
        if row.category:
            category_all[row.category] += 1
            if row.verdict == FEEDBACK_BAD:
                category_bad[row.category] += 1
        if row.title:
            words = set(candidate_words(row.title))  # 一条标题内同词只计一次
            for word in words:
                word_all[word] += 1
                if row.verdict == FEEDBACK_BAD:
                    word_bad[word] += 1
    stats.top_bad_categories = sorted(
        category_bad.items(), key=lambda item: (-item[1], item[0])
    )[:top_n]
    stats.top_bad_words = sorted(word_bad.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    stats.category_bad_counts = dict(category_bad)
    stats.word_bad_counts = dict(word_bad)
    stats.category_totals = dict(category_all)
    stats.word_totals = dict(word_all)
    return stats
