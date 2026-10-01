"""L2 engine: server-rendered static HTML via httpx + selectolax."""

from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from .fetch_base import BaseEngine

LAYER = "L2"


class StaticHTMLEngine(BaseEngine):
    """Fetch static pages and apply the CSS `extract` selectors."""

    async def fetch(self) -> list[dict]:
        # TODO: decode non-UTF8 responses (gb18030 fallback), walk pagination
        # (template/selector/scroll modes), apply extract.item +
        # extract.fields, record content fingerprint.
        raise NotImplementedError
