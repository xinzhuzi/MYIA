"""Tests for the engine registry & auto-degrade (PRD 10-01-v01-engine-l1-l2,
10-01-v01-engine-firecrawl, 10-01-v02-engine-crawl4ai, 10-01-v03-engine-scrapling,
10-01-v04-engine-stealth, 10-01-v04-engine-llm-browser).

Covers: auto chain order (链终形态 L1→L2→crawl4ai→firecrawl→scrapling→
stealth_browser→llm_browser, yaml-schema rule 5) / ENGINE_SCHEDULED_VERSIONS
已清空,结构化 not-available 分支以合成排期名保持覆盖 / L2 failure falls
through crawl4ai (dependency missing here) to firecrawl / hint priority /
hint invalidation with canonical-order fallback / hint write-back on success /
hint outside the chain ignored / unchanged skip surfaced in FetchOutcome /
structured failure records when every engine fails.

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock.
Tests that traverse the crawl4ai / scrapling legs pin
``sys.modules["crawl4ai"] = None`` / ``sys.modules["scrapling.fetchers"] = None``
(same deterministic technique as test_crawl4ai.py / test_scrapling.py) so the
dependency-missing branch is exercised whether or not the optional packages
are installed. stealth_browser / llm_browser legs stay deterministic without
touching real MCP/skyvern backends (fake fail engine / whitelist guard —
same technique as test_stealth.py / test_llm_browser.py).
"""

from __future__ import annotations

import sys

import httpx
import pytest

from myia.engines import registry
from myia.engines.fetch_base import (
    BaseEngine,
    EngineNotAvailableError,
    FetchError,
    load_proxy_pools,
)
from myia.engines.registry import FetchOutcome, auto_degrade, fetch_source, resolve_engine
from myia.engines.direct_api import DirectAPIEngine
from myia.engines.static_html import StaticHTMLEngine
from myia.engines.firecrawl import FirecrawlEngine
from myia.schema import SourceConfig

from conftest import make_client, make_context, make_handler, make_raw_source, make_source, run


def make_fail_engine(name: str, error_type: str) -> type[BaseEngine]:
    """Stand-in engine failing with a distinct structured error class
    (same deterministic technique as test_stealth.py / test_llm_browser.py —
    keeps browser legs out of real MCP/skyvern backends)."""

    class FailEngine(BaseEngine):
        LAYER = "fail"
        ENGINE_NAME = name
        REQUIRES_EXTRACT = False
        SUPPORTED_EXTRACT_TYPES = ("list", "item", "json_path")

        async def _fetch_impl(self) -> list[dict]:
            raise FetchError(f"{name} 挂了", error_type=error_type)

    return FailEngine

SITE_URL = "https://example.com/list"
FIRECRAWL_SCRAPE = "http://127.0.0.1:3002/v1/scrape"

SITE_HTML = (
    "<html><body>"
    "<div class=\"item\"><a class=\"title\" href=\"/t/1\">静态帖子一</a></div>"
    "<div class=\"item\"><a class=\"title\" href=\"/t/2\">静态帖子二</a></div>"
    "</body></html>"
)

LIST_EXTRACT = {
    "type": "list",
    "item": "div.item",
    "fields": {"title": "a.title", "url": "a.title@href"},
}

JSON_PAYLOAD = {
    "data": [
        {"title": "接口条目一", "url": "https://example.com/api/1"},
        {"title": "接口条目二", "url": "https://example.com/api/2"},
    ]
}

JSON_FIELDS = {"title": "$.data[*].title", "url": "$.data[*].url"}


def test_auto_degrade_chain_order():
    """链终形态链序(yaml-schema rule 5 / PRD 10-01-v04-engine-llm-browser):
    L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser→llm_browser。"""
    assert auto_degrade("auto") == [
        "direct_api", "static_html", "crawl4ai", "firecrawl", "scrapling",
        "stealth_browser", "llm_browser",
    ]
    assert auto_degrade("direct_api") == [
        "direct_api", "static_html", "crawl4ai", "firecrawl", "scrapling",
        "stealth_browser", "llm_browser",
    ]
    assert auto_degrade("static_html") == [
        "static_html", "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    ]
    assert auto_degrade("crawl4ai") == [
        "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    ]
    assert auto_degrade("firecrawl") == ["firecrawl", "scrapling", "stealth_browser", "llm_browser"]
    assert auto_degrade("scrapling") == ["scrapling", "stealth_browser", "llm_browser"]
    assert auto_degrade("stealth_browser") == ["stealth_browser", "llm_browser"]
    assert auto_degrade("llm_browser") == ["llm_browser"]  # 链尾不再降级(烧 token 兜底层)


def test_scheduled_engine_raises_structured_not_available(monkeypatch):
    """链终形态:全部 schema 引擎已实装(ENGINE_SCHEDULED_VERSIONS 清空),
    显式 stealth_browser / llm_browser 是合法链成员;结构化 not-available
    分支以合成排期名保持覆盖(未来新增排期引擎仍走此路径)。"""
    assert registry.ENGINE_SCHEDULED_VERSIONS == {}
    assert auto_degrade("stealth_browser") == ["stealth_browser", "llm_browser"]
    assert auto_degrade("llm_browser") == ["llm_browser"]
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    with pytest.raises(EngineNotAvailableError) as excinfo:
        auto_degrade("time_machine")
    assert excinfo.value.error_type == "engine_not_available"
    assert excinfo.value.scheduled_version == "v9.9"
    assert excinfo.value.engine == "time_machine"


def test_unknown_engine_raises_keyerror():
    with pytest.raises(KeyError):
        auto_degrade("no_such_engine")
    with pytest.raises(KeyError):
        resolve_engine("no_such_engine")


def test_registry_resolves_v01_engine_classes():
    assert resolve_engine("direct_api") is DirectAPIEngine
    assert resolve_engine("static_html") is StaticHTMLEngine
    assert resolve_engine("firecrawl") is FirecrawlEngine


def test_auto_l2_failure_falls_to_firecrawl(monkeypatch):
    """降级链单测:L2 失败 → crawl4ai 依赖缺失(固定 sys.modules 走确定性分支)
    → 按 v0.2 链序继续落 firecrawl 成功。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    site_calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "x",
                        "html": "<html><body>"
                        "<div class=\"item\"><a class=\"title\" href=\"/t/9\">JS 帖子</a></div>"
                        "</body></html>",
                    },
                },
            )
        site_calls["count"] += 1
        return httpx.Response(500, text="waf blocked")  # L1 extract 不支持无请求;L2 被拒

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", extract=LIST_EXTRACT)
    context, _ = make_context(client)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "firecrawl"
    assert len(outcome.items) == 1
    assert outcome.items[0]["title"] == "JS 帖子"
    assert [failure.error_type for failure in outcome.failures] == [
        "extract_unsupported",
        "http_500",
        "dependency_missing",  # crawl4ai 依赖缺失,链继续降级
    ]
    assert site_calls["count"] == 4  # L2 的 500 走满 retry=3 退避;crawl4ai 零站点请求
    assert any("pip install myia[crawl4ai]" in f.message for f in outcome.failures)


def test_hint_tried_first_when_in_chain(engine_store):
    """hint 优先:canonical 首位 direct_api 会被 list extract 拒掉(留下失败记录),
    空 failures + 首个请求即成功 证明 static_html 被 hint 提到链首先试。"""
    engine_store.set_engine_hint(SITE_URL, "static_html")
    site_calls: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        site_calls.append(str(request.url))
        return httpx.Response(200, text=SITE_HTML, headers={"content-type": "text/html"})

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "static_html"
    assert outcome.failures == []  # hint 先行:L1 从未被尝试(extract_unsupported 未出现)
    assert site_calls == [SITE_URL]
    assert engine_store.get_engine_hint(SITE_URL) == "static_html"  # 成功后回写


def test_hint_invalidated_falls_back_in_canonical_order(monkeypatch, engine_store):
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    engine_store.set_engine_hint(SITE_URL, "static_html")

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "html": "<html><body>"
                        "<div class=\"item\"><a class=\"title\" href=\"/t/7\">JS 兜底</a></div>"
                        "</body></html>"
                    },
                },
            )
        return httpx.Response(500, text="down")  # hint 引擎失效

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "firecrawl"  # L2 hint 失效 -> L1 extract 不支持 -> crawl4ai 依赖缺失 -> firecrawl
    assert engine_store.get_engine_hint(SITE_URL) == "firecrawl"  # 失效清除后由成功者重写
    assert [failure.error_type for failure in outcome.failures] == [
        "http_500",
        "extract_unsupported",
        "dependency_missing",
    ]


def test_hint_failure_cleared_when_all_engines_fail(monkeypatch, engine_store):
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)  # 强制依赖缺失分支
    # stealth_browser 假引擎:确定性失败,单测不真拉 MCP 浏览器进程(同 test_stealth.py)
    monkeypatch.setitem(
        registry.ENGINE_REGISTRY,
        "stealth_browser",
        lambda: make_fail_engine("stealth_browser", "mcp_server_missing"),
    )
    engine_store.set_engine_hint(SITE_URL, "static_html")

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(503, text="firecrawl down")
        return httpx.Response(500, text="site down")

    client = make_client(make_handler(responder))
    source = make_source(engine="static_html", url=SITE_URL, extract=LIST_EXTRACT, retry=0)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))
    assert outcome.engine is None
    assert outcome.items == []
    assert engine_store.get_engine_hint(SITE_URL) is None  # 失效提示已清除
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("static_html", "http_500"),
        ("crawl4ai", "dependency_missing"),  # 依赖缺失也记结构化失败
        ("firecrawl", "http_503"),
        ("scrapling", "dependency_missing"),
        ("stealth_browser", "mcp_server_missing"),  # L5 假引擎结构化失败
        ("llm_browser", "engine_not_whitelisted"),  # L6 白名单硬护栏:非 auto/显式源拒载
    ]
    assert outcome.failures[0].source == "demo"
    assert outcome.failures[0].url == SITE_URL


def test_hint_written_on_first_success(engine_store):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=JSON_PAYLOAD)

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract={"type": "json_path", "fields": JSON_FIELDS})
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "direct_api"  # 无 hint 时按链序 L1 优先
    assert engine_store.get_engine_hint(SITE_URL) == "direct_api"
    assert engine_store.get_engine_hint("https://other.example/") is None  # 不误写别的源


def test_hint_outside_chain_is_ignored(monkeypatch, engine_store):
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    engine_store.set_engine_hint(SITE_URL, "time_machine")  # 链外(排期中,未实装)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=JSON_PAYLOAD)

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract={"type": "json_path", "fields": JSON_FIELDS})
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "direct_api"  # 链外 hint 不改变顺序


def test_scheduled_engine_selection_recorded_as_failure(monkeypatch):
    """显式选择排期中引擎:链构建即拒绝,结构化失败记录,零网络。
    (链终形态无排期引擎;schema 的 engine Literal 也已在校验层拦住未知名,
    故用 model_construct 绕过校验 + 合成排期名保持该 fetch_source 路径覆盖。)"""
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    source = SourceConfig.model_construct(name="demo", url=SITE_URL, engine="time_machine")
    requests: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=SITE_HTML)

    client = make_client(make_handler(responder))
    context, _ = make_context(client)

    outcome = run(fetch_source(source, context))
    assert outcome.engine is None
    assert requests == []  # 引擎未实装,链构建即拒绝,零网络请求
    assert len(outcome.failures) == 1
    assert outcome.failures[0].error_type == "engine_not_available"
    assert "v9.9" in outcome.failures[0].message


def test_auto_scroll_source_degrades_past_l2_to_scrapling(monkeypatch, engine_store):
    """scroll 源走 auto:L2 不再吞掉滚动页首屏「成功」——非 L4 层结构化拒绝
    (scroll_unsupported),链正确降级到 L4 假引擎成功并回写 hint。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "scrapling", _ok_scrapling_factory)
    engine_store.set_engine_hint(SITE_URL, "static_html")  # 旧 hint:验证被滚动拒绝清掉

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SITE_HTML)  # L2 单页会「成功」的响应

    client = make_client(make_handler(responder))
    source = make_source(
        engine="auto",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        pagination={"mode": "scroll", "max_pages": 3},
    )
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine == "scrapling"
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("static_html", "scroll_unsupported"),  # hint 引擎被滚动语义拒绝,hint 清除
        ("direct_api", "scroll_unsupported"),
        ("crawl4ai", "scroll_unsupported"),  # 依赖检查之前即拒(scroll 是 L4 专属语义)
        ("firecrawl", "scroll_unsupported"),
    ]
    assert engine_store.get_engine_hint(SITE_URL) == "scrapling"


def _ok_scrapling_factory():
    """滚动页假 L4 工厂:证明链真的降级到了 scrapling 而非 L2 单页假成功。"""
    from myia.engines.scrapling import ScraplingEngine

    class FakeScrollScrapling(ScraplingEngine):
        async def _fetch_impl(self) -> list[dict]:
            return [{"title": "滚动帖子", "url": "https://example.com/t/scroll-1"}]

    return FakeScrollScrapling


def test_unchanged_skip_surfaced_in_outcome(engine_store):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=JSON_PAYLOAD)

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract={"type": "json_path", "fields": JSON_FIELDS})
    context, _ = make_context(client, store=engine_store)

    first = run(fetch_source(source, context))
    assert first.engine == "direct_api"
    assert len(first.items) == 2
    assert first.skipped is False

    second = run(fetch_source(source, context))
    assert second.engine == "direct_api"  # 引擎成功(传输层),hint 保持
    assert second.items == []
    assert second.skipped is True
    assert second.skip_reason == "hash_match"  # 变更指纹 skip 可见


def test_explicit_firecrawl_source_runs_alone():
    direct_hits: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.host != "127.0.0.1":
            direct_hits.append(str(request.url))
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"markdown": "# 内容", "metadata": {"title": "内容"}},
            },
        )

    client = make_client(make_handler(responder))
    # engine: firecrawl 还不在 schema.ENGINES 词汇表内(跨模块缺口,见 openIssues),
    # 这里用未校验构造直测 registry 的显式选择路径。
    source = make_raw_source(engine="firecrawl", url=SITE_URL)
    context, _ = make_context(client)

    outcome = run(fetch_source(source, context))
    assert outcome.engine == "firecrawl"
    assert outcome.items[0]["content"] == "# 内容"
    assert direct_hits == []  # explicit firecrawl 不应直接抓目标站


def test_fetch_outcome_defaults():
    outcome = FetchOutcome(source="demo")
    assert outcome.engine is None
    assert outcome.items == []
    assert outcome.skipped is False
    assert outcome.failures == []


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:proxy_* 分类消费(代理挂 ≠ 源死)
# ---------------------------------------------------------------------------


def test_proxy_failure_aborts_chain_and_keeps_hint(monkeypatch, engine_store):
    """回归:proxy_* 失败 ①中止降级链(其余引擎骑同一池 transport,必然同错,
    不该重复烧完 retry+退避);②不误清 hint(引擎选择没错,是出口断了)。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    engine_store.set_engine_hint(SITE_URL, "static_html")
    firecrawl_calls = {"count": 0}

    def firecrawl_factory():
        firecrawl_calls["count"] += 1
        return FirecrawlEngine

    monkeypatch.setitem(registry.ENGINE_REGISTRY, "firecrawl", firecrawl_factory)

    data_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        data_calls["count"] += 1
        raise httpx.ConnectError("connection refused via proxy", request=request)

    def origin_responder(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("direct 传输层不应收到任何请求")

    client = make_client(origin_responder)
    context, _ = make_context(client, store=engine_store)
    context.proxy_pools = load_proxy_pools({"pools": {"main": "http://10.0.0.1:8080"}})
    real_client = httpx.AsyncClient

    def pool_client_factory(**kwargs):
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 互斥
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_client(**kwargs)

    monkeypatch.setattr("myia.engines.fetch_base.httpx.AsyncClient", pool_client_factory)

    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT, retry=0, proxy="pool:main")
    outcome = run(fetch_source(source, context))

    assert outcome.engine is None
    # hint(static_html)先行,骑池 → proxy_network → 链立即短路:
    # direct_api / crawl4ai(依赖缺失)/ firecrawl 都不再被尝试。
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("static_html", "proxy_network")
    ]
    assert firecrawl_calls["count"] == 0
    assert engine_store.get_engine_hint(SITE_URL) == "static_html"  # hint 保留
