"""Tests for 10-03-messaging-w3-longtail 组三 — yuanbao(extras 报错壳)。

事实(蓝本 Hermes ``gateway/platforms/yuanbao.py``):出站走常驻 WebSocket
网关(sign-token → AUTH_BIND → 心跳),无 one-shot HTTP API → 结构化
``dependency_missing`` 壳,不硬造出站。覆盖:发送恒报依赖缺失(附修复
指引)、classify_dead_error 恒瞬态、直达三形态(direct:/group:/裸 id)、
别名/前缀寻址、DirectoryDiscoverUnsupported、协议契约。

No pytest-asyncio: async calls run through ``asyncio.run``;零网络 I/O。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.targets import RESOLVED_DIRECT, resolve_target
from shishi.push.yuanbao import INSTALL_HINT, YuanbaoChannel

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestShell:
    def test_send_always_reports_dependency_missing(self):
        channel = YuanbaoChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"
        # 蓝本事实 + 修复指引都在文案里(vision/ocr dependency_missing 范式)。
        assert "WebSocket" in str(excinfo.value)
        assert INSTALL_HINT in str(excinfo.value)

    def test_shell_error_is_never_dead(self):
        """依赖缺失是环境态,不是对象不可达:恒瞬态,不进死信。"""
        channel = YuanbaoChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(YuanbaoChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("yuanbao", [], now=1.0)
        counts = _run(directory.refresh({"yuanbao": YuanbaoChannel()}, now=100.0))
        assert counts == {}


class TestAddressing:
    def test_direct_ref_parse(self):
        """蓝本 chat_id 三形态:direct:{account_id} / group:{group_code} / 裸 id。"""
        for ref in ["direct:abc-123", "group:98765", "plain-id-1"]:
            target = YuanbaoChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "yuanbao",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["主人会话", "direct:", "带 空格", ""]:
            assert YuanbaoChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "yuanbao:group:98765",
            ChannelDirectory(tmp_path),
            platforms={"yuanbao": YuanbaoChannel},
        )
        assert (target.platform, target.chat_id) == ("yuanbao", "group:98765")

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("yuanbao", "direct:abc-123", "主人")
        directory.set_alias("yuanbao", "group:98765", "羊毛群")

        exact = resolve_target(
            "yuanbao:主人", directory, platforms={"yuanbao": YuanbaoChannel}
        )
        assert (exact.chat_id, exact.resolved_from) == ("direct:abc-123", "directory_name")

        prefix = resolve_target(
            "yuanbao:羊毛", directory, platforms={"yuanbao": YuanbaoChannel}
        )
        assert (prefix.chat_id, prefix.resolved_from) == ("group:98765", "directory_prefix")


class TestProtocol:
    def test_channel_protocol_conformance(self):
        channel = YuanbaoChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "yuanbao"
        assert channel.supports_targeting is True
