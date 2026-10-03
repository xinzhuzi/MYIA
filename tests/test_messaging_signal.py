"""Tests for 10-03-messaging-w3-longtail 组二 — signal 适配器(extras 壳)。

PRD R3 硬约束:出站依赖 signal-cli 守护进程(外部系统级依赖),通道只进
extras 并结构化报错,不进核心。覆盖:``send()`` 恒抛 ``dependency_missing``
(附安装命令 + 守护进程指引;依赖门先于凭据解析)、依赖错误不标死信、
直达 ``+手机号``(E.164)与目录名寻址、无自动发现语义、Channel 协议面
(TrendAwareChannel 子类 / name / supports_targeting)。

蓝本对照:Hermes ``gateway/platforms/signal.py``(MIT)的 signal-cli
daemon HTTP 模式出站形态——extras 落地批次的实装依据,本壳钉契约面。

No pytest-asyncio: async calls run through ``asyncio.run``(同
test_push.py 约定)。壳无网络 I/O,零 mock 面。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from myia.push import SendContext
from myia.push.base import Channel, PushSendError, TrendAwareChannel
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.signal import INSTALL_COMMAND, SignalChannel
from myia.push.targets import RESOLVED_DIRECT, RESOLVED_DIRECTORY_NAME, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

#: 真实 PLATFORMS 登记 is 集成步(push/__init__);本文件用显式注册表钉
#: 直达语义,登记后零改动生效。
PLATFORMS: dict[str, type] = {"signal": SignalChannel}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="signal", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


# ---------------------------------------------------------------------------
# extras 壳:依赖门先于一切(PRD R3)
# ---------------------------------------------------------------------------


class TestExtrasShell:
    def test_send_always_raises_dependency_missing(self):
        """send() 恒抛 dependency_missing:绝不假装可用,绝不发请求。"""
        channel = SignalChannel(target="env:SIGNAL_CHAT")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"

    def test_error_carries_install_command_and_daemon_hint(self):
        channel = SignalChannel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        message = str(excinfo.value)
        assert "pip install 'myia[signal]'" in message  # 安装命令(vision/ocr.py 范式)
        assert "signal-cli" in message  # 守护进程指引

    def test_dependency_error_is_not_dead_letter(self):
        """依赖缺装是环境问题,不是对象级死信 → 不标(forbidden/not_found 均不沾)。"""
        with pytest.raises(PushSendError) as excinfo:
            _run(SignalChannel().send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_gate_fires_before_credential_resolution(self, monkeypatch):
        """依赖门先于凭据解析:target 引用指向缺失 env 也不改错误类别。"""
        monkeypatch.delenv("SIGNAL_CHAT", raising=False)
        channel = SignalChannel(target="env:SIGNAL_CHAT")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "dependency_missing"  # 不是 env_var_missing

    def test_channel_protocol_surface(self):
        """Channel 协议面:name/supports_targeting/TrendAwareChannel 基类。"""
        channel = SignalChannel()
        assert channel.name == "signal"
        assert channel.supports_targeting is True
        assert isinstance(channel, TrendAwareChannel)
        assert isinstance(channel, Channel)


# ---------------------------------------------------------------------------
# 寻址面(壳照常接线,extras 落地即用)
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_direct_ref_parse(self):
        """E.164 手机号(可选 ``+``,7-15 位)直达;群 id/显示名回落目录。"""
        for ref in ["+8613800138000", "13800138000", "+15551234567"]:
            target = SignalChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "signal",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["group", "张三", "+86-138-0013", "+abc", "", "1555123456789016"]:
            assert SignalChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_via_explicit_registry(self, tmp_path):
        target = resolve_target("signal:+8613800138000", ChannelDirectory(tmp_path), platforms=PLATFORMS)

        assert (target.chat_id, target.resolved_from) == ("+8613800138000", RESOLVED_DIRECT)

    def test_resolve_via_directory_name(self, tmp_path):
        from myia.push.directory import ChannelEntry

        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "signal",
            [
                ChannelEntry(platform="signal", chat_id="+8613800138000", name="张三", type="dm"),
                ChannelEntry(platform="signal", chat_id="a2V5Z3JvdXBpZA==", name="行情群", type="group"),
            ],
        )

        target = resolve_target("signal:张三", directory, platforms=PLATFORMS)
        assert (target.chat_id, target.resolved_from) == ("+8613800138000", RESOLVED_DIRECTORY_NAME)


# ---------------------------------------------------------------------------
# 目录语义(无自动发现,不是失败)
# ---------------------------------------------------------------------------


class TestDirectorySemantics:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(SignalChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("signal", [], now=1.0)

        counts = _run(directory.refresh({"signal": SignalChannel()}, now=100.0))

        assert counts == {}
