"""Mattermost 通道:自建服务器的 REST v4(token)或 incoming webhook 双路。

蓝本归属(10-03-messaging-w3-longtail 组二):出站形态对照 Hermes
``plugins/platforms/mattermost/adapter.py``(NousResearch/Hermes-Agent,MIT)
的 ``_api_post("posts", {"channel_id": …, "message": …})`` + Bearer token、
``MAX_POST_LENGTH = 4000`` 量级;incoming-webhook 一-shot POST 形态取
Mattermost 官方「Incoming Webhooks」契约(``POST {url}`` body
``{"text": …}``,2xx 即成功)。MYIA 按本档语义重写,不整块复制。

- **REST 路(主路,telegram 范式)**:``POST {server}/api/v4/posts``,
  ``Authorization: Bearer <token>``,body ``{"channel_id": <26 位 id>,
  "message": <≤4000 字符分段>}``;成功 201。错误应答 JSON
  ``{"id": "app.channel.not_found.app_error", "message": …, "status_code": …}``,
  文案保留 ``HTTP <status>`` + 原厂 id/message 供死信分类
  (:func:`myia.push.delivery.classify_dead_error`:403 → forbidden、
  404(channel 不存在)→ not_found、429/5xx → 瞬态)。
- **webhook 路(legacy 单 target 退路,ntfy 范式)**:legacy target 引用解析
  出整条 webhook URL(``https://mm.example.com/hooks/xxx``)时走一-shot
  POST ``{"text": …}``(无 Bearer,webhook 自带鉴权);定向
  (``context.target``)给出 26 位 channel id 走 REST 路。两路由**解析值
  形态**分发,与 ntfy「topic 或整条 URL」同构。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用;两路全缺报 ``missing_target``。直达解析
26 位小写字母数字 channel id(蓝本 id 形态);频道名(``town-square``)走
目录别名。目录无自动发现(蓝本列表 API 建立在常驻 websocket 会话上,MYIA
出站-only 不含),抛
:class:`~myia.push.directory.DirectoryDiscoverUnsupported`。

凭据安全基线同其余通道:YAML 只写 ``env:``/``keychain:`` 引用,发送期才
解析;错误文案只带引用名,绝不带解析值。All HTTP I/O goes through an
injectable ``httpx.AsyncClient`` — tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence
from urllib.parse import urlsplit

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
    "CHANNEL_ID_RE",
    "DEFAULT_SERVER_ENV_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MESSAGE_LIMIT",
    "MattermostChannel",
    "POSTS_PATH",
]

logger = logging.getLogger(__name__)

#: Personal/team access token 引用(Bearer;发送期解析,值永不入日志)。
DEFAULT_TOKEN_ENV_REF = "env:MATTERMOST_TOKEN"
#: 自建服务器 base URL 引用(REST 路必填;webhook 路 URL 自带 host)。
DEFAULT_SERVER_ENV_REF = "env:MATTERMOST_SERVER"
#: Legacy 单频道 target 引用(channel id 或整条 webhook URL 的 env 引用)。
DEFAULT_TARGET_ENV_REF = "env:MATTERMOST_CHANNEL_ID"
#: 单条 post message 上限(蓝本 ``MAX_POST_LENGTH = 4000`` 同量级)。
MESSAGE_LIMIT = 4000
#: Mattermost channel id 形态(蓝本/官方:26 位小写字母数字)。
CHANNEL_ID_RE = re.compile(r"^[a-z0-9]{26}$")
#: REST 发送端点路径(拼在 server base URL 之后)。
POSTS_PATH = "/api/v4/posts"


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


class MattermostChannel(TrendAwareChannel):
    """``mattermost`` channel:REST ``POST /api/v4/posts`` 或 webhook 一-shot POST。

    Args:
        target: legacy target 引用,解析值二形:26 位 channel id(→ REST 路)
            或整条 incoming-webhook URL(→ webhook 路);定向(``targets``)
            在场时可省——两条寻址路径至少一条,否则发送期报 ``missing_target``。
        server_ref: 自建服务器 base URL 引用;省略 → :data:`DEFAULT_SERVER_ENV_REF`
            (仅 REST 路需要)。
        token_ref: access token 引用;省略 → :data:`DEFAULT_TOKEN_ENV_REF`
            (仅 REST 路需要)。
        template: 可选用户模板(Jinja2);省略 → 内置纯文本版式(ntfy 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次发送
            自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、target 形态既非 id 亦非 URL、HTTP 传输
            失败、非 JSON 响应或 Mattermost 返回错误(文案带 HTTP 状态 +
            原厂 id/message)。分段按序发送,中途失败整次失败(留池重试契约)。
    """

    name = "mattermost"
    #: 目录寻址已开:context.target 优先,legacy target 兜底;协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        server_ref: str | None = None,
        token_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._server_ref = server_ref
        self._token_ref = token_ref
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 按 target 值形态分发(webhook URL / REST channel id)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_target()
        )
        parts = self._compose(items, context)
        if _is_http_url(chat_id):
            # webhook 路:URL 自带 host 与鉴权,一-shot POST {"text": …}。
            for text in parts:
                await self._post_webhook(chat_id, text)
        else:
            token = self._resolve_credential(
                self._token_ref or DEFAULT_TOKEN_ENV_REF, "mattermost token"
            )
            server = self._resolve_credential(
                self._server_ref or DEFAULT_SERVER_ENV_REF, "mattermost server"
            )
            if not CHANNEL_ID_RE.fullmatch(chat_id):
                raise PushSendError(
                    "invalid_credential_ref",
                    "mattermost target 形态非法(须为 26 位 channel id 或整条 "
                    f"webhook URL): {chat_id[:120]!r}",
                )
            for text in parts:
                await self._post_rest(server, token, chat_id, text)
        logger.debug(
            "mattermost 已提交: slot=%s kind=%s count=%d parts=%d webhook=%s",
            context.slot,
            context.kind,
            len(items),
            len(parts),
            _is_http_url(chat_id),
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

    def _resolve_credential(self, reference: str, label: str) -> str:
        try:
            return resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"mattermost {label} 凭据解析失败: {exc}") from exc

    def _resolve_target(self) -> str:
        if self._target is None:
            raise PushSendError(
                "missing_target",
                "mattermost 未配置 legacy target(env:/keychain: 引用,26 位 "
                "channel id 或整条 webhook URL),本次发送也未携带 context.target"
                "——两条寻址路径至少一条在场",
            )
        try:
            value = resolve_credential(self._target).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"mattermost target 解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"mattermost target 引用 {self._target!r} 解析结果为空",
            )
        return value

    # ---------------------------------------------------------------- post

    async def _post_webhook(self, url: str, text: str) -> None:
        """webhook 路:``POST {url}`` JSON ``{"text": …}``;非 2xx/3xx 报错。"""
        try:
            if self._client is not None:
                response = await self._client.post(url, json={"text": text})
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json={"text": text})
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"mattermost webhook 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 300:
            # 文案带 HTTP 状态与响应体片段:死信分类按「HTTP 403/404」命中。
            raise PushSendError(
                "mattermost_api_error",
                f"mattermost HTTP {response.status_code}: {response.text[:200]!r}",
            )

    async def _post_rest(
        self, server: str, token: str, channel_id: str, text: str
    ) -> dict[str, Any]:
        """REST 路:``POST {server}/api/v4/posts``(Bearer,201 即成功)。"""
        url = f"{server.rstrip('/')}{POSTS_PATH}"
        headers = {"Authorization": f"Bearer {token}"}
        body = {"channel_id": channel_id, "message": text}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"mattermost 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"mattermost 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not response.is_success:
            # 错误应答 {"id": "app.channel.not_found.app_error", "message": …};
            # 文案保留 HTTP 状态 + 原厂 id/message:403 → forbidden、404 → not_found。
            api_id = data.get("id") if isinstance(data, dict) else None
            api_message = (
                data.get("message") if isinstance(data, dict) else response.text[:200]
            )
            raise PushSendError(
                "mattermost_api_error",
                f"mattermost HTTP {response.status_code}: {api_id}: {api_message}",
            )
        return dict(data)

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """mattermost 无出站目录发现:结构化说明,而非假装刷新。

        蓝本的频道枚举建立在常驻 websocket 会话(``api/v4/websocket``)上,
        MYIA 出站-only 无会话可复用。条目唯一来源 = 别名文件手工登记 +
        直达 26 位 channel id。
        """
        raise DirectoryDiscoverUnsupported(
            "mattermost 无出站目录发现(蓝本走常驻 websocket 会话,MYIA 出站-only 不含):"
            "直达写 mattermost:<26位channel_id>,频道名可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:26 位小写字母数字 channel id 不经目录。

        其余(频道名 ``town-square``、中文别名等)返回 None,调用方回落
        目录四路径解析——别名命名不受 id 字符集限制。
        """
        value = ref.strip()
        if CHANNEL_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="mattermost", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
