"""L4 engine: adaptive anti-bot crawling via Scrapling (self-healing selectors)."""

from __future__ import annotations

from .fetch_base import BaseEngine

try:
    from scrapling import Fetcher  # noqa: F401

    HAS_SCRAPLING = True
except ImportError:  # pragma: no cover - optional dependency
    Fetcher = None
    HAS_SCRAPLING = False

LAYER = "L4"


class ScraplingEngine(BaseEngine):
    """Adaptive element location that survives site redesigns; passes
    basic Cloudflare shields with stealth fingerprints."""

    async def fetch(self) -> list[dict]:
        if not HAS_SCRAPLING:
            raise RuntimeError("scrapling is not installed; pip install myia[scrapling]")
        raise NotImplementedError
