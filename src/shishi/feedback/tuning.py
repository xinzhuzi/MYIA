"""The feedback loop's periodic tuning: 负反馈 Top 类目/词 → 降权参数.

Simple statistics only (PRD: 不引入学习库). Every run's maintenance phase
feeds the window's feedback rows through :meth:`FeedbackTuner.apply`, which
appends :class:`~shishi.store.TuningRecord` rows when a category/word crosses
the bad-count + bad-ratio thresholds — append-only, so the full adjustment
history stays in ``feedback_tuning`` (调整历史可追溯) and the *newest* row per
key is the active adjustment.

Weight semantics (「watchlist.mute 词表权重」):

- ``weight`` ∈ [0.0, 1.0] is the score-retention ratio computed as
  ``1 - bad_ratio`` (all-negative feedback → 0.0 = full demotion);
- words reaching weight 0.0 join the *effective mute list* handed to the
  enricher — the zero-token demotion path (title hit → all-zero dims);
- partial weights (0 < w < 1) multiply a scored item's scalar ``score`` in
  the pipeline (feedback downweighting), so ``score < 5 → archive`` style
  routes catch them;
- when a key's negative share recovers below the thresholds, a ``weight=1.0``
  release row is appended (reversal is a recorded adjustment, not a delete).

enrich prompt 要点: qualifying windows also append a ``prompt_note`` row —
the note is recorded and surfaced in stats/run output (traceable), while the
prompt *template* placeholder to render it into the LLM message belongs to
``shishi.enrich.prompt`` (outside this task's boundary; tracked as a cross-module
gap — the effective mute list already hardens scoring at zero token cost).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from shishi.feedback.stats import FeedbackStats, compute_feedback_stats
from shishi.store import (
    TUNING_CATEGORY_PENALTY,
    TUNING_MUTE_WEIGHT,
    TUNING_PROMPT_NOTE,
    FeedbackRecord,
    Store,
    TuningRecord,
)

__all__ = [
    "DEFAULT_MIN_BAD_COUNT",
    "DEFAULT_MIN_BAD_RATIO",
    "DEFAULT_TOP_N",
    "DEFAULT_WINDOW_DAYS",
    "MAX_WEIGHT",
    "MIN_MUTE_WORD_LENGTH",
    "NO_PENALTY",
    "ActiveTuning",
    "FeedbackTuner",
    "TuningPolicy",
    "load_active_tuning",
]

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 14
#: A key needs at least this many bad verdicts in the window to tune at all
#: (one angry click must not mute a word forever).
DEFAULT_MIN_BAD_COUNT = 2
#: ...and bad must be at least this share of the key's good+bad feedback.
DEFAULT_MIN_BAD_RATIO = 0.5
DEFAULT_TOP_N = 3

#: Neutral weight (no demotion); release rows carry this.
MAX_WEIGHT = 1.0
NO_PENALTY = 1.0
#: Same floor as the mute matcher (shorter words would substring-match
#: unrelated titles — enrich/scoring.MIN_MUTE_LENGTH semantics).
MIN_MUTE_WORD_LENGTH = 2


@dataclass(frozen=True)
class TuningPolicy:
    """Thresholds deciding when a category/word earns a demotion adjustment.

    Raises:
        ValueError: non-positive window / top_n, negative counts, or a ratio
            outside (0, 1].
    """

    window_days: int = DEFAULT_WINDOW_DAYS
    min_bad_count: int = DEFAULT_MIN_BAD_COUNT
    min_bad_ratio: float = DEFAULT_MIN_BAD_RATIO
    top_n: int = DEFAULT_TOP_N

    def __post_init__(self) -> None:
        if self.window_days < 1:
            raise ValueError(f"字段校验失败: window_days 必须为正整数,得到 {self.window_days}")
        if self.min_bad_count < 1:
            raise ValueError(f"字段校验失败: min_bad_count 必须为正整数,得到 {self.min_bad_count}")
        if not 0 < self.min_bad_ratio <= 1:
            raise ValueError(
                f"字段校验失败: min_bad_ratio 必须在 (0, 1] 区间,得到 {self.min_bad_ratio}"
            )
        if self.top_n < 1:
            raise ValueError(f"字段校验失败: top_n 必须为正整数,得到 {self.top_n}")


@dataclass
class ActiveTuning:
    """The newest adjustment per key, plus the prompt-note trail.

    ``mute_weights`` maps word → weight, ``category_penalties`` maps category
    → weight; keys at ``weight >= 1.0`` are released (kept for traceability
    of the reversal, applied as no-ops).
    """

    mute_weights: dict[str, float] = field(default_factory=dict)
    category_penalties: dict[str, float] = field(default_factory=dict)
    prompt_notes: list[str] = field(default_factory=list)

    @property
    def has_penalties(self) -> bool:
        """Whether any adjustment actually demotes (weight < 1)."""
        return any(weight < MAX_WEIGHT for weight in self.mute_weights.values()) or any(
            weight < MAX_WEIGHT for weight in self.category_penalties.values()
        )

    def effective_mute_words(self) -> list[str]:
        """Words at full demotion (weight 0.0) — join the enrich mute list."""
        return sorted(word for word, weight in self.mute_weights.items() if weight <= 0.0)

    def penalty_for(self, *, category: str | None = None, title: str | None = None) -> float:
        """Strongest demotion weight hitting this item (1.0 = untouched).

        A category hit matches exactly; a word hit is a case-insensitive
        substring of the title (mute matcher semantics, length >= 2). The
        smallest weight wins (强降权优先).
        """
        weight = NO_PENALTY
        if category and category in self.category_penalties:
            weight = min(weight, self.category_penalties[category])
        if title and title.strip():
            folded = title.casefold()
            for word, word_weight in self.mute_weights.items():
                if word_weight >= MAX_WEIGHT or len(word) < MIN_MUTE_WORD_LENGTH:
                    continue
                if word.casefold() in folded:
                    weight = min(weight, word_weight)
        return weight

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form (stats / run stats 消费)."""
        return {
            "mute_weights": dict(sorted(self.mute_weights.items())),
            "category_penalties": dict(sorted(self.category_penalties.items())),
            "prompt_notes": list(self.prompt_notes),
        }


def load_active_tuning(store: Store) -> ActiveTuning:
    """Read the newest adjustment per key from ``feedback_tuning``.

    Rows arrive newest-first from the store; the first row seen per key wins.
    Unreadable/duplicate-free: a corrupted payload row is skipped with a
    warning (an audit table must not break the pipeline).
    """
    active = ActiveTuning()
    try:
        rows = store.list_tuning()
    except Exception as exc:  # noqa: BLE001 - 调参读取失败按无调参处理,不拖垮运行
        logger.warning("调参历史读取失败(按无调参处理): %s", exc)
        return active
    for row in rows:
        payload = row.payload if isinstance(row.payload, Mapping) else {}
        if row.kind == TUNING_MUTE_WEIGHT:
            word = payload.get("word")
            if isinstance(word, str) and word and word not in active.mute_weights:
                active.mute_weights[word] = _payload_weight(payload)
        elif row.kind == TUNING_CATEGORY_PENALTY:
            category = payload.get("category")
            if isinstance(category, str) and category and category not in active.category_penalties:
                active.category_penalties[category] = _payload_weight(payload)
        elif row.kind == TUNING_PROMPT_NOTE:
            note = payload.get("note")
            if isinstance(note, str) and note.strip() and note not in active.prompt_notes:
                active.prompt_notes.append(note.strip())
    return active


def _payload_weight(payload: Mapping[str, Any]) -> float:
    """Clamp a stored weight into [0.0, 1.0]; garbage → 1.0 (no demotion)."""
    raw = payload.get("weight")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return MAX_WEIGHT
    return max(0.0, min(MAX_WEIGHT, float(raw)))


@dataclass(frozen=True)
class _QualifiedKey:
    """One category/word that crossed both thresholds this window."""

    key: str
    bad: int
    total: int
    weight: float


class FeedbackTuner:
    """Appends tuning adjustments from negative-feedback statistics.

    Constructed once per pipeline (policy injectable); :meth:`apply` runs in
    the maintenance phase at the end of every non-dry run (随调度周期执行).

    Args:
        policy: threshold set; defaults to :class:`TuningPolicy` defaults.
    """

    def __init__(self, policy: TuningPolicy | None = None) -> None:
        self.policy = policy or TuningPolicy()

    # ------------------------------------------------------------------ apply

    def apply(self, store: Store, *, now: datetime | None = None) -> list[TuningRecord]:
        """Tune once: stats over the window → append changed adjustments.

        Idempotent per state: a key whose active adjustment already carries
        the computed weight writes nothing, so running after every run does
        not spam the history. Qualifying keys with weight 0.0 additionally
        produce a ``prompt_note`` when the note text changed.

        Args:
            store: storage backend.
            now: evaluation instant (defaults to current UTC); anchors the
                stats window.

        Returns:
            The adjustment rows written this call (empty = nothing changed).
        """
        now = now or datetime.now(timezone.utc)
        stats = self.stats(store, now=now)
        active = load_active_tuning(store)
        written: list[TuningRecord] = []
        category_hits = self._qualifying(stats, active.category_penalties)
        word_hits = self._qualifying_words(stats, active.mute_weights)
        for qualified in category_hits:
            written.append(
                self._write(
                    store,
                    kind=TUNING_CATEGORY_PENALTY,
                    payload=self._payload(qualified, key_field="category"),
                    now=now,
                )
            )
        for qualified in word_hits:
            written.append(
                self._write(
                    store,
                    kind=TUNING_MUTE_WEIGHT,
                    payload=self._payload(qualified, key_field="word"),
                    now=now,
                )
            )
        # 要点只描述真正越线的类目/词(未越线的 Top 出现在 note 里会夸大调参)。
        note = self._prompt_note(
            [q.key for q in category_hits if q.weight < MAX_WEIGHT],
            [q.key for q in word_hits if q.weight < MAX_WEIGHT],
        )
        if note is not None and note != (active.prompt_notes[0] if active.prompt_notes else None):
            written.append(
                self._write(
                    store,
                    kind=TUNING_PROMPT_NOTE,
                    payload={"note": note},
                    now=now,
                )
            )
        if written:
            logger.info(
                "反馈调参已应用 window_days=%s adjustments=%s kinds=%s",
                self.policy.window_days,
                len(written),
                sorted({record.kind for record in written}),
            )
        return written

    # ----------------------------------------------------------------- queries

    def stats(self, store: Store, *, now: datetime | None = None) -> FeedbackStats:
        """Window-filtered feedback statistics (CLI stats 与 apply 共用)."""
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=self.policy.window_days)
        rows: Sequence[FeedbackRecord] = store.list_feedback(since=since)
        return compute_feedback_stats(rows, top_n=self.policy.top_n)

    def active(self, store: Store) -> ActiveTuning:
        """Currently active adjustments (re-export for CLI convenience)."""
        return load_active_tuning(store)

    # ----------------------------------------------------------------- internals

    def _qualifying(
        self, stats: FeedbackStats, active_penalties: Mapping[str, float]
    ) -> list[_QualifiedKey]:
        """Categories to penalize now + active ones to release (见 :meth:`apply`)."""
        return self._qualify(
            stats.top_bad_categories,
            stats.category_totals,
            active=active_penalties,
            bad_counts=stats.category_bad_counts,
        )

    def _qualifying_words(
        self, stats: FeedbackStats, active_mutes: Mapping[str, float]
    ) -> list[_QualifiedKey]:
        """Words to mute/penalize now + active ones to release."""
        qualified = self._qualify(
            stats.top_bad_words,
            stats.word_totals,
            active=active_mutes,
            bad_counts=stats.word_bad_counts,
        )
        # 词候选要过最短长度(短词会子串误伤无关标题,与 mute 匹配同一底线)。
        return [
            entry
            for entry in qualified
            if entry.weight >= MAX_WEIGHT or len(entry.key) >= MIN_MUTE_WORD_LENGTH
        ]

    def _qualify(
        self,
        top_bad: Sequence[tuple[str, int]],
        totals: Mapping[str, int],
        *,
        active: Mapping[str, float],
        bad_counts: Mapping[str, int],
    ) -> list[_QualifiedKey]:
        """Shared threshold logic: penalize-when-crossed, release-when-recovered.

        越线/恢复判定一律用全量计数(``bad_counts`` / ``totals`` 不截 Top-N);
        ``top_n`` 只限制 Top 榜的遍历入口。活跃降权键逐键按真实窗口计数评估:
        仍越线(哪怕被挤出榜单)绝不释放,权重变化就写新行;真正回落才写释放行,
        且释放行携带真实计数(审计「调整历史可追溯」,不伪造 0/0)。
        """
        results: list[_QualifiedKey] = []
        qualified: set[str] = set()
        for key, bad in top_bad:
            total = totals.get(key, 0)
            if total <= 0:
                continue
            ratio = bad / total
            if bad < self.policy.min_bad_count or ratio < self.policy.min_bad_ratio:
                continue  # 只出现未越线:既不调参也不算「已恢复」
            qualified.add(key)
            weight = round(MAX_WEIGHT - ratio, 2)
            if active.get(key) == weight:
                continue  # 活跃调整已是该权重:幂等,不重复写历史
            results.append(_QualifiedKey(key=key, bad=bad, total=total, weight=weight))
        for key, weight in active.items():
            if key in qualified:
                continue
            bad = bad_counts.get(key, 0)
            total = totals.get(key, 0)
            ratio = bad / total if total > 0 else 0.0
            crossed = (
                total > 0
                and bad >= self.policy.min_bad_count
                and ratio >= self.policy.min_bad_ratio
            )
            new_weight = round(MAX_WEIGHT - ratio, 2) if crossed else MAX_WEIGHT
            if new_weight == weight:
                continue  # 活跃权重已是目标权重:幂等(含「已释放且未再越线」)
            results.append(_QualifiedKey(key=key, bad=bad, total=total, weight=new_weight))
        return results

    def _payload(self, qualified: _QualifiedKey, *, key_field: str) -> dict[str, Any]:
        """Adjustment payload: the key (word/category) + weight + evidence counts."""
        payload: dict[str, Any] = {
            key_field: qualified.key,
            "weight": qualified.weight,
            "bad_count": qualified.bad,
            "total": qualified.total,
        }
        if qualified.weight >= MAX_WEIGHT:
            payload["released"] = True  # 负反馈占比回落:可追溯地恢复权重
        return payload

    def _write(
        self, store: Store, *, kind: str, payload: dict[str, Any], now: datetime
    ) -> TuningRecord:
        record = TuningRecord(kind=kind, payload=dict(payload), created_at=now)
        record.id = store.save_tuning(record)
        logger.info("调参历史写入 kind=%s payload=%s", kind, payload)
        return record

    def _prompt_note(self, categories: list[str], words: list[str]) -> str | None:
        """One human/AI-readable 要点 line for the current qualifying keys."""
        if not categories and not words:
            return None
        parts: list[str] = ["近期用户负反馈集中"]
        if categories:
            parts.append("类目: " + "、".join(categories))
        if words:
            parts.append("词条: " + "、".join(words))
        return "；".join(parts) + "；enrich 评分请从严"
