"""钉钉通道:静态自定义机器人 webhook(``{"msgtype": "text"}``)。

蓝本归属(10-03-messaging-w2-platforms):出站形态移植自 Hermes
``plugins/platforms/dingtalk/adapter.py`` 的 ``_standalone_send``
(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/dingtalk/adapter.py``——cron/
无 gateway 场景走的静态机器人 webhook,与本档「无常驻出站」同一形态)。
MYIA 按本档语义重写,不整块复制:

- **静态 webhook**:``POST <webhook_url>`` body ``{"msgtype": "text",
  "text": {"content": <渲染文本>}}``;HTTP 非 2xx / 非 JSON / ``errcode != 0``
  结构化报错(蓝本 ``raise_for_status`` + ``errcode`` 判据同款)。
  text-only 是蓝本边界(markdown/卡片不做,prd 非目标)。
- **加签(HMAC-SHA256)为 MYIA 增量【偏离注记】**:蓝本未实现加签,但不
  加签则「安全设置=加签」的机器人全不可用(errcode 310000 sign not
  match)。配 ``dingtalk_secret`` 引用即启用:标准算法
  ``sign = urlencode(base64(HMAC-SHA256(key=secret,
  msg=f"{timestamp_ms}\\n{secret}")))``,以 query 参数 ``timestamp``/``sign``
  追加到 webhook URL;不配 = 蓝本裸 webhook 行为逐字节一致。

寻址(design D1/D4):``supports_targeting=True``;一个 webhook = 一个
投递端点,``context.target.chat_id``(定向/别名的 chat_id = **完整 webhook
URL**;定向 spec 写 ``dingtalk:https://oapi.dingtalk.com/robot/send?...``)
优先,退回 legacy ``target`` 引用(缺省 ``env:DINGTALK_WEBHOOK_URL``,蓝本
同名 env)。目录无自动发现(蓝本事实,钉钉 Stream Mode 为入站形态不在
本档),别名手工登记;webhook URL 内嵌 access_token,属凭据——别名文件
``channel_aliases.json`` 是本机私有数据,登记即落本机盘,指南文案已提示。

错误消息携带 ``errcode=<n>`` 与 ``HTTP <status>`` 供死信分类
(:func:`shishi.push.delivery.classify_dead_error`):310000 族(关键词/加签/
IP/时间戳校验未通过)→ forbidden 硬失败;``-1`` 系统繁忙/超时 → 瞬态。

凭据安全基线同其余通道:引用直到发送期才解析,错误只带引用名;webhook
URL 永不进日志。All HTTP I/O goes through an injectable
``httpx.AsyncClient`` — tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
import base64
from typing import Any, Callable, Sequence
from urllib.parse import quote_plus

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
    "DEFAULT_TARGET_ENV_REF",
    "DingTalkChannel",
    "WEBHOOK_HOST_RE",
    "build_signed_url",
]

logger = logging.getLogger(__name__)

#: 静态机器人 webhook 引用缺省(蓝本同名 env ``DINGTALK_WEBHOOK_URL``)。
DEFAULT_TARGET_ENV_REF = "env:DINGTALK_WEBHOOK_URL"
#: 直达/别名形态:完整 webhook URL,host 锚定官方 robot/send 端点(防与
#: 人类别名名撞车——直达钩子先于目录,宽松匹配会吃掉别名寻址)。
WEBHOOK_HOST_RE = re.compile(r"^https://oapi\.dingtalk\.com/robot/send\?\S+$")


def build_signed_url(webhook_url: str, secret: str, *, timestamp_ms: int) -> str:
    """加签增量:标准钉钉算法(官方「自定义机器人安全设置」文档)。

    ``sign = quote_plus(base64(HMAC-SHA256(key=secret, msg=f"{ts}\\n{secret}")))``,
    以 ``timestamp``/``sign`` query 追加(URL 已带 ``?`` 故用 ``&`` 拼接)。
    """
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = quote_plus(base64.b64encode(digest).decode("ascii"))
    return f"{webhook_url}&timestamp={timestamp_ms}&sign={sign}"


class DingTalkChannel(TrendAwareChannel):
    """``dingtalk`` channel:每条消息一次 POST 到自定义机器人 webhook。

    Args:
        target: webhook URL 的凭据引用(``env:DINGTALK_WEBHOOK_URL`` style,
            发送期解析);省略 → :data:`DEFAULT_TARGET_ENV_REF`。
        secret_ref: 可选加签密钥引用;配置即启用加签(MYIA 增量),解析失败
            结构化报错;省略 = 蓝本裸 webhook 行为(不加签)。
        template: 可选用户模板(Jinja2);在场时渲染输出为消息文本;
            省略 → 内置纯文本版式(ntfy 同款:标题行 + 每条目一行)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数(蓝本 30s;MYIA 缺省与全通道
            一致的 10s,可注入调大)。
        clock: 时间源注入(秒;加签 timestamp 用,测试可钉死)。

    Raises:
        PushSendError: 凭据解析失败、webhook 形态非法、HTTP 传输失败、
            非 2xx/非 JSON 响应、``errcode != 0``,或模板渲染失败。
    """

    name = "dingtalk"
    #: 目录寻址已开(context.target = 完整 webhook URL 优先,legacy target
    #: 兜底);协议判定见 base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        secret_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._target = target
        self._secret_ref = secret_ref
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout
        self._clock = clock

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        webhook = (
            context.target.chat_id if context.target is not None else self._resolve_webhook()
        )
        if not WEBHOOK_HOST_RE.fullmatch(webhook):
            # 解析值不回显:webhook 的 access_token query 本身是凭据
            # (10-03-messaging-w3-longtail 复核 C1 同病收敛)。
            raise PushSendError(
                "invalid_credential_ref",
                f"钉钉 webhook 形态非法(须为 https://oapi.dingtalk.com/robot/send?…):"
                f"得到 {len(webhook)} 字符的值,不匹配该形态(URL 含 access_token"
                " 凭据,内容不回显)",
            )
        url = self._maybe_sign(webhook)
        body = {"msgtype": "text", "text": {"content": self._compose(items, context)}}
        await self._post(url, body)
        logger.debug(
            "钉钉已提交: slot=%s kind=%s count=%d signed=%s",
            context.slot,
            context.kind,
            len(items),
            self._secret_ref is not None,
        )

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

    def _resolve_webhook(self) -> str:
        reference = self._target or DEFAULT_TARGET_ENV_REF
        try:
            resolved = resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"钉钉 webhook 解析失败: {exc}") from exc
        if not resolved:
            raise PushSendError(
                "invalid_credential_ref",
                f"钉钉 webhook 引用 {reference!r} 解析结果为空",
            )
        return resolved

    def _maybe_sign(self, webhook_url: str) -> str:
        """配了 secret 引用即加签;没配 = 蓝本裸 webhook(原样返回)。"""
        if self._secret_ref is None:
            return webhook_url
        try:
            secret = resolve_credential(self._secret_ref)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"钉钉加签密钥解析失败: {exc}") from exc
        timestamp_ms = int(self._clock() * 1000)
        return build_signed_url(webhook_url, secret, timestamp_ms=timestamp_ms)

    async def _post(self, url: str, body: dict[str, Any]) -> None:
        """POST;HTTP 非 2xx、非 JSON、``errcode != 0`` 均结构化报错。"""
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"钉钉请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            raise PushSendError(
                "dingtalk_api_error",
                f"钉钉 HTTP {response.status_code}: {response.text[:200]!r}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"钉钉响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        errcode = data.get("errcode") if isinstance(data, dict) else None
        if errcode:
            # errcode=N 进文案 → 死信分类按 310000 族(forbidden)命中。
            raise PushSendError(
                "dingtalk_api_error",
                f"钉钉 API 返回错误: errcode={errcode} errmsg={data.get('errmsg')}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """钉钉无目录发现(蓝本事实;Stream Mode 是入站形态,本档非目标)。"""
        raise DirectoryDiscoverUnsupported(
            "钉钉无自动发现(蓝本事实):静态 webhook 即端点,无「列出机器人」API;"
            "多机器人 = 多条 push 条目或别名登记完整 webhook URL"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:完整官方 webhook URL(host 锚定)不经目录。

        非完整 URL 形态返回 None(调用方回落目录四路径——别名用人类名,
        chat_id 登记完整 webhook URL)。
        """
        value = ref.strip()
        if WEBHOOK_HOST_RE.fullmatch(value):
            return ChannelTarget(
                platform="dingtalk", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
