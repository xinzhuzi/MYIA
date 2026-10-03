"""Tests for 10-03-messaging-w3-longtail 组一 — msgraph_webhook 适配器。

覆盖:client credentials token 形态(form-encoded + scope=graph/.default)、
60s 余量缓存、chatMessage 端点(``/v1.0/chats/{id}/messages`` Bearer + body.content,
chat id 路径段 URL 编码)、201 即成功、``19:`` chat id 直达 + 定向/legacy 两路、
错误分类(401/403 → forbidden、404 → not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``gateway/platforms/msgraph_webhook.py`` 是 Graph change
notification **入站**接收器(send 为空实现;NousResearch/Hermes-Agent,MIT)
——MYIA 零入站,按官方 Graph chatMessage API 补出站(weixin 通道同款偏离
先例),此处测 MYIA 出站重写。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``.
All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from myia.push.base import PushSendError, SendContext
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.msgraph_webhook import MSGraphWebhookChannel
from myia.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
CHAT_ID = "19:meeting_ngRmZmVkZTltZGItYQ==@thread.v2"
PLATFORMS = {"msgraph_webhook": MSGraphWebhookChannel}


def _capture(request: httpx.Request) -> dict:
    body: Any
    raw = request.content.decode("utf-8")
    try:
        body = json.loads(raw)
    except ValueError:
        # form-encoded(token 端点)→ dict;其余非 JSON 形态原样保留。
        if request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            body = {
                key: values[0] for key, values in parse_qs(raw).items()
            }
        else:
            body = raw
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": body,
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    """默认双响应:login.microsoftonline.com = token;graph.microsoft.com = 消息。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        if str(request.url).startswith("https://login.microsoftonline.com/"):
            return httpx.Response(
                200, json={"access_token": "JWT-token-1", "expires_in": 3600}
            )
        return response or httpx.Response(
            201, json={"id": "1672377827202", "body": {"content": "ok"}}
        )

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> MSGraphWebhookChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_MSGRAPH_CHAT")
    kwargs.setdefault("tenant_ref", "env:MYIA_TEST_MSGRAPH_TENANT")
    kwargs.setdefault("client_id_ref", "env:MYIA_TEST_MSGRAPH_CLIENT_ID")
    kwargs.setdefault("client_secret_ref", "env:MYIA_TEST_MSGRAPH_SECRET")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return MSGraphWebhookChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(
        platform="msgraph_webhook", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def target_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_MSGRAPH_CHAT", CHAT_ID)
    monkeypatch.setenv("MYIA_TEST_MSGRAPH_TENANT", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("MYIA_TEST_MSGRAPH_CLIENT_ID", "app-client-id-1")
    monkeypatch.setenv("MYIA_TEST_MSGRAPH_SECRET", "sup-er-secret-1")


# ---------------------------------------------------------------------------
# token 生命周期 + chatMessage 形态
# ---------------------------------------------------------------------------


class TestTokenAndShape:
    def test_token_then_chat_message(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        # 1) client credentials:form-encoded + 租户段 URL 编码。
        token_call = calls[0]
        assert token_call["url"].startswith(
            "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555"
            "/oauth2/v2.0/token"
        )
        assert token_call["headers"]["content-type"] == "application/x-www-form-urlencoded"
        form = token_call["body"]
        assert form["grant_type"] == "client_credentials"
        assert form["client_id"] == "app-client-id-1"
        assert form["client_secret"] == "sup-er-secret-1"
        assert form["scope"] == "https://graph.microsoft.com/.default"
        # 2) chatMessage:Bearer + body.content + 19: chat id 路径段 URL 编码。
        send_call = calls[1]
        assert send_call["headers"]["authorization"] == "Bearer JWT-token-1"
        assert (
            send_call["url"]
            == "https://graph.microsoft.com/v1.0/chats/"
            "19%3Ameeting_ngRmZmVkZTltZGItYQ%3D%3D%40thread.v2/messages"
        )
        assert send_call["body"]["body"]["contentType"] == "text"
        assert "羊毛" in send_call["body"]["body"]["content"]
        assert "https://x/1" in send_call["body"]["body"]["content"]

    def test_token_cached_across_sends(self, target_env):
        """60s 余量缓存:第二次发送复用 token(零 token 端点往返)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))
        _run(channel.send([{"title": "t2"}], CONTEXT))

        token_calls = [c for c in calls if "login.microsoftonline.com" in c["url"]]
        send_calls = [c for c in calls if "graph.microsoft.com" in c["url"]]
        assert len(token_calls) == 1
        assert len(send_calls) == 2

    def test_token_error_carries_oauth_error(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://login.microsoftonline.com/"):
                return httpx.Response(
                    401,
                    json={"error": "invalid_client", "error_description": "AADSTS7000215"},
                )
            raise AssertionError("消息端点不应被触达")  # pragma: no cover

        channel = MSGraphWebhookChannel(
            target="env:MYIA_TEST_MSGRAPH_CHAT",
            tenant_ref="env:MYIA_TEST_MSGRAPH_TENANT",
            client_id_ref="env:MYIA_TEST_MSGRAPH_CLIENT_ID",
            client_secret_ref="env:MYIA_TEST_MSGRAPH_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "msgraph_webhook_api_error"
        assert "invalid_client" in str(excinfo.value)
        assert "AADSTS7000215" in str(excinfo.value)

    def test_token_response_without_token_is_invalid(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://login.microsoftonline.com/"):
                return httpx.Response(200, json={"expires_in": 3600})
            raise AssertionError("消息端点不应被触达")  # pragma: no cover

        channel = MSGraphWebhookChannel(
            target="env:MYIA_TEST_MSGRAPH_CHAT",
            tenant_ref="env:MYIA_TEST_MSGRAPH_TENANT",
            client_id_ref="env:MYIA_TEST_MSGRAPH_CLIENT_ID",
            client_secret_ref="env:MYIA_TEST_MSGRAPH_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_message_response_without_id_is_invalid(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(201, json={"body": {}}))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_missing_secret_env_is_env_var_missing(self, target_env, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_MSGRAPH_SECRET", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# 寻址:19: chat id 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, target_env):
        alt_chat = "19:8b0ba4aa-bcba-4c11-bb8f-a3fc01111@unq.gbl.spaces"
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(alt_chat))))

        assert alt_chat.replace(":", "%3A").replace("@", "%40") in calls[1]["url"]

    def test_no_target_and_no_context_target_fails_fast(self, target_env):
        """target 未配置(定向 targets 最小配置)且无 context.target → missing_target。"""
        calls: list[dict] = []
        channel = MSGraphWebhookChannel(
            tenant_ref="env:MYIA_TEST_MSGRAPH_TENANT",
            client_id_ref="env:MYIA_TEST_MSGRAPH_CLIENT_ID",
            client_secret_ref="env:MYIA_TEST_MSGRAPH_SECRET",
            client=httpx.AsyncClient(transport=_mock(calls)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize("bad", ["羊毛群", "19:short", "chat-id-plain", "C0123ABCDEF"])
    def test_malformed_chat_id_rejected(self, target_env, bad):
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        for ref in [CHAT_ID, "19:8b0ba4aa-bcba-4c11-bb8f-a3fc01111@unq.gbl.spaces"]:
            target = MSGraphWebhookChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "msgraph_webhook",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛群", "19:", ""]:
            assert MSGraphWebhookChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            f"msgraph_webhook:{CHAT_ID}", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert (target.platform, target.chat_id) == ("msgraph_webhook", CHAT_ID)

    def test_resolve_via_directory_name(self, tmp_path):
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "msgraph_webhook",
            [ChannelEntry(platform="msgraph_webhook", chat_id=CHAT_ID, name="主人")],
            now=1.0,
        )
        by_name = resolve_target("msgraph_webhook:主人", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (CHAT_ID, RESOLVED_DIRECTORY_NAME)

    def test_template_render(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[1]["body"]["body"]["content"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(401/403 → forbidden、404 → not_found、429/5xx → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def _channel_with_message_response(self, calls, response):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_capture(request))
            if str(request.url).startswith("https://login.microsoftonline.com/"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return response

        return MSGraphWebhookChannel(
            target="env:MYIA_TEST_MSGRAPH_CHAT",
            tenant_ref="env:MYIA_TEST_MSGRAPH_TENANT",
            client_id_ref="env:MYIA_TEST_MSGRAPH_CLIENT_ID",
            client_secret_ref="env:MYIA_TEST_MSGRAPH_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    def test_403_carries_graph_error_code(self, target_env):
        calls: list[dict] = []
        channel = self._channel_with_message_response(
            calls,
            httpx.Response(
                403,
                json={
                    "error": {
                        "code": "Authorization_RequestDenied",
                        "message": "Principal does not have access",
                    }
                },
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert "Authorization_RequestDenied" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, target_env):
        calls: list[dict] = []
        channel = self._channel_with_message_response(
            calls,
            httpx.Response(
                404, json={"error": {"code": "ItemNotFound", "message": "The chat was not found"}}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "ItemNotFound" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, target_env, status):
        calls: list[dict] = []
        channel = self._channel_with_message_response(calls, httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = MSGraphWebhookChannel(
            target="env:MYIA_TEST_MSGRAPH_CHAT",
            tenant_ref="env:MYIA_TEST_MSGRAPH_TENANT",
            client_id_ref="env:MYIA_TEST_MSGRAPH_CLIENT_ID",
            client_secret_ref="env:MYIA_TEST_MSGRAPH_SECRET",
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
            _run(MSGraphWebhookChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("msgraph_webhook", [], now=1.0)

        counts = _run(
            directory.refresh({"msgraph_webhook": MSGraphWebhookChannel()}, now=100.0)
        )

        assert counts == {}
