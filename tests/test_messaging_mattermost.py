"""Tests for 10-03-messaging-w3-longtail 组二 — mattermost 适配器。

覆盖:REST ``POST /api/v4/posts`` 形态(Bearer/channel_id/message)、
incoming-webhook 一-shot 退路({"text"} 无鉴权头)、定向(context.target)
优先与 legacy 兜底、missing_target fail-fast、错误分类(HTTP 403 →
forbidden、404 app.channel.not_found → not_found、429/5xx → 瞬态)、
超长自动分段(4000)、直达 26 位 channel id 与目录名/前缀寻址、无自动
发现语义。

蓝本对照:Hermes ``plugins/platforms/mattermost/adapter.py``(MIT)的
``_api_post("posts", …)`` 出站路径——此处测 MYIA 重写(REST + webhook 双路)。

No pytest-asyncio: async calls run through ``asyncio.run``(同
test_push.py 约定)。All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from myia.push import SendContext
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, ChannelEntry, DirectoryDiscoverUnsupported
from myia.push.mattermost import MattermostChannel
from myia.push.targets import RESOLVED_DIRECT, RESOLVED_DIRECTORY_PREFIX, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
CHANNEL_ID = "qzcdqfqsppycymfm7ochaprnwr"  # 26 位小写字母数字(蓝本 id 形态)
WEBHOOK_URL = "https://mm.example.com/hooks/uexud7ypbt8idfa3hbq3fmojya"

#: 真实 PLATFORMS 登记 is 集成步(push/__init__);本文件用显式注册表钉
#: 直达语义,登记后零改动生效。
PLATFORMS: dict[str, type] = {"mattermost": MattermostChannel}


def _capture(request: httpx.Request) -> dict:
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": request.content.decode("utf-8"),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(
            201, json={"id": "post1", "channel_id": CHANNEL_ID, "message": "ok"}
        )

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> MattermostChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_MM_TARGET")
    kwargs.setdefault("server_ref", "env:MYIA_TEST_MM_SERVER")
    kwargs.setdefault("token_ref", "env:MYIA_TEST_MM_TOKEN")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return MattermostChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="mattermost", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def mm_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_MM_SERVER", "https://mm.example.com")
    monkeypatch.setenv("MYIA_TEST_MM_TOKEN", "mm_tok_123")
    monkeypatch.setenv("MYIA_TEST_MM_TARGET", CHANNEL_ID)


# ---------------------------------------------------------------------------
# REST 路(主路,telegram 范式)
# ---------------------------------------------------------------------------


class TestRestShape:
    def test_channel_id_target_posts_api_v4(self, mm_env):
        """26 位 channel id → ``POST {server}/api/v4/posts``(Bearer + JSON body)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert len(calls) == 1
        call = calls[0]
        assert call["url"] == f"https://mm.example.com/api/v4/posts"
        assert call["headers"]["authorization"] == "Bearer mm_tok_123"
        payload = json.loads(call["body"])
        assert payload["channel_id"] == CHANNEL_ID
        assert "羊毛" in payload["message"] and "https://x/1" in payload["message"]

    def test_context_target_channel_id_overrides_legacy(self, mm_env):
        """定向优先:context.target 的 channel id 覆盖 legacy target(仍走 REST)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        other = "a" * 26
        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(other))))

        payload = json.loads(calls[0]["body"])
        assert payload["channel_id"] == other

    def test_long_message_auto_splits(self, mm_env):
        """超 :data:`MESSAGE_LIMIT`(4000)自动分段,逐段顺序 POST。"""
        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": "长" * 200, "url": "https://x/1"} for _ in range(40)]

        _run(channel.send(items, CONTEXT))

        assert len(calls) > 1
        for call in calls:
            assert len(json.loads(call["body"])["message"]) <= 4000

    def test_no_target_and_no_context_target_fails_fast(self, mm_env):
        """两条寻址路径均缺席 → missing_target(fail-fast,绝不猜频道)。"""
        calls: list[dict] = []
        channel = _channel(calls, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_bad_shape_target_is_rejected(self, mm_env):
        """既非 26 位 id 亦非 URL → invalid_credential_ref(绝不猜端点)。

        复核 D1:解析值不回显(回显串可能含 ``403`` 等样式文本,放大死信
        分类误判暴露面)。
        """
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("room 403"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert "room 403" not in str(excinfo.value)  # 解析值不回显
        assert classify_dead_error(excinfo.value) is None  # 配置类错误永不标死信
        assert calls == []

    def test_template_render_failure_is_structured(self, mm_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert calls == []


# ---------------------------------------------------------------------------
# webhook 路(legacy 单 target 退路,ntfy 范式)
# ---------------------------------------------------------------------------


class TestWebhookPath:
    def test_webhook_url_target_posts_text_only(self, monkeypatch):
        """target 解析出整条 webhook URL → 一-shot POST {"text"},无鉴权头。"""
        monkeypatch.setenv("MYIA_TEST_MM_TARGET", WEBHOOK_URL)
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛"}], CONTEXT))

        call = calls[0]
        assert call["url"] == WEBHOOK_URL
        assert "authorization" not in call["headers"]
        payload = json.loads(call["body"])
        assert list(payload.keys()) == ["text"]  # webhook 路只发 text,无 channel_id
        assert "羊毛" in payload["text"]

    def test_webhook_error_carries_http_status(self, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_MM_TARGET", WEBHOOK_URL)
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text="forbidden"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "mattermost_api_error"
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"


# ---------------------------------------------------------------------------
# 错误分类(死信:403 → forbidden、404 → not_found、429/5xx/超时 → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, mm_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                403,
                json={"id": "api.context.permissions.app_error", "message": "You do not have the appropriate permissions"},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "mattermost_api_error"
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_channel_not_found_classifies_not_found(self, mm_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                404,
                json={"id": "app.channel.not_found.app_error", "message": "Channel does not exist"},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert "app.channel.not_found" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, mm_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, mm_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = MattermostChannel(
            target="env:MYIA_TEST_MM_TARGET",
            server_ref="env:MYIA_TEST_MM_SERVER",
            token_ref="env:MYIA_TEST_MM_TOKEN",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid_response(self, mm_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(502, text="<html/>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"
        assert classify_dead_error(excinfo.value) is None

    def test_2xx_non_dict_json_is_structured_not_typeerror(self, mm_env):
        """复核 B1 回归:2xx + JSON 数组不得让 ``dict(data)`` 的 TypeError
        裸逃 send()(契约:send 只抛 PushSendError;matrix 同款 Mapping 守卫)。"""
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(201, json=[1, 2]))

        try:
            _run(channel.send([{"title": "t"}], CONTEXT))
        except PushSendError as exc:
            assert exc.code == "invalid_response"
            assert "HTTP 201" in str(exc)
            assert classify_dead_error(exc) is None
        except Exception as exc:  # noqa: BLE001 - 契约违约即测试失败
            pytest.fail(f"NON-PushSendError escapes send(): {type(exc).__name__} - {exc}")
        else:
            pytest.fail("2xx 非 dict JSON 应结构化拒绝,却静默成功")


# ---------------------------------------------------------------------------
# 寻址:直达 26 位 id + 目录名/前缀
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_direct_ref_parse(self):
        """26 位小写字母数字直达;频道名/大写/短串回落目录。"""
        for ref in [CHANNEL_ID, "a" * 26, "0123456789" * 2 + "abcdef"]:
            target = MattermostChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "mattermost",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["town-square", CHANNEL_ID.upper(), CHANNEL_ID[:25], "羊毛群", ""]:
            assert MattermostChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_via_explicit_registry(self, tmp_path):
        """``mattermost:<26位id>`` 经注册表直达,空目录也可寻址。"""
        target = resolve_target(
            f"mattermost:{CHANNEL_ID}", ChannelDirectory(tmp_path), platforms=PLATFORMS
        )

        assert (target.chat_id, target.resolved_from) == (CHANNEL_ID, RESOLVED_DIRECT)

    def test_resolve_via_directory_name_and_prefix(self, tmp_path):
        """目录名精确/唯一前缀寻址(名称不受 id 字符集限制,中文别名可用)。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "mattermost",
            [
                ChannelEntry(platform="mattermost", chat_id=CHANNEL_ID, name="行情群", type="channel"),
                ChannelEntry(platform="mattermost", chat_id="b" * 26, name="town-square", type="channel"),
            ],
        )

        by_name = resolve_target("mattermost:行情群", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == (
            CHANNEL_ID,
            "directory_name",
        )

        by_prefix = resolve_target("mattermost:town", directory, platforms=PLATFORMS)
        assert (by_prefix.chat_id, by_prefix.resolved_from) == (
            "b" * 26,
            RESOLVED_DIRECTORY_PREFIX,
        )


# ---------------------------------------------------------------------------
# 目录语义(无自动发现,不是失败)
# ---------------------------------------------------------------------------


class TestDirectorySemantics:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(MattermostChannel().discover_directory())
        assert "无出站目录发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        """经 canonical refresh:无发现平台跳过、不计数、不算失败、桶不动。"""
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("mattermost", [], now=1.0)

        counts = _run(directory.refresh({"mattermost": MattermostChannel()}, now=100.0))

        assert counts == {}
