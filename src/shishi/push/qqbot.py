"""QQ 机器人通道:QQ 开放平台 ``api.sgroup.qq.com`` 主动消息(REST 直发)。

蓝本归属(10-03-messaging-w3-longtail 组一):出站形态移植自 Hermes
``gateway/platforms/qqbot/``(``adapter.py`` 的 ``_ensure_token`` /
``_api_request`` / ``_build_text_body`` / ``_rest_path``,``constants.py`` 的
``API_BASE``/``TOKEN_URL``,NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/gateway/platforms/qqbot/``)。MYIA 按本档语义重写,
不整块复制:

- **token 生命周期**(:meth:`QQBotChannel._ensure_token`):
  ``POST https://bots.qq.com/app/getAppAccessToken``(JSON body
  ``{"appId", "clientSecret"}``)→ ``{"access_token", "expires_in"}``(缺省
  7200s);进程内缓存、剩余 > 60s 才算有效(蓝本 ``_token_fresh`` 同判据)。
  出站头 ``Authorization: QQBot <access_token>``(蓝本 ``_auth_headers`` 同款,
  2024-04 后官方新鉴权形态)。
- **主动推送,非被动回复**(事实探查,PRD Requirements 2 定案):官方 v2
  消息接口两形态——被动回复**必须**携带入站 ``msg_id``(5 分钟有效期),
  主动消息**不带 msg_id**(消耗主动额度,群聊每月限 4 条/群)。MYIA 零入站
  (入站需 WebSocket 网关常驻,本档非目标)→ 只走主动推送;额度耗尽时原厂
  错误原样进文案如实上抛(绝不伪造 msg_id 假装被动回复)。
- **msg_seq 防重**:body 携带随机 ``msg_seq``(0..65535,蓝本
  ``_next_msg_seq`` 同算法;官方语义:相同 msg_id+msg_seq 组合重复发送失败,
  主动消息也要求该字段)。
- **三种寻址形态**(蓝本 ``_messages_path``/``_send_guild_text`` 同端点集):
  ``c2c:<openid>`` → ``POST /v2/users/{openid}/messages``(body 带
  msg_type/msg_seq);``group:<group_openid>`` → ``POST /v2/groups/{id}/messages``
  (同 body);``guild:<channel_id>`` → ``POST /channels/{id}/messages``(频道
  消息 body 只带 ``content``,无 msg_seq——蓝本 guild 分支同形态)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 ``env:QQBOT_TARGET``,值如
``group:ABCDEF``)。QQ 的 openid 系裸串无前缀可辨(蓝本靠入站元数据猜 chat
类型,MYIA 零入站)→ **显式形态前缀**是该约束下的诚实设计。目录无自动发现
(蓝本事实:出站无列表路径),别名手工登记。

错误文案保留 ``HTTP <status>`` 与原厂响应片段供死信分类
(:func:`shishi.push.delivery.classify_dead_error`):403(unauthorized,如
token 失效/未获该场景授权)→ forbidden、404 → not_found、429(限频)/5xx
→ 瞬态;额度类错误(原厂 ``message`` 文本)原样透传。

凭据安全基线同其余通道:appId/clientSecret 全为 ``env:``/``keychain:``
引用,发送期才解析,错误只带引用名;全部 HTTP 经注入的 ``httpx.AsyncClient``。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Callable, Sequence

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
    "API_BASE",
    "DEFAULT_APPID_REF",
    "DEFAULT_SECRET_REF",
    "DEFAULT_TARGET_ENV_REF",
    "MESSAGE_LIMIT",
    "QQBotChannel",
    "REF_RE",
    "TOKEN_EXPIRY_MARGIN_SECONDS",
    "TOKEN_TTL_SECONDS",
    "TOKEN_URL",
]

logger = logging.getLogger(__name__)

#: QQ 开放平台 REST 基址(蓝本 ``constants.API_BASE`` 同款)。
API_BASE = "https://api.sgroup.qq.com"
#: app access token 获取端点(蓝本 ``constants.TOKEN_URL`` 同款)。
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
#: access_token 有效期缺省(秒;官方应答 expires_in,蓝本同款)。
TOKEN_TTL_SECONDS = 7200
#: 缓存提前失效余量(秒;蓝本 ``_token_fresh`` 的 60s 同判据)。
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
#: appId 凭据引用缺省(官方「机器人 QQ 号/AppID」)。
DEFAULT_APPID_REF = "env:QQBOT_APP_ID"
#: clientSecret 凭据引用缺省。
DEFAULT_SECRET_REF = "env:QQBOT_CLIENT_SECRET"
#: legacy 目标的推荐引用名(显式配置 ``target`` 用;运行期不自动回退。
#: 值如 ``group:ABCDEF`` / ``c2c:XXX`` / ``guild:999``)。
DEFAULT_TARGET_ENV_REF = "env:QQBOT_TARGET"
#: 单条 content 上限(官方主动消息文本上限,蓝本 guild 分支的
#: ``content[:MAX_MESSAGE_LENGTH]`` 截断同判据);超长按行边界拆多条。
MESSAGE_LIMIT = 2000
#: 直达形态:``c2c:<openid>`` / ``group:<group_openid>`` / ``guild:<channel_id>``。
#: openid 系大小写字母数字下划线连字符串(官方形态);guild channel id 数字串
#: 亦被同一字符集覆盖。
REF_RE = re.compile(r"^(c2c|group|guild):([A-Za-z0-9_-]{4,64})$")


def next_msg_seq() -> int:
    """随机 msg_seq(0..65535;蓝本 ``_next_msg_seq`` 同算法:时间片 XOR 随机)。

    官方语义:``msg_id + msg_seq`` 组合用于防重复回复;主动消息无 msg_id,
    msg_seq 仍为必填序号,随机化避免跨次发送撞组合。
    """
    time_part = int(time.time()) % 100_000_000
    rand = int(uuid.uuid4().hex[:4], 16)
    return (time_part ^ rand) % 65536


class QQBotChannel(TrendAwareChannel):
    """``qqbot`` channel:token 现取 → 主动消息 REST 直发(零入站形态)。

    Args:
        target: 目标的凭据引用(值 ``c2c:…``/``group:…``/``guild:…``,发送期
            解析);与 ``targets`` 定向配置互斥可省,此时目标由
            ``context.target`` 给出。
        appid_ref: appId 引用;省略 → :data:`DEFAULT_APPID_REF`。
        secret_ref: clientSecret 引用;省略 → :data:`DEFAULT_SECRET_REF`。
        template: 可选用户模板(Jinja2);在场时渲染输出为消息文本;
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款复用)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。
        clock: 时间源注入(秒;token 缓存寿命判据,测试可钉死)。

    Raises:
        PushSendError: 凭据解析失败、目标形态非法、token 获取失败、HTTP
            传输失败、非 2xx(原厂 ``code``/``message`` 进文案),或模板渲染失败。
    """

    name = "qqbot"
    #: 目录寻址已开(context.target 优先,legacy target 兜底);协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        appid_ref: str | None = None,
        secret_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._target = target
        self._appid_ref = appid_ref or DEFAULT_APPID_REF
        self._secret_ref = secret_ref or DEFAULT_SECRET_REF
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout
        self._clock = clock
        self._cached_token: str | None = None
        self._token_expires_at = 0.0

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot 主动消息 POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        kind, target_id = self._resolve_target(context)
        token = await self._ensure_token()
        await self._post_active_message(token, kind, target_id, self._compose(items, context))
        logger.debug(
            "qqbot 已提交主动消息: slot=%s kind=%s count=%d target_kind=%s",
            context.slot,
            context.kind,
            len(items),
            kind,
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

    def _resolve_target(self, context: SendContext) -> tuple[str, str]:
        """定向优先 → ``(kind, target_id)``;形态非法/两路全缺如实报错。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"qqbot target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "qqbot 两条寻址路径均缺席:未配置 legacy target(c2c:/group:/guild: 形态"
                "引用),本次发送也未携带 context.target",
            )
        match = REF_RE.fullmatch(value)
        if match is None:
            # 解析值不回显(openid 系虽非凭据,但回显面会放大死信分类的
            # 误判暴露;长度 + 形态描述足够定位配置笔误)。
            raise PushSendError(
                "invalid_credential_ref",
                f"qqbot 目标形态非法(须为 c2c:<openid> / group:<group_openid> /"
                f" guild:<channel_id>):得到 {len(value)} 字符的值,不匹配任一形态"
                "(解析值不回显)",
            )
        return match.group(1), match.group(2)

    # -------------------------------------------------------- token 生命周期

    async def _ensure_token(self) -> str:
        """有效缓存(剩余 > 60s)复用;否则 getAppAccessToken 现取。

        MYIA 通道实例随派发重建,缓存是进程内的(一次 dispatch 批内复用,
        跨批现取——QQ token 端点无紧限频,换取零落盘的凭据安全面)。
        """
        now = self._clock()
        if self._cached_token and now < self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS:
            return self._cached_token
        app_id = self._resolve_ref(self._appid_ref, "qqbot appId")
        client_secret = self._resolve_ref(self._secret_ref, "qqbot clientSecret")
        data = await self._post_token(app_id, client_secret)
        token = data.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise PushSendError(
                "invalid_response",
                f"qqbot token 应答缺 access_token: {str(data)[:200]!r}",
            )
        try:
            expires_in = float(data.get("expires_in") or TOKEN_TTL_SECONDS)
        except (TypeError, ValueError):
            expires_in = float(TOKEN_TTL_SECONDS)
        self._cached_token = token
        self._token_expires_at = self._clock() + expires_in
        return token

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

    async def _post_token(self, app_id: str, client_secret: str) -> dict[str, Any]:
        """``POST getAppAccessToken``(蓝本 body 形态同款)。

        非 2xx(如 400 错误体)报 ``qqbot_api_error`` 带 ``HTTP <status>`` +
        原厂片段(msgraph_webhook._post_token 同款取舍——状态码先于 JSON
        形态判读,错误体未及 access_token 不算「缺字段」)。
        """
        body = {"appId": app_id, "clientSecret": client_secret}
        try:
            if self._client is not None:
                response = await self._client.post(TOKEN_URL, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(TOKEN_URL, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"qqbot token 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"qqbot token 响应不是 JSON(HTTP {response.status_code}):"
                f" {response.text[:200]!r}",
            ) from exc
        if response.status_code >= 400:
            raise PushSendError(
                "qqbot_api_error",
                f"qqbot token 获取失败: HTTP {response.status_code}"
                f" {response.text[:160]!r}",
            )
        if not isinstance(data, dict):
            raise PushSendError(
                "invalid_response",
                f"qqbot token 响应不是 JSON 对象: {str(data)[:200]!r}",
            )
        return data

    # ------------------------------------------------------------- send

    async def _post_active_message(
        self, token: str, kind: str, target_id: str, content: str
    ) -> None:
        """主动消息(无 msg_id;蓝本端点集三形态)。

        guild 频道消息 body 只带 ``content``;c2c/group 带 ``msg_type=0`` +
        随机 ``msg_seq``。额度/授权类失败原厂响应原样进文案。
        """
        if kind == "guild":
            path = f"/channels/{target_id}/messages"
            body: dict[str, Any] = {"content": content}
        else:
            resource = "users" if kind == "c2c" else "groups"
            path = f"/v2/{resource}/{target_id}/messages"
            body = {"content": content, "msg_type": 0, "msg_seq": next_msg_seq()}
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"QQBot {token}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"qqbot 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 400:
            # HTTP <status> + 原厂 body 片段进文案:403 → forbidden、
            # 404 → not_found、429(限频)/5xx → 瞬态;额度耗尽(原厂
            # message)原样透传,不吞不改。
            raise PushSendError(
                "qqbot_api_error",
                f"qqbot HTTP {response.status_code}: {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """qqbot 无自动发现(蓝本事实:出站无列表路径;入站元数据系网关态)。"""
        raise DirectoryDiscoverUnsupported(
            "qqbot 无自动发现(蓝本事实):主动消息无「列出会话」API;"
            "直达写 qqbot:group:<group_openid> / c2c:<openid> / guild:<channel_id>,"
            "常用地名用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``c2c:/group:/guild:`` 形态前缀不经目录。

        openid 裸串无前缀可辨(蓝本靠入站元数据,MYIA 零入站)→ 无前缀
        形态返回 None,调用方回落目录四路径解析(别名的 chat_id 仍是带
        前缀形态)。
        """
        value = ref.strip()
        match = REF_RE.fullmatch(value)
        if match is not None:
            return ChannelTarget(
                platform="qqbot", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
