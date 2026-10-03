"""Tests for 10-03-messaging-w3-longtail 组三 — a2a(A2A v1.0 JSON-RPC)适配器。

覆盖:one-shot ``SendMessage`` JSON-RPC 形态(``A2A-Version`` 头 + v1.0
Message 单 text Part 无 ``kind`` + contextId 在 Message 内)、可选 Bearer、
contextId 续会话(thread_id)/新生成、模板渲染、寻址两条路(定向优先/
legacy 引用/全缺 missing_target/URL 形态校验)、直达 ``a2a:<URL>`` 与别名/
前缀寻址、错误分类(HTTP 403 → forbidden、404 → not_found、429/5xx → 瞬态、
JSON-RPC error 对象 → a2a_api_error 瞬态、非 JSON → invalid_response)、
DirectoryDiscoverUnsupported。

蓝本对照:Hermes ``plugins/platforms/a2a/tools.py`` 的
``_send_task``/``_http_post_json``(NousResearch/Hermes-Agent,MIT)——
此处测 MYIA 重写。

No pytest-asyncio: async calls run through ``asyncio.run``. All network I/O
goes through ``httpx.MockTransport``(零真网,不触真对端)。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from shishi.push.a2a import PROTOCOL_VERSION, A2aChannel, build_rpc_body, text_part
from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
PEER = "https://agent.example.com/"


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "json": json.loads(request.content.decode("utf-8")) if request.content else None,
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "t1", "result": {}}
        )

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> A2aChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_A2A_PEER")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return A2aChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str, thread_id: str | None = None) -> ChannelTarget:
    return ChannelTarget(
        platform="a2a", chat_id=chat_id, thread_id=thread_id, resolved_from=RESOLVED_DIRECT
    )


@pytest.fixture()
def peer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYIA_TEST_A2A_PEER", PEER)


# ---------------------------------------------------------------------------
# one-shot JSON-RPC 形态
# ---------------------------------------------------------------------------


class TestPublishShape:
    def test_posts_send_message_rpc(self, peer_env):
        """蓝本同款:POST 基址(尾斜杠归一)+ A2A-Version 头 + v1.0 Message 形态。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "https://agent.example.com"
        assert calls[0]["headers"]["a2a-version"] == PROTOCOL_VERSION == "1.0"
        assert calls[0]["headers"]["content-type"] == "application/json"
        body = calls[0]["json"]
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "SendMessage"
        message = body["params"]["message"]
        assert message["role"] == "ROLE_USER"
        assert message["parts"] == [
            {"text": message["parts"][0]["text"], "mediaType": "text/plain"}
        ]
        assert "羊毛" in message["parts"][0]["text"]
        assert message["contextId"]  # 缺省每发新生成

    def test_explicit_token_adds_bearer_header(self, peer_env, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_A2A_TOKEN", "peer-token-1")
        calls: list[dict] = []
        channel = _channel(calls, token_ref="env:MYIA_TEST_A2A_TOKEN")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["headers"]["authorization"] == "Bearer peer-token-1"

    def test_default_token_absent_means_no_auth(self, peer_env):
        """缺省 env:A2A_TOKEN 缺席 = 无鉴权(局域对端合法态)。"""
        calls: list[dict] = []
        channel = _channel(calls)  # token_ref 缺省 None,env:A2A_TOKEN 未设

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert "authorization" not in calls[0]["headers"]

    def test_explicit_token_ref_missing_is_error(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, token_ref="env:MYIA_TEST_A2A_MISSING")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    def test_thread_id_continues_context(self, peer_env):
        """contextId = context.target.thread_id(别名登记续会话)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(
            channel.send(
                [{"title": "t"}], replace(CONTEXT, target=_target(PEER, thread_id="ctx-42"))
            )
        )

        assert calls[0]["json"]["params"]["message"]["contextId"] == "ctx-42"

    def test_template_render_becomes_text(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["json"]["params"]["message"]["parts"][0]["text"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert calls == []

    def test_rpc_body_helper_unit(self):
        body = build_rpc_body("hi", context_id="ctx-1", task_id="task-9")
        assert body["id"] == "task-9"
        assert body["params"]["message"]["contextId"] == "ctx-1"
        assert text_part("hi") == {"text": "hi", "mediaType": "text/plain"}


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达与别名
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls)

        _run(
            channel.send(
                [{"title": "t"}],
                replace(CONTEXT, target=_target("https://other.example.com")),
            )
        )

        assert calls[0]["url"] == "https://other.example.com"

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_A2A_PEER", raising=False)
        calls: list[dict] = []
        channel = _channel(calls, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize("bad", ["agent.example.com", "ftp://agent.example.com", "主人", ""])
    def test_invalid_peer_url_is_rejected(self, peer_env, bad):
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"

    def test_direct_ref_parse(self):
        for ref in ["https://agent.example.com", "http://127.0.0.1:9900"]:
            target = A2aChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "a2a",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["agent.example.com", "羊毛对端", "https://a b", ""]:
            assert A2aChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "a2a:https://agent.example.com",
            ChannelDirectory(tmp_path),
            platforms={"a2a": A2aChannel},
        )
        assert (target.platform, target.chat_id) == ("a2a", "https://agent.example.com")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("a2a", "https://agent.example.com", "主对端")
        directory.set_alias("a2a", "https://other.example.com", "备对端")

        exact = resolve_target("a2a:主对端", directory, platforms={"a2a": A2aChannel})
        assert (exact.chat_id, exact.resolved_from) == (
            "https://agent.example.com",
            "directory_name",
        )

        prefix = resolve_target("a2a:备", directory, platforms={"a2a": A2aChannel})
        assert (prefix.chat_id, prefix.resolved_from) == (
            "https://other.example.com",
            "directory_prefix",
        )


# ---------------------------------------------------------------------------
# 错误分类(死信映射:403/404 硬失败,429/5xx/JSON-RPC error 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(403, text="Forbidden"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(404, text="404: Not Found"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, peer_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "a2a_api_error"
        assert classify_dead_error(excinfo.value) is None

    def test_jsonrpc_error_object_is_structured_transient(self, peer_env):
        """JSON-RPC error(code/message)如实入文案;与 core marker 表无交集 → 瞬态。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "t1", "error": {"code": -32600, "message": "Invalid request"}},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "a2a_api_error"
        assert "-32600" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid_response(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, text="<html>"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_non_object_json_is_invalid_response(self, peer_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, json=[1, 2]))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_transport_error_reports_http_error(self, peer_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = A2aChannel(
            target="env:MYIA_TEST_A2A_PEER",
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
            _run(A2aChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("a2a", [], now=1.0)
        counts = _run(directory.refresh({"a2a": A2aChannel}, now=100.0))
        assert counts == {}

    def test_channel_protocol_conformance(self):
        channel = A2aChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "a2a"
        assert channel.supports_targeting is True
