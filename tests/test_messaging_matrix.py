"""Tests for 10-03-messaging-w3-longtail 组二 — matrix 适配器。

覆盖:CS API PUT 形态(Bearer/msgtype/body/txnId)、房间别名先解析、
定向(context.target)优先与 legacy 兜底、missing_target fail-fast、
错误分类(HTTP 403 M_FORBIDDEN → forbidden、404 M_NOT_FOUND → not_found、
429 → 瞬态)、超长自动分段、直达 ``!room``/``#alias`` 与目录名/前缀寻址、
无自动发现语义。

蓝本对照:Hermes ``plugins/platforms/matrix/adapter.py``(MIT)的
m.room.message 出站路径——此处测 MYIA 纯 CS API 重写(httpx 直达,无
mautrix)。

No pytest-asyncio: async calls run through ``asyncio.run``(同
test_push.py 约定)。All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import httpx
import pytest

from shishi.push import SendContext
from shishi.push.base import PushSendError
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.matrix import MatrixChannel
from shishi.push.targets import RESOLVED_DIRECT, RESOLVED_DIRECTORY_PREFIX, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
ROOM_ID = "!AbCdEf123:matrix.example.com"
ALIAS = "#myia:matrix.example.com"

#: 真实 PLATFORMS 登记 is 集成步(push/__init__);本文件用显式注册表钉
#: 直达语义,登记后零改动生效。
PLATFORMS: dict[str, type] = {"matrix": MatrixChannel}


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
            200, json={"event_id": "$ev1", "room_id": ROOM_ID}
        )

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> MatrixChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_MATRIX_ROOM")
    kwargs.setdefault("server_ref", "env:MYIA_TEST_MATRIX_SERVER")
    kwargs.setdefault("token_ref", "env:MYIA_TEST_MATRIX_TOKEN")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return MatrixChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="matrix", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def matrix_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_MATRIX_SERVER", "https://matrix.example.com")
    monkeypatch.setenv("MYIA_TEST_MATRIX_TOKEN", "syt_tok_123")
    monkeypatch.setenv("MYIA_TEST_MATRIX_ROOM", ROOM_ID)


# ---------------------------------------------------------------------------
# CS API PUT 形态
# ---------------------------------------------------------------------------


class TestSendShape:
    def test_room_id_target_puts_m_room_message(self, matrix_env):
        """房间 id 直发:一次 PUT,txnId 新生、Bearer 头、m.text body。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert len(calls) == 1
        call = calls[0]
        assert call["method"] == "PUT"
        assert call["url"].startswith("https://matrix.example.com/_matrix/client/v3/rooms/")
        assert "/send/m.room.message/" in call["url"]
        assert call["headers"]["authorization"] == "Bearer syt_tok_123"
        assert "羊毛" in call["body"] and "https://x/1" in call["body"]
        assert '"msgtype":"m.text"' in call["body"].replace(" ", "")

    def test_alias_target_resolves_directory_endpoint_first(self, matrix_env):
        """``#alias`` 先 GET directory/room 换 room_id,再 PUT 发送。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(ALIAS))))

        assert calls[0]["method"] == "GET"
        assert "/_matrix/client/v3/directory/room/" in calls[0]["url"]
        assert calls[0]["url"].endswith("%23myia%3Amatrix.example.com")  # # 与 : 均编码
        assert calls[1]["method"] == "PUT"
        # PUT 用解析出的房间 id(路径段全量编码:! 与 : 均转义)
        assert ROOM_ID.replace("!", "%21").replace(":", "%3A") in calls[1]["url"]

    def test_long_message_auto_splits(self, matrix_env):
        """超 :data:`MESSAGE_LIMIT` 的文本按换行边界分段,逐段顺序 PUT。"""
        import json as _json

        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": "长" * 200, "url": "https://x/1"} for _ in range(120)]

        _run(channel.send(items, CONTEXT))

        assert len(calls) > 1
        for call in calls:
            payload = _json.loads(call["body"])
            assert len(payload["body"]) <= 16000  # 蓝本同款字符上限语义

    def test_template_render_failure_is_structured(self, matrix_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert calls == []

    def test_transport_error_reports_http_error(self, matrix_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = MatrixChannel(
            target="env:MYIA_TEST_MATRIX_ROOM",
            server_ref="env:MYIA_TEST_MATRIX_SERVER",
            token_ref="env:MYIA_TEST_MATRIX_TOKEN",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达 + 目录
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, matrix_env):
        """定向优先:context.target 的房间 id 覆盖 legacy target。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("!other:matrix.example.com"))))

        assert "!other%3Amatrix.example.com".replace("!", "%21") in calls[0]["url"]

    def test_no_target_and_no_context_target_fails_fast(self, matrix_env):
        """两条寻址路径均缺席 → missing_target(fail-fast,绝不猜房)。"""
        calls: list[dict] = []
        channel = _channel(calls, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    def test_malformed_room_ref_is_rejected(self, matrix_env):
        """既非 ``!room`` 亦非 ``#alias`` 形态 → 结构化拒绝(绝不猜端点)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("myia-room"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        """``!room:server`` 与 ``#alias:server`` 直达;其余回落目录。"""
        for ref in [ROOM_ID, ALIAS, "!x:localhost"]:
            target = MatrixChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "matrix",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["matrix-room", "羊毛群", "", "#no-server", "!"]:
            assert MatrixChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_via_explicit_registry(self, tmp_path):
        """``matrix:!room:server`` 经注册表直达,空目录也可寻址。"""
        target = resolve_target(f"matrix:{ROOM_ID}", ChannelDirectory(tmp_path), platforms=PLATFORMS)

        assert (target.platform, target.chat_id, target.resolved_from) == (
            "matrix",
            ROOM_ID,
            RESOLVED_DIRECT,
        )

    def test_resolve_alias_via_directory_name_and_prefix(self, tmp_path):
        """目录名精确/唯一前缀寻址(Hermes 四路径;直达不劫持名称)。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "matrix",
            [
                _entry("!r1:s", "myia-room"),
                _entry("!r2:s", "game-room"),
                _entry("!r3:s", "game-room-2"),
            ],
        )

        by_name = resolve_target("matrix:myia-room", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == ("!r1:s", "directory_name")

        with pytest.raises(Exception) as excinfo:
            resolve_target("matrix:game", directory, platforms=PLATFORMS)
        assert "前缀多义" in str(excinfo.value) or "ambiguous" in str(excinfo.value)


def _entry(chat_id: str, name: str):
    from shishi.push.directory import ChannelEntry

    return ChannelEntry(platform="matrix", chat_id=chat_id, name=name, type="group")


# ---------------------------------------------------------------------------
# 错误分类(死信:403 → forbidden、404 → not_found、429/5xx/超时 → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_m_forbidden_classifies_forbidden(self, matrix_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                403, json={"errcode": "M_FORBIDDEN", "error": "You don't have permission to post to the room"}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "matrix_api_error"
        assert "HTTP 403" in str(excinfo.value) and "M_FORBIDDEN" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_m_not_found_classifies_not_found(self, matrix_env):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(404, json={"errcode": "M_NOT_FOUND", "error": "Room not found"}),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, matrix_env, status):
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                status, json={"errcode": "M_LIMIT_EXCEEDED", "error": "Too many requests"}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid_response(self, matrix_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(502, text="<html>bad gateway</html>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"
        assert classify_dead_error(excinfo.value) is None

    def test_alias_resolution_missing_room_id(self, matrix_env):
        """别名解析应答缺 room_id → invalid_response(不硬造)。"""
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_capture(request))
            if request.method == "GET":
                return httpx.Response(200, json={"servers": ["matrix.example.com"]})
            return httpx.Response(200, json={"event_id": "$ev1"})

        channel = MatrixChannel(
            target="env:MYIA_TEST_MATRIX_ROOM",
            server_ref="env:MYIA_TEST_MATRIX_SERVER",
            token_ref="env:MYIA_TEST_MATRIX_TOKEN",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(ALIAS))))
        assert excinfo.value.code == "invalid_response"
        assert len(calls) == 1  # 解析失败即止,零发送请求


# ---------------------------------------------------------------------------
# 目录语义(无自动发现,不是失败)
# ---------------------------------------------------------------------------


class TestDirectorySemantics:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(MatrixChannel().discover_directory())
        assert "无出站目录发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        """经 canonical refresh:无发现平台跳过、不计数、不算失败、桶不动。"""
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("matrix", [], now=1.0)

        counts = _run(directory.refresh({"matrix": MatrixChannel()}, now=100.0))

        assert counts == {}
