"""Home Assistant 通道:REST 通知出站(one-shot ``POST /api/services/notify/notify``)。

蓝本归属(10-03-messaging-w3-longtail 组三):出站形态移植自 Hermes
``plugins/platforms/homeassistant/adapter.py`` 的 ``_standalone_send``
(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/homeassistant/adapter.py``)。
MYIA 只做出站定向推送(入站 WebSocket ``state_changed`` 订阅是蓝本的另一
半,MYIA 零入站),按本档语义重写,不整块复制:

- **one-shot service call**:
  ``POST {HASS_URL}/api/services/notify/notify``,``Authorization: Bearer``
  长期访问令牌(蓝本 ``_auth_headers`` 同款),JSON payload
  ``{"title", "message", "target"}``——``title`` = card_title(跨通道标题
  一致,HA notify 服务原生支持 title),``target`` = notify 目标(HA 侧
  注册的通知目标名,如 ``mobile_app_<device>``/群组名);``< 300`` 即成功
  (蓝本 adapter ``send`` 同判据)。
- **4096 截断**:蓝本 ``MAX_MESSAGE_LENGTH`` 同款;超长截到
  :data:`MESSAGE_LIMIT` 并告警,不拆多条。
- **错误码 → 死信映射(W2 模板探查,已对照 HA REST API 文档)**:错误
  文案保留 ``HTTP <status>`` 与响应体片段,经
  :func:`shishi.push.delivery.classify_dead_error` 判定——HTTP 403 →
  ``forbidden``、HTTP 404 → ``not_found``(服务/路径不存在)、429/5xx/
  传输失败 → 瞬态不标。HTTP 401(令牌失效)是配置级硬失败,但 core
  分类器无 ``401`` marker → 按瞬态处理(令牌可被主人随时更换重试成功,
  误标死信的代价高于多试一轮)。

寻址(design D1):``supports_targeting=True``;``context.target.chat_id``
优先、退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 :data:`DEFAULT_TARGET_ENV_REF`),
两路全缺 ``missing_target``;目标形态非法(实体 id/slug 之外)→
``invalid_credential_ref``。直达 = 实体 id/notify 目标 slug
(:data:`TARGET_RE`,``notify.mobile``/``mobile_app_pixel`` 形态);无目录
发现(:class:`DirectoryDiscoverUnsupported`)——HA 无「列出通知目标」
REST API(蓝本事实),条目唯一来源 = 别名手工登记 + 直达目标名。

凭据安全基线同其余通道:HASS_URL/HASS_TOKEN 全部 ``env:``/``keychain:``
引用,发送期才解析,错误只带引用名。

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch a real Home Assistant.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence
from urllib.parse import urlsplit

import httpx

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
)
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.feishu_card import card_title
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_HASS_TOKEN_ENV_REF",
    "DEFAULT_HASS_URL_ENV_REF",
    "DEFAULT_TARGET_ENV_REF",
    "MESSAGE_LIMIT",
    "HomeAssistantChannel",
    "TARGET_RE",
    "notify_url",
]

logger = logging.getLogger(__name__)

#: HA 实例基址引用(发送期解析;形如 ``http://homeassistant.local:8123``,
#: 蓝本 ``HASS_URL`` 同名 env)。MYIA 不带蓝本的缺省 URL 猜测——引用缺席
#: 即 ``env_var_missing`` fail-fast,绝不猜主机名。
DEFAULT_HASS_URL_ENV_REF = "env:HASS_URL"
#: HA 长期访问令牌引用(蓝本 ``HASS_TOKEN`` 同名 env)。
DEFAULT_HASS_TOKEN_ENV_REF = "env:HASS_TOKEN"
#: legacy notify 目标引用(定向 ``targets`` 在场时可省,schema 层允许)。
DEFAULT_TARGET_ENV_REF = "env:HASS_TARGET"
#: 单条 message 上限(蓝本 ``MAX_MESSAGE_LENGTH`` 同款;超长截断不拆多条)。
MESSAGE_LIMIT = 4096
#: 直达 notify 目标形态:实体 id(``domain.object_id``,如 ``notify.mobile``)
#: 或设备/群组 slug(``mobile_app_pixel``);中文别名不匹配 → 回落目录。
TARGET_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$")


def notify_url(hass_url: str) -> str:
    """``{HASS_URL}`` 基址 → notify 服务端点(尾斜杠容忍)。"""
    return f"{hass_url.strip().rstrip('/')}/api/services/notify/notify"


def _validate_base_url(value: str, reference: str) -> str:
    """基址形态校验:http(s) URL,否则 ``invalid_credential_ref``。"""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PushSendError(
            "invalid_credential_ref",
            f"homeassistant 基址引用 {reference!r} 解析结果不是 http(s) URL: {value[:80]!r}",
        )
    return value


class HomeAssistantChannel(TrendAwareChannel):
    """``homeassistant`` channel:每条消息一次 one-shot notify 服务调用。

    Args:
        target: notify 目标的凭据引用(``env:HASS_TARGET`` style,发送期
            解析);定向(``targets``)在场时可省(schema 允许),目标由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);渲染输出为 message,标题仍为
            card_title。省略 → 内置纯文本版式(ntfy ``build_message`` 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        url_ref: HA 基址引用(缺省 :data:`DEFAULT_HASS_URL_ENV_REF`)。
        token_ref: HA 长期访问令牌引用(缺省 :data:`DEFAULT_HASS_TOKEN_ENV_REF`)。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、两条寻址路径全缺(``missing_target``)、
            基址/目标形态非法(``invalid_credential_ref``)、HTTP 传输失败
            (``http_error``)、非 2xx/3xx 响应(``homeassistant_api_error``,
            文案保留 ``HTTP <status>`` 与响应体片段供死信分类)或模板渲染
            失败。
    """

    name = "homeassistant"
    #: 目录寻址已开(context.target 优先,legacy target 兜底)。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        url_ref: str | None = None,
        token_ref: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._url_ref = url_ref or DEFAULT_HASS_URL_ENV_REF
        self._token_ref = token_ref or DEFAULT_HASS_TOKEN_ENV_REF
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot service POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        target = self._resolve_target(context)
        url = self._resolve_url()
        token = self._resolve_token()
        title, message = self._compose(items, context)
        await self._post(url, token, title, message, target)
        logger.debug(
            "homeassistant 已通知: slot=%s kind=%s count=%d target_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> tuple[str, str]:
        """(标题, 正文):标题恒为 card_title,正文 = 模板/内置版式(截 4096)。"""
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
        if len(text) > MESSAGE_LIMIT:
            logger.warning(
                "homeassistant 正文超限,截断 %d → %d 字符", len(text), MESSAGE_LIMIT
            )
            text = text[:MESSAGE_LIMIT]
        return card_title(context), text

    # ---------------------------------------------------------- credentials

    def _resolve_url(self) -> str:
        """基址引用 → notify 服务端点(形态校验 + 尾斜杠归一)。"""
        try:
            value = resolve_credential(self._url_ref)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"homeassistant 基址解析失败: {exc}") from exc
        value = value.strip()
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"homeassistant 基址引用 {self._url_ref!r} 解析结果为空",
            )
        return notify_url(_validate_base_url(value, self._url_ref))

    def _resolve_token(self) -> str:
        try:
            value = resolve_credential(self._token_ref)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"homeassistant 令牌解析失败: {exc}") from exc
        value = value.strip()
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"homeassistant 令牌引用 {self._token_ref!r} 解析结果为空",
            )
        return value

    def _resolve_target(self, context: SendContext) -> str:
        """定向优先,退回 legacy target 引用;全缺 ``missing_target``。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"homeassistant target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "homeassistant 两条寻址路径均缺席:未配置 legacy target(notify 目标引用),"
                "本次发送也未携带 context.target",
            )
        if not TARGET_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1)。
            raise PushSendError(
                "invalid_credential_ref",
                f"homeassistant notify 目标形态非法(实体 id 或 slug):"
                f"得到 {len(value)} 字符的值,不匹配该形态(解析值不回显)",
            )
        return value

    # ---------------------------------------------------------------- post

    async def _post(self, url: str, token: str, title: str, message: str, target: str) -> None:
        """One-shot service POST;``< 300`` 即成功(蓝本同判据)。"""
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"title": title, "message": message, "target": target}
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"homeassistant 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 300:
            # HTTP 状态与响应体片段进文案:死信分类按「HTTP 403/404」命中
            # (403 → forbidden、404 → not_found),HA 原文供诊断。
            raise PushSendError(
                "homeassistant_api_error",
                f"homeassistant HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """homeassistant 无目录发现:REST API 无「列出通知目标」端点(蓝本事实)。"""
        raise DirectoryDiscoverUnsupported(
            "homeassistant 无自动发现:HA REST API 无「列出通知目标」端点;"
            "直达写 homeassistant:<目标名>,常用目标可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``homeassistant:<实体 id/目标 slug>`` 不经目录。

        实体 id(``domain.object_id``)或设备/群组 slug(``mobile_app_pixel``);
        其余(中文别名等)返回 None,调用方回落目录四路径。
        """
        value = ref.strip()
        if TARGET_RE.fullmatch(value):
            return ChannelTarget(
                platform="homeassistant", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
