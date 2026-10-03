"""Tests for 10-03-messaging-w2-platforms 步骤 2 — 钉钉适配器。

覆盖(prd 验收 1/2 的钉钉面):裸 webhook 路径(蓝本形态:msgtype=text、
errcode 判据)、加签路径(配 secret 即 HMAC-SHA256 timestamp+sign query,
不配逐字节裸)、errcode 分类(310000 族 → forbidden 硬失败;-1/超时 →
瞬态)、直达(完整 webhook URL)与别名/定向两条寻址路。

蓝本对照:Hermes ``plugins/platforms/dingtalk/adapter.py`` 的
``_standalone_send``(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写
+ 加签增量(标准钉钉算法)。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``。
All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from myia.push import DingTalkChannel, SendContext
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target

WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc123def456"
CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "query": {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()},
        "body": json.loads(request.content.decode("utf-8")),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> DingTalkChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_DINGTALK_WEBHOOK")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return DingTalkChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="dingtalk", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def webhook_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_DINGTALK_WEBHOOK", WEBHOOK)


# ---------------------------------------------------------------------------
# 裸 webhook 路径(蓝本形态)
# ---------------------------------------------------------------------------


class TestBareWebhook:
    def test_posts_text_payload_exactly(self, webhook_env):
        """蓝本形态:POST body == {"msgtype": "text", "text": {"content": …}}。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == WEBHOOK  # 不加签 = URL 原样(逐字节裸)
        assert calls[0]["body"] == {
            "msgtype": "text",
            "text": {"content": calls[0]["body"]["text"]["content"]},
        }
        content = calls[0]["body"]["text"]["content"]
        assert "羊毛" in content and "https://x/1" in content

    def test_default_env_ref_is_blueprint_name(self, webhook_env, monkeypatch):
        """缺省引用 = 蓝本同名 env DINGTALK_WEBHOOK_URL。"""
        monkeypatch.setenv("DINGTALK_WEBHOOK_URL", WEBHOOK)
        calls: list[dict] = []
        channel = DingTalkChannel(client=httpx.AsyncClient(transport=_mock(calls)))

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["url"] == WEBHOOK

    def test_webhook_env_missing_is_structured_error(self, monkeypatch):
        monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
        calls: list[dict] = []
        channel = DingTalkChannel(client=httpx.AsyncClient(transport=_mock(calls)))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    @pytest.mark.parametrize(
        "bad",
        [
            "https://evil.example.com/robot/send?access_token=x",  # host 不符
            "oapi.dingtalk.com/robot/send?access_token=x",  # 缺 scheme
            "https://oapi.dingtalk.com/robot/send",  # 缺 query
            "羊毛群",  # 人类别名名:不是 URL,直达/兜底都不得放行
        ],
    )
    def test_non_webhook_form_rejected(self, webhook_env, bad):
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(bad))))
        assert excinfo.value.code == "invalid_credential_ref"

    def test_template_render_used_when_configured(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["body"]["text"]["content"] == "2026-10-03 1 条"


# ---------------------------------------------------------------------------
# 加签路径(MYIA 增量):配 secret 走加签、不配走裸
# ---------------------------------------------------------------------------


class TestSigning:
    def test_secret_config_appends_timestamp_and_sign(self, webhook_env, monkeypatch):
        """配 secret 即加签:标准算法(官方文档)可由测试独立复算验证。"""
        monkeypatch.setenv("MYIA_TEST_DINGTALK_SECRET", "SECxxxxxxxx")
        calls: list[dict] = []
        channel = _channel(
            calls,
            secret_ref="env:MYIA_TEST_DINGTALK_SECRET",
            clock=lambda: 1_762_000_000.0,  # 钉死时间源:timestamp_ms 确定性
        )

        _run(channel.send([{"title": "t"}], CONTEXT))

        query = calls[0]["query"]
        assert query["access_token"] == "abc123def456"
        assert query["timestamp"] == "1762000000000"
        expected_sign = base64.b64encode(
            hmac.new(
                b"SECxxxxxxxx",
                f"1762000000000\nSECxxxxxxxx".encode(),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        assert query["sign"] == expected_sign  # 官方算法逐字节复算

    def test_without_secret_url_stays_bare(self, webhook_env):
        """不配 secret = 蓝本裸 webhook:URL 无 timestamp/sign(双路单测)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert "timestamp" not in calls[0]["query"]
        assert "sign" not in calls[0]["query"]

    def test_secret_resolution_failure_is_structured(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, secret_ref="env:MYIA_TEST_DINGTALK_MISSING")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"


# ---------------------------------------------------------------------------
# 寻址:定向优先 / 直达解析 / 别名
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_webhook_overrides_legacy(self, webhook_env, monkeypatch):
        """定向优先:别名登记的另一个机器人 webhook 覆盖 legacy target。"""
        monkeypatch.setenv("MYIA_TEST_DINGTALK_WEBHOOK", WEBHOOK)
        other = "https://oapi.dingtalk.com/robot/send?access_token=zzz999"
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target(other))))

        assert calls[0]["query"]["access_token"] == "zzz999"

    def test_direct_ref_parse(self):
        """直达:完整官方 webhook URL 命中;其余(名称/他站 URL)回落目录。"""
        assert DingTalkChannel.parse_direct_ref(WEBHOOK) is not None
        assert DingTalkChannel.parse_direct_ref("  " + WEBHOOK + "  ") is not None
        assert DingTalkChannel.parse_direct_ref("https://evil.example.com/x?y=1") is None
        assert DingTalkChannel.parse_direct_ref("游戏群") is None

    def test_resolve_via_registry_bypasses_directory(self, tmp_path):
        target = resolve_target(f"dingtalk:{WEBHOOK}", ChannelDirectory(tmp_path))

        assert (target.platform, target.chat_id) == ("dingtalk", WEBHOOK)
        assert target.resolved_from == RESOLVED_DIRECT

    def test_alias_registered_webhook_resolves_by_name(self, tmp_path):
        """别名路:登记「游戏群 → 完整 webhook」后按名寻址(目录 chat_id 路径)。"""
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("dingtalk", WEBHOOK, "游戏群")

        target = resolve_target("dingtalk:游戏群", directory)

        assert target.chat_id == WEBHOOK
        assert target.resolved_from == "directory_name"


# ---------------------------------------------------------------------------
# errcode 分类(310000 族 → forbidden;-1/超时 → 瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        "errmsg",
        [
            "sign not match",  # 加签不匹配
            "keywords not in content",  # 关键词未命中
            "ip 1.2.3.4 not in whitelist",  # IP 白名单
        ],
    )
    def test_310000_family_marks_forbidden(self, webhook_env, errmsg):
        """官方表把安全校验失败统一记 310000(不同 errmsg)→ 硬失败。"""
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(200, json={"errcode": 310000, "errmsg": errmsg})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "errcode=310000" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_busy_errcode_minus_one_is_transient(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(200, json={"errcode": -1, "errmsg": "系统繁忙"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_http_500_is_transient(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(500, text="boom"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 500" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_success_response_is_invalid(self, webhook_env):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(200, text="ok"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"

    def test_transport_error_is_http_error(self, webhook_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        channel = DingTalkChannel(
            target="env:MYIA_TEST_DINGTALK_WEBHOOK",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None


# ---------------------------------------------------------------------------
# 目录语义(prd R4:无自动发现,不是失败)
# ---------------------------------------------------------------------------


class TestDirectorySemantics:
    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(DingTalkChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)

        counts = _run(directory.refresh({"dingtalk": DingTalkChannel()}, now=100.0))

        assert counts == {}
