"""Tests for 10-03-messaging-w3-longtail 组三 — buzz(extras 报错壳)。

事实(蓝本 Hermes ``plugins/platforms/buzz/adapter.py``):中继读写经
``buzz`` CLI 子进程(Nostr 签名),无 one-shot HTTP API → 结构化
``dependency_missing`` 壳,不硬造出站。覆盖:发送恒报依赖缺失(附 buzz CLI
修复指引)、classify_dead_error 恒瞬态、直达频道 UUID、别名/前缀寻址、
DirectoryDiscoverUnsupported、协议契约。

No pytest-asyncio: async calls run through ``asyncio.run``;零网络 I/O。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shishi.push.base import Channel, PushSendError, SendContext
from shishi.push.buzz import INSTALL_HINT, BuzzChannel
from shishi.push.delivery import classify_dead_error
from shishi.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from shishi.push.targets import RESOLVED_DIRECT, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

_UUID = "0f0e0d0c-0b0a-4909-8807-060504030201"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestShell:
    def test_send_always_reports_dependency_missing(self):
        channel = BuzzChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"
        # 蓝本事实(buzz CLI 子进程)+ 修复指引都在文案里。
        assert "buzz CLI" in str(excinfo.value)
        assert INSTALL_HINT in str(excinfo.value)
        assert "block/buzz" in str(excinfo.value)

    def test_shell_error_is_never_dead(self):
        channel = BuzzChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(BuzzChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("buzz", [], now=1.0)
        counts = _run(directory.refresh({"buzz": BuzzChannel()}, now=100.0))
        assert counts == {}


class TestAddressing:
    def test_direct_ref_parse(self):
        """直达:频道 UUID(蓝本 BUZZ_CHANNELS 同一形态);大小写归一。"""
        target = BuzzChannel.parse_direct_ref(_UUID.upper())
        assert target is not None
        assert (target.platform, target.chat_id, target.resolved_from) == (
            "buzz",
            _UUID,
            RESOLVED_DIRECT,
        )
        for ref in ["羊毛频道", "not-a-uuid", "0f0e0d0c-0b0a-4909-8807-06050403020", ""]:
            assert BuzzChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            f"buzz:{_UUID}", ChannelDirectory(tmp_path), platforms={"buzz": BuzzChannel}
        )
        assert (target.platform, target.chat_id) == ("buzz", _UUID)

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("buzz", _UUID, "主频道")
        directory.set_alias("buzz", "1f0e0d0c-0b0a-4909-8807-060504030201", "羊毛频道")

        exact = resolve_target("buzz:主频道", directory, platforms={"buzz": BuzzChannel})
        assert (exact.chat_id, exact.resolved_from) == (_UUID, "directory_name")

        prefix = resolve_target("buzz:羊毛", directory, platforms={"buzz": BuzzChannel})
        assert (prefix.chat_id, prefix.resolved_from) == (
            "1f0e0d0c-0b0a-4909-8807-060504030201",
            "directory_prefix",
        )


class TestProtocol:
    def test_channel_protocol_conformance(self):
        channel = BuzzChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "buzz"
        assert channel.supports_targeting is True
