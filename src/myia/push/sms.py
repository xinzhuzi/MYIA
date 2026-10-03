"""SMS 通道:Twilio REST 出站(one-shot ``POST Messages.json``)。

蓝本归属(10-03-messaging-w3-longtail 组三):出站形态移植自 Hermes
``plugins/platforms/sms/adapter.py`` 的 ``_standalone_send`` /
``_messages_endpoint`` / ``_twilio_form`` / ``_basic_auth``
(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/sms/adapter.py``)。MYIA 只做出站
定向推送(入站 TwiML webhook + 签名校验是蓝本的另一半,MYIA 零入站),
按本档语义重写,不整块复制:

- **one-shot form POST**:
  ``POST {TWILIO_API_BASE}/{AccountSID}/Messages.json``,HTTP Basic
  (``AccountSID:AuthToken``),application/x-www-form-urlencoded 表单
  ``From``/``To``/``Body``(蓝本同款三字段;``To`` = E.164 收信号码)。
- **1600 截断**:Twilio 单条 Messages.json body 上限 1600 字符(蓝本
  ``MAX_SMS_LENGTH``,约 10 个 SMS 分段);超长截到 :data:`MESSAGE_LIMIT`
  并告警,不拆多条——通知场景一条即达(ntfy 同款取舍)。
- **错误码 → 死信映射(W2 模板探查,已对照官方错误码表)**:错误文案保留
  ``HTTP <status>`` 与 Twilio ``code=<n>``/``message`` 片段,经
  :func:`myia.push.delivery.classify_dead_error` 判定——HTTP 403 →
  ``forbidden``、HTTP 404 → ``not_found``(账号/端点级,蓝本 ``_HTTP_CALL_ERRORS``
  同族)、429/5xx/传输失败 → 瞬态不标。HTTP 400 + ``code=21211``(To 号码
  无效)是 Twilio 语义上的硬失败,但与 core 分类器 ASCII marker 表无码点
  交集 → 按瞬态处理(误标死信的代价——好号码被跳过——高于多试一轮;
  core 分类器将来扩表 ``code=21211`` 即可直接命中)。

寻址(design D1):``supports_targeting=True``;``context.target.chat_id``
优先、退回 legacy ``target`` 引用(缺省 :data:`DEFAULT_TARGET_ENV_REF`),
两路全缺 ``missing_target``;号码形态非法(E.164 之外)→
``invalid_credential_ref``(绝不猜号码)。直达 = E.164 号码
(:data:`E164_RE`);无目录发现(:class:`DirectoryDiscoverUnsupported`)
——短信无「列出可达对象」API,条目唯一来源 = 别名手工登记 + 直达号码。

凭据安全基线同其余通道:AccountSID/AuthToken/From 号码全部 ``env:``/
``keychain:`` 引用,发送期才解析,错误只带引用名。

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real Twilio API.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any, Sequence

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
    "DEFAULT_FROM_ENV_REF",
    "DEFAULT_SID_ENV_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "E164_RE",
    "MESSAGE_LIMIT",
    "SmsChannel",
    "TWILIO_API_BASE",
    "build_basic_auth",
]

logger = logging.getLogger(__name__)

#: Twilio REST API base(蓝本 ``TWILIO_API_BASE`` 同款);发送端点 =
#: ``{base}/{AccountSID}/Messages.json``。
TWILIO_API_BASE = "https://api.twilio.com/2010-04-01/Accounts"
#: AccountSID 凭据引用(发送期解析;值永不入日志)。
DEFAULT_SID_ENV_REF = "env:TWILIO_ACCOUNT_SID"
#: AuthToken 凭据引用。
DEFAULT_TOKEN_ENV_REF = "env:TWILIO_AUTH_TOKEN"
#: 发信号码(E.164)引用(蓝本 ``TWILIO_PHONE_NUMBER``)。
DEFAULT_FROM_ENV_REF = "env:TWILIO_PHONE_NUMBER"
#: legacy 收信号码引用(定向 ``targets`` 在场时可省,schema 层允许)。
DEFAULT_TARGET_ENV_REF = "env:SMS_TO"
#: 单条 body 上限(蓝本 ``MAX_SMS_LENGTH`` 同款 1600;超长截断不拆多条)。
MESSAGE_LIMIT = 1600
#: 直达收信号码形态:E.164(``+`` 开头、1-9 起始国家码、总长 8-15 位)。
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def build_basic_auth(account_sid: str, auth_token: str) -> str:
    """``AccountSID:AuthToken`` → HTTP Basic 头值(蓝本 ``_basic_auth`` 同款)。"""
    encoded = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


class SmsChannel(TrendAwareChannel):
    """``sms`` channel:每条消息一次 one-shot ``POST Messages.json``(Twilio)。

    Args:
        target: 收信号码(E.164)的凭据引用(``env:SMS_TO`` style,发送期
            解析);定向(``targets``)在场时可省(schema 允许),号码由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);渲染输出为整条 SMS 正文(短信
            渲染 markdown 为字面字符,内置版式本就是纯文本;用户模板自担
            排版)。省略 → 内置纯文本版式(ntfy ``build_message`` 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        sid_ref: AccountSID 引用(缺省 :data:`DEFAULT_SID_ENV_REF`)。
        token_ref: AuthToken 引用(缺省 :data:`DEFAULT_TOKEN_ENV_REF`)。
        from_ref: 发信号码引用(缺省 :data:`DEFAULT_FROM_ENV_REF`)。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、两条寻址路径全缺(``missing_target``)、
            号码形态非法(``invalid_credential_ref``)、HTTP 传输失败
            (``http_error``)、非 2xx 响应(``sms_api_error``,文案保留
            ``HTTP <status>`` 与 Twilio ``code``/``message`` 片段供死信分类)
            或模板渲染失败。
    """

    name = "sms"
    #: 目录寻址已开(context.target 优先,legacy target 兜底)。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        sid_ref: str | None = None,
        token_ref: str | None = None,
        from_ref: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._sid_ref = sid_ref or DEFAULT_SID_ENV_REF
        self._token_ref = token_ref or DEFAULT_TOKEN_ENV_REF
        self._from_ref = from_ref or DEFAULT_FROM_ENV_REF
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + one-shot form POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        to_number = self._resolve_recipient(context)
        sid = self._resolve(self._sid_ref, "sms AccountSID")
        token = self._resolve(self._token_ref, "sms AuthToken")
        from_number = self._resolve(self._from_ref, "sms 发信号码")
        if not E164_RE.fullmatch(from_number):
            raise PushSendError(
                "invalid_credential_ref",
                f"sms 发信号码不是 E.164 形态(引用 {self._from_ref!r}): {from_number[:32]!r}",
            )
        body = self._compose(items, context)
        await self._post(sid, token, from_number, to_number, body)
        logger.debug(
            "sms 已发送: slot=%s kind=%s count=%d target_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        """正文:用户模板在场走渲染器,缺省内置纯文本版式;超长截 1600。"""
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
            logger.warning("sms 正文超限,截断 %d → %d 字符", len(text), MESSAGE_LIMIT)
            text = text[:MESSAGE_LIMIT]
        return text

    # ---------------------------------------------------------- credentials

    @staticmethod
    def _resolve(reference: str, label: str) -> str:
        """必需引用:解析失败如实报错(错误只带引用名)。"""
        try:
            value = resolve_credential(reference)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败(引用 {reference!r}): {exc}") from exc
        return value.strip()

    def _resolve_recipient(self, context: SendContext) -> str:
        """定向优先,退回 legacy target 引用;全缺 ``missing_target``。"""
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"sms target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "sms 两条寻址路径均缺席:未配置 legacy target(收信号码引用),"
                "本次发送也未携带 context.target",
            )
        if not E164_RE.fullmatch(value):
            raise PushSendError(
                "invalid_credential_ref",
                f"sms 收信号码不是 E.164 形态(须为 +国家码号码): {value[:32]!r}",
            )
        return value

    # ---------------------------------------------------------------- post

    async def _post(
        self, sid: str, token: str, from_number: str, to_number: str, body: str
    ) -> None:
        """One-shot form POST;2xx 即成功,否则带原厂片段结构化报错。"""
        url = f"{TWILIO_API_BASE}/{sid}/Messages.json"
        headers = {"Authorization": build_basic_auth(sid, token)}
        data = {"From": from_number, "To": to_number, "Body": body}
        try:
            if self._client is not None:
                response = await self._client.post(url, data=data, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"sms 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 300:
            # 原厂片段进文案:HTTP 状态供死信分类(403/404),Twilio code/
            # message(如 code=21211 To 号码无效)供诊断;HTTP 400 + 21211
            # 不与 core 分类器 marker 表相交 → 瞬态(模块 docstring 注记)。
            try:
                payload = response.json()
            except ValueError:
                payload = None
            detail = ""
            if isinstance(payload, dict):
                code = payload.get("code")
                message = payload.get("message")
                detail = f" code={code} message={message!r}" if message else ""
            raise PushSendError(
                "sms_api_error",
                f"sms HTTP {response.status_code}:{detail} {response.text[:200]!r}",
            )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """sms 无目录发现:短信无「列出可达对象」API(蓝本事实)。"""
        raise DirectoryDiscoverUnsupported(
            "sms 无自动发现:收信号码即地址,无目录概念;直达写 sms:<E.164 号码>,"
            "常用收件人可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``sms:<E.164 号码>`` 不经目录;其余(中文别名等)回落目录。"""
        value = ref.strip()
        if E164_RE.fullmatch(value):
            return ChannelTarget(
                platform="sms", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
