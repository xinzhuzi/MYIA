"""Tests for 10-03-messaging-w3-longtail 组一 — whatsapp_cloud 适配器。

覆盖:Graph messages 形态(Bearer + phone_number_id 路径段 + 官方 envelope)、
4096 分段、wamid(messages[])校验、E.164 直达 + 定向/legacy 两路、graph
error 形态(code+HTTP status)进文案与死信分类。

蓝本对照:Hermes ``gateway/platforms/whatsapp_cloud.py`` 的 ``send`` /
``_outbound_payload`` / ``_response_error``(NousResearch/Hermes-Agent,MIT)
——此处测 MYIA 重写。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``.
All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from myia.push.base import PushSendError, SendContext
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)
from myia.push.whatsapp_cloud import MESSAGE_LIMIT, WhatsAppCloudChannel

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
PLATFORMS = {"whatsapp_cloud": WhatsAppCloudChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.HBg…"}]}
    )


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or _ok_response()

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> WhatsAppCloudChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_WHATSAPP_TO")
    kwargs.setdefault("phone_number_id", "11987654321")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return WhatsAppCloudChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(
        platform="whatsapp_cloud", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def target_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_WHATSAPP_TO", "+8613800138000")
    monkeypatch.setenv("WHATSAPP_CLOUD_TOKEN", "EAAG-default-token")
    monkeypatch.setenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "11987654321")


# ---------------------------------------------------------------------------
# Graph messages 形态
# ---------------------------------------------------------------------------


class TestPostShape:
    def test_post_message_shape(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-direct-token")

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert (
            calls[0]["url"]
            == "https://graph.facebook.com/v20.0/11987654321/messages"
        )
        assert calls[0]["headers"]["authorization"] == "Bearer EAAG-direct-token"
        body = calls[0]["body"]
        assert body["messaging_product"] == "whatsapp"
        assert body["recipient_type"] == "individual"
        assert body["to"] == "+8613800138000"
        assert body["type"] == "text"
        assert body["text"]["preview_url"] is True
        assert "羊毛" in body["text"]["body"]

    def test_api_version_overridable(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t", api_version="v23.0")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["url"].startswith("https://graph.facebook.com/v23.0/")

    def test_long_body_splits_at_4096(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t")

        _run(channel.send([{"title": "长" * 2500}, {"title": "另" * 2500}], CONTEXT))

        assert len(calls) == 2
        assert all(len(call["body"]["text"]["body"]) <= MESSAGE_LIMIT for call in calls)

    def test_response_without_messages_is_invalid(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, json={"messaging_product": "whatsapp"}))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_missing_token_env_is_env_var_missing(self, target_env, monkeypatch):
        monkeypatch.delenv("WHATSAPP_CLOUD_TOKEN", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []

    def test_missing_phone_id_env_is_env_var_missing(self, target_env, monkeypatch):
        monkeypatch.delenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID", raising=False)
        calls: list[dict] = []
        channel = WhatsAppCloudChannel(
            target="env:MYIA_TEST_WHATSAPP_TO",
            token="EAAG-t",
            client=httpx.AsyncClient(transport=_mock(calls)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# 寻址:E.164 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t")

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("8613900139000"))))

        assert calls[0]["body"]["to"] == "8613900139000"

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_WHATSAPP_TO", raising=False)
        calls: list[dict] = []
        channel = WhatsAppCloudChannel(
            token="EAAG-t",
            phone_number_id="11987654321",
            client=httpx.AsyncClient(transport=_mock(calls)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_malformed_recipient_rejected(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("羊毛群"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        for ref in ["+8613800138000", "8613800138000", "447700900123"]:
            target = WhatsAppCloudChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "whatsapp_cloud",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛群", "0138000", "tel:+8613800138000", ""]:
            assert WhatsAppCloudChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            "whatsapp_cloud:+8613800138000", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert (target.platform, target.chat_id) == ("whatsapp_cloud", "+8613800138000")

    def test_resolve_via_directory_name(self, tmp_path):
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "whatsapp_cloud",
            [ChannelEntry(platform="whatsapp_cloud", chat_id="+8613800138000", name="主人")],
            now=1.0,
        )
        by_name = resolve_target("whatsapp_cloud:主人", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (
            "+8613800138000",
            RESOLVED_DIRECTORY_NAME,
        )

    def test_template_render(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t", template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["text"]["body"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="EAAG-t", template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(graph error 形态进文案;403/404 硬失败,429/5xx/超时瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_graph_error_carries_code_and_status(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "(#131030) Recipient phone number not in allowed list",
                        "type": "OAuthException",
                        "code": 131030,
                    }
                },
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "whatsapp_cloud_api_error"
        assert "graph error 131030 (HTTP 400)" in str(excinfo.value)
        assert "Recipient phone number" in str(excinfo.value)

    def test_403_classifies_forbidden(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                403, json={"error": {"message": "(#200) Permissions error", "code": 200}}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_non_json_error_body_still_api_error(self, target_env):
        """非 JSON 错误体(网关 HTML):HTTP 状态先报 whatsapp_cloud_api_error。"""
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text="<html>blocked</html>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "whatsapp_cloud_api_error"
        assert classify_dead_error(excinfo.value) == "forbidden"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, target_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid_response(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, text="not-json"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_transport_error_reports_http_error(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = WhatsAppCloudChannel(
            target="env:MYIA_TEST_WHATSAPP_TO",
            token="EAAG-t",
            phone_number_id="11987654321",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# 目录语义(prd R4:无自动发现,不是失败)
# ---------------------------------------------------------------------------


class TestDirectorySemantics:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(WhatsAppCloudChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("whatsapp_cloud", [], now=1.0)

        counts = _run(directory.refresh({"whatsapp_cloud": WhatsAppCloudChannel()}, now=100.0))

        assert counts == {}
