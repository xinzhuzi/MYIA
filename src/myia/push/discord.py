"""Discord 通道:Bot REST ``POST /channels/{id}/messages``(Bot token + JSON)。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态移植自 Hermes
``plugins/platforms/discord/adapter.py`` 的 ``_standalone_send``(NousResearch/
Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/discord/adapter.py``——文本路径
``POST https://discord.com/api/v10/channels/{id}/messages`` + ``Authorization:
Bot <token>`` + ``{"content": …}``)。MYIA 按本档语义重写,不整块复制:

- **REST 直发**:无网关 WebSocket(那是蓝本入站的另一半,MYIA 零入站);
  2xx(200/201)即成功,响应 JSON 含 ``id``(消息 snowflake)。
- **2000 硬上限**:官方 content 上限 2000 字符,蓝本
  ``MAX_MESSAGE_LENGTH = 2000`` 同值(:data:`MESSAGE_LIMIT`);长文按行边界
  拆多条(:func:`telegram.split_message` 复用,逐块顺序发送)。
- **话题定向**::class:`~myia.push.targets.ChannelTarget.thread_id` 在场时改投
  ``/channels/{thread_id}/messages``(蓝本 thread_id 分支同形态;蓝本的论坛
  建帖分支(type 15 探测)是入站元数据依赖,MYIA 不做——出站直投普通频道/
  已存在话题)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用(缺省 ``env:DISCORD_CHANNEL_ID``;蓝本
``DISCORD_BOT_TOKEN`` 同名 env 先例)。直达形态:雪花 id(17-20 位数字,
Discord snowflake 官方形态)。目录无自动发现(蓝本事实:出站无列表路径),
别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段(Discord 错误体
``{"message": "Unknown Channel", "code": 10003}``)供死信分类:
401/403 → forbidden、404 → not_found、429(``retry_after`` 限频)/5xx → 瞬态。

凭据安全基线同其余通道;全部 HTTP 经注入的 ``httpx.AsyncClient``。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

import httpx

from myia.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
)
from myia.push.directory import DirectoryDiscoverUnsupported
from myia.push.ntfy import build_message
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget
from myia.push.telegram import split_message
from myia.push.templates import TemplateRenderError, TemplateRenderer
from myia.schema import CredentialResolveError, resolve_credential

__all__ = [
    "API_BASE",
    "CHANNEL_ID_RE",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "DiscordChannel",
    "MESSAGE_LIMIT",
]

logger = logging.getLogger(__name__)

#: Discord Bot REST 基址(v10,蓝本同版)。
API_BASE = "https://discord.com/api/v10"
#: Bot token 凭据引用缺省(蓝本同名 env ``DISCORD_BOT_TOKEN``)。
DEFAULT_TOKEN_ENV_REF = "env:DISCORD_BOT_TOKEN"
#: 接收频道 id 凭据引用缺省。
DEFAULT_TARGET_ENV_REF = "env:DISCORD_CHANNEL_ID"
#: content 官方硬上限 2000(蓝本同值);按字符拆,行边界优先。
MESSAGE_LIMIT = 2000
#: 直达 id 形态:Discord snowflake(17-20 位数字,官方 Twitter 雪花同源)。
CHANNEL_ID_RE = re.compile(r"^\d{16,20}$")


class DiscordChannel(TrendAwareChannel):
    """``discord`` channel:每 ≤2000 字符块一次频道消息 POST。

    Args:
        target: 频道 id 的凭据引用(``env:DISCORD_CHANNEL_ID`` style,发送期
            解析);与 ``targets`` 定向配置互斥可省,此时频道由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        token: 预解析 bot token(构造注入,测试用);省略 → 发送期从
            :data:`DEFAULT_TOKEN_ENV_REF` 解析。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、HTTP 传输失败、非 JSON 响应、
            非 2xx(原厂 ``message``/``code`` 进文案),或模板渲染失败。
    """

    name = "discord"
    #: 目录寻址已开(context.target 优先,legacy target 兜底);协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        token: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._token = token
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 逐块 POST;定向优先(``context.target.chat_id``/``thread_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success). 块序发送,
                中途失败整通道报错(telegram 同款取舍)。
        """
        token = self._resolve_token()
        channel, thread_id = self._resolve_channel(context)
        # thread_id 在场 = 话题内投递(蓝本 thread_id 分支:改投话题端点)。
        endpoint_id = thread_id or channel
        for text in split_message(self._compose(items, context), limit=MESSAGE_LIMIT):
            await self._post_message(token, endpoint_id, text)
        logger.debug(
            "discord 发送完成: slot=%s kind=%s count=%d channel_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        """消息文本:用户模板优先,缺省内置纯文本版式(ntfy 同款复用)。"""
        if self._template is None:
            return build_message(items, context)
        # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
        # template_render_error(语法错误已在加载期被 schema 拒绝)。
        try:
            return self._renderer.render(
                self._template, items, context, **self.trend_render_kwargs()
            )
        except TemplateRenderError as exc:
            raise PushSendError(
                "template_render_error", f"push[].template 渲染失败: {exc}"
            ) from exc

    # ------------------------------------------------------------ credentials

    def _resolve_token(self) -> str:
        if self._token is not None:
            return self._token
        try:
            return resolve_credential(DEFAULT_TOKEN_ENV_REF)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"discord bot 凭据解析失败: {exc}") from exc

    def _resolve_channel(self, context: SendContext) -> tuple[str, str | None]:
        """定向优先 → ``(channel_id, thread_id)``;两路全缺 → missing_target。"""
        if context.target is not None:
            channel = context.target.chat_id.strip()
            thread_id = (context.target.thread_id or "").strip() or None
        elif self._target is not None:
            channel = self._resolve_ref(self._target, "discord target")
            thread_id = None
        else:
            raise PushSendError(
                "missing_target",
                "discord 两条寻址路径均缺席:未配置 legacy target(频道 id 引用),"
                "本次发送也未携带 context.target",
            )
        if not CHANNEL_ID_RE.fullmatch(channel):
            raise PushSendError(
                "invalid_credential_ref",
                f"discord 频道 id 形态非法(须为 17-20 位数字 snowflake): {channel[:40]!r}",
            )
        if thread_id is not None and not CHANNEL_ID_RE.fullmatch(thread_id):
            raise PushSendError(
                "invalid_credential_ref",
                f"discord 话题 id 形态非法(须为 17-20 位数字 snowflake): {thread_id[:40]!r}",
            )
        return channel, thread_id

    def _resolve_ref(self, reference: str, label: str) -> str:
        try:
            value = resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败: {exc}") from exc
        return value

    # ------------------------------------------------------------- send

    async def _post_message(self, token: str, channel_id: str, text: str) -> None:
        """一块正文:``POST /channels/{id}/messages``;2xx 即成功。"""
        url = f"{API_BASE}/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json={"content": text})
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json={"content": text})
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"discord 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            # HTTP <status> + 原厂 body 片段进文案:401/403 → forbidden、
            # 404(Unknown Channel)→ not_found、429(限频)/5xx → 瞬态。
            raise PushSendError(
                "discord_api_error",
                f"discord HTTP {response.status_code}: {response.text[:200]!r}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"discord 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, dict) or not data.get("id"):
            raise PushSendError(
                "invalid_response",
                f"discord 响应缺消息 id: {str(data)[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """discord 无自动发现(蓝本事实:出站 REST 无列表消费)。"""
        raise DirectoryDiscoverUnsupported(
            "discord 无自动发现(蓝本事实):本档未消费 guild/channel 列表 API;"
            "直达写 discord:<snowflake 频道 id>,常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:雪花 id(17-20 位数字)不经目录。

        非数字形态(名称等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if CHANNEL_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="discord", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
