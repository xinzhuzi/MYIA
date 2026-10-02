"""Tests for the crawl4ai engine & its place in the auto chain
(PRD 10-01-v02-engine-crawl4ai).

Covers:

- dependency missing: real ImportError path (skipped automatically if the
  owner installs crawl4ai) and the deterministic None-in-sys.modules path —
  both assert the structured ``dependency_missing`` error carries
  ``pip install myia[crawl4ai]`` verbatim;
- fetch path against a **fake crawl4ai module injected via sys.modules**
  (zero real network, zero real browser): list extract over rendered HTML,
  no-extract auto-structuring fallback (markdown str 与 MarkdownGenerationResult
  两种形态), options pass-through (headless / page_timeout), robots guard,
  ``success: false`` 与 arun 异常的结构化包装, 外层 wait_for 超时分类,
  ``{page}`` 模板翻页;
- auto chain order (fake engines injected into ENGINE_REGISTRY): L2 失败 →
  crawl4ai 成功且不再落 firecrawl;crawl4ai 依赖缺失按普通引擎失败继续降级。

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sys
import types
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx
import pytest

from myia.engines import registry
from myia.engines.crawl4ai import Crawl4AIEngine, load_crawl4ai
from myia.engines.fetch_base import (
    BaseEngine,
    EngineNotAvailableError,
    FetchError,
    RobotsDisallowedError,
    load_proxy_pools,
)
from myia.engines.firecrawl import FirecrawlEngine

from conftest import make_client, make_context, make_handler, make_source, run

SITE_URL = "https://js-heavy.example.com/hot"

RENDERED_HTML = (
    "<html><body>"
    "<div class=\"item\"><a class=\"title\" href=\"/t/1\">JS 帖子一</a></div>"
    "<div class=\"item\"><a class=\"title\" href=\"/t/2\">JS 帖子二</a></div>"
    "</body></html>"
)

LIST_EXTRACT = {
    "type": "list",
    "item": "div.item",
    "fields": {"title": "a.title", "url": "a.title@href"},
}


# ---------------------------------------------------------------------------
# Fake crawl4ai package (sys.modules 注入;引擎对包的全部消费面都在这里)
# ---------------------------------------------------------------------------


class FakeCore:
    """Scripted ``arun`` recorder: pops one script entry per call.

    Entries are exceptions (raised) or dicts of FakeResult fields; a dict may
    carry ``_delay`` seconds awaited before returning (drives the wait_for
    timeout test).
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    async def arun(self, url: str | None = None, config=None, **kwargs):
        self.calls.append({"url": url, "config": config, "kwargs": kwargs})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        delay = 0.0
        if isinstance(step, dict):
            delay = float(step.pop("_delay", 0.0))
        if delay:
            await asyncio.sleep(delay)
        metadata = step.get("metadata") if isinstance(step, dict) else None
        return SimpleNamespace(
            success=step.get("success", True) if isinstance(step, dict) else True,
            html=step.get("html", "") if isinstance(step, dict) else "",
            markdown=step.get("markdown", "") if isinstance(step, dict) else "",
            metadata=metadata if isinstance(metadata, dict) else None,
            error_message=step.get("error_message", "") if isinstance(step, dict) else "",
        )


@dataclass
class FakeCrawl4AI:
    """Everything the engine may touch on the fake module, for assertions."""

    module: types.ModuleType
    core: FakeCore
    browser_configs: list[dict] = field(default_factory=list)
    run_configs: list[dict] = field(default_factory=list)


def install_fake_crawl4ai(monkeypatch: pytest.MonkeyPatch, script: list) -> FakeCrawl4AI:
    """Build a fake ``crawl4ai`` package and inject it via sys.modules."""
    module = types.ModuleType("crawl4ai")
    fake = FakeCrawl4AI(module=module, core=FakeCore(script))

    class BrowserConfig:
        def __init__(self, **kwargs) -> None:
            fake.browser_configs.append(kwargs)

    class CrawlerRunConfig:
        def __init__(self, **kwargs) -> None:
            fake.run_configs.append(kwargs)

    class CacheMode:
        BYPASS = "bypass"

    class AsyncWebCrawler:
        def __init__(self, config=None) -> None:
            self.config = config

        async def __aenter__(self):
            return fake.core

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    module.BrowserConfig = BrowserConfig
    module.CrawlerRunConfig = CrawlerRunConfig
    module.CacheMode = CacheMode
    module.AsyncWebCrawler = AsyncWebCrawler
    monkeypatch.setitem(sys.modules, "crawl4ai", module)
    return fake


# ---------------------------------------------------------------------------
# 未安装路径:结构化错误含安装命令
# ---------------------------------------------------------------------------


def test_load_crawl4ai_absent_raises_structured_dependency_error(monkeypatch):
    """确定性路径:sys.modules 置 None 强制 import 走真实 ImportError 分支
    (即使主人日后装了 crawl4ai,该测试仍稳定覆盖未安装分支)。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    with pytest.raises(FetchError) as excinfo:
        load_crawl4ai()
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[crawl4ai]" in str(excinfo.value)


@pytest.mark.skipif(
    importlib.util.find_spec("crawl4ai") is not None,
    reason="真实未安装环境路径:本环境应未安装 crawl4ai(装了则此用例无意义)",
)
def test_fetch_engine_not_installed_real_error(monkeypatch):
    """真实报错路径:当前 venv 未安装 crawl4ai,引擎 fetch 直接结构化拒绝。"""
    monkeypatch.delitem(sys.modules, "crawl4ai", raising=False)
    client = make_client(make_handler(lambda r: pytest.fail("依赖缺失时不应发起任何请求")))
    context, _ = make_context(client)
    engine = Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL, extract=LIST_EXTRACT), context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[crawl4ai]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 假模块注入:采集路径
# ---------------------------------------------------------------------------


def test_fetch_with_list_extract_parses_rendered_html(monkeypatch):
    fake = install_fake_crawl4ai(monkeypatch, [{"html": RENDERED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="crawl4ai", url=SITE_URL, extract=LIST_EXTRACT)

    items = run(Crawl4AIEngine(source, context).fetch())

    assert items == [
        {"title": "JS 帖子一", "url": "https://js-heavy.example.com/t/1"},  # 相对链接按页面 URL 补全
        {"title": "JS 帖子二", "url": "https://js-heavy.example.com/t/2"},
    ]
    assert fake.core.calls[0]["url"] == SITE_URL
    assert fake.browser_configs == [{"headless": True}]  # 默认无头
    assert fake.run_configs[0]["page_timeout"] == 60000  # 默认 60s,API 侧毫秒
    assert fake.run_configs[0]["cache_mode"] == "bypass"  # 缓存语义由我们自管


def test_fetch_without_extract_auto_structures_markdown(monkeypatch):
    """无 extract 时自动结构化兜底:{url,title,content},与 firecrawl 无 extract 同形。"""
    install_fake_crawl4ai(
        monkeypatch,
        [{"markdown": "# 渲染后的标题\n\n- 条目一", "metadata": {"title": "渲染后的标题"}}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="crawl4ai", url=SITE_URL)  # 无 extract

    items = run(Crawl4AIEngine(source, context).fetch())

    assert items == [
        {"url": SITE_URL, "title": "渲染后的标题", "content": "# 渲染后的标题\n\n- 条目一"}
    ]


def test_markdown_generation_result_object_is_unwrapped(monkeypatch):
    """crawl4ai 新版 markdown 是 MarkdownGenerationResult 对象,取 raw_markdown。"""

    class FakeMarkdownResult:
        raw_markdown = "# 对象形态"

    install_fake_crawl4ai(monkeypatch, [{"markdown": FakeMarkdownResult()}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    items = run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL), context).fetch())

    assert items[0]["content"] == "# 对象形态"
    assert items[0]["title"] == ""  # metadata 缺失不炸,留空串


def test_unsuccessful_result_is_structured_error(monkeypatch):
    install_fake_crawl4ai(monkeypatch, [{"success": False, "error_message": "net::ERR_NAME_NOT_RESOLVED"}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "crawl4ai_error"
    assert "net::ERR_NAME_NOT_RESOLVED" in str(excinfo.value)


def test_arun_exception_wrapped_as_crawl4ai_error(monkeypatch):
    """arun 抛浏览器异常 → 结构化包装且保留错误链(__cause__)。"""
    install_fake_crawl4ai(monkeypatch, [RuntimeError("playwright browser crashed")])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "crawl4ai_error"
    assert "browser crashed" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_run_budget_timeout_wrapped_as_timeout_error(monkeypatch):
    """外层 wait_for 预算耗尽 → asyncio.TimeoutError 包装为 timeout 分类(错误链保留)。"""
    install_fake_crawl4ai(monkeypatch, [{"_delay": 5.0, "html": RENDERED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="crawl4ai",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={"crawl4ai": {"timeout": 0.05}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(Crawl4AIEngine(source, context).fetch())
    assert excinfo.value.error_type == "timeout"
    assert isinstance(excinfo.value.__cause__, asyncio.TimeoutError)


def test_robots_disallowed_skips_browser(monkeypatch):
    fake = install_fake_crawl4ai(monkeypatch, [])
    client = make_client(
        make_handler(lambda r: httpx.Response(404, text=""), robots="User-agent: *\nDisallow: /")
    )
    context, _ = make_context(client)

    with pytest.raises(RobotsDisallowedError):
        run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL), context).fetch())
    assert fake.core.calls == []  # robots 拒绝在浏览器启动前,零抓取


def test_engine_options_headless_and_timeout_pass_through(monkeypatch):
    fake = install_fake_crawl4ai(monkeypatch, [{"html": RENDERED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="crawl4ai",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={"crawl4ai": {"headless": False, "timeout": 15}},
    )

    run(Crawl4AIEngine(source, context).fetch())

    assert fake.browser_configs == [{"headless": False}]
    assert fake.run_configs[0]["page_timeout"] == 15000


def test_invalid_options_are_structured_errors(monkeypatch):
    client = make_client(make_handler(lambda r: pytest.fail("配置错误时不应发起任何请求")))

    bad_timeout = make_source(
        engine="crawl4ai", url=SITE_URL, engine_options={"crawl4ai": {"timeout": "soon"}}
    )
    bad_bool_timeout = make_source(
        engine="crawl4ai", url=SITE_URL, engine_options={"crawl4ai": {"timeout": True}}
    )
    bad_headless = make_source(
        engine="crawl4ai", url=SITE_URL, engine_options={"crawl4ai": {"headless": "yes"}}
    )
    for source, expected_type in (
        (bad_timeout, "invalid_timeout"),
        (bad_bool_timeout, "invalid_timeout"),  # bool 是 int 子类,必须显式拒绝
        (bad_headless, "invalid_headless"),
    ):
        context, _ = make_context(client)
        with pytest.raises(FetchError) as excinfo:
            run(Crawl4AIEngine(source, context).fetch())
        assert excinfo.value.error_type == expected_type  # 配置校验先于依赖加载,无需注入假模块


def test_json_path_extract_rejected_for_degrade(monkeypatch):
    """json_path 属 L1 语义:L3 拒绝(extract_unsupported)让 auto 链正确降级。"""
    install_fake_crawl4ai(monkeypatch, [])
    client = make_client(make_handler(lambda r: pytest.fail("extract 不支持时不应发起抓取")))
    context, _ = make_context(client)
    source = make_source(
        engine="crawl4ai",
        url=SITE_URL,
        extract={"type": "json_path", "fields": {"title": "$.t", "url": "$.url"}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(Crawl4AIEngine(source, context).fetch())
    assert excinfo.value.error_type == "extract_unsupported"


def test_template_pagination_walks_pages(monkeypatch):
    fake = install_fake_crawl4ai(
        monkeypatch,
        [{"html": RENDERED_HTML}, {"html": RENDERED_HTML.replace("帖子", "第二页帖")}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="crawl4ai",
        url="https://js-heavy.example.com/hot?page={page}",
        extract=LIST_EXTRACT,
        pagination={"mode": "template", "max_pages": 2},
    )

    items = run(Crawl4AIEngine(source, context).fetch())

    assert [call["url"] for call in fake.core.calls] == [
        "https://js-heavy.example.com/hot?page=1",
        "https://js-heavy.example.com/hot?page=2",
    ]
    assert len(items) == 4


# ---------------------------------------------------------------------------
# auto 链顺序:注入假引擎验证 L2 失败 → crawl4ai 成功,不再落 firecrawl
# ---------------------------------------------------------------------------


class FakeChainCrawl4AI(BaseEngine):
    """Stand-in engine for chain-order tests (no optional dependency needed)."""

    LAYER = "L3"
    ENGINE_NAME = "crawl4ai"
    REQUIRES_EXTRACT = False
    SUPPORTED_EXTRACT_TYPES = ("list", "item")

    async def _fetch_impl(self) -> list[dict]:
        return [{"title": "JS 帖子", "url": "https://js-heavy.example.com/t/9"}]


class AlwaysFailEngine(BaseEngine):
    """Guard engine: any construction/use means the chain degraded too far."""

    LAYER = "guard"
    ENGINE_NAME = "guard"
    REQUIRES_EXTRACT = False

    async def _fetch_impl(self) -> list[dict]:  # pragma: no cover - 不应被触达
        raise AssertionError("guard 引擎不应被触达")


def test_auto_degrade_chain_order_with_crawl4ai():
    """链终形态链序(yaml-schema rule 5 / PRD 10-01-v04-engine-llm-browser):
    L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser→llm_browser。"""
    assert registry.AUTO_CHAIN == (
        "direct_api", "static_html", "crawl4ai", "firecrawl", "scrapling",
        "stealth_browser", "llm_browser",
    )
    assert registry.auto_degrade("auto") == [
        "direct_api", "static_html", "crawl4ai", "firecrawl", "scrapling",
        "stealth_browser", "llm_browser",
    ]
    assert registry.auto_degrade("static_html") == [
        "static_html", "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    ]
    assert registry.auto_degrade("crawl4ai") == [
        "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    ]
    assert registry.auto_degrade("firecrawl") == ["firecrawl", "scrapling", "stealth_browser", "llm_browser"]
    assert registry.auto_degrade("scrapling") == ["scrapling", "stealth_browser", "llm_browser"]


def test_crawl4ai_no_longer_scheduled_not_available(monkeypatch):
    """crawl4ai(v0.2)/ scrapling(v0.3)/ stealth_browser / llm_browser(v0.4)
    已实装,ENGINE_SCHEDULED_VERSIONS 清空;结构化拒绝分支以合成排期名保持覆盖。"""
    assert "crawl4ai" not in registry.ENGINE_SCHEDULED_VERSIONS
    assert "scrapling" not in registry.ENGINE_SCHEDULED_VERSIONS
    assert registry.ENGINE_SCHEDULED_VERSIONS == {}
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    with pytest.raises(EngineNotAvailableError) as excinfo:
        registry.auto_degrade("time_machine")
    assert excinfo.value.scheduled_version == "v9.9"


def test_registry_resolves_crawl4ai_engine_class():
    assert registry.resolve_engine("crawl4ai") is Crawl4AIEngine


def test_auto_chain_l2_failure_degrades_to_crawl4ai_not_firecrawl(monkeypatch, engine_store):
    """链序单测:L1 词汇表拒绝 → L2 500 → crawl4ai 成功;firecrawl 工厂零调用。"""
    firecrawl_factory_calls = {"count": 0}

    def firecrawl_factory():
        firecrawl_factory_calls["count"] += 1
        return AlwaysFailEngine

    monkeypatch.setitem(registry.ENGINE_REGISTRY, "crawl4ai", lambda: FakeChainCrawl4AI)
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "firecrawl", firecrawl_factory)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="waf blocked")  # L1 无请求;L2 被拒

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT)
    context, _ = make_context(client, store=engine_store)

    outcome = run(registry.fetch_source(source, context))

    assert outcome.engine == "crawl4ai"
    assert outcome.items == [{"title": "JS 帖子", "url": "https://js-heavy.example.com/t/9"}]
    assert [failure.error_type for failure in outcome.failures] == ["extract_unsupported", "http_500"]
    assert firecrawl_factory_calls["count"] == 0  # crawl4ai 成功即停,不再落 firecrawl
    assert engine_store.get_engine_hint(SITE_URL) == "crawl4ai"  # 成功选择回写 hint


def test_auto_chain_dependency_missing_degrades_to_firecrawl(monkeypatch):
    """crawl4ai 依赖未安装按普通引擎失败记录(dependency_missing),链继续降级。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)  # 强制依赖缺失分支

    class FakeFirecrawlOK(FirecrawlEngine):
        async def _fetch_impl(self) -> list[dict]:
            return [{"url": SITE_URL, "title": "兜底", "content": "# 兜底"}]

    monkeypatch.setitem(registry.ENGINE_REGISTRY, "firecrawl", lambda: FakeFirecrawlOK)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT, retry=0)
    context, _ = make_context(client)

    outcome = run(registry.fetch_source(source, context))

    assert outcome.engine == "firecrawl"
    assert [failure.error_type for failure in outcome.failures] == [
        "extract_unsupported",
        "http_500",
        "dependency_missing",
    ]
    assert any("pip install myia[crawl4ai]" in failure.message for failure in outcome.failures)


# ---------------------------------------------------------------------------
# 真实源 smoke(PRD 验收:JS 渲染源真实跑通 TapNow):可选依赖 + 真实网络,
# 默认跳过,本地装好 myia[crawl4ai] 后设 MYIA_CRAWL4AI_SMOKE=1 执行。
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("MYIA_CRAWL4AI_SMOKE"),
    reason="真实源 smoke:仅本地安装 myia[crawl4ai] 且设 MYIA_CRAWL4AI_SMOKE=1 时执行,CI 不依赖",
)
def test_smoke_tapnow_js_render_auto_structures():
    """PRD 验收入口:TapNow(https://app.tapnow.ai/)JS 渲染 → 自动结构化兜底非空。"""

    async def scenario():
        client = httpx.AsyncClient()
        try:
            context, _ = make_context(client)
            engine = Crawl4AIEngine(make_source(engine="crawl4ai", url="https://app.tapnow.ai/"), context)
            return await engine.fetch()
        finally:
            await client.aclose()

    items = run(scenario())
    assert items and items[0]["content"]  # 渲染后的 markdown 载荷非空


# ---------------------------------------------------------------------------
# v0.2 复盘修复:代理/headers 透传 + 浏览器启动失败结构化
# ---------------------------------------------------------------------------


def test_pool_proxy_is_passed_to_browser_config(monkeypatch):
    """源配 pool: 代理时,BrowserConfig 必须收到解析后的 upstream——
    否则用户以为走了代理,真实 IP 直连目标站(显式代理意图静默丢弃)。"""
    fake = install_fake_crawl4ai(monkeypatch, [{"html": RENDERED_HTML}])
    recorded: list[dict] = []
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        recorded.append(dict(kwargs))
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 互斥
        kwargs.setdefault("transport", httpx.MockTransport(make_handler(
            lambda r: httpx.Response(404, text="")
        )))
        return real_client(**kwargs)

    monkeypatch.setattr("myia.engines.fetch_base.httpx.AsyncClient", factory)
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    context.proxy_pools = load_proxy_pools(
        {"pools": {"main": "http://proxy.example.com:8080"}}
    )
    source = make_source(engine="crawl4ai", url=SITE_URL, extract=LIST_EXTRACT, proxy="pool:main")

    run(Crawl4AIEngine(source, context).fetch())

    assert fake_kwargs_proxy(recorded) is not None  # 池客户端拿到 proxy(基座挂载)
    assert fake.browser_configs[0]["proxy"] == "http://proxy.example.com:8080"  # 浏览器也拿到
    assert fake.browser_configs[0]["headless"] is True


def fake_kwargs_proxy(recorded: list[dict]) -> str | None:
    for kwargs in recorded:
        if "proxy" in kwargs:
            return kwargs["proxy"]
    return None


def test_direct_source_browser_config_has_no_proxy(monkeypatch):
    """direct 源:BrowserConfig 不带 proxy 键(与既有契约一致)。"""
    fake = install_fake_crawl4ai(monkeypatch, [{"html": RENDERED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL, extract=LIST_EXTRACT), context).fetch())

    assert "proxy" not in fake.browser_configs[0]
    assert "headers" not in fake.browser_configs[0]


def test_source_headers_are_passed_to_browser_config(monkeypatch):
    """配置了 headers(含 Cookie 凭据)的源必须以该身份渲染,不许静默匿名。"""
    fake = install_fake_crawl4ai(monkeypatch, [{"html": RENDERED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="crawl4ai",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        headers={"Cookie": "env:MYIA_C4A_COOKIE"},
    )
    monkeypatch.setenv("MYIA_C4A_COOKIE", "sid=xyz")

    run(Crawl4AIEngine(source, context).fetch())

    assert fake.browser_configs[0]["headers"]["Cookie"] == "sid=xyz"
    assert "user-agent" in {k.lower() for k in fake.browser_configs[0]["headers"]}


def test_browser_startup_failure_is_structured_with_setup_hint(monkeypatch):
    """__aenter__ 失败(playwright 二进制未装——最常见真实故障)必须结构化,
    并带 crawl4ai-setup 提示;裸 RuntimeError 逃逸会被归类 unknown。"""

    class BrokenCrawler:
        def __init__(self, config=None) -> None:
            pass

        async def __aenter__(self):
            raise RuntimeError(
                "Executable doesn't exist at .../chrome. Please run playwright install"
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False  # pragma: no cover - 不可达

    module = types.ModuleType("crawl4ai")

    class BrowserConfig:
        def __init__(self, **kwargs) -> None:
            pass

    class CrawlerRunConfig:
        def __init__(self, **kwargs) -> None:
            pass

    class CacheMode:
        BYPASS = "bypass"

    module.BrowserConfig = BrowserConfig
    module.CrawlerRunConfig = CrawlerRunConfig
    module.CacheMode = CacheMode
    module.AsyncWebCrawler = BrokenCrawler
    monkeypatch.setitem(sys.modules, "crawl4ai", module)

    client = make_client(make_handler(lambda r: pytest.fail("启动失败时不应发起抓取")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(Crawl4AIEngine(make_source(engine="crawl4ai", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "crawl4ai_error"
    assert "crawl4ai-setup" in str(excinfo.value)


def test_broken_install_import_error_is_structured(monkeypatch):
    """导入期抛非 ImportError(安装损坏)也走 dependency_missing,不裸逃。"""
    monkeypatch.setitem(sys.modules, "crawl4ai", None)

    real_import = importlib.import_module

    def broken_import(name: str):
        if name == "crawl4ai":
            raise OSError("dlopen: framework not found")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", broken_import)
    with pytest.raises(FetchError) as excinfo:
        load_crawl4ai()
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[crawl4ai]" in str(excinfo.value)
