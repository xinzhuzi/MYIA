"""Digest aggregation across AM/PM slots + immediate dispatch.

The digest pool is in-memory for v0.1 (single-process asyncio, in-process
APScheduler); the pipeline can seed it from
``store.list_items(since=slot_window_start)`` across restarts. Slot
suppression shares the AM/PM dedup registry (:class:`myia.dedup.DedupRegistry`,
grill Q3: local 12:00 boundary) — the registry answers 「发没发过」, routing
answers 「推不推」 (orthogonal layers, yaml-schema rule 6).

Partial-failure convention: one failing channel reports a failed
:class:`SendReport` and the batch continues; a digest whose every channel
failed stays pooled for the next slot flush.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any, Sequence

from myia.dedup import SLOT_BOUNDARY_HOUR, DedupRegistry
from myia.push.base import (
    Channel,
    PushSendError,
    SendContext,
    SendReport,
    item_view,
)
from myia.store import SLOT_AM, SLOT_PM

__all__ = ["DigestAggregator", "PendingDigestItem", "send_immediate"]

logger = logging.getLogger(__name__)


@dataclass
class PendingDigestItem:
    """One pooled digest item plus its dedup key (None → no suppression)."""

    item: Any
    dedup_key: str | None = None


def _local_now(now: datetime | None, tz: tzinfo | None) -> datetime:
    """Resolve ``now`` into ``tz`` (naive input interpreted in tz; default now)."""
    zone = tz or datetime.now().astimezone().tzinfo
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _slot_of(local: datetime) -> str:
    return SLOT_AM if local.hour < SLOT_BOUNDARY_HOUR else SLOT_PM


async def _send_via(channel: Channel, items: Sequence[Any], context: SendContext) -> SendReport:
    """Send through one channel, converting failures into a failed report."""
    try:
        await channel.send(items, context)
    except PushSendError as exc:
        logger.warning(
            "通道发送失败(继续其余通道): channel=%s code=%s error=%s", channel.name, exc.code, exc
        )
        return SendReport(channel.name, ok=False, item_count=len(items), error=f"[{exc.code}] {exc}")
    except Exception as exc:  # noqa: BLE001 - 部分失败语义要求隔离未知异常
        logger.error(
            "通道发送未知异常(需要介入): channel=%s error=%s", channel.name, exc, exc_info=True
        )
        return SendReport(
            channel.name, ok=False, item_count=len(items), error=f"[unexpected] {type(exc).__name__}: {exc}"
        )
    return SendReport(channel.name, ok=True, item_count=len(items))


class DigestAggregator:
    """Pools digest items and flushes one merged card per AM/PM slot.

    Args:
        channels: delivery channels; each flush sends the whole pool through
            every channel (one card per channel).
        registry: shared AM/PM dedup registry; when present, items whose key
            was already pushed inside the current slot window are skipped
            (同槽位拦截) and successful sends are recorded (防重发).
        tz: slot-boundary timezone (category schedule timezone); defaults to
            the system local zone.
    """

    def __init__(
        self,
        *,
        channels: Sequence[Channel],
        registry: DedupRegistry | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self._channels = list(channels)
        self._registry = registry
        self._tz = tz
        self._pool: list[PendingDigestItem] = []

    def __len__(self) -> int:
        return len(self._pool)

    def add(self, item: Any, *, dedup_key: str | None = None) -> None:
        """Append one item to the digest pool (``dedup_key`` enables suppression)."""
        self._pool.append(PendingDigestItem(item=item, dedup_key=dedup_key))

    def current_slot(self, now: datetime | None = None) -> str:
        """The AM/PM slot containing ``now`` (local 12:00 boundary, grill Q3)."""
        return _slot_of(_local_now(now, self._tz))

    async def flush(
        self, *, now: datetime | None = None, category: str | None = None
    ) -> list[SendReport]:
        """Send the whole pool as one merged card per channel; return reports.

        Same-slot-suppressed keys are dropped first (skip 原因 logged); an
        empty effective pool sends nothing. Items stay pooled when every
        channel failed, so the next flush retries them.

        Args:
            now: dispatch time (defaults to now; slot/date derive from it).
            category: category label for the card title context.
        """
        local_now = _local_now(now, self._tz)
        slot = _slot_of(local_now)
        context = SendContext(
            slot=slot, date=local_now.strftime("%Y-%m-%d"), category=category, kind="digest"
        )
        keep = self._drop_suppressed(local_now)
        self._pool = []
        if not keep:
            logger.info("摘要槽位无待发条目,跳过发送: slot=%s date=%s", slot, context.date)
            return []
        reports = [
            await _send_via(channel, [pending.item for pending in keep], context)
            for channel in self._channels
        ]
        sent_ok = any(report.ok for report in reports)
        if sent_ok:
            if self._registry is not None:
                for pending in keep:
                    if pending.dedup_key:
                        self._registry.record_push(pending.dedup_key, now=local_now)
            logger.info(
                "摘要已发送: slot=%s date=%s count=%d ok_channels=%d/%d",
                slot,
                context.date,
                len(keep),
                sum(1 for r in reports if r.ok),
                len(reports),
            )
        else:
            self._pool = keep  # 全通道失败 → 留池,下次 flush 重试
            logger.warning(
                "摘要全部通道发送失败,条目留池待重试: slot=%s count=%d", slot, len(keep)
            )
        return reports

    def _drop_suppressed(self, now: datetime) -> list[PendingDigestItem]:
        keep: list[PendingDigestItem] = []
        blocked = 0
        for pending in self._pool:
            if (
                self._registry is not None
                and pending.dedup_key is not None
                and not self._registry.should_send(pending.dedup_key, now=now)
            ):
                blocked += 1
                continue
            keep.append(pending)
        if blocked:
            logger.info("摘要去重拦截(同槽位已发过): slot=%s 拦截=%d 保留=%d",
                        _slot_of(now), blocked, len(keep))
        return keep


async def send_immediate(
    items: Sequence[Any],
    *,
    channels: Sequence[Channel],
    registry: DedupRegistry | None = None,
    tz: tzinfo | None = None,
    now: datetime | None = None,
    category: str | None = None,
) -> list[SendReport]:
    """Push immediate-bucket items right away, one card per item per channel.

    Items whose dedup key (``dedup_key`` field, falling back to ``url`` —
    the schema default dedup key) was already pushed inside the current slot
    window are skipped and logged (与摘要共享 AM/PM 防重发注册表). A channel
    failing on one item never stops the remaining items (partial failure).

    Returns:
        One :class:`SendReport` per item × channel.
    """
    local_now = _local_now(now, tz)
    context = SendContext(
        slot=_slot_of(local_now),
        date=local_now.strftime("%Y-%m-%d"),
        category=category,
        kind="immediate",
    )
    reports: list[SendReport] = []
    for item in items:
        view = item_view(item)
        key = view.get("dedup_key") or view.get("url")
        if registry is not None and key and not registry.should_send(key, now=local_now):
            logger.info("立即推送跳过(同槽位已发过): key=%s slot=%s", key, context.slot)
            continue
        item_reports = [await _send_via(channel, [item], context) for channel in channels]
        reports.extend(item_reports)
        if registry is not None and key and any(report.ok for report in item_reports):
            registry.record_push(key, now=local_now)
    return reports
