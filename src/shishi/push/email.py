"""Email 通道:SMTP 出站(``smtplib`` 纯标准库,非 httpx)。

蓝本归属(10-03-messaging-w3-longtail 组三):出站形态移植自 Hermes
``plugins/platforms/email/adapter.py`` 的 ``_standalone_send`` /
``_smtp_send`` / ``_open_smtp``(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/plugins/platforms/email/adapter.py``)。MYIA 只做出站
定向推送(入站 IMAP 轮询是蓝本的另一半,MYIA 零入站),按本档语义重写,
不整块复制:

- **security 三态**(蓝本 ``_SECURITY_ALIASES``/``_normalize_security`` 同款
  归一):``tls``(465 隐式 SSL)/ ``starttls``(587)/ ``plain``;缺省按端口
  推导——465 → ``tls``,其余 → ``starttls``(蓝本同款缺省)。未知取值告警
  回落缺省,绝不静默降级到明文。
- **纯 stdlib + 可注入 opener**:SMTP 会话经构造注入的 ``opener`` 创建
  (缺省 :func:`default_smtp_opener`,``SMTP_SSL``/``SMTP``+``starttls``);
  阻塞 socket 调用走 ``asyncio.to_thread``,不占事件循环。测试注入桩
  opener,零真网(同组内 httpx 通道的 ``MockTransport`` 注入位)。
- **登录三件套**:from 地址 / 密码 / SMTP host 全部是 ``env:``/``keychain:``
  引用(schema 纪律:YAML 零明文),发送期才解析,错误文案只带引用名。
  密码显式引用解析失败即报错;缺省 ``env:EMAIL_PASSWORD`` 缺席 = 匿名投递
  (本地中继 ``localhost:25`` 合法态——ntfy 可选 token 同款取舍,告警不报错)。
- **一封一报**:subject = :func:`~shishi.push.feishu_card.card_title`(跨通道
  标题一致),正文 = 用户模板渲染或内置纯文本版式;正文不预切(蓝本
  ``MAX_MESSAGE_LENGTH`` 50K 是 Gmail 单封上限,远超摘要体量)。

寻址(design D1):``supports_targeting=True``;``context.target.chat_id``
优先、退回 legacy ``target`` 引用(**须显式配置,无运行期 env 缺省回退**;
推荐引用名 :data:`DEFAULT_TARGET_ENV_REF`),
两路全缺 ``missing_target``(feishu_card 同款 fail-fast);收件人形态非法
→ ``invalid_credential_ref``(绝不猜地址)。直达 = 收件人邮箱地址
(:data:`EMAIL_RE`);无目录发现(:class:`DirectoryDiscoverUnsupported`)
——邮箱通讯录无 list API,条目唯一来源 = 别名文件手工登记 + 直达地址。

错误码 → 死信映射(W2 模板探查):SMTP 应答码空间(535 认证失败 / 550
收件人拒收 / 421 临时拒绝)与 core 分类器
(:func:`shishi.push.delivery.classify_dead_error`)的 ASCII marker 表
(``403`` / ``http 404`` / ``forbidden`` 族)无码点交集 → **本通道全部
错误按瞬态处理,不标 dead**。这不是疏漏而是取舍:邮件硬退信以 DSN 回执
邮件形式异步到达(发送期常是「先收下再退信」),发送期拒绝是服务器实现
细节;错误文案仍保留原厂 SMTP 应答片段(``(535, b'…')``)供诊断,若
core 分类器将来扩表即可直接命中。
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any, Callable, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel, item_view
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.feishu_card import card_title
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_FROM_ENV_REF",
    "DEFAULT_HOST_ENV_REF",
    "DEFAULT_PASSWORD_ENV_REF",
    "DEFAULT_PORT_ENV_REF",
    "DEFAULT_SECURITY_ENV_REF",
    "DEFAULT_SMTP_TIMEOUT_SECONDS",
    "DEFAULT_TARGET_ENV_REF",
    "EMAIL_RE",
    "SECURITY_ALIASES",
    "EmailChannel",
    "SmtpOpener",
    "build_body",
    "default_smtp_opener",
    "normalize_security",
]

logger = logging.getLogger(__name__)

#: 发件人地址引用(发送期解析;错误文案只带引用名)。
DEFAULT_FROM_ENV_REF = "env:EMAIL_ADDRESS"
#: SMTP 密码引用(缺省 env 缺席 = 匿名投递,本地中继合法态)。
DEFAULT_PASSWORD_ENV_REF = "env:EMAIL_PASSWORD"
#: SMTP host 引用(必需;缺省 env 未设 → env_var_missing)。
DEFAULT_HOST_ENV_REF = "env:EMAIL_SMTP_HOST"
#: SMTP 端口引用(可选;缺省 587,非数字 → invalid_credential_ref)。
DEFAULT_PORT_ENV_REF = "env:EMAIL_SMTP_PORT"
#: SMTP security 引用(可选;tls/starttls/plain,缺省按端口推导)。
DEFAULT_SECURITY_ENV_REF = "env:EMAIL_SMTP_SECURITY"
#: legacy 收件人地址引用(定向 ``targets`` 在场时可省,schema 层允许)。
DEFAULT_TARGET_ENV_REF = "env:EMAIL_TO"
#: SMTP 连接/交互超时(蓝本 ``SMTP_CONNECT_TIMEOUT`` 同款 30s)。
DEFAULT_SMTP_TIMEOUT_SECONDS = 30.0
#: SMTP 缺省端口(587 = STARTTLS 提交口;465 隐式 SSL 时 security 自动推导为 tls)。
DEFAULT_SMTP_PORT = 587
#: 隐式 SSL 端口(蓝本同款:security 未配置时 465 → tls,其余 → starttls)。
IMPLICIT_TLS_PORT = 465

#: security 别名归一(蓝本 ``_SECURITY_ALIASES`` 同款:ssl/implicit→tls、
#: none→plain;未知值告警回落调用方缺省,绝不静默降级明文)。
SECURITY_ALIASES = {
    "tls": "tls",
    "ssl": "tls",
    "implicit": "tls",
    "starttls": "starttls",
    "plain": "plain",
    "none": "plain",
}

#: 直达收件人形态:非空白字符、单一 ``@``、域段含 ``.``(保守形态,
#: 中文别名/群名不匹配 → 回落目录四路径)。
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

#: SMTP 会话工厂契约:(host, port, security, timeout) → 已建连的 SMTP 会话。
#: 测试注入点;缺省 :func:`default_smtp_opener`。
SmtpOpener = Callable[[str, int, str, float], "smtplib.SMTP"]


def normalize_security(value: str | None, default: str) -> str:
    """security 取值归一(蓝本 ``_normalize_security`` 同语义)。

    未知取值告警后回落 ``default``(拼写错误永不静默降级到明文——回落值
    由调用方按端口推导,而非硬编码 ``plain``)。
    """
    raw = str(value or "").strip().lower().replace("-", "").replace("_", "")
    if raw and raw not in SECURITY_ALIASES:
        logger.warning("email security 取值未知,回落缺省: value=%r default=%r", value, default)
    return SECURITY_ALIASES.get(raw, default)


def default_smtp_opener(host: str, port: int, security: str, timeout: float) -> smtplib.SMTP:
    """缺省 opener:``tls`` → ``SMTP_SSL``;``starttls`` → ``SMTP`` + 升级;
    ``plain`` → 裸 ``SMTP``。证书链走 ``ssl.create_default_context``(不提供
    关闭校验的口子——本地中继用 ``plain`` 明文形态即可,不开明文 TLS 例外)。
    """
    context = ssl.create_default_context()
    if security == "tls":
        return smtplib.SMTP_SSL(host, port, context=context, timeout=timeout)
    smtp = smtplib.SMTP(host, port, timeout=timeout)
    if security == "starttls":
        try:
            smtp.starttls(context=context)
        except Exception:
            smtp.close()
            raise
    return smtp


def build_body(items: Sequence[Any], context: SendContext) -> str:
    """内置纯文本正文:每条目一行「标题 · URL」(标题在 subject,正文不重复)。

    与 ntfy ``build_message`` 同款行形态;邮件 subject 已由
    :func:`~shishi.push.feishu_card.card_title` 承载,正文直接从条目行开始。
    """
    lines: list[str] = []
    for item in items:
        view = item_view(item)
        title = str(view.get("title") or "(无标题)")
        url = view.get("url")
        lines.append(f"▸ {title} · {url}" if url else f"▸ {title}")
    return "\n".join(lines)


class EmailChannel(TrendAwareChannel):
    """``email`` channel:每条消息一封 SMTP 邮件(纯 stdlib 出站)。

    Args:
        target: 收件人地址的凭据引用(``env:EMAIL_TO`` style,发送期解析);
            定向(``targets``)在场时可省(schema 允许),地址由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);渲染输出为邮件正文,subject 仍为
            card_title。省略 → 内置纯文本版式(:func:`build_body`)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        from_ref: 发件人地址引用(必需项;缺省 :data:`DEFAULT_FROM_ENV_REF`)。
        password_ref: SMTP 密码引用;缺省 :data:`DEFAULT_PASSWORD_ENV_REF`,
            env 缺席 = 匿名投递(本地中继合法态,告警不报错)。
        host_ref: SMTP host 引用(必需项;缺省 :data:`DEFAULT_HOST_ENV_REF`)。
        port_ref: 端口引用(可选;缺省 :data:`DEFAULT_PORT_ENV_REF`,env 缺席
            = 587,非数字 → ``invalid_credential_ref``)。
        security_ref: security 引用(可选;缺省 :data:`DEFAULT_SECURITY_ENV_REF`,
            env 缺席按端口推导:465 → tls,其余 → starttls)。
        opener: SMTP 会话工厂注入点(测试桩替身,零真网)。
        timeout: 连接/交互超时秒数(缺省 :data:`DEFAULT_SMTP_TIMEOUT_SECONDS`)。

    Raises:
        PushSendError: 凭据解析失败、两条寻址路径全缺(``missing_target``)、
            收件人形态非法(``invalid_credential_ref``)、SMTP 认证失败
            (``email_auth_error``)、收件人被拒(``email_recipients_refused``)、
            其余 SMTP/网络失败(``email_smtp_error``)或模板渲染失败。
            全部错误按瞬态处理,不进死信(见模块 docstring 映射注记)。
    """

    name = "email"
    #: 目录寻址已开(context.target 优先,legacy target 兜底)。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        from_ref: str | None = None,
        password_ref: str | None = None,
        host_ref: str | None = None,
        port_ref: str | None = None,
        security_ref: str | None = None,
        opener: SmtpOpener | None = None,
        timeout: float = DEFAULT_SMTP_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._from_ref = from_ref or DEFAULT_FROM_ENV_REF
        self._password_ref = password_ref or DEFAULT_PASSWORD_ENV_REF
        self._host_ref = host_ref or DEFAULT_HOST_ENV_REF
        self._port_ref = port_ref or DEFAULT_PORT_ENV_REF
        self._security_ref = security_ref or DEFAULT_SECURITY_ENV_REF
        self._opener = opener or default_smtp_opener
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Compose + one SMTP submission;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/address/SMTP failure (callers
                isolate per channel; nothing is raised on success).
        """
        recipient = self._resolve_recipient(context)
        from_addr = self._resolve_required(self._from_ref, "email 发件人地址")
        host = self._resolve_required(self._host_ref, "email SMTP host")
        password = self._resolve_optional_password()
        port = self._resolve_port()
        security = self._resolve_security(port)
        message = self._build_message(items, context, from_addr, recipient)
        # smtplib 是阻塞 socket:丢线程池,不占事件循环(蓝本同走 executor)。
        try:
            await asyncio.to_thread(
                self._deliver, message, from_addr, password, host, port, security
            )
        except PushSendError:
            raise
        except Exception as exc:  # noqa: BLE001 - _deliver 只抛结构化映射,兜底防御
            raise PushSendError(
                "email_smtp_error", f"email SMTP 发送失败: {type(exc).__name__}: {exc}"
            ) from exc
        logger.debug(
            "email 已投递: slot=%s kind=%s count=%d target_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _build_message(
        self,
        items: Sequence[Any],
        context: SendContext,
        from_addr: str,
        recipient: str,
    ) -> EmailMessage:
        """邮件装配:subject=card_title,正文=模板/内置版式(RFC 2047 自动编码)。"""
        if self._template is not None:
            # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
            # template_render_error(语法错误已在加载期被 schema 拒绝)。
            try:
                body = self._renderer.render(
                    self._template, items, context, **self.trend_render_kwargs()
                )
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
        else:
            body = build_body(items, context)
        message = EmailMessage()
        message["From"] = from_addr
        message["To"] = recipient
        message["Subject"] = card_title(context)
        message["Date"] = formatdate(localtime=True)
        domain = from_addr.rsplit("@", 1)[-1] if "@" in from_addr else "localhost"
        message["Message-ID"] = f"<myia-{uuid.uuid4().hex[:12]}@{domain}>"
        message.set_content(body, charset="utf-8")
        return message

    def _deliver(
        self,
        message: EmailMessage,
        from_addr: str,
        password: str | None,
        host: str,
        port: int,
        security: str,
    ) -> None:
        """阻塞投递(线程池内执行):建连 → 可选登录 → send_message → 释放。

        SMTP 异常族在这里映射为结构化 :class:`PushSendError`(原厂应答片段
        保留在文案里);opener 自身建连失败同样落入 ``email_smtp_error``。
        """
        try:
            session = self._opener(host, port, security, self._timeout)
        except PushSendError:
            raise
        except Exception as exc:  # noqa: BLE001 - 建连失败统一结构化
            raise PushSendError(
                "email_smtp_error",
                f"email SMTP 建连失败({host}:{port} security={security}): "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        try:
            if password is not None:
                try:
                    session.login(from_addr, password)
                except smtplib.SMTPAuthenticationError as exc:
                    # 错误文案带原厂应答(如 (535, b'authentication failed')),
                    # 不带密码值(纪律:凭据零明文)。
                    raise PushSendError(
                        "email_auth_error",
                        f"email SMTP 认证失败(检查 {self._password_ref!r} 是否为应用专用密码): "
                        f"{exc.smtp_code} {exc.smtp_error!r}",
                    ) from exc
                except smtplib.SMTPException as exc:
                    raise PushSendError(
                        "email_auth_error", f"email SMTP 登录失败: {type(exc).__name__}: {exc}"
                    ) from exc
            try:
                session.send_message(message)
            except smtplib.SMTPRecipientsRefused as exc:
                # 全部收件人被拒:文案保留逐收件人原厂应答(诊断用)。
                refused = "; ".join(
                    f"{addr}: {code} {msg!r}" for addr, (code, msg) in exc.recipients.items()
                )
                raise PushSendError(
                    "email_recipients_refused",
                    f"email 收件人被服务器拒绝: {refused}",
                ) from exc
            except smtplib.SMTPResponseException as exc:
                raise PushSendError(
                    "email_smtp_error",
                    f"email SMTP 拒绝发送: {exc.smtp_code} {exc.smtp_error!r}",
                ) from exc
            except (smtplib.SMTPException, OSError) as exc:
                raise PushSendError(
                    "email_smtp_error", f"email SMTP 发送失败: {type(exc).__name__}: {exc}"
                ) from exc
        finally:
            try:
                session.quit()
            except Exception:  # noqa: BLE001 - 释放兜底:quit 失败强关(蓝本同款)
                session.close()

    # ---------------------------------------------------------- credentials

    @staticmethod
    def _resolve_required(reference: str, label: str) -> str:
        """必需引用:解析失败如实报错(错误只带引用名)。"""
        try:
            value = resolve_credential(reference)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败: {exc}") from exc
        value = value.strip()
        if not value:
            raise PushSendError(
                "invalid_credential_ref", f"{label} 引用 {reference!r} 解析结果为空"
            )
        return value

    def _resolve_optional_password(self) -> str | None:
        """密码引用:显式(≠缺省名)失败即报错;缺省 env 缺席 = 匿名投递。"""
        try:
            value = resolve_credential(self._password_ref)
        except CredentialResolveError as exc:
            if self._password_ref == DEFAULT_PASSWORD_ENV_REF:
                logger.warning(
                    "email 未配置 SMTP 密码(%s 缺席),尝试匿名投递(本地中继合法态)",
                    DEFAULT_PASSWORD_ENV_REF,
                )
                return None
            raise PushSendError(exc.code, f"email SMTP 密码解析失败: {exc}") from exc
        value = value.strip()
        return value or None

    def _resolve_port(self) -> int:
        """端口引用(可选):env 缺席 = 587;非数字 → invalid_credential_ref。"""
        try:
            raw = resolve_credential(self._port_ref)
        except CredentialResolveError:
            return DEFAULT_SMTP_PORT  # env 未设:可选调参缺席是合法态
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise PushSendError(
                "invalid_credential_ref",
                f"email SMTP 端口引用 {self._port_ref!r} 解析结果不是整数: {raw.strip()!r}",
            ) from exc

    def _resolve_security(self, port: int) -> str:
        """security 引用(可选):env 缺席按端口推导(蓝本同款)。"""
        try:
            raw = resolve_credential(self._security_ref)
        except CredentialResolveError:
            raw = ""
        default = "tls" if port == IMPLICIT_TLS_PORT else "starttls"
        return normalize_security(raw, default)

    def _resolve_recipient(self, context: SendContext) -> str:
        """定向(``context.target.chat_id``)优先,退回 legacy target 引用。

        两条寻址路径均缺席 → ``missing_target``;解析值非邮箱形态 →
        ``invalid_credential_ref``(绝不猜地址)。
        """
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"email target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "email 两条寻址路径均缺席:未配置 legacy target(收件人地址引用),"
                "本次发送也未携带 context.target",
            )
        if not EMAIL_RE.fullmatch(value):
            # 解析值不回显(死信分类误判暴露面收敛,D1;邮箱属通讯录敏感面)。
            raise PushSendError(
                "invalid_credential_ref",
                f"email 收件人不是邮箱地址形态:得到 {len(value)} 字符的值,"
                "不匹配该形态(解析值不回显)",
            )
        return value

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """email 无目录发现:邮箱无「列出可达对象」API(MYIA 亦无入站可回填)。"""
        raise DirectoryDiscoverUnsupported(
            "email 无自动发现:收件人即地址,无目录概念;直达写 email:<地址>,"
            "常用收件人可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``email:<地址>``(单一 ``@`` 的邮箱形态)不经目录。

        非邮箱形态(中文别名等)返回 None,调用方回落目录四路径——别名命名
        不受邮箱字符集限制。
        """
        value = ref.strip()
        if EMAIL_RE.fullmatch(value):
            return ChannelTarget(
                platform="email", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
