"""Tests for 10-03-messaging-w3-longtail 组二 — irc 适配器(纯 stdlib 最小客户端)。

覆盖:一次性会话时序(PASS?/NICK/USER → 001 → NickServ? → JOIN →
PRIVMSG → QUIT)、433 昵称碰撞重试、JOIN 显式拒绝(403 → 死信 forbidden)、
注册超时/服务器断连、CRLF 注入防护(target 与正文)、字节预算分段与步进、
定向(context.target)优先、missing_target fail-fast、直达 ``#频道`` 与目录
名寻址、无自动发现语义、纯函数面(parse_irc_line/privmsg_budget/
chunk_paragraph/strip_markdown)。

蓝本对照:Hermes ``plugins/platforms/irc/adapter.py``(MIT)的
``_standalone_send`` 一次性出站路径——此处测 MYIA 重写(TrendAwareChannel
契约 + PushSendError 结构化错误)。

No pytest-asyncio: async calls run through ``asyncio.run``(同
test_push.py 约定)。All socket I/O goes through the injected ``connect``
(fake reader/writer)——零真连接。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

import myia.push.irc as irc_module
from myia.push import SendContext
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, ChannelEntry, DirectoryDiscoverUnsupported
from myia.push.irc import (
    IrcChannel,
    chunk_paragraph,
    parse_irc_line,
    privmsg_budget,
    strip_control_chars,
    strip_markdown,
)
from myia.push.targets import RESOLVED_DIRECT, RESOLVED_DIRECTORY_NAME, ChannelTarget, resolve_target

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

#: 真实 PLATFORMS 登记 is 集成步(push/__init__);本文件用显式注册表钉
#: 直达语义,登记后零改动生效。
PLATFORMS: dict[str, type] = {"irc": IrcChannel}


class FakeReader:
    """脚本化 IRC 服务端:逐行吐 CRLF 帧;耗尽后 EOF。"""

    def __init__(self, lines: list[str]) -> None:
        self._frames = [line.encode("utf-8") + b"\r\n" for line in lines]

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        if not self._frames:
            raise asyncio.IncompleteReadError(b"", separator)
        return self._frames.pop(0)

    async def read(self, n: int = -1) -> bytes:
        return self._frames.pop(0) if self._frames else b""


class HangingReader:
    """永不回帧(注册超时路径)。"""

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        await asyncio.sleep(30)

    async def read(self, n: int = -1) -> bytes:
        await asyncio.sleep(30)


class FakeWriter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.lines.append(data.decode("utf-8").rstrip("\r\n"))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class IrcHarness:
    """一次用例的连接桩:脚本化服务端行 + 捕获写流与 sleep。"""

    def __init__(self, server_lines: list[str]) -> None:
        self.writer = FakeWriter()
        self.reader = FakeReader(server_lines)
        self.connects: list[tuple[str, int, bool]] = []
        self.sleeps: list[float] = []

        async def connect(host: str, port: int, use_tls: bool) -> tuple[Any, Any]:
            self.connects.append((host, port, use_tls))
            return self.reader, self.writer

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.connect = connect
        self.sleep = sleep

    def channel(self, **kwargs: Any) -> IrcChannel:
        kwargs.setdefault("target", "env:MYIA_TEST_IRC_CHANNEL")
        kwargs.setdefault("server_ref", "env:MYIA_TEST_IRC_SERVER")
        kwargs.setdefault("nick_ref", "env:MYIA_TEST_IRC_NICK")
        kwargs["connect"] = self.connect
        kwargs["sleep"] = self.sleep
        return IrcChannel(**kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="irc", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def irc_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_IRC_SERVER", "irc.example.com")
    monkeypatch.setenv("MYIA_TEST_IRC_NICK", "myia")
    monkeypatch.setenv("MYIA_TEST_IRC_CHANNEL", "#test")


WELCOME = ":irc.example.com 001 myia-push :Welcome to the IRC Network"


# ---------------------------------------------------------------------------
# 一次性会话时序(蓝本 _standalone_send)
# ---------------------------------------------------------------------------


class TestSessionSequence:
    def test_channel_send_full_handshake(self, irc_env):
        """频道目标:NICK/USER → 001 → JOIN → PRIVMSG → QUIT → close。"""
        harness = IrcHarness([WELCOME, ":myia-push!~u@h JOIN :#test"])
        channel = harness.channel()

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        lines = harness.writer.lines
        assert lines[0] == "NICK myia-push"  # 一次性身份:基名 + -push 后缀
        assert lines[1].startswith("USER myia-push ")
        assert "JOIN #test" in lines
        privmsgs = "\n".join(line for line in lines if line.startswith("PRIVMSG #test :"))
        assert "羊毛" in privmsgs and "https://x/1" in privmsgs
        assert lines[-1] == "QUIT :MYIA push"
        assert harness.writer.closed
        assert harness.connects == [("irc.example.com", 6697, True)]  # TLS 缺省开
        # 内置版式两行(标题行 + 条目行)→ 一次段间步进;无 NickServ 等待。
        assert harness.sleeps == [0.3]

    def test_dm_target_skips_join(self, irc_env):
        """裸 nick DM 目标不 JOIN(蓝本同款),PRIVMSG 直投。"""
        harness = IrcHarness([WELCOME])
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("alice"))))

        assert not any(line.startswith("JOIN") for line in harness.writer.lines)
        assert any(line.startswith("PRIVMSG alice :") for line in harness.writer.lines)

    def test_context_target_overrides_legacy(self, irc_env):
        harness = IrcHarness([WELCOME, ":myia-push!~u@h JOIN :#other"])
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("#other"))))

        assert "JOIN #other" in harness.writer.lines

    def test_server_password_and_nickserv_are_sent(self, irc_env, monkeypatch):
        """可选 PASS 与 NickServ IDENTIFY 按引用在场发送(等待经 sleep 注入)。"""
        monkeypatch.setenv("MYIA_TEST_IRC_PASS", "s3cret")
        monkeypatch.setenv("MYIA_TEST_IRC_NICKSERV", "hunter2")
        harness = IrcHarness([WELCOME, ":myia-push!~u@h JOIN :#test"])
        channel = harness.channel(
            server_password_ref="env:MYIA_TEST_IRC_PASS",
            nickserv_password_ref="env:MYIA_TEST_IRC_NICKSERV",
        )

        _run(channel.send([{"title": "t"}], CONTEXT))

        lines = harness.writer.lines
        assert lines[0] == "PASS s3cret"
        assert "PRIVMSG NickServ :IDENTIFY hunter2" in lines
        assert 2.0 in harness.sleeps  # NickServ 生效等待(蓝本同款)

    def test_missing_target_fails_fast(self, irc_env):
        """两条寻址路径均缺席 → missing_target(零连接)。"""
        harness = IrcHarness([])
        channel = harness.channel(target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert harness.connects == []

    def test_connect_failure_is_transport_error(self, irc_env):
        async def connect(host: str, port: int, use_tls: bool) -> tuple[Any, Any]:
            raise OSError("connection refused")

        channel = IrcChannel(
            target="env:MYIA_TEST_IRC_CHANNEL",
            server_ref="env:MYIA_TEST_IRC_SERVER",
            nick_ref="env:MYIA_TEST_IRC_NICK",
            connect=connect,
            sleep=_noop_sleep,
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None


async def _noop_sleep(seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# 协议错误路径(433 重试 / JOIN 拒绝 / 注册失败)
# ---------------------------------------------------------------------------


class TestProtocolErrors:
    def test_nick_collision_retries_with_suffix(self, irc_env):
        """433 → 换 ``-1`` 后缀重试;蓝本同款有界重试。"""
        harness = IrcHarness(
            [
                ":irc.example.com 433 * myia-push :Nickname is already in use",
                ":irc.example.com 001 myia-push-1 :Welcome",
                ":myia-push-1!~u@h JOIN :#test",
            ]
        )
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert "NICK myia-push-1" in harness.writer.lines

    def test_join_403_is_forbidden_dead(self, irc_env):
        """JOIN 403(频道不存在):原厂片段保留;403 → forbidden 死信(既有
        403→forbidden 规则,IRC 语义为频道不可达,同为硬死信)。"""
        harness = IrcHarness(
            [WELCOME, ":irc.example.com 403 myia-push #nope :No such channel"]
        )
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("#nope"))))
        assert excinfo.value.code == "irc_api_error"
        assert "IRC 403 ERR_NOSUCHCHANNEL" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_bad_channel_key_is_transient(self, irc_env):
        """JOIN 475(错误频道 key)无 ASCII 死信 marker → 瞬态不标。"""
        harness = IrcHarness(
            [WELCOME, ":irc.example.com 475 myia-push #locked :Cannot join channel (+k)"]
        )
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("#locked"))))
        assert "IRC 475 ERR_BADCHANNELKEY" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_password_mismatch_is_transient(self, irc_env):
        """464(服务器密码错)是配置错误,不是对象级死信 → 不标。"""
        harness = IrcHarness([":irc.example.com 464 * :Password incorrect"])
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "IRC 464 ERR_PASSWDMISMATCH" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_registration_timeout_is_structured(self, irc_env, monkeypatch):
        monkeypatch.setattr(irc_module, "REGISTER_TIMEOUT_SECONDS", 0.05)
        writer = FakeWriter()

        async def connect(host: str, port: int, use_tls: bool) -> tuple[Any, Any]:
            return HangingReader(), writer

        channel = IrcChannel(
            target="env:MYIA_TEST_IRC_CHANNEL",
            server_ref="env:MYIA_TEST_IRC_SERVER",
            nick_ref="env:MYIA_TEST_IRC_NICK",
            connect=connect,
            sleep=_noop_sleep,
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "irc_api_error"
        assert "注册超时" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_server_closes_during_registration(self, irc_env):
        harness = IrcHarness([])  # 零帧即 EOF
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "服务器关闭连接" in str(excinfo.value)

    def test_ping_during_join_gets_pong(self, irc_env):
        """等待期间的服务端 PING 必须应答 PONG(否则被踢,蓝本 pump 同款)。"""
        harness = IrcHarness(
            [
                WELCOME,
                "PING :irc.example.com",
                ":myia-push!~u@h JOIN :#test",
            ]
        )
        channel = harness.channel()

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert "PONG :irc.example.com" in harness.writer.lines


# ---------------------------------------------------------------------------
# 注入防护与分段(蓝本 _strip_irc_control_chars / _chunk_paragraph)
# ---------------------------------------------------------------------------


class TestInjectionAndChunking:
    def test_crlf_in_target_is_rejected(self, irc_env):
        """target 内嵌换行 → 结构化拒绝(IRC 命令注入向量)。"""
        harness = IrcHarness([WELCOME])
        channel = harness.channel()

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("#a\r\nQUIT"))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert harness.connects == []

    def test_crlf_in_content_never_creates_rogue_commands(self, irc_env):
        """正文里的 CR/LF 无法伪造协议命令:内容只可能出现在
        ``PRIVMSG <目标> :`` 载荷内(蓝本先分行再逐行剥 CR/LF 的同款防线)。"""
        harness = IrcHarness([WELCOME, ":myia-push!~u@h JOIN :#test"])
        channel = harness.channel()

        _run(
            channel.send(
                [{"title": "正常标题\r\nPRIVMSG #evil :pwned\r\nJOIN #evil", "url": "https://x/1"}],
                CONTEXT,
            )
        )

        lines = harness.writer.lines
        assert all("\r" not in line and "\n" not in line for line in lines)  # 一写一行
        # 伪造命令全部被吞进 PRIVMSG 载荷:不存在以 PRIVMSG #evil/JOIN 开头的行
        assert not any(line.startswith(("PRIVMSG #evil", "JOIN #evil", "QUIT :pwned")) for line in lines)
        payload = "\n".join(line.split("PRIVMSG #test :", 1)[1] for line in lines if line.startswith("PRIVMSG #test :"))
        assert "pwned" in payload and "#evil" in payload  # 内容不丢,只在载荷内

    def test_long_content_chunks_with_pacing(self, irc_env):
        """超行预算(510 字节 - PRIVMSG 开销)自动多行,段间 0.3s 步进。"""
        harness = IrcHarness([WELCOME, ":myia-push!~u@h JOIN :#test"])
        channel = harness.channel()

        _run(channel.send([{"title": "长" * 2000}], CONTEXT))

        privmsgs = [line for line in harness.writer.lines if line.startswith("PRIVMSG #test :")]
        assert len(privmsgs) > 1
        for line in privmsgs:
            payload = line.split("PRIVMSG #test :", 1)[1]
            assert len(payload.encode("utf-8")) <= privmsg_budget("#test")
        assert harness.sleeps.count(0.3) == len(privmsgs) - 1  # 段间步进,尾段不等

    def test_template_render_failure_is_structured(self, irc_env):
        harness = IrcHarness([])
        channel = harness.channel(template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"

    def test_blank_render_is_invalid_response(self, irc_env):
        """渲染输出清洗后无可见文本 → 结构化报错,不发 PRIVMSG。"""
        harness = IrcHarness([WELCOME])
        channel = harness.channel(template="{{ '' }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"
        assert not any(line.startswith("PRIVMSG #test") for line in harness.writer.lines)


class TestPureHelpers:
    """蓝本纯函数面(解析/预算/分段/降级)。"""

    def test_parse_irc_line_variants(self):
        assert parse_irc_line("PING :srv") == {
            "prefix": "",
            "command": "PING",
            "params": ["srv"],
        }
        assert parse_irc_line(":srv 403 me #nope :No such channel") == {
            "prefix": "srv",
            "command": "403",
            "params": ["me", "#nope", "No such channel"],
        }
        assert parse_irc_line(":nick!user@host PRIVMSG #chan :hello there") == {
            "prefix": "nick!user@host",
            "command": "PRIVMSG",
            "params": ["#chan", "hello there"],
        }
        assert parse_irc_line("") == {"prefix": "", "command": "", "params": []}

    def test_privmsg_budget(self):
        assert privmsg_budget("#test") == 510 - (len("PRIVMSG #test :".encode()) + 2)
        assert privmsg_budget("#很长的频道名字") < privmsg_budget("#t")

    def test_chunk_paragraph_byte_budget_and_space_boundary(self):
        # CJK 按字节预算切:每块 ≤ limit 字节
        chunks = chunk_paragraph("长" * 600, 100)
        assert len(chunks) > 1
        assert all(len(chunk.encode("utf-8")) <= 100 for chunk in chunks)
        assert "".join(chunks) == "长" * 600  # 无字符丢失
        # 空格边界优先:ASCII 长串在词间切,不切单词内部
        chunks = chunk_paragraph("word " * 60, 50)
        assert all(len(chunk.encode()) <= 50 for chunk in chunks)
        joined = " ".join(part.strip() for part in chunks)
        assert joined.replace(" ", "") == ("word" * 60)  # 字符守恒(空格容差)

    def test_chunk_paragraph_short_passthrough(self):
        assert chunk_paragraph("短文本", 100) == ["短文本"]
        assert chunk_paragraph("", 100) == []

    def test_strip_control_chars(self):
        assert strip_control_chars("a\r\nb\x00c") == "a  bc"

    def test_strip_markdown(self):
        assert strip_markdown("**bold** and *italic*") == "bold and italic"
        assert strip_markdown("[标题](https://x/1)") == "标题 (https://x/1)"
        assert strip_markdown("![图](https://x/i.png)") == "https://x/i.png"
        assert strip_markdown("`code`") == "code"


# ---------------------------------------------------------------------------
# 寻址:直达 #频道 + 目录名;无自动发现
# ---------------------------------------------------------------------------


class TestAddressingAndDirectory:
    def test_direct_ref_parse(self):
        """``#频道``/``&频道``/``+频道`` 直达;裸 nick 与中文别名回落目录。"""
        for ref in ["#libera", "&local", "+noisy"]:
            target = IrcChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "irc",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["alice", "羊毛群", "#a,b", "#a b", "", "#"]:
            assert IrcChannel.parse_direct_ref(ref) is None

    def test_resolve_direct_via_explicit_registry(self, tmp_path):
        target = resolve_target("irc:#libera", ChannelDirectory(tmp_path), platforms=PLATFORMS)

        assert (target.chat_id, target.resolved_from) == ("#libera", RESOLVED_DIRECT)

    def test_resolve_via_directory_name(self, tmp_path):
        """裸 nick DM 经目录登记后按名寻址(不设直达避免劫持别名)。"""
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "irc",
            [ChannelEntry(platform="irc", chat_id="#行情", name="行情台", type="group")],
        )

        target = resolve_target("irc:行情台", directory, platforms=PLATFORMS)
        assert (target.chat_id, target.resolved_from) == ("#行情", RESOLVED_DIRECTORY_NAME)

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(IrcChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("irc", [], now=1.0)

        counts = _run(directory.refresh({"irc": IrcChannel()}, now=100.0))

        assert counts == {}
