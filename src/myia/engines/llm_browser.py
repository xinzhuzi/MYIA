"""L6 engine: LLM-driven browser (skyvern) as the last-resort fallback.

Natural-language browser control for multi-step interactions and complex
forms. Token-hungry: only runs when explicitly configured per source and
never as part of the default degrade chain.
"""

from __future__ import annotations

from .fetch_base import BaseEngine

LAYER = "L6"


class LLMBrowserEngine(BaseEngine):
    async def fetch(self) -> list[dict]:
        raise NotImplementedError
