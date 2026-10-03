"""Tests for 10-03-messaging-w3-longtail 组三 — sms(Twilio REST)适配器。

覆盖:one-shot form POST 形态(``{base}/{sid}/Messages.json`` + Basic 鉴权 +
From/To/Body 表单)、1600 截断、模板渲染、寻址两条路(定向优先/legacy
引用/全缺 missing_target/E.164 形态校验)、直达 ``sms:<E.164>`` 与别名/
前缀寻址、错误分类(HTTP 403 → forbidden、404 → not_found、429/5xx → 瞬态、
400+code=21211 → 瞬态不标)、DirectoryDiscoverUnsupported。

蓝本对照:Hermes ``plugins/platforms/sms/adapter.py`` 的
``_standalone_send``/``_messages_endpoint``/``_twilio_form``
(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

No pytest-asyncio: async calls run through ``asyncio.run``. All network I/O
goes through ``httpx.MockTransport``(零真网,不触真 Twilio)。
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from myia.push.base import Channel, PushSendError, SendContext
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.sms import MESSAGE_LIMIT, SmsChannel, build_basic_auth
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": request.content.decode("utf-8"),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(201, json={"sid": "SM123"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> SmsChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_SMS_TO")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return SmsChannel(
        sid_ref="env:MYIA_TEST_SMS_SID",
        token_ref="env:MYIA_TEST_SMS_TOKEN",
        from_ref="env:MYIA_TEST_SMS_FROM",
        client=client,
        **kwargs,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="sms", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYIA_TEST_SMS_SID", "AC1234567890abcdef")
    monkeypatch.setenv("MYIA_TEST_SMS_TOKEN", "tw-auth-token")
    monkeypatch.setenv("MYIA_TEST_SMS_FROM", "+15551234567")
    monkeypatch.setenv("MYIA_TEST_SMS_TO", "+8613800138000")


# ---------------------------------------------------------------------------
# one-shot POST 形态 + Basic 鉴权
# ---------------------------------------------------------------------------


class TestPublishShape:
    def test_posts_messages_endpoint_with_basic_auth(self, creds):
        """蓝本同款:{base}/{AccountSID}/Messages.json + Basic(SID:Token) + 三字段表单。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1234567890abcdef/Messages.json"
        expected = base64.b64encode(b"AC1234567890abcdef:tw-auth-token").decode("ascii")
        assert calls[0]["headers"]["authorization"] == f"Basic {expected}"
        assert calls[0]["headers"]["content-type"].startswith("application/x-www-form-urlencoded")
        form = parse_qs(calls[0]["body"])
        assert form["From"] == ["+15551234567"]
        assert form["To"] == ["+8613800138000"]
        assert "羊毛" in form["Body"][0] and "https://x/1" in form["Body"][0]

    def test_body_truncated_to_1600(self, creds):
        """超长正文截到 1600(蓝本 MAX_SMS_LENGTH 同款),不拆多条。"""
        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": "长" * 2000, "url": "https://x/1"}]

        _run(channel.send(items, CONTEXT))

        assert len(calls) == 1
        assert len(parse_qs(calls[0]["body"])["Body"][0]) == MESSAGE_LIMIT

    def test_template_render_becomes_body(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert parse_qs(calls[0]["body"])["Body"] == ["2026-10-03 共 1 条"]

    def test_template_render_failure_is_structured(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert calls == []

    def test_missing_credentials_is_env_var_missing(self, creds, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_SMS_TOKEN", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert "tw-auth-token" not in str(excinfo.value)

    def test_from_number_not_e164_is_rejected(self, creds, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_SMS_FROM", "15550000")
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_basic_auth_unit(self):
        assert build_basic_auth("AC1", "tok") == f"Basic {base64.b64encode(b'AC1:tok').decode()}"


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达与别名
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, creds):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("+8613900139000"))))

        assert parse_qs(calls[0]["body"])["To"] == ["+8613900139000"]

    def test_no_target_and_no_context_target_fails_fast(self, creds, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_SMS_TO", raising=False)
        calls: list[dict] = []
        channel = _channel(calls, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize("bad", ["13800138000", "+86 138 0013 8000", "sms", ""])
    def test_invalid_recipient_shape_is_rejected(self, creds, bad):
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"

    def test_direct_ref_parse(self):
        for ref in ["+8613800138000", "+15551234567", "+212612345678"]:
            target = SmsChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "sms",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["13800138000", "主人", "+86 13800138000", ""]:
            assert SmsChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "sms:+8613800138000", ChannelDirectory(tmp_path), platforms={"sms": SmsChannel}
        )
        assert (target.platform, target.chat_id) == ("sms", "+8613800138000")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("sms", "+8613800138000", "主人")
        directory.set_alias("sms", "+8613900139000", "羊毛群")

        exact = resolve_target("sms:主人", directory, platforms={"sms": SmsChannel})
        assert (exact.chat_id, exact.resolved_from) == ("+8613800138000", "directory_name")

        prefix = resolve_target("sms:羊毛", directory, platforms={"sms": SmsChannel})
        assert (prefix.chat_id, prefix.resolved_from) == ("+8613900139000", "directory_prefix")


# ---------------------------------------------------------------------------
# 错误分类(死信映射:403/404 硬失败,429/5xx/21211 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, creds):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(403, json={"code": 20003, "message": "Authenticate"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, creds):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(404, text="not found")
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, creds, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "sms_api_error"
        assert classify_dead_error(excinfo.value) is None

    def test_twilio_21211_keeps_vendor_code_and_stays_transient(self, creds):
        """400+code=21211(To 无效):原厂片段保留;与 core marker 表无交集 → 瞬态。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                400,
                json={"code": 21211, "message": "The 'To' number is not a valid phone number"},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "21211" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_error_body_still_carries_status(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(502, text="<bad gateway>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "502" in str(excinfo.value)

    def test_transport_error_reports_http_error(self, creds):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = SmsChannel(
            sid_ref="env:MYIA_TEST_SMS_SID",
            token_ref="env:MYIA_TEST_SMS_TOKEN",
            from_ref="env:MYIA_TEST_SMS_FROM",
            target="env:MYIA_TEST_SMS_TO",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# 目录语义(无自动发现)+ 协议契约
# ---------------------------------------------------------------------------


class TestDirectoryAndProtocol:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(SmsChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("sms", [], now=1.0)
        counts = _run(directory.refresh({"sms": SmsChannel()}, now=100.0))
        assert counts == {}

    def test_channel_protocol_conformance(self):
        channel = SmsChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "sms"
        assert channel.supports_targeting is True
