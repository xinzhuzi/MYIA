"""Tests for 10-03-messaging-weixin-bridge 步骤 1 — 微信桥接通道。

覆盖(prd 验收 1):AC1 四态(mock runner:成功 / Hermes 缺失 /
token 过期 ``ret=-14`` / stale-session ``session not ready``)+ D3 错误
映射表全行(每行断言 ``classify_dead_error`` 为 None——零死信设计)、
``HTTP <ddd>`` 归一化不撞死信 marker、超时 kill、exit 2 用法漂移、
寻址(定向优先 / legacy env 引用 / 直达正则 / 目录无自动发现)与
``probe_bridge`` 三形态。

蓝本对照:Hermes ``hermes_cli/send_cmd.py`` + ``gateway/platforms/
weixin.py``(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 桥接重写,
不 vendor 上游代码。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``
(同 test_push.py 约定)。Mock runner 走构造器 ``runner`` 注入点,不触
真 Hermes;仅两条真实子进程冒烟(回显脚本成功 / sleep 超时 kill)。
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import replace
from typing import Any

import pytest

from myia.push import SendContext
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget
from myia.push.weixin import (
    DEFAULT_BRIDGE_TIMEOUT_SECONDS,
    DEFAULT_HERMES_BIN,
    BridgeTimeoutError,
    WeixinChannel,
    normalize_error_text,
    probe_bridge,
)

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")

PEER = "peer123@im.wechat"

ITEMS = [{"title": "羊毛", "url": "https://x/1"}, {"title": "另一条"}]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(
        platform="weixin", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
    )


def _mock_runner(result: tuple[int, str, str], calls: list[dict] | None = None):
    """固定应答的 runner:记录 (argv, input, timeout) 供断言。"""

    async def run(argv, input_text, timeout):
        if calls is not None:
            calls.append({"argv": list(argv), "input": input_text, "timeout": timeout})
        return result

    return run


def _raising_runner(exc: BaseException, calls: list[dict] | None = None):
    async def run(argv, input_text, timeout):
        if calls is not None:
            calls.append({"argv": list(argv), "input": input_text, "timeout": timeout})
        raise exc

    return run


def _channel(**kwargs: Any) -> WeixinChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_WEIXIN_PEER")
    return WeixinChannel(**kwargs)


def _write_executable(path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def peer_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_WEIXIN_PEER", PEER)


# ---------------------------------------------------------------------------
# AC1 四态:成功 / Hermes 缺失 / token 过期 / session-not-ready
# ---------------------------------------------------------------------------


class TestBridgeFourStates:
    def test_success_invokes_hermes_send_with_stdin_body(self, peer_env):
        """exit 0 + ``{"success": true}`` → 放行;argv 形状 + 正文走 stdin。"""
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh",
            timeout=7.5,
            runner=_mock_runner(
                (0, '{"success": true, "platform": "weixin"}', ""), calls
            ),
        )

        _run(channel.send(ITEMS, CONTEXT))

        assert calls[0]["argv"] == [
            "/bin/sh",
            "send",
            "--to",
            f"weixin:{PEER}",
            "--json",
            "--file",
            "-",
        ]
        assert calls[0]["timeout"] == 7.5
        # 正文经 stdin:内置版式 = card_title 行 + 每条「▸ 标题 · URL」行
        assert "羊毛" in calls[0]["input"] and "https://x/1" in calls[0]["input"]

    def test_hermes_bin_missing_is_structured_unavailable(self, peer_env):
        """bin 不存在:发送期预检 → ``bridge_unavailable`` 带修复指引,
        不触 runner(R2:无 Hermes 环境的如实结构化错误)。"""
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/nonexistent/hermes", runner=_mock_runner((0, "{}", ""), calls)
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_unavailable"
        assert "weixin_hermes_bin" in str(excinfo.value)
        assert calls == []  # 预检先于子进程,零调用
        assert classify_dead_error(excinfo.value) is None

    def test_token_expired_ret14_maps_session_not_ready(self, peer_env):
        """tokenless 重发后仍会话过期(``ret=-14``)→ ``session_not_ready``
        + 结构化指引(R4:可修复态不标 dead)。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner(
                (
                    1,
                    '{"error": "Weixin send failed: iLink sendmessage error:'
                    ' ret=-14 errcode=None errmsg=session expired"}',
                    "",
                )
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "session_not_ready"
        assert "先让对方给 bot 发" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_stale_session_minus2_maps_session_not_ready(self, peer_env):
        """``-2`` stale-session 终态文案(``session not ready``)→
        ``session_not_ready`` 带指引,而非死信。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner(
                (
                    1,
                    '{"error": "Weixin send failed: iLink sendmessage session not ready:'
                    " ret=-2 errcode=-2 errmsg=unknown error"
                    ' — the user must send the bot a message first (or re-pair)"}',
                    "",
                )
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "session_not_ready"
        assert "重新扫码" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# D3 错误映射表全行(零死信断言)
# ---------------------------------------------------------------------------


class TestErrorMappingTable:
    """上表每一行:期望 code + ``classify_dead_error`` 必得 None。"""

    ROWS = [
        (
            "token_missing",
            1,
            '{"error": "Weixin token missing. Configure WEIXIN_TOKEN or platforms.weixin.token."}',
            "bridge_unavailable",
        ),
        (
            "account_id_missing",
            1,
            '{"error": "Weixin account ID missing. Configure WEIXIN_ACCOUNT_ID or platforms.weixin.extra.account_id."}',
            "bridge_unavailable",
        ),
        (
            "requirements_unmet",
            1,
            '{"error": "Weixin requirements not met. Need aiohttp + cryptography."}',
            "bridge_unavailable",
        ),
        (
            "adapter_unavailable",
            1,
            '{"error": "Weixin adapter not available."}',
            "bridge_unavailable",
        ),
        (
            "session_not_ready_text",
            1,
            '{"error": "iLink sendmessage session not ready: ret=-2 errcode=-2 errmsg=prepare failed"}',
            "session_not_ready",
        ),
        (
            "ret_minus_14",
            1,
            '{"error": "iLink sendmessage error: ret=-14 errcode=None errmsg=expired"}',
            "session_not_ready",
        ),
        (
            "errcode_minus_14",
            1,
            '{"error": "iLink sendmessage error: ret=None errcode=-14 errmsg=expired"}',
            "session_not_ready",
        ),
        (
            "rate_limited",
            1,
            '{"error": "Weixin send failed: iLink sendmessage rate limited; cooldown active for 30.0s"}',
            "weixin_send_error",
        ),
        (
            "ilink_other_error",
            1,
            '{"error": "Weixin send failed: iLink sendmessage error: ret=-1 errcode=None errmsg=busy"}',
            "weixin_send_error",
        ),
        ("invalid_json_stdout", 1, "not-json-at-all", "weixin_send_error"),
        ("empty_stdout", 1, "", "weixin_send_error"),
        ("usage_exit_2", 2, "", "bridge_usage_error"),
    ]

    @pytest.mark.parametrize(
        "label,exit_code,stdout,expected", ROWS, ids=[r[0] for r in ROWS]
    )
    def test_row_maps_to_expected_code_and_never_dead(
        self, peer_env, label, exit_code, stdout, expected
    ):
        stderr = (
            "hermes send: --to PLATFORM[:channel[:thread]] is required"
            if exit_code == 2
            else ""
        )
        channel = _channel(
            hermes_bin="/bin/sh", runner=_mock_runner((exit_code, stdout, stderr))
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == expected
        assert classify_dead_error(excinfo.value) is None  # 零死信:全表无一 dead

    def test_invalid_json_carries_stderr_fragment(self, peer_env):
        """stdout 非法 JSON:错误文案带原始片段(design D3 表行)。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((1, "{{broken", "hermes send: boom detail")),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "weixin_send_error"
        assert "boom detail" in str(excinfo.value)

    def test_exit_0_with_error_payload_still_maps(self, peer_env):
        """防御:exit 0 但 payload 带 error(上游契约不该出现)→ 按错误表映射。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((0, '{"error": "Weixin adapter not available."}', "")),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_unavailable"


class TestTimeoutAndUsage:
    def test_bridge_timeout_is_transient(self, peer_env):
        """runner 抛 :class:`BridgeTimeoutError` → ``bridge_timeout`` 瞬态。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_raising_runner(
                BridgeTimeoutError("hermes send 超过 120s 未退出,子进程已终止")
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_timeout"
        assert classify_dead_error(excinfo.value) is None

    def test_default_timeout_is_120s_module_constant(self):
        """design D0:120s 模块常量,不设配置口。"""
        assert DEFAULT_BRIDGE_TIMEOUT_SECONDS == 120.0
        assert _channel()._timeout == DEFAULT_BRIDGE_TIMEOUT_SECONDS

    def test_usage_exit_2_logs_error(self, peer_env, caplog):
        """exit 2 = MYIA argv 契约漂移(自家 bug):ERROR 级日志 + stderr 原文。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((2, "", "hermes send: --to is required\nExamples: …")),
        )

        with pytest.raises(PushSendError) as excinfo, caplog.at_level("ERROR"):
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_usage_error"
        assert "--to is required" in str(excinfo.value)
        assert any(r.levelname == "ERROR" for r in caplog.records)

    def test_runner_file_not_found_maps_unavailable(self, peer_env):
        """预检放过后子进程仍 FileNotFoundError(竞态)→ 同归
        ``bridge_unavailable`` 带指引。"""
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_raising_runner(
                FileNotFoundError(2, "No such file", "/fake/hermes")
            ),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_unavailable"
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# HTTP 归一化(design D3:防误伤 core 死信 marker)
# ---------------------------------------------------------------------------


class TestHttpNormalization:
    def test_normalize_error_text_unit(self):
        assert normalize_error_text(
            "iLink POST ilink/bot/sendmessage HTTP 404: gone"
        ) == ("iLink POST ilink/bot/sendmessage HTTP状态码４０４: gone")
        assert normalize_error_text("http 403 lower") == "HTTP状态码４０３ lower"
        assert normalize_error_text("no status here") == "no status here"

    @pytest.mark.parametrize("status", ["404", "403", "500"])
    def test_http_status_text_never_classifies_dead(self, peer_env, status):
        """``HTTP <ddd>`` 上游文本:归一后不撞 ``http 404``/裸 ``403``
        marker(零死信闭口:状态码数字转全角)。"""
        upstream = (
            "Weixin send failed: iLink POST ilink/bot/sendmessage"
            f" HTTP {status}: upstream said no"
        )
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((1, json.dumps({"error": upstream}), "")),
        )
        fullwidth = status.translate(
            str.maketrans("0123456789", "０１２３４５６７８９")
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "weixin_send_error"
        assert f"HTTP {status}" not in str(excinfo.value)  # ASCII 形已归一
        assert f"HTTP状态码{fullwidth}" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# 真实子进程冒烟(缺省 runner:asyncio 子进程 + 超时 kill)
# ---------------------------------------------------------------------------


class TestRealSubprocess:
    def test_default_runner_success_with_echo_script(self, tmp_path, peer_env):
        """真子进程端到端:回显 success JSON 的脚本 → 送达。"""
        script = tmp_path / "hermes-fake"
        _write_executable(
            script, '#!/bin/sh\necho \'{"success": true, "platform": "weixin"}\'\n'
        )
        channel = _channel(hermes_bin=str(script))

        _run(channel.send(ITEMS, CONTEXT))  # 不抛即成功

    def test_default_runner_timeout_kills_subprocess(self, tmp_path, peer_env):
        """真子进程超时:sleep 脚本 + 0.25s 超时 → kill + ``bridge_timeout``。"""
        script = tmp_path / "hermes-slow"
        _write_executable(script, "#!/bin/sh\nsleep 5\n")
        channel = _channel(hermes_bin=str(script), timeout=0.25)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_timeout"
        assert classify_dead_error(excinfo.value) is None

    def test_default_runner_missing_bin_raises_unavailable(self, peer_env):
        """预检捕获大多数缺 bin 场景;不可执行 bin(无 X 位)同样预检拒。"""
        channel = _channel(hermes_bin="/definitely/not/here/hermes")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "bridge_unavailable"


# ---------------------------------------------------------------------------
# 寻址:定向优先 / legacy env 引用 / 直达正则 / 目录无发现
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_overrides_legacy(self, peer_env):
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((0, '{"success": true}', ""), calls),
        )

        _run(channel.send(ITEMS, replace(CONTEXT, target=_target("other@chatroom"))))

        assert "--to" in calls[0]["argv"]
        assert (
            calls[0]["argv"][calls[0]["argv"].index("--to") + 1]
            == "weixin:other@chatroom"
        )

    def test_legacy_target_env_resolution(self, monkeypatch):
        """legacy target 引用发送期解析(env:WEIXIN_PEER_ID 风格)。"""
        monkeypatch.setenv("MYIA_TEST_WEIXIN_PEER", "legacy-peer_1@im.wechat")
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((0, '{"success": true}', ""), calls),
        )

        _run(channel.send(ITEMS, CONTEXT))

        assert (
            calls[0]["argv"][calls[0]["argv"].index("--to") + 1]
            == "weixin:legacy-peer_1@im.wechat"
        )

    def test_legacy_target_resolution_failure_is_structured(self, monkeypatch):
        monkeypatch.delenv("MYIA_TEST_WEIXIN_PEER", raising=False)
        channel = _channel(hermes_bin="/bin/sh", runner=_mock_runner((0, "{}", ""), []))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    def test_no_target_and_no_context_target_fails_fast(self):
        channel = WeixinChannel(
            hermes_bin="/bin/sh", runner=_mock_runner((0, "{}", ""), [])
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "missing_target"

    @pytest.mark.parametrize(
        "bad_peer",
        ["羊毛群", "peer@im wechat", "peer@", "weixin:peer@im.wechat", ""],
    )
    def test_malformed_peer_is_rejected_before_subprocess(self, peer_env, bad_peer):
        """形态校验保证 ``--to weixin:<peer>`` 段不破坏上游
        ``PLATFORM[:channel[:thread]]`` 解析(含 ``:``/空白的值即拒)。"""
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh", runner=_mock_runner((0, "{}", ""), calls)
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, replace(CONTEXT, target=_target(bad_peer))))
        assert excinfo.value.code == "invalid_credential_ref"
        assert calls == []

    def test_direct_ref_parse(self):
        """直达正则:DM/群形态命中,中文别名与坏形态回落目录四路径。"""
        for ref in ["peer123@im.wechat", "abc-1_x@chatroom", "x@im.wechat.qq.com"]:
            target = WeixinChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "weixin",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in [
            "羊毛群",
            "peer@im wechat",
            "@chatroom",
            "peer@",
            "a:b@im.wechat",
            "",
        ]:
            assert WeixinChannel.parse_direct_ref(ref) is None

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(WeixinChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)
        assert "--list weixin" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        """经 canonical refresh:无发现平台跳过、不计数、不算失败、桶不动。"""
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("weixin", [], now=1.0)  # 空桶占位(平台在场)

        counts = _run(directory.refresh({"weixin": WeixinChannel()}, now=100.0))

        assert counts == {}


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_user_template_replaces_builtin_layout(self, peer_env):
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh",
            template="{{ date }} 共 {{ items|length }} 条",
            runner=_mock_runner((0, '{"success": true}', ""), calls),
        )

        _run(channel.send(ITEMS, CONTEXT))

        assert calls[0]["input"] == "2026-10-03 共 2 条"

    def test_template_render_failure_is_structured(self, peer_env):
        channel = _channel(
            hermes_bin="/bin/sh",
            template="{{ no_such_helper() }}",
            runner=_mock_runner((0, "{}", ""), []),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send(ITEMS, CONTEXT))
        assert excinfo.value.code == "template_render_error"

    def test_long_body_not_prechunked(self, peer_env):
        """长文不预切——Hermes 侧 1800/2000 分块是它的本职(design D1)。"""
        calls: list[dict] = []
        channel = _channel(
            hermes_bin="/bin/sh",
            runner=_mock_runner((0, '{"success": true}', ""), calls),
        )
        items = [{"title": "长" * 6000, "url": "https://x/1"}]

        _run(channel.send(items, CONTEXT))

        assert len(calls[0]["input"]) > 6000  # 原样整条,不截不拆


# ---------------------------------------------------------------------------
# probe_bridge 三形态(design D2)
# ---------------------------------------------------------------------------


class TestProbeBridge:
    def _fake_home(self, tmp_path, *, bin_=True, accounts=True, sock=False):
        home = tmp_path / "hermes-home"
        if bin_:
            script = home / "hermes-agent" / ".hermes" / "bin" / "hermes"
            script.parent.mkdir(parents=True, exist_ok=True)
            _write_executable(script, "#!/bin/sh\n:")
        if accounts:
            acc = home / "weixin" / "accounts"
            acc.mkdir(parents=True, exist_ok=True)
            (acc / "abc@im.bot.json").write_text("{}", encoding="utf-8")
        if sock:
            (home / "gateway.sock").write_text("", encoding="utf-8")
        return home

    def test_all_signals_present(self, tmp_path, monkeypatch):
        home = self._fake_home(tmp_path, sock=True)
        monkeypatch.setenv("HERMES_HOME", str(home))
        # 显式传 bin:缺省路径指向真机 ~/.hermes,不密闭(本机装有 Hermes)
        bin_path = home / "hermes-agent" / ".hermes" / "bin" / "hermes"

        status = probe_bridge(str(bin_path))

        assert (status.available, status.reason, status.fix_hint) == (True, None, None)
        assert status.bin_found and status.weixin_configured and status.gateway_alive
        assert status.bin_path == str(bin_path)

    def test_bin_missing_reports_hermes_missing(self, tmp_path, monkeypatch):
        home = self._fake_home(tmp_path, bin_=False)
        monkeypatch.setenv("HERMES_HOME", str(home))

        status = probe_bridge(str(home / "hermes-agent" / ".hermes" / "bin" / "hermes"))

        assert status.available is False
        assert status.reason == "hermes_missing"
        assert "weixin_hermes_bin" in (status.fix_hint or "")
        assert status.bin_found is False and status.weixin_configured is True

    def test_accounts_missing_reports_not_configured(self, tmp_path, monkeypatch):
        home = self._fake_home(tmp_path, accounts=False)
        monkeypatch.setenv("HERMES_HOME", str(home))
        # bin 用真存在可执行文件(本机 /bin/sh),隔离「accounts 缺失」单因
        status = probe_bridge("/bin/sh")

        assert status.available is False
        assert status.reason == "weixin_not_configured"
        assert "gateway setup" in (status.fix_hint or "")
        assert status.gateway_alive is False  # 仅咨询信号,不参与 available

    def test_bin_override_and_payload_shape(self, tmp_path, monkeypatch):
        """``weixin_hermes_bin`` 覆写生效;payload 七键齐全(协议对账)。"""
        home = self._fake_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        custom = tmp_path / "custom-hermes"
        _write_executable(custom, "#!/bin/sh\n:")

        status = probe_bridge(str(custom))

        assert status.bin_path == str(custom)
        assert set(status.to_payload()) == {
            "available",
            "reason",
            "fix_hint",
            "bin_found",
            "weixin_configured",
            "gateway_alive",
            "bin_path",
        }

    def test_non_executable_bin_is_not_found(self, tmp_path, monkeypatch):
        """bin 文件在但无执行位(不可执行)→ 同 hermes_missing。"""
        home = self._fake_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        plain = tmp_path / "plain-hermes"
        plain.write_text("#!/bin/sh\n:", encoding="utf-8")  # 无 X 位
        plain.chmod(0o644)

        status = probe_bridge(str(plain))

        assert status.bin_found is False and status.available is False

    def test_default_bin_path_constant(self):
        """缺省 bin 常量 = 蓝本 launcher 实存路径(design D2 判据)。"""
        assert DEFAULT_HERMES_BIN == "~/.hermes/hermes-agent/.hermes/bin/hermes"
        assert os.path.isabs(os.path.expanduser(DEFAULT_HERMES_BIN))
