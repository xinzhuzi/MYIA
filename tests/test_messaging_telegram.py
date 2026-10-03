"""Tests for 10-03-messaging-telegram — 被动目录 + 定向发送。

步骤 1:定向发送(``context.target.chat_id`` 覆盖 / legacy 回退 /
``@username`` 报错文案指路数字 id / 4096 分段不回归)+ 直达解析
(数字 id、负数群/频道 id、``@username``;非形态回落目录)。

蓝本对照:Hermes ``plugins/platforms/telegram/telegram_ids.py`` 的
id/username 双形态(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

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

import myia.push as push_module
from myia.push import PLATFORMS, SendContext, TelegramChannel
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target
from myia.push.telegram import MESSAGE_LIMIT

TOKEN = "test-token"
CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _capture(request: httpx.Request) -> dict:
    return {"url": str(request.url), "body": json.loads(request.content.decode("utf-8"))}


def _mock_telegram(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> TelegramChannel:
    kwargs.setdefault("token", TOKEN)
    client = kwargs.pop("client", None) or httpx.AsyncClient(
        transport=_mock_telegram(calls, response=response)
    )
    return TelegramChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="telegram", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


# ---------------------------------------------------------------------------
# 步骤 1a:直达解析(数字 id / @username;Hermes telegram_ids 双形态)
# ---------------------------------------------------------------------------


class TestDirectRefParse:
    @pytest.mark.parametrize("ref", ["12345", "-1001234567890", "-777", "0"])
    def test_numeric_ids_go_direct(self, ref):
        target = TelegramChannel.parse_direct_ref(ref)

        assert target is not None
        assert target.chat_id == ref
        assert target.platform == "telegram"
        assert target.resolved_from == RESOLVED_DIRECT

    def test_public_username_goes_direct(self):
        target = TelegramChannel.parse_direct_ref("@myia_channel")

        assert target is not None
        assert target.chat_id == "@myia_channel"
        assert target.resolved_from == RESOLVED_DIRECT

    @pytest.mark.parametrize("ref", ["张三", "abc", "@ab", "@bad name", "@x!y", "12 34", ""])
    def test_non_matching_refs_fall_back_to_directory(self, ref):
        assert TelegramChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_bypasses_directory(self, tmp_path):
        """``telegram:12345`` 经真实 PLATFORMS 注册表直达,空目录也可寻址。"""
        target = resolve_target("telegram:12345", ChannelDirectory(tmp_path))

        assert (target.platform, target.chat_id) == ("telegram", "12345")
        assert target.resolved_from == RESOLVED_DIRECT


# ---------------------------------------------------------------------------
# 步骤 1b:定向发送(目标覆盖 / legacy 回退 / @username 文案 / 分段)
# ---------------------------------------------------------------------------


class TestTargetedSend:
    def test_context_target_overrides_legacy_chat_id(self, monkeypatch):
        """定向优先:legacy env 在场也必须被 context.target 盖过。"""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
        calls: list[dict] = []
        channel = _channel(calls)  # legacy target = env:TELEGRAM_CHAT_ID

        _run(channel.send([{"title": "t", "url": "https://x/1"}], replace(CONTEXT, target=_target("99999"))))

        assert calls[0]["body"]["chat_id"] == "99999"

    def test_legacy_fallback_without_target_context(self, monkeypatch):
        """不配 targets 的旧配置行为不变:仍解析 env:TELEGRAM_CHAT_ID。"""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["body"]["chat_id"] == "424242"

    def test_explicit_target_reference_still_resolves(self, monkeypatch):
        """构造期显式 target 引用(env:)照旧解析(回归护栏)。"""
        monkeypatch.setenv("MYIA_TEST_TG_CHAT", "777")
        calls: list[dict] = []
        channel = _channel(calls, target="env:MYIA_TEST_TG_CHAT")

        _run(channel.send([{"title": "t", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["body"]["chat_id"] == "777"

    def test_username_not_found_error_hints_numeric_id(self):
        """@username 打不进私聊:API chat not found 文案须指路数字 chat_id(design D1)。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                200,
                json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t", "url": "https://x/1"}], replace(CONTEXT, target=_target("@nobody"))))

        message = str(excinfo.value)
        assert "chat not found" in message  # 原厂描述保留 → 死信分类照常命中
        assert "数字 chat_id" in message  # 私聊必须数字 id 的指路文案
        assert classify_dead_error(excinfo.value) == "not_found"

    def test_numeric_target_not_found_carries_no_hint(self):
        """数字 id 打不存在:原厂错误即可,不附 @username 提示。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                200,
                json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t", "url": "https://x/1"}], replace(CONTEXT, target=_target("00000"))))

        assert "数字 chat_id" not in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    def test_transient_error_on_username_target_has_no_hint(self):
        """瞬态错误(429)不附 @username 指路文案——提示只在 chat 不存在时才有意义。"""
        calls: list[dict] = []
        channel = _channel(
            calls,
            response=httpx.Response(
                200, json={"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3"}
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t", "url": "https://x/1"}], replace(CONTEXT, target=_target("@channel"))))

        assert "数字 chat_id" not in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None  # 瞬态不标死信(design D4)

    def test_splitting_applies_to_every_targeted_chunk(self):
        """4096 分段对定向目标各自生效:每个 chunk 都打到 target.chat_id。"""
        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": f"标题{i}x" + "长" * 200, "url": f"https://x/{i}"} for i in range(60)]
        assert len(channel._compose(items, CONTEXT)[0]) > 1  # 前置:确实分了段

        _run(channel.send(items, replace(CONTEXT, target=_target("99999"))))

        assert len(calls) > 1
        assert {call["body"]["chat_id"] for call in calls} == {"99999"}
        assert all(len(call["body"]["text"]) <= MESSAGE_LIMIT for call in calls)


# ---------------------------------------------------------------------------
# 步骤 1c:注册(core 交付的 PLATFORMS 空表由本任务登记 telegram)
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_platforms_registry_contains_telegram(self):
        assert PLATFORMS.get("telegram") is TelegramChannel
        assert TelegramChannel.supports_targeting is True

    def test_registered_channel_supports_targeting_contract(self):
        """派发层判定:注册进 PLATFORMS 的通道必须真的支持寻址(delivery 契约)。"""
        assert getattr(push_module.PLATFORMS["telegram"], "supports_targeting", False) is True


# ---------------------------------------------------------------------------
# 步骤 2:poller on_chat sink(被动目录积累,design D2)
# ---------------------------------------------------------------------------

from myia.push.telegram_feedback import (  # noqa: E402
    TelegramFeedbackPoller,
    chat_entry_from_update,
    entry_from_chat,
)

GROUP_CHAT = {"id": -1001234567890, "type": "supergroup", "title": "羊毛交流群"}
PRIVATE_CHAT = {
    "id": 555001,
    "type": "private",
    "first_name": "张",
    "last_name": "三",
    "username": "zhangsan",
}


def _update(update_id: int, payload_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"update_id": update_id, payload_key: payload}


def _message(chat: dict[str, Any]) -> dict[str, Any]:
    return {"message_id": 1, "chat": chat, "text": "hi"}


def _make_poller(
    updates: list[dict[str, Any]], *, on_chat: Any = None
) -> tuple[TelegramFeedbackPoller, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramFeedbackPoller(token=TOKEN, on_chat=on_chat, client=client), client


class TestChatNormalization:
    def test_group_and_supergroup_map_to_group_type(self):
        for chat_type in ("group", "supergroup"):
            entry = entry_from_chat({"id": -100, "type": chat_type, "title": "服务器折扣群"})
            assert (entry.name, entry.type) == ("服务器折扣群", "group")

    def test_channel_maps_to_channel_type_with_title(self):
        entry = entry_from_chat({"id": -1009, "type": "channel", "title": "AI 日报"})
        assert (entry.name, entry.type) == ("AI 日报", "channel")

    def test_private_dm_name_is_first_last_plus_username_note(self):
        entry = entry_from_chat(PRIVATE_CHAT)
        assert (entry.name, entry.type) == ("张 三 (@zhangsan)", "dm")
        assert entry.platform == "telegram"
        assert entry.chat_id == "555001"

    def test_private_without_username_keeps_plain_name(self):
        entry = entry_from_chat({"id": 7, "type": "private", "first_name": "李四"})
        assert entry.name == "李四"

    def test_private_name_falls_back_to_username_then_chat_id(self):
        by_user = entry_from_chat({"id": 8, "type": "private", "username": "u_only"})
        bare = entry_from_chat({"id": 9, "type": "private"})
        assert by_user.name == "u_only"
        assert bare.name == "9"

    @pytest.mark.parametrize(
        "bad",
        [None, "chat", {}, {"type": "private", "first_name": "无 id"}, {"id": "  "}],
    )
    def test_malformed_chat_is_dropped(self, bad):
        assert entry_from_chat(bad) is None

    def test_effective_chat_from_message_and_callback_query(self):
        message_entry = chat_entry_from_update(_update(1, "message", _message(GROUP_CHAT)))
        query_entry = chat_entry_from_update(
            _update(
                2,
                "callback_query",
                {"id": "c1", "data": "fb:good:k", "message": _message(PRIVATE_CHAT)},
            )
        )

        assert (message_entry.chat_id, message_entry.type) == ("-1001234567890", "group")
        assert (query_entry.chat_id, query_entry.type) == ("555001", "dm")

    def test_updates_without_chat_yield_none(self):
        assert chat_entry_from_update({"update_id": 3, "unknown_event": {}}) is None
        assert chat_entry_from_update("not-a-dict") is None


class TestPollerSink:
    def test_poll_dispatches_every_observed_chat(self):
        seen: list[Any] = []
        updates = [
            _update(11, "message", _message(GROUP_CHAT)),
            _update(12, "callback_query", {"id": "c1", "data": "fb:good:k", "message": _message(PRIVATE_CHAT)}),
            _update(13, "message", _message(GROUP_CHAT)),  # 同 chat 重复观测照样回调
        ]
        poller, client = _make_poller(updates, on_chat=seen.append)
        try:
            result = _run(poller.poll())
        finally:
            _run(client.aclose())

        assert [(e.chat_id, e.type) for e in seen] == [
            ("-1001234567890", "group"),
            ("555001", "dm"),
            ("-1001234567890", "group"),
        ]
        # 轮询本体零影响:回调/书签照旧。
        assert result.update_count == 3
        assert result.next_offset == 14

    def test_sink_error_never_breaks_polling(self):
        """sink 抛错只记日志:轮询返回值不受影响,后续 update 照常派发。"""
        seen: list[Any] = []

        def flaky_sink(entry: Any) -> None:
            if entry.chat_id == "-1001234567890":
                raise RuntimeError("sink exploded")
            seen.append(entry)

        updates = [
            _update(21, "message", _message(GROUP_CHAT)),  # 抛错点
            _update(22, "message", _message(PRIVATE_CHAT)),  # 其后必须仍被派发
        ]
        poller, client = _make_poller(updates, on_chat=flaky_sink)
        try:
            result = _run(poller.poll())
        finally:
            _run(client.aclose())

        assert [e.chat_id for e in seen] == ["555001"]
        assert result.update_count == 2 and result.next_offset == 23

    def test_without_sink_behaviour_is_unchanged(self):
        """不注入 sink:poll 行为与既有契约逐字段一致(回归护栏,design D2)。"""
        updates = [
            _update(31, "message", _message(PRIVATE_CHAT)),
            _update(32, "callback_query", {"id": "c1", "data": "fb:bad:k"}),
        ]
        poller, client = _make_poller(updates)
        try:
            result = _run(poller.poll())
        finally:
            _run(client.aclose())

        assert result.update_count == 2
        assert len(result.callbacks) == 1
        assert result.callbacks[0].dedup_key == "k"
        assert result.next_offset == 33

    def test_poll_error_path_skips_sink_entirely(self):
        """API 报错时 _parse_response 先抛,sink 不应被触碰。"""
        seen: list[Any] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        poller = TelegramFeedbackPoller(token=TOKEN, on_chat=seen.append, client=client)
        from myia.push.telegram_feedback import TelegramFeedbackError

        with pytest.raises(TelegramFeedbackError):
            _run(poller.poll())
        _run(client.aclose())
        assert seen == []


class TestDirectoryMerge:
    def test_sink_merges_into_directory_and_persists(self, tmp_path):
        """接线形态:poller sink → merge_entries → 落盘可重载(prd 验收 1 的单测面)。"""
        directory = ChannelDirectory(tmp_path)
        updates = [
            _update(41, "message", _message(GROUP_CHAT)),
            _update(42, "message", _message(PRIVATE_CHAT)),
        ]

        def sink(entry: Any) -> None:
            directory.merge_entries("telegram", [entry], now=1000.0)

        poller, client = _make_poller(updates, on_chat=sink)
        try:
            _run(poller.poll())
        finally:
            _run(client.aclose())

        # 内存态 + 磁盘态都到位;重载后仍在(getUpdates 丢书签重放也不丢目录)。
        assert [(e.name, e.type) for e in directory.entries("telegram")] == [
            ("羊毛交流群", "group"),
            ("张 三 (@zhangsan)", "dm"),
        ]
        reloaded = ChannelDirectory(tmp_path)
        assert {e.chat_id for e in reloaded.entries("telegram")} == {"-1001234567890", "555001"}

    def test_merge_refreshes_existing_and_alias_still_wins(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries(
            "telegram",
            [entry_from_chat(GROUP_CHAT)],
            now=1000.0,
        )
        assert directory.merge_entries("telegram", [entry_from_chat(GROUP_CHAT)], now=1000.0) == 0  # 重复观测零变化

        # 观测到改名:title 变了要刷新,last_seen 前移。
        renamed = dict(GROUP_CHAT, title="羊毛交流群 2.0")
        changed = directory.merge_entries("telegram", [entry_from_chat(renamed)], now=2000.0)
        entry = directory.find("telegram", "-1001234567890")
        assert changed > 0 and entry.name == "羊毛交流群 2.0" and entry.last_seen == 2000.0

        # 手工别名是持久覆盖层:合并后重套,别名赢。
        directory.set_alias("telegram", "-1001234567890", "我的羊毛群")
        directory.merge_entries("telegram", [entry_from_chat(renamed)], now=3000.0)
        assert directory.find("telegram", "-1001234567890").name == "我的羊毛群"


# ---------------------------------------------------------------------------
# 步骤 3:管线接线(_build_feedback_poller 注入 sink;注册已随步骤 1 落地)
# ---------------------------------------------------------------------------

from myia.pipeline import Pipeline  # noqa: E402
from myia.schema import load_category  # noqa: E402
from myia.store import SQLiteStore  # noqa: E402


def _tg_pipeline(tmp_path, monkeypatch, push: list[dict[str, Any]] | None = None) -> Pipeline:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "wired-token")
    config = load_category(
        {
            "id": "tg-demo",
            "name": "TG 演示",
            "schedule": "0 9 * * *",
            "timezone": "UTC",
            "sources": [
                {
                    "name": "api",
                    "engine": "direct_api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*].title", "url": "$[*].url"}},
                }
            ],
            "push": push if push is not None else [{"channel": "telegram", "targets": ["telegram:12345"]}],
        }
    )
    # db_path 必须显式指到 tmp_path:目录落数据根(db 父目录),缺省会写 cwd。
    return Pipeline(
        config,
        db_path=tmp_path / "p.db",
        store=SQLiteStore(tmp_path / "p.db"),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, text=""))
        ),
    )


class TestPipelineWiring:
    def test_poller_build_injects_directory_sink(self, tmp_path, monkeypatch):
        """接线:常驻轮询器带 sink,落点经真实 merge 进目录并落盘(design D2)。"""
        pipeline = _tg_pipeline(tmp_path, monkeypatch)
        try:
            poller = pipeline._build_feedback_poller()
            assert poller is not None
            assert poller._on_chat == pipeline._merge_telegram_chat  # 注入了管线 sink

            # 真实 poll 轮经 sink → 目录 telegram 桶(与 run_forever 循环同一路径)。
            wired, client = _make_poller(
                [
                    _update(51, "message", _message(PRIVATE_CHAT)),
                    _update(52, "message", _message(GROUP_CHAT)),
                ],
                on_chat=pipeline._merge_telegram_chat,
            )
            try:
                _run(wired.poll())
            finally:
                _run(client.aclose())

            entry = pipeline._channel_directory.find("telegram", "555001")
            assert entry is not None and entry.name == "张 三 (@zhangsan)"
            reloaded = ChannelDirectory(tmp_path)
            assert {e.chat_id for e in reloaded.entries("telegram")} == {"555001", "-1001234567890"}
        finally:
            pipeline.close()

    def test_poller_absent_without_telegram_channel(self, tmp_path, monkeypatch):
        """未配 telegram 通道:不建轮询器(既有行为回归护栏)。"""
        pipeline = _tg_pipeline(tmp_path, monkeypatch, push=[{"channel": "stdout"}])
        try:
            assert pipeline._build_feedback_poller() is None
        finally:
            pipeline.close()

    def test_poller_absent_when_token_unresolvable(self, tmp_path, monkeypatch):
        pipeline = _tg_pipeline(tmp_path, monkeypatch)
        try:
            # 凭据在 poller 构造期解析(pipeline 构造不解析);此刻撤掉即未启用。
            monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
            assert pipeline._build_feedback_poller() is None
        finally:
            pipeline.close()


class TestChannelsCliPassivePlatform:
    """telegram 注册进 PLATFORMS 后 ``channels refresh`` 的被动目录形态。"""

    def test_refresh_reports_passive_not_failure(self, tmp_path, monkeypatch, capsys):
        import myia.cli as cli_module
        from myia.cli import main

        monkeypatch.setattr(cli_module, "PLATFORMS", {"telegram": TelegramChannel})
        monkeypatch.chdir(tmp_path)

        code = main(["channels", "refresh", "telegram", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0  # 无主动发现 API 不是失败(prd:被动目录是唯一形态)
        assert payload["refreshed"] == {}
        assert payload["passive"] == ["telegram"]
        assert payload["failed"] == []
        assert payload["platforms"] == {}  # 不触碰目录桶(被动积累不被清空)

    def test_refresh_default_all_platforms_isolates_passive(self, tmp_path, monkeypatch, capsys):
        import myia.cli as cli_module
        from myia.cli import main

        monkeypatch.setattr(cli_module, "PLATFORMS", {"telegram": TelegramChannel})
        monkeypatch.chdir(tmp_path)

        code = main(["channels", "refresh"])
        out = capsys.readouterr().out

        assert code == 0
        assert "被动目录平台" in out
        assert "刷新失败" not in out
