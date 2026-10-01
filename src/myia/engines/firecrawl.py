"""Firecrawl engine for heavily-rendered sources (cloud or self-hosted).

Reads MYIA_FIRECRAWL_API_KEY / MYIA_FIRECRAWL_URL at call time (never at
import time) so the module stays importable without credentials and works
with either the Firecrawl cloud or a self-hosted endpoint.
"""

from __future__ import annotations

import os

from .fetch_base import BaseEngine

LAYER = "L3-alt"


class FirecrawlEngine(BaseEngine):
    async def fetch(self) -> list[dict]:
        api_key = os.environ.get("MYIA_FIRECRAWL_API_KEY", "")
        base_url = os.environ.get("MYIA_FIRECRAWL_URL", "")
        if not api_key and not base_url:
            raise RuntimeError(
                "Firecrawl needs MYIA_FIRECRAWL_API_KEY (cloud) or "
                "MYIA_FIRECRAWL_URL (self-hosted)"
            )
        raise NotImplementedError
