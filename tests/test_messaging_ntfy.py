"""Tests for 10-03-messaging-w2-platforms 步骤 1 — ntfy 适配器。

覆盖(prd 验收 1 的 ntfy 面):one-shot POST 形态、鉴权头二形
(Bearer/Basic、缺省 env 缺席 = 无鉴权)、4096 截断、X-Markdown 随模板、
直达寻址(``ntfy:<topic>``)与别名/定向寻址两条路、错误分类
(HTTP 403 → forbidden、404 → not_found、429/5xx → 瞬态不标)。

蓝本对照:Hermes ``plugins/platforms/ntfy/adapter.py`` 的 send/
_standalone_send(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写。

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

from myia.push import NtfyChannel, SendContext
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import DirectoryDiscoverUnsupported
from myia.push.ntfy import MESSAGE_LIMIT, build_auth_header
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target
from myia.push.directory import ChannelDirectory

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": request.content.decode("utf-8"),
    }


def _mock(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"id": "msg1"})

    return httpx.MockTransport(handler)


def _channel(calls: list[dict], *, response: httpx.Response | None = None, **kwargs: Any) -> NtfyChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_NTFY_TARGET")
    # token_ref 缺省 None(无鉴权合法态);要测鉴权的用例显式传 env:MYIA_TEST_NTFY_TOKEN。
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock(calls, response=response))
    return NtfyChannel(client=client, **kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="ntfy", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def target_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_NTFY_TARGET", "https://ntfy.example.com/default-topic")


# ---------------------------------------------------------------------------
# one-shot POST 形态 + 鉴权头
# ---------------------------------------------------------------------------


class TestPublishShape:
    def test_legacy_target_posts_server_slash_topic(self, target_env, monkeypatch):
        monkeypatch.setenv("MYIA_TEST_NTFY_TOKEN", "tk_bearer_123")
        calls: list[dict] = []
        channel = _channel(calls, token_ref="env:MYIA_TEST_NTFY_TOKEN")

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        assert calls[0]["url"] == "https://ntfy.example.com/default-topic"
        assert calls[0]["headers"]["authorization"] == "Bearer tk_bearer_123"
        assert calls[0]["headers"]["content-type"] == "text/plain; charset=utf-8"
        assert "羊毛" in calls[0]["body"] and "https://x/1" in calls[0]["body"]
        assert "x-markdown" not in calls[0]["headers"]  # 内置版式纯文本

    def test_colon_token_uses_basic(self, target_env, monkeypatch):
        """蓝本同款二形:值含 ``:``(user:pass)→ Basic(base64)。"""
        import base64

        monkeypatch.setenv("MYIA_TEST_NTFY_TOKEN", "myuser:s3cret")
        calls: list[dict] = []
        channel = _channel(calls, token_ref="env:MYIA_TEST_NTFY_TOKEN")

        _run(channel.send([{"title": "t"}], CONTEXT))

        expected = base64.b64encode(b"myuser:s3cret").decode("ascii")
        assert calls[0]["headers"]["authorization"] == f"Basic {expected}"

    def test_no_token_env_means_no_auth_header(self, target_env):
        """缺省 env 未设 = 无鉴权(公共 topic 合法态,不报错不带头)。"""
        calls: list[dict] = []
        channel = _channel(calls)  # env:MYIA_TEST_NTFY_TOKEN 未设

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert "authorization" not in calls[0]["headers"]

    def test_explicit_token_ref_failure_is_structured_error(self, target_env):
        """显式 token 引用解析失败必须如实报错,绝不静默降级成无鉴权。"""
        calls: list[dict] = []
        channel = _channel(calls, token_ref="env:MYIA_TEST_NTFY_MISSING")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    def test_no_target_and_no_context_target_fails_fast(self, monkeypatch):
        """target 未配置且无定向对象:missing_target(fail-fast,绝不猜端点)。"""
        monkeypatch.delenv("MYIA_TEST_NTFY_TARGET", raising=False)
        calls: list[dict] = []
        channel = NtfyChannel(client=httpx.AsyncClient(transport=_mock(calls)))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert calls == []

    @pytest.mark.parametrize(
        "bad_target",
        [
            "ntfy.example.com/topic",  # 缺 scheme
            "ftp://ntfy.example.com/topic",  # 非 http(s)
            "https://ntfy.example.com",  # 缺 topic 段
            "https://ntfy.example.com/非法 topic!",
        ],
    )
    def test_malformed_target_value_is_rejected(self, monkeypatch, bad_target):
        monkeypatch.setenv("MYIA_TEST_NTFY_TARGET", bad_target)
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_credential_ref"


# ---------------------------------------------------------------------------
# 寻址:定向(context.target)优先 / legacy 兜底 / 直达解析
# ---------------------------------------------------------------------------


class TestAddressing:
    def test_context_target_topic_overrides_default(self, target_env):
        """定向优先:bare topic 覆盖 legacy target 的 topic 段,server 沿用。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("my-games"))))

        assert calls[0]["url"] == "https://ntfy.example.com/my-games"

    def test_context_target_full_url_wins_as_is(self, target_env):
        """定向值是整条 URL(server+topic 一起换)时原样投递——别名可登记
        跨 server 的对象,chat_id 即完整端点。"""
        calls: list[dict] = []
        channel = _channel(calls)

        _run(
            channel.send(
                [{"title": "t"}],
                replace(CONTEXT, target=_target("https://other.example.com/alt-topic")),
            )
        )

        assert calls[0]["url"] == "https://other.example.com/alt-topic"

    def test_targets_only_config_falls_back_to_public_server(self, monkeypatch):
        """最小配置(只配 targets、target 省缺)→ server 退公共 ntfy.sh
        (蓝本 DEFAULT_SERVER 同款)。"""
        monkeypatch.delenv("MYIA_TEST_NTFY_TARGET", raising=False)
        calls: list[dict] = []
        # target 显式 None + token_ref 缺省 env 未设 → 无鉴权
        channel = NtfyChannel(
            client=httpx.AsyncClient(transport=_mock(calls)), target=None, token_ref=None
        )

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("free-topic"))))

        assert calls[0]["url"] == "https://ntfy.sh/free-topic"

    def test_direct_ref_parse(self):
        """``ntfy:<topic>`` 直达:topic 字符集(字母/数字/-/_)命中,其余回落目录。"""
        for ref in ["my-topic", "Games_99", "a" * 64]:
            target = NtfyChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "ntfy",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["羊毛群", "has space", "a" * 65, "", "topic/other"]:
            assert NtfyChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_bypasses_directory(self, tmp_path):
        """``ntfy:mytopic`` 经真实 PLATFORMS 注册表直达,空目录也可寻址。"""
        target = resolve_target("ntfy:mytopic", ChannelDirectory(tmp_path))

        assert (target.platform, target.chat_id) == ("ntfy", "mytopic")
        assert target.resolved_from == RESOLVED_DIRECT

    def test_invalid_topic_target_fails_fast(self, target_env):
        """定向值既非合法 topic 也非 URL → 结构化报错(绝不猜端点)。"""
        calls: list[dict] = []
        channel = _channel(calls)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("非法 名"))))
        assert excinfo.value.code == "invalid_credential_ref"


# ---------------------------------------------------------------------------
# 截断 / 模板 / X-Markdown
# ---------------------------------------------------------------------------


class TestBodyAndTemplate:
    def test_long_body_truncated_to_4096(self, target_env):
        """超长 body 截断(蓝本 _truncate_body 同款),不拆多条。"""
        calls: list[dict] = []
        channel = _channel(calls)
        items = [{"title": "长" * 6000, "url": "https://x/1"}, {"title": "另一条"}]

        _run(channel.send(items, CONTEXT))

        assert len(calls) == 1
        assert len(calls[0]["body"]) == MESSAGE_LIMIT
        assert calls[0]["body"].endswith("长")  # 尾条目丢弃,不拆多条

    def test_template_render_carries_x_markdown(self, target_env):
        """用户模板在场:渲染输出为整条消息,附 X-Markdown: true(R1)。"""
        calls: list[dict] = []
        channel = _channel(calls, template="{{ date }} 共 {{ items|length }} 条")

        _run(channel.send([{"title": "t"}], CONTEXT))

        assert calls[0]["headers"]["x-markdown"] == "true"
        assert calls[0]["body"] == "2026-10-03 共 1 条"

    def test_template_render_failure_is_structured(self, target_env):
        calls: list[dict] = []
        channel = _channel(calls, template="{{ no_such_helper() }}")

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 错误分类(prd:HTTP 403/404 硬失败,429/5xx/超时瞬态)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_403_classifies_forbidden(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(403, json={"code": 40301, "error": "forbidden"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 403" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"

    def test_404_classifies_not_found(self, target_env):
        calls: list[dict] = []
        channel = _channel(
            calls, response=httpx.Response(404, json={"code": 40401, "error": "not found"})
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "HTTP 404" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "not_found"

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_statuses_do_not_mark_dead(self, target_env, status):
        calls: list[dict] = []
        channel = _channel(calls, response=httpx.Response(status, text="busy"))

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_reports_http_error(self, target_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        channel = NtfyChannel(
            target="env:MYIA_TEST_NTFY_TARGET",
            token_ref=None,
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
            _run(NtfyChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value) or "无目录概念" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        """经 canonical refresh:无发现平台跳过、不计数、不算失败、桶不动。"""
        directory = ChannelDirectory(tmp_path)
        directory.merge_entries("ntfy", [], now=1.0)  # 空桶占位(平台在场)

        counts = _run(directory.refresh({"ntfy": NtfyChannel()}, now=100.0))

        assert counts == {}


class TestAuthHeaderUnit:
    """蓝本 ``_build_auth_header`` 语义的单测面(去空白/二形/空值)。"""

    def test_blank_and_strip(self):
        assert build_auth_header("") == {}
        assert build_auth_header("   ") == {}
        assert build_auth_header("  tk  ") == {"Authorization": "Bearer tk"}
