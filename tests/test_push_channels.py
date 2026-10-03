"""Tests for the v0.2 push channels — telegram + webhook (PRD 10-01-v02-push-telegram).

Covers: TG 4096 超限自动分段(边界切分 + 单测)/ TG sendMessage mock 请求体与
结构化错误 / webhook mock 端点校验 payload 结构、凭据引用端点、超时与重试路径
/ 与 route 分级、digest 聚合、AM/PM 防重发的通道无关打通。真实收发不做(任务
openIssues:需主人手动验证)。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``
(同 test_push.py 约定)。Time-dependent behavior uses fixed clocks/sleepers;
all network I/O goes through ``httpx.MockTransport`` — nothing leaves the
process.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from myia.dedup import DedupRegistry
from myia.push import telegram as _module
from myia.push import (
    CHANNELS,
    PLATFORMS,
    Channel,
    DigestAggregator,
    PushSendError,
    SendContext,
    TelegramChannel,
    WebhookChannel,
    route,
    send_immediate,
)
from myia.push.telegram import MESSAGE_LIMIT, build_message, split_message
from myia.push.webhook import build_payload
from myia.store import SQLiteStore

# Fixed +08:00 offset: deterministic slot math regardless of machine TZ.
TIMEZONE = timezone(timedelta(hours=8))


def local_dt(hour: int, minute: int = 0) -> datetime:
    """A fixed local datetime in TIMEZONE (2026-10-01)."""
    return datetime(2026, 10, 1, hour, minute, tzinfo=TIMEZONE)


@pytest.fixture(autouse=True)
def _channel_credentials(monkeypatch):
    """Default credential env for the helper-built channels (tests may override)."""
    monkeypatch.setenv("MYIA_TEST_TG_CHAT", "424242")
    monkeypatch.setenv("MYIA_TEST_HOOK_URL", "https://example.com/myia-hook")


class SleepRecorder:
    """Backoff sleeper double: records every wait, never really waits."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _capture(request: httpx.Request) -> dict:
    return {"url": str(request.url), "body": json.loads(request.content.decode("utf-8"))}


def _mock_telegram(calls: list[dict], response: httpx.Response | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        return response or httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return httpx.MockTransport(handler)


def _mock_webhook(calls: list[dict], responses: list[httpx.Response] | None = None) -> httpx.MockTransport:
    """Respond with ``responses[i]`` per call; the last one repeats (retry paths)."""
    index = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_capture(request))
        if not responses:
            return httpx.Response(200, json={"received": True})
        response = responses[min(index["i"], len(responses) - 1)]
        index["i"] += 1
        return response

    return httpx.MockTransport(handler)


def _tg_channel(calls: list[dict], **kwargs: Any) -> TelegramChannel:
    kwargs.setdefault("token", "test-token")
    kwargs.setdefault("target", "env:MYIA_TEST_TG_CHAT")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock_telegram(calls))
    return TelegramChannel(client=client, **kwargs)


def _hook_channel(calls: list[dict], **kwargs: Any) -> WebhookChannel:
    kwargs.setdefault("target", "env:MYIA_TEST_HOOK_URL")
    client = kwargs.pop("client", None) or httpx.AsyncClient(transport=_mock_webhook(calls))
    return WebhookChannel(client=client, **kwargs)


DIGEST_CONTEXT = SendContext(slot="am", date="2026-10-01", category="羊毛", kind="digest")


# ---------------------------------------------------------------------------
# telegram:4096 分段(纯函数单测,无 I/O)
# ---------------------------------------------------------------------------


def test_split_message_empty_text_returns_empty_list():
    assert split_message("") == []


def test_split_message_short_text_single_part():
    parts = split_message("第一行\n第二行")
    assert parts == ["第一行\n第二行"]


def test_split_message_chunks_respect_limit_and_keep_order():
    lines = [f"公开示例条目{i}" + "x" * 90 for i in range(80)]
    text = "📡 标题行\n" + "\n".join(lines)
    parts = split_message(text)
    assert len(parts) >= 2
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    assert "\n".join(parts).splitlines() == text.splitlines()  # 行序与行内容完整保留


def test_split_message_hard_splits_oversized_single_line():
    parts = split_message("x" * 10000)
    assert [len(part) for part in parts] == [MESSAGE_LIMIT, MESSAGE_LIMIT, 1808]
    assert "".join(parts) == "x" * 10000


# ---------------------------------------------------------------------------
# telegram:sendMessage mock 请求体 + 结构化错误
# ---------------------------------------------------------------------------


def test_telegram_send_posts_builtin_html_layout(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_TG_CHAT", "424242")
    calls: list[dict] = []
    channel = _tg_channel(calls)
    items = [{"title": "公开示例福利", "url": "https://example.com/a", "category": "freebie"}]
    asyncio.run(channel.send(items, DIGEST_CONTEXT))
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottest-token/sendMessage"
    body = calls[0]["body"]
    assert body["chat_id"] == "424242"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True
    assert body["text"].startswith("📡 羊毛日报 10-01 · 上午摘要\n")
    assert '<a href="https://example.com/a">公开示例福利</a> · freebie' in body["text"]


def test_telegram_send_escapes_html_in_title_and_url():
    calls: list[dict] = []
    channel = _tg_channel(calls)
    items = [{"title": "福利 <免费> & \"外送\"", "url": 'https://example.com/a?x=1&y="2"'}]
    asyncio.run(channel.send(items, DIGEST_CONTEXT))
    text = calls[0]["body"]["text"]
    assert "<免费>" not in text  # 原始尖括号不得出现在消息里(parse_mode=HTML 会被吞)
    assert "福利 &lt;免费&gt; &amp; &quot;外送&quot;" in text
    assert 'href="https://example.com/a?x=1&amp;y=&quot;2&quot;"' in text


def test_telegram_send_auto_splits_message_over_4096_chars():
    calls: list[dict] = []
    channel = _tg_channel(calls)
    items = [
        {"title": f"公开示例条目{i}" + "x" * 90, "url": f"https://example.com/{i}"} for i in range(60)
    ]
    asyncio.run(channel.send(items, DIGEST_CONTEXT))
    expected = split_message(build_message(items, DIGEST_CONTEXT))
    assert len(expected) >= 2
    assert [call["body"]["text"] for call in calls] == expected
    assert all(len(call["body"]["text"]) <= MESSAGE_LIMIT for call in calls)
    assert calls[0]["body"]["text"].startswith("📡")  # 标题只在第一段
    assert all("📡" not in call["body"]["text"] for call in calls[1:])


def test_telegram_send_template_sends_plain_text_without_parse_mode():
    calls: list[dict] = []
    channel = _tg_channel(calls, template="{{ date }} 共 {{ count }} 条\n第二行")
    asyncio.run(channel.send([{"title": "a"}, {"title": "b"}], DIGEST_CONTEXT))
    body = calls[0]["body"]
    assert body["text"] == "2026-10-01 共 2 条\n第二行"
    assert "parse_mode" not in body  # 用户模板 = 纯文本,不声明 HTML 解析


def test_telegram_send_template_render_error_wrapped_as_push_send_error():
    channel = _tg_channel([], template="{{ item.pirce }}")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "template_render_error"


def test_telegram_send_api_error_raises_telegram_api_error():
    calls: list[dict] = []
    transport = _mock_telegram(
        calls, httpx.Response(400, json={"ok": False, "error_code": 400, "description": "chat not found"})
    )
    channel = _tg_channel(calls, client=httpx.AsyncClient(transport=transport))
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "telegram_api_error"
    assert "chat not found" in str(excinfo.value)


def test_telegram_send_non_json_response_raises_invalid_response():
    calls: list[dict] = []
    transport = _mock_telegram(calls, httpx.Response(200, text="Not JSON"))
    channel = _tg_channel(calls, client=httpx.AsyncClient(transport=transport))
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "invalid_response"


def test_telegram_send_transport_error_wraps_cause_as_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    calls: list[dict] = []
    channel = _tg_channel(calls, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "http_error"
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


def test_telegram_send_resolves_token_and_chat_from_default_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    calls: list[dict] = []
    channel = TelegramChannel(target=None, client=httpx.AsyncClient(transport=_mock_telegram(calls)))
    asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert calls[0]["url"] == "https://api.telegram.org/botenv-token-xyz/sendMessage"
    assert calls[0]["body"]["chat_id"] == "424242"


def test_telegram_send_missing_token_env_raises_env_var_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    channel = TelegramChannel(target="env:MYIA_TEST_TG_CHAT")  # 不注入 token → 走 env 解析
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "env_var_missing"
    assert "TELEGRAM_BOT_TOKEN" in str(excinfo.value)


def test_telegram_send_noncanonical_keychain_target_raises_structured_error():
    """keychain: target 已实装(v0.2);非规范扁平名结构化拒绝。"""
    channel = TelegramChannel(target="keychain:tg_chat", token="t")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "invalid_secret_name"


# ---------------------------------------------------------------------------
# webhook:payload 结构 + 凭据引用端点
# ---------------------------------------------------------------------------


def test_webhook_send_posts_payload_with_render_data_and_item_metadata():
    calls: list[dict] = []
    channel = _hook_channel(calls)
    items = [
        {
            "title": "公开羊毛示例",
            "url": "https://example.com/free",
            "category": "freebie",
            "metadata": {"symbol": "NVDA"},
        }
    ]
    asyncio.run(channel.send(items, DIGEST_CONTEXT))
    assert len(calls) == 1
    assert calls[0]["url"] == "https://example.com/myia-hook"
    payload = calls[0]["body"]
    assert payload["channel"] == "webhook"
    assert payload["kind"] == "digest"
    assert payload["slot"] == "am"
    assert payload["date"] == "2026-10-01"
    assert payload["category"] == "羊毛"
    assert payload["count"] == 1
    assert payload["items"][0]["title"] == "公开羊毛示例"
    assert payload["items"][0]["symbol"] == "NVDA"  # metadata 并入条目元数据


def test_webhook_send_resolves_endpoint_from_default_env(monkeypatch):
    monkeypatch.setenv("MYIA_WEBHOOK_URL", "https://example.com/default-hook")
    calls: list[dict] = []
    channel = WebhookChannel(target=None, client=httpx.AsyncClient(transport=_mock_webhook(calls)))
    asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert calls[0]["url"] == "https://example.com/default-hook"


def test_webhook_send_plaintext_endpoint_refused():
    channel = WebhookChannel(target="https://example.com/hook", client=httpx.AsyncClient(
        transport=_mock_webhook([])
    ))
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "invalid_credential_ref"  # 端点只许凭据引用(安全基线)


def test_webhook_send_noncanonical_keychain_endpoint_raises_structured_error():
    """keychain: endpoint 已实装(v0.2);非规范扁平名结构化拒绝。"""
    channel = WebhookChannel(target="keychain:hook_url")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "invalid_secret_name"


def test_webhook_send_template_includes_rendered_text_in_payload():
    calls: list[dict] = []
    channel = _hook_channel(calls, template="{{ date }} 共 {{ count }} 条")
    asyncio.run(channel.send([{"title": "a"}, {"title": "b"}], DIGEST_CONTEXT))
    payload = calls[0]["body"]
    assert payload["text"] == "2026-10-01 共 2 条"
    assert payload["count"] == 2  # 结构化数据与渲染 text 并存


def test_webhook_send_template_render_error_wrapped_as_push_send_error():
    channel = _hook_channel([], template="{{ item.pirce }}")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "template_render_error"


def test_build_payload_omits_text_without_template():
    payload = build_payload([{"title": "t"}], DIGEST_CONTEXT)
    assert "text" not in payload


# ---------------------------------------------------------------------------
# webhook:超时/重试路径(sleeper 注入,零真实等待)
# ---------------------------------------------------------------------------


def test_webhook_send_retries_transient_503_then_succeeds():
    calls: list[dict] = []
    transport = _mock_webhook(calls, [httpx.Response(503), httpx.Response(200, json={})])
    sleeper = SleepRecorder()
    channel = _hook_channel(calls, client=httpx.AsyncClient(transport=transport), sleep=sleeper)
    asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert len(calls) == 2
    assert sleeper.sleeps == [1.0]  # 首次退避 1s(指数基数)


def test_webhook_send_exhausts_retries_on_permanent_500():
    calls: list[dict] = []
    transport = _mock_webhook(calls, [httpx.Response(500)])
    sleeper = SleepRecorder()
    channel = _hook_channel(calls, client=httpx.AsyncClient(transport=transport), sleep=sleeper)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "webhook_api_error"
    assert "500" in str(excinfo.value)
    assert len(calls) == 3  # 1 首次 + 2 重试(默认 retries=2)
    assert sleeper.sleeps == [1.0, 2.0]  # 指数退避


def test_webhook_send_client_error_400_fails_without_retry():
    calls: list[dict] = []
    transport = _mock_webhook(calls, [httpx.Response(400, text="bad request")])
    sleeper = SleepRecorder()
    channel = _hook_channel(calls, client=httpx.AsyncClient(transport=transport), sleep=sleeper)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "webhook_api_error"
    assert len(calls) == 1  # 4xx 永久性错误:单次尝试,不重试
    assert sleeper.sleeps == []


def test_webhook_send_transport_error_retries_then_raises_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url)})  # 请求已发出再失败,计入尝试次数
        raise httpx.ConnectError("connection refused")

    calls: list[dict] = []
    sleeper = SleepRecorder()
    channel = _hook_channel(
        calls, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=sleeper
    )
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "http_error"
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
    assert len(calls) == 3
    assert sleeper.sleeps == [1.0, 2.0]


def test_webhook_send_timeout_exception_retries_then_raises_http_error():
    """超时路径:httpx.ReadTimeout 属 TransportError,与网络错误同走退避重试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url)})
        raise httpx.ReadTimeout("timed out")

    calls: list[dict] = []
    sleeper = SleepRecorder()
    channel = _hook_channel(
        calls, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=sleeper
    )
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert excinfo.value.code == "http_error"
    assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)
    assert len(calls) == 3
    assert sleeper.sleeps == [1.0, 2.0]


def test_webhook_send_zero_retries_single_attempt():
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url)})
        raise httpx.ConnectError("connection refused")

    calls: list[dict] = []
    sleeper = SleepRecorder()
    channel = _hook_channel(
        calls,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retries=0,
        sleep=sleeper,
    )
    with pytest.raises(PushSendError):
        asyncio.run(channel.send([{"title": "t"}], DIGEST_CONTEXT))
    assert len(calls) == 1
    assert sleeper.sleeps == []


def test_webhook_channel_rejects_negative_retry_config():
    with pytest.raises(ValueError, match="retries"):
        WebhookChannel(retries=-1)
    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        WebhookChannel(retry_backoff_seconds=-0.5)


# ---------------------------------------------------------------------------
# 通道无关打通:route 分级 → digest 聚合 / immediate + AM/PM 防重发
# ---------------------------------------------------------------------------


def test_route_digest_and_slot_suppression_flow_through_new_channels(tmp_path, monkeypatch):
    monkeypatch.setenv("MYIA_TEST_TG_CHAT", "424242")
    monkeypatch.setenv("MYIA_TEST_HOOK_URL", "https://example.com/myia-hook")
    tg_calls: list[dict] = []
    hook_calls: list[dict] = []
    tg_client = httpx.AsyncClient(transport=_mock_telegram(tg_calls))
    hook_client = httpx.AsyncClient(transport=_mock_webhook(hook_calls))
    store = SQLiteStore(tmp_path / "channels.db")
    try:
        telegram = _tg_channel(tg_calls, client=tg_client)
        webhook = _hook_channel(hook_calls, client=hook_client)
        registry = DedupRegistry(store, tz=TIMEZONE)
        aggregator = DigestAggregator(channels=[telegram, webhook], registry=registry, tz=TIMEZONE)

        items = [
            {"title": f"公开羊毛示例{i}", "url": f"https://example.com/free{i}", "category": "freebie"}
            for i in range(3)
        ]
        buckets = route(items, None)  # freebie → 大类缺省 immediate
        assert len(buckets.immediate) == 3 and buckets.digest == []
        for item in buckets.immediate:
            aggregator.add(item, dedup_key=item["url"])

        am_reports = asyncio.run(aggregator.flush(now=local_dt(9), category="羊毛"))
        assert [report.ok for report in am_reports] == [True, True]  # 每通道一份报告
        assert len(tg_calls) == 1 and len(hook_calls) == 1
        assert hook_calls[0]["body"]["count"] == 3  # 三条目合并一个 payload

        aggregator.add(items[0], dedup_key=items[0]["url"])
        assert asyncio.run(aggregator.flush(now=local_dt(10, 30))) == []  # 同槽位拦截(条目出池)
        assert len(tg_calls) == 1 and len(hook_calls) == 1

        aggregator.add(items[0], dedup_key=items[0]["url"])  # 再次入池,等下一个槽位
        pm_reports = asyncio.run(aggregator.flush(now=local_dt(13)))  # 跨槽位放行
        assert all(report.ok for report in pm_reports)
        assert hook_calls[1]["body"]["slot"] == "pm"
        assert len(tg_calls) == 2 and len(hook_calls) == 2

        immediate_item = {"title": "立即示例", "url": "https://example.com/now", "category": "freebie"}
        immediate = asyncio.run(
            send_immediate(
                [immediate_item], channels=[webhook], registry=registry, tz=TIMEZONE, now=local_dt(14)
            )
        )
        assert all(report.ok for report in immediate)
        assert len(hook_calls) == 3 and hook_calls[2]["body"]["kind"] == "immediate"
        again = asyncio.run(
            send_immediate(
                [immediate_item], channels=[webhook], registry=registry, tz=TIMEZONE, now=local_dt(14, 30)
            )
        )
        assert again == []  # 立即推送同槽位防重发
        assert len(hook_calls) == 3
    finally:
        store.close()
        asyncio.run(tg_client.aclose())
        asyncio.run(hook_client.aclose())


# ---------------------------------------------------------------------------
# Channel 协议一致性(v0.1 评审曾抓到协议不符,回归护栏)
# ---------------------------------------------------------------------------


def test_new_channels_conform_to_channel_protocol_and_registry():
    assert isinstance(TelegramChannel(token="t", target="env:X"), Channel)
    assert isinstance(WebhookChannel(target="env:X"), Channel)
    # 注册表钉死:ntfy/dingtalk/wecom 随 10-03-messaging-w2-platforms 落地,
    # weixin 随 10-03-messaging-weixin-bridge 落地(可选桥接:出站经本机
    # Hermes CLI,无目录发现)——钉死集随通道注册表演进同步(新通道协议
    # 符合性由 tests/test_messaging_{ntfy,dingtalk,wecom,weixin_bridge}.py 专测)。
    # W3 长尾 22 家随 10-03-messaging-w3-longtail 终局接线落地(见下方
    # TestW3LongtailRegistry;逐家协议/发送专测在 tests/test_messaging_<平台>.py)。
    assert set(CHANNELS) == {
        "feishu_card",
        "telegram",
        "webhook",
        "stdout",
        "ntfy",
        "dingtalk",
        "wecom",
        "weixin",
        *W3_LONGTAIL_NAMES,
    }
    assert TelegramChannel.name == "telegram"
    assert WebhookChannel.name == "webhook"


# ---------------------------------------------------------------------------
# W3 长尾 22 家终局接线(10-03-messaging-w3-longtail):注册表 + 协议 + 构建契约
# ---------------------------------------------------------------------------

#: W3 长尾 22 家通道名(组一 Slack 系 8 + 组二 Matrix 系 8 + 组三长尾壳 6);
#: 与 myia.push._W3_LONGTAIL_CHANNELS、schema._W3_LONGTAIL 一一对应。
W3_LONGTAIL_NAMES = (
    "slack",
    "discord",
    "whatsapp_cloud",
    "line",
    "qqbot",
    "google_chat",
    "teams",
    "msgraph_webhook",
    "matrix",
    "mattermost",
    "irc",
    "simplex",
    "signal",
    "bluebubbles",
    "email",
    "sms",
    "homeassistant",
    "a2a",
    "yuanbao",
    "buzz",
    "photon",
    "raft",
)


class TestW3LongtailRegistry:
    """22 家全量入表:CHANNELS/PLATFORMS 注册、通道计数、管线构建契约。"""

    def test_all_w3_channels_in_channels_registry(self):
        for name in W3_LONGTAIL_NAMES:
            channel_cls = CHANNELS.get(name)
            assert channel_cls is not None, f"{name} 未注册进 CHANNELS"
            assert channel_cls.name == name
            # 22 家全量开目录寻址(壳通道可解析寻址,发送能力另论)
            assert channel_cls.supports_targeting is True

    def test_all_w3_channels_in_platforms_registry(self):
        for name in W3_LONGTAIL_NAMES:
            assert PLATFORMS.get(name) is CHANNELS.get(name), (
                f"{name} 的 PLATFORMS 条目与 CHANNELS 条目不是同一类"
            )

    def test_registry_counts_after_w3_wiring(self):
        # 通道计数如实:8 既有 + 22 长尾 = 30 通道;28 家支持目录寻址
        # (webhook/stdout 不支持;feishu_card 通道名经 schema 的
        # CHANNEL_PLATFORMS 映射到平台名 feishu,其余 29+27 家平台名 = 通道名)。
        from myia.schema import CHANNEL_PLATFORMS

        assert len(CHANNELS) == 30
        assert len(PLATFORMS) == 28
        assert set(CHANNELS) - set(CHANNEL_PLATFORMS) == {"webhook", "stdout"}
        assert set(CHANNEL_PLATFORMS.values()) == set(PLATFORMS)

    def test_w3_channels_buildable_under_pipeline_contract(self):
        """pipeline._build_channel 对所有非 stdout 通道下传 target/template。

        回归背景:4 家壳通道(yuanbao/buzz/photon/raft)原为无参构造,配置
        legacy ``target:`` 会在管线构建期裸抛 TypeError;终局接线为它们补了
        收下备档的最小构造面。其余 18 家构造期不做网络 I/O(凭据发送期才解析)。
        """
        for name in W3_LONGTAIL_NAMES:
            channel = CHANNELS[name](target="env:W3_TEST_REF", template="{{ date }}")
            assert channel.name == name
            bare = CHANNELS[name]()  # targets-only 配置路径(不下传任何 kwargs)
            assert bare.name == name


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:_item_line 永不把行切成未闭合标签
# ---------------------------------------------------------------------------


def test_item_line_long_title_never_cuts_tag():
    """长标题:截标题文本,<a>…</a> 结构完整(整行硬截会产出非法 HTML,
    Telegram 400 拒收 → digest 毒池逐槽位重试)。"""
    view = {"title": "标题" * 2000, "url": "https://example.com/x"}
    line = _module._item_line(view)
    assert len(line) <= _module.MAX_ITEM_LINE_LENGTH
    assert line.startswith('▸ <a href="https://example.com/x">')
    assert line.endswith("</a>")
    assert "…" in line


def test_item_line_long_url_degrades_to_plain_text():
    """长 URL(>1024):<a> 标签放不下 → 整行降级纯文本,不产出残缺标签。"""
    view = {"title": "t", "url": "https://example.com/" + "a" * 1100}
    line = _module._item_line(view)
    assert len(line) <= _module.MAX_ITEM_LINE_LENGTH
    assert "<a" not in line and "</a>" not in line
    assert line.startswith("▸ ")


def test_item_line_truncated_entity_is_trimmed():
    """截断停在转义实体(&amp;)中间时回退到实体前,不产出残缺实体。"""
    view = {"title": "a" + "&amp;" * 800, "url": "https://example.com/x"}
    line = _module._item_line(view)
    assert len(line) <= _module.MAX_ITEM_LINE_LENGTH
    body = line[len('▸ <a href="https://example.com/x">'):-len("</a>")]
    assert body.endswith(("&amp;", "…"))  # 残缺 "&am" 不允许出现


# ---------------------------------------------------------------------------
# v1.1 low 清理:「另见 N 源」截断路径预算计入完整后缀(不超自声明上限)
# ---------------------------------------------------------------------------


def test_item_also_line_truncation_reserves_full_suffix_width():
    """满长正文 + 后缀 ≤1024:截断预算计入「…等 N 源」完整宽度。

    构造单个恰好填满旧预算(MAX-1,旧实现只预留一个省略号)的源链接:
    旧实现行长会冲到 1023 + len("…等 10 源") = 1030(两位数 N 超上限 6
    字符);修后预算 = MAX - len(suffix),该链接放不下 → 整行降级为纯计数
    说明,行长不超上限。
    """
    prefix = "　└ 另见 10 源: "  # len = 12
    url = "https://a.example/x"  # 无需转义字符,html.escape 后长度不变
    # ref = '<a href="…">' + title + '</a>':9 + len(url) + 2 + len(title) + 4
    ref_len = _module.MAX_ITEM_LINE_LENGTH - 1 - len(prefix)  # 旧预算恰好容下
    title_len = ref_len - (15 + len(url))
    entries = [{"title": "长" * title_len, "url": url, "source": "s"} for _ in range(10)]
    view = {"title": "主条目", "also_seen": entries}

    line = _module._item_also_line(view)

    assert len(line) <= _module.MAX_ITEM_LINE_LENGTH
    assert line.endswith("…等 10 源")  # 计数后缀完整保留


def test_item_also_line_truncation_keeps_refs_within_cap():
    """截断但仍有源放得下:保留前缀 + 已放下的链接 + 计数后缀,行 ≤ 上限。"""
    entries = [
        {"title": "标" * 250, "url": f"https://s{i}.example/x", "source": "s"}
        for i in range(10)
    ]
    view = {"title": "主条目", "also_seen": entries}

    line = _module._item_also_line(view)

    assert len(line) <= _module.MAX_ITEM_LINE_LENGTH
    assert line.endswith("…等 10 源")
    # 3 条链接可放进预算(12 + 285*3 + 分隔 2 = 869),第 4 条越界被舍弃
    assert line.count("<a href=") == 3
