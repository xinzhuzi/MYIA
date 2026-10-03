"""Tests for FeishuCardChannel.discover_directory — 10-03-messaging-feishu 步骤 1。

覆盖(蓝本:Hermes plugins/platforms/feishu,MIT;列表发现是 MYIA 新增
设计,见 prd 偏离注记):im/v1/chats 翻页聚合(has_more/page_token)、
items→ChannelEntry 映射(chat_id/name/type=group/已解散跳过/无名退回
chat_id)、空目录、429 退避一次再试、401/token 失效结构化错误、发现
结果经 ChannelDirectory.refresh 合并进目录桶(失败保留旧桶,不触碰
死信账本)。全部 httpx.MockTransport,零真实网络。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from shishi.push import FeishuCardChannel, PushSendError
from shishi.push.directory import ChannelDirectory


def _chats_body(items: list[dict[str, Any]], *, has_more: bool = False, page_token: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"items": items, "has_more": has_more}
    if page_token:
        data["page_token"] = page_token
    return {"code": 0, "msg": "success", "data": data}


def _chat(chat_id: str, name: str | None = "群", **extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {"chat_id": chat_id}
    if name is not None:
        raw["name"] = name
    raw.update(extra)
    return raw


def _channel(handler) -> FeishuCardChannel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FeishuCardChannel(token="test-token", client=client)


def _run(coro):
    return asyncio.run(coro)


class TestPaginationAggregation:
    def test_multi_page_aggregates_all_groups(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if "page_token" not in request.url.params:
                return httpx.Response(200, json=_chats_body(
                    [_chat("oc_1", "AI中转站合伙人群"), _chat("oc_2", "服务器折扣群")],
                    has_more=True, page_token="pt-2",
                ))
            assert request.url.params["page_token"] == "pt-2"
            return httpx.Response(200, json=_chats_body([_chat("oc_3", "羊毛线报群")]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert [e.chat_id for e in entries] == ["oc_1", "oc_2", "oc_3"]
        assert entries[0].name == "AI中转站合伙人群"
        # 翻页请求参数契约(官方文档:user_id_type/page_size/page_token)
        assert len(requests) == 2
        assert requests[0].url.params["page_size"] == "100"
        assert requests[0].url.params["user_id_type"] == "open_id"
        assert requests[0].headers["Authorization"] == "Bearer test-token"
        assert requests[0].url.path == "/open-apis/im/v1/chats"

    def test_page_size_capped_at_100_with_max_pages_guard(self):
        """服务端 has_more 永不收敛 → 保底 20 页止步(防死循环)。"""
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json=_chats_body(
                [_chat(f"oc_{calls['count']}")], has_more=True, page_token=f"pt-{calls['count']}",
            ))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert calls["count"] == 20  # CHATS_MAX_PAGES 保底
        assert len(entries) == 20

    def test_has_more_without_page_token_stops_defensively(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body([_chat("oc_1")], has_more=True))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())
        assert [e.chat_id for e in entries] == ["oc_1"]


class TestEntryMapping:
    def test_entries_map_platform_group_type_with_no_thread(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body([_chat("oc_1", "AI中转站合伙人群")]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())

        entry = entries[0]
        assert entry.platform == "feishu"
        assert entry.type == "group"
        assert entry.thread_id is None

    def test_dissolved_groups_skipped_and_nameless_falls_back_to_chat_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body([
                _chat("oc_dead", "已解散", chat_status="dissolved"),
                _chat("oc_saved", "解散留档", chat_status="dissolved_save"),
                _chat("oc_noname", None),
                _chat("oc_ok", "正常群", chat_status="normal"),
            ]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert [(e.chat_id, e.name) for e in entries] == [
            ("oc_noname", "oc_noname"),  # 无名群退回 chat_id 占位,仍可按 id 寻址
            ("oc_ok", "正常群"),
        ]

    def test_malformed_items_skipped_quietly(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body(["not-a-dict", {"name": "无id"}, {"chat_id": "oc_1", "name": "ok"}]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())
        assert [e.chat_id for e in entries] == ["oc_1"]

    def test_empty_directory_returns_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body([]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())
        assert entries == []


class TestRateLimitAndAuthErrors:
    def test_429_backs_off_once_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(FeishuCardChannel, "discover_backoff_seconds", 0.0)
        seen = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["count"] += 1
            if seen["count"] == 1:
                return httpx.Response(429, json={"code": 99991400, "msg": "too many requests"})
            return httpx.Response(200, json=_chats_body([_chat("oc_1", "限频后群")]))

        channel = _channel(handler)
        entries = _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert seen["count"] == 2
        assert [e.chat_id for e in entries] == ["oc_1"]

    def test_429_twice_raises_structured_error(self, monkeypatch):
        monkeypatch.setattr(FeishuCardChannel, "discover_backoff_seconds", 0.0)
        seen = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["count"] += 1
            return httpx.Response(429, json={"code": 99991400, "msg": "too many requests"})

        channel = _channel(handler)
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert seen["count"] == 2  # 退避一次,再失败按发现失败处理(design D2)
        assert excinfo.value.code == "feishu_api_error"

    def test_401_invalid_token_raises_structured_error(self):
        """token 失效:飞书 401 + 错误码 envelope → 结构化 PushSendError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"code": 99991663, "msg": "invalid access token for Authorization header."}
            )

        channel = _channel(handler)
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        _run(channel._client.aclose())

        assert excinfo.value.code == "feishu_api_error"
        assert "99991663" in str(excinfo.value)

    def test_missing_token_env_raises_credential_error(self, monkeypatch):
        monkeypatch.delenv("FEISHU_BOT_TOKEN", raising=False)
        channel = FeishuCardChannel()  # 无注入 token/client:凭据路径走到 env
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        assert excinfo.value.code == "env_var_missing"

    def test_non_json_response_raises_invalid_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        channel = _channel(handler)
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        _run(channel._client.aclose())
        assert excinfo.value.code == "invalid_response"

    def test_missing_data_object_raises_invalid_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "msg": "success"})

        channel = _channel(handler)
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        _run(channel._client.aclose())
        assert excinfo.value.code == "invalid_response"


class TestDirectoryMerge:
    def test_discovered_entries_merge_into_channel_directory(self, tmp_path):
        """acceptance:发现结果经 directory.refresh 合并进 feishu 桶并持久化。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chats_body(
                [_chat("oc_1", "AI中转站合伙人群"), _chat("oc_2", "服务器折扣群")],
                has_more=True, page_token="pt",
            ) if "page_token" not in request.url.params else _chats_body([_chat("oc_dm", None)]))

        channel = _channel(handler)
        directory = ChannelDirectory(tmp_path)
        counts = _run(directory.refresh({"feishu": channel}))
        _run(channel._client.aclose())

        assert counts == {"feishu": 3}
        assert [(e.chat_id, e.name, e.type) for e in directory.entries("feishu")] == [
            ("oc_1", "AI中转站合伙人群", "group"),
            ("oc_2", "服务器折扣群", "group"),
            ("oc_dm", "oc_dm", "group"),
        ]
        saved = json.loads((tmp_path / "channel_directory.json").read_text(encoding="utf-8"))
        assert [e["chat_id"] for e in saved["platforms"]["feishu"]] == ["oc_1", "oc_2", "oc_dm"]

    def test_discovery_failure_keeps_old_bucket_and_ledger_untouched(self, tmp_path):
        """token 失效容错(prd 需求 2):保留旧目录、不产生死信(design D3)。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform("feishu", [_entry("oc_old", "旧群")], now=1.0)
        directory.save()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"code": 99991663, "msg": "invalid access token"})

        channel = _channel(handler)
        counts = _run(directory.refresh({"feishu": channel}))
        _run(channel._client.aclose())

        assert counts == {}  # 失败平台不在结果里
        assert [e.chat_id for e in directory.entries("feishu")] == ["oc_old"]  # 旧桶保留
        assert not (tmp_path / "delivery_ledger.json").exists()  # 不触碰投递死信


def _entry(chat_id: str, name: str):
    from shishi.push.directory import ChannelEntry

    return ChannelEntry(platform="feishu", chat_id=chat_id, name=name)
