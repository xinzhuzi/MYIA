"""Tests for 10-03-messaging-w3-longtail 组三 — photon(extras 报错壳)。

事实(蓝本 Hermes ``plugins/platforms/photon/adapter.py`` + README):收发
均走 ``spectrum-ts`` SDK 的 gRPC 长流(经 Node sidecar),无 one-shot HTTP
API → 结构化 ``dependency_missing`` 壳,不硬造出站。覆盖:发送恒报依赖
缺失(附 sidecar 修复指引)、classify_dead_error 恒瞬态、直达三形态
(UUID space/GUID + E.164,蓝本 PHOTON_HOME_CHANNEL 文档)、别名/前缀寻址、
DirectoryDiscoverUnsupported、协议契约。

No pytest-asyncio: async calls run through ``asyncio.run``;零网络 I/O。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from myia.push.base import Channel, PushSendError, SendContext
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.photon import INSTALL_HINT, PhotonChannel
from myia.push.targets import RESOLVED_DIRECT, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

_GUID = "0f0e0d0c-0b0a-4909-8807-060504030201"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestShell:
    def test_send_always_reports_dependency_missing(self):
        channel = PhotonChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"
        # 蓝本事实(spectrum-ts gRPC 长流/sidecar)+ 修复指引都在文案里。
        assert "spectrum-ts" in str(excinfo.value)
        assert "sidecar" in str(excinfo.value)
        assert INSTALL_HINT in str(excinfo.value)

    def test_shell_error_is_never_dead(self):
        channel = PhotonChannel()
        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(PhotonChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("photon", [], now=1.0)
        counts = _run(directory.refresh({"photon": PhotonChannel()}, now=100.0))
        assert counts == {}


class TestAddressing:
    def test_direct_ref_parse(self):
        """直达:UUID(space id/DM GUID,大小写归一)与 E.164 号码双形态。"""
        uuid_target = PhotonChannel.parse_direct_ref(_GUID.upper())
        assert uuid_target is not None
        assert (uuid_target.platform, uuid_target.chat_id, uuid_target.resolved_from) == (
            "photon",
            _GUID,
            RESOLVED_DIRECT,
        )
        phone_target = PhotonChannel.parse_direct_ref("+15551234567")
        assert (phone_target.platform, phone_target.chat_id) == ("photon", "+15551234567")
        for ref in ["口袋提醒", "not-a-target", "+86138", ""]:
            assert PhotonChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_direct(self, tmp_path):
        target = resolve_target(
            "photon:+15551234567",
            ChannelDirectory(tmp_path),
            platforms={"photon": PhotonChannel},
        )
        assert (target.platform, target.chat_id) == ("photon", "+15551234567")

    def test_alias_and_prefix_addressing(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("photon", _GUID, "主人")
        directory.set_alias("photon", "+15551234567", "口袋提醒")

        exact = resolve_target("photon:主人", directory, platforms={"photon": PhotonChannel})
        assert (exact.chat_id, exact.resolved_from) == (_GUID, "directory_name")

        prefix = resolve_target("photon:口袋", directory, platforms={"photon": PhotonChannel})
        assert (prefix.chat_id, prefix.resolved_from) == ("+15551234567", "directory_prefix")


class TestProtocol:
    def test_channel_protocol_conformance(self):
        channel = PhotonChannel()
        assert isinstance(channel, Channel)
        assert channel.name == "photon"
        assert channel.supports_targeting is True
