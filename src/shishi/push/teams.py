"""Microsoft Teams 通道:Workflows incoming webhook 直发(Adaptive Card)。

蓝本归属(10-03-messaging-w3-longtail 组一):Hermes ``plugins/platforms/
teams/adapter.py`` 的 Teams 出站走 Bot Framework(SDK + app 注册,蓝本路径
``_standalone_send``:client credentials 换 token →
``{service_url}v3/conversations/{id}/activities``),需 Bot Framework 应用
注册面;MYIA 按本档「静态 webhook 优先」约定(ntfy/webhook 蓝本)取官方
**Workflows incoming webhook** 形态【偏离注记:旧 O365 connector(
outlook.office.com/webhook)已被微软退役(2024-2025),Workflows webhook
(``<tenant>.webhook.office.com/webhookb2/…``)是现行官方 webhook 路径;
蓝本的 Bot Framework 形态与 Graph 直发形态归 ``msgraph_webhook`` 通道的
簇内探查(组内说明:微软系同簇,形态归一)】:

- **one-shot POST**:``POST <webhook URL>`` body
  ``{"type": "message", "attachments": [{"contentType":
  "application/vnd.microsoft.card.adaptive", "content": <Adaptive Card>}]}``
  (官方 Workflows「When a Teams webhook request is received」触发器载荷)。
  卡体 = 单 ``TextBlock``(:func:`build_card`,wrap 开、无长度声明——
  Workflows 无已核实的文本硬上限,超长内容按 :data:`MESSAGE_LIMIT` 拆卡
  顺序投递,保守取 4000)。
- **成功判据**:HTTP 2xx 即成功——**应答 body 是字面 ``1``**(Power
  Automate 触发器接受的回执,官方形态),不是 JSON,故不做 JSON 解析
  (webhook.py 的 JSON 判据在此不适用,这是 teams 特有事实)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(**完整 webhook URL**,一个 webhook = 一个频道,dingtalk 同款语义),
退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 ``env:TEAMS_WEBHOOK_URL``)。直达形态:
完整官方 webhook URL(host 锚定 ``*.webhook.office.com/webhookb2/``)。
目录无自动发现(蓝本事实:webhook 是静态端点),别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段供死信分类:403 → forbidden、
404(webhook 被删/流程不存在)→ not_found、429(限频)/5xx → 瞬态。

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
    "MESSAGE_LIMIT",
    "TeamsChannel",
    "WEBHOOK_URL_RE",
    "build_card",
]

logger = logging.getLogger(__name__)

#: 接收 webhook URL 的推荐引用名(显式配置 ``target`` 用;运行期不自动
#: 回退。Workflows「入站 webhook」复制件,属凭据)。
DEFAULT_TARGET_ENV_REF = "env:TEAMS_WEBHOOK_URL"
#: 单卡 TextBlock 文本上限(保守取 4000;Workflows 无已核实文本硬上限,
#: 超长拆卡顺序投递)。
MESSAGE_LIMIT = 4000
#: 直达/别名形态:完整 Workflows webhook URL(host 锚定官方
#: ``<tenant>.webhook.office.com/webhookb2/`` 端点,防与人类别名撞车)。
WEBHOOK_URL_RE = re.compile(r"^https://[A-Za-z0-9-]+\.webhook\.office\.com/webhookb2/\S+$")


def build_card(text: str) -> dict[str, Any]:
    """一段文本 → Workflows webhook 载荷(Adaptive Card 单 TextBlock)。

    官方触发器契约:顶层 ``{"type": "message", "attachments": [...]}``,
    attachment 的 ``content`` 即 Adaptive Card;``wrap: true`` 保中文长文
    折行。纯文本直入 TextBlock(不产 markdown,版式与 ntfy 内置一致)。
    """
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                },
            }
        ],
    }


class TeamsChannel(TrendAwareChannel):
    """``teams`` channel:每 ≤4000 字符块一次 Workflows webhook POST。

    Args:
        target: webhook URL 的凭据引用(``env:TEAMS_WEBHOOK_URL`` style,
            发送期解析);与 ``targets`` 定向配置互斥可省,此时 URL 由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本
            (仍按 :data:`MESSAGE_LIMIT` 拆卡);省略 → 内置纯文本版式
            (ntfy ``build_message`` 同款复用)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、URL 形态非法、HTTP 传输失败、非 2xx
            (原厂响应片段进文案),或模板渲染失败。
    """

    name = "teams"
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
            await self._post(url, build_card(text))
        logger.debug(
            "teams 已提交: slot=%s kind=%s count=%d target_ref=%s",
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
                raise PushSendError(exc.code, f"teams target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "teams 两条寻址路径均缺席:未配置 legacy target(webhook URL 引用),"
                "本次发送也未携带 context.target",
            )
        if not WEBHOOK_URL_RE.fullmatch(value):
            # 解析值不回显:webhookb2/<guid> 路径段本身是凭据(自宣基线
            # 「错误只带引用名」)。
            raise PushSendError(
                "invalid_credential_ref",
                "teams webhook 形态非法(须为 https://<tenant>.webhook.office.com/"
                f"webhookb2/… Workflows 端点):得到 {len(value)} 字符的值,"
                "不匹配该形态(URL 属凭据,内容不回显)",
            )
        return value

    # ------------------------------------------------------------- send

    async def _post(self, url: str, body: dict[str, Any]) -> None:
        """One-shot POST;**2xx 即成功**(应答 body 是字面 ``1``,非 JSON)。"""
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"teams 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            # HTTP <status> + 原厂 body 片段进文案:402/403(流程停用/未授权)
            # → forbidden、404(webhook 失效)→ not_found、429/5xx → 瞬态。
            raise PushSendError(
                "teams_api_error",
                f"teams HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """teams 无自动发现(蓝本事实:webhook 是静态端点,无列表路径)。"""
        raise DirectoryDiscoverUnsupported(
            "teams 无自动发现(蓝本事实):一个 Workflows webhook = 一个频道,"
            "无「列出频道」路径;直达写 teams:<完整 webhook URL>,"
            "常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:完整 Workflows webhook URL(host 锚定)不经目录。

        非完整 URL 形态返回 None(调用方回落目录四路径——别名用人类名,
        chat_id 登记完整 webhook URL)。
        """
        value = ref.strip()
        if WEBHOOK_URL_RE.fullmatch(value):
            return ChannelTarget(
                platform="teams", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
