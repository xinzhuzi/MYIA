"""Microsoft Graph 出站通道:client credentials → ``POST /chats/{id}/messages``。

蓝本归属(10-03-messaging-w3-longtail 组一):Hermes 侧的
``gateway/platforms/msgraph_webhook.py`` 是 **Graph change notification 的
入站接收器**(订阅回调,``send()`` 为日志空实现;NousResearch/Hermes-Agent,
MIT;上游路径 ``~/.hermes/hermes-agent/gateway/platforms/msgraph_webhook.py``)。
MYIA 推送层零入站(定向出站决议不变)→ 本通道取该平台名下可出站的官方
Graph 直发形态【偏离注记:MYIA 侧无入站面,蓝本出站为空;微信 weixin 通道
同款「蓝本能力半边不在,按官方 API 补出站」先例】:

- **token 生命周期**(:meth:`MSGraphWebhookChannel._ensure_token`):
  ``POST https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token``
  (form-encoded ``grant_type=client_credentials&client_id=…&client_secret=…&
  scope=https://graph.microsoft.com/.default``)→ ``{"access_token",
  "expires_in"}``(缺省 3600s);进程内缓存、剩余 > 60s 才算有效(Hermes
  teams ``_standalone_send`` 的 token 两段式同款)。应用需具 **应用权限
  ``Chat.ReadWrite``**(app-only 发 chat 消息的官方路径;``ChatMessage.Send``
  是委托权限不可 app-only——事实核订)。
- **chatMessage 直发**:``POST https://graph.microsoft.com/v1.0/chats/{chat_id}/messages``
  + Bearer + body ``{"body": {"content": <文本>, "contentType": "text"}}``;
  **201 Created** 即成功(应答是消息资源 JSON,含 ``id``)。**只寻址 chat**
  (1:1/群聊,chat id 形如 ``19:…@unq.gbl.spaces``/``19:…@thread.v2``)——
  团队频道消息的 app-only 发送需 RSC 权限的已装 bot(官方事实,本档非目标;
  频道场景走 teams 通道的 Workflows webhook)。
- **无长度硬限**:chatMessage body.content 无已核实的硬字符上限,不拆条
  (weixin 通道同款取舍:平台侧分块是平台的事)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(Graph chat id),退回 legacy ``target`` 引用(**须显式配置,无运行期
env 缺省回退**;推荐引用名
``env:MSGRAPH_WEBHOOK_CHAT_ID``)。直达形态:``19:`` 前缀 chat id(含
``@unq.gbl.spaces``/``@thread.v2`` 等资源后缀)。目录无自动发现(蓝本事实:
出站无列表路径——``/chats`` 列表是委托权限面,app-only 不可用),别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段(Graph 错误体
``{"error": {"code", "message"}}``)供死信分类:401/403(token 失效/应用无
Chat.ReadWrite)→ forbidden、404(chat 不存在)→ not_found、429(限频)/
5xx → 瞬态。

凭据安全基线同其余通道:tenant/client id/secret 全为 ``env:``/``keychain:``
引用,发送期才解析,错误只带引用名;全部 HTTP 经注入的 ``httpx.AsyncClient``。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Sequence
from urllib.parse import quote

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
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CHAT_ID_RE",
    "DEFAULT_CLIENT_ID_REF",
    "DEFAULT_CLIENT_SECRET_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TENANT_REF",
    "GRAPH_API_BASE",
    "MSGraphWebhookChannel",
    "TOKEN_EXPIRY_MARGIN_SECONDS",
    "TOKEN_TTL_SECONDS",
    "TOKEN_URL_TEMPLATE",
]

logger = logging.getLogger(__name__)

#: Graph v1.0 基址(消息端点 ``/v1.0/chats/{id}/messages``)。
GRAPH_API_BASE = "https://graph.microsoft.com"
#: OAuth2 v2.0 token 端点(租户段 URL 编码拼入;``{tenant_id}`` 占位)。
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
#: access_token 有效期缺省(秒;v2.0 client credentials 应答常为 3600)。
TOKEN_TTL_SECONDS = 3600
#: 缓存提前失效余量(秒;Hermes teams 蓝本同判据)。
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
#: 租户 id 凭据引用缺省(GUID 形态;非密但随密钥组统一引用化)。
DEFAULT_TENANT_REF = "env:MSGRAPH_WEBHOOK_TENANT_ID"
#: 应用 client id 凭据引用缺省。
DEFAULT_CLIENT_ID_REF = "env:MSGRAPH_WEBHOOK_CLIENT_ID"
#: 应用 client secret 凭据引用缺省。
DEFAULT_CLIENT_SECRET_REF = "env:MSGRAPH_WEBHOOK_CLIENT_SECRET"
#: 接收 chat id 的推荐引用名(显式配置 ``target`` 用;运行期不自动回退)。
DEFAULT_TARGET_ENV_REF = "env:MSGRAPH_WEBHOOK_CHAT_ID"
#: 直达 chat id 形态:``19:`` 前缀(Graph chat/thread 资源 id 官方形态;
#: 主体为 base64url 字符集,后缀 ``@unq.gbl.spaces``/``@thread.v2`` 等)。
CHAT_ID_RE = re.compile(r"^19:[A-Za-z0-9_+=/@.-]{8,200}$")


class MSGraphWebhookChannel(TrendAwareChannel):
    """``msgraph_webhook`` channel:token 现取 → chatMessage 一次 POST。

    Args:
        target: Graph chat id 的凭据引用(``env:MSGRAPH_WEBHOOK_CHAT_ID``
            style,发送期解析);与 ``targets`` 定向配置互斥可省,此时 chat
            由 ``context.target`` 给出。
        tenant_ref: 租户 id 引用;省略 → :data:`DEFAULT_TENANT_REF`。
        client_id_ref: 应用 client id 引用;省略 → :data:`DEFAULT_CLIENT_ID_REF`。
        client_secret_ref: 应用 client secret 引用;省略 →
            :data:`DEFAULT_CLIENT_SECRET_REF`。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。
        clock: 时间源注入(秒;token 缓存寿命判据,测试可钉死)。

    Raises:
        PushSendError: 凭据解析失败、chat id 形态非法、token 获取失败、
            HTTP 传输失败、非 2xx(原厂 ``error.code``/``message`` 进文案),
            或模板渲染失败。
    """

    name = "msgraph_webhook"
    #: 目录寻址已开(context.target 优先,legacy target 兜底);协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        tenant_ref: str | None = None,
        client_id_ref: str | None = None,
        client_secret_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._target = target
        self._tenant_ref = tenant_ref or DEFAULT_TENANT_REF
        self._client_id_ref = client_id_ref or DEFAULT_CLIENT_ID_REF
        self._client_secret_ref = client_secret_ref or DEFAULT_CLIENT_SECRET_REF
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout
        self._clock = clock
        self._cached_token: str | None = None
        self._token_expires_at = 0.0

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot chatMessage POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        tenant_id, client_id, client_secret = self._resolve_credentials()
        chat_id = self._resolve_chat_id(context)
        token = await self._ensure_token(tenant_id, client_id, client_secret)
        body = {"body": {"content": self._compose(items, context), "contentType": "text"}}
        await self._post_message(token, chat_id, body)
        logger.debug(
            "msgraph_webhook 已提交: slot=%s kind=%s count=%d target_ref=%s",
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

    def _resolve_credentials(self) -> tuple[str, str, str]:
        """三凭据引用 → ``(tenant_id, client_id, client_secret)``。"""
        return (
            self._resolve_ref(self._tenant_ref, "msgraph_webhook tenant id"),
            self._resolve_ref(self._client_id_ref, "msgraph_webhook client id"),
            self._resolve_ref(self._client_secret_ref, "msgraph_webhook client secret"),
        )

    def _resolve_ref(self, reference: str, label: str) -> str:
        try:
            value = resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref", f"{label} 引用 {reference!r} 解析结果为空"
            )
        return value

    def _resolve_chat_id(self, context: SendContext) -> str:
        """定向优先;形态非法/两路全缺如实报错(绝不猜会话)。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"msgraph_webhook target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "msgraph_webhook 两条寻址路径均缺席:未配置 legacy target(chat id 引用),"
                "本次发送也未携带 context.target",
            )
        if not CHAT_ID_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1)。
            raise PushSendError(
                "invalid_credential_ref",
                "msgraph_webhook chat id 形态非法(须为 19: 前缀 Graph chat id,"
                f" 如 19:…@unq.gbl.spaces):得到 {len(value)} 字符的值,"
                "不匹配该形态(解析值不回显)",
            )
        return value

    # -------------------------------------------------------- token 生命周期

    async def _ensure_token(self, tenant_id: str, client_id: str, client_secret: str) -> str:
        """有效缓存(剩余 > 60s)复用;否则 client credentials 现取。

        MYIA 通道实例随派发重建,缓存是进程内的(一次 dispatch 批内复用,
        跨批现取——token 端点无紧限频,换取零落盘的凭据安全面)。
        """
        now = self._clock()
        if self._cached_token and now < self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS:
            return self._cached_token
        data = await self._post_token(tenant_id, client_id, client_secret)
        token = data.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise PushSendError(
                "invalid_response",
                f"msgraph_webhook token 应答缺 access_token: {str(data)[:200]!r}",
            )
        try:
            expires_in = float(data.get("expires_in") or TOKEN_TTL_SECONDS)
        except (TypeError, ValueError):
            expires_in = float(TOKEN_TTL_SECONDS)
        self._cached_token = token
        self._token_expires_at = self._clock() + expires_in
        return token

    async def _post_token(
        self, tenant_id: str, client_id: str, client_secret: str
    ) -> dict[str, Any]:
        """``POST oauth2/v2.0/token``(form-encoded;蓝本 teams 两段式同款)。"""
        url = TOKEN_URL_TEMPLATE.format(tenant_id=quote(tenant_id, safe=""))
        form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": f"{GRAPH_API_BASE}/.default",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            if self._client is not None:
                response = await self._client.post(url, data=form, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"msgraph_webhook token 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"msgraph_webhook token 响应不是 JSON(HTTP {response.status_code}):"
                f" {response.text[:200]!r}",
            ) from exc
        if response.status_code >= 400:
            # OAuth 错误体 {"error": "invalid_client", "error_description": …};
            # 原厂片段进文案(token 级失败无死信语义,分类器判瞬态/None)。
            error = data.get("error") if isinstance(data, dict) else None
            raise PushSendError(
                "msgraph_webhook_api_error",
                f"msgraph_webhook token 获取失败: HTTP {response.status_code}"
                f" error={error} {response.text[:160]!r}",
            )
        if not isinstance(data, dict):
            raise PushSendError(
                "invalid_response",
                f"msgraph_webhook token 响应不是 JSON 对象: {str(data)[:200]!r}",
            )
        return data

    # ------------------------------------------------------------- send

    async def _post_message(self, token: str, chat_id: str, body: dict[str, Any]) -> None:
        """``POST /v1.0/chats/{id}/messages``;201 即成功(应答含消息 id)。

        chat id 含 ``:``/``@`` 等保留字符 → 路径段 URL 编码(官方要求)。
        """
        url = f"{GRAPH_API_BASE}/v1.0/chats/{quote(chat_id, safe='')}/messages"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"msgraph_webhook 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 400:
            # HTTP <status> + 原厂 body 片段(Graph 错误体 {"error":
            # {"code","message"}})进文案:401/403 → forbidden、404(chat 不
            # 存在)→ not_found、429/5xx → 瞬态。
            raise PushSendError(
                "msgraph_webhook_api_error",
                f"msgraph_webhook HTTP {response.status_code}: {response.text[:200]!r}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"msgraph_webhook 响应不是 JSON(HTTP {response.status_code}):"
                f" {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, dict) or not data.get("id"):
            raise PushSendError(
                "invalid_response",
                f"msgraph_webhook 响应缺消息 id: {str(data)[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """msgraph_webhook 无自动发现(蓝本事实:出站无列表路径)。

        ``GET /chats`` 列表是委托权限面(app-only 的 ``Chat.ReadBasic.All``
        只覆盖只读且需管理员同意,非本档目标);chat id 从 Teams/Graph 复制。
        """
        raise DirectoryDiscoverUnsupported(
            "msgraph_webhook 无自动发现(蓝本事实):app-only 出站无会话列表路径;"
            "直达写 msgraph_webhook:19:…(Graph chat id),常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``19:`` 前缀 Graph chat id 不经目录。

        非命中形态(备注名等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if CHAT_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="msgraph_webhook", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
