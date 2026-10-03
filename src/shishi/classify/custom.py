"""Compatibility shim: implementation lives in ``myia_classifier.custom``.

See :mod:`shishi.classify` (the package shim) for the migration note.

``_BIN_OPS`` is re-exported on purpose (private, not in ``__all__``): tests
monkeypatch the operator table via ``monkeypatch.setitem`` on this module's
reference, which must stay the *same dict object* the implementation reads.
"""

from myia_classifier.custom import (
    ALLOWED_FUNCTIONS,
    Rule,
    RuleConfigError,
    RuleEvalError,
    RuleSyntaxError,
    _BIN_OPS,
    evaluate_expression,
    load_rules,
    rules_from_config,
)

__all__ = [
    "ALLOWED_FUNCTIONS",
    "Rule",
    "RuleConfigError",
    "RuleEvalError",
    "RuleSyntaxError",
    "evaluate_expression",
    "load_rules",
    "rules_from_config",
]
