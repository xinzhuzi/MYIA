"""Slack 通道:Bot Token ``chat.postMessage``(Bearer + channel id + JSON POST)。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态移植自 Hermes
``plugins/platforms/slack/adapter.py`` 的 ``_standalone_send`` /
``_slack_json_post`` / ``_resolve_slack_user_dm``(NousResearch/Hermes-Agent,
MIT;上游路径 ``~/.hermes/hermes-agent/plugins/platforms/slack/adapter.py``)。
MYIA 按本档语义重写,不整块复制:

- **chat.postMessage**:``POST https://slack.com/api/chat.postMessage`` +
  ``Authorization: Bearer <bot token>`` + JSON body;HTTP 200 且 ``ok=true``
  即成功——Slack 的 API 错误几乎恒为 **HTTP 200 + ``ok=false`` + ``error``**
  (blueprint ``data.get("ok")`` 同判据),错误文案保留原厂 ``error`` 值
  (``channel_not_found`` / ``invalid_auth`` / ``not_in_channel`` / …)。
- **分段**:text 上限官方 40000,蓝本 ``MAX_MESSAGE_LENGTH = 39000`` 留余量
  (:data:`MESSAGE_LIMIT`);长文按行边界自动拆多条(:func:`telegram.split_message`
  复用,逐块顺序发送,中途失败整通道报错——telegram 同款取舍)。
- **U/W 用户直达**::func:`parse_direct_ref` 收 ``U…``/``W…``(bot 不能直发
  裸用户 id,蓝本 ``channel_not_found`` 事实注释),发送期先
  ``conversations.open``(需 ``im:write`` scope)换 DM 会话 id ``D…`` 再投递;
  解析失败如实报 ``slack_api_error``(原厂 error 进文案)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 ``env:SLACK_CHANNEL``——蓝本 cron delivery 同名先例
``SLACK_HOME_CHANNEL``,MYIA 按通道名取 ``SLACK_CHANNEL``,schema 层
target/targets 二选一强制)。直达形态 ``C/G/D/U/W`` 系 id(Hermes setup 指南:「频道
ID 以 C 开头」)。目录无自动发现(蓝本事实:slack 适配器无列表 API 路径),
别名手工登记。

凭据安全基线同其余通道:token 引用直到发送期才解析,错误只带引用名;全部
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
    "API_BASE",
    "CHANNEL_ID_RE",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MESSAGE_LIMIT",
    "SlackChannel",
]

logger = logging.getLogger(__name__)

#: Slack Web API 基址(方法拼在 ``/api/<method>`` 后)。
API_BASE = "https://slack.com/api"
#: Bot token 凭据引用缺省(``xoxb-`` 系;发送期解析,值永不入日志)。
DEFAULT_TOKEN_ENV_REF = "env:SLACK_BOT_TOKEN"
#: 接收频道 id 的推荐引用名(显式配置 ``target`` 用;运行期不自动回退,
#: 两路全缺报 missing_target)。
DEFAULT_TARGET_ENV_REF = "env:SLACK_CHANNEL"
#: chat.postMessage text 上限(官方 40000;蓝本 39000 余量同款)。
MESSAGE_LIMIT = 39000
#: 直达 id 形态:``C`` 频道 / ``G`` 私有群 / ``D`` DM 会话 / ``U``/``W`` 用户
#: (U/W 发送期先 conversations.open 换 D…,蓝本 #17444 同款);9-12 位大写
#: 字母数字(Slack id 官方字符集)。
CHANNEL_ID_RE = re.compile(r"^[CGDUW][A-Z0-9]{8,20}$")


class SlackChannel(TrendAwareChannel):
    """``slack`` channel:每 ≤39000 字符块一次 ``chat.postMessage``。

    Args:
        target: 频道 id 的凭据引用(``env:SLACK_CHANNEL`` style,发送期解析);
            与 ``targets`` 定向配置互斥可省,此时频道由 ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        token: 预解析 bot token(构造注入,测试用);省略 → 发送期从
            :data:`DEFAULT_TOKEN_ENV_REF` 解析。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、HTTP 传输失败、非 JSON 响应、
            ``ok != true``(原厂 ``error`` 进文案),或模板渲染失败。
    """

    name = "slack"
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
        """Render + 逐块 ``chat.postMessage``;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        channel = await self._resolve_channel(token, context)
        for text in split_message(self._compose(items, context), limit=MESSAGE_LIMIT):
            await self._post_message(token, channel, text)
        logger.debug(
            "slack 发送完成: slot=%s kind=%s count=%d channel_ref=%s",
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
            raise PushSendError(exc.code, f"slack bot 凭据解析失败: {exc}") from exc

    async def _resolve_channel(self, token: str, context: SendContext) -> str:
        """定向(``context.target.chat_id``)优先,退回 legacy target 引用。

        ``U…``/``W…`` 裸用户 id 不可直发(蓝本 #17444:chat.postMessage 报
        ``channel_not_found``),先 ``conversations.open`` 换 DM 会话 id;
        其余 id(C/G/D)原样投递。两条寻址路径全缺 → ``missing_target``。
        """
        if context.target is not None:
            channel = context.target.chat_id.strip()
        elif self._target is not None:
            channel = self._resolve_ref(self._target, "slack target")
        else:
            raise PushSendError(
                "missing_target",
                "slack 两条寻址路径均缺席:未配置 legacy target(频道 id 引用),"
                "本次发送也未携带 context.target",
            )
        if channel[:1] in ("U", "W"):
            return await self._open_dm(token, channel)
        return channel

    def _resolve_ref(self, reference: str, label: str) -> str:
        try:
            value = resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"{label} 引用 {reference!r} 解析结果为空",
            )
        return value

    async def _open_dm(self, token: str, user_id: str) -> str:
        """``conversations.open`` 换 DM 会话 id(蓝本 ``_resolve_slack_user_dm`` 同形态)。

        需应用具备 ``im:write`` scope;``ok=false``(如 ``user_not_found`` /
        ``not_authed``)原厂 error 进文案如实上抛。
        """
        body = {"users": user_id}
        data = await self._api_call(token, "conversations.open", body)
        channel = data.get("channel")
        dm_id = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(dm_id, str) or not dm_id.strip():
            raise PushSendError(
                "invalid_response",
                f"slack conversations.open 响应缺 channel.id(用户 {user_id[:4]}…):"
                f" {str(data)[:200]!r}",
            )
        return dm_id.strip()

    # ------------------------------------------------------------- send

    async def _post_message(self, token: str, channel: str, text: str) -> None:
        """一块正文:``chat.postMessage``(纯文本,不 unfurl 预览)。"""
        await self._api_call(token, "chat.postMessage", {"channel": channel, "text": text})

    async def _api_call(self, token: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST {API_BASE}/{method}``;HTTP 非 2xx / 非 JSON / ``ok != true`` 报错。

        错误文案保留 ``HTTP <status>`` 与原厂 ``error`` 描述片段,供死信分类
        (:func:`shishi.push.delivery.classify_dead_error`)。
        """
        url = f"{API_BASE}/{method}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"slack 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 400:
            # HTTP 层错误(429 限频 / 5xx / 方法不存在):状态码先于 JSON 解析
            # 进文案,死信分类按 429/5xx → 瞬态、403/404 → 硬失败命中。
            raise PushSendError(
                "slack_api_error",
                f"slack HTTP {response.status_code}({method}): {response.text[:200]!r}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"slack 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, dict) or data.get("ok") is not True:
            error = data.get("error") if isinstance(data, dict) else None
            raise PushSendError(
                "slack_api_error",
                f"slack API 返回错误({method}): error={error}",
            )
        return data

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """slack 无自动发现(蓝本事实:适配器无列表 API 出站路径)。

        conversations.list 存在于官方 API 面,但本档蓝本未消费、MYIA 侧无
        入站可回填;条目唯一来源 = 别名手工登记 + 直达 id(组内约定)。
        """
        raise DirectoryDiscoverUnsupported(
            "slack 无自动发现(蓝本事实):本档未消费列表 API;"
            "直达写 slack:C…/G…/D…/U…(U/W 自动换 DM),常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``C/G/D/U/W`` 系 id 不经目录。

        U/W 形态发送期先 conversations.open 换 D…(蓝本 #17444);非 id 形态
        (名称等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if CHANNEL_ID_RE.fullmatch(value):
            return ChannelTarget(
                platform="slack", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
