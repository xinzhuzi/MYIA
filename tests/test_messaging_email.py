"""Tests for 10-03-messaging-w3-longtail 组三 — email(SMTP stdlib)适配器。

覆盖:SMTP 会话形态(security 三态/登录/投递)、邮件头(From/To/Subject=
card_title)与正文版式、可选密码(显式引用失败报错/缺省缺席匿名投递)、
寻址两条路(定向优先/legacy 引用/全缺 missing_target/形态非法)、直达
``email:<地址>`` 与别名/前缀寻址、错误分类(SMTP 应答码空间与 core 分类器
ASCII marker 表无交集 → 全瞬态不标 dead)、DirectoryDiscoverUnsupported。

蓝本对照:Hermes ``plugins/platforms/email/adapter.py`` 的
``_standalone_send``/``_smtp_send``/``_open_smtp``
(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

No pytest-asyncio: async calls run through ``asyncio.run``;SMTP 会话经注入
opener 桩替身,零真网。
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import replace
from typing import Any

import pytest

from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.email import DEFAULT_TARGET_ENV_REF, EmailChannel, normalize_security
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _FakeSMTP:
    """smtplib.SMTP 桩:记录 login/send_message/quit,按需抛蓝本错误族。"""

    def __init__(
        self,
        *,
        auth_error: tuple[int, bytes] | None = None,
        recipients_refused: dict[str, tuple[int, bytes]] | None = None,
        response_error: tuple[int, bytes] | None = None,
    ) -> None:
        self.login_calls: list[tuple[str, str]] = []
        self.sent: list[Any] = []
        self.quit_called = False
        self._auth_error = auth_error
        self._recipients_refused = recipients_refused
        self._response_error = response_error

    def login(self, user: str, password: str) -> None:
        if self._auth_error is not None:
            raise smtplib.SMTPAuthenticationError(*self._auth_error)
        self.login_calls.append((user, password))

    def send_message(self, msg: Any) -> None:
        if self._recipients_refused is not None:
            raise smtplib.SMTPRecipientsRefused(self._recipients_refused)
        if self._response_error is not None:
            raise smtplib.SMTPResponseException(*self._response_error)
        self.sent.append(msg)

    def quit(self) -> None:
        self.quit_called = True


def _channel(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake: _FakeSMTP | None = None,
    opener_calls: list[dict] | None = None,
    connect_error: Exception | None = None,
    password_ref: str | None = None,
    **kwargs: Any,
) -> EmailChannel:
    """标准测试通道:全凭据走 MYIA_TEST_* env 引用,SMTP 会话注入桩。"""
    monkeypatch.setenv("MYIA_TEST_EMAIL_FROM", "myia@example.com")
    monkeypatch.setenv("MYIA_TEST_EMAIL_PASSWORD", "app-password-1")
    monkeypatch.setenv("MYIA_TEST_EMAIL_HOST", "smtp.example.com")
    fake = fake if fake is not None else _FakeSMTP()
    opener_calls = opener_calls if opener_calls is not None else []

    def opener(host: str, port: int, security: str, timeout: float) -> _FakeSMTP:
        opener_calls.append(
            {"host": host, "port": port, "security": security, "timeout": timeout}
        )
        if connect_error is not None:
            raise connect_error
        return fake

    kwargs.setdefault("target", "env:MYIA_TEST_EMAIL_TO")
    return EmailChannel(
        from_ref="env:MYIA_TEST_EMAIL_FROM",
        password_ref=password_ref or "env:MYIA_TEST_EMAIL_PASSWORD",
        host_ref="env:MYIA_TEST_EMAIL_HOST",
        opener=opener,
        **kwargs,
    )


@pytest.fixture()
def target_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYIA_TEST_EMAIL_TO", "owner@example.com")


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="email", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


# ---------------------------------------------------------------------------
# SMTP 会话形态 + 邮件装配
# ---------------------------------------------------------------------------


class TestSessionShape:
    def test_sends_via_opener_with_starttls_default(self, target_env, monkeypatch):
        """587 缺省 starttls(蓝本端口推导同款);登录三件套齐;投递后 quit。"""
        fake = _FakeSMTP()
        opener_calls: list[dict] = []
        channel = _channel(monkeypatch, fake=fake, opener_calls=opener_calls)

        _run(
            channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT)
        )

        assert opener_calls == [
            {"host": "smtp.example.com", "port": 587, "security": "starttls", "timeout": 30.0}
        ]
        assert fake.login_calls == [("myia@example.com", "app-password-1")]
        assert len(fake.sent) == 1 and fake.quit_called

    def test_port_465_defaults_to_implicit_tls(self, target_env, monkeypatch):
        """465 → tls(蓝本同款推导);端口经 env 引用可调。"""
        monkeypatch.setenv("MYIA_TEST_EMAIL_PORT", "465")
        fake = _FakeSMTP()
        opener_calls: list[dict] = []
        channel = _channel(
            monkeypatch,
            fake=fake,
            opener_calls=opener_calls,
            port_ref="env:MYIA_TEST_EMAIL_PORT",
        )

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert opener_calls[0]["security"] == "tls"
        assert opener_calls[0]["port"] == 465

    def test_security_alias_and_unknown_fallback(self):
        """security 归一:ssl/implicit→tls、none→plain;未知值回落缺省不降明文。"""
        assert normalize_security("ssl", "starttls") == "tls"
        assert normalize_security("implicit", "starttls") == "tls"
        assert normalize_security("STARTTLS", "tls") == "starttls"
        assert normalize_security("none", "starttls") == "plain"
        assert normalize_security("bogus", "starttls") == "starttls"
        assert normalize_security("", "tls") == "tls"

    def test_message_headers_and_body(self, target_env, monkeypatch):
        """Subject=card_title(跨通道一致);正文 = 条目行(标题不重复占行)。"""
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake)

        _run(
            channel.send(
                [{"title": "羊毛线报", "url": "https://x/1", "category": "羊毛"}],
                CONTEXT,
            )
        )

        msg = fake.sent[0]
        assert msg["From"] == "myia@example.com"
        assert msg["To"] == "owner@example.com"
        assert "羊毛" in msg["Subject"] and "上午" in msg["Subject"]
        body = msg.get_content()
        assert "羊毛线报" in body and "https://x/1" in body
        assert "Date" in msg and "Message-ID" in msg

    def test_template_render_becomes_body(self, target_env, monkeypatch):
        """用户模板在场:渲染输出为正文,subject 仍 card_title。"""
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        # set_content 语义:正文尾随一个换行(get_content 原样返回)。
        assert fake.sent[0].get_content() == "2026-10-03 共 1 条\n"

    def test_template_render_failure_is_structured(self, target_env, monkeypatch):
        channel = _channel(monkeypatch, template="{{ no_such_helper() }}")
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 凭据纪律:密码可选语义 / 引用失败 / 匿名投递
# ---------------------------------------------------------------------------


class TestCredentials:
    def test_default_password_absent_means_anonymous(self, target_env, monkeypatch):
        """缺省 env:EMAIL_PASSWORD 缺席 = 匿名投递(本地中继合法态,不登录)。"""
        fake = _FakeSMTP()
        monkeypatch.delenv("EMAIL_PASSWORD", raising=False)  # 缺省引用名缺席
        channel = _channel(monkeypatch, fake=fake, password_ref="env:EMAIL_PASSWORD")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert fake.login_calls == [] and len(fake.sent) == 1

    def test_explicit_password_ref_missing_is_error(self, target_env, monkeypatch):
        """显式密码引用解析失败 → env_var_missing(绝不静默降级匿名)。"""
        fake = _FakeSMTP()
        channel = _channel(
            monkeypatch, fake=fake, password_ref="env:MYIA_TEST_EMAIL_MISSING"
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert fake.sent == []

    def test_missing_from_or_host_is_env_var_missing(self, target_env, monkeypatch):
        """from 引用缺席 → env_var_missing(构造后删 env:发送期才解析)。"""
        channel = _channel(monkeypatch)
        monkeypatch.delenv("MYIA_TEST_EMAIL_FROM", raising=False)
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    def test_bad_port_value_is_invalid_ref(self, target_env, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_EMAIL_PORT", "not-a-port")
        channel = _channel(monkeypatch, port_ref="env:MYIA_TEST_EMAIL_PORT")
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_credential_ref"

    def test_error_messages_never_carry_credential_values(self, target_env, monkeypatch):
        """认证失败文案:带原厂应答与引用名,不带密码值。"""
        fake = _FakeSMTP(auth_error=(535, b"authentication failed"))
        channel = _channel(monkeypatch, fake=fake)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "app-password-1" not in str(excinfo.value)
        assert "535" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达与别名
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, target_env, monkeypatch):
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake)

        _run(
            channel.send([{"title": "t"}], replace(CONTEXT, target=_target("alt@example.com")))
        )

        assert fake.sent[0]["To"] == "alt@example.com"

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_EMAIL_TO", raising=False)
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert fake.sent == []

    @pytest.mark.parametrize("bad", ["not-an-address", "a@b", "带 空格@x.com", ""])
    def test_invalid_recipient_shape_is_rejected(self, target_env, monkeypatch, bad):
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert fake.sent == []

    def test_direct_ref_parse(self):
        for ref in ["owner@example.com", "a.b+tag@sub.domain.org"]:
            target = EmailChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "email",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["主人", "羊毛群", "a@b", "x y@z.com", ""]:
            assert EmailChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "email:owner@example.com",
            ChannelDirectory(tmp_path),
            platforms={"email": EmailChannel},
        )
        assert (target.platform, target.chat_id) == ("email", "owner@example.com")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_alias_and_prefix_addressing(self, tmp_path):
        """别名精确名命中 + 唯一前缀命中(目录四路径,不经直达)。"""
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("email", "owner@example.com", "主人")
        directory.set_alias("email", "a@example.com", "羊毛群")

        exact = resolve_target(
            "email:主人", directory, platforms={"email": EmailChannel}
        )
        assert (exact.chat_id, exact.resolved_from) == ("owner@example.com", "directory_name")

        prefix = resolve_target(
            "email:羊毛", directory, platforms={"email": EmailChannel}
        )
        assert (prefix.chat_id, prefix.resolved_from) == ("a@example.com", "directory_prefix")


# ---------------------------------------------------------------------------
# 错误分类:SMTP 应答码空间与 core marker 表无交集 → 全瞬态
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_auth_error_is_transient_not_dead(self, target_env, monkeypatch):
        """535 认证失败:结构化 email_auth_error;码空间无交集 → 不标 dead。"""
        fake = _FakeSMTP(auth_error=(535, b"authentication failed"))
        channel = _channel(monkeypatch, fake=fake)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "email_auth_error"
        assert classify_dead_error(excinfo.value) is None

    def test_recipients_refused_keeps_vendor_fragments(self, target_env, monkeypatch):
        """收件人被拒:文案保留逐收件人原厂应答;不标 dead(模块 docstring 取舍)。"""
        fake = _FakeSMTP(recipients_refused={"owner@example.com": (550, b"mailbox unavailable")})
        channel = _channel(monkeypatch, fake=fake)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "email_recipients_refused"
        assert "550" in str(excinfo.value) and "owner@example.com" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_smtp_response_error_is_structured(self, target_env, monkeypatch):
        fake = _FakeSMTP(response_error=(421, b"service not available"))
        channel = _channel(monkeypatch, fake=fake)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "email_smtp_error"
        assert "421" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_connect_failure_is_structured(self, target_env, monkeypatch):
        """opener 建连失败(蓝本 IPv6/网络族等价物)→ email_smtp_error 瞬态。"""
        channel = _channel(
            monkeypatch, connect_error=ConnectionRefusedError(61, "Connection refused")
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "email_smtp_error"
        assert "ConnectionRefusedError" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_session_quit_failure_falls_back_to_close(self, target_env, monkeypatch):
        """quit 抛错兜底 close,发送结果不受释放路径影响(蓝本同款)。"""
        fake = _FakeSMTP()
        channel = _channel(monkeypatch, fake=fake)

        def broken_quit() -> None:
            raise smtplib.SMTPException("connection lost")

        fake.quit = broken_quit
        fake.close = lambda: None

        _run(channel.send([{"title": "t"}], CONTEXT))  # 不抛:释放失败不影响投递结果
        assert len(fake.sent) == 1


# ---------------------------------------------------------------------------
# 目录语义(无自动发现)+ 协议契约
# ---------------------------------------------------------------------------


class TestDirectoryAndProtocol:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(EmailChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("email", [], now=1.0)
        counts = _run(directory.refresh({"email": EmailChannel()}, now=100.0))
        assert counts == {}

    def test_channel_protocol_conformance(self):
        channel = EmailChannel(target=DEFAULT_TARGET_ENV_REF)
        assert isinstance(channel, Channel)
        assert channel.name == "email"
        assert channel.supports_targeting is True
