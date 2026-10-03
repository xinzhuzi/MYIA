"""Tests for 10-03-messaging-w3-longtail 组一 — discord 适配器。

覆盖:REST 形态(Bot 头 + /channels/{id}/messages + content)、2000 硬拆、
thread_id 话题改投、定向/legacy 两路寻址与雪花 id 直达、错误分类(403 →
forbidden、404 Unknown Channel → not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``plugins/platforms/discord/adapter.py`` 的
``_standalone_send``(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

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
from shishi.push.discord import MESSAGE_LIMIT, DiscordChannel
from shishi.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
CHANNEL_ID = "123456789012345678"
PLATFORMS = {"discord": DiscordChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"id": "998877", "content": "x"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> DiscordChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_DISCORD_CHANNEL")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return DiscordChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str, thread_id: str | None = None) -> ChannelTarget:
    return ChannelTarget(
        platform="discord", chat_id=chat_id, thread_id=thread_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def channel_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_DISCORD_CHANNEL", CHANNEL_ID)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "MTIz-bot-token")


# ---------------------------------------------------------------------------
# REST 形态 + 2000 硬拆
# ---------------------------------------------------------------------------


class TestPostShape:
    def test_post_message_shape(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="MTIz-direct-token")

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
        assert calls[0]["headers"]["authorization"] == "Bot MTIz-direct-token"
        assert "羊毛" in calls[0]["body"]["content"] and "https://x/1" in calls[0]["body"]["content"]

    def test_long_content_hard_splits_at_2000(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="MTIz-t")

        _run(channel.send([{"title": "长" * 1500}, {"title": "另" * 1500}], CONTEXT))

        assert len(calls) == 2
        assert all(len(call["body"]["content"]) <= MESSAGE_LIMIT for call in calls)

    def test_response_without_id_is_invalid(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, json={"content": "no id"}))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_default_token_ref_resolved_at_send_time(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["headers"]["authorization"] == "Bot MTIz-bot-token"

    def test_missing_token_env_is_env_var_missing(self, channel_env, monkeypatch):
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# 寻址:定向优先 / thread 话题 / 直达 + 目录
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="MTIz-t")

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("987654321098765432"))))

        assert calls[0]["url"].endswith("/channels/987654321098765432/messages")

    def test_thread_id_reroutes_to_thread_endpoint(self, channel_env):
        """thread_id 在场 → 改投话题端点(蓝本 thread_id 分支同形态)。"""
        calls: list[dict] = []
        channel = _channel(calls, token="MTIz-t")

        _run(
            channel.send(
                [{"title": "t"}],
                replace(CONTEXT, target=_target(CHANNEL_ID, thread_id="111222333444555666")),
            )
        )

        assert calls[0]["url"].endswith("/channels/111222333444555666/messages")

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_DISCORD_CHANNEL", raising=False)
        calls: list[dict] = []
        channel = DiscordChannel(client=httpx.AsyncClient(transport=_mock(calls)), token="t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_malformed_channel_id_rejected(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("general"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        for ref in ["123456789012345678", "998877665544332211"]:
            target = DiscordChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "discord",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛频道", "123", "123456789012345678901", "c0123"]:
            assert DiscordChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            "discord:123456789012345678", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert (target.platform, target.chat_id) == ("discord", "123456789012345678")

    def test_resolve_via_directory_name(self, tmp_path):
        from shishi.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "discord",
            [ChannelEntry(platform="discord", chat_id="123456789012345678", name="羊毛频道")],
            now=1.0,
        )
        by_name = resolve_target("discord:羊毛频道", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (
            "123456789012345678",
            RESOLVED_DIRECTORY_NAME,
        )

    def test_template_render(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t", template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["content"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t", template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(401/403 → forbidden、404 → not_found、429/5xx → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, channel_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(403, json={"message": "Missing Permissions", "code": 50013})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_unknown_channel_classifies_not_found(self, channel_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(404, json={"message": "Unknown Channel", "code": 10003})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "Unknown Channel" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, channel_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, channel_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = DiscordChannel(
            target="env:MYIA_TEST_DISCORD_CHANNEL",
            token="t",
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
            _run(DiscordChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("discord", [], now=1.0)

        counts = _run(directory.refresh({"discord": DiscordChannel()}, now=100.0))

        assert counts == {}
