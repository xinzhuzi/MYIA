"""Make the in-repo src/ package importable without installing the project.

防回归注记(10-03-ci-gates D3):测试文件一律 `from conftest import …`,
勿改回 `from tests.conftest import …`——那种写法会让裸 `pytest`(区别于
`python -m pytest`,不把 CWD 放进 sys.path)假红。历史与双跑法证据见 718d56c。
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Shared engine-test helpers (PRD 10-01-v01-fetch-base / engine-l1-l2 /
# engine-firecrawl). All engine tests run on httpx.MockTransport and a
# FakeClock — never real network, never real waiting. pytest-asyncio is not a
# dependency, so tests drive coroutines with asyncio.run() directly.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from shishi.engines.fetch_base import FetchContext  # noqa: E402
from shishi.schema import SourceConfig  # noqa: E402
from shishi.store import SQLiteStore  # noqa: E402


def run(coro: Any) -> Any:
    """Run one coroutine to completion (asyncio.run alias for readability)."""
    return asyncio.run(coro)


@dataclass
class FakeClock:
    """Deterministic clock + sleep recorder: every wait is recorded, none real."""

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def silent_sleep(seconds: float) -> None:
    """Sleep that neither records nor advances time (isolate backoff from limiter)."""


def make_source(**overrides: Any) -> SourceConfig:
    """A minimal valid SourceConfig with keyword overrides (schema-validated)."""
    data: dict[str, Any] = {"name": "demo", "url": "https://example.com/list"}
    data.update(overrides)
    return SourceConfig.model_validate(data)


def make_raw_source(**overrides: Any) -> SourceConfig:
    """Build a SourceConfig without schema validation (pydantic model_construct).

    Historical seam: ``engine: firecrawl`` used to be missing from
    schema.ENGINES (fixed — it validates now), so keep using :func:`make_source`
    for everything that the schema accepts, including firecrawl.
    """
    from shishi.schema import RateLimitConfig

    fields: dict[str, Any] = {
        "name": "demo",
        "engine": "firecrawl",
        "url": "https://example.com/list",
        "method": "GET",
        "post_body": None,
        "headers": {},
        "pagination": None,
        "extract": None,
        "rate_limit": RateLimitConfig(),
        "proxy": "direct",
        "retry": 3,
    }
    fields.update(overrides)
    return SourceConfig.model_construct(**fields)


def make_handler(
    responder: Callable[[httpx.Request], httpx.Response],
    *,
    robots: str | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap a site responder with automatic robots.txt handling.

    ``robots=None`` answers 404 (fail-open, allowed); a string is served as
    the robots.txt body (rules like "User-agent: *\\nDisallow: /private/").
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if robots is None:
                return httpx.Response(404, text="")
            return httpx.Response(200, text=robots)
        return responder(request)

    return handler


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_context(
    client: httpx.AsyncClient,
    *,
    store: SQLiteStore | None = None,
    clock: FakeClock | None = None,
    silent_limiter: bool = False,
) -> tuple[FetchContext, FakeClock]:
    """Build a FetchContext on the injected client; returns (context, clock)."""
    clock = clock or FakeClock()
    limiters = None
    if silent_limiter:
        from shishi.engines.fetch_base import HostLimiterRegistry

        limiters = HostLimiterRegistry(clock=clock.time, sleeper=silent_sleep)
    context = FetchContext(
        client=client, store=store, clock=clock.time, sleep=clock.sleep, limiters=limiters
    )
    return context, clock


@pytest.fixture()
def engine_store(tmp_path):
    """A throwaway SQLiteStore per test (zero shared state between tests)."""
    store = SQLiteStore(tmp_path / "engines.db")
    yield store
    store.close()
