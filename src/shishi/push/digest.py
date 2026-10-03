"""Digest aggregation across AM/PM slots + immediate dispatch.

The digest pool is in-memory for v0.1 (single-process asyncio, in-process
APScheduler); the pipeline can seed it from
``store.list_items(since=slot_window_start)`` across restarts. Slot
suppression shares the AM/PM dedup registry (:class:`shishi.dedup.DedupRegistry`,
grill Q3: local 12:00 boundary) — the registry answers 「发没发过」, routing
answers 「推不推」 (orthogonal layers, yaml-schema rule 6).

Partial-failure convention: one failing channel reports a failed
:class:`SendReport` and the batch continues; a digest whose every channel
failed stays pooled for the next slot flush.

Targeted delivery (v1.2, PRD 10-03-messaging-core design D2/D5):items may
carry per-item target specs (rule-level ``targets`` > channel-level); digest
aggregation granularity rises from one-card-per-channel to
one-card-per-(channel × target) — legacy items (no specs) keep the merged
single-card path byte-for-byte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any, Sequence

from shishi.dedup import SLOT_BOUNDARY_HOUR, DedupRegistry
from shishi.push.base import (
    Channel,
    PushSendError,
    SendContext,
    SendReport,
    item_view,
)
from shishi.push.delivery import DeliveryLedger, send_batch_to_targets
from shishi.push.directory import ChannelDirectory
from shishi.store import SLOT_AM, SLOT_PM

__all__ = ["DigestAggregator", "PendingDigestItem", "send_immediate"]

logger = logging.getLogger(__name__)


@dataclass
class PendingDigestItem:
    """One pooled digest item plus its dedup key (None → no suppression).

    ``targets``(10-03-messaging-core design D5):该条目的定向对象 specs
    (规则级覆盖后的有效值);None = 走通道 legacy 单 target 路径。
    """

    item: Any
    dedup_key: str | None = None
    targets: list[str] | None = None


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


async def _send_via_channel_targets(
    items: Sequence[Any],
    *,
    specs: Sequence[str] | None,
    channels: Sequence[Channel],
    context: SendContext,
    directory: ChannelDirectory | None,
    ledger: DeliveryLedger | None,
) -> list[SendReport]:
    """One batch through every channel:legacy 单卡 or 定向逐对象派发。

    ``specs`` 为 None/空 = legacy 路径(每通道一张卡,行为不变);否则经
    :func:`shishi.push.delivery.send_batch_to_targets` 解析→死信过滤→逐对象
    发送。定向条目但 ``directory`` 缺席(管线未接线)按失败报告说破,不
    静默丢卡。
    """
    reports: list[SendReport] = []
    for channel in channels:
        if not specs:
            reports.append(await _send_via(channel, items, context))
            continue
        if directory is None:
            logger.error(
                "定向条目缺少通道目录(管线未接线),按失败报告: channel=%s specs=%s",
                channel.name,
                list(specs),
            )
            reports.append(
                SendReport(
                    channel=channel.name,
                    ok=False,
                    item_count=len(items),
                    error="[targeting_not_configured] 定向条目缺少通道目录(管线未接线)",
                    skipped=True,
                )
            )
            continue
        reports.extend(
            await send_batch_to_targets(
                items,
                specs=specs,
                channel=channel,
                context=context,
                directory=directory,
                ledger=ledger,
            )
        )
    return reports


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

    def add(
        self, item: Any, *, dedup_key: str | None = None, targets: list[str] | None = None
    ) -> None:
        """Append one item to the digest pool (``dedup_key`` enables suppression)."""
        self._pool.append(PendingDigestItem(item=item, dedup_key=dedup_key, targets=targets))

    def current_slot(self, now: datetime | None = None) -> str:
        """The AM/PM slot containing ``now`` (local 12:00 boundary, grill Q3)."""
        return _slot_of(_local_now(now, self._tz))

    async def flush(
        self,
        *,
        now: datetime | None = None,
        category: str | None = None,
        directory: ChannelDirectory | None = None,
        ledger: DeliveryLedger | None = None,
    ) -> list[SendReport]:
        """Send the whole pool as one merged card per channel; return reports.

        Same-slot-suppressed keys are dropped first (skip 原因 logged); an
        empty effective pool sends nothing. Items stay pooled when every
        channel failed, so the next flush retries them.

        定向条目(design D5):聚合粒度从「每通道一卡」升为「每(通道×对象)
        一卡」——同一通道的不同对象各收各的卡,互不串台;legacy 条目(无
        targets)仍走每通道一卡的原路径,行为逐字节不变。留池判定:全部
        报告失败且存在**非跳过**的真失败(瞬态错误)才留池;纯死信跳过/
        未解析(``skipped=True``)是终态,不做无限重试。

        Args:
            now: dispatch time (defaults to now; slot/date derive from it).
            category: category label for the card title context.
            directory: 通道目录(在场才启用定向派发)。
            ledger: 死信账本(与 directory 一起注入;None = 不做死信跟踪)。
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
        legacy_items = [pending.item for pending in keep if not pending.targets]
        targeted = [pending for pending in keep if pending.targets]
        reports: list[SendReport] = []
        if legacy_items:
            # legacy 路径:每通道一张合并卡(现状,零行为变化)。
            reports.extend(
                await _send_via_channel_targets(
                    legacy_items,
                    specs=None,
                    channels=self._channels,
                    context=context,
                    directory=directory,
                    ledger=ledger,
                )
            )
        # 定向路径:按 spec 元组分组,每组一张卡、组内对象各收各的(D5)。
        groups: dict[tuple[str, ...], list[Any]] = {}
        for pending in targeted:
            groups.setdefault(tuple(pending.targets), []).append(pending.item)
        for specs, group_items in groups.items():
            reports.extend(
                await _send_via_channel_targets(
                    group_items,
                    specs=list(specs),
                    channels=self._channels,
                    context=context,
                    directory=directory,
                    ledger=ledger,
                )
            )
        sent_ok = any(report.ok for report in reports)
        if sent_ok:
            if self._registry is not None:
                for pending in keep:
                    if pending.dedup_key:
                        self._registry.record_push(pending.dedup_key, now=local_now)
            logger.info(
                "摘要已发送: slot=%s date=%s count=%d ok_sends=%d/%d",
                slot,
                context.date,
                len(keep),
                sum(1 for r in reports if r.ok),
                len(reports),
            )
        elif reports and all(report.skipped for report in reports):
            # 纯终态(死信跳过/对象未解析):留池只会无限重试,放弃并说破。
            logger.warning(
                "摘要全部对象为终态不可达(死信/未解析),条目放弃不留池: slot=%s count=%d",
                slot,
                len(keep),
            )
        else:
            # 全部发送尝试失败(存在真失败),或无任何报告(如无通道):保守留池,
            # 下次 flush 重试(legacy 行为不变)。
            self._pool = keep
            logger.warning(
                "摘要全部发送失败,条目留池待重试: slot=%s count=%d", slot, len(keep)
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
    item_specs: Sequence[Sequence[str] | None] | None = None,
    directory: ChannelDirectory | None = None,
    ledger: DeliveryLedger | None = None,
) -> list[SendReport]:
    """Push immediate-bucket items right away, one card per item per channel.

    Items whose dedup key (``dedup_key`` field, falling back to ``url`` —
    the schema default dedup key) was already pushed inside the current slot
    window are skipped and logged (与摘要共享 AM/PM 防重发注册表). A channel
    failing on one item never stops the remaining items (partial failure).

    定向条目(10-03-messaging-core):``item_specs`` 与 ``items`` 按位对齐,
    非空 specs 的条目逐对象派发(每对象一张卡);缺省 None/空 = 全 legacy
    路径,行为逐字节不变。``directory`` 在场才启用定向。

    Returns:
        One :class:`SendReport` per item × channel(定向条目为 item × 对象)。
    """
    local_now = _local_now(now, tz)
    context = SendContext(
        slot=_slot_of(local_now),
        date=local_now.strftime("%Y-%m-%d"),
        category=category,
        kind="immediate",
    )
    reports: list[SendReport] = []
    for index, item in enumerate(items):
        view = item_view(item)
        key = view.get("dedup_key") or view.get("url")
        if registry is not None and key and not registry.should_send(key, now=local_now):
            logger.info("立即推送跳过(同槽位已发过): key=%s slot=%s", key, context.slot)
            continue
        specs = item_specs[index] if item_specs is not None and index < len(item_specs) else None
        item_reports = await _send_via_channel_targets(
            [item],
            specs=list(specs) if specs else None,
            channels=channels,
            context=context,
            directory=directory,
            ledger=ledger,
        )
        reports.extend(item_reports)
        if registry is not None and key and any(report.ok for report in item_reports):
            registry.record_push(key, now=local_now)
    return reports
