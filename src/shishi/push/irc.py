"""IRC 通道:纯 stdlib asyncio 一次性 TCP 会话(connect → PRIVMSG → QUIT)。

蓝本归属(10-03-messaging-w3-longtail 组二):协议握手与分段对照 Hermes
``plugins/platforms/irc/adapter.py``(NousResearch/Hermes-Agent,MIT)的
``_standalone_send`` 一次性出站路径(上游自述 "Uses Python stdlib — no
extra packages needed"):``_StandaloneConn`` 的 pump(等数字回执期间应答
PING)、``_sa_register``(NICK 碰撞重试)、``_sa_join``(显式拒绝才算失败)、
``_chunk_paragraph``/``_privmsg_budget`` 的 510 字节行预算分段、
``_strip_irc_control_chars`` 的 CRLF 注入防护与 ``_MARKDOWN_RULES`` 纯文本
化。MYIA 按本档语义重写(出站-only、TrendAwareChannel 契约、PushSendError
结构化错误),不整块复制。【探查结论:IRC 是 TCP 协议,纯 httpx 不可达;
蓝本证明最小客户端零第三方依赖(asyncio + ssl),故不进 extras】

- **一次性会话**:每次发送新开连接:``PASS?`` → ``NICK``/``USER``(等 001
  RPL_WELCOME;432/433 换后缀重试 ≤5 次;464/465 直接失败)→
  NickServ ``IDENTIFY?`` → 频道目标 ``JOIN``(403/405/471/473/474/475 显式
  拒绝才算失败;超时照发,蓝本同款)→ 分段 ``PRIVMSG`` → ``QUIT``。nick
  取 ``{配置 nick}-push``(≤30 字符),不与任何常驻网关身份抢注。
- **分段**:IRC 行上限 510 字节,扣除 ``PRIVMSG <target> :`` 与 CRLF 后按
  UTF-8 字节预算切行(二分找最大前缀 + 空格边界优先,蓝本同款算法);
  分段间 0.3s 步进(基础防洪,蓝本同款)。
- **安全**:target 与正文均剥离 CR/LF/NUL(IRC 命令注入向量,蓝本同款);
  markdown 语法降级纯文本(IRC 无富文本)。

错误语义:协议级拒绝(数字回执)抛 ``irc_api_error``,文案保留
``IRC <numeric> <名称>`` 原厂片段——按 core 分类表
(:func:`shishi.push.delivery.classify_dead_error`)``IRC 403
ERR_NOSUCHCHANNEL``(频道不存在)→ chat 级 not_found 硬死信(复核 D1:
裸 ``403`` marker 收敛为锚定形态后,IRC 403 以原厂片段归位 not_found
家族——硬死信语义不变,仅家族标签校正);429/5xx/超时
类无 ASCII marker → 瞬态不标。连接/传输失败抛 ``http_error``(沿用本层
通用传输错误码;IRC 是 TCP,无 HTTP 语义,仅复用码值)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用;两路全缺报 ``missing_target``。直达解析
``#频道``/``&频道``(sigil 形态);裸 nick DM 不设直达(与中文别名撞形),
经目录登记。目录无自动发现(IRC 无列表 API,蓝本事实),抛
:class:`~shishi.push.directory.DirectoryDiscoverUnsupported`。

凭据安全基线同其余通道:server/nick/密码只写 ``env:``/``keychain:`` 引用,
发送期才解析;错误文案只带引用名。测试经 ``connect``/``sleep`` 注入点 mock,
零真连接(真实 TCP 冒烟留主人手工门禁)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import ssl
from typing import Any, Awaitable, Callable, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_CHANNEL_RE",
    "DEFAULT_NICK_ENV_REF",
    "DEFAULT_PORT",
    "DEFAULT_SERVER_ENV_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_USE_TLS",
    "IrcChannel",
    "JOIN_TIMEOUT_SECONDS",
    "LINE_BYTE_LIMIT",
    "MARKDOWN_RULES",
    "MAX_CHUNK_CHARS",
    "PRIVMSG_PACE_SECONDS",
    "REGISTER_TIMEOUT_SECONDS",
    "chunk_paragraph",
    "parse_irc_line",
    "privmsg_budget",
    "strip_control_chars",
    "strip_markdown",
]

logger = logging.getLogger(__name__)

#: IRC 服务器主机引用(自建 irc.example.com 或公共网络)。
DEFAULT_SERVER_ENV_REF = "env:IRC_SERVER"
#: bot 昵称引用(一次性会话实际用 ``{nick}-push``,避免与常驻客户端抢注)。
DEFAULT_NICK_ENV_REF = "env:IRC_NICK"
#: legacy 频道 target 引用(``#频道`` 或裸 nick DM)。
DEFAULT_TARGET_ENV_REF = "env:IRC_CHANNEL"
#: 可选服务器 PASS 密码引用(缺省不发送 PASS)。
DEFAULT_SERVER_PASSWORD_ENV_REF = "env:IRC_SERVER_PASSWORD"
#: 可选 NickServ 密码引用(注册完成后 IDENTIFY;缺省跳过)。
DEFAULT_NICKSERV_PASSWORD_ENV_REF = "env:IRC_NICKSERV_PASSWORD"
#: 缺省端口:6697 = IRC over TLS(use_tls 缺省 True,蓝本同款推荐)。
DEFAULT_PORT = 6697
DEFAULT_USE_TLS = True
#: IRC 协议行上限(字节,RFC 1459 蓝本事实);扣掉命令开销后是分段预算。
LINE_BYTE_LIMIT = 510
#: 分段字符数上限(蓝本 ``max_message_length=450`` 同量级,双保险)。
MAX_CHUNK_CHARS = 450
#: 一次性会话各阶段超时(秒;蓝本 _standalone_send 同款量级,不设配置口)。
CONNECT_TIMEOUT_SECONDS = 15.0
REGISTER_TIMEOUT_SECONDS = 15.0
JOIN_TIMEOUT_SECONDS = 5.0
#: PRIVMSG 分段间步进(基础防洪,蓝本 send/send_standalone 同款)。
PRIVMSG_PACE_SECONDS = 0.3
#: NickServ IDENTIFY 后的生效等待(蓝本同款;经 ``sleep`` 注入,测试零等待)。
NICKSERV_WAIT_SECONDS = 2.0
#: 频道 sigil 形态(``#`` 常规 / ``&`` 本地 / ``+`` 无噪 / ``!`` 安全,蓝本
#: ``_is_irc_channel`` 同款集合);裸 nick DM 不进直达(与别名撞形)。
DEFAULT_CHANNEL_RE = re.compile(r"^[#&+!][^\s,:]+$")

#: markdown → 纯文本降级规则(蓝本 ``_MARKDOWN_RULES`` 同款:IRC 无富文本)。
MARKDOWN_RULES: tuple[tuple[str, str], ...] = (
    (r"\*\*(.+?)\*\*", r"\1"),  # bold
    (r"__(.+?)__", r"\1"),
    (r"\*(.+?)\*", r"\1"),  # italic
    (r"(?<!\w)_(.+?)_(?!\w)", r"\1"),
    (r"`(.+?)`", r"\1"),  # inline code
    (r"```\w*\n?", ""),  # code fences
    (r"!\[([^\]]*)\]\(([^)]+)\)", r"\2"),  # 图片 → url(须先于链接规则)
    (r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)"),  # 链接 → 文本 (url)
)

#: join/register 显式拒绝数字 → 协议错误名(文案保留原厂片段)。
_JOIN_ERROR_NAMES = {
    "403": "ERR_NOSUCHCHANNEL",
    "405": "ERR_TOOMANYCHANNELS",
    "471": "ERR_CHANNELISFULL",
    "473": "ERR_INVITEONLYCHAN",
    "474": "ERR_BANNEDONCHAN",
    "475": "ERR_BADCHANNELKEY",
}
_REGISTER_ERROR_NAMES = {
    "432": "ERR_ERRONEUSNICKNAME",
    "433": "ERR_NICKNAMEINUSE",
    "464": "ERR_PASSWDMISMATCH",
    "465": "ERR_YOUREBANNEDCREEP",
}

_EOF = object()  # sentinel:server closed the connection

#: 注入点契约:``connect(host, port, use_tls) -> (reader, writer)``;reader 需
#: ``readuntil(b"\r\n")``,writer 需 ``write``/``drain``/``close``。缺省
#: ``asyncio.open_connection``(use_tls 时挂缺省 SSL context)。
IrcConnect = Callable[[str, int, bool], Awaitable["tuple[Any, Any]"]]


def parse_irc_line(raw: str) -> dict[str, Any]:
    """一行 IRC 协议文本 → ``{"prefix", "command", "params"}``(蓝本同款)。"""
    prefix, trailing = "", ""
    if raw.startswith(":"):
        prefix, _, raw = raw[1:].partition(" ")
    if " :" in raw:
        raw, trailing = raw.split(" :", 1)
    parts = raw.split()
    params = parts[1:] if len(parts) > 1 else []
    if trailing:
        params.append(trailing)
    return {"prefix": prefix, "command": parts[0] if parts else "", "params": params}


def strip_control_chars(text: str) -> str:
    """剥 CR/LF/NUL(IRC 命令注入向量;蓝本 ``_strip_irc_control_chars`` 同款)。"""
    return text.replace("\r", " ").replace("\n", " ").replace("\x00", "")


def strip_markdown(text: str) -> str:
    """markdown 语法降级纯文本(:data:`MARKDOWN_RULES` 逐条替换)。"""
    for pattern, repl in MARKDOWN_RULES:
        text = re.sub(pattern, repl, text)
    return text


def privmsg_budget(target: str) -> int:
    """``PRIVMSG <target> :`` + CRLF 之后的字节预算(510 - 开销,蓝本同款)。"""
    return LINE_BYTE_LIMIT - (len(f"PRIVMSG {target} :".encode("utf-8")) + 2)


def chunk_paragraph(paragraph: str, limit: int) -> list[str]:
    """一行长文按 UTF-8 字节预算切(二分最大前缀 + 空格边界优先,蓝本同款)。"""
    chunks: list[str] = []
    while paragraph:
        if len(paragraph.encode("utf-8")) <= limit:
            chunks.append(paragraph)
            break
        low, high, split_at = 1, len(paragraph), 0
        while low <= high:  # 二分:最大的仍放得下的字符前缀
            mid = (low + high) // 2
            if len(paragraph[:mid].encode("utf-8")) <= limit:
                split_at, low = mid, mid + 1
            else:
                high = mid - 1
        space = paragraph.rfind(" ", 0, split_at)
        if space > split_at // 3:
            split_at = space
        chunks.append(paragraph[:split_at].rstrip())
        paragraph = paragraph[split_at:].lstrip()
    return chunks


async def _open_connection(host: str, port: int, use_tls: bool) -> tuple[Any, Any]:
    """缺省连接器:asyncio TCP(use_tls 时挂缺省 SSL context,蓝本同款)。"""
    ctx: ssl.SSLContext | None = ssl.create_default_context() if use_tls else None
    return await asyncio.open_connection(host, port, ssl=ctx)


def _encode_line(line: str) -> bytes:
    return (line + "\r\n").encode("utf-8")


def _numeric_error(command: str, names: dict[str, str], params: list[str]) -> str:
    """数字回执 → 原厂片段文案(``IRC 403 ERR_NOSUCHCHANNEL: …``)。"""
    name = names.get(command, "ERROR")
    detail = params[-1] if params else ""
    return f"IRC {command} {name}: {detail}"


class IrcChannel(TrendAwareChannel):
    """``irc`` channel:每次发送一条一次性 TCP 会话(PRIVMSG 后 QUIT)。

    Args:
        target: legacy 频道/nick target 引用(``env:IRC_CHANNEL`` style,发送期
            解析);定向(``targets``)在场时可省——两条寻址路径至少一条,
            否则发送期报 ``missing_target``。
        server_ref: IRC 服务器主机引用;省略 → :data:`DEFAULT_SERVER_ENV_REF`。
        nick_ref: bot 昵称引用;省略 → :data:`DEFAULT_NICK_ENV_REF`。
        server_password_ref: 可选 PASS 密码引用;None = 不发送 PASS。
        nickserv_password_ref: 可选 NickServ 密码引用;None = 跳过 IDENTIFY。
        port: 服务器端口(缺省 :data:`DEFAULT_PORT` 6697)。
        use_tls: 是否 IRC over TLS(缺省 True)。
        template: 可选用户模板(Jinja2);省略 → 内置纯文本版式(ntfy 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        connect: 连接器注入点(测试 mock);缺省 :func:`_open_connection`。
        sleep: 等待注入点(分段步进/NickServ 等待;测试钉零等待)。

    Raises:
        PushSendError: 凭据解析失败、target 含非法字符、连接/注册/JOIN 被
            拒、或写流失败(协议拒绝带 ``IRC <numeric> <名称>`` 原厂片段)。
    """

    name = "irc"
    #: 目录寻址已开:context.target 优先,legacy target 兜底;协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        server_ref: str | None = None,
        nick_ref: str | None = None,
        server_password_ref: str | None = None,
        nickserv_password_ref: str | None = None,
        port: int = DEFAULT_PORT,
        use_tls: bool = DEFAULT_USE_TLS,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        connect: IrcConnect | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._target = target
        self._server_ref = server_ref
        self._nick_ref = nick_ref
        self._server_password_ref = server_password_ref
        self._nickserv_password_ref = nickserv_password_ref
        self._port = port
        self._use_tls = use_tls
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._connect = connect or _open_connection
        self._sleep = sleep

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """一次性 IRC 会话:register → NickServ? → JOIN? → 分段 PRIVMSG → QUIT。

        Raises:
            PushSendError: on any credential/transport/protocol failure
                (callers isolate per channel; nothing is raised on success).
        """
        server = self._resolve_credential(
            self._server_ref or DEFAULT_SERVER_ENV_REF, "irc server"
        )
        nick_base = self._resolve_credential(
            self._nick_ref or DEFAULT_NICK_ENV_REF, "irc nick"
        )
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_target()
        )
        target = strip_control_chars(chat_id).strip()
        if not target or " " in target:
            # 解析值不回显(死信分类误判暴露面收敛,D1;自宣基线「错误文案
            # 只带引用名」)。
            raise PushSendError(
                "invalid_credential_ref",
                f"irc target 含非法字符或为空(不允许空格/换行):得到 "
                f"{len(chat_id)} 字符的值,不匹配该形态(解析值不回显)",
            )
        text = self._compose(items, context)
        try:
            reader, writer = await asyncio.wait_for(
                self._connect(server, self._port, self._use_tls),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 连接器可能抛 OSError/ssl/注入桩异常
            # 文案只带 server 引用名(自宣基线):解析后的主机名不进错误文案。
            raise PushSendError(
                "http_error",
                f"irc 连接失败(server 引用 {self._server_ref or DEFAULT_SERVER_ENV_REF!r},"
                f"port {self._port}): {type(exc).__name__}: {exc}",
            ) from exc
        try:
            nick = await self._register(writer, reader, nick_base)
            if self._nickserv_password_ref is not None:
                password = self._resolve_credential(
                    self._nickserv_password_ref, "irc nickserv 密码"
                )
                await self._raw(writer, f"PRIVMSG NickServ :IDENTIFY {strip_control_chars(password)}")
                await self._sleep(NICKSERV_WAIT_SECONDS)
            if DEFAULT_CHANNEL_RE.fullmatch(target):
                await self._join(writer, reader, target)
            await self._privmsg(writer, target, text)
            await self._raw(writer, "QUIT :MYIA push")
            with contextlib.suppress(Exception):  # 优雅退出:读掉服务端告别行
                await asyncio.wait_for(reader.read(1024), timeout=1.0)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
        logger.debug(
            # 日志同样只带引用名/计数,不带解析后的 target/nick(自宣基线
            # 「错误文案只带引用名」;C3 收敛——debug 流也不落解析值)。
            "irc 发送完成: slot=%s kind=%s count=%d target_ref=%s nick_ref=%s nick_len=%d",
            context.slot,
            context.kind,
            len(items),
            self._target,
            self._nick_ref or DEFAULT_NICK_ENV_REF,
            len(nick),
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        """模板渲染输出或内置版式,markdown 降级纯文本(IRC 无富文本)。"""
        if self._template is not None:
            # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
            # template_render_error(语法错误已在加载期被 schema 拒绝)。
            try:
                text = self._renderer.render(
                    self._template, items, context, **self.trend_render_kwargs()
                )
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
        else:
            text = build_message(items, context)
        return strip_markdown(text)

    # ------------------------------------------------------------- resolve

    def _resolve_credential(self, reference: str, label: str) -> str:
        try:
            return resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"irc {label} 凭据解析失败: {exc}") from exc

    def _resolve_target(self) -> str:
        if self._target is None:
            raise PushSendError(
                "missing_target",
                "irc 未配置 legacy target(env:/keychain: 引用,#频道 或 DM nick),"
                "本次发送也未携带 context.target——两条寻址路径至少一条在场",
            )
        try:
            return resolve_credential(self._target).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"irc target 解析失败: {exc}") from exc

    # ------------------------------------------------------------ protocol

    async def _raw(self, writer: Any, line: str) -> None:
        """写一行协议文本(蓝本 ``_send_raw``/``raw`` 同款)。"""
        try:
            writer.write(_encode_line(line))
            await writer.drain()
        except Exception as exc:  # noqa: BLE001 - 流错误统一映射传输错误
            raise PushSendError(
                "http_error", f"irc 写流失败({line.split(' ', 1)[0]}): {type(exc).__name__}: {exc}"
            ) from exc

    async def _pump(
        self, writer: Any, reader: Any, timeout: float, on_command: Callable[[str, list[str]], Any]
    ) -> Any:
        """读到 ``on_command`` 返回非 None 为止(期间应答 PING;蓝本 pump 同款)。

        超时返回 None(调用方按「照常进行」或「报错」各自定夺);EOF 返回
        ``_EOF`` 哨兵。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                raw_line = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            except Exception:  # noqa: BLE001 - IncompleteReadError/LimitOverrun 等 = 连接终止
                return _EOF
            message = parse_irc_line(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
            command, params = message["command"], message["params"]
            if command == "PING":
                await self._raw(writer, f"PONG :{params[0] if params else ''}")
                continue
            result = on_command(command, params)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                return result
        return None

    async def _register(self, writer: Any, reader: Any, nick_base: str) -> str:
        """PASS?/NICK/USER 并等 001;432/433 换后缀重试 ≤5(蓝本同款)。

        Returns:
            服务器确认的当前 nick(001 参数首位)。
        """
        # 后缀区分一次性推送身份,基名截 24 字符保重试后缀仍在 NICKLEN 内。
        base = strip_control_chars(nick_base).rstrip("_0123456789-")[:24] or "myia"
        nick = f"{base}-push"
        attempts = 0
        state = {"nick": nick}

        def _on_registration(command: str, params: list[str]) -> Any:
            nonlocal attempts
            if command in ("432", "433"):
                attempts += 1
                if attempts > 5:
                    return _numeric_error(command, _REGISTER_ERROR_NAMES, params)
                state["nick"] = f"{base}-push-{attempts}"
                return ("retry", state["nick"])
            if command in ("464", "465"):
                return _numeric_error(command, _REGISTER_ERROR_NAMES, params)
            if command == "001":
                state["nick"] = params[0] if params else state["nick"]
                return True
            return None

        if self._server_password_ref is not None:
            password = self._resolve_credential(
                self._server_password_ref, "irc 服务器密码"
            )
            await self._raw(writer, f"PASS {strip_control_chars(password)}")
        await self._raw(writer, f"NICK {nick}")
        await self._raw(writer, f"USER {nick} 0 * :MYIA push")
        while True:
            outcome = await self._pump(writer, reader, REGISTER_TIMEOUT_SECONDS, _on_registration)
            if outcome == _EOF:
                raise PushSendError(
                    "irc_api_error", "irc 注册阶段服务器关闭连接(未收到 001 RPL_WELCOME)"
                )
            if outcome is None:
                raise PushSendError(
                    "irc_api_error",
                    f"irc 注册超时({REGISTER_TIMEOUT_SECONDS:.0f}s 未收到 001 RPL_WELCOME)",
                )
            if outcome is True:
                return str(state["nick"])
            if isinstance(outcome, tuple) and outcome[0] == "retry":
                await self._raw(writer, f"NICK {outcome[1]}")
                continue
            raise PushSendError("irc_api_error", str(outcome))

    async def _join(self, writer: Any, reader: Any, target: str) -> None:
        """JOIN 频道:显式拒绝数字才算失败;超时/EOF 照发(蓝本 ``_sa_join`` 同款)。"""
        await self._raw(writer, f"JOIN {target}")

        def _on_join(command: str, params: list[str]) -> Any:
            if command in _JOIN_ERROR_NAMES:
                return _numeric_error(command, _JOIN_ERROR_NAMES, params)
            return True if command in ("366", "JOIN") else None

        outcome = await self._pump(writer, reader, JOIN_TIMEOUT_SECONDS, _on_join)
        if isinstance(outcome, str):  # 显式拒绝(错误文案)
            raise PushSendError("irc_api_error", outcome)

    async def _privmsg(self, writer: Any, target: str, text: str) -> None:
        """分段 PRIVMSG:行预算 = min(:data:`MAX_CHUNK_CHARS`, 字节预算)。"""
        paragraphs = [
            q
            for q in (strip_control_chars(p).rstrip() for p in text.split("\n"))
            if q
        ]
        budget = min(MAX_CHUNK_CHARS, privmsg_budget(target))
        lines = [chunk for paragraph in paragraphs for chunk in chunk_paragraph(paragraph, budget)]
        if not lines:
            raise PushSendError(
                "invalid_response", "irc 正文清洗后为空(无可见文本可发送)"
            )
        for index, line in enumerate(lines):
            if index:
                await self._sleep(PRIVMSG_PACE_SECONDS)  # 基础防洪,蓝本同款
            await self._raw(writer, f"PRIVMSG {target} :{line}")

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """IRC 无目录概念(蓝本事实):结构化说明,而非假装刷新。"""
        raise DirectoryDiscoverUnsupported(
            "irc 无自动发现(蓝本事实:IRC 无列表 API):频道即地址;"
            "直达写 irc:#频道,常用地名可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``#频道``/``&频道``(sigil 形态)不经目录。

        裸 nick DM 不设直达——与目录别名(任意名称)撞形会劫持别名解析;
        DM nick 经目录登记(chat_id = nick)。非法形态返回 None。
        """
        value = ref.strip()
        if DEFAULT_CHANNEL_RE.fullmatch(value):
            return ChannelTarget(
                platform="irc", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
