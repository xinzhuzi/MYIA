"""Jinja2 template rendering behind a sandboxed environment (grill Q2).

User-supplied ``push[].template`` renders with
``jinja2.sandbox.SandboxedEnvironment``: dunder/``__class__`` escapes raise
``SecurityError`` instead of executing, and unknown template variables raise
``UndefinedError`` (StrictUndefined) so a typo fails loudly per channel
instead of rendering silent blanks. Both surface as
:class:`TemplateRenderError` with the original error as ``__cause__``.

Trend functions (v0.4 趋势基线, PRD 10-01-v04-trend-baseline): every render
context also carries three sandbox-safe callables plus the keyword trend
list, so a template can ask for comparisons without touching the store:

- ``vs_yesterday(item, field)`` → 「较昨日 +5.2%」style text, computed from
  the newest ``metric_history`` snapshot recorded before 当地零点;
- ``vs_last_week(item, field)`` → 「较上周 −3.0%」style, baseline = newest
  snapshot before 本 ISO 周周一零点;
- ``vs_msrp(item)`` → 「低于 MSRP 8.0%」style, from the ``baseline.msrp``
  对照表 keyed by case-insensitive title substring;
- ``keyword_trends`` → list of :class:`KeywordTrend` (关键词提及量周环比,
  品类维度), rendered as a digest-card section.

The comparison data is assembled *before* rendering from the store
(:func:`build_trend_table` / :func:`build_keyword_trends`), so the sandboxed
template only ever sees plain dataclasses and closures — no store, no I/O,
no attribute-escape surface. History *recording* is explicit
(:func:`record_item_metrics` / :func:`record_keyword_mentions`): snapshots
are append-only and retention keeps them longer than items (基线期长于条目期).

The planning example (stocks.yaml, Handlebars ``{{#each}}``) is translated to
Jinja2 in :data:`STOCKS_EXAMPLE_TEMPLATE` per PRD v01-push-feishu-route; the
repo fixture ``tests/fixtures/stocks.yaml`` and every ``plugins/*.yaml``
template carry the Jinja2 form, and template *syntax* is refused at load time
(schema ``invalid_template``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, Iterable, Mapping, Sequence

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from shishi.push.base import SendContext, item_view
from shishi.store import (
    METRIC_WINDOW_DAY,
    METRIC_WINDOW_WEEK,
    METRIC_WINDOWS,
    metric_window_start,
)
from shishi.store.models import MetricRecord

__all__ = [
    "KEYWORD_METRIC_FIELD",
    "KEYWORD_METRIC_PREFIX",
    "STOCKS_EXAMPLE_TEMPLATE",
    "WINDOW_LABELS",
    "KeywordTrend",
    "MetricComparison",
    "TemplateRenderError",
    "TemplateRenderer",
    "TrendTable",
    "build_keyword_trends",
    "build_trend_table",
    "count_keyword_mentions",
    "format_change",
    "format_msrp",
    "numeric_value",
    "record_item_metrics",
    "record_keyword_mentions",
]

logger = logging.getLogger(__name__)

#: Jinja2 translation of the planning stocks example (PRD: {{#each}} →
#: {% for %}); context variables: items, date, slot, category, count.
STOCKS_EXAMPLE_TEMPLATE = """\
**股票异动 · {{ date }}**
{% for item in items %}
{{ item.symbol }} {{ item.change_pct }}% 现价 {{ item.price }}
{% endfor %}
"""

#: 窗口 → 卡片文案前缀(vs 昨日 / vs 上周)。
WINDOW_LABELS: dict[str, str] = {
    METRIC_WINDOW_DAY: "较昨日",
    METRIC_WINDOW_WEEK: "较上周",
}

#: 品类级关键词提及量的 metric_history 约定键(field 固定为 mentions,
#: 值为该 run 命中该词的条目数;周累计由 sum_metrics 半开窗口求和)。
KEYWORD_METRIC_PREFIX = "keyword:"
KEYWORD_METRIC_FIELD = "mentions"


class TemplateRenderError(RuntimeError):
    """``push[].template`` rendering failed (syntax / sandbox / undefined var).

    ``__cause__`` keeps the original jinja2 error for debugging.
    """


# ---------------------------------------------------------------------------
# Numeric helpers (模板函数与趋势装配共用的取数/格式化)
# ---------------------------------------------------------------------------


def numeric_value(raw: Any) -> float | None:
    """Coerce a raw item field to a finite float; None when it is not one.

    bool 不是数值(显式排除);NaN/inf 一律按「无数值」处理 —— 缺值/脏值渲染
    为空对比而不是渲染误导性数字(fail-quiet 于展示,fail-fast 于写入:
    store.save_metric 仍会对坏值抛错)。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    number = float(raw)
    return number if math.isfinite(number) else None


def item_metric_key(view: Mapping[str, Any]) -> str | None:
    """The stable per-item identity for metric history (url 优先,退回 dedup_key).

    条目 dedup 键带上状态后(games 的 ``{url}-{final_price}``、stocks 的
    ``{symbol}-{date}-{slot}`` 都随价格态/槽位轮换),dedup_key 不再是跨轮
    稳定身份——键一换,昨日/上周快照就断链,vs_yesterday/vs_last_week 恒空。
    价格基线的稳定身份是商品 url(url 恒为 schema 必填的真值——stocks 以
    symbol 充 url,取到的就是 "NVDA" 这样的稳定码,同样受益);url 缺失
    或为空的兜底形态才退回 dedup_key(stocks 若将来开基线,其键带槽位轮换
    正是本反转的动机例)。
    """
    key = view.get("url") or view.get("dedup_key")
    return key if isinstance(key, str) and key else None


def format_change(comparison: "MetricComparison") -> str:
    """One comparison as card text: 「较上周 +5.2%」/ 基线为 0 时给绝对差.

    百分比一律带符号、保留 1 位小数;基线为 0(百分比无定义)退回绝对差
    (``+g`` 格式,同样带符号)。
    """
    label = WINDOW_LABELS.get(comparison.window, comparison.window)
    pct = comparison.change_pct
    if pct is None:
        return f"{label} {comparison.change:+g}"
    return f"{label} {pct:+.1f}%"


def format_msrp(price: float, msrp: float) -> str:
    """Price vs MSRP as card text: 「低于 MSRP 8.0%」/「高于 MSRP 3.1%」/「MSRP 平价」."""
    pct = (price - msrp) / msrp * 100.0
    if pct < 0:
        return f"低于 MSRP {abs(pct):.1f}%"
    if pct > 0:
        return f"高于 MSRP {pct:.1f}%"
    return "MSRP 平价"


# ---------------------------------------------------------------------------
# Comparison records & trend table (precomputed render data, no store inside)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricComparison:
    """One (item key, field, window) comparison, ready for rendering.

    ``current`` is the live item value from this run; ``baseline`` is the
    newest store snapshot recorded strictly before the window start (见
    ``Store.get_metric_baseline``).
    """

    window: str  # METRIC_WINDOW_DAY / METRIC_WINDOW_WEEK
    field: str
    current: float
    baseline: float
    baseline_recorded_at: datetime | None = None

    @property
    def change(self) -> float:
        """Absolute change: current − baseline."""
        return self.current - self.baseline

    @property
    def change_pct(self) -> float | None:
        """Signed percentage change; None when the baseline is 0 (无定义)."""
        if self.baseline == 0:
            return None
        return self.change / abs(self.baseline) * 100.0


class TrendTable:
    """Precomputed ``(metric_key, field, window) → MetricComparison`` lookups.

    Built once per send by :func:`build_trend_table`; the sandbox template
    functions close over it. Missing lookups simply render as empty text —
    首次观察没有基线是正常路径,不是渲染错误.
    """

    def __init__(
        self, comparisons: Mapping[tuple[str, str, str], MetricComparison] | None = None
    ) -> None:
        self._comparisons: dict[tuple[str, str, str], MetricComparison] = dict(
            comparisons or {}
        )

    def get(self, metric_key: str, field: str, window: str) -> MetricComparison | None:
        """Return the comparison for one key, or None when absent."""
        return self._comparisons.get((metric_key, field, window))

    def __len__(self) -> int:
        return len(self._comparisons)


def record_item_metrics(
    store: Any,
    *,
    category: str,
    items: Sequence[Any],
    fields: Sequence[str],
    now: datetime | None = None,
) -> int:
    """Append one ``metric_history`` snapshot per (item, numeric field).

    「历史写入」入口:对 baseline.fields 里声明的每个数值字段,逐条目把本轮
    取到的值写入快照表(append-only;同一 run 内同键多次出现按条各记一行,
    窗口对比取「窗口开始前最新一行」,天然取到上一轮的值)。非数值/缺失
    字段静默跳过(字符串字段不是基线素材),返回写入行数。
    """
    written = 0
    for item in items:
        view = item_view(item)
        key = item_metric_key(view)
        if key is None:
            continue
        for name in fields:
            value = numeric_value(view.get(name))
            if value is None:
                continue
            store.save_metric(category, key, name, value, recorded_at=now)
            written += 1
    logger.debug(
        "数值快照写入 category=%s items=%s fields=%s rows=%s", category, len(items), list(fields), written
    )
    return written


def build_trend_table(
    store: Any,
    *,
    category: str,
    items: Sequence[Any],
    fields: Sequence[str],
    now: datetime,
    tz: tzinfo | None = None,
    windows: Sequence[str] = (METRIC_WINDOW_DAY, METRIC_WINDOW_WEEK),
) -> TrendTable:
    """Precompute every (item, field, window) comparison from the store.

    「窗口对比」装配:current 取本轮条目值,baseline 取窗口开始前最新快照
    (store 只读)。当前值缺失/非数值的条目不产生对比(渲染为空);窗口
    必须是 ``day``/``week`` 子集(fail-fast)。

    Raises:
        ValueError: ``windows`` 为空或含未知窗口.
    """
    unknown = [w for w in windows if w not in METRIC_WINDOWS]
    if not windows or unknown:
        raise ValueError(
            f"字段校验失败: 对比窗口必须是 {sorted(METRIC_WINDOWS)} 的非空子集,得到 {list(windows)}"
        )
    comparisons: dict[tuple[str, str, str], MetricComparison] = {}
    for item in items:
        view = item_view(item)
        key = item_metric_key(view)
        if key is None:
            continue
        for name in fields:
            current = numeric_value(view.get(name))
            if current is None:
                continue
            for window in windows:
                baseline: MetricRecord | None = store.get_metric_baseline(
                    category, key, name, now=now, window=window, tz=tz
                )
                if baseline is None:
                    continue
                comparisons[(key, name, window)] = MetricComparison(
                    window=window,
                    field=name,
                    current=current,
                    baseline=baseline.value,
                    baseline_recorded_at=baseline.recorded_at,
                )
    logger.debug(
        "趋势对比装配完成 category=%s items=%s fields=%s comparisons=%s",
        category, len(items), list(fields), len(comparisons),
    )
    return TrendTable(comparisons)


# ---------------------------------------------------------------------------
# Keyword mention week-over-week (关键词提及量周环比, 品类维度)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeywordTrend:
    """One watched keyword's mention count vs last week (品类维度)."""

    word: str
    count: int  # 本周累计提及条目数(ISO 周半开窗口求和)
    previous: int  # 上一周累计

    @property
    def change(self) -> int:
        return self.count - self.previous

    @property
    def change_pct(self) -> float | None:
        """Signed week-over-week percentage; None when last week was 0."""
        if self.previous == 0:
            return None
        return self.change / self.previous * 100.0

    @property
    def change_text(self) -> str:
        """Card-ready 环比文案:「环比 +40.0%」/「环比 新增」/「环比 持平」."""
        pct = self.change_pct
        if pct is None:
            return "环比 新增" if self.count > 0 else "环比 持平"
        return f"环比 {pct:+.1f}%"


def count_keyword_mentions(
    items: Sequence[Any], keywords: Sequence[str]
) -> dict[str, int]:
    """Count items whose title mentions each keyword (品类维度统计口径).

    口径:标题大小写不敏感子串命中,每条目每词计 1(「提及条目数」而非
    「出现次数」,与 watchlist 关键词命中语义对齐,可跨 run 累加)。
    """
    counts = {word: 0 for word in keywords}
    lowered = {word: word.lower() for word in keywords}
    for item in items:
        view = item_view(item)
        title = view.get("title")
        if not isinstance(title, str) or not title:
            continue
        title_lower = title.lower()
        for word in keywords:
            if lowered[word] in title_lower:
                counts[word] += 1
    return counts


def record_keyword_mentions(
    store: Any,
    *,
    category: str,
    items: Sequence[Any],
    keywords: Sequence[str],
    now: datetime | None = None,
) -> dict[str, int]:
    """Count this run's keyword mentions and append them as metric snapshots.

    每个 watchlist 词都落一行(含 0 命中):零也要入账,否则下周的周环比
    分不清「没统计」和「没提及」。返回本轮命中计数(观测/单测消费)。
    """
    counts = count_keyword_mentions(items, keywords)
    for word, count in counts.items():
        store.save_metric(
            category,
            f"{KEYWORD_METRIC_PREFIX}{word}",
            KEYWORD_METRIC_FIELD,
            count,
            recorded_at=now,
        )
    logger.debug(
        "关键词提及快照写入 category=%s words=%s hits=%s",
        category, list(counts), sum(counts.values()),
    )
    return counts


def build_keyword_trends(
    store: Any,
    *,
    category: str,
    keywords: Sequence[str],
    now: datetime,
    tz: tzinfo | None = None,
) -> list[KeywordTrend]:
    """Aggregate weekly mention totals into render-ready trends (周环比).

    本周 = ``[本周一零点, +7天)``,上周 = 前移一周;两个半开窗口恰好铺满
    相邻两周。窗口数学与数值基线共用 :func:`shishi.store.metric_window_start`
    (同一份窗口语义)。返回按本周计数降序的列表(卡片热度排序)。
    """
    week_start = metric_window_start(now, METRIC_WINDOW_WEEK, tz)
    week_end = week_start + timedelta(days=7)
    trends: list[KeywordTrend] = []
    for word in keywords:
        key = f"{KEYWORD_METRIC_PREFIX}{word}"
        count = int(
            store.sum_metrics(
                category, key, KEYWORD_METRIC_FIELD, since=week_start, until=week_end
            )
        )
        previous = int(
            store.sum_metrics(
                category,
                key,
                KEYWORD_METRIC_FIELD,
                since=week_start - timedelta(days=7),
                until=week_start,
            )
        )
        trends.append(KeywordTrend(word=word, count=count, previous=previous))
    trends.sort(key=lambda trend: (-trend.count, trend.word))
    return trends


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _vs_function(
    window: str, table: TrendTable | None
) -> Any:
    """Build the sandbox ``vs_yesterday`` / ``vs_last_week`` callable.

    合同:入参 ``(item, field)``,返回卡片文案;无表/无该键/无基线一律
    返回空串 —— 缺历史是正常渲染路径,不是错误。
    """

    def vs(item: Any, field: str) -> str:
        if table is None:
            return ""
        view = item_view(item)
        key = item_metric_key(view)
        if key is None:
            return ""
        comparison = table.get(key, field, window)
        if comparison is None:
            return ""
        return format_change(comparison)

    return vs


def _msrp_function(msrp: Mapping[str, float] | None) -> Any:
    """Build the sandbox ``vs_msrp`` callable (MSRP 对照表 → 卡片文案).

    合同:入参 ``(item,)``;在 ``baseline.msrp`` 对照表里按标题大小写不敏感
    子串匹配商品名(多键命中取最长键,最具体的对照优先),返回
    「低于 MSRP 8.0%」风格文案;无表/无价格/无匹配返回空串。

    隐性契约(v1.1 披露):价格**只读条目视图的 ``price`` 字段**——与
    ``vs_yesterday`` / ``vs_last_week`` 接受任意 ``field`` 参数不同,
    ``vs_msrp`` 没有字段参数,钉死 ``price``;源 extract 产出的价格字段若
    不叫 ``price``(或非数值),该品类恒返回空串。
    """
    table = {name.lower(): value for name, value in (msrp or {}).items()}

    def vs_msrp(item: Any) -> str:
        if not table:
            return ""
        view = item_view(item)
        title = view.get("title")
        price = numeric_value(view.get("price"))
        if not isinstance(title, str) or not title or price is None:
            return ""
        title_lower = title.lower()
        matches = [name for name in table if name in title_lower]
        if not matches:
            return ""
        best = max(matches, key=len)  # 最长键 = 最具体的商品对照
        return format_msrp(price, table[best])

    return vs_msrp


class TemplateRenderer:
    """Renders user templates against a shared, hardened Jinja2 environment.

    Environment hardening: ``SandboxedEnvironment`` (no unsafe attribute
    access), ``StrictUndefined`` (missing variables error out), block tags
    consume their own line (``trim_blocks``/``lstrip_blocks``) so YAML block
    scalars render without stray blank lines.

    Every render injects the v0.4 trend context (``vs_yesterday`` /
    ``vs_last_week`` / ``vs_msrp`` callables + ``keyword_trends`` list), so a
    trend-aware template renders on any call path — callers without trend
    data simply get empty comparisons and an empty keyword list.
    """

    def __init__(self, *, environment: SandboxedEnvironment | None = None) -> None:
        self._environment = environment or SandboxedEnvironment(
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )

    def render(
        self,
        source: str,
        items: Sequence[Any],
        context: SendContext,
        *,
        trends: TrendTable | None = None,
        keyword_trends: Iterable[KeywordTrend] | None = None,
        msrp: Mapping[str, float] | None = None,
    ) -> str:
        """Render ``source`` with items plus date/slot/category/count context.

        Args:
            source: template text (Jinja2 syntax).
            items: raw pipeline items; each is normalized via
                :func:`shishi.push.base.item_view` before rendering.
            context: slot/date/category/kind of the enclosing send.
            trends: precomputed per-item comparisons
                (:func:`build_trend_table`); ``None`` → ``vs_yesterday`` /
                ``vs_last_week`` render empty strings.
            keyword_trends: 关键词提及量周环比 records
                (:func:`build_keyword_trends`); ``None`` → empty list.
            msrp: MSRP 对照表(schema ``baseline.msrp``);``None``/empty →
                ``vs_msrp`` renders empty strings. ``vs_msrp`` 只认条目视图的
                ``price`` 字段(无字段参数,契约见 :func:`_msrp_function`).

        Returns:
            The rendered text (trimmed of surrounding whitespace).

        Raises:
            TemplateRenderError: empty source, syntax error, sandbox
                violation, or undefined variable (original error as cause).
        """
        if not isinstance(source, str) or not source.strip():
            raise TemplateRenderError("push[].template 必须是非空字符串")
        try:
            template = self._environment.from_string(source)
            rendered = template.render(
                items=[item_view(item) for item in items],
                date=context.date,
                slot=context.slot,
                category=context.category,
                count=len(items),
                vs_yesterday=_vs_function(METRIC_WINDOW_DAY, trends),
                vs_last_week=_vs_function(METRIC_WINDOW_WEEK, trends),
                vs_msrp=_msrp_function(msrp),
                keyword_trends=list(keyword_trends or []),
            )
        except TemplateError as exc:
            raise TemplateRenderError(
                f"push[].template 渲染失败: {type(exc).__name__}: {exc}"
            ) from exc
        logger.debug(
            "模板渲染完成: chars=%d slot=%s count=%d", len(rendered), context.slot, len(items)
        )
        return rendered.strip()
