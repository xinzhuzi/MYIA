"""A2A 通道:Agent-to-Agent 协议 v1.0 JSON-RPC 出站(one-shot ``SendMessage``)。

蓝本归属(10-03-messaging-w3-longtail 组三):出站形态移植自 Hermes
``plugins/platforms/a2a/tools.py`` 的 ``_send_task`` / ``_http_post_json`` /
``_auth_header``(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/a2a/``)。A2A 是 Linux Foundation
开放标准,HTTP 是官方传输之一(JSON-RPC 2.0 POST)——「官方 HTTP 出站」
成立,照 ntfy.py one-shot 范式实装。MYIA 按本档语义重写,不整块复制:

- **one-shot JSON-RPC POST**:``POST {对端基址}``,头 ``Content-Type:
  application/json`` + ``A2A-Version: 1.0``(蓝本 ``_http_post_json`` 同款),
  body = ``{"jsonrpc": "2.0", "id": <task id>, "method": "SendMessage",
  "params": {"message": <A2A Message>}}``;Message 载荷 = 单 text Part
  (``{"text": …, "mediaType": "text/plain"}``,v1.0 成员判别式、无
  ``kind`` 字段——蓝本 ``text_part`` 同款)。
- **不发现 Agent Card**:蓝本抓 ``/.well-known/agent-card.json`` 学 RPC URL
  属 best-effort,失败即回落基址直发——MYIA one-shot 恒走该回落路径
  (推送场景多一次 GET 只加延迟;对端在 card 里另配 RPC URL 时,把该 URL
  本身登记为基址即可)。
- **contextId 会话**:``context.target.thread_id`` 在场即续该 A2A 会话
  (别名文件可登记);缺席每发新生成(摘要推送每次新会话,不与历史任务
  纠缠)。
- **错误码 → 死信映射(W2 模板探查,已对照 A2A v1.0 规范)**:HTTP 层错误
  文案保留 ``HTTP <status>`` 与响应体片段,经
  :func:`myia.push.delivery.classify_dead_error` 判定——HTTP 403 →
  ``forbidden``、HTTP 404 → ``not_found``、429/5xx/传输失败 → 瞬态不标。
  JSON-RPC 层 ``error`` 对象(如 ``code=-32600`` 请求非法/``-32000`` 服务端
  错误)如实入 ``a2a_api_error`` 文案;与 core 分类器 marker 表无码点交集 →
  瞬态(对端 agent 的语义错误重试无害)。
- **鉴权可选**:对端要求 Bearer 时配 :data:`DEFAULT_TOKEN_ENV_REF`
  (蓝本 ``_auth_header`` 同款 ``Authorization: Bearer``);缺省 env 缺席 =
  无鉴权(局域对端合法态,ntfy 可选 token 同款取舍)。

寻址(design D1):``supports_targeting=True``;``context.target.chat_id``
= 对端基址 URL(``https://agent.example.com``),``context.target.thread_id``
= A2A contextId(可选);退回 legacy ``target`` 引用(**须显式配置,无运行期
env 缺省回退**;推荐引用名
:data:`DEFAULT_TARGET_ENV_REF`),两路全缺 ``missing_target``;基址形态非法
→ ``invalid_credential_ref``。直达 = ``http(s)://`` 基址 URL;无目录发现
(:class:`DirectoryDiscoverUnsupported`)——Agent Card 描述对端能力而非
「可达会话列表」,不是目录来源,条目唯一来源 = 别名手工登记 + 直达 URL。

凭据安全基线同其余通道:对端 URL/令牌全部 ``env:``/``keychain:`` 引用,
发送期才解析,错误只带引用名。

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch a real A2A peer.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Mapping, Sequence

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
from myia.push.templates import TemplateRenderError, TemplateRenderer
from myia.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "PEER_URL_RE",
    "PROTOCOL_VERSION",
    "A2aChannel",
    "build_rpc_body",
    "text_part",
]

logger = logging.getLogger(__name__)

#: A2A 协议版本(蓝本 ``protocol.PROTOCOL_VERSION`` 同款;随头 ``A2A-Version`` 携带)。
PROTOCOL_VERSION = "1.0"
#: 对端令牌引用(缺省 env 缺席 = 无鉴权,局域对端合法态)。
DEFAULT_TOKEN_ENV_REF = "env:A2A_TOKEN"
#: legacy 对端基址引用(定向 ``targets`` 在场时可省,schema 层允许)。
DEFAULT_TARGET_ENV_REF = "env:A2A_PEER"
#: 直达对端形态:``http(s)://`` 基址 URL(A2A 对端地址即 HTTP 端点)。
PEER_URL_RE = re.compile(r"^https?://[^\s]+$")


def text_part(text: str) -> dict[str, Any]:
    """A2A v1.0 text Part(蓝本 ``protocol.text_part`` 同款:成员判别式,无 ``kind``)。"""
    return {"text": text, "mediaType": "text/plain"}


def build_rpc_body(text: str, *, context_id: str, task_id: str | None = None) -> dict[str, Any]:
    """一条 ``SendMessage`` JSON-RPC 2.0 body(蓝本 ``_send_task`` 装配同款)。

    v1.0 语义:``contextId`` 在 Message 内部(不在 params 顶层);role 取
    ``ROLE_USER``(推送方向上 MYIA 是发起方,蓝本同款)。
    """
    message: dict[str, Any] = {
        "role": "ROLE_USER",
        "parts": [text_part(text)],
        "messageId": uuid.uuid4().hex,
        "contextId": context_id,
    }
    return {
        "jsonrpc": "2.0",
        "id": task_id or uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {"message": message},
    }


class A2aChannel(TrendAwareChannel):
    """``a2a`` channel:每条消息一次 one-shot JSON-RPC ``SendMessage``。

    Args:
        target: 对端基址 URL 的凭据引用(``env:A2A_PEER`` style,发送期
            解析);定向(``targets``)在场时可省(schema 允许),对端由
            ``context.target`` 给出(chat_id = 基址,thread_id = contextId)。
        template: 可选用户模板(Jinja2);渲染输出为整条消息文本。省略 →
            内置纯文本版式(ntfy ``build_message`` 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        token_ref: 对端 Bearer 令牌引用;缺省 :data:`DEFAULT_TOKEN_ENV_REF`,
            env 缺席 = 无鉴权(局域对端合法态,静默省略)。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、两条寻址路径全缺(``missing_target``)、
            对端基址形态非法(``invalid_credential_ref``)、HTTP 传输失败
            (``http_error``)、非 JSON 响应(``invalid_response``)、非 2xx
            (``a2a_api_error``,文案保留 ``HTTP <status>`` 供死信分类)、
            JSON-RPC ``error`` 对象(``a2a_api_error``,带 code/message)
            或模板渲染失败。
    """

    name = "a2a"
    #: 目录寻址已开(context.target 优先,legacy target 兜底)。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        token_ref: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._token_ref = token_ref
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot ``SendMessage``;定向优先(``context.target``)。

        Raises:
            PushSendError: on any credential/transport/peer failure
                (callers isolate per channel; nothing is raised on success).
        """
        peer_url, context_id = self._resolve_peer(context)
        body = build_rpc_body(self._compose(items, context), context_id=context_id)
        headers = {"Content-Type": "application/json", "A2A-Version": PROTOCOL_VERSION}
        headers.update(self._auth_header())
        await self._post(peer_url, body, headers)
        logger.debug(
            "a2a 已发送: slot=%s kind=%s count=%d target_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        """消息文本:用户模板在场走渲染器,缺省内置纯文本版式。"""
        if self._template is not None:
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
        return build_message(items, context)

    def _auth_header(self) -> dict[str, str]:
        """可选 Bearer:显式引用失败即报错;缺省 env 缺席 = 无鉴权。"""
        reference = self._token_ref
        if reference is None:
            try:
                value = resolve_credential(DEFAULT_TOKEN_ENV_REF)
            except CredentialResolveError:
                return {}  # env 未设:局域对端无鉴权是合法态
        else:
            try:
                value = resolve_credential(reference)
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"a2a 对端令牌解析失败: {exc}") from exc
        value = value.strip()
        return {"Authorization": f"Bearer {value}"} if value else {}

    def _resolve_peer(self, context: SendContext) -> tuple[str, str]:
        """(对端基址, contextId):定向优先,退回 legacy target 引用。

        contextId = ``context.target.thread_id``(别名登记续会话)或缺省
        每发新生成;基址非 http(s) URL → ``invalid_credential_ref``。
        """
        thread_id: str | None = None
        if context.target is not None:
            value = context.target.chat_id.strip()
            thread_id = (context.target.thread_id or "").strip() or None
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"a2a target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "a2a 两条寻址路径均缺席:未配置 legacy target(对端基址引用),"
                "本次发送也未携带 context.target",
            )
        if not PEER_URL_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1;基址 query 可能带凭据)。
            raise PushSendError(
                "invalid_credential_ref",
                f"a2a 对端基址不是 http(s) URL:得到 {len(value)} 字符的值,"
                "不匹配该形态(解析值不回显)",
            )
        return value.rstrip("/"), thread_id or uuid.uuid4().hex

    # ---------------------------------------------------------------- post

    async def _post(self, url: str, body: Mapping[str, Any], headers: dict[str, str]) -> None:
        """One-shot JSON-RPC POST;2xx 且无 ``error`` 成员即成功。

        推送语义只认「对端已受理」:result 载荷(Task/Message)不解析——
        摘要推送不期待对端 agent 的回复文本。
        """
        try:
            if self._client is not None:
                response = await self._client.post(url, json=dict(body), headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=dict(body), headers=headers)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"a2a 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 300:
            # HTTP 状态与响应体片段进文案:死信分类按「HTTP 403/404」命中
            # (403 → forbidden、404 → not_found)。
            raise PushSendError(
                "a2a_api_error",
                f"a2a HTTP {response.status_code}: {response.text[:200]!r}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"a2a 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(payload, Mapping):
            raise PushSendError(
                "invalid_response",
                f"a2a 响应不是 JSON 对象: {str(payload)[:200]!r}",
            )
        error = payload.get("error")
        if error is not None:
            # JSON-RPC error 对象(code/message)如实入文案;与 core 分类器
            # marker 表无码点交集 → 瞬态(模块 docstring 注记)。
            code = error.get("code") if isinstance(error, Mapping) else None
            message = error.get("message") if isinstance(error, Mapping) else str(error)
            raise PushSendError(
                "a2a_api_error",
                f"a2a 对端返回 JSON-RPC 错误: code={code} message={message}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """a2a 无目录发现:Agent Card 描述对端能力,非「可达会话列表」。"""
        raise DirectoryDiscoverUnsupported(
            "a2a 无自动发现:Agent Card 描述对端能力而非会话列表;"
            "直达写 a2a:<http(s) 对端基址>,常用对端可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``a2a:<http(s) 基址 URL>`` 不经目录;其余回落目录四路径。"""
        value = ref.strip()
        if PEER_URL_RE.fullmatch(value):
            return ChannelTarget(
                platform="a2a", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
