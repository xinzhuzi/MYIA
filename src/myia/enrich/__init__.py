"""LLM enrichment: precision scoring after the keyword pre-filter.

Cost guardrails (v1.7): batch scoring, per-URL result cache, and a token
budget per run. When the budget is exhausted, the remainder of the run
degrades to pure keyword filtering and the UI is notified.
"""

from __future__ import annotations


class LLMEnricher:
    """Scores items with an OpenAI-compatible endpoint.

    Defaults mirror the plugin `enrich:` schema.
    """

    model: str = "glm-4-flash"
    scores: tuple[str, ...] = ("value", "relevance", "credibility")
    batch: int = 20
    cache: bool = True
    budget_per_run: int = 50000

    async def enrich(self, items) -> list:
        """Score each item on value/relevance/credibility (0-10)."""
        raise NotImplementedError("LLMEnricher.enrich is not implemented in the v0.1 skeleton yet")
