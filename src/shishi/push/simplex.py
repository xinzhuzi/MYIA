"""SimpleX 通道:本机 simplex-chat 守护进程的 WebSocket JSON API(出站-only)。

蓝本归属(10-03-messaging-w3-longtail 组二):守护进程协议对照 Hermes
``plugins/platforms/simplex/adapter.py``(NousResearch/Hermes-Agent,MIT)——
WS 帧 ``{"corrId": …, "cmd": "<命令>"}``、应答按 corrId 关联
(``_send_command``)、聊天发送 fire-and-forget(蓝本事实:守护进程对聊天
命令不保证回 corrId 应答,等待只会把出站串行化在超时后面)、``_send_cmd``
的 ``/_send <target> json […]`` 结构化寻址(``group:<id>`` → ``#<id>``,
其余 → ``@<显示名>``)、以及 ``list_channels`` 的 ``/contacts`` + ``/groups``
两命令。MYIA 按本档语义重写(出站-only、无常驻监听、每次操作新开短连),
不整块复制。

- **22 家长尾平台中唯一的目录发现**:SimpleX 守护进程有列表命令
  (``/contacts``/``/groups``,蓝本事实),照 feishu_card
  ``discover_directory`` 范式实装(本批唯一的发现实装)。
- **依赖门(ocr.py 范式)**:WS 客户端库 ``websockets`` 是可选依赖(extras
  ``shishi[simplex]``),惰性 import——缺装时发送/发现抛
  ``dependency_missing`` 结构化错误并附安装命令,核心依赖红线不破;
  另需本机 ``simplex-chat`` 守护进程(``simplex-chat -p 5225`` 类服务模式)。
- **分段**:守护进程对超长文本无硬限,蓝本按 8000 字符 sanity 切块
  (``MAX_MESSAGE_LENGTH = 8000`` 同款),逐块独立 ``/_send`` 帧。

错误语义:守护进程不可达/命令超时/连接中断抛 ``simplex_api_error``(聊天
发送是 fire-and-forget,无逐条回执——错误面在连接与命令层,蓝本同款
取舍);文本无 HTTP 状态片段,不触 core 死信分类器(全部瞬态)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用;两路全缺报 ``missing_target``。直达解析
``group:<id>``(原生群 id);DM 按显示名寻址,经目录发现登记后按名解析
(不设显示名直达——与中文别名撞形)。

凭据安全基线同其余通道:YAML 只写 ``env:``/``keychain:`` 引用,发送期才
解析;错误文案只带引用名。测试经 ``ws_connect`` 注入点 mock,零真守护进程。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import time
from typing import Any, Mapping, Protocol, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import ChannelEntry
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.telegram import split_message
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CLIENT_PACKAGE",
    "COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_WS_URL",
    "DEFAULT_WS_URL_REF",
    "GROUP_ID_RE",
    "INSTALL_COMMAND",
    "MESSAGE_LIMIT",
    "OPEN_TIMEOUT_SECONDS",
    "SimplexChannel",
    "send_command_text",
]

logger = logging.getLogger(__name__)

#: WS 客户端库(extras ``shishi[simplex]``;缺装 → dependency_missing)。
CLIENT_PACKAGE = "websockets"
INSTALL_COMMAND = (
    "pip install 'shishi[simplex]'  # 或 uv add 'shishi[simplex]';"
    "并需本机 simplex-chat 守护进程(服务模式,WS API)"
)
#: 守护进程 WS 地址引用(本机服务模式缺省端口,蓝本同款)。
DEFAULT_WS_URL_REF = "env:SIMPLEX_WS_URL"
#: 守护进程缺省 WS 地址(蓝本 ``ws://127.0.0.1:5225`` 同款)。
DEFAULT_WS_URL = "ws://127.0.0.1:5225"
#: legacy 单会话 target 引用(``group:<id>`` 或 DM 显示名的 env 引用)。
DEFAULT_TARGET_ENV_REF = "env:SIMPLEX_CHAT"
#: 单块文本上限(蓝本 ``MAX_MESSAGE_LENGTH = 8000`` 同款 sanity 切块)。
MESSAGE_LIMIT = 8000
#: WS 连接/命令超时(秒;蓝本 connect open_timeout=10 / 命令 30 同量级)。
OPEN_TIMEOUT_SECONDS = 10.0
COMMAND_TIMEOUT_SECONDS = 30.0
#: 直达群 id 形态(SimpleX 群 id 是数字,蓝本 ``group:<groupId>`` 同款)。
GROUP_ID_RE = re.compile(r"^group:[0-9]+$")


class SimplexWS(Protocol):
    """守护进程连接的最小面(注入点与 websockets 客户端共用的鸭子协议)。"""

    async def send(self, text: str) -> None: ...

    async def recv(self) -> str | None: ...  # None = 连接已关闭

    async def close(self) -> None: ...


def _require_client() -> Any:
    """惰性 import ``websockets``;缺装 → ``dependency_missing``(ocr.py 范式)。"""
    try:
        return importlib.import_module(CLIENT_PACKAGE)
    except ImportError as exc:
        raise PushSendError(
            "dependency_missing",
            f"simplex 通道依赖 {CLIENT_PACKAGE} 客户端库未安装:请先执行 {INSTALL_COMMAND}",
        ) from exc


async def _default_ws_connect(url: str, open_timeout: float) -> SimplexWS:
    """缺省连接器:``websockets.connect``(依赖门在此触发)。"""
    websockets = _require_client()
    return await websockets.connect(url, open_timeout=open_timeout)


def send_command_text(chat_id: str, items: list[Mapping[str, Any]]) -> str:
    """结构化 ``/_send`` 命令(蓝本 ``_send_cmd`` 同款寻址与 JSON 载荷)。

    ``group:<id>`` → ``#<id>`` 目标;其余(DM 显示名)→ ``@<名>``。items
    即 ``/_send json`` 的载荷数组(本通道用 ``{"msgContent": {"type":
    "text", "text": …}}`` 单元素)。
    """
    target = f"#{chat_id[6:]}" if chat_id.startswith("group:") else f"@{chat_id}"
    return f"/_send {target} json {json.dumps(items, ensure_ascii=False)}"


def _display_name(obj: Mapping[str, Any], profile_key: str) -> str:
    """``localDisplayName`` 退回嵌套 profile 的 ``displayName``(蓝本同款)。"""
    profile = obj.get(profile_key)
    return str(obj.get("localDisplayName") or "") or (
        str(profile.get("displayName") or "") if isinstance(profile, Mapping) else ""
    )


class SimplexChannel(TrendAwareChannel):
    """``simplex`` channel:每次操作一条短 WS 会话(发帧后即关)。

    Args:
        target: legacy target 引用(``group:<id>`` 或 DM 显示名的 env 引用,
            发送期解析);定向(``targets``)在场时可省——两条寻址路径至少
            一条,否则发送期报 ``missing_target``。
        ws_url_ref: 守护进程 WS 地址引用;省略 → 本机缺省 :data:`DEFAULT_WS_URL`。
        template: 可选用户模板(Jinja2);省略 → 内置纯文本版式(ntfy 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        ws_connect: 连接器注入点(测试 mock);缺省 :func:`_default_ws_connect`
            (惰性 import ``websockets``,缺装报 ``dependency_missing``)。

    Raises:
        PushSendError: 依赖缺装、凭据解析失败、守护进程连接失败/中断、或
            发现命令超时无应答。
    """

    name = "simplex"
    #: 目录寻址已开:context.target 优先,legacy target 兜底;协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        ws_url_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        ws_connect: Any | None = None,
    ) -> None:
        self._target = target
        self._ws_url_ref = ws_url_ref
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._ws_connect = ws_connect or _default_ws_connect
        self._corr_counter = 0

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 分段 fire-and-forget ``/_send`` 帧;定向优先(``context.target``)。

        聊天命令无逐条回执(蓝本事实:守护进程不保证对聊天命令回 corrId
        应答)——成功语义 = 帧已写入守护进程连接;连接/依赖层错误照常抛。

        Raises:
            PushSendError: on any dependency/credential/transport failure
                (callers isolate per channel; nothing is raised on success).
        """
        ws_url = self._resolve_ws_url()
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_target()
        )
        parts = self._compose(items, context)
        ws = await self._connect(ws_url)
        try:
            for text in parts:
                command = send_command_text(
                    chat_id, [{"msgContent": {"type": "text", "text": text}}]
                )
                await ws.send(json.dumps({"corrId": self._next_corr_id(), "cmd": command}))
        finally:
            await ws.close()
        logger.debug(
            "simplex 已发送: slot=%s kind=%s count=%d parts=%d",
            context.slot,
            context.kind,
            len(items),
            len(parts),
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> list[str]:
        """消息分段:模板渲染输出或内置版式,均按 :data:`MESSAGE_LIMIT` 切。"""
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
            return split_message(text, limit=MESSAGE_LIMIT)
        return split_message(build_message(items, context), limit=MESSAGE_LIMIT)

    # ------------------------------------------------------------- resolve

    def _resolve_ws_url(self) -> str:
        if self._ws_url_ref is None:
            return DEFAULT_WS_URL
        try:
            value = resolve_credential(self._ws_url_ref).strip().rstrip("/")
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"simplex 守护进程地址解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"simplex 守护进程地址引用 {self._ws_url_ref!r} 解析结果为空",
            )
        return value

    def _resolve_target(self) -> str:
        if self._target is None:
            raise PushSendError(
                "missing_target",
                "simplex 未配置 legacy target(env:/keychain: 引用,group:<id> 或 "
                "DM 显示名),本次发送也未携带 context.target——两条寻址路径至少一条在场",
            )
        try:
            value = resolve_credential(self._target).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"simplex target 解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"simplex target 引用 {self._target!r} 解析结果为空",
            )
        return value

    # ---------------------------------------------------------- ws plumbing

    def _next_corr_id(self) -> str:
        """mint 关联 id(计数器 + 毫秒,蓝本 ``_make_corr_id`` 同构)。"""
        self._corr_counter += 1
        return f"myia-{self._corr_counter}-{int(time.time() * 1000)}"

    async def _connect(self, ws_url: str) -> SimplexWS:
        """开一条短连接;连接层异常 → 结构化错误(依赖门在缺省连接器内)。

        文案只带地址引用名(自宣基线「错误文案只带引用名」):解析后的
        ws 地址不进错误文案(即便通常是本机回环,也不开先例)。
        """
        try:
            return await self._ws_connect(ws_url, OPEN_TIMEOUT_SECONDS)
        except PushSendError:
            raise
        except Exception as exc:  # noqa: BLE001 - websockets/注入桩各类连接异常
            raise PushSendError(
                "simplex_api_error",
                f"simplex 守护进程连接失败(地址引用 {self._ws_url_ref or DEFAULT_WS_URL_REF!r},"
                f"缺省即本机 {DEFAULT_WS_URL}): {type(exc).__name__}: {exc}"
                "(确认本机 simplex-chat 服务模式在跑、WS 端口正确)",
            ) from exc

    async def _command(self, ws: SimplexWS, command: str) -> Mapping[str, Any]:
        """发命令并按 corrId 等应答(跳过无关聊天事件;蓝本 ``_send_command``
        的短会话版)。超时/EOF/坏帧 → 结构化错误。"""
        corr_id = self._next_corr_id()
        await ws.send(json.dumps({"corrId": corr_id, "cmd": command}))
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PushSendError(
                    "simplex_api_error",
                    f"simplex 命令超时无应答({COMMAND_TIMEOUT_SECONDS:.0f}s): {command[:60]!r}",
                )
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError as exc:  # 3.11+:asyncio.TimeoutError 即内建形态
                raise PushSendError(
                    "simplex_api_error",
                    f"simplex 命令超时无应答({COMMAND_TIMEOUT_SECONDS:.0f}s): {command[:60]!r}",
                ) from exc
            if raw is None:
                raise PushSendError(
                    "simplex_api_error",
                    f"simplex 守护进程在应答前关闭连接: {command[:60]!r}",
                )
            try:
                event = json.loads(raw)
            except ValueError:
                continue  # 非法 JSON 帧跳过(蓝本同款 fail-quiet)
            if not isinstance(event, Mapping) or event.get("corrId") != corr_id:
                continue  # 无关 corrId(聊天事件/别端回执)跳过
            resp = event.get("resp")
            return resp if isinstance(resp, Mapping) else event

    # ------------------------------------------------- 目录发现(本批唯一实装)

    async def discover_directory(self) -> list[ChannelEntry]:
        """列出守护进程的全部联系人(DM)与已加入群(feishu_card 范式)。

        ``/contacts`` → ``contacts[]``(chat_id = 显示名,退 contactId;
        type=dm);``/groups`` → ``groups[]``(dict 或 ``[groupInfo, …]``
        双形态,蓝本同款;chat_id = ``group:<groupId>``,type=group)。
        名称缺失的群退 groupId 占位(仍可按 id 寻址,feishu 同款语义)。

        Raises:
            PushSendError: 依赖缺装、连接失败、命令超时或守护进程中途断连
                (调用方 refresh 按发现失败隔离:告警 + 保留旧桶)。
        """
        ws_url = self._resolve_ws_url()
        ws = await self._connect(ws_url)
        try:
            contacts = await self._command(ws, "/contacts")
            groups = await self._command(ws, "/groups")
        finally:
            await ws.close()
        entries: list[ChannelEntry] = []
        for raw in contacts.get("contacts") or []:
            if not isinstance(raw, Mapping):
                continue
            name = _display_name(raw, "profile")
            contact_id = raw.get("contactId")
            if not name and contact_id is None:
                continue
            # DM 发送路径按显示名寻址(蓝本事实),id 退 contactId。
            label = str(name or contact_id)
            entries.append(
                ChannelEntry(platform="simplex", chat_id=label, name=label, type="dm")
            )
        for raw in groups.get("groups") or []:
            if isinstance(raw, list) and raw:  # [groupInfo, groupSummary] 双形态
                raw = raw[0]
            if not isinstance(raw, Mapping) or raw.get("groupId") is None:
                continue
            group_id = str(raw["groupId"])
            name = _display_name(raw, "groupProfile") or group_id
            entries.append(
                ChannelEntry(
                    platform="simplex",
                    chat_id=f"group:{group_id}",
                    name=str(name),
                    type="group",
                )
            )
        return entries

    # ------------------------------------------------------------- 直达

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``group:<数字 id>`` 不经目录(22 家长尾唯一发现平台的
        群原生 id 形态)。DM 显示名不设直达(与中文别名撞形),经目录按名
        解析;其余形态返回 None。
        """
        value = ref.strip()
        if GROUP_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="simplex", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
