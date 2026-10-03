"""Tests for 10-03-messaging-w3-longtail 组一 — google_chat 适配器(webhook 形态)。

覆盖:incoming webhook one-shot POST(``{"text": …}``、凭据在 URL query)、
4000 截断分段、完整 URL 直达(host 锚定)+ 定向/legacy 两路、错误分类
(403 → forbidden、404 → not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``plugins/platforms/google_chat/adapter.py`` 的
``_standalone_send``(SA/Graph 形态;MYIA 偏离注记取 webhook 形态,
端点同源 chat.googleapis.com)(NousResearch/Hermes-Agent,MIT)——此处测
MYIA 重写。

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

from shishi.push.base import PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.google_chat import MESSAGE_LIMIT, GoogleChatChannel
from shishi.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
WEBHOOK_URL = (
    "https://chat.googleapis.com/v1/spaces/AAAA1234/messages"
    "?key=AIzaSyABCDEF&token=abc-token-xyz"
)
PLATFORMS = {"google_chat": GoogleChatChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(
            200, json={"name": "spaces/AAAA1234/messages/xyz", "text": "ok"}
        )

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> GoogleChatChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_GCHAT_WEBHOOK")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return GoogleChatChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(
        platform="google_chat", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def webhook_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_GCHAT_WEBHOOK", WEBHOOK_URL)


# ---------------------------------------------------------------------------
# one-shot POST 形态
# ---------------------------------------------------------------------------


class TestPostShape:
    def test_post_text_to_webhook(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == WEBHOOK_URL
        assert calls[0]["body"].keys() == {"text"}
        assert "羊毛" in calls[0]["body"]["text"] and "https://x/1" in calls[0]["body"]["text"]
        # 凭据在 URL query 内:无 Authorization 头。
        assert "authorization" not in calls[0]["headers"]

    def test_long_message_splits_at_4000(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "长" * 2500}, {"title": "另" * 2500}], CONTEXT))

        assert len(calls) == 2
        assert all(len(call["body"]["text"]) <= MESSAGE_LIMIT for call in calls)

    def test_webhook_url_never_in_error_messages(self, webhook_env):
        """凭据纪律:错误文案只带引用名,绝不含解析后的 URL(key/token 在内)。"""
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text="PERMISSION_DENIED"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "AIzaSyABCDEF" not in str(excinfo.value)
        assert "abc-token-xyz" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 寻址:完整 webhook URL 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, webhook_env):
        alt_url = (
            "https://chat.googleapis.com/v1/spaces/BBBB5678/messages"
            "?key=AIzaSyOTHER&token=tok2"
        )
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(alt_url))))

        assert calls[0]["url"] == alt_url

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_GCHAT_WEBHOOK", raising=False)
        calls: list[dict] = []
        channel = GoogleChatChannel(client=httpx.AsyncClient(transport=_mock(calls)))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize(
        "bad",
        [
            "https://evil.example.com/v1/spaces/AAAA/messages?key=k&token=t",
            "https://chat.googleapis.com/v1/spaces/AAAA/messages",  # 缺 key/token query
            "chat.googleapis.com/v1/spaces/AAAA/messages?key=k&token=t",  # 缺 scheme
            "羊毛群",
        ],
    )
    def test_malformed_webhook_url_rejected(self, webhook_env, bad):
        """复核 C1 回归:形态错误不回显解析值(URL 的 key/token query 属凭据,
        任何前缀片段都可能探进 key 值)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert bad not in str(excinfo.value) and bad[:40] not in str(excinfo.value)
        assert "key=k" not in str(excinfo.value) and "token=t" not in str(excinfo.value)
        assert calls == []

    def test_direct_ref_parse(self):
        target = GoogleChatChannel.parse_direct_ref(WEBHOOK_URL)
        assert target is not None
        assert (target.platform, target.chat_id, target.resolved_from) == (
            "google_chat",
            WEBHOOK_URL,
            RESOLVED_DIRECT,
        )
        assert GoogleChatChannel.parse_direct_ref("羊毛空间") is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            f"google_chat:{WEBHOOK_URL}", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert target.resolved_from == RESOLVED_DIRECT

    def test_resolve_via_directory_name(self, tmp_path):
        """别名登记:人类名 → chat_id(完整 webhook URL)。"""
        from shishi.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "google_chat",
            [ChannelEntry(platform="google_chat", chat_id=WEBHOOK_URL, name="羊毛空间")],
            now=1.0,
        )
        by_name = resolve_target("google_chat:羊毛空间", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (WEBHOOK_URL, RESOLVED_DIRECTORY_NAME)

    def test_template_render(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["text"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(403 → forbidden、404 → not_found、429/5xx → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED"}}),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(404, json={"error": {"code": 404}}))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, webhook_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, webhook_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = GoogleChatChannel(
            target="env:MYIA_TEST_GCHAT_WEBHOOK",
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
            _run(GoogleChatChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("google_chat", [], now=1.0)

        counts = _run(directory.refresh({"google_chat": GoogleChatChannel()}, now=100.0))

        assert counts == {}
