"""Fetch engine layer: the L1 API-direct -> L6 LLM-browser degrade chain."""

from .registry import ENGINE_REGISTRY, resolve_engine

__all__ = ["ENGINE_REGISTRY", "resolve_engine"]
