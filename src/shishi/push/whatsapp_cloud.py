"""WhatsApp Cloud API 通道:Graph ``POST /{version}/{phone_number_id}/messages``。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态移植自 Hermes
``gateway/platforms/whatsapp_cloud.py`` 的 ``send``/``_outbound_payload``/
``_post_messages``/``_response_error``(NousResearch/Hermes-Agent,MIT;上游
路径 ``~/.hermes/hermes-agent/gateway/platforms/whatsapp_cloud.py``)。MYIA 按
本档语义重写,不整块复制:

- **Graph 直发**:``POST https://graph.facebook.com/{api_version}/{phone_number_id}/messages``
  + ``Authorization: Bearer <永久访问令牌>``;body
  ``{"messaging_product": "whatsapp", "recipient_type": "individual", "to": <wa_id>,
  "type": "text", "text": {"body": <块>, "preview_url": true}}``(蓝本同构)。
  HTTP 200 且响应含 ``messages[].id``(wamid)即成功;非 200 时错误体形态
  ``{"error": {"message", "type", "code"}}``,``code`` 与 message 原样进文案
  (蓝本 ``_response_error`` 同判据)。
- **4096 分段**:text.body 官方上限 4096(蓝本 ``whatsapp_common.MAX_MESSAGE_LENGTH``
  同值);长文按行边界拆多条(:func:`telegram.split_message` 复用),逐块顺序发送。
- **api_version 可覆写**:蓝本缺省 ``v20.0``(:data:`DEFAULT_GRAPH_API_VERSION`,
  Meta 每年滚动发版、旧版约两年退役;配置层经构造参数换新版本号即可,
  端点形态跨版本稳定)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(``+``E.164 或纯数字 wa_id),退回 legacy ``target`` 引用(**须显式
配置,无运行期 env 缺省回退**;推荐引用名 ``env:WHATSAPP_CLOUD_TO``)。
直达形态:E.164 电话号码。目录无自动发现
(蓝本事实:出站无列表路径),别名手工登记。

凭据安全基线同其余通道:token 与 phone_number_id 全为 ``env:``/``keychain:``
引用,发送期才解析,错误只带引用名;全部 HTTP 经注入的 ``httpx.AsyncClient``。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

import httpx

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
)
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.telegram import split_message
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_API_VERSION",
    "DEFAULT_PHONE_ID_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MESSAGE_LIMIT",
    "PHONE_RE",
    "GRAPH_API_BASE",
    "WhatsAppCloudChannel",
]

logger = logging.getLogger(__name__)

#: Graph API 基址(蓝本 ``GRAPH_API_BASE`` 同款)。
GRAPH_API_BASE = "https://graph.facebook.com"
#: Graph 版本缺省(蓝本 ``DEFAULT_API_VERSION = "v20.0"``;构造可覆写)。
DEFAULT_API_VERSION = "v20.0"
#: 永久访问令牌(System User token)凭据引用缺省。
DEFAULT_TOKEN_ENV_REF = "env:WHATSAPP_CLOUD_TOKEN"
#: 发送方电话号码 id(Cloud API 注册号码)凭据引用缺省。
DEFAULT_PHONE_ID_REF = "env:WHATSAPP_CLOUD_PHONE_NUMBER_ID"
#: 接收方 wa_id 的推荐引用名(显式配置 ``target`` 用;运行期不自动回退)。
DEFAULT_TARGET_ENV_REF = "env:WHATSAPP_CLOUD_TO"
#: text.body 官方上限 4096(蓝本 whatsapp_common 同值)。
MESSAGE_LIMIT = 4096
#: 直达 wa_id 形态:E.164(可带 ``+`` 前缀,7-15 位数字主体)。
PHONE_RE = re.compile(r"^\+?[1-9]\d{6,14}$")


class WhatsAppCloudChannel(TrendAwareChannel):
    """``whatsapp_cloud`` channel:每 ≤4096 字符块一次 Graph messages POST。

    Args:
        target: 接收方 wa_id 的凭据引用(``env:WHATSAPP_CLOUD_TO`` style,
            发送期解析);与 ``targets`` 定向配置互斥可省,此时接收方由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        token: 预解析访问令牌(构造注入,测试用);省略 → 发送期从
            :data:`DEFAULT_TOKEN_ENV_REF` 解析。
        phone_number_id: 预解析发送方电话号码 id(构造注入,测试用);省略 →
            发送期从 :data:`DEFAULT_PHONE_ID_REF` 解析。
        api_version: Graph 版本段(缺省 :data:`DEFAULT_API_VERSION`)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、HTTP 传输失败、非 JSON 响应、非 200
            (原厂 ``error.code``/``message`` 进文案)、响应缺 ``messages``,
            或模板渲染失败。
    """

    name = "whatsapp_cloud"
    #: 目录寻址已开(context.target 优先,legacy target 兜底);协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        token: str | None = None,
        phone_number_id: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._token = token
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 逐块 POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success). 块序发送,
                中途失败整通道报错(telegram 同款取舍)。
        """
        token = self._resolve_token()
        phone_number_id = self._resolve_phone_id()
        wa_id = self._resolve_recipient(context)
        url = f"{GRAPH_API_BASE}/{self._api_version}/{phone_number_id}/messages"
        for text in split_message(self._compose(items, context), limit=MESSAGE_LIMIT):
            await self._post_message(token, url, wa_id, text)
        logger.debug(
            "whatsapp_cloud 发送完成: slot=%s kind=%s count=%d target_ref=%s",
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
            raise PushSendError(exc.code, f"whatsapp_cloud 令牌解析失败: {exc}") from exc

    def _resolve_phone_id(self) -> str:
        if self._phone_number_id is not None:
            value = self._phone_number_id.strip()
        else:
            try:
                value = resolve_credential(DEFAULT_PHONE_ID_REF).strip()
            except CredentialResolveError as exc:
                raise PushSendError(
                    exc.code, f"whatsapp_cloud 电话号码 id 解析失败: {exc}"
                ) from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"whatsapp_cloud 电话号码 id 为空(引用 {DEFAULT_PHONE_ID_REF!r})",
            )
        return value

    def _resolve_recipient(self, context: SendContext) -> str:
        """定向优先;形态非法/两路全缺如实报错(绝不猜收件人)。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"whatsapp_cloud target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "whatsapp_cloud 两条寻址路径均缺席:未配置 legacy target(wa_id 引用),"
                "本次发送也未携带 context.target",
            )
        if not PHONE_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1;号码亦属通讯录敏感面)。
            raise PushSendError(
                "invalid_credential_ref",
                f"whatsapp_cloud wa_id 形态非法(须为 E.164 电话号码):"
                f"得到 {len(value)} 字符的值,不匹配该形态(解析值不回显)",
            )
        return value

    # ------------------------------------------------------------- send

    async def _post_message(self, token: str, url: str, wa_id: str, text: str) -> None:
        """一块正文:Graph messages POST(蓝本 ``_outbound_payload`` 同构)。"""
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": wa_id,
            "type": "text",
            "text": {"body": text, "preview_url": True},
        }
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"whatsapp_cloud 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code != 200:
            # 非 200 先于 JSON 解析报错(错误体可能非 JSON);蓝本
            # _response_error 同判据:graph error code + HTTP status + message
            # 原样进文案(死信分类按 403/404/429/5xx 命中)。
            try:
                data = response.json()
            except ValueError:
                data = None
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else response.text[:200]
            raise PushSendError(
                "whatsapp_cloud_api_error",
                f"whatsapp_cloud graph error {code} (HTTP {response.status_code}): {message}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"whatsapp_cloud 响应不是 JSON(HTTP {response.status_code}):"
                f" {response.text[:200]!r}",
            ) from exc
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list) or not messages:
            raise PushSendError(
                "invalid_response",
                f"whatsapp_cloud 响应缺 messages[](wamid): {str(data)[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """whatsapp_cloud 无自动发现(蓝本事实:出站无联系人列表路径)。"""
        raise DirectoryDiscoverUnsupported(
            "whatsapp_cloud 无自动发现(蓝本事实):Cloud API 无「列出收件人」路径;"
            "直达写 whatsapp_cloud:+8613800000000(E.164),常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:E.164 电话号码(可带 ``+``)不经目录。

        非号码形态(备注名等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if PHONE_RE.fullmatch(value):
            return ChannelTarget(
                platform="whatsapp_cloud", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
