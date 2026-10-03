"""Tests for 10-03-messaging-w3-longtail 组三 — raft(extras 报错壳)。

事实(蓝本 Hermes ``plugins/platforms/raft/adapter.py``):读写经 Raft CLI
子进程 + 本地唤醒桥(蓝本 install_hint:https://raft.build),无 one-shot
HTTP API → 结构化 ``dependency_missing`` 壳,不硬造出站。覆盖:发送恒报
依赖缺失、classify_dead_error 恒瞬态、直达钩子有意缺席(蓝本 chat_id 形态
无公开成文约束,不猜形态)、别名/前缀寻址、DirectoryDiscoverUnsupported、
协议契约。

No pytest-asyncio: async calls run through ``asyncio.run``;零网络 I/O。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.raft import INSTALL_HINT, RaftChannel
from shishi.push.targets import resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestShell:
    def test_send_always_reports_dependency_missing(self):
        channel = RaftChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"
        # 蓝本事实(Raft CLI 子进程 + 唤醒桥)+ 修复指引都在文案里。
        assert "Raft CLI" in str(excinfo.value)
        assert INSTALL_HINT in str(excinfo.value)
        assert "raft.build" in str(excinfo.value)

    def test_shell_error_is_never_dead(self):
        channel = RaftChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(RaftChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("raft", [], now=1.0)
        counts = _run(directory.refresh({"raft": RaftChannel()}, now=100.0))
        assert counts == {}


class TestAddressing:
    def test_direct_ref_hook_absent_by_design(self):
        """蓝本 chat_id 是运行期会话标识(形态无公开约束):不猜形态,
        直达钩子有意缺席——寻址回落目录别名(可选能力钩子语义)。"""
        assert getattr(RaftChannel, "parse_direct_ref", None) is None

    def test_alias_and_prefix_addressing(self, tmp_path):
        """无直达形态的平台仍可寻址:别名文件登记,目录精确名/唯一前缀命中。"""
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("raft", "session-alpha", "主工作区")
        directory.set_alias("raft", "session-beta", "羊毛工作区")

        exact = resolve_target("raft:主工作区", directory, platforms={"raft": RaftChannel})
        assert (exact.chat_id, exact.resolved_from) == ("session-alpha", "directory_name")

        prefix = resolve_target("raft:羊毛", directory, platforms={"raft": RaftChannel})
        assert (prefix.chat_id, prefix.resolved_from) == ("session-beta", "directory_prefix")

    def test_unregistered_name_is_structured_error(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("raft", "session-alpha", "主工作区")
        with pytest.raises(Exception) as excinfo:  # TargetResolveError(ValueError 族)
            resolve_target("raft:不存在", directory, platforms={"raft": RaftChannel})
        assert "无法解析" in str(excinfo.value)


class TestProtocol:
    def test_channel_protocol_conformance(self):
        channel = RaftChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "raft"
        assert channel.supports_targeting is True
