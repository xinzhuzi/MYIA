"""LINE Messaging API 通道:``POST /v2/bot/message/push``(Bearer + to)。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态移植自 Hermes
``plugins/platforms/line/adapter.py`` 的 ``_LineClient.push`` /
``_text_message`` / ``split_for_line``(NousResearch/Hermes-Agent,MIT;上游
路径 ``~/.hermes/hermes-agent/plugins/platforms/line/adapter.py``)。MYIA 按
本档语义重写,不整块复制:

- **push 直发**(蓝本 ``LINE_PUSH_URL`` 同端点;reply 需要 webhook 回调入站
  的 reply token,MYIA 零入站故走 push):body
  ``{"to": <id>, "messages": [{"type": "text", "text": <块>}, …]}``;
  **2xx 即成功**——push 的成功应答是 **200 + 空 body**(蓝本
  ``status >= 400`` 判错同款,JSON 解析不做强制)。
- **分泡**:每泡 text 硬上限 5000(蓝本 ``LINE_PER_BUBBLE_CHARS``),保守
  切 4500(:data:`MESSAGE_LIMIT`,蓝本 ``LINE_SAFE_BUBBLE_CHARS`` 同值);
  一次 push 最多 5 泡(蓝本 ``LINE_MAX_MESSAGES_PER_CALL``,官方上限,
  第 6 泡起丢弃并告警——通知场景一卡即达,尾巴丢弃是显式取舍)。
- **纯文本**:LINE text 泡不渲染 markdown(蓝本 platform_hint 事实),内置
  版式即纯文本(ntfy ``build_message`` 复用);URL 自动可链。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 ``env:LINE_TO``;token 引用
``env:LINE_CHANNEL_ACCESS_TOKEN``,蓝本同名 env)。直达形态:``U`` 用户 /
``C`` 群组 / ``R`` 聊天室 id(33 位十六进制,蓝本 ``get_chat_info`` 的
U/C/R 前缀判据)。目录无自动发现(蓝本事实:出站无列表路径),别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段(LINE 错误体
``{"message": "Invalid reply token"}`` 形态)供死信分类:403 → forbidden、
404 → not_found、429(限频)/5xx → 瞬态。

凭据安全基线同其余通道;全部 HTTP 经注入的 ``httpx.AsyncClient``。
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
    "API_URL",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "LINE_MAX_BUBBLES",
    "LINE_PER_BUBBLE_LIMIT",
    "LineChannel",
    "MESSAGE_LIMIT",
    "USER_ID_RE",
    "split_bubbles",
]

logger = logging.getLogger(__name__)

#: LINE Messaging API push 端点(蓝本 ``LINE_PUSH_URL`` 同款)。
API_URL = "https://api.line.me/v2/bot/message/push"
#: Channel access token(长-life)凭据引用缺省(蓝本同名 env)。
DEFAULT_TOKEN_ENV_REF = "env:LINE_CHANNEL_ACCESS_TOKEN"
#: 接收方 id 的推荐引用名(显式配置 ``target`` 用;运行期不自动回退)。
DEFAULT_TARGET_ENV_REF = "env:LINE_TO"
#: 单泡 text 硬上限(官方;蓝本 ``LINE_PER_BUBBLE_CHARS``)。
LINE_PER_BUBBLE_LIMIT = 5000
#: 保守切泡宽度(蓝本 ``LINE_SAFE_BUBBLE_CHARS``;预留平台侧包装开销)。
MESSAGE_LIMIT = 4500
#: 一次 push 最多气泡数(官方;蓝本 ``LINE_MAX_MESSAGES_PER_CALL``)。
LINE_MAX_BUBBLES = 5
#: 直达 id 形态:``U`` 用户 / ``C`` 群组 / ``R`` 聊天室(33 位十六进制,
#: 蓝本 get_chat_info 的前缀判据同源)。
USER_ID_RE = re.compile(r"^[UCR][0-9a-f]{32}$")


def split_bubbles(text: str, *, limit: int = MESSAGE_LIMIT) -> list[str]:
    """长文切 ≤limit 泡(行边界优先);返回 ≤ :data:`LINE_MAX_BUBBLES` 泡。

    蓝本 ``split_for_line`` 同语义;切完仍超 5 泡时**截断到 5 泡并告警**
    (push 单次上限硬约束,尾巴丢弃是显式取舍,不静默)。
    """
    parts = split_message(text, limit=limit)
    if len(parts) > LINE_MAX_BUBBLES:
        logger.warning(
            "line 消息超 %d 泡上限,第 %d 泡起丢弃(不拆多次 push)",
            LINE_MAX_BUBBLES,
            LINE_MAX_BUBBLES + 1,
        )
        parts = parts[:LINE_MAX_BUBBLES]
    return parts


class LineChannel(TrendAwareChannel):
    """``line`` channel:一次 ``message/push`` 携带 ≤5 个 text 泡。

    Args:
        target: 接收方 id 的凭据引用(``env:LINE_TO`` style,发送期解析);
            与 ``targets`` 定向配置互斥可省,此时接收方由 ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本
            (仍切泡);省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        token: 预解析 channel access token(构造注入,测试用);省略 →
            发送期从 :data:`DEFAULT_TOKEN_ENV_REF` 解析。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、HTTP 传输失败、非 2xx(原厂 ``message``
            进文案),或模板渲染失败。
    """

    name = "line"
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
        """Compose + one-shot push;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        to = self._resolve_recipient(context)
        body = {
            "to": to,
            "messages": [
                {"type": "text", "text": bubble} for bubble in split_bubbles(self._compose(items, context))
            ],
        }
        await self._post(token, body)
        logger.debug(
            "line 已推送: slot=%s kind=%s count=%d bubbles=%d target_ref=%s",
            context.slot,
            context.kind,
            len(items),
            len(body["messages"]),
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
            raise PushSendError(exc.code, f"line channel token 解析失败: {exc}") from exc

    def _resolve_recipient(self, context: SendContext) -> str:
        """定向优先;形态非法/两路全缺如实报错(绝不猜收件人)。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"line target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "line 两条寻址路径均缺席:未配置 legacy target(接收方 id 引用),"
                "本次发送也未携带 context.target",
            )
        if not USER_ID_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1)。
            raise PushSendError(
                "invalid_credential_ref",
                f"line id 形态非法(须为 U/C/R 前缀 33 位十六进制):"
                f"得到 {len(value)} 字符的值,不匹配该形态(解析值不回显)",
            )
        return value

    # ------------------------------------------------------------- send

    async def _post(self, token: str, body: dict[str, Any]) -> None:
        """One-shot push;**2xx 即成功**(push 成功应答是 200 + 空 body)。"""
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(API_URL, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"line 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if not response.is_success:
            # HTTP <status> + 原厂 body 片段进文案(蓝本 ``status >= 400``
            # 同判据;死信分类按 403/404 → 硬失败、429/5xx → 瞬态命中)。
            raise PushSendError(
                "line_api_error",
                f"line HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """line 无自动发现(蓝本事实:出站无「列出会话」路径)。"""
        raise DirectoryDiscoverUnsupported(
            "line 无自动发现(蓝本事实):Messaging API 出站无会话列表路径;"
            "直达写 line:U…/C…/R…,常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``U/C/R`` 前缀 id 不经目录。

        非命中形态(备注名等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if USER_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="line", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
