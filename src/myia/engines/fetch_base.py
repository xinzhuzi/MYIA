"""Shared fetch-engine base: rate limiting, retry, change fingerprint, robots.

Mirrors the source-level schema of the plugin spec: engine / url / method /
headers / pagination / extract / rate_limit / proxy / retry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class BaseEngine:
    """Common configuration and primitives shared by all fetch engines."""

    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    pagination: dict | None = None
    extract: dict | None = None
    rate_limit: dict | None = None
    proxy: str = "direct"
    retry: int = 3

    async def fetch(self) -> list[dict]:
        """Fetch all pages and return the extracted items as dicts."""
        raise NotImplementedError

    def content_fingerprint(
        self,
        body: bytes,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> str:
        """SHA-256 change baseline of an HTTP response (v1.7 primitive).

        etag/last_modified are mixed in when present so a response whose
        validators changed but body did not still yields a stable baseline.
        Sources whose fingerprint is unchanged never re-enter the pipeline;
        baselines live in the store (baselines table).
        """
        hasher = hashlib.sha256()
        if etag:
            hasher.update(etag.encode("utf-8"))
        if last_modified:
            hasher.update(last_modified.encode("utf-8"))
        hasher.update(body)
        return hasher.hexdigest()

    def respect_robots(self) -> bool:
        """Check robots.txt for the target URL (v0.2)."""
        raise NotImplementedError

    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff for 429/5xx: 2**attempt seconds, capped at 60s."""
        return min(60.0, float(2 ** max(0, attempt)))
