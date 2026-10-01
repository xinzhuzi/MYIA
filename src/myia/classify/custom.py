"""User-defined classification rules loaded from a plugin YAML `classify:` section."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Rule:
    """One custom rule: `name`, a `when` expression over item fields, and a `tag`."""

    name: str
    when: str
    tag: str = ""

    def evaluate(self, item) -> bool:
        # TODO: restricted evaluator for the `when` expression
        # (e.g. "abs(change_pct) >= 3") — never raw eval().
        raise NotImplementedError


def load_rules(yaml_path: str) -> list[Rule]:
    """Load the `classify.rules` entries from a plugin YAML."""
    import yaml  # deferred: keeps this module importable without PyYAML

    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return [
        Rule(
            name=str(rule.get("name", "")),
            when=str(rule.get("when", "")),
            tag=str(rule.get("tag", "")),
        )
        for rule in (data.get("classify") or {}).get("rules", [])
    ]
