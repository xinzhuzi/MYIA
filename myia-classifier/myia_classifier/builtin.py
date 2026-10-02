"""Built-in seven-category classifier: the zero-token first funnel.

Faithful port of the production crawler's keyword classifier with the
free/paid dual-signal adjudication (2026-09-28 revision), semantics verified
by running the production ``cat_of`` over the real corpus. Keyword tables
live in ``data/keywords.json`` so the owner or an AI can tune them without
code changes; this module only loads the tables and runs the adjudication.

Adjudication order (production semantics, pinned by the gold set):

1. dead/noise filters -> discard (category ``None``)
2. registration lure: title contains 注册 without any direct-give signal
   -> discard (blocks "register xxx" bait posts)
3. free signal (freebie table union direct-give) beats paid signal:
   token-context hit -> ``token``, otherwise ``freebie``
4. no free signal: primary categories scanned in priority order
   (credit-card -> proxy-node -> buying-agent -> server)
5. paid signal or paid-target (channel) hit -> ``channel``
6. fallback scan -> the configured ``fallback_target`` (ai-news). Note the
   token pattern only gates this step: ``token`` is reachable exclusively
   through the free branch (production behavior, pinned by the gold set)
7. otherwise ``None`` -- the caller decides whether to drop the item

Known divergence in the production source: its docstring claims the four
primary categories also accept free titles, but the code gives the free
signal absolute priority (checked first). The code behavior is implemented
here; flagged for owner confirmation in the task's open issues.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "ALL_CATEGORIES",
    "BuiltinResult",
    "CATEGORY_CHANNEL",
    "ClassifyDataError",
    "ClassifyTable",
    "DEFAULT_TABLE_PATH",
    "MatchEvidence",
    "SEVEN_CATEGORIES",
    "category_label",
    "classify_title",
    "load_table",
]

logger = logging.getLogger(__name__)

#: The seven MYIA categories (kept stable, pinned by tests/test_smoke.py).
SEVEN_CATEGORIES: list[str] = [
    "credit-card",
    "proxy-node",
    "buying-agent",
    "server",
    "token",
    "ai-news",
    "freebie",
]

#: Eighth production label: destination of paid-signal-without-free-signal
#: titles (要掏钱的,绝不进羊毛). Not part of SEVEN_CATEGORIES pending owner
#: confirmation -- see task open issues.
CATEGORY_CHANNEL = "channel"

ALL_CATEGORIES: list[str] = [*SEVEN_CATEGORIES, CATEGORY_CHANNEL]

DEFAULT_TABLE_PATH = Path(__file__).resolve().with_name("data") / "keywords.json"

_STAGE_FREE_TARGET = "free_target"
_STAGE_PRIMARY = "primary"
_STAGE_PAID_TARGET = "paid_target"
_STAGE_FALLBACK = "fallback"
_KNOWN_STAGES = frozenset(
    {_STAGE_FREE_TARGET, _STAGE_PRIMARY, _STAGE_PAID_TARGET, _STAGE_FALLBACK}
)

_TABLE_TOP_KEYS = frozenset({"version", "description", "categories", "signals", "filters", "adjudication"})
_SECTION_KEYS = frozenset({"keywords", "patterns", "case_insensitive", "target_category", "description"})


class ClassifyDataError(ValueError):
    """Raised when the keyword table is missing, malformed or invalid.

    The message is structured: ``字段路径: 原因`` so AI/user edits can be
    fixed without reading the loader.
    """


@dataclass(frozen=True)
class MatchEvidence:
    """One keyword/pattern hit, kept for traceability and rule tuning."""

    table: str  # dotted data-file section, e.g. "category:token" / "signals.paid"
    kind: str  # "keyword" | "pattern"
    value: str  # the keyword or pattern exactly as written in the data file
    start: int  # match span within the normalized title
    end: int

    def describe(self) -> str:
        return f"{self.table}:{self.kind}={self.value}"


@dataclass(frozen=True)
class _Entry:
    """A compiled matcher plus its raw source for evidence output."""

    kind: str  # "keyword" | "pattern"
    value: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Section:
    """One named table section: compiled keywords/patterns (+ optional target)."""

    name: str  # dotted path in the data file, e.g. "signals.paid"
    entries: tuple[_Entry, ...] = ()
    target_category: str | None = None


@dataclass(frozen=True)
class Category:
    """One category with its keyword section and adjudication stage."""

    id: str
    label: str
    stage: str
    priority: int
    section: Section


@dataclass(frozen=True)
class BuiltinResult:
    """Classification outcome for one title, with hit trace."""

    category: str | None
    matched: tuple[MatchEvidence, ...]
    normalized_title: str


@dataclass(frozen=True)
class ClassifyTable:
    """Compiled keyword table; build via :func:`load_table`."""

    version: int
    source: str
    categories: tuple[Category, ...]  # sorted by (priority, file order)
    free_target: Category
    paid_target: Category
    primary_categories: tuple[Category, ...]
    fallback_categories: tuple[Category, ...]
    direct_give: Section
    paid_signal: Section
    token_context: Section
    dead_filter: Section
    noise_filter: Section
    registration_lure: Section
    fallback_target: str

    def label_of(self, category_id: str) -> str | None:
        """Return the human label (e.g. "🎁羊毛") for a category id."""
        for category in self.categories:
            if category.id == category_id:
                return category.label
        return None


def _compile_entry(kind: str, value: str, flags: int, path: str) -> _Entry:
    source = re.escape(value) if kind == "keyword" else value
    try:
        return _Entry(kind=kind, value=value, regex=re.compile(source, flags))
    except re.error as exc:
        raise ClassifyDataError(f"分类关键词表字段错误: {path}: 正则编译失败: {exc}") from exc


def _build_section(name: str, raw: Any, base_path: str) -> Section:
    if not isinstance(raw, Mapping):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}: 必须是对象, got {type(raw).__name__}")
    unknown = set(raw) - _SECTION_KEYS
    if unknown:
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}: 未知字段 {sorted(unknown)}")
    case_insensitive = raw.get("case_insensitive", False)
    if not isinstance(case_insensitive, bool):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.case_insensitive: 必须是布尔值")
    flags = re.UNICODE | (re.IGNORECASE if case_insensitive else 0)
    entries: list[_Entry] = []
    for kind, key in (("keyword", "keywords"), ("pattern", "patterns")):
        raw_items = raw.get(key, [])
        if not isinstance(raw_items, list) or not all(isinstance(item, str) for item in raw_items):
            raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.{key}: 必须是字符串列表")
        for index, item in enumerate(raw_items):
            if not item:
                raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.{key}[{index}]: 不能为空字符串")
            entries.append(_compile_entry(kind, item, flags, f"{base_path}.{key}[{index}]"))
    target = raw.get("target_category")
    if target is not None and not isinstance(target, str):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.target_category: 必须是字符串")
    return Section(name=name, entries=tuple(entries), target_category=target)


def _build_category(raw: Any, index: int) -> Category:
    base_path = f"categories[{index}]"
    if not isinstance(raw, Mapping):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}: 必须是对象")
    unknown = set(raw) - (_SECTION_KEYS | {"id", "label", "stage", "priority"})
    if unknown:
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}: 未知字段 {sorted(unknown)}")
    category_id = raw.get("id")
    if not isinstance(category_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", category_id):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.id: 必须是 snake_case 字符串, got {category_id!r}")
    label = raw.get("label")
    if not isinstance(label, str) or not label:
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.label: 必须是非空字符串")
    stage = raw.get("stage")
    if stage not in _KNOWN_STAGES:
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.stage: 必须是 {_KNOWN_STAGES} 之一, got {stage!r}")
    priority = raw.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ClassifyDataError(f"分类关键词表字段错误: {base_path}.priority: 必须是整数, got {priority!r}")
    # categories carry both adjudication metadata and a keyword section;
    # the section builder must only see the section keys
    section_raw = {key: raw[key] for key in _SECTION_KEYS if key in raw}
    section = _build_section(f"category:{category_id}", section_raw, base_path)
    return Category(id=category_id, label=label, stage=stage, priority=priority, section=section)


def _validate_references(categories: tuple[Category, ...], token_context: Section, fallback_target: Any) -> None:
    ids = [category.id for category in categories]
    duplicated = {category_id for category_id in ids if ids.count(category_id) > 1}
    if duplicated:
        raise ClassifyDataError(f"分类关键词表字段错误: categories: 类目 id 重复: {sorted(duplicated)}")
    free_targets = [category for category in categories if category.stage == _STAGE_FREE_TARGET]
    if len(free_targets) != 1:
        raise ClassifyDataError(
            f"分类关键词表字段错误: categories: 必须有且只有一个 stage=free_target 类目, 当前 {len(free_targets)} 个"
        )
    paid_targets = [category for category in categories if category.stage == _STAGE_PAID_TARGET]
    if len(paid_targets) != 1:
        raise ClassifyDataError(
            f"分类关键词表字段错误: categories: 必须有且只有一个 stage=paid_target 类目, 当前 {len(paid_targets)} 个"
        )
    if token_context.target_category is None or token_context.target_category not in ids:
        raise ClassifyDataError(
            f"分类关键词表字段错误: signals.token_context.target_category: "
            f"必须指向存在的类目 id, got {token_context.target_category!r}"
        )
    if not isinstance(fallback_target, str) or fallback_target not in ids:
        raise ClassifyDataError(
            f"分类关键词表字段错误: adjudication.fallback_target: 必须指向存在的类目 id, got {fallback_target!r}"
        )


def load_table(path: str | Path = DEFAULT_TABLE_PATH) -> ClassifyTable:
    """Load and validate a keyword table JSON file.

    Raises:
        ClassifyDataError: file missing, JSON invalid, unknown fields or
            broken references -- fail fast with 字段路径 + 原因.
    """
    table_path = Path(path)
    if not table_path.is_file():
        raise ClassifyDataError(f"分类关键词表加载失败: 文件不存在: {table_path}")
    try:
        data = json.loads(table_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ClassifyDataError(f"分类关键词表加载失败: {table_path}: JSON 解析错误: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ClassifyDataError(f"分类关键词表加载失败: {table_path}: 顶层必须是对象")
    unknown = set(data) - _TABLE_TOP_KEYS
    if unknown:
        raise ClassifyDataError(f"分类关键词表字段错误: 顶层未知字段 {sorted(unknown)}")
    version = data.get("version")
    if version != 1 or isinstance(version, bool):
        raise ClassifyDataError(f"分类关键词表字段错误: version: 仅支持 1, got {version!r}")

    raw_categories = data.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ClassifyDataError("分类关键词表字段错误: categories: 必须是非空列表")
    categories = tuple(_build_category(raw, index) for index, raw in enumerate(raw_categories))

    raw_signals = data.get("signals")
    if not isinstance(raw_signals, Mapping) or set(raw_signals) != {"direct_give", "paid", "token_context"}:
        raise ClassifyDataError(
            "分类关键词表字段错误: signals: 必须包含且仅包含 direct_give/paid/token_context"
        )
    direct_give = _build_section("signals.direct_give", raw_signals["direct_give"], "signals.direct_give")
    paid_signal = _build_section("signals.paid", raw_signals["paid"], "signals.paid")
    token_context = _build_section("signals.token_context", raw_signals["token_context"], "signals.token_context")

    raw_filters = data.get("filters")
    if not isinstance(raw_filters, Mapping) or set(raw_filters) != {"dead", "noise", "registration_lure"}:
        raise ClassifyDataError("分类关键词表字段错误: filters: 必须包含且仅包含 dead/noise/registration_lure")
    dead_filter = _build_section("filters.dead", raw_filters["dead"], "filters.dead")
    noise_filter = _build_section("filters.noise", raw_filters["noise"], "filters.noise")
    registration_lure = _build_section(
        "filters.registration_lure", raw_filters["registration_lure"], "filters.registration_lure"
    )

    raw_adjudication = data.get("adjudication", {})
    if not isinstance(raw_adjudication, Mapping):
        raise ClassifyDataError("分类关键词表字段错误: adjudication: 必须是对象")
    unknown_adjudication = set(raw_adjudication) - {"fallback_target", "description"}
    if unknown_adjudication:
        raise ClassifyDataError(f"分类关键词表字段错误: adjudication: 未知字段 {sorted(unknown_adjudication)}")
    fallback_target = raw_adjudication.get("fallback_target")

    _validate_references(categories, token_context, fallback_target)

    ordered = tuple(sorted(categories, key=lambda category: category.priority))
    return ClassifyTable(
        version=1,
        source=str(table_path),
        categories=ordered,
        free_target=next(c for c in ordered if c.stage == _STAGE_FREE_TARGET),
        paid_target=next(c for c in ordered if c.stage == _STAGE_PAID_TARGET),
        primary_categories=tuple(c for c in ordered if c.stage == _STAGE_PRIMARY),
        fallback_categories=tuple(c for c in ordered if c.stage == _STAGE_FALLBACK),
        direct_give=direct_give,
        paid_signal=paid_signal,
        token_context=token_context,
        dead_filter=dead_filter,
        noise_filter=noise_filter,
        registration_lure=registration_lure,
        fallback_target=fallback_target,
    )


@lru_cache(maxsize=1)
def _default_table() -> ClassifyTable:
    return load_table()


def _scan(section: Section, title: str, evidence: list[MatchEvidence]) -> bool:
    """Record every keyword/pattern hit of ``section``; return True on any hit."""
    hit = False
    for entry in section.entries:
        match = entry.regex.search(title)
        if match is not None:
            hit = True
            evidence.append(
                MatchEvidence(
                    table=section.name,
                    kind=entry.kind,
                    value=entry.value,
                    start=match.start(),
                    end=match.end(),
                )
            )
    return hit


def classify_title(title: str, table: ClassifyTable | None = None) -> BuiltinResult:
    """Classify one title with the dual-signal adjudication.

    Args:
        title: raw title; whitespace is normalized exactly like production
            (``re.sub(r"\\s+", " ", ...).strip()``) before matching.
        table: preloaded table; defaults to the packaged ``data/keywords.json``.

    Returns:
        BuiltinResult with ``category`` (id or ``None`` for discarded titles)
        and the hit trace (which table/keyword/pattern matched where).

    Raises:
        TypeError: ``title`` is not a string.
        ClassifyDataError: the default table is malformed (propagated from
            first use; use :func:`load_table` to fail fast at startup).
    """
    if table is None:
        table = _default_table()
    if not isinstance(title, str):
        raise TypeError(f"分类标题必须是字符串, got {type(title).__name__}")
    normalized = re.sub(r"\s+", " ", title).strip()

    evidence: list[MatchEvidence] = []
    if _scan(table.dead_filter, normalized, evidence):
        logger.debug("分类丢弃: reason=dead title=%r evidence=%s", normalized, [e.describe() for e in evidence])
        return BuiltinResult(category=None, matched=tuple(evidence), normalized_title=normalized)
    if _scan(table.noise_filter, normalized, evidence):
        logger.debug("分类丢弃: reason=noise title=%r evidence=%s", normalized, [e.describe() for e in evidence])
        return BuiltinResult(category=None, matched=tuple(evidence), normalized_title=normalized)

    reg_hit = _scan(table.registration_lure, normalized, evidence)
    give_hit = _scan(table.direct_give, normalized, evidence)
    if reg_hit and not give_hit:
        logger.debug("分类丢弃: reason=registration_lure title=%r evidence=%s", normalized, [e.describe() for e in evidence])
        return BuiltinResult(category=None, matched=tuple(evidence), normalized_title=normalized)

    # 两个信号表都全量扫描: 命中明细要完整(主人调规则要看所有命中的关键词)
    free_hit = _scan(table.free_target.section, normalized, evidence) or give_hit
    paid_hit = _scan(table.paid_signal, normalized, evidence)

    category: str | None = None
    if free_hit:
        # 免费信号优先(2026-09-28 主人纠错): 撞付费信号也进白嫖类
        if _scan(table.token_context, normalized, evidence):
            category = table.token_context.target_category
        else:
            category = table.free_target.id
    else:
        for candidate in table.primary_categories:
            if _scan(candidate.section, normalized, evidence):
                category = candidate.id
                break
        if category is None and (paid_hit or _scan(table.paid_target.section, normalized, evidence)):
            category = table.paid_target.id
        if category is None:
            for candidate in table.fallback_categories:
                if _scan(candidate.section, normalized, evidence):
                    category = table.fallback_target
                    break

    logger.debug(
        "分类完成: category=%s title=%r evidence=%s",
        category,
        normalized,
        [e.describe() for e in evidence],
    )
    return BuiltinResult(category=category, matched=tuple(evidence), normalized_title=normalized)


def category_label(category_id: str, table: ClassifyTable | None = None) -> str | None:
    """Return the human-readable label (e.g. "🎁羊毛") for a category id."""
    if table is None:
        table = _default_table()
    return table.label_of(category_id)
