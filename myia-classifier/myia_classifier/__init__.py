"""myia-classifier: standalone seven-category keyword classifier.

Zero-dependency classification engine extracted from MYIA's classification
layer. Public entry point is :func:`classify_item`: it runs the built-in
dual-signal adjudication (seven categories + channel) over the item title
and applies optional custom rules (safe ``when`` expressions) on top,
returning ``category`` + ``tags`` + the hit trace for debugging.

The keyword tables live in the packaged ``data/keywords.json`` so the owner
or an AI can tune them without code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from myia_classifier.builtin import (
    ALL_CATEGORIES,
    CATEGORY_CHANNEL,
    DEFAULT_TABLE_PATH,
    SEVEN_CATEGORIES,
    BuiltinResult,
    ClassifyDataError,
    ClassifyTable,
    MatchEvidence,
    category_label,
    classify_title,
    load_table,
)
from myia_classifier.custom import (
    ALLOWED_FUNCTIONS,
    Rule,
    RuleConfigError,
    RuleEvalError,
    RuleSyntaxError,
    evaluate_expression,
    load_rules,
    rules_from_config,
)

__all__ = [
    "ALL_CATEGORIES",
    "ALLOWED_FUNCTIONS",
    "CATEGORY_CHANNEL",
    "DEFAULT_TABLE_PATH",
    "SEVEN_CATEGORIES",
    "BuiltinResult",
    "ClassifyDataError",
    "ClassifyResult",
    "ClassifyTable",
    "MatchEvidence",
    "Rule",
    "RuleConfigError",
    "RuleEvalError",
    "RuleSyntaxError",
    "category_label",
    "classify_item",
    "classify_title",
    "evaluate_expression",
    "load_rules",
    "load_table",
    "rules_from_config",
]

__version__ = "0.0.1"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassifyResult:
    """Full classification outcome: built-in category + custom rule tags + trace."""

    category: str | None  # category id, or None when the title is discarded
    tags: list[str]  # tags from fired custom rules (falls back to rule name)
    matched: tuple[MatchEvidence, ...]  # which keyword/pattern hit where
    normalized_title: str


def _item_title(item: Mapping[str, Any] | object) -> Any:
    """Read the title; raise TypeError when the field is absent or non-str (fail fast)."""
    if isinstance(item, Mapping):
        if "title" not in item:
            raise TypeError("分类条目缺少 title 字段")
        return item["title"]
    if not hasattr(item, "title"):
        raise TypeError("分类条目缺少 title 属性")
    return item.title


def classify_item(
    item: Mapping[str, Any] | object,
    *,
    table: ClassifyTable | None = None,
    rules: Iterable[Rule] | None = None,
) -> ClassifyResult:
    """Classify one item: built-in keyword funnel, then custom rules.

    The built-in stage reads only ``item.title`` (production semantics:
    classification never looks at the URL). Custom rules may read any item
    field, e.g. ``abs(change_pct) >= 3``.

    Args:
        item: dict-like item or object with a ``title`` attribute.
        table: preloaded keyword table; defaults to the packaged
            ``data/keywords.json``.
        rules: optional custom rules (see :mod:`myia_classifier.custom`).

    Returns:
        ClassifyResult with ``category`` (or ``None`` for dead/noise/
        registration-lure/no-signal titles), fired ``tags`` and the hit
        trace. A failing rule never breaks the batch (WARNING + skipped).

    Raises:
        TypeError: ``item.title`` is missing or not a string.
        ClassifyDataError: keyword table malformed (fail fast at startup).
    """
    title = _item_title(item)
    if not isinstance(title, str):
        raise TypeError(f"分类条目缺少字符串 title 字段, got {type(title).__name__}")
    builtin_result = classify_title(title, table)
    tags: list[str] = []
    for rule in rules or ():
        if rule.evaluate(item):
            tags.append(rule.tag or rule.name)
    logger.debug(
        "分类条目完成: category=%s tags=%s title=%r",
        builtin_result.category,
        tags,
        builtin_result.normalized_title,
    )
    return ClassifyResult(
        category=builtin_result.category,
        tags=tags,
        matched=builtin_result.matched,
        normalized_title=builtin_result.normalized_title,
    )
