"""Feedback ingestion: normalize verdicts, resolve items, write the table.

The receiving layer sits in front of :class:`shishi.store.Store` so all three
delivery paths (CLI ``myia feedback mark`` / TG ``getUpdates`` callback /
飞书卡片回调端点) land identical rows: verdict normalized to ``good``/
``bad``, the item resolved for the association, and title/category
snapshotted at feedback time — the snapshots keep 负反馈 Top 类目/词 statistics
usable even after retention prunes the item row itself.

Channel callbacks may reference items that retention already removed (the
button carries only the dedup key); ingestion therefore tolerates dangling
references (``allow_missing=True``) while the CLI's manual marking stays
strict (an unknown entry is a user error, not a race).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from shishi.store import (
    FEEDBACK_BAD,
    FEEDBACK_GOOD,
    FEEDBACK_VERDICTS,
    FeedbackRecord,
    ItemRecord,
    Store,
)

__all__ = [
    "FEEDBACK_BAD",
    "FEEDBACK_GOOD",
    "IngestReport",
    "normalize_verdict",
    "record_feedback",
    "ingest_callbacks",
    "resolve_item_ref",
]

logger = logging.getLogger(__name__)


def normalize_verdict(value: Any) -> str:
    """Normalize a raw verdict to ``good`` / ``bad`` (case-insensitive).

    Raises:
        ValueError: anything else (structured message with the legal set —
            fail fast at the boundary, never store a third verdict).
    """
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in FEEDBACK_VERDICTS:
            return folded
    raise ValueError(
        f"字段校验失败: 判定必须是 {sorted(FEEDBACK_VERDICTS)} 之一,得到 {value!r}"
    )


def resolve_item_ref(store: Store, item_ref: Any) -> ItemRecord | None:
    """Resolve a CLI ``<条目>`` reference to an item (id or dedup key).

    An integer (or all-digit string) is treated as ``items.id``; anything
    else as the dedup key. Returns None when nothing matches.
    """
    if isinstance(item_ref, bool):
        return None
    if isinstance(item_ref, int):
        return store.get_item(item_ref)
    if isinstance(item_ref, str):
        text = item_ref.strip()
        if not text:
            return None
        if text.isdigit():
            return store.get_item(int(text))
        return store.get_item_by_dedup_key(text)
    return None


def record_feedback(
    store: Store,
    *,
    verdict: str,
    channel: str,
    item: ItemRecord | None = None,
    dedup_key: str | None = None,
    now: datetime | None = None,
    external_id: str | None = None,
) -> FeedbackRecord:
    """Persist one verdict; snapshot title/category from the resolved item.

    Args:
        store: storage backend (the run's store; dry-run callers pass their
            in-memory store).
        verdict: raw verdict (normalized here).
        channel: receiving path (``cli`` / ``telegram`` / ``feishu``).
        item: resolved item (supplies item_id / dedup_key / snapshots).
        dedup_key: fallback identity when the item row is gone (channel
            callbacks after retention); one of the two must be present.
        now: timestamp override (tests pin it; defaults to current UTC).
        external_id: provider event identity (TG update_id / Feishu
            event_id);幂等键 —— 同 ``(channel, external_id)`` 只记一次。

    Returns:
        The saved :class:`FeedbackRecord`; ``id`` is ``None`` when the same
        ``(channel, external_id)`` row already existed (duplicate skipped).

    Raises:
        ValueError: no usable item identity, or an invalid verdict.
    """
    verdict = normalize_verdict(verdict)
    key = (item.dedup_key if item is not None else None) or dedup_key
    if not key:
        raise ValueError(
            "字段校验失败: 反馈缺少条目身份(需要 item 或 dedup_key),无法关联 items"
        )
    record = FeedbackRecord(
        dedup_key=key,
        verdict=verdict,
        channel=channel,
        item_id=item.id if item is not None else None,
        title=item.title if item is not None else None,
        category=item.category if item is not None else None,
        external_id=external_id,
        created_at=now or datetime.now(timezone.utc),
    )
    record.id = store.save_feedback(record)
    if record.id is not None:
        logger.info(
            "反馈已记录 feedback_id=%s verdict=%s channel=%s item_id=%s dedup_key=%s",
            record.id, record.verdict, record.channel, record.item_id, record.dedup_key,
        )
    else:
        logger.info(
            "重复反馈已忽略(幂等) channel=%s external_id=%s dedup_key=%s",
            record.channel, record.external_id, record.dedup_key,
        )
    return record


@dataclass
class IngestReport:
    """One batch ingestion's outcome (observability for pollers/端点)."""

    saved: int = 0
    skipped: int = 0
    duplicates: int = 0  # 幂等命中:同 (channel, external_id) 已入库,忽略不重复记
    feedback_ids: list[int] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form (run stats / ``--json`` 消费)."""
        return {
            "saved": self.saved,
            "skipped": self.skipped,
            "duplicates": self.duplicates,
            "feedback_ids": list(self.feedback_ids),
            "failures": list(self.failures),
        }


def _callback_field(callback: Any, name: str) -> Any:
    """Read a field from a mapping or attribute-style callback."""
    if isinstance(callback, Mapping):
        return callback.get(name)
    return getattr(callback, name, None)


def ingest_callbacks(
    store: Store,
    callbacks: Any,
    *,
    now: datetime | None = None,
    allow_missing: bool = True,
) -> IngestReport:
    """Ingest channel callbacks into the feedback table (单条失败不整批中止).

    Each callback carries ``channel`` / ``verdict`` / ``dedup_key`` (mapping
    or attributes). Verdict normalization or identity problems skip that one
    callback and land a structured failure — the rest of the batch proceeds
    (批量采集单条失败不 abort 整批).

    Args:
        store: storage backend.
        callbacks: iterable of callback objects.
        now: timestamp override (tests pin it).
        allow_missing: True (channel paths) records dangling dedup keys with
            no snapshot; False (strict manual paths) skips unknown items.

    Returns:
        :class:`IngestReport` with saved/skipped counts and structured
        failures.
    """
    report = IngestReport()
    for index, callback in enumerate(callbacks):
        try:
            verdict = normalize_verdict(_callback_field(callback, "verdict"))
            channel = _callback_field(callback, "channel")
            dedup_key = _callback_field(callback, "dedup_key")
            if not isinstance(channel, str) or not channel.strip():
                raise ValueError(f"回调缺少有效 channel 字段,当前为 {channel!r}")
            if not isinstance(dedup_key, str) or not dedup_key.strip():
                raise ValueError(f"回调缺少有效 dedup_key 字段,当前为 {dedup_key!r}")
            item = store.get_item_by_dedup_key(dedup_key)
            if item is None and not allow_missing:
                raise ValueError(f"条目不存在 dedup_key={dedup_key!r}")
            external_id = _callback_field(callback, "external_id")
            record = record_feedback(
                store, verdict=verdict, channel=channel.strip(), item=item,
                dedup_key=dedup_key, now=now,
                external_id=external_id if isinstance(external_id, str) else None,
            )
        except ValueError as exc:  # 单条失败:结构化记录后继续
            report.skipped += 1
            report.failures.append(
                {"index": str(index), "error_type": "invalid_callback", "message": str(exc)}
            )
            logger.warning("回调入库失败(跳过该条) index=%s: %s", index, exc)
            continue
        if record.id is None:
            report.duplicates += 1  # 幂等命中:双击/重试/重放,只记一次的另一半
            continue
        report.saved += 1
        report.feedback_ids.append(record.id)
    logger.info(
        "回调批量入库完成 saved=%s skipped=%s failures=%s",
        report.saved, report.skipped, len(report.failures),
    )
    return report
