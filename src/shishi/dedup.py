"""Dedup registry: composite dedup keys + AM/PM push-slot suppression.

Semantics (PRD 10-01-v01-store-dedup, grill Q3):
- ``dedup.key`` is a composite template (e.g. ``"{symbol}-{date}"``) rendered
  against per-item values; when a plugin defines no template the caller falls
  back to the bare URL. Title fingerprints are forbidden forever —
  :meth:`DedupRegistry.make_key` rejects a ``{title}`` placeholder.
- A key is suppressed only inside the slot window it was last pushed in.
  Slots split the local day at 12:00 (AM = 00:00-11:59, PM = 12:00-23:59);
  crossing the boundary — or the day — releases the key again. The timezone
  is the category's schedule timezone, defaulting to the system local zone.
- digest and immediate pushes share one registry: this layer answers
  "发没发过", the push route layer answers "推不推" (orthogonal layers).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, tzinfo
from string import Formatter
from typing import Mapping

from shishi.store import SLOT_AM, SLOT_PM, DedupEntry, Store

__all__ = ["SLOT_BOUNDARY_HOUR", "DedupRegistry"]

logger = logging.getLogger(__name__)

# 本地时区 12:00 分界(grill Q3 定案):AM = 00:00-11:59,PM = 12:00-23:59。
SLOT_BOUNDARY_HOUR = 12

# 永不标题指纹(规划铁律):dedup 键只允许 URL 或结构化字段组合键。
_BANNED_KEY_FIELDS = frozenset({"title"})

# Only plain field names are valid placeholders — attribute/index access and
# positional/empty fields are rejected so a YAML-supplied template can never
# reach into objects (mirrors the whitelist-AST rule for when-expressions).
_FIELD_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_FORMATTER = Formatter()


class DedupRegistry:
    """Persistent dedup registry on top of a :class:`shishi.store.base.Store`.

    The registry owns the dedup semantics (key rendering, slot windows); the
    store owns the rows. One store-backed registry serves both digest and
    immediate pushes.
    """

    def __init__(self, store: Store, tz: tzinfo | None = None) -> None:
        """Bind the registry to a storage backend.

        Args:
            store: pluggable storage backend (default SQLiteStore).
            tz: timezone defining the 12:00 slot boundary — the category's
                schedule timezone; defaults to the system local zone.
        """
        self._store = store
        self._tz = tz or datetime.now().astimezone().tzinfo

    # ------------------------------------------------------------- key building

    @staticmethod
    def make_key(template: str, values: Mapping[str, object]) -> str:
        """Render a dedup key from the plugin's ``dedup.key`` template.

        Only plain ``{field}`` placeholders are allowed (e.g. ``{symbol}-{date}``);
        a template without any placeholder would collapse every item into one
        key and is rejected, as is any ``{title}`` reference (永不标题指纹).

        Raises:
            ValueError: empty/constant template, illegal placeholder syntax,
                missing value, banned field, or conversion/format spec.
        """
        if not template or not template.strip():
            raise ValueError("字段校验失败: dedup.key 模板不能为空")
        try:
            parsed = list(_FORMATTER.parse(template))
        except ValueError as exc:
            raise ValueError(f"字段校验失败: dedup.key 模板语法错误 {template!r}({exc})") from exc

        parts: list[str] = []
        placeholder_count = 0
        for literal, field_name, format_spec, conversion in parsed:
            parts.append(literal)
            if field_name is None:
                continue
            placeholder_count += 1
            if not _FIELD_NAME_RE.fullmatch(field_name):
                raise ValueError(
                    f"字段校验失败: dedup.key 模板占位符非法(仅支持 {{field}} 简单字段名,"
                    f"不支持属性/下标/位置访问):{field_name!r}"
                )
            if field_name in _BANNED_KEY_FIELDS:
                raise ValueError(
                    f"字段校验失败: dedup.key 模板禁止引用字段 {field_name!r}(永不标题指纹)"
                )
            if conversion is not None or format_spec:
                raise ValueError(
                    f"字段校验失败: dedup.key 模板不支持转换/格式说明({field_name!r},"
                    f"模板:{template!r})"
                )
            if field_name not in values:
                raise ValueError(
                    f"字段校验失败: dedup.key 模板缺少字段 {field_name!r}(模板:{template!r})"
                )
            parts.append(str(values[field_name]))
        if placeholder_count == 0:
            raise ValueError(
                f"字段校验失败: dedup.key 模板必须包含至少一个字段占位符(模板:{template!r})"
            )
        return "".join(parts)

    # -------------------------------------------------------------- slot windows

    def key_context(self, now: datetime | None = None) -> dict[str, str]:
        """Reserved ``dedup.key`` placeholders injectable for every item.

        ``date`` is the local ``YYYY-MM-DD`` and ``slot`` the AM/PM push slot
        (same 12:00 boundary as :meth:`slot_of`), so a plugin can rotate its
        dedup keys per slot — e.g. ``"{symbol}-{date}-{slot}"`` lets a
        watchlist symbol re-surface in the next slot/day instead of being
        suppressed all-time. Item fields of the same name win over these.
        """
        local = self._to_local(now)
        return {
            "date": local.strftime("%Y-%m-%d"),
            "slot": SLOT_AM if local.hour < SLOT_BOUNDARY_HOUR else SLOT_PM,
        }

    def slot_of(self, now: datetime | None = None) -> str:
        """Return the push slot (``am``/``pm``) containing ``now``.

        ``now`` defaults to the current time; naive datetimes are interpreted
        in the registry timezone. Boundary: hour < 12 → am, else pm.
        """
        local = self._to_local(now)
        return SLOT_AM if local.hour < SLOT_BOUNDARY_HOUR else SLOT_PM

    def slot_window_start(self, now: datetime | None = None) -> datetime:
        """Return the aware start of the slot window containing ``now``.

        AM windows start at local 00:00, PM windows at local 12:00. A push
        recorded at or after this instant (UTC-compared) blocks a resend.
        """
        local = self._to_local(now)
        hour = 0 if local.hour < SLOT_BOUNDARY_HOUR else SLOT_BOUNDARY_HOUR
        return local.replace(hour=hour, minute=0, second=0, microsecond=0)

    # ------------------------------------------------------------------ registry

    def is_seen(self, key: str) -> bool:
        """All-time check: has ``key`` ever entered the registry (dedup stage)."""
        return self._store.get_dedup_entry(key) is not None

    def mark_seen(self, key: str, now: datetime | None = None) -> None:
        """Record ``key`` as seen without touching its push info."""
        self._store.mark_dedup_seen(key, self._utc(now))

    def should_send(self, key: str, now: datetime | None = None) -> bool:
        """Push-slot check: may ``key`` be sent at time ``now``?

        False only when the key was pushed at or after the start of the
        current slot window (同槽位拦截); any earlier window — the other
        slot, or a previous day's same-name slot — allows the send (跨槽位放行).
        """
        entry = self._store.get_dedup_entry(key)
        if entry is None or entry.last_pushed_at is None:
            return True
        window_start_utc = self.slot_window_start(now).astimezone(timezone.utc)
        allowed = entry.last_pushed_at < window_start_utc
        if not allowed:
            logger.debug(
                "去重拦截 key=%s last_pushed_at=%s last_push_slot=%s",
                key,
                entry.last_pushed_at,
                entry.last_push_slot,
            )
        return allowed

    def record_push(self, key: str, now: datetime | None = None) -> None:
        """Record a push of ``key`` in the slot containing ``now`` (upsert)."""
        local_now = self._to_local(now)
        slot = SLOT_AM if local_now.hour < SLOT_BOUNDARY_HOUR else SLOT_PM
        self._store.record_dedup_push(key, local_now.astimezone(timezone.utc), slot)
        logger.debug("去重推送登记 key=%s slot=%s", key, slot)

    def get_entry(self, key: str) -> DedupEntry | None:
        """Return the raw registry entry for ``key`` (doctor/debug introspection)."""
        return self._store.get_dedup_entry(key)

    # ------------------------------------------------------------------ internals

    def _to_local(self, now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=self._tz)
        return value.astimezone(self._tz)

    def _utc(self, now: datetime | None) -> datetime:
        return self._to_local(now).astimezone(timezone.utc)
