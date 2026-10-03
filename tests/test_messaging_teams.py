"""Tests for 10-03-messaging-w3-longtail 组一 — teams 适配器(Workflows webhook)。

覆盖:Adaptive Card 载荷形态(``type: message`` + attachments)、**200 + 字面
``1`` 应答即成功**(Workflows 触发器特有,非 JSON)、4000 拆卡、完整 webhook
URL 直达(``<tenant>.webhook.office.com/webhookb2/`` host 锚定)+ 定向/legacy
两路、错误分类(403 → forbidden、404 → not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``plugins/platforms/teams/adapter.py``(Bot Framework 形态;
MYIA 偏离注记取 Workflows incoming webhook——O365 connector 已退役)——
此处测 MYIA 重写。

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
from shishi.push.teams import MESSAGE_LIMIT, TeamsChannel, build_card
from shishi.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
WEBHOOK_URL = (
    "https://mycompany.webhook.office.com/webhookb2"
    "/b1c0f33d-1111-2222-3333-444455556666@abcd/IncomingWebhook/"
    "ef0123456789abcdef0123456789abcdef/abcd-v1-abcd"
)
PLATFORMS = {"teams": TeamsChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        # Workflows 触发器成功应答:200 + 字面 "1"(纯文本,非 JSON)。
        return response or httpx.Response(200, text="1")

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> TeamsChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_TEAMS_WEBHOOK")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return TeamsChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="teams", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def webhook_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_TEAMS_WEBHOOK", WEBHOOK_URL)


# ---------------------------------------------------------------------------
# Adaptive Card 载荷 + 「1」应答
# ---------------------------------------------------------------------------


class TestCardShape:
    def test_post_adaptive_card_shape(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == WEBHOOK_URL
        body = calls[0]["body"]
        assert body["type"] == "message"
        (attachment,) = body["attachments"]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        card = attachment["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        (block,) = card["body"]
        assert block["type"] == "TextBlock" and block["wrap"] is True
        assert "羊毛" in block["text"] and "https://x/1" in block["text"]

    def test_literal_one_response_is_success(self, webhook_env):
        """Workflows 成功应答是字面 ``1``(纯文本):2xx 即成功,不解析 JSON。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))  # 不抛即通过

        assert len(calls) == 1

    def test_build_card_unit(self):
        card = build_card("你好\n世界")
        assert card["attachments"][0]["content"]["body"][0]["text"] == "你好\n世界"

    def test_long_message_splits_into_cards(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "长" * 2500}, {"title": "另" * 2500}], CONTEXT))

        assert len(calls) == 2
        for call in calls:
            text = call["body"]["attachments"][0]["content"]["body"][0]["text"]
            assert len(text) <= MESSAGE_LIMIT

    def test_webhook_url_never_in_error_messages(self, webhook_env):
        """凭据纪律:错误文案不带解析后的 webhook URL(URL 内嵌路径凭据)。"""
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(404, text="Flow not found"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "webhookb2" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 寻址:完整 webhook URL 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, webhook_env):
        alt_url = (
            "https://otherco.webhook.office.com/webhookb2"
            "/aaaa-bbbb@t/IncomingWebhook/xyz/uuid2"
        )
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(alt_url))))

        assert calls[0]["url"] == alt_url

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_TEAMS_WEBHOOK", raising=False)
        calls: list[dict] = []
        channel = TeamsChannel(client=httpx.AsyncClient(transport=_mock(calls)))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize(
        "bad",
        [
            # 旧 O365 connector 端点(已退役,非 webhookb2):host 锚定拒绝。
            "https://outlook.office.com/webhook/abc/IncomingWebhook/xyz/uuid",
            "https://evil.example.com/webhookb2/abc",
            "https://mycompany.webhook.office.com/api/messages",  # 非 webhookb2 路径
            "羊毛频道",
        ],
    )
    def test_malformed_webhook_url_rejected(self, webhook_env, bad):
        """复核 C1 回归:形态错误不回显解析值(webhookb2/<guid> 路径段与
        query 本身是凭据)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert bad not in str(excinfo.value) and bad[:40] not in str(excinfo.value)
        assert calls == []

    def test_direct_ref_parse(self):
        target = TeamsChannel.parse_direct_ref(WEBHOOK_URL)
        assert target is not None
        assert (target.platform, target.chat_id, target.resolved_from) == (
            "teams",
            WEBHOOK_URL,
            RESOLVED_DIRECT,
        )
        assert TeamsChannel.parse_direct_ref("羊毛频道") is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            f"teams:{WEBHOOK_URL}", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert target.resolved_from == RESOLVED_DIRECT

    def test_resolve_via_directory_name(self, tmp_path):
        from shishi.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "teams",
            [ChannelEntry(platform="teams", chat_id=WEBHOOK_URL, name="羊毛频道")],
            now=1.0,
        )
        by_name = resolve_target("teams:羊毛频道", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (WEBHOOK_URL, RESOLVED_DIRECTORY_NAME)

    def test_template_render(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        text = calls[0]["body"]["attachments"][0]["content"]["body"][0]["text"]
        assert text == "2026-10-03 共 1 条"

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
    def test_404_classifies_not_found(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(404, text="Flow not found"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    def test_403_classifies_forbidden(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text="Forbidden"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "forbidden"

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_transient_statuses_do_not_mark_dead(self, webhook_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, webhook_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = TeamsChannel(
            target="env:MYIA_TEST_TEAMS_WEBHOOK",
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
            _run(TeamsChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("teams", [], now=1.0)

        counts = _run(directory.refresh({"teams": TeamsChannel()}, now=100.0))

        assert counts == {}
