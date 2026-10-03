"""Threshold routing: ``push.route[]`` rules + v0.1 category default mapping.

Decision order per item (PRD v01-push-feishu-route, grill Q1/Q3):

1. **Score rules** (v0.2 interface — enrich backfills a scalar ``score``):
   rules are evaluated in declared order, first match wins. Rules that
   reference ``score`` stay dormant while the item carries no score, so
   v0.1 YAMLs written for v0.2 scoring degrade cleanly.
2. **YAML override**: non-score rules (e.g. ``when: "category in
   ['server']"``) act as the category-mapping override grill Q1 promises.
3. **No score** → the grill-Q1 category default mapping: 羊毛/节点/代买 →
   immediate; AI资讯/服务器/token/信用卡/渠道 → digest; any other or missing
   category → the conservative default (digest, 不打扰).
4. **Score present, no rule matched**: no rules configured at all →
   immediate (v1.7 定案「无 route 缺省 immediate」, 兼容简单用法); rules
   configured but unmatched → conservative digest (a rule-set gap must not
   leak spam).

``when`` expressions reuse the classify whitelist-AST evaluator
(:class:`shishi.classify.custom.Rule`) — the same safe grammar, the same
per-item isolation, never ``eval``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from shishi.classify.custom import Rule, RuleSyntaxError
from shishi.schema import ROUTE_MODES

__all__ = [
    "SCORE_FIELD",
    "CATEGORY_DEFAULT_ROUTES",
    "DEFAULT_ROUTE_UNMATCHED",
    "DEFAULT_ROUTE_NO_RULES",
    "RouteConfigError",
    "RouteRule",
    "RouteDecision",
    "RouteBuckets",
    "resolve_route",
    "route",
    "routes_from_config",
]

logger = logging.getLogger(__name__)

#: Item field carrying the v0.2 LLM score used by threshold rules.
SCORE_FIELD = "score"

#: grill Q1 default mapping (v0.1, no score yet). ``channel`` (付费渠道广告)
#: is not in the immediate whitelist — production semantics are 「只推白给型」,
#: so it lands in the conservative digest bucket.
CATEGORY_DEFAULT_ROUTES: dict[str, str] = {
    "freebie": "immediate",  # 羊毛
    "proxy-node": "immediate",  # 节点
    "buying-agent": "immediate",  # 代买
    "ai-news": "digest",  # AI资讯
    "server": "digest",  # 服务器
    "token": "digest",  # token
    "credit-card": "digest",  # 信用卡
    "channel": "digest",  # 渠道(付费中转广告)
}

#: Category not in the mapping (or missing) → conservative digest (不打扰).
DEFAULT_ROUTE_UNMATCHED = "digest"
#: Score present but no ``push.route[]`` configured → immediate (v1.7 兼容).
DEFAULT_ROUTE_NO_RULES = "immediate"


class RouteConfigError(ValueError):
    """Invalid ``push.route[]`` config (mode/when/entry shape) — fail fast."""


@dataclass
class RouteRule:
    """One threshold route: ``when`` expression → ``mode``.

    The expression is validated at construction through the classify
    whitelist-AST evaluator (never ``eval``); ``fields`` lists the item
    fields it references and :attr:`references_score` reports whether it is
    a v0.2 score-threshold rule.

    ``targets``(10-03-messaging-core design D4):规则级定向对象 specs,
    覆盖通道级 ``targets``/legacy ``target``;格式与同平台约束由 schema
    加载期校验,这里只透传。

    Raises:
        RouteConfigError: unknown mode, or the ``when`` expression violates
            the whitelist grammar (wraps :class:`RuleSyntaxError`).
    """

    when: str
    mode: str
    targets: list[str] | None = None
    _evaluator: Rule = field(init=False, repr=False, compare=False)
    _fields: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode not in ROUTE_MODES:
            raise RouteConfigError(
                f"字段校验失败: push.route[].mode 必须是 {list(ROUTE_MODES)} 之一,得到 {self.mode!r}"
            )
        try:
            self._evaluator = Rule(name=f"route→{self.mode}", when=self.when, tag=self.mode)
        except RuleSyntaxError as exc:
            raise RouteConfigError(f"字段校验失败: push.route[].when 无效({exc})") from exc
        self._fields = self._evaluator.fields

    @property
    def fields(self) -> tuple[str, ...]:
        """Item fields referenced by the ``when`` expression, in order."""
        return self._fields

    @property
    def references_score(self) -> bool:
        """Whether the expression reads the v0.2 score field."""
        return SCORE_FIELD in self._fields

    def matches(self, item: Mapping[str, Any] | object) -> bool:
        """Evaluate the expression against one item (isolated, never raises)."""
        return self._evaluator.evaluate(item)


@dataclass(frozen=True)
class RouteDecision:
    """Routing outcome for one item, with a traceable reason.

    ``reason``: ``rule`` | ``category_default`` | ``conservative_default`` |
    ``no_rules_default``. ``rule_when`` carries the winning expression.
    ``targets``(design D4):命中规则声明的定向对象(无则 None——调用方
    回落通道级 targets / legacy target)。
    """

    mode: str
    reason: str
    rule_when: str | None = None
    targets: list[str] | None = None


@dataclass
class RouteBuckets:
    """Items grouped by route mode; built by :func:`route`."""

    immediate: list[Any] = field(default_factory=list)
    digest: list[Any] = field(default_factory=list)
    archive: list[Any] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "immediate": len(self.immediate),
            "digest": len(self.digest),
            "archive": len(self.archive),
        }


def resolve_route(
    item: Mapping[str, Any] | object, rules: Sequence[RouteRule] | None
) -> RouteDecision:
    """Resolve the route mode for one item (see module docstring for order).

    Args:
        item: dict-like item or object with attributes (score/category are
            read via mapping key or attribute).
        rules: declared ``push.route[]`` rules, in order; may be empty.

    Returns:
        RouteDecision with the winning mode and why.
    """
    score = _item_field(item, SCORE_FIELD)
    muted = _item_field(item, "muted")
    has_score = score is not None  # muted 条目也有分(0.0):显式 score 规则照常评估
    for rule in rules or ():
        if not has_score and rule.references_score:
            continue  # v0.1 无 score:score 阈值规则休眠(v0.2 接口位)
        if rule.matches(item):
            logger.debug("路由命中规则: mode=%s when=%r", rule.mode, rule.when)
            return RouteDecision(
                mode=rule.mode,
                reason="rule",
                rule_when=rule.when,
                targets=list(rule.targets) if rule.targets else None,
            )
    if not has_score:
        category = _item_field(item, "category")
        mode = CATEGORY_DEFAULT_ROUTES.get(category) if isinstance(category, str) else None
        if mode is not None:
            return RouteDecision(mode=mode, reason="category_default")
        logger.debug("路由保守缺省: category=%r 未命中大类映射 → digest", category)
        return RouteDecision(mode=DEFAULT_ROUTE_UNMATCHED, reason="conservative_default")
    if not rules:
        if muted:
            # mute 命中且用户未配置任何规则:0.0 是降权信号而非真实评分,落入
            # v1.7 兼容缺省 immediate 会把用户明确静默的词条目每轮立即推送
            # (语义精确反转)——按保守缺省 digest 降权(PRD:命中即降权/归档)。
            return RouteDecision(mode=DEFAULT_ROUTE_UNMATCHED, reason="conservative_default")
        return RouteDecision(mode=DEFAULT_ROUTE_NO_RULES, reason="no_rules_default")
    logger.debug("路由保守缺省: 有 score 但规则未覆盖 → digest")
    return RouteDecision(mode=DEFAULT_ROUTE_UNMATCHED, reason="conservative_default")


def route(items: Iterable[Any] | None, rules: Sequence[RouteRule] | None) -> RouteBuckets:
    """Bucket items into immediate/digest/archive per ``push.route[]``.

    Raises:
        RouteConfigError: never — callers validate rules up front via
            :func:`routes_from_config`; per-item evaluation is isolated.
    """
    buckets = RouteBuckets()
    for item in items or ():
        decision = resolve_route(item, rules)
        getattr(buckets, decision.mode).append(item)
        logger.debug(
            "路由条目: mode=%s reason=%s title=%r",
            decision.mode,
            decision.reason,
            _item_field(item, "title"),
        )
    counts = buckets.counts()
    logger.info(
        "路由完成: immediate=%d digest=%d archive=%d",
        counts["immediate"],
        counts["digest"],
        counts["archive"],
    )
    return buckets


def routes_from_config(
    entries: Iterable[Mapping[str, Any] | Any] | None, *, source: str = "push.route"
) -> list[RouteRule]:
    """Build :class:`RouteRule` list from schema objects or raw mappings.

    Accepts pydantic ``RouteRuleConfig`` models (attribute access) or raw
    mappings (``{"when": ..., "mode": ..., "targets": [...]}``); unknown
    mapping fields fail fast (schema 铁律: 未知字段不许静默忽略)。

    Raises:
        RouteConfigError: entry shape invalid, unknown field, missing
            ``when``/``mode``, or an invalid expression/mode.
    """
    rules: list[RouteRule] = []
    for index, entry in enumerate(entries or ()):
        path = f"{source}[{index}]"
        if isinstance(entry, RouteRule):
            rules.append(entry)
            continue
        if isinstance(entry, Mapping):
            unknown = set(entry) - {"when", "mode", "targets"}
            if unknown:
                raise RouteConfigError(f"字段校验失败: {path} 未知字段 {sorted(unknown)}")
            when = entry.get("when")
            mode = entry.get("mode")
            targets = entry.get("targets") or None
        else:
            when = getattr(entry, "when", None)
            mode = getattr(entry, "mode", None)
            targets = getattr(entry, "targets", None) or None
        if not isinstance(when, str) or not when.strip():
            raise RouteConfigError(f"字段校验失败: {path} 缺少非空 when 表达式")
        if not isinstance(mode, str) or not mode:
            raise RouteConfigError(f"字段校验失败: {path} 缺少 mode")
        if targets is not None and not isinstance(targets, list):
            raise RouteConfigError(f"字段校验失败: {path} targets 必须是字符串列表")
        try:
            rules.append(RouteRule(when=when, mode=mode, targets=list(targets) if targets else None))
        except RouteConfigError as exc:
            raise RouteConfigError(f"{path}: {exc}") from exc
    return rules


def _item_field(item: Mapping[str, Any] | object, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)
