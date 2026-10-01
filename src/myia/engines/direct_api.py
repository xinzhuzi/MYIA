"""L1 engine: direct JSON/REST API calls via httpx (fastest, zero cost)."""

from __future__ import annotations

import httpx

from .fetch_base import BaseEngine

LAYER = "L1"


class DirectAPIEngine(BaseEngine):
    """Fetch public JSON APIs; supports {placeholder} URL templates."""

    async def fetch(self) -> list[dict]:
        # TODO: expand URL templates per placeholder/page, send with
        # self.headers, rotate API key pools on 401/429, apply json_path
        # extract, record content fingerprint.
        raise NotImplementedError
