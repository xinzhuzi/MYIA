"""L3 engine: LLM-oriented crawling via crawl4ai (default JS-render engine)."""

from __future__ import annotations

from .fetch_base import BaseEngine

try:
    from crawl4ai import AsyncWebCrawler  # noqa: F401

    HAS_CRAWL4AI = True
except ImportError:  # pragma: no cover - optional dependency
    AsyncWebCrawler = None
    HAS_CRAWL4AI = False

LAYER = "L3"


class Crawl4AIEngine(BaseEngine):
    """Render JS-heavy pages into clean text/JSON; auto-structures list
    pages when the source has no `extract` section."""

    async def fetch(self) -> list[dict]:
        if not HAS_CRAWL4AI:
            raise RuntimeError("crawl4ai is not installed; pip install myia[crawl4ai]")
        raise NotImplementedError
