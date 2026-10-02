"""Tests for the firecrawl engine (PRD 10-01-v01-engine-firecrawl).

Runs against a local httpx.MockTransport stand-in for ``POST /v1/scrape``:
request body (url/formats/timeout ms), Authorization header from env, default
self-host endpoint, endpoint via env: reference, plaintext api_key refusal,
markdown payload without extract, list extraction over returned html,
``success: false`` and missing-data structured errors, and the backend-down
failure path (retry budget exhausted, structured network error).
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import ValidationError

from myia.engines.fetch_base import FetchError, classify_exception
from myia.engines.firecrawl import FirecrawlEngine
from myia.schema import RateLimitConfig, SourceConfig

from conftest import make_client, make_context, make_handler, make_source, run

SCRAPE_OK_MARKDOWN = {
    "success": True,
    "data": {
        "markdown": "# 渲染后的标题\n\n- 条目一\n- 条目二",
        "metadata": {"title": "渲染后的标题"},
    },
}

SCRAPE_OK_HTML = {
    "success": True,
    "data": {
        "markdown": "# 备用",
        "html": (
            "<html><body><div class=\"item\"><a class=\"title\" href=\"/t/1\">JS 帖子一</a></div>"
            "<div class=\"item\"><a class=\"title\" href=\"/t/2\">JS 帖子二</a></div></body></html>"
        ),
    },
}


def scrape_source(**overrides):
    return make_source(url="https://js-heavy.example.com/hot", **overrides)


def firecrawl_handler(responder):
    def handler(request: httpx.Request) -> httpx.Response:
        # 目标站点的 robots.txt 由 make_handler 处理;/v1/scrape 走这里。
        return responder(request)

    return make_handler(handler)


def test_scrape_request_body_auth_and_default_endpoint(monkeypatch):
    monkeypatch.setenv("MYIA_FIRECRAWL_API_KEY", "fc-secret")
    captured: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=SCRAPE_OK_MARKDOWN)

    client = make_client(firecrawl_handler(responder))
    context, _ = make_context(client)
    engine = FirecrawlEngine(scrape_source(), context)

    items = run(engine.fetch())
    assert captured["url"] == "http://127.0.0.1:3002/v1/scrape"  # 自建默认端点
    assert captured["auth"] == "Bearer fc-secret"  # key 来自 env,禁明文
    assert captured["body"] == {
        "url": "https://js-heavy.example.com/hot",
        "formats": ["markdown"],
        "timeout": 60000,  # 默认 60s,API 侧毫秒
    }
    assert items == [
        {
            "url": "https://js-heavy.example.com/hot",
            "title": "渲染后的标题",
            "content": "# 渲染后的标题\n\n- 条目一\n- 条目二",
        }
    ]


def test_endpoint_via_env_reference(monkeypatch):
    monkeypatch.delenv("MYIA_FIRECRAWL_URL", raising=False)
    monkeypatch.setenv("MYIA_FC_ENDPOINT", "http://127.0.0.1:9402")
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=SCRAPE_OK_MARKDOWN)

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(
        engine_options={"firecrawl": {"endpoint": "env:MYIA_FC_ENDPOINT"}},
    )
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    run(engine.fetch())
    assert seen == ["http://127.0.0.1:9402/v1/scrape"]


def test_plaintext_endpoint_refused():
    source = scrape_source(engine_options={"firecrawl": {"endpoint": "http://10.0.0.8:3002"}})
    client = make_client(firecrawl_handler(lambda r: pytest.fail("不应发起任何请求")))
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "credentials_plaintext"


def test_plaintext_api_key_refused():
    """明文 api_key 加载期即被 schema 拒绝(安全基线:启动即拒跑)。"""
    with pytest.raises(ValidationError) as excinfo:
        scrape_source(engine_options={"firecrawl": {"api_key": "fc-plain-key"}})
    (error,) = excinfo.value.errors()
    assert error["ctx"]["error"].code == "credential_plaintext"


def test_plaintext_api_key_refused_at_engine_layer():
    """纵深防御:绕过 schema 构造(如直连引擎)时引擎仍拒绝明文并结构化上报。"""
    source = SourceConfig.model_construct(
        name="demo",
        engine="firecrawl",
        url="https://js-heavy.example.com/hot",
        method="GET",
        headers={},
        rate_limit=RateLimitConfig(),
        proxy="direct",
        retry=3,
        engine_options={"firecrawl": {"api_key": "fc-plain-key"}},
    )
    client = make_client(firecrawl_handler(lambda r: pytest.fail("不应发起任何请求")))
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "credentials_plaintext"


def test_keychain_api_key_with_noncanonical_name_raises():
    """keychain: 引用已实装(v0.2);非规范扁平名解析前即被结构化拒绝。"""
    source = scrape_source(engine_options={"firecrawl": {"api_key": "keychain:firecrawl_key"}})
    client = make_client(firecrawl_handler(lambda r: pytest.fail("不应发起任何请求")))
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    from myia.schema import CredentialResolveError

    with pytest.raises(CredentialResolveError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.code == "invalid_secret_name"


def test_extract_over_returned_html_adds_html_format():
    captured: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["formats"] = json.loads(request.content)["formats"]
        return httpx.Response(200, json=SCRAPE_OK_HTML)

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(
        extract={
            "type": "list",
            "item": "div.item",
            "fields": {"title": "a.title", "url": "a.title@href"},
        },
    )
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    items = run(engine.fetch())
    assert "html" in captured["formats"]  # extract 需要时自动追加 html 格式
    assert items == [
        {"title": "JS 帖子一", "url": "https://js-heavy.example.com/t/1"},
        {"title": "JS 帖子二", "url": "https://js-heavy.example.com/t/2"},
    ]


def test_success_false_is_structured_error():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "target site blocked us"})

    client = make_client(firecrawl_handler(responder))
    context, _ = make_context(client)
    engine = FirecrawlEngine(scrape_source(), context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "firecrawl_error"
    assert "blocked us" in str(excinfo.value)


def test_missing_data_section_is_structured_error():
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True})

    client = make_client(firecrawl_handler(responder))
    context, _ = make_context(client)
    engine = FirecrawlEngine(scrape_source(), context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "firecrawl_error"


def test_backend_down_retries_then_structured_network_error():
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("connection refused (self-host down)")

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(retry=2)
    context, clock = make_context(client)
    engine = FirecrawlEngine(source, context)

    with pytest.raises(httpx.ConnectError):
        run(engine.fetch())
    assert calls["count"] == 3  # 1 + retry 2
    assert clock.sleeps == [1.0, 2.0]
    assert classify_exception(httpx.ConnectError("x")) == "network"


def test_backend_500_exhausts_retries_and_raises():
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, text="overloaded")

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(retry=1)
    context, clock = make_context(client)
    engine = FirecrawlEngine(source, context)

    with pytest.raises(httpx.HTTPStatusError):
        run(engine.fetch())
    assert calls["count"] == 2
    assert clock.sleeps == [1.0]


def test_timeout_option_reaches_body_and_httpx():
    captured: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["extensions"] = request.extensions
        return httpx.Response(200, json=SCRAPE_OK_MARKDOWN)

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(engine_options={"firecrawl": {"timeout": 15}})
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    run(engine.fetch())
    assert captured["body"]["timeout"] == 15000
    # HTTP 客户端超时 = max(管线默认 30s, 后端预算 15s) —— 客户端永不早于后端预算掐断
    assert captured["extensions"]["timeout"] == {
        "connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0,
    }


def test_timeout_option_widens_httpx_client_timeout():
    """配置 timeout=60 > 管线默认 30s:HTTP 客户端等满后端预算。

    Regression: the client was hardcoded to context.timeout (30s), so the
    part of a configured firecrawl timeout beyond 30s never took effect —
    the default 60s backend budget was silently cut at 30s.
    """
    captured: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["extensions"] = request.extensions
        return httpx.Response(200, json=SCRAPE_OK_MARKDOWN)

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(engine_options={"firecrawl": {"timeout": 60}})
    context, _ = make_context(client)
    engine = FirecrawlEngine(source, context)

    run(engine.fetch())
    assert captured["extensions"]["timeout"]["read"] == 60.0


def test_timeout_failure_path_retries_then_raises_classified():
    """超时失败路径:ReadTimeout 按网络类重试,耗尽后结构化抛出(超时分类)。"""
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("read timed out")

    client = make_client(firecrawl_handler(responder))
    source = scrape_source(retry=2)
    context, clock = make_context(client)
    engine = FirecrawlEngine(source, context)

    with pytest.raises(httpx.ReadTimeout):
        run(engine.fetch())
    assert calls["count"] == 3  # 1 + retry 2
    assert clock.sleeps == [1.0, 2.0]
    assert classify_exception(httpx.ReadTimeout("x")) == "timeout"


# ---------------------------------------------------------------------------
# 真实自建端点 smoke:本地有服务(设 MYIA_FIRECRAWL_URL)才跑,CI 不依赖。
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("MYIA_FIRECRAWL_URL"),
    reason="真实自建端点 smoke:仅本地存在 firecrawl 服务时执行(设 MYIA_FIRECRAWL_URL)",
)
def test_smoke_selfhost_endpoint_scrapes_example_com():
    from conftest import make_context as _mc

    async def scenario():
        client = httpx.AsyncClient(timeout=120.0)
        context, _ = _mc(client)
        engine = FirecrawlEngine(make_source(url="https://example.com"), context)
        try:
            return await engine.fetch()
        finally:
            await client.aclose()

    items = run(scenario())
    assert items and items[0]["content"]  # markdown 载荷非空
