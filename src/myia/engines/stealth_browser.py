"""L5 engine: anti-detection browser via invisible_playwright_mcp.

Will drive the MCP server through an embedded MCP client (planned, v0.4):
real-browser fingerprint to pass anti-bot walls and captchas, cookie
injection for login-only sources.
"""

from __future__ import annotations

from .fetch_base import BaseEngine

LAYER = "L5"


class StealthBrowserEngine(BaseEngine):
    async def fetch(self) -> list[dict]:
        raise NotImplementedError
