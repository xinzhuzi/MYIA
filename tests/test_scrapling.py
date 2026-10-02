"""Tests for the scrapling engine & its place in the auto chain
(PRD 10-01-v03-engine-scrapling).

Covers:

- dependency missing: real ImportError path (skipped automatically if the
  owner installs scrapling) and the deterministic None-in-sys.modules path —
  both assert the structured ``dependency_missing`` error carries
  ``pip install myia[scrapling]`` verbatim; broken install (non-ImportError)
  is structured the same way;
- fetch path against a **fake scrapling.fetchers module injected via
  sys.modules** (zero real network, zero real browser): list/item extract
  with adaptive kwargs, no-extract auto-structuring fallback, backend
  kwargs pass-through (stealth/dynamic/static), robots guard,
  ``http_403`` 结构化分类, 后端异常包装(scrapling_error/含 install 提示),
  外层 wait_for 超时分类, ``{page}`` 模板翻页, pool 代理与 headers 透传
  (默认 UA 不透传);
- **pagination.mode: scroll**:page_action 注入与轮数上限(= max_pages)、
  与 static 后端互斥(scroll_unsupported)、与 ``{page}`` 模板互斥
  (invalid_pagination)、单目标不展开页;
- auto chain order (fake engines injected into ENGINE_REGISTRY): 五层链序
  L1→L2→crawl4ai→firecrawl→scrapling,前四级全败后 scrapling 兜底成功且
  hint 回写;scrapling 已实装、不再命中 scheduled 分支;
- 真实盾源 smoke(nodeseek)写成 opt-in:MYIA_SMOKE_REAL=1 才跑,CI 不依赖。

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

import httpx
import pytest
from selectolax.parser import HTMLParser

from myia.engines import registry
from myia.engines.fetch_base import (
    BaseEngine,
    EngineNotAvailableError,
    FetchError,
    RobotsDisallowedError,
    load_proxy_pools,
)
from myia.engines.scrapling import (
    SCROLL_TO_BOTTOM_JS,
    ScraplingEngine,
    load_scrapling,
)

from conftest import make_client, make_context, make_handler, make_source, run

SITE_URL = "https://shielded.example.com/hot"

SHIELDED_HTML = (
    "<html><head><title>盾源标题</title></head><body>"
    '<div class="thread"><a class="title" href="/t/1">帖子一</a></div>'
    '<div class="thread"><a class="title" href="/t/2">帖子二</a></div>'
    "</body></html>"
)

LIST_EXTRACT = {
    "type": "list",
    "item": "div.thread",
    "fields": {"title": "a.title", "url": "a.title@href"},
}


# ---------------------------------------------------------------------------
# Fake scrapling.fetchers package (sys.modules 注入;引擎对包的全部消费面:
# StealthyFetcher/DynamicFetcher.async_fetch 与 AsyncFetcher.get -> Response)
# ---------------------------------------------------------------------------


class FakeSelectors(list):
    """List-like result set with Scrapling's ``.first`` (None when empty)."""

    @property
    def first(self):
        return self[0] if self else None


class FakeElement:
    """Scrapling Selector element surface: ``.css`` / ``.attrib`` / ``.text``."""

    def __init__(self, fake: FakeScrapling, node) -> None:
        self._fake = fake
        self._node = node

    @property
    def text(self) -> str:
        return self._node.text(separator=" ", strip=True)

    @property
    def attrib(self) -> dict:
        return dict(self._node.attributes)

    def css(self, selector: str, **kwargs):
        self._fake.css_calls.append((selector, kwargs))
        return FakeSelectors(FakeElement(self._fake, n) for n in self._node.css(selector))


class FakeResponse:
    """Scrapling Response surface: ``.status`` / ``.url`` / ``.css`` (Selector)."""

    def __init__(self, fake: FakeScrapling, html: str, *, status: int = 200, url: str = "") -> None:
        self._fake = fake
        self.status = status
        self.url = url
        self._tree = HTMLParser(html)

    def css(self, selector: str, **kwargs):
        self._fake.css_calls.append((selector, kwargs))
        return FakeSelectors(FakeElement(self._fake, n) for n in self._tree.css(selector))


class FakePage:
    """Playwright page surface consumed by the scroll page_action (evaluate)."""

    def __init__(self, fake: FakeScrapling) -> None:
        self._fake = fake

    async def evaluate(self, js: str, *args):
        self._fake.evaluations.append((js, args))
        return None


@dataclass
class FakeScrapling:
    """Everything the engine may touch on the fake package, for assertions."""

    script: list
    calls: list[dict] = field(default_factory=list)  # {"backend", "url", "kwargs"}
    css_calls: list[tuple] = field(default_factory=list)  # (selector, kwargs)
    evaluations: list[tuple] = field(default_factory=list)  # (js, args)

    async def perform(self, backend: str, url: str, kwargs: dict):
        self.calls.append({"backend": backend, "url": url, "kwargs": kwargs})
        page = FakePage(self)
        action = kwargs.get("page_action")
        if action is not None:
            await action(page)  # 真库在导航完成后 await page_action(page)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        delay = float(step.pop("_delay", 0.0)) if isinstance(step, dict) else 0.0
        if delay:
            await asyncio.sleep(delay)
        return FakeResponse(self, step.get("html", ""), status=step.get("status", 200), url=url)


def install_fake_scrapling(monkeypatch: pytest.MonkeyPatch, script: list) -> FakeScrapling:
    """Build a fake ``scrapling.fetchers`` package and inject it via sys.modules."""
    fake = FakeScrapling(script)
    module = types.ModuleType("scrapling")
    fetchers = types.ModuleType("scrapling.fetchers")

    class StealthyFetcher:
        @classmethod
        async def async_fetch(cls, url: str, **kwargs):
            return await fake.perform("stealth", url, kwargs)

    class DynamicFetcher:
        @classmethod
        async def async_fetch(cls, url: str, **kwargs):
            return await fake.perform("dynamic", url, kwargs)

    class AsyncFetcher:
        @classmethod
        def get(cls, url: str, **kwargs):
            return fake.perform("static", url, kwargs)  # 真库返回 Awaitable

    fetchers.StealthyFetcher = StealthyFetcher
    fetchers.DynamicFetcher = DynamicFetcher
    fetchers.AsyncFetcher = AsyncFetcher
    module.fetchers = fetchers
    monkeypatch.setitem(sys.modules, "scrapling", module)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers)
    return fake


# ---------------------------------------------------------------------------
# 未安装路径:结构化错误含安装命令
# ---------------------------------------------------------------------------


def test_load_scrapling_absent_raises_structured_dependency_error(monkeypatch):
    """确定性路径:sys.modules 置 None 强制 import 走真实 ImportError 分支
    (即使主人日后装了 scrapling,该测试仍稳定覆盖未安装分支)。"""
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)
    with pytest.raises(FetchError) as excinfo:
        load_scrapling()
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[scrapling]" in str(excinfo.value)


@pytest.mark.skipif(
    importlib.util.find_spec("scrapling") is not None,
    reason="真实未安装环境路径:本环境应未安装 scrapling(装了则此用例无意义)",
)
def test_fetch_engine_not_installed_real_error(monkeypatch):
    """真实报错路径:当前 venv 未安装 scrapling,引擎 fetch 直接结构化拒绝。"""
    monkeypatch.delitem(sys.modules, "scrapling.fetchers", raising=False)
    monkeypatch.delitem(sys.modules, "scrapling", raising=False)
    client = make_client(make_handler(lambda r: pytest.fail("依赖缺失时不应发起任何请求")))
    context, _ = make_context(client)
    engine = ScraplingEngine(make_source(engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT), context)

    with pytest.raises(FetchError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[scrapling]" in str(excinfo.value)


def test_broken_install_import_error_is_structured(monkeypatch):
    """导入期抛非 ImportError(安装损坏)也走 dependency_missing,不裸逃。"""
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)

    real_import = importlib.import_module

    def broken_import(name: str):
        if name == "scrapling.fetchers":
            raise OSError("dlopen: framework not found")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", broken_import)
    with pytest.raises(FetchError) as excinfo:
        load_scrapling()
    assert excinfo.value.error_type == "dependency_missing"
    assert "pip install myia[scrapling]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 假模块注入:采集路径(默认 stealth 后端)
# ---------------------------------------------------------------------------


def test_fetch_with_list_extract_parses_html_with_adaptive(monkeypatch):
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT)

    items = run(ScraplingEngine(source, context).fetch())

    assert items == [
        {"title": "帖子一", "url": "https://shielded.example.com/t/1"},  # 相对链接按页面 URL 补全
        {"title": "帖子二", "url": "https://shielded.example.com/t/2"},
    ]
    call = fake.calls[0]
    assert call["backend"] == "stealth"  # 默认隐身后端(过盾是 L4 卖点)
    assert call["url"] == SITE_URL
    assert call["kwargs"] == {
        "headless": True,  # 默认无头
        "timeout": 60000,  # 默认 60s,浏览器侧毫秒
        "network_idle": True,
        "solve_cloudflare": True,  # PRD:过 CF 基础盾
        "selector_config": {"adaptive": True},
    }
    assert "proxy" not in call["kwargs"]  # direct 源不带代理
    assert "extra_headers" not in call["kwargs"]  # 无源级 headers 不传空壳
    assert "page_action" not in call["kwargs"]  # 非 scroll 模式无滚动钩子
    # 自愈选择器:每次 css 命中即存指纹、失效按相似度重定位
    assert fake.css_calls[0] == (LIST_EXTRACT["item"], {"adaptive": True, "auto_save": True})
    assert fake.css_calls[1] == ("a.title", {"adaptive": True, "auto_save": True})


def test_fetch_without_extract_auto_structures(monkeypatch):
    """无 extract 时自动结构化兜底:{url,title,content},与 L3/firecrawl 无 extract 同形。"""
    install_fake_scrapling(
        monkeypatch,
        [{"html": SHIELDED_HTML.replace("帖子", "瀑布流帖")}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL)  # 无 extract

    items = run(ScraplingEngine(source, context).fetch())

    assert items == [{"url": SITE_URL, "title": "盾源标题", "content": "瀑布流帖一 瀑布流帖二"}]


def test_item_extract_single_page(monkeypatch):
    """extract.type 为 item:全文档级字段选择器,单条记录。"""
    install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract={"type": "item", "fields": {"title": "head title", "first_link": "a.title@href"}},
    )

    items = run(ScraplingEngine(source, context).fetch())

    assert items == [{"title": "盾源标题", "first_link": "https://shielded.example.com/t/1"}]


def test_http_403_is_structured_http_error(monkeypatch):
    """盾拦截(HTTP 403)按 http_<status> 分类,auto 链据此降级。"""
    install_fake_scrapling(monkeypatch, [{"status": 403, "html": ""}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(make_source(engine="scrapling", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "http_403"


def test_backend_exception_wrapped_as_scrapling_error(monkeypatch):
    """后端运行异常 → 结构化包装且保留错误链(__cause__)。"""
    install_fake_scrapling(monkeypatch, [RuntimeError("stealth browser crashed")])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(make_source(engine="scrapling", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "scrapling_error"
    assert "browser crashed" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_startup_failure_hint_mentions_scrapling_install(monkeypatch):
    """浏览器内核缺失(executable/install 字样)的启动失败带 scrapling install 提示。"""
    install_fake_scrapling(monkeypatch, [RuntimeError("Executable doesn't exist; please run scrapling install")])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(make_source(engine="scrapling", url=SITE_URL), context).fetch())
    assert excinfo.value.error_type == "scrapling_error"
    assert "scrapling install" in str(excinfo.value)


def test_run_budget_timeout_wrapped_as_timeout_error(monkeypatch):
    """外层 wait_for 预算耗尽 → asyncio.TimeoutError 包装为 timeout 分类(错误链保留)。"""
    install_fake_scrapling(monkeypatch, [{"_delay": 5.0, "html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={"scrapling": {"timeout": 0.05}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())
    assert excinfo.value.error_type == "timeout"
    assert isinstance(excinfo.value.__cause__, asyncio.TimeoutError)


def test_robots_disallowed_skips_fetch(monkeypatch):
    fake = install_fake_scrapling(monkeypatch, [])
    client = make_client(make_handler(lambda r: httpx.Response(404, text=""), robots="User-agent: *\nDisallow: /"))
    context, _ = make_context(client)

    with pytest.raises(RobotsDisallowedError):
        run(ScraplingEngine(make_source(engine="scrapling", url=SITE_URL), context).fetch())
    assert fake.calls == []  # robots 拒绝在后端调用前,零抓取


# ---------------------------------------------------------------------------
# 后端选择与 kwargs 透传
# ---------------------------------------------------------------------------


def test_dynamic_backend_uses_dynamic_fetcher(monkeypatch):
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={"scrapling": {"backend": "dynamic"}},
    )

    run(ScraplingEngine(source, context).fetch())

    call = fake.calls[0]
    assert call["backend"] == "dynamic"
    assert "solve_cloudflare" not in call["kwargs"]  # Turnstile 求解仅 stealth 后端


def test_static_backend_uses_async_fetcher_get(monkeypatch):
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={"scrapling": {"backend": "static"}},
    )

    run(ScraplingEngine(source, context).fetch())

    call = fake.calls[0]
    assert call["backend"] == "static"
    assert call["kwargs"] == {"timeout": 60.0, "selector_config": {"adaptive": True}}  # static 秒,非毫秒
    assert "headless" not in call["kwargs"]


def test_source_headers_forwarded_without_default_user_agent(monkeypatch):
    """源级 headers(含凭据)经 extra_headers 透传;引擎默认 UA 不透传
    (隐身后端的 UA 必须与浏览器指纹同源,写死 MYIA/0.1 反而破功)。"""
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        headers={"Cookie": "env:MYIA_SCK_COOKIE"},
    )
    monkeypatch.setenv("MYIA_SCK_COOKIE", "sid=xyz")

    run(ScraplingEngine(source, context).fetch())

    extra = fake.calls[0]["kwargs"]["extra_headers"]
    assert extra["Cookie"] == "sid=xyz"
    assert "user-agent" not in {key.lower() for key in extra}


def test_custom_user_agent_is_forwarded(monkeypatch):
    """源显式配置的 UA 属配置意图,透传(仅引擎默认 UA 被剔除)。"""
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        headers={"user-agent": "env:MYIA_SCK_UA"},
    )
    monkeypatch.setenv("MYIA_SCK_UA", "CustomUA/1.0")

    run(ScraplingEngine(source, context).fetch())

    assert fake.calls[0]["kwargs"]["extra_headers"]["user-agent"] == "CustomUA/1.0"


def test_browser_options_pass_through(monkeypatch):
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        engine_options={
            "scrapling": {
                "headless": False,
                "timeout": 15,
                "solve_cloudflare": False,
                "network_idle": False,
                "wait_selector": "div.thread",
                "adaptive": False,
            }
        },
    )

    run(ScraplingEngine(source, context).fetch())

    assert fake.calls[0]["kwargs"] == {
        "headless": False,
        "timeout": 15000,
        "network_idle": False,
        "solve_cloudflare": False,
        "wait_selector": "div.thread",
        "selector_config": {"adaptive": False},
    }
    # adaptive 关闭:css 调用不带自愈参数(纯定位)
    assert fake.css_calls[0] == (LIST_EXTRACT["item"], {})


def test_pool_proxy_is_passed_to_fetch_kwargs(monkeypatch):
    """源配 pool: 代理时,后端必须收到解析后的 upstream——否则用户以为走了
    代理,真实 IP 直连目标站(显式代理意图静默丢弃)。"""
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    recorded: list[dict] = []
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        recorded.append(dict(kwargs))
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 互斥
        kwargs.setdefault(
            "transport",
            httpx.MockTransport(make_handler(lambda r: httpx.Response(404, text=""))),
        )
        return real_client(**kwargs)

    monkeypatch.setattr("myia.engines.fetch_base.httpx.AsyncClient", factory)
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    context.proxy_pools = load_proxy_pools({"pools": {"main": "http://proxy.example.com:8080"}})
    source = make_source(engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT, proxy="pool:main")

    run(ScraplingEngine(source, context).fetch())

    assert fake.calls[0]["kwargs"]["proxy"] == "http://proxy.example.com:8080"


def test_invalid_options_are_structured_errors():
    """engine_options 拼错 → 先报配置错(fail-fast 于配置),零网络零依赖加载。"""
    client = make_client(make_handler(lambda r: pytest.fail("配置错误时不应发起任何请求")))

    cases = (
        ({"backend": "wat"}, "invalid_backend"),
        ({"timeout": "soon"}, "invalid_timeout"),
        ({"timeout": True}, "invalid_timeout"),  # bool 是 int 子类,必须显式拒绝
        ({"headless": "yes"}, "invalid_headless"),
        ({"solve_cloudflare": "yes"}, "invalid_solve_cloudflare"),
        ({"network_idle": "yes"}, "invalid_network_idle"),
        ({"wait_selector": "  "}, "invalid_wait_selector"),
        ({"adaptive": "yes"}, "invalid_adaptive"),
    )
    for options, expected_type in cases:
        context, _ = make_context(client)
        source = make_source(
            engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT, engine_options={"scrapling": options}
        )
        with pytest.raises(FetchError) as excinfo:
            run(ScraplingEngine(source, context).fetch())
        assert excinfo.value.error_type == expected_type


def test_json_path_extract_rejected_for_degrade():
    """json_path 属 L1 语义:L4 拒绝(extract_unsupported)让 auto 链正确降级。"""
    client = make_client(make_handler(lambda r: pytest.fail("extract 不支持时不应发起抓取")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract={"type": "json_path", "fields": {"title": "$.t", "url": "$.url"}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())
    assert excinfo.value.error_type == "extract_unsupported"


# ---------------------------------------------------------------------------
# pagination.mode: scroll(本引擎实装;L1-L3 不支持)
# ---------------------------------------------------------------------------


def test_scroll_runs_page_action_with_rounds_cap(monkeypatch):
    """scroll:page_action 注入,轮数上限 = pagination.max_pages,单目标不展开页。"""
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        pagination={"mode": "scroll", "max_pages": 3},
    )

    items = run(ScraplingEngine(source, context).fetch())

    assert len(items) == 2
    assert len(fake.calls) == 1  # scroll 是同一页的无限加载,不做 URL 展开
    assert "page_action" in fake.calls[0]["kwargs"]
    assert fake.evaluations == [(SCROLL_TO_BOTTOM_JS, (3,))]  # 库会 await page_action(page)


def test_scroll_default_rounds_is_max_pages_default(monkeypatch):
    """未显式配 max_pages 时 schema 缺省 1 → 一轮滚动(行为确定可解释)。"""
    fake = install_fake_scrapling(monkeypatch, [{"html": SHIELDED_HTML}])
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL, pagination={"mode": "scroll"})

    run(ScraplingEngine(source, context).fetch())

    assert fake.evaluations == [(SCROLL_TO_BOTTOM_JS, (1,))]


def test_scroll_with_static_backend_rejected():
    """scroll 需要浏览器后端挂 page_action;static 后端结构化拒绝并降级。"""
    client = make_client(make_handler(lambda r: pytest.fail("scroll+static 不应发起任何请求")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        pagination={"mode": "scroll", "max_pages": 5},
        engine_options={"scrapling": {"backend": "static"}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())
    assert excinfo.value.error_type == "scroll_unsupported"


def test_scroll_with_page_template_rejected():
    """scroll 与 {page} 模板占位符互斥(语义冲突),结构化拒绝。"""
    client = make_client(make_handler(lambda r: pytest.fail("scroll+{page} 不应发起任何请求")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url="https://shielded.example.com/hot?page={page}",
        extract=LIST_EXTRACT,
        pagination={"mode": "scroll", "max_pages": 5},
    )

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())
    assert excinfo.value.error_type == "invalid_pagination"


def test_template_pagination_walks_pages(monkeypatch):
    """template 翻页照常:逐页开合浏览器门面,条目按页累加。"""
    fake = install_fake_scrapling(
        monkeypatch,
        [{"html": SHIELDED_HTML}, {"html": SHIELDED_HTML.replace("帖子", "第二页帖")}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="scrapling",
        url="https://shielded.example.com/hot?page={page}",
        extract=LIST_EXTRACT,
        pagination={"mode": "template", "max_pages": 2},
    )

    items = run(ScraplingEngine(source, context).fetch())

    assert [call["url"] for call in fake.calls] == [
        "https://shielded.example.com/hot?page=1",
        "https://shielded.example.com/hot?page=2",
    ]
    assert len(items) == 4


# ---------------------------------------------------------------------------
# auto 链五层顺序:注入假引擎验证 L1→L2→crawl4ai→firecrawl→scrapling
# ---------------------------------------------------------------------------


class FakeChainScrapling(BaseEngine):
    """Stand-in engine for chain-order tests (no optional dependency needed)."""

    LAYER = "L4"
    ENGINE_NAME = "scrapling"
    REQUIRES_EXTRACT = False
    SUPPORTED_EXTRACT_TYPES = ("list", "item")

    async def _fetch_impl(self) -> list[dict]:
        return [{"title": "盾源帖子", "url": "https://shielded.example.com/t/9"}]


def make_fail_engine(name: str, error_type: str) -> type[BaseEngine]:
    """Factory of stand-in engines that fail with a distinct error class."""

    class FailEngine(BaseEngine):
        LAYER = "fail"
        ENGINE_NAME = name
        REQUIRES_EXTRACT = False
        SUPPORTED_EXTRACT_TYPES = ("list", "item")

        async def _fetch_impl(self) -> list[dict]:
            raise FetchError(f"{name} 挂了", error_type=error_type)

    return FailEngine


def test_auto_degrade_chain_order_final_form():
    """链终形态链序(yaml-schema rule 5 / PRD 10-01-v04-engine-llm-browser):
    L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser→llm_browser;
    firecrawl 不再是链尾,llm_browser 是烧 token 的最后兜底。"""
    assert registry.AUTO_CHAIN == (
        "direct_api",
        "static_html",
        "crawl4ai",
        "firecrawl",
        "scrapling",
        "stealth_browser",
        "llm_browser",
    )
    assert registry.auto_degrade("auto") == [
        "direct_api",
        "static_html",
        "crawl4ai",
        "firecrawl",
        "scrapling",
        "stealth_browser",
        "llm_browser",
    ]
    assert registry.auto_degrade("firecrawl") == ["firecrawl", "scrapling", "stealth_browser", "llm_browser"]
    assert registry.auto_degrade("scrapling") == ["scrapling", "stealth_browser", "llm_browser"]


def test_registry_resolves_scrapling_engine_class():
    assert registry.resolve_engine("scrapling") is ScraplingEngine


def test_scrapling_implemented_but_successors_no_longer_scheduled(monkeypatch):
    """scrapling / stealth_browser / llm_browser 均已实装,ENGINE_SCHEDULED_VERSIONS
    清空;结构化 not-available 分支以合成排期名保持覆盖。"""
    assert "scrapling" not in registry.ENGINE_SCHEDULED_VERSIONS
    assert registry.ENGINE_SCHEDULED_VERSIONS == {}
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    with pytest.raises(EngineNotAvailableError) as excinfo:
        registry.auto_degrade("time_machine")
    assert excinfo.value.scheduled_version == "v9.9"
    assert excinfo.value.engine == "time_machine"


def test_auto_chain_first_four_fail_degrade_to_scrapling(monkeypatch, engine_store):
    """链序单测:L1 词汇表拒绝 → L2 500 → crawl4ai/firecrawl 假引擎失败 →
    scrapling 兜底成功;成功选择回写 hint。"""
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "crawl4ai", lambda: make_fail_engine("crawl4ai", "crawl4ai_down"))
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "firecrawl", lambda: make_fail_engine("firecrawl", "firecrawl_down"))
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "scrapling", lambda: FakeChainScrapling)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="waf blocked")  # L1 无请求;L2 被拒

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, extract=LIST_EXTRACT, retry=0)
    context, _ = make_context(client, store=engine_store)

    outcome = run(registry.fetch_source(source, context))

    assert outcome.engine == "scrapling"
    assert outcome.items == [{"title": "盾源帖子", "url": "https://shielded.example.com/t/9"}]
    assert [failure.error_type for failure in outcome.failures] == [
        "extract_unsupported",
        "http_500",
        "crawl4ai_down",
        "firecrawl_down",
    ]
    assert engine_store.get_engine_hint(SITE_URL) == "scrapling"  # 成功选择回写 hint


def test_auto_chain_dependency_missing_at_scrapling_still_records_failure(monkeypatch):
    """显式 engine: scrapling 且依赖未安装:结构化 dependency_missing,零网络;
    链继续降级(stealth 假引擎确定性失败 → llm_browser 白名单拒载,同样不触网)。"""
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)
    # stealth_browser 假引擎:单测不真拉 MCP 浏览器进程(同 test_stealth.py)
    monkeypatch.setitem(
        registry.ENGINE_REGISTRY,
        "stealth_browser",
        lambda: make_fail_engine("stealth_browser", "mcp_server_missing"),
    )
    requests_seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    client = make_client(make_handler(responder))
    source = make_source(engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT)
    context, _ = make_context(client)

    outcome = run(registry.fetch_source(source, context))

    assert outcome.engine is None
    assert requests_seen == []  # 依赖缺失在 robots/网络之前
    assert [(f.engine, f.error_type) for f in outcome.failures] == [
        ("scrapling", "dependency_missing"),
        ("stealth_browser", "mcp_server_missing"),  # L5 假引擎结构化失败
        ("llm_browser", "engine_not_whitelisted"),  # L6 白名单硬护栏:非 auto/显式源拒载
    ]
    assert "pip install myia[scrapling]" in outcome.failures[0].message


# ---------------------------------------------------------------------------
# 真实盾源 smoke(PRD 验收:nodeseek 一类基础盾源真实跑通,手动验证记录):
# 可选依赖 + 真实网络 + 真实盾,默认跳过;本地装好 myia[scrapling] 并完成
# scrapling install 后设 MYIA_SMOKE_REAL=1 执行,CI 不依赖。
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("MYIA_SMOKE_REAL"),
    reason="真实盾源 smoke:仅本地安装 myia[scrapling] 且设 MYIA_SMOKE_REAL=1 时执行,CI 不依赖",
)
def test_smoke_nodeseek_basic_shield_auto_structures():
    """PRD 验收入口:nodeseek(基础盾)→ 默认 stealth 后端真实抓取非空。

    robots.txt 若拒绝则按安全基线跳过(RobotsDisallowedError),不绕过。
    """

    async def scenario():
        client = httpx.AsyncClient()
        try:
            context, _ = make_context(client)
            engine = ScraplingEngine(make_source(engine="scrapling", url="https://www.nodeseek.com/"), context)
            return await engine.fetch()
        finally:
            await client.aclose()

    items = run(scenario())
    assert items and items[0]["content"]  # 自动结构化兜底载荷非空


# ---------------------------------------------------------------------------
# 硬墙(手机验证码/真人审核)零尝试拒绝:安全基线「无解也不碰——结构化报错」
# 与 L5 stealth_browser 同款检测(classify_hard_wall 复用);基础盾不在其列。
# ---------------------------------------------------------------------------


def test_hard_wall_phone_verification_page_refused_structured(monkeypatch):
    """HTTP 200 手机验证墙:零提取,结构化 captcha_phone_verification。
    (无 extract 时此前会把墙页 auto-structure 成条目入库——回归钉死。)"""
    install_fake_scrapling(
        monkeypatch,
        [{"html": "<html><head><title>安全验证</title></head><body>"
                  "<p>请输入手机验证码以继续访问</p></body></html>"}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL)  # 无 extract:墙页最易混入

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())

    assert excinfo.value.error_type == "captcha_phone_verification"
    assert "无解也不碰" in str(excinfo.value)


def test_hard_wall_human_review_page_refused_even_with_extract(monkeypatch):
    """带 extract 的硬墙页:同样拒绝(此前会得到 0 条静默「成功」)。"""
    install_fake_scrapling(
        monkeypatch,
        [{"html": "<html><body><div class='item'>prove you are human to continue</div>"
                  "</body></html>"}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL, extract=LIST_EXTRACT)

    with pytest.raises(FetchError) as excinfo:
        run(ScraplingEngine(source, context).fetch())

    assert excinfo.value.error_type == "captcha_human_review"


def test_basic_shield_page_not_treated_as_hard_wall(monkeypatch):
    """基础盾(CF 质询/hCaptcha)不属于硬墙:本引擎职责正是尝试过盾,不拒绝。"""
    install_fake_scrapling(
        monkeypatch,
        [{"html": "<html><head><title>Just a moment...</title></head><body>"
                  "<div class='cf-turnstile' data-sitekey='x'></div></body></html>"}],
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="scrapling", url=SITE_URL)  # 无 extract:走 auto-structure

    items = run(ScraplingEngine(source, context).fetch())

    assert items  # 质询页照常提取(能否过盾由后端能力决定),不做硬墙否决
