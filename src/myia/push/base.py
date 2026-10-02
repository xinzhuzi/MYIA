"""Push-layer shared vocabulary: channel protocol, send context, reports.

The :class:`Channel` protocol is the extension point for delivery channels
(feishu_card and stdout are v0.1; telegram/webhook land in v0.2). Channels
receive already-routed items plus a :class:`SendContext` and raise
:class:`PushSendError` on failure — callers (digest aggregator / immediate
dispatch) isolate per channel so one broken channel never breaks the batch
(partial-failure convention).

Credentials never appear here as values: channels resolve ``env:`` /
``keychain:`` references via :func:`myia.schema.resolve_credential` at send
time, and error messages carry the reference name only, never the value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

from myia.store import PUSH_SLOTS

__all__ = [
    "TrendAwareChannel",
    "DEFAULT_SEND_TIMEOUT_SECONDS",
    "PushSendError",
    "SendContext",
    "SendReport",
    "Channel",
    "also_seen_list",
    "item_view",
]

logger = logging.getLogger(__name__)

#: Per-request timeout for HTTP-backed channels.
DEFAULT_SEND_TIMEOUT_SECONDS = 10.0

SendKind = Literal["digest", "immediate"]

_ITEM_FIELDS = ("url", "title", "source", "category", "scores", "dedup_key")


class TrendAwareChannel:
    """v0.4 趋势渲染上下文载体(PRD 10-01-v04-trend-baseline)。

    管线在每轮 run 装配好趋势数据后写入通道实例(:meth:`set_trend_context`);
    通道渲染用户模板时经 :meth:`trend_render_kwargs` 转发给
    :meth:`TemplateRenderer.render`。三个属性缺省 ``None``:未接线的构造路径
    (单测直构通道等)渲染行为不变 —— 缺历史渲染为空对比是正常路径,不是错误。

    属性类型是运行期注入的 ``templates.TrendTable`` / ``list[KeywordTrend]`` /
    ``Mapping[str, float]``;本模块不反向 import templates(避免环)。
    """

    trend_table: Any = None
    keyword_trends: Any = None
    msrp_table: Any = None

    def set_trend_context(
        self,
        *,
        trends: Any | None = None,
        keyword_trends: Any | None = None,
        msrp: Any | None = None,
    ) -> None:
        """Pipeline 侧装配入口:一轮 run 写一次,通道实例随后只读。"""
        if trends is not None:
            self.trend_table = trends
        if keyword_trends is not None:
            self.keyword_trends = keyword_trends
        if msrp is not None:
            self.msrp_table = msrp

    def trend_render_kwargs(self) -> dict[str, Any]:
        """Only-set values → ``render(...)`` keyword args(未装配项不覆盖缺省)."""
        kwargs: dict[str, Any] = {}
        if self.trend_table is not None:
            kwargs["trends"] = self.trend_table
        if self.keyword_trends is not None:
            kwargs["keyword_trends"] = self.keyword_trends
        if self.msrp_table is not None:
            kwargs["msrp"] = self.msrp_table
        return kwargs


class PushSendError(RuntimeError):
    """One channel send failed (isolated per channel by callers).

    Attributes:
        code: machine-readable failure class — ``http_error``,
            ``feishu_api_error``, ``invalid_response``,
            ``template_render_error`` (user template failed at send time),
            ``not_implemented`` (v0.2 channel shells: telegram/webhook), or
            the credential codes re-exported from
            :class:`myia.schema.CredentialResolveError`
            (``env_var_missing`` / ``keychain_not_supported`` /
            ``invalid_credential_ref``).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SendContext:
    """When/what/for-whom a send happens; built once per dispatch batch.

    Raises:
        ValueError: ``slot`` is not ``am``/``pm`` or ``kind`` is not
            ``digest``/``immediate`` (fail fast on programmer error).
    """

    slot: str
    date: str  # local date "YYYY-MM-DD" (grill Q3: slot boundary is local 12:00)
    category: str | None = None
    kind: SendKind = "digest"

    def __post_init__(self) -> None:
        if self.slot not in PUSH_SLOTS:
            raise ValueError(
                f"字段校验失败: SendContext.slot 必须是 {sorted(PUSH_SLOTS)} 之一,得到 {self.slot!r}"
            )
        if self.kind not in ("digest", "immediate"):
            raise ValueError(
                f"字段校验失败: SendContext.kind 必须是 digest/immediate 之一,得到 {self.kind!r}"
            )

    @property
    def slot_label(self) -> str:
        """Human slot label for card titles (am → 上午, pm → 下午)."""
        return "上午" if self.slot == "am" else "下午"


@dataclass(frozen=True)
class SendReport:
    """One channel-send outcome, aggregated by digest/immediate dispatch."""

    channel: str
    ok: bool
    item_count: int
    error: str | None = None


@runtime_checkable
class Channel(Protocol):
    """Delivery-channel contract (feishu_card / stdout / …).

    Implementations render internally (their own ``push[].template`` or the
    built-in layout) and raise :class:`PushSendError` on any failure; they
    never raise for empty item lists (callers skip those beforehand).
    """

    name: str

    async def send(
        self, items: Sequence[Mapping[str, Any] | object], context: SendContext
    ) -> None:
        """Deliver one message carrying ``items``. Raises PushSendError."""
        ...


def item_view(item: Mapping[str, Any] | object) -> dict[str, Any]:
    """Normalize one pipeline item into a plain dict for templates/channels.

    Dict-like items pass through as-is; objects contribute their known
    pipeline fields (url/title/source/category/scores/dedup_key). A
    ``metadata`` mapping is merged in, with top-level fields winning over
    same-name metadata keys (metadata holds engine-extracted extras such as
    ``symbol`` / ``change_pct``).
    """
    if isinstance(item, Mapping):
        view = dict(item)
        metadata = view.get("metadata")
    else:
        view = {key: getattr(item, key) for key in _ITEM_FIELDS if hasattr(item, key)}
        metadata = getattr(item, "metadata", None)
    if isinstance(metadata, Mapping):
        view = {**metadata, **view}
    return view


def also_seen_list(item: Mapping[str, Any] | object) -> list[dict[str, Any]]:
    """The item's merged-card 「另见 N 源」 entries (v0.4 事件聚合).

    Reads ``metadata['also_seen']`` (list of ``{title, url, source}`` dicts
    written by the pipeline's aggregate stage); non-list/empty/ entries
    without a title degrade to an empty list — 渲染层对脏数据 fail-quiet,
    合并决策的正确性由 aggregate 阶段负责.
    """
    view = item_view(item)
    entries = view.get("also_seen")
    if not isinstance(entries, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        title = entry.get("title")
        url = entry.get("url")
        if not isinstance(title, str) or not title.strip():
            continue
        cleaned.append(
            {
                "title": title,
                "url": url if isinstance(url, str) and url.strip() else "",
                "source": entry.get("source") if isinstance(entry.get("source"), str) else "",
            }
        )
    return cleaned
