"""Pluggable storage interface.

``Store`` is the contract every storage backend implements. The default
backend is :class:`myia.store.SQLiteStore`; a PostgreSQL implementation is
slotted for v0.2+ and must satisfy this same protocol, so pipeline/engines/
push code depends on ``Store`` only — never on a concrete backend.

All timestamps crossing this interface are timezone-aware ``datetime``
objects; implementations serialize them (SQLite: ISO-8601 UTC strings).
All methods are synchronous and short — async callers run them directly
(single-process asyncio, v0.1 scale) or via an executor.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from myia.store.models import (
    ChangeBaseline,
    DedupEntry,
    FeedbackRecord,
    ItemRecord,
    MetricRecord,
    RunRecord,
    TuningRecord,
)


@runtime_checkable
class Store(Protocol):
    """Storage contract: items / dedup_registry / change_baseline /
    engine_hints / runs, feedback + feedback_tuning (反馈闭环),
    metric_history (v0.4 趋势基线数值快照), plus run-step progress
    (断点续跑) and the retention/vacuum lifecycle hooks (长期运行防膨胀)."""

    # ------------------------------------------------------------------ items

    def save_item(self, item: ItemRecord) -> int:
        """Insert one intelligence entry and return its row id.

        Raises:
            ValueError: url / dedup_key / title is empty (structured message
                with the field path).
        """
        ...

    def get_item(self, item_id: int) -> ItemRecord | None:
        """Return the item with ``item_id``, or None when absent."""
        ...

    def get_item_by_dedup_key(self, dedup_key: str) -> ItemRecord | None:
        """Return the newest item carrying ``dedup_key``, or None when absent.

        Raises:
            ValueError: empty ``dedup_key``.
        """
        ...

    def list_items(
        self,
        *,
        category: str | None = None,
        since: datetime | None = None,
        before: datetime | None = None,
        before_id: int | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[ItemRecord]:
        """List items, newest first.

        ``since`` filters on ``first_seen`` (useful for a slot's digest pool);
        ``limit`` caps the row count. Both filters are optional.
        ``before``/``before_id`` form the ``(first_seen, id)`` composite cursor
        (strictly-older pagination); ``query`` is a NOCASE LIKE over
        title/content/source(桌面情报流,10-03-v112-desktop-batch C1).
        """
        ...

    def mark_item_pushed(
        self, item_id: int, slot: str, pushed_at: datetime | None = None
    ) -> None:
        """Stamp an item as pushed in push slot ``slot`` (``am``/``pm``).

        Raises:
            ValueError: unknown slot, or no item with ``item_id``.
        """
        ...

    def update_item_scores(self, dedup_key: str, scores: Mapping[str, object]) -> bool:
        """Backfill one item's ``scores`` column (LLM 精评回填 items 表).

        Args:
            dedup_key: the item's dedup key (stable identity from the dedup
                stage).
            scores: JSON-serializable score payload (dimensions + scalar).

        Returns:
            True when a row was updated, False when the key matches no item.

        Raises:
            ValueError: empty ``dedup_key`` or a non-mapping ``scores``.
        """
        ...

    # ---------------------------------------------------------- dedup_registry

    def get_dedup_entry(self, key: str) -> DedupEntry | None:
        """Return the registry entry for ``key``, or None when never seen."""
        ...

    def mark_dedup_seen(self, key: str, first_seen: datetime) -> None:
        """Record a key as seen (first_seen only; keeps an existing value)."""
        ...

    def record_dedup_push(self, key: str, pushed_at: datetime, slot: str) -> None:
        """Record a push of ``key`` in slot ``slot`` (upsert; keeps first_seen).

        Raises:
            ValueError: unknown slot (must be ``am``/``pm``).
        """
        ...

    # ----------------------------------------------------------- change_baseline
    # Consumed by engines/fetch_base: read before a fetch to decide
    # changed/unchanged, written after a response with the new validator set.

    def get_baseline(self, url: str) -> ChangeBaseline | None:
        """Return the stored fingerprint baseline for ``url``.

        None means "no baseline yet" — the caller treats the first fetch as
        changed and then writes a baseline.
        """
        ...

    def set_baseline(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        content_hash: str | None = None,
        last_changed: datetime | None = None,
    ) -> None:
        """Replace (full upsert) the baseline row for ``url``.

        Callers pass the complete validator set observed in the latest
        response; validators the response did not carry are stored as None
        (they are no longer usable for negotiation).
        """
        ...

    # -------------------------------------------------------------- engine_hints
    # Consumed by engines/registry (auto-degrade). Written back to SQLite,
    # never to the user's YAML (yaml-schema rule 5 — no git noise in user files).

    def get_engine_hint(self, source_key: str) -> str | None:
        """Return the engine name that last succeeded for a source, or None.

        ``source_key`` is the caller's stable per-source identifier; v0.1
        convention is the source URL. The registry reads this at run start to
        try the hinted engine first.
        """
        ...

    def set_engine_hint(self, source_key: str, engine: str) -> None:
        """Record the engine that just succeeded for ``source_key`` (upsert).

        Called by the engine registry after a successful fetch so the next
        run starts the degrade chain at the known-good engine.
        """
        ...

    def clear_engine_hint(self, source_key: str) -> None:
        """Drop the hint for ``source_key``.

        Called when the hinted engine fails: the registry clears the hint and
        continues down the degrade chain; the next success rewrites it.
        """
        ...

    # ----------------------------------------------------------- enrich_cache
    # Consumed by myia.enrich (LLM precision scoring, v0.2): same-URL scores
    # are cached until the model / score dimensions / prompt version changes
    # (all fingerprinted into ``scores_key``) or invalidation is manual.

    def get_enrich_cache(self, url: str, model: str, scores_key: str) -> dict | None:
        """Return the cached score result for one URL, or None on a miss.

        Raises:
            ValueError: any key component is empty.
        """
        ...

    def set_enrich_cache(
        self,
        url: str,
        model: str,
        scores_key: str,
        result: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> None:
        """Upsert one cached score result.

        Raises:
            ValueError: empty key component or a non-mapping ``result``.
        """
        ...

    def clear_enrich_cache(self, url: str | None = None) -> int:
        """Drop cached scores (``url=None`` clears everything); return count."""
        ...

    # ---------------------------------------------------------------- feedback
    # Consumed by myia.feedback (v0.3 反馈闭环): good/bad verdicts on pushed
    # items and the append-only tuning-adjustment history (调整历史可追溯).

    def save_feedback(self, feedback: FeedbackRecord) -> int:
        """Insert one good/bad verdict and return its row id.

        Raises:
            ValueError: empty ``dedup_key`` / ``channel``, or ``verdict``
                outside :data:`myia.store.models.FEEDBACK_VERDICTS`.
        """
        ...

    def list_feedback(
        self,
        *,
        verdict: str | None = None,
        channel: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FeedbackRecord]:
        """List feedback rows, newest first, with optional filters.

        Raises:
            ValueError: ``verdict`` given but outside the vocabulary, or a
                negative ``limit``.
        """
        ...

    def save_tuning(self, adjustment: TuningRecord) -> int:
        """Append one tuning-adjustment row and return its id (append-only).

        Raises:
            ValueError: empty ``kind`` or a non-mapping ``payload``.
        """
        ...

    def list_tuning(
        self, *, kind: str | None = None, limit: int | None = None
    ) -> list[TuningRecord]:
        """List tuning rows, newest first (调整历史), optionally by kind."""
        ...

    # ----------------------------------------------------------- metric_history
    # Consumed by the v0.4 trend baseline (PRD 10-01-v04-trend-baseline):
    # numeric snapshots power the 「vs 昨日 / vs 上周」 template functions and
    # the keyword mention week-over-week; retention keeps this table longer
    # than items (基线期长于条目期).

    def save_metric(
        self,
        category: str,
        metric_key: str,
        field: str,
        value: float,
        *,
        recorded_at: datetime | None = None,
    ) -> int:
        """Append one numeric snapshot for ``(category, metric_key, field)``.

        Append-only history — a re-observation adds a row, it never overwrites
        (窗口对比需要时间轴). ``metric_key`` is the item identity
        (``dedup_key`` / URL) for per-item fields, or the ``keyword:<词>``
        convention for category-level mention counts.

        Returns:
            The new row id.

        Raises:
            ValueError: empty key components, or a non-numeric / non-finite
                ``value`` (bool 也不是数值).
        """
        ...

    def latest_metric(
        self, category: str, metric_key: str, field: str
    ) -> MetricRecord | None:
        """Return the newest snapshot for the key, or None when never recorded."""
        ...

    def get_metric_baseline(
        self,
        category: str,
        metric_key: str,
        field: str,
        *,
        now: datetime,
        window: str,
        tz: object | None = None,
    ) -> MetricRecord | None:
        """Newest snapshot strictly before the ``window`` start (窗口对比).

        ``window`` is ``day`` (vs 昨日 — before 当地零点) or ``week`` (vs 上周
        — before 本 ISO 周周一零点); ``tz`` is the category schedule timezone
        (None = UTC). None result = no history before the window start
        (first observation: callers render no comparison).

        Raises:
            ValueError: unknown ``window``.
        """
        ...

    def sum_metrics(
        self,
        category: str,
        metric_key: str,
        field: str,
        *,
        since: datetime,
        until: datetime,
    ) -> float:
        """Sum snapshots in the half-open ``[since, until)`` window (周环比聚合).

        Raises:
            ValueError: empty key components, or ``since >= until``.
        """
        ...

    # --------------------------------------------------------------------- runs

    def start_run(self, category: str) -> int:
        """Open a run row (status ``running``) for a category, return its id."""
        ...

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        stats: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Close a run with status ``success``/``partial``/``failed``.

        Raises:
            ValueError: unknown status, or no run with ``run_id``.
        """
        ...

    def update_run_step(
        self,
        run_id: int,
        step: str,
        *,
        status: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Record one pipeline step's progress on the run row (断点续跑).

        ``payload`` is the resumable checkpoint (e.g. ``{"items": [...]}``)
        that lets a rerun skip the completed stage; entries for other steps
        are preserved.

        Raises:
            ValueError: unknown ``run_id``, empty ``step``, unknown ``status``
                (must be one of :data:`myia.store.models.STEP_STATUSES`), or
                a non-mapping payload.
        """
        ...

    def get_run(self, run_id: int) -> RunRecord | None:
        """Return the run with ``run_id``, or None when absent."""
        ...

    def previous_run(self, category: str, *, before_run_id: int) -> RunRecord | None:
        """Return the most recent run of ``category`` before ``before_run_id``.

        The resume lookup anchor: the pipeline inspects the run right before
        its own for interrupted-progress checkpoints.
        """
        ...

    def latest_run(self, category: str | None = None) -> RunRecord | None:
        """Return the most recent run (optionally for one category), or None."""
        ...

    def list_runs(self, *, category: str | None = None, limit: int = 50) -> list[RunRecord]:
        """List persisted runs, newest first(桌面 runs.list 直读,C3).

        Raises:
            ValueError: negative ``limit``.
        """
        ...

    # --------------------------------------------------------- retention/vacuum

    def cleanup_expired(
        self,
        retention_days: int,
        *,
        now: datetime | None = None,
        active_urls: Collection[str] | None = None,
        metrics_retention_days: int | None = None,
    ) -> dict[str, int]:
        """Delete retention-expired rows; return per-table deletion counts.

        Pushed items drop after ``retention_days`` (anchored at
        ``pushed_at``); never-pushed items after a longer grace window
        (anchored at ``first_seen``); stale per-URL baselines/engine hints
        (only via explicit ``active_urls``), old run rows, aged
        ``enrich_cache`` rows and aged ``metric_history`` snapshots (基线期
        长于条目期, ``BASELINE_RETENTION_MULTIPLIER × retention``) likewise.
        ``dedup_registry`` is never pruned
        automatically (all-time dedup semantics) — manual governance via
        ``prune_dedup_registry``.

        Raises:
            ValueError: ``retention_days`` < 1.
        """
        ...

    def vacuum(self) -> None:
        """Rebuild the database file, reclaiming free pages (anti-bloat)."""
        ...

    def maybe_vacuum(self, cadence: str, *, now: datetime | None = None) -> bool:
        """Run :meth:`vacuum` when ``cadence`` (daily/weekly/monthly/never)
        is due against the persisted last-vacuum stamp; return whether it ran.

        Raises:
            ValueError: unknown cadence.
        """
        ...

    # ---------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Release backend resources (connection/socket)."""
        ...
