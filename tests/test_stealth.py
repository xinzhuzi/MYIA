"""Tests for the stealth_browser engine & its place in the auto chain
(PRD 10-01-v04-engine-stealth).

Covers:

- **MCP 协议往返(真实 stdio 子进程 mock server)**:initialize 握手 /
  tools/list / tools/call 逐帧校验;JSON-RPC 错误响应 -> ``mcp_protocol_error``;
  服务器退出 -> ``mcp_server_died``;单工具预算 -> ``timeout``;
  streamable-HTTP 传输(application/json 与 SSE 两种帧)经注入的
  MockTransport 验证,连不上 -> ``mcp_server_missing``;
- **采集路径(inject 假 McpClient,零真实进程/网络)**:list extract 经
  browser_navigate + browser_read_html 提取;无 extract 自动结构化兜底;
  browser_status 复用(已开不 open,也不代关服务器的实例)/ 未开与已挂
  (is gone)时 open 且收尾 close;
- **cookie 注入往返**:keychain: 凭据 -> browser_set_cookies 参数
  ({name, value, domain, path}),注入顺序 open -> cookies -> navigate,
  同 host 多页只注一次;服务器无 cookie 工具 -> ``cookie_unsupported``;
- **护栏与风控分类**:页面预算熔断(page_budget_exhausted,连接前)、
  未装 MCP 服务(mcp_server_missing 含安装指引)、导航 HTTP 403
  (http_403)、手机验证码/真人审核硬墙(captcha_phone_verification /
  captcha_human_review,零等待零尝试)、基础盾沉降重试通过 / 仍不过
  (captcha_challenge);
- **配置 fail-fast**:engine_options.stealth_browser 逐项 invalid_*;
  json_path 拒绝让 auto 链降级;robots 拒绝零浏览器成本;
  seed/profile/pool 代理透传 browser_open;
- **auto 链位置(L5)**:L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser,
  前五级全败后 L5 兜底成功且 hint 回写;链尾 llm_browser 见 test_llm_browser.py。

All site I/O runs on httpx.MockTransport; MCP is a subprocess script or an
injected fake — zero real network, zero real browser.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import make_client, make_context, make_handler, make_source, run

from shishi.engines import registry
from shishi.engines import stealth_browser as stealth
from shishi.engines.fetch_base import (
    BaseEngine,
    EngineNotAvailableError,
    FetchError,
    RobotsDisallowedError,
    load_proxy_pools,
)
from shishi.engines.registry import fetch_source
from shishi.engines.stealth_browser import (
    COOKIE_TOOL_NAME,
    DEFAULT_COMMAND,
    INSTALL_HINT,
    McpClient,
    McpError,
    StealthBrowserEngine,
    classify_risk_control,
    parse_cookie_header,
    parse_navigate_status,
)
from shishi.secrets import SECRET_SERVICE, InMemoryKeychainBackend

SITE_URL = "https://shielded.example.com/hot"
HOST = "shielded.example.com"

STEALTH_HTML = (
    "<html><head><title>盾源标题</title></head><body>"
    '<div class="thread"><a class="title" href="/t/1">隐身帖子一</a></div>'
    '<div class="thread"><a class="title" href="/t/2">隐身帖子二</a></div>'
    "</body></html>"
)

LIST_EXTRACT = {
    "type": "list",
    "item": "div.thread",
    "fields": {"title": "a.title", "url": "a.title@href"},
}

DEFAULT_TOOLS = ["browser_open", "browser_status", "browser_navigate", "browser_read_html", "browser_close"]

NOT_OPEN_TEXT = "the main browser is not open. Call browser_open to open it."
GONE_TEXT = (
    "the main browser is gone: it closed or crashed. Call browser_open to open it again; "
    "it comes back as the same person."
)


# ---------------------------------------------------------------------------
# 真实 stdio mock MCP server(python -c 脚本;换行分隔 JSON-RPC,零外部依赖)
# ---------------------------------------------------------------------------

MCP_SERVER_SCRIPT = r"""
import asyncio, json, sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"

async def main():
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    if MODE == "die":  # 读走一条请求后直接退,保证 EOF 晚于客户端注册 pending
        await reader.readline()
        return
    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or "id" not in message:
            continue  # notification(如 notifications/initialized)
        mid = message["id"]
        method = message.get("method")
        if MODE == "error" and method == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "Method not found: tools/list"}}) + "\n")
            sys.stdout.flush()
            continue
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "mock-stealth", "version": "0.0.1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "browser_open", "inputSchema": {}},
                                 {"name": "browser_navigate", "inputSchema": {}}]}
        elif method == "tools/call":
            if MODE == "slow":
                await asyncio.sleep(5)
            name = message["params"]["name"]
            args = json.dumps(message["params"].get("arguments", {}), sort_keys=True)
            result = {"content": [{"type": "text", "text": f"called {name} {args}"}], "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        sys.stdout.flush()

asyncio.run(main())
"""


def make_stdio_client(mode: str, timeout: float = 30.0) -> McpClient:
    return McpClient(command=[sys.executable, "-c", MCP_SERVER_SCRIPT, mode], timeout=timeout)


def test_mcp_client_stdio_protocol_round_trip():
    """协议往返:initialize 握手 / tools/list / tools/call 逐帧走通(stdio 子进程)."""

    async def scenario():
        client = make_stdio_client("ok")
        try:
            await client.connect()
            assert client.server_info == {"name": "mock-stealth", "version": "0.0.1"}
            assert await client.list_tools() == ["browser_open", "browser_navigate"]
            answer = await client.call_tool("browser_navigate", {"url": "https://x.example/"})
            assert "browser_navigate" in answer
            assert "https://x.example/" in answer
        finally:
            await client.close()

    run(scenario())


def test_mcp_client_jsonrpc_error_is_structured():
    async def scenario():
        client = make_stdio_client("error")
        try:
            await client.connect()
            with pytest.raises(McpError) as excinfo:
                await client.list_tools()
            assert excinfo.value.error_type == "mcp_protocol_error"
            assert "-32601" in str(excinfo.value)
        finally:
            await client.close()

    run(scenario())


def test_mcp_client_server_death_is_structured():
    async def scenario():
        client = make_stdio_client("die")
        with pytest.raises(McpError) as excinfo:
            await client.connect()
        assert excinfo.value.error_type == "mcp_server_died"
        await client.close()

    run(scenario())


def test_mcp_client_timeout_is_structured():
    async def scenario():
        client = make_stdio_client("slow", timeout=0.3)
        await client.connect()
        try:
            assert await client.list_tools()  # 握手/枚举不受影响
            with pytest.raises(McpError) as excinfo:
                await client.call_tool("browser_navigate", {"url": "https://x.example/"})
            assert excinfo.value.error_type == "timeout"
        finally:
            await client.close()

    run(scenario())


# ---------------------------------------------------------------------------
# streamable-HTTP 传输(注入 MockTransport:JSON 与 SSE 两种响应帧)
# ---------------------------------------------------------------------------


def _http_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_mcp_http_json_round_trip():
    """application/json 帧:POST initialize/tools/list/tools/call 全部走通."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx.Response(202)  # notification(notifications/initialized)无响应体
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": payload["id"]}
        if payload["method"] == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mock-http", "version": "1.0"},
            }
        elif payload["method"] == "tools/list":
            response["result"] = {"tools": [{"name": "browser_open"}]}
        else:
            response["result"] = {"content": [{"type": "text", "text": "pong"}]}
        return httpx.Response(200, json=response)

    async def scenario():
        http = _http_client(handler)
        client = McpClient(url="http://127.0.0.1:8766/mcp", timeout=5, http=http)
        try:
            await client.connect()
            assert client.server_info == {"name": "mock-http", "version": "1.0"}
            assert await client.list_tools() == ["browser_open"]
            assert await client.call_tool("browser_evaluate", {"expression": "1+1"}) == "pong"
        finally:
            await client.close()
            await http.aclose()

    run(scenario())


def test_mcp_http_sse_round_trip():
    """text/event-stream 帧:按 id 匹配 data: 行,忽略无 id 通知帧."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx.Response(202)
        result = {"tools": [{"name": "browser_open"}, {"name": "browser_navigate"}]}
        body = (
            'data: {"jsonrpc":"2.0","method":"notifications/noise"}\n\n'
            + "data: "
            + json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result})
            + "\n\n"
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    async def scenario():
        http = _http_client(handler)
        client = McpClient(url="http://127.0.0.1:8766/mcp", timeout=5, http=http)
        try:
            await client.connect()
            assert await client.list_tools() == ["browser_open", "browser_navigate"]
        finally:
            await client.close()
            await http.aclose()

    run(scenario())


def test_mcp_http_unreachable_is_server_missing():
    async def refusing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def scenario():
        client = McpClient(url="http://127.0.0.1:8766/mcp", timeout=5, http=_http_client(refusing))
        with pytest.raises(McpError) as excinfo:
            await client.connect()
        assert excinfo.value.error_type == "mcp_server_missing"
        assert INSTALL_HINT.split("(")[0].strip() in str(excinfo.value)
        await client.close()

    run(scenario())


def test_mcp_client_requires_command_or_url():
    with pytest.raises(ValueError):
        McpClient()


# ---------------------------------------------------------------------------
# 假 McpClient(注入):引擎级采集路径
# ---------------------------------------------------------------------------


class FakeMcp:
    """Duck-typed McpClient double:记录全部工具调用,答案按脚本弹出."""

    def __init__(
        self,
        *,
        tools: list[str] | None = None,
        status_text: str = NOT_OPEN_TEXT,
        status_as_tool_error: bool = False,
        navigate_answers: list[str] | None = None,
        html_answers: list[str] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.tools = list(tools) if tools is not None else list(DEFAULT_TOOLS)
        self.status_text = status_text
        self.status_as_tool_error = status_as_tool_error
        self.navigate_answers = list(navigate_answers or [])
        self.html_answers = list(html_answers or [])
        self.connect_error = connect_error
        self.calls: list[tuple[str, dict]] = []
        self.init_kwargs: dict = {}
        self.server_info = {"name": "mock-stealth", "version": "0.0.1"}
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def list_tools(self) -> list[str]:
        return list(self.tools)

    async def call_tool(self, name: str, arguments: dict | None = None) -> str:
        arguments = dict(arguments or {})
        self.calls.append((name, arguments))
        if name == "browser_status":
            if self.status_as_tool_error:
                # 实测 invisible_playwright_mcp 的「未开」回答是 isError 工具结果。
                raise McpError(
                    f"MCP 工具执行失败 tool=browser_status: Error executing tool browser_status: "
                    f"{self.status_text}",
                    error_type="mcp_tool_error",
                )
            return self.status_text
        if name == "browser_navigate":
            if self.navigate_answers:
                return self.navigate_answers.pop(0)
            return f"HTTP 200 {arguments.get('url', '')}"
        if name == "browser_read_html":
            return self.html_answers.pop(0) if self.html_answers else STEALTH_HTML
        return "ok"

    async def close(self) -> None:
        self.closed = True

    def names(self) -> list[str]:
        return [name for name, _arguments in self.calls]


def install_fake_mcp(monkeypatch: pytest.MonkeyPatch, fake: FakeMcp) -> FakeMcp:
    def factory(**kwargs):
        fake.init_kwargs = kwargs
        return fake

    monkeypatch.setattr(stealth, "McpClient", factory)
    return fake


def test_fetch_list_extract_via_mcp_tools(monkeypatch):
    fake = install_fake_mcp(monkeypatch, FakeMcp(html_answers=[STEALTH_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="stealth_browser", url=SITE_URL, extract=LIST_EXTRACT)

    items = run(StealthBrowserEngine(source, context).fetch())

    assert items == [
        {"title": "隐身帖子一", "url": f"https://{HOST}/t/1"},
        {"title": "隐身帖子二", "url": f"https://{HOST}/t/2"},
    ]
    assert fake.connected and fake.closed
    assert fake.names() == [
        "browser_status",
        "browser_open",
        "browser_navigate",
        "browser_read_html",
        "browser_close",
    ]
    assert fake.calls[2][1] == {"url": SITE_URL, "wait_until": "domcontentloaded"}
    assert fake.calls[3][1] == {"mode": "full"}
    assert fake.init_kwargs["command"] == list(DEFAULT_COMMAND)  # 默认 stdio 自启


def test_fetch_without_extract_auto_structures(monkeypatch):
    """无 extract 兜底 {url, title, content},与 L3/L4 同形."""
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[STEALTH_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(engine="stealth_browser", url=SITE_URL)  # 无 extract

    items = run(StealthBrowserEngine(source, context).fetch())

    assert items == [{"url": SITE_URL, "title": "盾源标题", "content": "隐身帖子一 隐身帖子二"}]


def test_browser_reused_when_status_reports_open(monkeypatch):
    """browser_status 回答无 not open/is gone 字样 → 复用,不 open 也不代关."""
    fake = install_fake_mcp(
        monkeypatch,
        FakeMcp(
            status_text="browsers: main on https://shielded.example.com/hot (focus)",
            html_answers=[STEALTH_HTML, STEALTH_HTML.replace("隐身帖子", "次页帖子")],
        ),
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="stealth_browser",
        url=f"{SITE_URL}?page={{page}}",
        extract=LIST_EXTRACT,
        pagination={"mode": "template", "max_pages": 2},
    )

    items = run(StealthBrowserEngine(source, context).fetch())

    assert len(items) == 4
    names = fake.names()
    assert "browser_open" not in names  # 已开实例复用
    assert "browser_close" not in names  # 服务器的实例不代关
    assert names.count("browser_navigate") == 2


def test_browser_opened_when_gone_and_closed_after(monkeypatch):
    """status 回答 is gone → 重新 open;本引擎 open 的实例收尾 close."""
    fake = install_fake_mcp(
        monkeypatch,
        FakeMcp(
            status_text=GONE_TEXT,
            html_answers=[STEALTH_HTML, STEALTH_HTML],
        ),
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="stealth_browser",
        url=f"{SITE_URL}?page={{page}}",
        extract=LIST_EXTRACT,
        pagination={"mode": "template", "max_pages": 2},
    )

    items = run(StealthBrowserEngine(source, context).fetch())

    assert len(items) == 4
    names = fake.names()
    assert names.count("browser_open") == 1  # 两个目标页共用一次 open(实例复用)
    assert names[-1] == "browser_close"


def test_browser_status_answered_as_is_error_still_opens(monkeypatch):
    """回归(实测服务器行为):「未开」回答被标成 isError 工具结果 →
    按句子识别为需要 open,不误报 mcp_tool_error 硬失败."""
    fake = install_fake_mcp(
        monkeypatch, FakeMcp(status_as_tool_error=True, html_answers=[STEALTH_HTML])
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    items = run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert items == [{"url": SITE_URL, "title": "盾源标题", "content": "隐身帖子一 隐身帖子二"}]
    assert fake.names()[1] == "browser_open"  # status(报错句)-> open -> navigate…
    assert fake.names()[-1] == "browser_close"


def test_open_arguments_carry_seed_profile_and_pool_proxy(monkeypatch):
    """seed/profile 显式配置与 pool: 代理解析后的 upstream 必须到达 browser_open
    (显式代理意图不得静默丢弃)."""
    fake = install_fake_mcp(monkeypatch, FakeMcp(html_answers=[STEALTH_HTML]))
    real_client = httpx.AsyncClient

    def pool_client_factory(**kwargs):
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 互斥
        kwargs.setdefault(
            "transport",
            httpx.MockTransport(make_handler(lambda r: httpx.Response(404, text=""))),
        )
        return real_client(**kwargs)

    monkeypatch.setattr("shishi.engines.fetch_base.httpx.AsyncClient", pool_client_factory)
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    context.proxy_pools = load_proxy_pools({"pools": {"main": "http://proxy.example.com:8080"}})
    source = make_source(
        engine="stealth_browser",
        url=SITE_URL,
        extract=LIST_EXTRACT,
        proxy="pool:main",
        engine_options={"stealth_browser": {"seed": 4242, "profile": "/tmp/myia-profile"}},
    )

    run(StealthBrowserEngine(source, context).fetch())

    open_call = next(call for name, call in fake.calls if name == "browser_open")
    assert open_call == {
        "seed": 4242,
        "profile": "/tmp/myia-profile",
        "proxy": "http://proxy.example.com:8080",
    }


# ---------------------------------------------------------------------------
# cookie 注入往返(keychain: 凭据)
# ---------------------------------------------------------------------------


def make_cookie_source(**overrides):
    defaults = {
        "engine": "stealth_browser",
        "url": SITE_URL,
        "extract": LIST_EXTRACT,
        "headers": {"Cookie": "keychain:myia/demo/linuxdo_cookie"},
    }
    defaults.update(overrides)
    return make_source(**defaults)


def make_keychain_context(monkeypatch, client):
    context, _ = make_context(client)
    backend = InMemoryKeychainBackend()
    backend.set_password(SECRET_SERVICE, "myia/demo/linuxdo_cookie", "sid=abc; theme=dark")
    context.keychain_backend = backend
    return context


def test_cookie_injection_round_trip_from_keychain(monkeypatch):
    """keychain: 凭据 -> browser_set_cookies 参数(name/value/domain/path),
    注入顺序 open -> cookies -> navigate;同 host 多页只注一次."""
    fake = install_fake_mcp(
        monkeypatch,
        FakeMcp(
            tools=DEFAULT_TOOLS + [COOKIE_TOOL_NAME],
            html_answers=[STEALTH_HTML, STEALTH_HTML],
        ),
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context = make_keychain_context(monkeypatch, client)
    source = make_cookie_source(
        url=f"{SITE_URL}?page={{page}}", pagination={"mode": "template", "max_pages": 2}
    )

    items = run(StealthBrowserEngine(source, context).fetch())

    assert len(items) == 4
    names = fake.names()
    assert names.count(COOKIE_TOOL_NAME) == 1  # 同 host 只注入一次
    index_open = names.index("browser_open")
    index_cookies = names.index(COOKIE_TOOL_NAME)
    index_navigate = names.index("browser_navigate")
    assert index_open < index_cookies < index_navigate  # 开浏览器 -> 注 cookie -> 再导航
    assert fake.calls[index_cookies][1] == {
        "cookies": [
            {"name": "sid", "value": "abc", "domain": HOST, "path": "/"},
            {"name": "theme", "value": "dark", "domain": HOST, "path": "/"},
        ]
    }


def test_cookie_tool_missing_is_structured_and_still_cleans_up(monkeypatch):
    """服务器未暴露 cookie 注入工具 -> cookie_unsupported(列出已暴露工具);清理照常."""
    fake = install_fake_mcp(monkeypatch, FakeMcp())  # 默认工具面无 browser_set_cookies
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context = make_keychain_context(monkeypatch, client)

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(make_cookie_source(), context).fetch())

    assert excinfo.value.error_type == "cookie_unsupported"
    assert COOKIE_TOOL_NAME in str(excinfo.value)
    assert "browser_navigate" in str(excinfo.value)  # 已暴露工具列表可见
    assert fake.closed  # 失败路径也把本引擎 open 的实例关掉


def test_plain_cookie_header_is_rejected_by_schema():
    """凭据禁明文:schema 层即拒(Cookie 键 + 明文值),引擎根本不会构建.

    直接 model_validate 时 pydantic 把校验器里的 SchemaValueError 包成
    ValidationError(两者都是 ValueError 子类);品类文件加载路径的
    LoadError/credential_plaintext 形态已由 test_schema.py 覆盖.
    """
    with pytest.raises(ValueError):
        make_source(engine="stealth_browser", url=SITE_URL, headers={"Cookie": "sid=plaintext"})


# ---------------------------------------------------------------------------
# 护栏:页面预算 / 未装 MCP 服务 / HTTP 状态 / 风控分类
# ---------------------------------------------------------------------------


def test_page_budget_exhausted_before_any_connection(monkeypatch):
    """单 run 页面预算:超预算在建立 MCP 连接前结构化熔断."""

    def factory(**kwargs):  # pragma: no cover - 熔断点之后不应被调用
        raise AssertionError("页面预算熔断后不应建立 MCP 连接")

    monkeypatch.setattr(stealth, "McpClient", factory)
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="stealth_browser",
        url=f"{SITE_URL}?page={{page}}",
        extract=LIST_EXTRACT,
        pagination={"mode": "template", "max_pages": 3},
        engine_options={"stealth_browser": {"max_pages": 2}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(source, context).fetch())

    assert excinfo.value.error_type == "page_budget_exhausted"
    assert "max_pages" in str(excinfo.value)


def test_missing_mcp_server_is_structured(monkeypatch):
    """未装/未启动 MCP 服务(进程起不来)→ mcp_server_missing,含安装指引."""
    install_fake_mcp(
        monkeypatch,
        FakeMcp(connect_error=FileNotFoundError(2, "No such file or directory: 'uvx'")),
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert excinfo.value.error_type == "mcp_server_missing"
    assert "uvx invisible-playwright-mcp" in str(excinfo.value)


def test_navigate_http_403_is_structured(monkeypatch):
    """导航回答携带 HTTP 403 → http_403,auto 链据此降级."""
    install_fake_mcp(monkeypatch, FakeMcp(navigate_answers=[f"HTTP 403 {SITE_URL}"]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert excinfo.value.error_type == "http_403"


PHONE_WALL_HTML = "<html><body><h1>安全验证</h1><p>请输入手机验证码</p></body></html>"
HUMAN_REVIEW_HTML = "<html><body><p>该内容正在人工审核,请先完成真人审核</p></body></html>"
CF_CHALLENGE_HTML = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Checking your browser before accessing the site</body></html>"
)
HCAPTCHA_HTML = "<html><body><div class='h-captcha' data-sitekey='public-sitekey'></div></body></html>"


def test_phone_verification_wall_refused_without_attempt(monkeypatch):
    """手机验证码硬墙:零尝试零等待,结构化 captcha_phone_verification(不做绕过)."""
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[PHONE_WALL_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, clock = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert excinfo.value.error_type == "captcha_phone_verification"
    assert "不做绕过" in str(excinfo.value)
    assert clock.sleeps == []  # 硬墙不做任何沉降/重试尝试


def test_human_review_wall_refused_without_attempt(monkeypatch):
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[HUMAN_REVIEW_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert excinfo.value.error_type == "captcha_human_review"
    assert "不做绕过" in str(excinfo.value)


def test_basic_shield_settles_retries_and_passes(monkeypatch):
    """CF 质询页等基础盾:沉降等待后重导航一次即通过(隐身指纹即是尝试)."""
    fake = install_fake_mcp(
        monkeypatch, FakeMcp(html_answers=[CF_CHALLENGE_HTML, STEALTH_HTML])
    )
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, clock = make_context(client)

    items = run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert items == [{"url": SITE_URL, "title": "盾源标题", "content": "隐身帖子一 隐身帖子二"}]
    assert clock.sleeps == [8.0]  # 默认 challenge_settle_seconds
    assert fake.names().count("browser_navigate") == 2  # 沉降后重导航一次


def test_basic_shield_still_blocked_after_settle(monkeypatch):
    """基础盾沉降后仍未通过 -> captcha_challenge(不做绕过,交由降级链)."""
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[HCAPTCHA_HTML, HCAPTCHA_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    context, _ = make_context(client)
    source = make_source(
        engine="stealth_browser",
        url=SITE_URL,
        engine_options={"stealth_browser": {"challenge_settle_seconds": 0}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(source, context).fetch())

    assert excinfo.value.error_type == "captcha_challenge"
    assert "不做绕过" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 配置 fail-fast 与纯函数
# ---------------------------------------------------------------------------


def test_invalid_options_are_structured_errors():
    """engine_options.stealth_browser 拼错 → 先报配置错,零连接零抓取."""
    client = make_client(make_handler(lambda r: pytest.fail("配置错误时不应发起任何请求")))

    cases = (
        ({"transport": "websocket"}, "invalid_transport"),
        ({"command": []}, "invalid_command"),
        ({"command": "uvx"}, "invalid_command"),
        ({"command": ["uvx", ""]}, "invalid_command"),
        ({"url": "ftp://127.0.0.1:8766/mcp"}, "invalid_url"),
        ({"timeout": "soon"}, "invalid_timeout"),
        ({"timeout": True}, "invalid_timeout"),  # bool 是 int 子类,显式拒绝
        ({"max_pages": 0}, "invalid_max_pages"),
        ({"max_pages": True}, "invalid_max_pages"),
        ({"seed": "4242"}, "invalid_seed"),
        ({"seed": True}, "invalid_seed"),
        ({"profile": "  "}, "invalid_profile"),
        ({"wait_until": "forever"}, "invalid_wait_until"),
        ({"challenge_settle_seconds": -1}, "invalid_settle_seconds"),
    )
    for options, expected_type in cases:
        context, _ = make_context(client)
        source = make_source(
            engine="stealth_browser",
            url=SITE_URL,
            extract=LIST_EXTRACT,
            engine_options={"stealth_browser": options},
        )
        with pytest.raises(FetchError) as excinfo:
            run(StealthBrowserEngine(source, context).fetch())
        assert excinfo.value.error_type == expected_type, options


def test_json_path_extract_rejected_for_degrade():
    """json_path 属 L1 语义:L5 拒绝(extract_unsupported)让 auto 链正确降级."""
    client = make_client(make_handler(lambda r: pytest.fail("extract 不支持时不应发起抓取")))
    context, _ = make_context(client)
    source = make_source(
        engine="stealth_browser",
        url=SITE_URL,
        extract={"type": "json_path", "fields": {"title": "$.t", "url": "$.u"}},
    )

    with pytest.raises(FetchError) as excinfo:
        run(StealthBrowserEngine(source, context).fetch())
    assert excinfo.value.error_type == "extract_unsupported"


def test_robots_disallowed_costs_no_browser(monkeypatch):
    """robots 拒绝:零 MCP 连接、零浏览器调用(礼貌先于花钱)."""
    fake = install_fake_mcp(monkeypatch, FakeMcp())
    client = make_client(
        make_handler(lambda r: pytest.fail("robots 拒绝时不应发起站点请求"), robots="User-agent: *\nDisallow: /")
    )
    context, _ = make_context(client)

    with pytest.raises(RobotsDisallowedError):
        run(StealthBrowserEngine(make_source(engine="stealth_browser", url=SITE_URL), context).fetch())

    assert fake.calls == []
    assert not fake.connected


def test_parse_cookie_header_skips_malformed_fragments():
    cookies = parse_cookie_header("sid=abc; broken; =novalue; theme=dark", HOST)
    assert cookies == [
        {"name": "sid", "value": "abc", "domain": HOST, "path": "/"},
        {"name": "theme", "value": "dark", "domain": HOST, "path": "/"},
    ]


def test_classify_risk_control_hard_wall_wins_over_challenge():
    """硬墙(手机验证码)优先于基础盾(一票否决)."""
    mixed = PHONE_WALL_HTML + "<div class='h-captcha'></div>"
    assert classify_risk_control(mixed) == "phone_verification"
    assert classify_risk_control(HCAPTCHA_HTML) == "hcaptcha"
    assert classify_risk_control(CF_CHALLENGE_HTML) == "cloudflare_interstitial"
    assert classify_risk_control(STEALTH_HTML) is None


def test_parse_navigate_status():
    assert parse_navigate_status("HTTP 200 https://x.example/") == 200
    assert parse_navigate_status("status: 503 service unavailable") == 503
    assert parse_navigate_status("到达 状态码 403 的登录墙") == 403
    assert parse_navigate_status("arrived at https://x.example/hot?page=404") is None  # URL 数字不算状态
    assert parse_navigate_status("landed without a status") is None


# ---------------------------------------------------------------------------
# auto 链 L5 位置:L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser(链尾 llm_browser)
# ---------------------------------------------------------------------------


def make_fail_engine(name: str, error_type: str) -> type[BaseEngine]:
    """Stand-in engine failing with a distinct structured error class."""

    class FailEngine(BaseEngine):
        LAYER = "fail"
        ENGINE_NAME = name
        REQUIRES_EXTRACT = False
        SUPPORTED_EXTRACT_TYPES = ("list", "item")

        async def _fetch_impl(self) -> list[dict]:
            raise FetchError(f"{name} 挂了", error_type=error_type)

    return FailEngine


def test_auto_chain_stealth_layer():
    """链终形态(v0.4 全量落地,yaml-schema 规则 5):stealth_browser 为 L5,
    后继 llm_browser(L6,烧 token 兜底)为链尾."""
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
    assert registry.auto_degrade("crawl4ai") == [
        "crawl4ai", "firecrawl", "scrapling", "stealth_browser", "llm_browser",
    ]
    assert registry.auto_degrade("scrapling") == ["scrapling", "stealth_browser", "llm_browser"]
    assert registry.auto_degrade("stealth_browser") == ["stealth_browser", "llm_browser"]


def test_registry_resolves_stealth_engine_and_schedule_table_empty(monkeypatch):
    assert registry.resolve_engine("stealth_browser") is StealthBrowserEngine
    assert "stealth_browser" not in registry.ENGINE_SCHEDULED_VERSIONS
    # v0.4 全量落地:llm_browser 已实装为链尾,排期表清空;
    # 结构化拒绝分支以合成排期名保持覆盖(同 test_registry.py)。
    assert registry.ENGINE_SCHEDULED_VERSIONS == {}
    monkeypatch.setitem(registry.ENGINE_SCHEDULED_VERSIONS, "time_machine", "v9.9")
    with pytest.raises(EngineNotAvailableError) as excinfo:
        registry.auto_degrade("time_machine")
    assert excinfo.value.scheduled_version == "v9.9"
    assert excinfo.value.engine == "time_machine"


def test_auto_chain_first_five_fail_degrade_to_stealth(monkeypatch, engine_store):
    """链序集成:L1/L2 无 extract 拒载 → crawl4ai/firecrawl/scrapling 假引擎失败 →
    stealth_browser(假 MCP)兜底成功,hint 回写."""
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "crawl4ai", lambda: make_fail_engine("crawl4ai", "crawl4ai_down"))
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "firecrawl", lambda: make_fail_engine("firecrawl", "firecrawl_down"))
    monkeypatch.setitem(registry.ENGINE_REGISTRY, "scrapling", lambda: make_fail_engine("scrapling", "scrapling_down"))
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[STEALTH_HTML]))

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="waf blocked")  # L1 无请求;L2 被拒

    client = make_client(make_handler(responder))
    source = make_source(engine="auto", url=SITE_URL, retry=0)  # 无 extract
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine == "stealth_browser"
    assert outcome.items == [{"url": SITE_URL, "title": "盾源标题", "content": "隐身帖子一 隐身帖子二"}]
    assert [failure.error_type for failure in outcome.failures] == [
        "extract_required",  # L1 需要 extract(无 extract 即拒)
        "extract_required",  # L2 同样需要 extract
        "crawl4ai_down",
        "firecrawl_down",
        "scrapling_down",
    ]
    assert engine_store.get_engine_hint(SITE_URL) == "stealth_browser"  # 成功选择回写 hint


def test_explicit_stealth_source_runs_and_writes_hint(monkeypatch, engine_store):
    """显式 engine: stealth_browser:L5 成功即停(链尾 llm_browser 仅兜底),hint 回写."""
    install_fake_mcp(monkeypatch, FakeMcp(html_answers=[STEALTH_HTML]))
    client = make_client(make_handler(lambda r: httpx.Response(404, text="")))
    source = make_source(engine="stealth_browser", url=SITE_URL)
    context, _ = make_context(client, store=engine_store)

    outcome = run(fetch_source(source, context))

    assert outcome.engine == "stealth_browser"
    assert outcome.failures == []
    assert outcome.skipped is False
    assert engine_store.get_engine_hint(SITE_URL) == "stealth_browser"
