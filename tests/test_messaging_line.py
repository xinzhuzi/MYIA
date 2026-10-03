"""Tests for 10-03-messaging-w3-longtail 组一 — line 适配器。

覆盖:push 形态(Bearer + to + ≤5 泡)、**200 空应答即成功**(LINE 特有)、
4500 保守切泡与 5 泡上限丢弃、U/C/R 直达 + 定向/legacy 两路、错误分类
(403 → forbidden、404 → not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``plugins/platforms/line/adapter.py`` 的 ``_LineClient.push``
/``_text_message``/``split_for_line``(NousResearch/Hermes-Agent,MIT)——此处
测 MYIA 重写。

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
from myia.push.line import LINE_MAX_BUBBLES, MESSAGE_LIMIT, LineChannel, split_bubbles
from myia.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
USER_ID = "U1234567890abcdef1234567890abcdef"
PLATFORMS = {"line": LineChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        # LINE push 成功应答:200 + 空 body(官方形态,非 JSON)。
        return response or httpx.Response(200, text="")

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> LineChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_LINE_TO")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return LineChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="line", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def target_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_LINE_TO", USER_ID)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "default-line-token")


# ---------------------------------------------------------------------------
# push 形态 + 200 空应答
# ---------------------------------------------------------------------------


class TestPushShape:
    def test_push_shape_with_empty_200_body(self, target_env):
        """push 成功应答是 200 + 空 body:2xx 即成功,不做 JSON 解析。"""
        calls: list[dict] = []
        channel = _channel(calls, token="direct-line-token")

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "https://api.line.me/v2/bot/message/push"
        assert calls[0]["headers"]["authorization"] == "Bearer direct-line-token"
        body = calls[0]["body"]
        assert body["to"] == USER_ID
        assert all(m["type"] == "text" for m in body["messages"])
        assert "羊毛" in body["messages"][0]["text"] and "https://x/1" in body["messages"][0]["text"]

    def test_long_message_splits_into_bubbles(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t")

        _run(channel.send([{"title": "行" * 3000}, {"title": "另" * 3000}], CONTEXT))

        messages = calls[0]["body"]["messages"]
        assert len(messages) == 2
        assert all(len(m["text"]) <= MESSAGE_LIMIT for m in messages)

    def test_more_than_five_bubbles_truncated(self, target_env):
        """超 5 泡:截断到 5(官方 push 单次上限,尾巴丢弃显式告警)。"""
        calls: list[dict] = []
        channel = _channel(calls, token="t")

        _run(channel.send([{"title": str(i) + "行" * 3000} for i in range(8)], CONTEXT))

        assert len(calls[0]["body"]["messages"]) == LINE_MAX_BUBBLES

    def test_split_bubbles_unit(self):
        assert split_bubbles("短文") == ["短文"]
        assert split_bubbles("") == []
        chunks = split_bubbles("行" * (MESSAGE_LIMIT * 7))
        assert len(chunks) == LINE_MAX_BUBBLES

    def test_default_token_ref_resolved_at_send_time(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["headers"]["authorization"] == "Bearer default-line-token"

    def test_missing_token_env_is_env_var_missing(self, target_env, monkeypatch):
        monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# 寻址:U/C/R 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t")
        group_id = "C1234567890abcdef1234567890abcdef"

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(group_id))))

        assert calls[0]["body"]["to"] == group_id

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_LINE_TO", raising=False)
        calls: list[dict] = []
        channel = LineChannel(client=httpx.AsyncClient(transport=_mock(calls)), token="t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_malformed_recipient_rejected(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("羊毛群"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        room_id = "R1234567890abcdef1234567890abcdef"
        for ref in [USER_ID, "C1234567890abcdef1234567890abcdef", room_id]:
            target = LineChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "line",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛群", "u1234567890abcdef1234567890abcdef", "U123", ""]:
            assert LineChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(f"line:{USER_ID}", ChannelDirectory(tmp_path), platforms=PLATFORMS)
        assert (target.platform, target.chat_id) == ("line", USER_ID)

    def test_resolve_via_directory_name(self, tmp_path):
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "line",
            [ChannelEntry(platform="line", chat_id=USER_ID, name="主人")],
            now=1.0,
        )
        by_name = resolve_target("line:主人", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (USER_ID, RESOLVED_DIRECTORY_NAME)

    def test_template_render(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t", template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["messages"][0]["text"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, token="t", template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(LINE 错误体 {"message": …} 形态;403/404 硬失败,429/5xx 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_400_carries_vendor_message(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                400, json={"message": "The request body could not be parsed as JSON"}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "line_api_error"
        assert "HTTP 400" in str(excinfo.value)
        assert "The request body could not be parsed as JSON" in str(excinfo.value)

    def test_403_classifies_forbidden(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(403, json={"message": "Access denied"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(404, json={"message": "Not found"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, target_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = LineChannel(
            target="env:MYIA_TEST_LINE_TO",
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
            _run(LineChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("line", [], now=1.0)

        counts = _run(directory.refresh({"line": LineChannel()}, now=100.0))

        assert counts == {}
