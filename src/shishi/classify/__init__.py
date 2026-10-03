"""Compatibility shim: the implementation moved to the standalone myia-classifier package.

The classifier ships as its own zero-dependency distribution (PyPI name
``myia-classifier``, import name :mod:`myia_classifier`); this module
re-exports its full public API so existing imports (``shishi.pipeline``,
``shishi.push.route``, plugins, tests) keep working unchanged. New code should
import from :mod:`myia_classifier` directly.
"""

from myia_classifier import (
    ALL_CATEGORIES,
    ALLOWED_FUNCTIONS,
    CATEGORY_CHANNEL,
    DEFAULT_TABLE_PATH,
    SEVEN_CATEGORIES,
    BuiltinResult,
    ClassifyDataError,
    ClassifyResult,
    ClassifyTable,
    MatchEvidence,
    Rule,
    RuleConfigError,
    RuleEvalError,
    RuleSyntaxError,
    category_label,
    classify_item,
    classify_title,
    evaluate_expression,
    load_rules,
    load_table,
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
