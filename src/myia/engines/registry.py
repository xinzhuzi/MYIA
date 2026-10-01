"""Engine registry and auto-degrade orchestration.

Default degrade chain: L1 direct_api -> L2 static_html -> L3 crawl4ai ->
L4 scrapling. L5 stealth_browser and L6 llm_browser are excluded by default:
they drive real browsers and burn tokens, so they are opt-in per source.
firecrawl is an explicit-choice re-render backend (cloud or self-hosted),
not part of the default chain.

All imports are lazy so optional dependencies stay optional.
"""

from __future__ import annotations

from typing import Callable

from .fetch_base import BaseEngine

# name -> zero-arg factory returning the engine class.
ENGINE_REGISTRY: dict[str, Callable[[], type[BaseEngine]]] = {
    "direct_api": lambda: _load("direct_api", "DirectAPIEngine"),
    "static_html": lambda: _load("static_html", "StaticHTMLEngine"),
    "crawl4ai": lambda: _load("crawl4ai", "Crawl4AIEngine"),
    "scrapling": lambda: _load("scrapling", "ScraplingEngine"),
    "stealth_browser": lambda: _load("stealth_browser", "StealthBrowserEngine"),
    "llm_browser": lambda: _load("llm_browser", "LLMBrowserEngine"),
    "firecrawl": lambda: _load("firecrawl", "FirecrawlEngine"),
}


def _load(module_name: str, class_name: str) -> type[BaseEngine]:
    from importlib import import_module

    module = import_module(f".{module_name}", __package__)
    return getattr(module, class_name)


def resolve_engine(name: str) -> type[BaseEngine]:
    """Return the engine class registered under `name` (lazy import)."""
    try:
        factory = ENGINE_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; known: {sorted(ENGINE_REGISTRY)}") from None
    return factory()


def auto_degrade(preferred: str) -> list[str]:
    """Ordered fallback chain starting at `preferred`.

    Returns the L1 -> L2 -> L3 -> L4 slice starting at the preferred engine.
    L5/L6 are excluded from the default chain (browser engines, token cost,
    opt-in per source); explicit-choice engines (stealth_browser, llm_browser,
    firecrawl) degrade nowhere. The first engine that succeeds is written
    back to the source config (v0.2).
    """
    default_chain = ["direct_api", "static_html", "crawl4ai", "scrapling"]
    if preferred in default_chain:
        return default_chain[default_chain.index(preferred):]
    if preferred in ("stealth_browser", "llm_browser", "firecrawl"):
        return [preferred]
    raise KeyError(f"unknown engine {preferred!r}")
