"""Pipeline orchestration: fetch -> classify -> dedup -> analyze -> enrich -> push.

Stage responsibilities (see README architecture):
- fetch:    engines/* resolve one fetch engine per source along the L1->L4
            degrade chain (L5/L6 opt-in)
- classify: keyword pre-filter (classify/builtin), zero token
- dedup:    URL/composite-key registry (dedup.DedupRegistry); never title
            fingerprints
- analyze:  watchlist relevance profile + threshold routing targets
            (push.route immediate/digest/archive)
- enrich:   optional LLM precision scoring with batch/cache/budget guardrails
- push:     channel dispatch (feishu_card / telegram / webhook)
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGES = ["fetch", "classify", "dedup", "analyze", "enrich", "push"]


@dataclass
class Item:
    """One intelligence unit flowing through the pipeline."""

    url: str
    title: str
    category: str | None = None
    scores: dict | None = None
    metadata: dict = field(default_factory=dict)


class Pipeline:
    """Runs the six stages for one category YAML."""

    def __init__(self, category_yaml: str) -> None:
        self.category_yaml = category_yaml

    async def run(self) -> list[Item]:
        """Execute fetch -> classify -> dedup -> analyze -> enrich -> push."""
        raise NotImplementedError("Pipeline.run is not implemented in the v0.1 skeleton yet")
