"""Tests for 10-03-messaging-w3-longtail 组一 — slack 适配器。

覆盖:chat.postMessage 形态(Bearer + JSON + ok 判据)、U/W 用户先
conversations.open 换 DM(蓝本 #17444)、39000 分段、定向/legacy 两路寻址
与直达解析、错误分类(HTTP 403 → forbidden、404 → not_found、429/5xx →
瞬态、ok=false 原厂 error 进文案)。

蓝本对照:Hermes ``plugins/platforms/slack/adapter.py`` 的
``_standalone_send``/``_slack_json_post``/``_resolve_slack_user_dm``
(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``
(同 test_push.py 约定)。All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.slack import MESSAGE_LIMIT, SlackChannel
from myia.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)
from myia.push.base import SendContext

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

PLATFORMS = {"slack": SlackChannel}  # 注册表钉死前的注入形态(集成后随 PLATFORMS)


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"ok": True, "ts": "1234.5678"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> SlackChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_SLACK_CHANNEL")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return SlackChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="slack", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def channel_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_SLACK_CHANNEL", "C0123ABCDEF")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fixture-token")


# ---------------------------------------------------------------------------
# chat.postMessage 形态 + ok 判据
# ---------------------------------------------------------------------------


class TestPostShape:
    def test_post_message_shape(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="xoxb-test-token")

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "https://slack.com/api/chat.postMessage"
        assert calls[0]["headers"]["authorization"] == "Bearer xoxb-test-token"
        assert calls[0]["body"]["channel"] == "C0123ABCDEF"
        assert "羊毛" in calls[0]["body"]["text"] and "https://x/1" in calls[0]["body"]["text"]

    def test_ok_false_is_structured_api_error(self, channel_env):
        """Slack API 错误恒为 HTTP 200 + ok=false:原厂 error 进文案。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(200, json={"ok": False, "error": "channel_not_found"}),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "slack_api_error"
        assert "error=channel_not_found" in str(excinfo.value)

    def test_long_message_splits_at_limit(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="xoxb-t")
        items = [
            {"title": "行" * 20000, "url": "https://x/1"},
            {"title": "另" * 20000},
        ]

        _run(channel.send(items, CONTEXT))

        assert len(calls) == 2
        assert all(len(call["body"]["text"]) <= MESSAGE_LIMIT for call in calls)

    def test_default_token_ref_resolved_at_send_time(self, channel_env, monkeypatch):
        """缺省 token 走 env:SLACK_BOT_TOKEN 引用(发送期才解析)。"""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-default-ref-token")
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["headers"]["authorization"] == "Bearer xoxb-default-ref-token"

    def test_missing_token_env_is_env_var_missing(self, channel_env, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# U/W 用户直达:conversations.open 先行(蓝本 #17444)
# ---------------------------------------------------------------------------


class TestUserDirectDM:
    def test_user_id_resolved_via_conversations_open(self, channel_env):
        """U/W 裸用户 id 不可直发:先 conversations.open 换 D… 再 postMessage。"""
        calls: list[dict] = []
        responses = [
            httpx.Response(200, json={"ok": True, "channel": {"id": "D0123DMID"}}),
            httpx.Response(200, json={"ok": True, "ts": "1.2"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_capture(request))
            return responses[len(calls) - 1]

        channel = SlackChannel(
            target="env:MYIA_TEST_SLACK_CHANNEL",
            token="xoxb-t",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("U0456USERID"))))

        assert calls[0]["url"] == "https://slack.com/api/conversations.open"
        assert calls[0]["body"] == {"users": "U0456USERID"}
        assert calls[1]["url"] == "https://slack.com/api/chat.postMessage"
        assert calls[1]["body"]["channel"] == "D0123DMID"

    def test_conversations_open_failure_surfaces_vendor_error(self, channel_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(200, json={"ok": False, "error": "user_not_found"}),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("U0456USERID"))))
        assert excinfo.value.code == "slack_api_error"
        assert "error=user_not_found" in str(excinfo.value)
        assert len(calls) == 1  # postMessage 未发出


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达与目录
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="xoxb-t")

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("G089GROUP1"))))

        assert calls[0]["body"]["channel"] == "G089GROUP1"

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_SLACK_CHANNEL", raising=False)
        calls: list[dict] = []
        channel = SlackChannel(client=httpx.AsyncClient(transport=_mock(calls)), token="xoxb-t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_direct_ref_parse(self):
        for ref in ["C0123ABCDEF", "G0123456AB", "D0123456AB", "U0456USERID", "W0123ENTUSER"]:
            target = SlackChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "slack",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛群", "c0123abcdef", "#general", "", "X0123ABCDEF"]:
            assert SlackChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target("slack:C0123ABCDEF", ChannelDirectory(tmp_path), platforms=PLATFORMS)

        assert (target.platform, target.chat_id) == ("slack", "C0123ABCDEF")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_resolve_via_alias_name_and_prefix(self, tmp_path):
        """目录精确名 + 唯一前缀两路径(注入注册表;集成后随 PLATFORMS)。"""
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "slack",
            [
                ChannelEntry(platform="slack", chat_id="C0AAA999999", name="羊毛群"),
                ChannelEntry(platform="slack", chat_id="C0BBB888888", name="节点群"),
            ],
            now=1.0,
        )
        by_name = resolve_target("slack:羊毛群", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == ("C0AAA999999", RESOLVED_DIRECTORY_NAME)
        by_prefix = resolve_target("slack:节点", directory, platforms=PLATFORMS)
        assert by_prefix.chat_id == "C0BBB888888"

    def test_template_render(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="xoxb-t", template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["text"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, token="xoxb-t", template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(prd:HTTP 403/404 硬失败,429/5xx/超时瞬态;ok=false 原样透传)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text=" Forbidden"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        # 非 JSON 的 403 body:HTTP 状态先于 JSON 解析 → slack_api_error(非
        # invalid_response),死信分类照常命中 forbidden。
        assert excinfo.value.code == "slack_api_error"
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_429_classifies_transient(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(429, text="Too many requests"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_ok_false_not_found_like_error_is_transient(self, channel_env):
        """ok=false + channel_not_found 无 HTTP 404:不判死信(诚实文案面)。

        Slack 的 chat 级不存在以 200+error 形态返回,死信分类器按 HTTP 状态
        命中;该错误如实透传、分类为瞬态(下轮重试)。
        """
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid_response(self, channel_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, text="<html>not json</html>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_transport_error_reports_http_error(self, channel_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = SlackChannel(
            target="env:MYIA_TEST_SLACK_CHANNEL",
            token="xoxb-t",
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
            _run(SlackChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("slack", [], now=1.0)

        counts = _run(directory.refresh({"slack": SlackChannel()}, now=100.0))

        assert counts == {}
