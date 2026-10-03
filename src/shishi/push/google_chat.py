"""Google Chat 通道:incoming webhook 直发(静态 URL,零 extras)。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态对照 Hermes
``plugins/platforms/google_chat/adapter.py`` 的 ``_standalone_send``(
NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/google_chat/adapter.py``)。
蓝本走 service account(google-auth 刷 Bearer →
``POST https://chat.googleapis.com/v1/{space}/messages``),依赖 google-auth
包;MYIA 取同一官方端点的 **incoming webhook 形态**(chat.googleapis.com/
v1/spaces/<id>/messages?key=…&token=…,凭据在 URL query 内)——纯 httpx
零 extras、零 SA 密钥文件,是「无常驻出站」场景的最小官方路径【偏离注记:
蓝本 SA 形态需 google-auth,属 extras 结构化报错路径(vision/ocr.py 的
dependency_missing 先例);本通道选 webhook 形态后该路径不需要】:

- **one-shot POST**:``POST <webhook URL>`` body ``{"text": <文本>}``;
  2xx 即成功(应答是消息资源 JSON,含 ``name``/``sender``/``createTime``)。
  webhook URL 内嵌 key/token 凭据,无需 Authorization 头;URL 属凭据
  (dingtalk 同款判定),只经 ``env:``/``keychain:`` 引用进配置。
- **4000 截断**:Chat text 消息上限官方 4096,蓝本
  ``max_message_length = 4000`` 余量同值(:data:`MESSAGE_LIMIT`);超长按行
  边界拆多条(:func:`telegram.split_message` 复用)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(**完整 webhook URL**,与 dingtalk 同款语义:一个 webhook = 一个空间),
退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 ``env:GOOGLE_CHAT_WEBHOOK_URL``)。直达
形态:完整官方 webhook URL(host 锚定,防与人类别名撞车——dingtalk 先例)。
目录无自动发现(蓝本事实:出站无列表路径),别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段供死信分类:403 → forbidden、
404 → not_found、429(限频)/5xx → 瞬态。

凭据安全基线同其余通道:webhook URL 直到发送期才解析,错误只带引用名;全部
HTTP 经注入的 ``httpx.AsyncClient``(测试 ``httpx.MockTransport``,零真发)。
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
    "DEFAULT_TARGET_ENV_REF",
    "GoogleChatChannel",
    "MESSAGE_LIMIT",
    "WEBHOOK_URL_RE",
]

logger = logging.getLogger(__name__)

#: 接收 webhook URL 的推荐引用名(显式配置 ``target`` 用;运行期不自动
#: 回退。URL 内嵌 key/token,属凭据,零明文)。
DEFAULT_TARGET_ENV_REF = "env:GOOGLE_CHAT_WEBHOOK_URL"
#: Chat text 消息上限余量(官方 4096;蓝本 max_message_length=4000 同值)。
MESSAGE_LIMIT = 4000
#: 直达/别名形态:完整官方 incoming webhook URL(host 锚定 + key/token query,
#: dingtalk ``WEBHOOK_HOST_RE`` 同款防撞车设计)。
WEBHOOK_URL_RE = re.compile(
    r"^https://chat\.googleapis\.com/v1/spaces/[A-Za-z0-9_-]+/messages\?\S+$"
)


class GoogleChatChannel(TrendAwareChannel):
    """``google_chat`` channel:每 ≤4000 字符块一次 webhook POST。

    Args:
        target: webhook URL 的凭据引用(``env:GOOGLE_CHAT_WEBHOOK_URL`` style,
            发送期解析);与 ``targets`` 定向配置互斥可省,此时 URL 由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、URL 形态非法、HTTP 传输失败、非 2xx
            (原厂响应片段进文案),或模板渲染失败。
    """

    name = "google_chat"
    #: 目录寻址已开(context.target = 完整 webhook URL 优先,legacy target
    #: 兜底);协议判定见 base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 逐块 POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        url = self._resolve_webhook(context)
        for text in split_message(self._compose(items, context), limit=MESSAGE_LIMIT):
            await self._post(url, {"text": text})
        logger.debug(
            "google_chat 已提交: slot=%s kind=%s count=%d target_ref=%s",
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

    # ------------------------------------------------------------ targeting

    def _resolve_webhook(self, context: SendContext) -> str:
        """定向优先;URL 形态非法/两路全缺如实报错(绝不猜端点)。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"google_chat target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "google_chat 两条寻址路径均缺席:未配置 legacy target(webhook URL 引用),"
                "本次发送也未携带 context.target",
            )
        if not WEBHOOK_URL_RE.fullmatch(value):
            # 解析值不回显:URL 的 ?key=…&token=… query 本身是凭据,任何前缀
            # 片段都可能探进 key 值(自宣基线「错误只带引用名」)。
            raise PushSendError(
                "invalid_credential_ref",
                "google_chat webhook 形态非法(须为"
                " https://chat.googleapis.com/v1/spaces/<id>/messages?key=…&token=…):"
                f"得到 {len(value)} 字符的值,不匹配该形态(URL 含 key/token"
                " 凭据,内容不回显)",
            )
        return value

    # ------------------------------------------------------------- send

    async def _post(self, url: str, body: dict[str, Any]) -> None:
        """One-shot POST;2xx 即成功(应答是消息资源 JSON)。"""
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"google_chat 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            # HTTP <status> + 原厂 body 片段进文案:403(webhook 被删/无权限)
            # → forbidden、404(空间不存在)→ not_found、429/5xx → 瞬态。
            raise PushSendError(
                "google_chat_api_error",
                f"google_chat HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """google_chat 无自动发现(蓝本事实:webhook 是静态端点,无列表路径)。"""
        raise DirectoryDiscoverUnsupported(
            "google_chat 无自动发现(蓝本事实):一个 incoming webhook = 一个空间,"
            "无「列出空间」路径;直达写 google_chat:<完整 webhook URL>,"
            "常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:完整官方 webhook URL(host 锚定)不经目录。

        非完整 URL 形态返回 None(调用方回落目录四路径——别名用人类名,
        chat_id 登记完整 webhook URL)。
        """
        value = ref.strip()
        if WEBHOOK_URL_RE.fullmatch(value):
            return ChannelTarget(
                platform="google_chat", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
