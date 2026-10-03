"""L5 engine: anti-detection browser via invisible_playwright_mcp (built-in MCP client).

Contract (PRD 10-01-v04-engine-stealth):

- **内置 MCP 客户端**:标准 MCP 协议子集(JSON-RPC 2.0;stdio 换行分帧 /
  streamable-HTTP POST),零外部 SDK —— ``initialize`` 握手 +
  ``notifications/initialized`` + ``tools/list`` + ``tools/call`` 即全部协议面。
  服务器为 feder-cr/invisible_playwright_mcp(MIT)或任何同工具面的
  Playwright-MCP 变体;工具名沿用 Microsoft Playwright MCP 词汇:
  ``browser_open`` / ``browser_status`` / ``browser_navigate`` /
  ``browser_read_html`` / ``browser_close``,cookie 注入探针
  ``browser_set_cookies``(见下)。
- **浏览器实例复用**:连接后先 ``browser_status`` 探测 —— 服务器回答不含
  "not open" / "is gone"(其文档化句子:the main browser is not open… /
  the main browser is gone…)即复用已开实例不再 open(http transport 下
  服务器持有浏览器,跨 run 复用;stdio 下浏览器随客户端进程生灭,run 内
  逐页复用同一实例);只有本引擎自己 open 的实例才会被 ``browser_close``
  (服务器持有的浏览器不抢着关)。
- **单 run 页面预算**:模板展开后的目标页数超过
  ``engine_options.stealth_browser.max_pages``(默认 10)即在建立连接前
  结构化报错(``page_budget_exhausted``)—— 反检测浏览器是最贵的采集层,
  超预算必须显式升预算而不是默默烧钱。
- **cookie 注入登录墙**:源 ``headers.Cookie``(凭据经 ``env:``/``keychain:``
  引用,基座已解析)按 host 注入 —— 每个新 host 首次导航前调
  ``browser_set_cookies``(参数 ``{"cookies": [{name, value, domain, path}]}``,
  值不落日志);连着的 MCP 服务器未暴露该工具时结构化 ``cookie_unsupported``
  (invisible_playwright_mcp 当前工具面无 cookie 工具,登录墙场景需要支持
  该工具的服务器/分支)。
- **验证码边界(安全基线,不做绕过)**:页面命中 手机验证码/短信 OTP /
  真人人工审核 类标记 → 结构化 ``captcha_phone_verification`` /
  ``captcha_human_review``,零尝试直接报;hCaptcha / Cloudflare Turnstile /
  CF 质询页等**基础盾** → 隐身指纹本身即是尝试:沉降等待
  (``challenge_settle_seconds``,默认 8s)后重导航一次,仍未通过 →
  ``captcha_challenge``。导航回答携带 HTTP >= 400 → ``http_<status>``。
- **transport**:``stdio``(默认,自启 ``uvx invisible-playwright-mcp``,
  命令缺失 → ``mcp_server_missing`` 含安装提示)或 ``http``(连已运行的
  ``STEALTHFOX_MCP_TRANSPORT=http`` 服务,默认 ``http://127.0.0.1:8766/mcp``)。
- extraction reuses the L2 field grammar via fetch_base ``extract_html``
  (list/item,``a.title@href`` 属性或文本);无 extract 自动结构化兜底
  ``{url, title, content}``(yaml-schema 规则 7,与 L3/L4 同形);
  ``json_path`` 属 L1 语义,此处拒绝让 auto 链正确降级。
- 礼貌原语(robots/qps)作用于目标站点(浏览器代抓),与 crawl4ai/scrapling
  同约定;headers 中只有 Cookie 被消费(注入),UA 属浏览器指纹不透传;
  源配 ``pool:`` 代理时解析后的 upstream 经 ``browser_open`` 的 ``proxy``
  参数透传(显式代理意图不得静默丢弃;值不落日志)。

Raises:
    FetchError: 配置错(``invalid_*``)/ 页面预算(``page_budget_exhausted``)/
        MCP 服务缺失或未启动(``mcp_server_missing``)/ 协议错误
        (``mcp_protocol_error``)/ 工具失败(``mcp_tool_error``)/
        服务器退出(``mcp_server_died``)/ 单工具预算(``timeout``)/
        HTTP >= 400(``http_<status>``)/ 风控墙(``captcha_challenge`` /
        ``captcha_phone_verification`` / ``captcha_human_review``)/
        cookie 工具缺失(``cookie_unsupported``)。
    RobotsDisallowedError: robots.txt 禁止抓取(基座抛出)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from selectolax.parser import HTMLParser

from shishi.engines.fetch_base import BaseEngine, FetchError, extract_html

logger = logging.getLogger(__name__)

LAYER = "L5"

#: stdio transport 默认启动命令(invisible_playwright_mcp 文档注册方式;
#: 首次启动由 uvx 拉取包,服务器再自取浏览器内核)。
DEFAULT_COMMAND: tuple[str, ...] = ("uvx", "invisible-playwright-mcp")

#: http transport 默认端点(STEALTHFOX_MCP_PORT 默认 8766;/mcp 为
#: streamable-HTTP 约定路径)。
DEFAULT_HTTP_URL = "http://127.0.0.1:8766/mcp"

#: ``mcp_server_missing`` 错误里原样给出的启动/安装指引(doctor 消费原文)。
INSTALL_HINT = (
    "uvx invisible-playwright-mcp(需先安装 uv;"
    "或以 STEALTHFOX_MCP_TRANSPORT=http 自启动后改用 transport: http 连接)"
)

#: 每次工具调用的运行预算(秒)。隐身浏览器过盾 + 等质询比静态页慢,
#: 默认宽于管线 HTTP 默认 30s。
DEFAULT_TOOL_TIMEOUT_SECONDS = 90.0

#: 单 run 页面预算默认值(反检测浏览器是最贵的采集层)。
DEFAULT_MAX_PAGES = 10

#: 基础盾沉降等待默认值(质询自动通过需要的时间)。
DEFAULT_SETTLE_SECONDS = 8.0

WAIT_UNTIL_MODES: tuple[str, ...] = ("domcontentloaded", "load", "networkidle")
TRANSPORTS: tuple[str, ...] = ("stdio", "http")

#: cookie 注入工具探针名(MCP 工具面按名发现;服务器不支持即结构化报错)。
COOKIE_TOOL_NAME = "browser_set_cookies"

#: MCP 客户端标识(initialize 握手用)。
MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "myia"

__all__ = [
    "CLIENT_NAME",
    "COOKIE_TOOL_NAME",
    "DEFAULT_COMMAND",
    "DEFAULT_HTTP_URL",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_SETTLE_SECONDS",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "INSTALL_HINT",
    "LAYER",
    "MCP_PROTOCOL_VERSION",
    "TRANSPORTS",
    "WAIT_UNTIL_MODES",
    "McpClient",
    "McpError",
    "StealthBrowserEngine",
    "parse_cookie_header",
]


# ---------------------------------------------------------------------------
# 内置 MCP 客户端(JSON-RPC 2.0 子集:initialize / tools/list / tools/call)
# ---------------------------------------------------------------------------


class McpError(FetchError):
    """Structured MCP round-trip failure(error_type 直接进 EngineFailure)。"""


def _content_text(content: Any) -> str:
    """MCP ``tools/call`` 结果的 content 块 -> 纯文本(只拼 text 块)。"""
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )


class McpClient:
    """Minimal async MCP client: one request in flight, stdio or streamable HTTP.

    只实现本引擎需要的协议子集(标准 MCP 协议,JSON-RPC 2.0):``initialize``
    握手、``notifications/initialized``、``tools/list``、``tools/call``;
    通知帧(无 id)直接忽略。stdio 帧为换行分隔 JSON;HTTP 为
    streamable-HTTP POST(整包缓冲,application/json 或 text/event-stream
    两种响应都收)。测试可注入预构建的 ``http`` 客户端(MockTransport)或
    直接整体替换本类。

    Args:
        command: stdio 模式的启动 argv(与 ``url`` 二选一,command 优先)。
        url: streamable-HTTP 端点。
        timeout: 单次 JSON-RPC 往返预算(秒),超时为 ``timeout`` 分类。
        http: 注入的 httpx.AsyncClient(http 模式;测试用 MockTransport,
            ``None`` = 内部自建并在 :meth:`close` 关闭)。

    Raises:
        McpError: ``mcp_server_missing``(进程/端点起不来)、
            ``mcp_protocol_error``(JSON-RPC 错误响应/坏帧)、
            ``mcp_server_died``(stdio 对端退出)、``timeout``(预算耗尽)。
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        url: str | None = None,
        timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if command is None and url is None:
            raise ValueError("McpClient 需要 command(stdio)或 url(http)之一")
        self._command = command
        self._url = url
        self._timeout = timeout
        self._own_http = http is None and command is None
        self._http = http
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        """Start the transport and run the MCP initialize handshake."""
        if self._command is not None:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,  # 噪声日志直接丢弃,防管道塞死
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise McpError(
                    f"MCP 服务器无法启动 command={list(self._command)}: {exc};"
                    f"请先安装/启动 MCP 服务({INSTALL_HINT})",
                    error_type="mcp_server_missing",
                ) from exc
            self._reader_task = asyncio.create_task(self._read_stdio())
        elif self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        result = await self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "0.1.0"},
            },
        )
        self.server_info = result.get("serverInfo") if isinstance(result, dict) else {}
        if not isinstance(self.server_info, dict):
            self.server_info = {}
        await self.notify("notifications/initialized")

    async def close(self) -> None:
        """Tear the transport down (进程 terminate -> kill 兜底;HTTP 客户端关闭)."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if self._proc is not None:
            if self._proc.returncode is None:
                with suppress(ProcessLookupError):
                    self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), 5)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        self._proc.kill()
                    await self._proc.wait()
            self._proc = None
        if self._http is not None and self._own_http:
            await self._http.aclose()
        self._http = None
        self._pending.clear()

    # ------------------------------------------------------------------ rpc

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """One JSON-RPC request -> result;JSON-RPC error -> McpError(结构化)."""
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        send = self._send_stdio(payload) if self._command is not None else self._send_http(payload)
        try:
            return await asyncio.wait_for(send, self._timeout)
        except TimeoutError as exc:
            raise McpError(
                f"MCP 调用超时 method={method}(预算 {self._timeout}s)",
                error_type="timeout",
            ) from exc

    async def notify(self, method: str) -> None:
        """Fire a JSON-RPC notification(无 id,无响应;失败不重试)."""
        payload = {"jsonrpc": "2.0", "method": method}
        if self._command is not None:
            await self._write_line(payload)
            return
        assert self._http is not None and self._url is not None
        with suppress(httpx.HTTPError):
            await self._http.post(self._url, json=payload)

    async def list_tools(self) -> list[str]:
        """``tools/list`` -> 服务器暴露的工具名列表。"""
        result = await self.request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpError("MCP tools/list 响应缺少 tools 数组", error_type="mcp_protocol_error")
        return [tool["name"] for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """``tools/call`` -> 文本结果;``isError`` -> ``mcp_tool_error``。"""
        result = await self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise McpError(f"MCP tools/call 响应格式异常 tool={name}", error_type="mcp_protocol_error")
        if result.get("isError"):
            raise McpError(
                f"MCP 工具执行失败 tool={name}: {_content_text(result.get('content')) or '未知错误'}",
                error_type="mcp_tool_error",
            )
        return _content_text(result.get("content"))

    # ------------------------------------------------------------ transports

    async def _write_line(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("MCP stdio 通道未打开", error_type="mcp_server_died")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise McpError(
                "MCP 服务器进程已退出,无法写入请求;请检查服务器安装与启动日志",
                error_type="mcp_server_died",
            ) from exc

    async def _send_stdio(self, payload: dict[str, Any]) -> Any:
        """Write one framed request and await its response future(读循环回填)."""
        request_id = int(payload["id"])  # type: ignore[arg-type]
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write_line(payload)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _read_stdio(self) -> None:
        """Reader loop: newline-delimited JSON;notifications 忽略,id 匹配回填."""
        if self._proc is None or self._proc.stdout is None:  # pragma: no cover - connect 先建进程
            return
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break  # EOF:服务器进程退出
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行忽略(服务器噪声)
            if not isinstance(message, dict) or message.get("id") is None:
                continue
            future = self._pending.get(int(message["id"]))
            if future is None or future.done():
                continue
            error = message.get("error")
            if isinstance(error, dict):
                future.set_exception(
                    McpError(
                        f"MCP 服务器返回错误 code={error.get('code')}: {error.get('message')}",
                        error_type="mcp_protocol_error",
                    )
                )
            else:
                future.set_result(message.get("result") or {})
        dead = McpError(
            "MCP 服务器进程已退出(EOF),请求未得到响应;请检查服务器安装与启动日志",
            error_type="mcp_server_died",
        )
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(dead)

    async def _send_http(self, payload: dict[str, Any]) -> Any:
        """streamable-HTTP POST;application/json 或 SSE(data: 行)都收."""
        request_id = int(payload["id"])  # type: ignore[arg-type]
        if self._http is None or self._url is None:  # pragma: no cover - connect 先建
            raise McpError("MCP HTTP 通道未打开", error_type="mcp_protocol_error")
        try:
            response = await self._http.post(
                self._url,
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
        except httpx.HTTPError as exc:
            raise McpError(
                f"MCP 服务器无法连接 url={self._url}: {exc};请先启动 MCP 服务({INSTALL_HINT})",
                error_type="mcp_server_missing",
            ) from exc
        if response.status_code >= 400:
            raise McpError(
                f"MCP 端点返回 HTTP {response.status_code} url={self._url}",
                error_type="mcp_protocol_error",
            )
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                data = line[5:].strip() if line.startswith("data:") else ""
                if not data:
                    continue
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and message.get("id") == request_id:
                    return self._rpc_result(message)
            raise McpError("MCP SSE 流中没有匹配 id 的响应", error_type="mcp_protocol_error")
        try:
            message = response.json()
        except ValueError as exc:
            raise McpError("MCP HTTP 响应不是合法 JSON", error_type="mcp_protocol_error") from exc
        return self._rpc_result(message)

    @staticmethod
    def _rpc_result(message: Any) -> Any:
        if not isinstance(message, dict):
            raise McpError("MCP 响应帧不是对象", error_type="mcp_protocol_error")
        error = message.get("error")
        if isinstance(error, dict):
            raise McpError(
                f"MCP 服务器返回错误 code={error.get('code')}: {error.get('message')}",
                error_type="mcp_protocol_error",
            )
        return message.get("result") or {}


# ---------------------------------------------------------------------------
# Cookie / 风控标记解析
# ---------------------------------------------------------------------------


def parse_cookie_header(raw: str, domain: str) -> list[dict[str, str]]:
    """``name=value; name2=value2`` -> MCP cookie 注入参数(缺 name/= 的碎片跳过)."""
    cookies: list[dict[str, str]] = []
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not name.strip():
            continue
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"})
    return cookies


# 硬墙(手机验证码 / 真人审核):安全基线不做绕过,命中即结构化报错。
_HARD_WALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "phone_verification",
        re.compile(
            r"手机验证码|短信验证码|验证码短信|sms\s{0,10}code|phone\s{0,10}verification"
            r"|one[- ]time\s{0,5}(?:code|password)|\botp\b",
            re.IGNORECASE,
        ),
    ),
    (
        "human_review",
        re.compile(
            r"真人审核|人工审核|人工验证|真人验证|manual\s{0,5}review|human\s{0,5}review"
            r"|prove\s{0,3}you\s{0,3}(?:are|'re)\s{0,3}human",
            re.IGNORECASE,
        ),
    ),
)

# 基础盾:hCaptcha / Turnstile / CF 质询页 —— 隐身指纹尝试(沉降后重导航一次)。
_CHALLENGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hcaptcha", re.compile(r"hcaptcha|h-captcha", re.IGNORECASE)),
    ("turnstile", re.compile(r"challenges\.cloudflare\.com|cf-turnstile|turnstile", re.IGNORECASE)),
    (
        "cloudflare_interstitial",
        re.compile(
            r"just\s{0,3}a\s{0,3}moment|attention\s{0,3}required|checking your browser|安全检查|请稍候",
            re.IGNORECASE,
        ),
    ),
    ("generic_captcha", re.compile(r"captcha|人机验证|verify you are human", re.IGNORECASE)),
)

# browser_navigate 回答里的 HTTP 状态(限定 "HTTP/status/状态码 + 数字" 形态,
# 避免把 URL/正文里的普通三位数误当状态码)。
_NAV_STATUS_RE = re.compile(r"\b(?:HTTP|status|状态码?)\s*[:= ]\s*(\d{3})", re.IGNORECASE)


def classify_hard_wall(text: str) -> str | None:
    """文本 -> 硬墙类型(``phone_verification`` / ``human_review``);无命中 ``None``。

    硬墙(手机验证码/真人审核)是安全基线的一票否决项,与「基础盾可尝试」的
    :func:`classify_risk_control` 分开:L4 scrapling 复用本函数做同款零尝试拒绝
    (两引擎的安全边界必须一致,见 security-baseline)。
    """
    for kind, pattern in _HARD_WALL_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def classify_risk_control(page_html: str) -> str | None:
    """HTML -> 风控类型;硬墙优先于基础盾(硬墙一票否决),无命中为 ``None``。"""
    kind = classify_hard_wall(page_html)
    if kind is not None:
        return kind
    for kind, pattern in _CHALLENGE_PATTERNS:
        if pattern.search(page_html):
            return kind
    return None


def parse_navigate_status(answer: str) -> int | None:
    """``browser_navigate`` 回答 -> HTTP 状态(解析不到返回 ``None``,尽力而为)."""
    match = _NAV_STATUS_RE.search(answer or "")
    if match is None:
        return None
    status = int(match.group(1))
    return status if 100 <= status < 600 else None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Options:
    """Validated ``engine_options.stealth_browser`` snapshot(全量先于连接)."""

    transport: str
    command: tuple[str, ...]
    url: str
    timeout: float
    max_pages: int
    seed: int | None
    profile: str | None
    wait_until: str
    settle_seconds: float


class StealthBrowserEngine(BaseEngine):
    """Anti-detection real browser (invisible_playwright_mcp) behind a thin MCP client."""

    LAYER = "L5"
    ENGINE_NAME = "stealth_browser"
    REQUIRES_EXTRACT = False  # 无 extract 时自动结构化兜底(yaml-schema 规则 7)
    SUPPORTED_EXTRACT_TYPES = ("list", "item")  # json_path 属 L1 语义,拒绝让链降级

    # ------------------------------------------------------- configuration

    def _transport(self) -> str:
        value = self.engine_options().get("transport")
        if value is None:
            return "stdio"
        if value not in TRANSPORTS:
            raise FetchError(
                f"engine_options.stealth_browser.transport 应为 {'/'.join(TRANSPORTS)} 之一,当前为 {value!r}",
                error_type="invalid_transport",
            )
        return str(value)

    def _command(self) -> tuple[str, ...]:
        """stdio 启动 argv(逐项非空字符串校验)."""
        value = self.engine_options().get("command")
        if value is None:
            return DEFAULT_COMMAND
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise FetchError(
                f"engine_options.stealth_browser.command 应为非空字符串列表(argv),当前为 {value!r}",
                error_type="invalid_command",
            )
        return tuple(value)

    def _url(self) -> str:
        value = self.engine_options().get("url")
        if value is None:
            return DEFAULT_HTTP_URL
        if not isinstance(value, str) or urlsplit(value).scheme not in ("http", "https"):
            raise FetchError(
                f"engine_options.stealth_browser.url 应为 http(s) 端点,当前为 {value!r}",
                error_type="invalid_url",
            )
        return value

    def _timeout(self) -> float:
        value = self.engine_options().get("timeout")
        if value is None:
            return DEFAULT_TOOL_TIMEOUT_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.stealth_browser.timeout 应为正数秒,当前为 {value!r}",
                error_type="invalid_timeout",
            )
        return float(value)

    def _max_pages(self) -> int:
        value = self.engine_options().get("max_pages")
        if value is None:
            return DEFAULT_MAX_PAGES
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise FetchError(
                f"engine_options.stealth_browser.max_pages 应为正整数,当前为 {value!r}",
                error_type="invalid_max_pages",
            )
        return int(value)

    def _seed(self) -> int | None:
        value = self.engine_options().get("seed")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise FetchError(
                f"engine_options.stealth_browser.seed 应为整数指纹种子,当前为 {value!r}",
                error_type="invalid_seed",
            )
        return int(value)

    def _profile(self) -> str | None:
        value = self.engine_options().get("profile")
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise FetchError(
                f"engine_options.stealth_browser.profile 应为非空目录路径,当前为 {value!r}",
                error_type="invalid_profile",
            )
        return value

    def _wait_until(self) -> str:
        value = self.engine_options().get("wait_until")
        if value is None:
            return "domcontentloaded"
        if value not in WAIT_UNTIL_MODES:
            raise FetchError(
                f"engine_options.stealth_browser.wait_until 应为 {'/'.join(WAIT_UNTIL_MODES)} 之一,"
                f"当前为 {value!r}",
                error_type="invalid_wait_until",
            )
        return str(value)

    def _settle_seconds(self) -> float:
        value = self.engine_options().get("challenge_settle_seconds")
        if value is None:
            return DEFAULT_SETTLE_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise FetchError(
                f"engine_options.stealth_browser.challenge_settle_seconds 应为非负数秒,当前为 {value!r}",
                error_type="invalid_settle_seconds",
            )
        return float(value)

    def _options(self) -> _Options:
        """engine_options.stealth_browser 全量校验快照(先于连接,fail-fast 于配置)."""
        transport = self._transport()
        return _Options(
            transport=transport,
            command=self._command(),
            url=self._url(),
            timeout=self._timeout(),
            max_pages=self._max_pages(),
            seed=self._seed(),
            profile=self._profile(),
            wait_until=self._wait_until(),
            settle_seconds=self._settle_seconds(),
        )

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        options = self._options()
        targets = self._template_urls()
        self._check_page_budget(targets, options)
        # robots 预检在连接前:robots 拒绝的源零浏览器成本(礼貌先于花钱)。
        for target_url in targets:
            await self._ensure_robots_allowed(target_url)
        client = McpClient(
            command=list(options.command) if options.transport == "stdio" else None,
            url=options.url if options.transport == "http" else None,
            timeout=options.timeout,
        )
        try:
            try:
                await client.connect()
            except FetchError:
                raise
            except (OSError, httpx.HTTPError) as exc:
                # 客户端底层抛裸 OS/传输错误(如自定义注入)同样归为服务缺失,不裸逃。
                raise FetchError(
                    f"MCP 服务器无法启动/连接: {exc};请先安装/启动 MCP 服务({INSTALL_HINT})",
                    error_type="mcp_server_missing",
                ) from exc
            tools = await client.list_tools()
            logger.info(
                "MCP 服务器就绪 server=%s version=%s transport=%s tools=%s pages=%s/%s",
                client.server_info.get("name", "?"),
                client.server_info.get("version", "?"),
                options.transport,
                len(tools),
                len(targets),
                options.max_pages,
            )
            opened = await self._ensure_browser(client, tools, options)
            try:
                return await self._crawl_pages(client, tools, targets, options)
            finally:
                if opened:  # 复用的实例归服务器所有,不代关
                    await self._close_browser_best_effort(client)
        finally:
            await client.close()

    def _check_page_budget(self, targets: list[str], options: _Options) -> None:
        """单 run 页面预算护栏:连接前结构化拒绝(最贵层的成本闸门)."""
        if len(targets) > options.max_pages:
            raise FetchError(
                f"单 run 页面预算耗尽:目标页数 {len(targets)} 超过预算 {options.max_pages}"
                f"(engine_options.stealth_browser.max_pages);反检测浏览器逐页开合成本高,"
                "请减小 pagination.max_pages 或显式调大预算",
                error_type="page_budget_exhausted",
            )

    async def _ensure_browser(self, client: McpClient, tools: list[str], options: _Options) -> bool:
        """browser_status 探测复用;没开/已挂才 browser_open。返回是否由本引擎 open.

        服务器对「未开/已挂」的回答带其文档化句子(the main browser is not
        open… / …is gone…);实测 invisible_playwright_mcp 把该回答标成
        isError 工具结果,故两种形态都按句子识别,其余视为已开复用。
        """
        if "browser_status" in tools:
            try:
                status_text = await client.call_tool("browser_status", {})
            except McpError as exc:
                status_text = str(exc)
            if "not open" not in status_text and "is gone" not in status_text:
                logger.info("MCP 浏览器实例复用(http transport 跨 run 共享,不重复 open)")
                return False
        await client.call_tool("browser_open", self._open_arguments(options))
        return True

    def _open_arguments(self, options: _Options) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        if options.seed is not None:
            arguments["seed"] = options.seed
        if options.profile is not None:
            arguments["profile"] = options.profile
        if self._active_proxy_url:  # 显式代理意图不得静默丢弃;值不落日志
            arguments["proxy"] = self._active_proxy_url
        return arguments

    async def _crawl_pages(
        self,
        client: McpClient,
        tools: list[str],
        targets: list[str],
        options: _Options,
    ) -> list[dict]:
        items: list[dict] = []
        injected_hosts: set[str] = set()
        for target_url in targets:
            # qps 限速作用于目标站点(浏览器代抓);robots 已在连接前预检。
            await self._acquire_rate_limit(target_url)
            host = urlsplit(target_url).hostname or ""
            if host and host not in injected_hosts:
                await self._inject_cookies(client, tools, target_url)
                injected_hosts.add(host)
            html = await self._read_page(client, target_url, options)
            risk = classify_risk_control(html)
            if risk is not None:
                html = await self._settle_or_refuse(client, target_url, options, risk)
            items.extend(self._extract_page(html, target_url))
        return items

    async def _inject_cookies(self, client: McpClient, tools: list[str], target_url: str) -> None:
        """源 Cookie 凭据(基座已解析)-> 每新 host 导航前注入一次.

        Raises:
            FetchError: 服务器未暴露 cookie 注入工具(``cookie_unsupported``)。
        """
        cookie_value = next(
            (value for key, value in self._headers.items() if key.lower() == "cookie"),
            None,
        )
        if not cookie_value:
            return
        host = urlsplit(target_url).hostname or ""
        cookies = parse_cookie_header(cookie_value, host)
        if not cookies:
            return
        if COOKIE_TOOL_NAME not in tools:
            raise FetchError(
                f"源配置了 Cookie 凭据,但 MCP 服务器未暴露 cookie 注入工具 {COOKIE_TOOL_NAME!r}"
                f"(已暴露: {', '.join(tools) or '无'});登录墙场景需要支持该工具的服务器/分支",
                error_type="cookie_unsupported",
            )
        await client.call_tool(COOKIE_TOOL_NAME, {"cookies": cookies})
        logger.info("Cookie 已注入 MCP 浏览器 host=%s cookies=%s(值不落日志)", host, len(cookies))

    async def _read_page(self, client: McpClient, target_url: str, options: _Options) -> str:
        """导航 + 读整页 HTML(供风控检测与 CSS 提取)."""
        answer = await client.call_tool(
            "browser_navigate", {"url": target_url, "wait_until": options.wait_until}
        )
        status = parse_navigate_status(answer)
        if status is not None and status >= 400:
            raise FetchError(
                f"MCP 浏览器导航失败 url={target_url}: HTTP {status}(盾拦截或源不可达,auto 链将据此降级)",
                error_type=f"http_{status}",
            )
        return await client.call_tool("browser_read_html", {"mode": "full"})

    async def _settle_or_refuse(
        self,
        client: McpClient,
        target_url: str,
        options: _Options,
        risk: str,
    ) -> str:
        """风控分流:硬墙(手机验证码/真人审)零尝试直接报;基础盾沉降后重试一次."""
        if risk in {kind for kind, _pattern in _HARD_WALL_PATTERNS}:
            raise FetchError(
                f"源命中不可自动处理的验证码/审核墙 kind={risk} url={target_url};"
                "安全基线:手机验证码/真人审核不做绕过,请人工获取登录态或放弃该源",
                error_type=f"captcha_{risk}",
            )
        logger.info("基础盾质询沉降重试 kind=%s url=%s 等待=%ss", risk, target_url, options.settle_seconds)
        await self.context.sleep(options.settle_seconds)
        html = await self._read_page(client, target_url, options)
        still = classify_risk_control(html)
        if still is None:
            return html
        if still in {kind for kind, _pattern in _HARD_WALL_PATTERNS}:
            raise FetchError(
                f"源命中不可自动处理的验证码/审核墙 kind={still} url={target_url};"
                "安全基线:手机验证码/真人审核不做绕过,请人工获取登录态或放弃该源",
                error_type=f"captcha_{still}",
            )
        raise FetchError(
            f"基础盾尝试后仍未通过 kind={still} url={target_url}(沉降 {options.settle_seconds}s 重导航一次);"
            "不做绕过,由降级链/人工接管",
            error_type="captcha_challenge",
        )

    # ----------------------------------------------------------- extraction

    def _extract_page(self, page_html: str, target_url: str) -> list[dict]:
        extract = self.source.extract
        if extract is None:
            return self._auto_structure(page_html, target_url)
        return extract_html(page_html, extract, base_url=target_url)

    @staticmethod
    def _auto_structure(page_html: str, target_url: str) -> list[dict]:
        """无 extract 兜底:{url, title, content}(与 L3/L4 同形)."""
        tree = HTMLParser(page_html)
        title_node = tree.css_first("title")
        body_node = tree.css_first("body")
        title = title_node.text(separator=" ", strip=True) if title_node is not None else ""
        content = body_node.text(separator=" ", strip=True) if body_node is not None else ""
        return [{"url": target_url, "title": title, "content": content}]

    @staticmethod
    async def _close_browser_best_effort(client: McpClient) -> None:
        """browser_close 尽力而为:清理失败不得掩盖原始错误."""
        try:
            await client.call_tool("browser_close", {})
        except Exception as exc:  # noqa: BLE001 - 收尾路径只记日志
            logger.debug("MCP 浏览器关闭失败(忽略): %s", exc)
