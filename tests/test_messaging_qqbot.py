"""Tests for 10-03-messaging-w3-longtail 组一 — qqbot 适配器。

覆盖:getAppAccessToken 生命周期(QQBot 头 + 60s 余量缓存)、主动消息形态
(**无 msg_id**——被动回复需入站 msg_id,MYIA 零入站;官方事实探查定案)、
msg_seq 在场、三寻址形态(c2c:/group:/guild:)端点与 body 差异(guild 无
msg_seq)、直达 + 定向/legacy 两路、错误分类(403 → forbidden、404 →
not_found、429/5xx → 瞬态)。

蓝本对照:Hermes ``gateway/platforms/qqbot/``(``adapter.py`` 的
``_ensure_token``/``_api_request``/``_build_text_body``,``constants.py``)
(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

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
from myia.push.qqbot import QQBotChannel, next_msg_seq
from myia.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_NAME,
    ChannelTarget,
    resolve_target,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
PLATFORMS = {"qqbot": QQBotChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    """默认双响应:第一调 = token 端点(200 token),其余 = 消息端点(200 空)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        if str(request.url).startswith("https://bots.qq.com/"):
            return httpx.Response(
                200, json={"access_token": "QQ-token-1", "expires_in": 7200}
            )
        return response or httpx.Response(200, json={"id": "MSGID", "timestamp": "0"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> QQBotChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_QQBOT_TARGET")
    kwargs.setdefault("appid_ref", "env:MYIA_TEST_QQBOT_APPID")
    kwargs.setdefault("secret_ref", "env:MYIA_TEST_QQBOT_SECRET")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return QQBotChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="qqbot", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def target_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_QQBOT_TARGET", "group:ABCDEF123456")
    monkeypatch.setenv("MYIA_TEST_QQBOT_APPID", "102345678")
    monkeypatch.setenv("MYIA_TEST_QQBOT_SECRET", "s3cret-qq")


# ---------------------------------------------------------------------------
# token 生命周期 + 主动消息形态
# ---------------------------------------------------------------------------


class TestTokenAndShape:
    def test_token_fetch_then_active_group_message(self, target_env):
        """群主动消息:先 getAppAccessToken,再 QQBot 头 POST v2/groups 端点。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛"}], CONTEXT))

        assert calls[0]["url"] == "https://bots.qq.com/app/getAppAccessToken"
        assert calls[0]["body"] == {"appId": "102345678", "clientSecret": "s3cret-qq"}
        assert calls[1]["url"] == "https://api.sgroup.qq.com/v2/groups/ABCDEF123456/messages"
        assert calls[1]["headers"]["authorization"] == "QQBot QQ-token-1"
        body = calls[1]["body"]
        assert body["msg_type"] == 0
        assert isinstance(body["msg_seq"], int) and 0 <= body["msg_seq"] <= 65535
        assert "msg_id" not in body  # 主动消息:不带 msg_id(被动回复才带)
        assert "羊毛" in body["content"]

    def test_c2c_target_uses_users_endpoint(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("c2c:OPENID9876"))))

        assert calls[1]["url"] == "https://api.sgroup.qq.com/v2/users/OPENID9876/messages"

    def test_guild_target_omits_msg_seq(self, target_env):
        """guild 频道消息 body 只带 content(蓝本 ``_send_guild_text`` 同形态)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("guild:998877665"))))

        assert calls[1]["url"] == "https://api.sgroup.qq.com/channels/998877665/messages"
        assert "content" in calls[1]["body"]
        assert "msg_seq" not in calls[1]["body"]

    def test_token_cached_across_sends(self, target_env):
        """60s 余量缓存:第二次发送复用 token(零 token 端点往返)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))
        _run(channel.send([{"title": "t2"}], CONTEXT))

        token_calls = [c for c in calls if c["url"].startswith("https://bots.qq.com/")]
        message_calls = [c for c in calls if c["url"].startswith("https://api.sgroup.qq.com/")]
        assert len(token_calls) == 1
        assert len(message_calls) == 2

    def test_token_response_without_token_is_invalid(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://bots.qq.com/"):
                return httpx.Response(200, json={"expires_in": 7200})
            raise AssertionError("消息端点不应被触达")  # pragma: no cover

        channel = QQBotChannel(
            target="env:MYIA_TEST_QQBOT_TARGET",
            appid_ref="env:MYIA_TEST_QQBOT_APPID",
            secret_ref="env:MYIA_TEST_QQBOT_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_token_non_200_error_body_reports_api_error_with_status(self, target_env):
        """复核 B2 回归:token 端点非 200 + JSON 错误体 → ``qqbot_api_error``
        带 ``HTTP <status>`` 与原厂片段(与 msgraph_webhook._post_token 同款;
        不得报成「缺 access_token」的 invalid_response、不得丢状态码)。"""
        from myia.push.delivery import classify_dead_error

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://bots.qq.com/"):
                return httpx.Response(
                    400, json={"code": 100036, "message": "invalid client secret"}
                )
            raise AssertionError("消息端点不应被触达")  # pragma: no cover

        channel = QQBotChannel(
            target="env:MYIA_TEST_QQBOT_TARGET",
            appid_ref="env:MYIA_TEST_QQBOT_APPID",
            secret_ref="env:MYIA_TEST_QQBOT_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "qqbot_api_error"
        assert "HTTP 400" in str(excinfo.value)
        assert "invalid client secret" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None  # token 级失败无死信语义

    def test_msg_seq_range(self):
        for _ in range(50):
            seq = next_msg_seq()
            assert 0 <= seq <= 65535

    def test_missing_secret_env_is_env_var_missing(self, target_env, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_QQBOT_SECRET", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"
        assert calls == []


# ---------------------------------------------------------------------------
# 寻址:c2c:/group:/guild: 直达 + 定向/legacy 两路
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("group:XYZ999"))))

        assert calls[1]["url"].endswith("/v2/groups/XYZ999/messages")

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_QQBOT_TARGET", raising=False)
        calls: list[dict] = []
        channel = QQBotChannel(
            appid_ref="env:MYIA_TEST_QQBOT_APPID",
            secret_ref="env:MYIA_TEST_QQBOT_SECRET",
            client=httpx.AsyncClient(transport=_mock(calls)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_bare_openid_without_prefix_rejected(self, target_env):
        """裸 openid 无前缀可辨(蓝本靠入站元数据,MYIA 零入站)→ 形态报错。"""
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("ABCDEF123456"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert "c2c:" in str(excinfo.value)
        assert calls == []

    def test_direct_ref_parse(self):
        for ref in ["group:ABCDEF123456", "c2c:OPENID9876", "guild:998877665"]:
            target = QQBotChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "qqbot",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["ABCDEF123456", "channel:123", "群:abc", ""]:
            assert QQBotChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_bypasses_directory(self, tmp_path):
        target = resolve_target(
            "qqbot:group:ABCDEF123456", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )
        assert (target.platform, target.chat_id) == ("qqbot", "group:ABCDEF123456")

    def test_resolve_via_directory_name(self, tmp_path):
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "qqbot",
            [ChannelEntry(platform="qqbot", chat_id="group:ABCDEF123456", name="羊毛群")],
            now=1.0,
        )
        by_name = resolve_target("qqbot:羊毛群", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (
            "group:ABCDEF123456",
            RESOLVED_DIRECTORY_NAME,
        )

    def test_template_render(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[1]["body"]["content"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(403 → forbidden、404 → not_found、429/5xx → 瞬态;额度透传)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def _channel_with_message_response(self, calls, response):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_capture(request))
            if str(request.url).startswith("https://bots.qq.com/"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
            return response

        return QQBotChannel(
            target="env:MYIA_TEST_QQBOT_TARGET",
            appid_ref="env:MYIA_TEST_QQBOT_APPID",
            secret_ref="env:MYIA_TEST_QQBOT_SECRET",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    def test_quota_error_vendor_message_passthrough(self, target_env):
        """主动消息额度耗尽:原厂 message 原样透传,不吞不改。"""
        calls: list[dict] = []
        channel = self._channel_with_message_response(
            calls,
            httpx.Response(
                400, json={"code": 120003, "message": "主动消息推送数量到达上限"}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "qqbot_api_error"
        assert "主动消息推送数量到达上限" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_403_classifies_forbidden(self, target_env):
        calls: list[dict] = []
        channel = self._channel_with_message_response(
            calls, httpx.Response(403, json={"code": 11253, "message": "auth check failed"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, target_env):
        calls: list[dict] = []
        channel = self._channel_with_message_response(
            calls, httpx.Response(404, json={"code": 1000200, "message": "group not found"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
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

        channel = QQBotChannel(
            target="env:MYIA_TEST_QQBOT_TARGET",
            appid_ref="env:MYIA_TEST_QQBOT_APPID",
            secret_ref="env:MYIA_TEST_QQBOT_SECRET",
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
            _run(QQBotChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("qqbot", [], now=1.0)

        counts = _run(directory.refresh({"qqbot": QQBotChannel()}, now=100.0))

        assert counts == {}
