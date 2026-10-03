"""Tests for 10-03-messaging-w3-longtail 组三 — homeassistant(REST one-shot)。

覆盖:one-shot ``POST /api/services/notify/notify`` 形态(Bearer + title/
message/target 三字段)、4096 截断、模板渲染、寻址两条路(定向优先/legacy
引用/全缺 missing_target/目标形态校验)、基址形态校验、直达
``homeassistant:<实体 id/slug>`` 与别名/前缀寻址、错误分类(HTTP 403 →
forbidden、404 → not_found、401/429/5xx → 瞬态)、DirectoryDiscoverUnsupported。

蓝本对照:Hermes ``plugins/platforms/homeassistant/adapter.py`` 的
``_standalone_send``(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

No pytest-asyncio: async calls run through ``asyncio.run``. All network I/O
goes through ``httpx.MockTransport``(零真网,不触真 Home Assistant)。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.homeassistant import MESSAGE_LIMIT, HomeAssistantChannel, notify_url
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "json": json.loads(request.content.decode("utf-8")) if request.content else None,
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"message": "Notification sent"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> HomeAssistantChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_HA_TARGET")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return HomeAssistantChannel(
        url_ref="env:MYIA_TEST_HA_URL",
        token_ref="env:MYIA_TEST_HA_TOKEN",
        client=client,
        **kwargs,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(
        platform="homeassistant", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYIA_TEST_HA_URL", "http://homeassistant.local:8123/")
    monkeypatch.setenv("MYIA_TEST_HA_TOKEN", "ha-long-lived-token")
    monkeypatch.setenv("MYIA_TEST_HA_TARGET", "mobile_app_pixel")


# ---------------------------------------------------------------------------
# one-shot service call 形态
# ---------------------------------------------------------------------------


class TestPublishShape:
    def test_posts_notify_service_with_bearer_and_payload(self, creds):
        """蓝本同款:{url}/api/services/notify/notify + Bearer + message/target;title=card_title。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "http://homeassistant.local:8123/api/services/notify/notify"
        assert calls[0]["headers"]["authorization"] == "Bearer ha-long-lived-token"
        payload = calls[0]["json"]
        assert payload["target"] == "mobile_app_pixel"
        assert "羊毛" in payload["title"] and "上午" in payload["title"]
        assert "羊毛" in payload["message"] and "https://x/1" in payload["message"]

    def test_notify_url_helper(self):
        assert (
            notify_url("http://h:8123/") == "http://h:8123/api/services/notify/notify"
        )
        assert (
            notify_url("https://ha.example.com")
            == "https://ha.example.com/api/services/notify/notify"
        )

    def test_message_truncated_to_4096(self, creds):
        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": "长" * 5000, "url": "https://x/1"}]

        _run(channel.send(items, CONTEXT))

        assert len(calls[0]["json"]["message"]) == MESSAGE_LIMIT

    def test_template_render_becomes_message(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["json"]["message"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert calls == []

    @pytest.mark.parametrize("bad_url", ["homeassistant.local:8123", "ftp://ha.local", ""])
    def test_bad_base_url_is_rejected(self, creds, monkeypatch, bad_url):
        monkeypatch.setenv("MYIA_TEST_HA_URL", bad_url)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_missing_url_or_token_is_env_var_missing(self, creds, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_HA_URL", raising=False)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达与别名
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, creds):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(
            channel.send(
                [{"title": "t"}], replace(CONTEXT, target=_target("notify.mobile"))
            )
        )

        assert calls[0]["json"]["target"] == "notify.mobile"

    def test_no_target_and_no_context_target_fails_fast(self, creds, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_HA_TARGET", raising=False)
        calls: list[dict] = []
        channel = _channel(calls, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize("bad", ["Mobile App", "notify.mobile extra", "notify.MOBILE", ""])
    def test_invalid_target_shape_is_rejected(self, creds, bad):
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"

    def test_direct_ref_parse(self):
        """直达:实体 id(``domain.object``)与 slug(``mobile_app_pixel``)双形态。"""
        for ref in ["notify.mobile", "mobile_app_pixel", "person_owner", "a1"]:
            target = HomeAssistantChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "homeassistant",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["客厅面板", "notify.mobile extra", ".mobile", "notify.", ""]:
            assert HomeAssistantChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "homeassistant:notify.mobile",
            ChannelDirectory(tmp_path),
            platforms={"homeassistant": HomeAssistantChannel},
        )
        assert (target.platform, target.chat_id) == ("homeassistant", "notify.mobile")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("homeassistant", "mobile_app_pixel", "客厅平板")
        directory.set_alias("homeassistant", "mobile_app_iphone", "口袋提醒")

        exact = resolve_target(
            "homeassistant:客厅平板", directory, platforms={"homeassistant": HomeAssistantChannel}
        )
        assert (exact.chat_id, exact.resolved_from) == ("mobile_app_pixel", "directory_name")

        prefix = resolve_target(
            "homeassistant:口袋", directory, platforms={"homeassistant": HomeAssistantChannel}
        )
        assert (prefix.chat_id, prefix.resolved_from) == ("mobile_app_iphone", "directory_prefix")


# ---------------------------------------------------------------------------
# 错误分类(死信映射:403/404 硬失败,401/429/5xx 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, creds):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(403, text="Forbidden")
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, creds):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(404, text="404: Not Found"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, creds, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "homeassistant_api_error"
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, creds):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = HomeAssistantChannel(
            url_ref="env:MYIA_TEST_HA_URL",
            token_ref="env:MYIA_TEST_HA_TOKEN",
            target="env:MYIA_TEST_HA_TARGET",
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
            _run(HomeAssistantChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("homeassistant", [], now=1.0)
        counts = _run(
            directory.refresh({"homeassistant": HomeAssistantChannel()}, now=100.0)
        )
        assert counts == {}

    def test_channel_protocol_conformance(self):
        channel = HomeAssistantChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "homeassistant"
        assert channel.supports_targeting is True
