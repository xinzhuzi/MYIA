"""Tests for 10-03-messaging-w3-longtail 组二 — simplex 适配器(WS 守护进程)。

覆盖:fire-and-forget ``/_send`` 帧形态(corrId/cmd/group:# 与 DM:@ 寻址)、
超长自动分段(8000)、定向(context.target)优先、missing_target fail-fast、
守护进程连接失败结构化错误、命令超时/断连、**目录发现**(/contacts +
/groups → ChannelEntry;22 家长尾唯一发现实装,含 canonical refresh 整链)、
websockets 依赖门(缺装 → dependency_missing + 安装命令)、直达
``group:<id>`` 与目录名寻址。

蓝本对照:Hermes ``plugins/platforms/simplex/adapter.py``(MIT)的 WS
corrId 协议与 ``list_channels``——此处测 MYIA 重写(短会话出站-only)。

No pytest-asyncio: async calls run through ``asyncio.run``(同
test_push.py 约定)。All WS I/O goes through the injected ``ws_connect``
(fake connection)——零真守护进程。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

import shishi.push.simplex as simplex_module
from shishi.push import SendContext
from shishi.push.base import PushSendError
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.simplex import SimplexChannel, send_command_text
from shishi.push.targets import RESOLVED_DIRECT, RESOLVED_DIRECTORY_NAME, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

#: 真实 PLATFORMS 登记 is 集成步(push/__init__);本文件用显式注册表钉
#: 直达语义,登记后零改动生效。
PLATFORMS: dict[str, type] = {"simplex": SimplexChannel}


class FakeWS:
    """脚本化守护进程连接:按 corrId 回应答帧;也回无关帧测跳过逻辑。"""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._responses = list(responses or [])
        self.connect_args: tuple[str, float] | None = None

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self) -> str | None:
        if not self._responses:
            return None
        reply = self._responses.pop(0)
        if reply.get("__echo__"):
            last = json.loads(self.sent[-1])
            return json.dumps({"corrId": last["corrId"], "resp": reply["resp"]})
        return json.dumps(reply)  # 原样帧(无关 corrId / 坏 JSON 由调用方给)

    async def close(self) -> None:
        self.closed = True


class HangingWS:
    """永不回帧(命令超时路径)。"""

    async def send(self, text: str) -> None:
        return None

    async def recv(self) -> str | None:
        await asyncio.sleep(30)

    async def close(self) -> None:
        return None


class SimplexHarness:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.urls: list[tuple[str, float]] = []

        async def ws_connect(url: str, open_timeout: float) -> Any:
            self.urls.append((url, open_timeout))
            if isinstance(ws, Exception):
                raise ws
            return ws

        self.ws_connect = ws_connect

    def channel(self, **kwargs: Any) -> SimplexChannel:
        kwargs.setdefault("target", "env:MYIA_TEST_SIMPLEX_CHAT")
        kwargs["ws_connect"] = self.ws_connect
        return SimplexChannel(**kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="simplex", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def simplex_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_SIMPLEX_CHAT", "group:3")


# ---------------------------------------------------------------------------
# fire-and-forget 发送形态
# ---------------------------------------------------------------------------


class TestSendShape:
    def test_group_target_sends_send_command_frames(self, simplex_env):
        """group:<id> → ``/_send #<id> json [{"msgContent":…}]`` 帧(corrId mint)。"""
        harness = SimplexHarness(FakeWS())
        channel = harness.channel()

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert len(harness.ws.sent) >= 1
        frame = json.loads(harness.ws.sent[0])
        assert frame["corrId"].startswith("myia-")
        assert frame["cmd"].startswith("/_send #3 json ")
        payload = json.loads(frame["cmd"].rsplit(" json ", 1)[1])
        assert payload[0]["msgContent"]["type"] == "text"
        assert "羊毛" in payload[0]["msgContent"]["text"]
        assert harness.ws.closed  # 短会话:发完即关

    def test_dm_display_name_target_uses_at_prefix(self, simplex_env):
        """DM 显示名 → ``/_send @<名> json …``(蓝本寻址形态)。"""
        harness = SimplexHarness(FakeWS())
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("Alice"))))

        frame = json.loads(harness.ws.sent[0])
        assert frame["cmd"].startswith("/_send @Alice json ")

    def test_long_message_auto_splits(self, simplex_env):
        """超 :data:`MESSAGE_LIMIT`(8000)分段,逐块独立帧。"""
        harness = SimplexHarness(FakeWS())
        channel = harness.channel()
        items = [{"title": "长" * 6000, "url": "https://x/1"} for _ in range(4)]

        _run(channel.send(items, CONTEXT))

        assert len(harness.ws.sent) > 1
        for raw in harness.ws.sent:
            frame = json.loads(raw)
            payload = json.loads(frame["cmd"].rsplit(" json ", 1)[1])
            assert len(payload[0]["msgContent"]["text"]) <= 8000

    def test_context_target_overrides_legacy(self, simplex_env):
        harness = SimplexHarness(FakeWS())
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("group:9"))))

        assert json.loads(harness.ws.sent[0])["cmd"].startswith("/_send #9 json ")

    def test_default_ws_url_and_ref_resolution(self, monkeypatch):
        """无 ws_url_ref → 本机缺省 ws://127.0.0.1:5225;引用在场则解析。"""
        monkeypatch.setenv("MYIA_TEST_SIMPLEX_WS", "ws://127.0.0.1:6001")
        default_harness = SimplexHarness(FakeWS())
        _run(
            default_harness.channel(target=None).send(
                [{"title": "t"}], replace(CONTEXT, target=_target("group:1"))
            )
        )
        assert default_harness.urls == [("ws://127.0.0.1:5225", 10.0)]

        ref_harness = SimplexHarness(FakeWS())
        _run(
            ref_harness.channel(
                target=None, ws_url_ref="env:MYIA_TEST_SIMPLEX_WS"
            ).send([{"title": "t"}], replace(CONTEXT, target=_target("group:1")))
        )
        assert ref_harness.urls[0][0] == "ws://127.0.0.1:6001"

    def test_missing_target_fails_fast(self):
        """两条寻址路径均缺席 → missing_target(零连接)。"""
        harness = SimplexHarness(FakeWS())
        channel = harness.channel(target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert harness.urls == []

    def test_template_render_failure_is_structured(self, simplex_env):
        """模板渲染失败先于连接:零 WS 连接(绝不产孤儿帧)。"""
        harness = SimplexHarness(FakeWS())
        channel = harness.channel(template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"
        assert harness.urls == []


# ---------------------------------------------------------------------------
# 错误路径(连接失败/超时/断连/依赖门)
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_daemon_unreachable_is_structured(self, simplex_env):
        harness = SimplexHarness(OSError("connection refused"))
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "simplex_api_error"
        assert "守护进程连接失败" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None  # 瞬态,不标死信

    def test_command_timeout_is_structured(self, simplex_env, monkeypatch):
        monkeypatch.setattr(simplex_module, "COMMAND_TIMEOUT_SECONDS", 0.05)
        harness = SimplexHarness(HangingWS())
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        assert excinfo.value.code == "simplex_api_error"
        assert "超时" in str(excinfo.value)

    def test_connection_closed_before_reply(self, simplex_env):
        """应答前守护进程关连接(零应答帧)→ 结构化错误。"""
        harness = SimplexHarness(FakeWS())  # recv 立即 None
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.discover_directory())
        assert "关闭连接" in str(excinfo.value)

    def test_dependency_gate_raises_with_install_command(self, simplex_env, monkeypatch):
        """websockets 缺装 → dependency_missing + 安装命令(ocr.py 范式)。"""
        monkeypatch.setattr(simplex_module, "CLIENT_PACKAGE", "definitely_not_a_package_x")
        # 不注入 ws_connect:走缺省连接器,依赖门在其内触发。
        channel = SimplexChannel(target="env:MYIA_TEST_SIMPLEX_CHAT")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"
        assert "shishi[simplex]" in str(excinfo.value)  # 安装命令附文案
        assert "simplex-chat 守护进程" in str(excinfo.value)

    def test_dependency_gate_direct_call(self, monkeypatch):
        monkeypatch.setattr(simplex_module, "CLIENT_PACKAGE", "definitely_not_a_package_x")

        with pytest.raises(PushSendError) as excinfo:
            _run(simplex_module._require_client())
        assert excinfo.value.code == "dependency_missing"


# ---------------------------------------------------------------------------
# 目录发现(22 家长尾唯一实装;feishu_card 范式)
# ---------------------------------------------------------------------------


class TestDirectoryDiscovery:
    def _discovery_ws(self) -> FakeWS:
        """应答队列:先两帧无关流量(坏 JSON + 别端 corrId)再按 corrId 回。"""
        return FakeWS(
            responses=[
                {"__echo__": False, "junk": "not json will be handled"},  # 无 corrId 帧:跳过
                {"corrId": "someone-else", "resp": {"type": "chat"}},  # 无关 corrId:跳过
                {"__echo__": True, "resp": {
                    "contacts": [
                        {"contactId": 7, "localDisplayName": "Alice",
                         "profile": {"displayName": "A"}},
                        {"contactId": 8},
                    ]
                }},
                {"__echo__": True, "resp": {
                    "groups": [
                        {"groupId": 3, "groupProfile": {"displayName": "行情群"}},
                        [{"groupId": 5, "localDisplayName": "游戏群"},
                         {"members": 2}],  # 蓝本 [groupInfo, summary] 双形态
                        {"name": "no id entry"},  # 无 groupId:剔除
                    ]
                }},
            ]
        )

    def test_discover_directory_builds_entries(self, simplex_env):
        """/contacts + /groups → ChannelEntry(dm=显示名/group=group:<id>)。"""
        harness = SimplexHarness(self._discovery_ws())
        channel = harness.channel()

        entries = _run(channel.discover_directory())

        by_id = {entry.chat_id: entry for entry in entries}
        assert by_id["Alice"].type == "dm"
        assert by_id["8"].name == "8"  # 无显示名退 contactId 占位
        assert by_id["group:3"].name == "行情群" and by_id["group:3"].type == "group"
        assert by_id["group:5"].name == "游戏群"  # 列表双形态取 groupInfo
        assert not any(chat_id == "group:None" for chat_id in by_id)
        assert harness.ws.closed

    def test_discovery_flows_through_canonical_refresh(self, simplex_env, tmp_path):
        """整链:directory.refresh 桶替换 + 计数(本批唯一发现平台)。"""
        directory = ChannelDirectory(tmp_path)
        harness = SimplexHarness(self._discovery_ws())

        counts = _run(directory.refresh({"simplex": harness.channel()}, now=100.0))

        assert counts == {"simplex": 4}
        assert {entry.name for entry in directory.entries("simplex")} == {
            "Alice", "8", "行情群", "游戏群",
        }

    def test_send_command_text_forms(self):
        """寻址前缀纯函数:group 剥前缀加 ``#``,其余加 ``@``。"""
        assert send_command_text("group:12", [{"msgContent": {"type": "text", "text": "hi"}}]) == (
            '/_send #12 json [{"msgContent": {"type": "text", "text": "hi"}}]'
        )
        assert send_command_text("Alice", []).startswith("/_send @Alice json ")

    def test_discovery_error_keeps_old_bucket(self, simplex_env, tmp_path):
        """发现失败(守护进程不可达)→ refresh 告警隔离,旧桶保留。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform("simplex", [_entry("group:3", "行情群")])
        harness = SimplexHarness(OSError("down"))

        counts = _run(directory.refresh({"simplex": harness.channel()}, now=100.0))

        assert counts == {}
        assert directory.entries("simplex")[0].chat_id == "group:3"


def _entry(chat_id: str, name: str):
    from shishi.push.directory import ChannelEntry

    return ChannelEntry(platform="simplex", chat_id=chat_id, name=name, type="group")


# ---------------------------------------------------------------------------
# 寻址:直达 group:<id> + 目录名(DM 显示名)
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_direct_ref_parse(self):
        for ref in ["group:3", "group:0", "group:999999999"]:
            target = SimplexChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "simplex",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["group:abc", "Alice", "group:", "group:3x", "羊毛群", ""]:
            assert SimplexChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_via_explicit_registry(self, tmp_path):
        target = resolve_target("simplex:group:7", ChannelDirectory(tmp_path), platforms=PLATFORMS)

        assert (target.chat_id, target.resolved_from) == ("group:7", RESOLVED_DIRECT)

    def test_resolve_dm_name_via_directory(self, tmp_path):
        """DM 显示名经目录登记后按名寻址(不设直达,避免劫持别名)。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "simplex",
            [
                _dm_entry("Alice", "爱丽丝"),
                _dm_entry("Bob", "Bob"),
            ],
        )

        # 精确 chat_id 命中走 id 路径(目录四路径第 2 步)
        by_id = resolve_target("simplex:Alice", directory, platforms=PLATFORMS)
        assert (by_id.chat_id, by_id.resolved_from) == ("Alice", "directory_id")
        # 名称命中走 name 路径(第 3 步)
        by_name = resolve_target("simplex:爱丽丝", directory, platforms=PLATFORMS)
        assert (by_name.chat_id, by_name.resolved_from) == ("Alice", RESOLVED_DIRECTORY_NAME)


def _dm_entry(chat_id: str, name: str):
    from shishi.push.directory import ChannelEntry

    return ChannelEntry(platform="simplex", chat_id=chat_id, name=name, type="dm")
