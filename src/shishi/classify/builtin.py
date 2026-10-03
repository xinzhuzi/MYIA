"""Compatibility shim: implementation lives in ``myia_classifier.builtin``.

See :mod:`shishi.classify` (the package shim) for the migration note.
``DEFAULT_TABLE_PATH`` points at the packaged table inside the standalone
distribution, so data/code separation works identically through this shim.
"""

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
