"""ntfy 通道:one-shot ``POST {server}/{topic}``(纯文本通知)。

蓝本归属(10-03-messaging-w2-platforms):出站形态移植自 Hermes
``plugins/platforms/ntfy/adapter.py`` 的 ``send``/``_standalone_send``
(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/ntfy/adapter.py``,鉴权头见其
``_build_auth_header``,发布头见 ``_publish_headers``,4096 截断见
``_truncate_body``)。MYIA 按本档语义重写,不整块复制:

- **one-shot POST**:发布 = 向 ``{server}/{topic}`` POST 纯文本 body,
  ``2xx/3xx 即成功``(蓝本 ``resp.status_code < 300`` 同判据);无常驻
  连接、无订阅(入站流是蓝本的另一半,MYIA 零入站,见 prd 非目标)。
- **可选鉴权**:``Authorization`` 头二形(蓝本同款语义):解析值含
  ``:``(即 ``user:pass``)→ Basic(base64),否则 → Bearer。缺省不鉴权
  (公共 ntfy.sh topic);``env:NTFY_TOKEN`` 在场即自动启用,显式
  ``ntfy_token`` 引用解析失败则如实报错(不静默降级成无鉴权)。
- **4096 截断**:ntfy 服务端缺省 ``message-size-limit`` 4K;超长 body
  截到 :data:`MESSAGE_LIMIT` 并告警(蓝本同款),不拆多条——通知场景
  一条即达,尾巴丢弃是显式取舍。
- **X-Markdown**:仅在用户模板在场时携带(R1「随模板配置」):模板输出
  是用户自控的 markdown;内置版式是纯文本,不带该头。
  蓝本的 ``X-Tags`` 回显标记用于入站回环防护,MYIA 无入站,不携带。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(bare topic 或整条 URL 皆可),退回 legacy ``target`` 引用
(解析出 ``{server}/{topic}`` 整串,server 段兼作定向发送的缺省 server,
topic 段是缺省投递 topic)。直达解析 ``ntfy:<topic>``
(topic 名规则:字母/数字/``-``/``_``,ntfy 官方约束);目录无自动发现
(蓝本事实),别名手工登记(见 :class:`DirectoryDiscoverUnsupported`)。

凭据安全基线同其余通道:引用直到发送期才解析,错误只带引用名;
错误消息携带 HTTP 状态与响应体片段,供死信分类
(:func:`shishi.push.delivery.classify_dead_error`,403 → forbidden、
404 → not_found,429/5xx/超时 → 瞬态不标)。

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch a real ntfy server.
"""

from __future__ import annotations

import base64
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
    item_view,
)
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.feishu_card import card_title
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_SERVER",
    "DEFAULT_TOKEN_ENV_REF",
    "MESSAGE_LIMIT",
    "NtfyChannel",
    "TOPIC_RE",
    "build_auth_header",
    "build_message",
]

logger = logging.getLogger(__name__)

#: ntfy 公共服务(蓝本 ``DEFAULT_SERVER`` 同款);自建 server 写进 target 整串。
DEFAULT_SERVER = "https://ntfy.sh"
#: 可选鉴权 token 引用缺省:env 未设 = 无鉴权(公共 topic 合法态,静默省略)。
DEFAULT_TOKEN_ENV_REF = "env:NTFY_TOKEN"
#: ntfy 服务端缺省 message-size-limit(4K);body 按字符截断(蓝本同款)。
MESSAGE_LIMIT = 4096
#: topic 名规则(ntfy 官方:字母/数字/``-``/``_``):直达解析与 URL 拼装共用。
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_auth_header(token: str) -> dict[str, str]:
    """ntfy token 值 → ``Authorization`` 头(蓝本 ``_build_auth_header`` 同语义)。

    值含 ``:``(即 ``user:pass``)→ Basic(base64);否则 → Bearer。空值
    返回空 dict(无鉴权)。token 先去首尾空白(粘贴的 token 常带换行)。
    """
    value = (token or "").strip()
    if not value:
        return {}
    if ":" in value:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {"Authorization": f"Bearer {value}"}


def build_message(items: Sequence[Any], context: SendContext) -> str:
    """内置纯文本版式:标题行(card_title 跨通道一致)+ 每条目一行「标题 · URL」。

    ntfy 客户端对纯文本里的 URL 自动可点;不产出 markdown(那是用户模板
    的事,伴随 ``X-Markdown`` 头)。
    """
    lines = [card_title(context)]
    for item in items:
        view = item_view(item)
        title = str(view.get("title") or "(无标题)")
        url = view.get("url")
        line = f"▸ {title} · {url}" if url else f"▸ {title}"
        lines.append(line)
    return "\n".join(lines)


def _split_target_url(value: str) -> tuple[str, str]:
    """``{server}/{topic}`` 整串 → ``(server, topic)``;形态坏抛 ValueError。"""
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if not parts.scheme or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"不是 http(s)://<server>/<topic> 形态: {raw[:120]!r}")
    topic = parts.path.lstrip("/").split("/", 1)[0]
    if not TOPIC_RE.fullmatch(topic):
        raise ValueError(f"topic 段不是合法 topic 名(字母/数字/-/_): {topic[:64]!r}")
    server = f"{parts.scheme}://{parts.netloc}"
    return server, topic


class NtfyChannel(TrendAwareChannel):
    """``ntfy`` channel:每条消息一次 one-shot POST 到 ``{server}/{topic}``。

    Args:
        target: ``{server}/{topic}`` 整串的凭据引用(``env:NTFY_TARGET`` style,
            发送期解析),其 server 段兼作定向发送的缺省 server、topic 段是
            缺省投递 topic。定向(``targets``)在场时 target 可省(schema 允许)
            ——server 退回公共 :data:`DEFAULT_SERVER`,topic 必须由
            ``context.target`` 给出。
        token_ref: 可选鉴权 token 引用(值 = Bearer token 或 ``user:pass``);
           显式配置且解析失败 → 结构化报错。省略时尝试
            :data:`DEFAULT_TOKEN_ENV_REF`,env 未设 = 无鉴权(合法态)。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息并携带
            ``X-Markdown: true`` 头(R1);省略 → 内置纯文本版式。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、HTTP 传输失败、非 2xx/3xx 响应
            (消息含 ``HTTP <status>`` 供死信分类)、或模板渲染失败。
    """

    name = "ntfy"
    #: 目录寻址已开(context.target 优先,legacy target 兜底);协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        token_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._token_ref = token_ref
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        server, default_topic = self._resolve_server_and_topic()
        topic_value = (
            context.target.chat_id if context.target is not None else default_topic
        )
        url = self._publish_url(server, topic_value)
        body, headers = self._compose(items, context)
        await self._post(url, body, headers)
        logger.debug(
            "ntfy 已发布: slot=%s kind=%s count=%d topic_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(
        self, items: Sequence[Any], context: SendContext
    ) -> tuple[str, dict[str, str]]:
        """消息文本(headers 携带纯文本类型;模板在场附 X-Markdown)。"""
        headers: dict[str, str] = {"Content-Type": "text/plain; charset=utf-8"}
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
            headers["X-Markdown"] = "true"
        else:
            text = build_message(items, context)
        if len(text) > MESSAGE_LIMIT:
            logger.warning(
                "ntfy 消息超限,截断 %d → %d 字符",
                len(text),
                MESSAGE_LIMIT,
            )
            text = text[:MESSAGE_LIMIT]
        headers.update(self._auth_header())
        return text, headers

    def _auth_header(self) -> dict[str, str]:
        """可选鉴权:显式引用失败即报错;缺省 env 缺席 = 无鉴权。"""
        reference = self._token_ref
        if reference is None:
            try:
                value = resolve_credential(DEFAULT_TOKEN_ENV_REF)
            except CredentialResolveError:
                return {}  # env 未设:公共 topic 无鉴权是合法态
            return build_auth_header(value)
        try:
            return build_auth_header(resolve_credential(reference))
        except CredentialResolveError as exc:
            raise PushSendError(
                exc.code, f"ntfy 鉴权凭据解析失败: {exc}"
            ) from exc

    def _resolve_server_and_topic(self) -> tuple[str, str]:
        """legacy target 引用 → ``(server, default_topic)``。

        target 未配置(定向 targets 在场的最小配置,schema 层允许)→ 公共
        :data:`DEFAULT_SERVER` + 空 topic(发送期必须由 ``context.target``
        给 topic,两条寻址路径至少一条在场,否则 ``missing_target``)。
        """
        if self._target is None:
            return DEFAULT_SERVER, ""
        try:
            resolved = resolve_credential(self._target)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"ntfy target 解析失败: {exc}") from exc
        try:
            return _split_target_url(resolved)
        except ValueError as exc:
            raise PushSendError(
                "invalid_credential_ref",
                f"ntfy target 引用 {self._target!r} 解析结果不是 server/topic 整串: {exc}",
            ) from exc

    @staticmethod
    def _publish_url(server: str, topic_value: str) -> str:
        """定向/缺省 topic 值 → 发布 URL(bare topic 拼 server;整条 URL 原样)。"""
        value = (topic_value or "").strip()
        if not value:
            raise PushSendError(
                "missing_target",
                "ntfy 两条寻址路径均缺席:未配置 legacy target(server/topic 整串引用),"
                "本次发送也未携带 context.target",
            )
        if value.startswith(("http://", "https://")):
            return value.rstrip("/")
        if not TOPIC_RE.fullmatch(value):
            raise PushSendError(
                "invalid_credential_ref",
                f"ntfy topic 形态非法(须为字母/数字/-/_ 或整条 URL): {value[:64]!r}",
            )
        return f"{server.rstrip('/')}/{value}"

    async def _post(self, url: str, body: str, headers: dict[str, str]) -> None:
        """One-shot POST;``< 300`` 即成功(蓝本同判据),否则结构化报错。"""
        try:
            if self._client is not None:
                response = await self._client.post(url, content=body.encode("utf-8"), headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url, content=body.encode("utf-8"), headers=headers
                    )
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"ntfy 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 300:
            # 消息体片段进错误文案:死信分类按「HTTP 403/404」与原厂
            # error 文本(forbidden/topic 不存在)命中。
            raise PushSendError(
                "ntfy_api_error",
                f"ntfy HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """ntfy 无目录概念(蓝本事实):结构化说明,而非假装刷新。"""
        raise DirectoryDiscoverUnsupported(
            "ntfy 无自动发现(蓝本事实):topic 即地址,无目录概念;"
            "直达写 ntfy:<topic>,常用地名可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``ntfy:<topic>``(字母/数字/``-``/``_``)不经目录。

        非法的 topic 形态返回 None(调用方回落目录四路径——别名命名不收
        topic 字符集限制,如中文别名仍可寻址)。
        """
        value = ref.strip()
        if TOPIC_RE.fullmatch(value):
            return ChannelTarget(
                platform="ntfy", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
