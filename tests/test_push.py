"""Tests for shishi.push — routing, digest aggregation, feishu/stdout channels.

Covers PRD 10-01-v01-push-feishu-route acceptance criteria: route 三分支 /
大类缺省映射 / 未命中保守缺省 / 模板快照(Jinja2 版 stocks 示例)/ digest
同槽位多条目合并一张卡 / 飞书 mock 请求体。真实推送不做(见任务 openIssues:
需主人手动验证)。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``.
Time-dependent behavior uses injected clocks (``now=``) with a fixed-offset
timezone — deterministic on any machine, no freezegun. All network I/O goes
through ``httpx.MockTransport`` — nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from shishi.classify import ALL_CATEGORIES
from shishi.dedup import DedupRegistry
from shishi.push import (
    CATEGORY_DEFAULT_ROUTES,
    CHANNELS,
    Channel,
    DigestAggregator,
    FeishuCardChannel,
    PushSendError,
    RouteConfigError,
    RouteRule,
    STOCKS_EXAMPLE_TEMPLATE,
    SendContext,
    StdoutChannel,
    TelegramChannel,
    TemplateRenderError,
    TemplateRenderer,
    WebhookChannel,
    build_card,
    item_view,
    resolve_route,
    route,
    routes_from_config,
    send_immediate,
)
from shishi.schema import load_category_file
from shishi.store import SLOT_AM, SLOT_PM, SQLiteStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stocks.yaml"

# Fixed +08:00 offset: deterministic slot math regardless of machine TZ.
TIMEZONE = timezone(timedelta(hours=8))


def local_dt(hour: int, minute: int = 0) -> datetime:
    """A fixed local datetime in TIMEZONE (2026-10-01)."""
    return datetime(2026, 10, 1, hour, minute, tzinfo=TIMEZONE)


# v1.7 定案的三分支阈值规则(stock 示例同款)。
CANONICAL_RULES = [
    RouteRule(when="score >= 8", mode="immediate"),
    RouteRule(when="score >= 5", mode="digest"),
    RouteRule(when="score < 5", mode="archive"),
]


class RecordingChannel:
    """Test double capturing sends; ``fail=True`` raises a structured error."""

    name = "recording"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    async def send(self, items, context) -> None:
        if self.fail:
            raise PushSendError("http_error", "模拟通道故障")
        self.calls.append({"items": [item_view(item) for item in items], "context": context})


class FlakyChannel:
    """Fails the first send, succeeds afterwards (retry-path testing)."""

    name = "flaky"

    def __init__(self) -> None:
        self.fail_next = True
        self.calls: list[dict] = []

    async def send(self, items, context) -> None:
        if self.fail_next:
            self.fail_next = False
            raise PushSendError("http_error", "首次发送失败")
        self.calls.append({"items": list(items), "context": context})


@pytest.fixture()
def store(tmp_path):
    s = SQLiteStore(tmp_path / "push.db")
    yield s
    s.close()


@pytest.fixture()
def registry(store):
    return DedupRegistry(store, tz=TIMEZONE)


def _mock_feishu(capture: dict, payload: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["auth"] = request.headers.get("authorization")
        capture["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=payload or {"code": 0, "msg": "ok", "data": {}})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# route 三分支(v1.7:score >= 8 immediate / >= 5 digest / < 5 archive)
# ---------------------------------------------------------------------------


def test_resolve_route_score_ge_8_returns_immediate():
    assert resolve_route({"score": 8, "title": "a"}, CANONICAL_RULES).mode == "immediate"
    assert resolve_route({"score": 9.5, "title": "a"}, CANONICAL_RULES).mode == "immediate"


def test_resolve_route_score_between_5_and_8_returns_digest():
    assert resolve_route({"score": 5, "title": "b"}, CANONICAL_RULES).mode == "digest"
    assert resolve_route({"score": 7.9, "title": "b"}, CANONICAL_RULES).mode == "digest"


def test_resolve_route_score_below_5_returns_archive():
    assert resolve_route({"score": 4.9, "title": "c"}, CANONICAL_RULES).mode == "archive"
    assert resolve_route({"score": 0, "title": "c"}, CANONICAL_RULES).mode == "archive"


def test_resolve_route_reads_object_attributes():
    item = SimpleNamespace(title="示例", score=9)
    assert resolve_route(item, CANONICAL_RULES).mode == "immediate"


def test_route_buckets_items_into_three_modes():
    items = [
        {"score": 9, "title": "a"},
        {"score": 6, "title": "b"},
        {"score": 1, "title": "c"},
        {"category": "freebie", "title": "d"},
    ]
    buckets = route(items, CANONICAL_RULES)
    assert [i["title"] for i in buckets.immediate] == ["a", "d"]
    assert [i["title"] for i in buckets.digest] == ["b"]
    assert [i["title"] for i in buckets.archive] == ["c"]
    assert buckets.counts() == {"immediate": 2, "digest": 1, "archive": 1}


# ---------------------------------------------------------------------------
# v0.1 无 score:大类缺省映射(grill Q1)+ 未命中保守缺省
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["freebie", "proxy-node", "buying-agent"])
def test_resolve_route_without_score_immediate_categories_map_immediate(category):
    decision = resolve_route({"category": category}, [])
    assert decision.mode == "immediate"
    assert decision.reason == "category_default"


@pytest.mark.parametrize("category", ["ai-news", "server", "token", "credit-card", "channel"])
def test_resolve_route_without_score_digest_categories_map_digest(category):
    decision = resolve_route({"category": category}, [])
    assert decision.mode == "digest"
    assert decision.reason == "category_default"


def test_resolve_route_without_score_unknown_category_defaults_conservative_digest():
    decision = resolve_route({"category": "no-such-category"}, [])
    assert decision.mode == "digest"
    assert decision.reason == "conservative_default"


def test_resolve_route_without_score_missing_category_defaults_conservative_digest():
    decision = resolve_route({"title": "无法分类的条目"}, [])
    assert decision.mode == "digest"
    assert decision.reason == "conservative_default"


def test_category_default_mapping_covers_all_known_categories():
    assert set(CATEGORY_DEFAULT_ROUTES) == set(ALL_CATEGORIES)


def test_resolve_route_category_override_rule_beats_default_mapping():
    rules = [RouteRule(when="category in ['server', 'ai-news']", mode="immediate")]
    hit = resolve_route({"category": "server"}, rules)
    assert hit.mode == "immediate"
    assert hit.reason == "rule"
    # 未被覆盖规则命中的大类仍走缺省映射
    miss = resolve_route({"category": "token"}, rules)
    assert miss.mode == "digest"
    assert miss.reason == "category_default"


# ---------------------------------------------------------------------------
# v0.2 接口位:score 路由优先于大类映射;无 score 时 score 规则休眠
# ---------------------------------------------------------------------------


def test_resolve_route_score_rule_takes_precedence_over_category_mapping():
    # score=2 本应 archive,即便大类是羊毛(immediate 映射)→ score 优先
    decision = resolve_route({"score": 2, "category": "freebie"}, CANONICAL_RULES)
    assert decision.mode == "archive"
    assert decision.reason == "rule"


def test_resolve_route_score_rules_stay_dormant_without_score(caplog):
    with caplog.at_level(logging.WARNING):
        decision = resolve_route({"category": "server", "title": "示例"}, CANONICAL_RULES)
    assert decision.mode == "digest"
    assert decision.reason == "category_default"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_resolve_route_with_score_and_no_rules_defaults_immediate():
    decision = resolve_route({"score": 3}, [])
    assert decision.mode == "immediate"
    assert decision.reason == "no_rules_default"


def test_resolve_route_with_score_and_unmatched_rules_defaults_conservative_digest():
    decision = resolve_route({"score": 3}, [RouteRule(when="score > 100", mode="immediate")])
    assert decision.mode == "digest"
    assert decision.reason == "conservative_default"


# ---------------------------------------------------------------------------
# RouteRule / routes_from_config:fail-fast 配置校验 + stocks 夹具对接
# ---------------------------------------------------------------------------


def test_route_rule_invalid_mode_raises_route_config_error():
    with pytest.raises(RouteConfigError, match="mode"):
        RouteRule(when="score >= 8", mode="push")


def test_route_rule_disallowed_expression_raises_route_config_error():
    with pytest.raises(RouteConfigError, match="when"):
        RouteRule(when="__import__('os').system('true')", mode="immediate")


def test_routes_from_config_accepts_raw_mappings_and_rule_objects():
    raw = routes_from_config([{"when": "score >= 8", "mode": "immediate"}])
    assert len(raw) == 1 and raw[0].mode == "immediate"
    assert raw[0].references_score is True
    assert routes_from_config(None) == []
    passed = routes_from_config(CANONICAL_RULES)
    assert passed == CANONICAL_RULES


def test_routes_from_config_unknown_field_fails_fast():
    with pytest.raises(RouteConfigError, match="priority"):
        routes_from_config([{"when": "score >= 8", "mode": "immediate", "priority": 1}])


def test_routes_from_stocks_fixture_route_three_thresholds():
    cfg = load_category_file(FIXTURE)
    rules = routes_from_config(cfg.push[0].route)
    assert [r.mode for r in rules] == ["immediate", "digest", "archive"]
    assert resolve_route({"score": 9}, rules).mode == "immediate"
    assert resolve_route({"score": 6}, rules).mode == "digest"
    assert resolve_route({"score": 2}, rules).mode == "archive"
    # v0.1 无 score:score 规则休眠 → 股票条目未命中七大类 → 保守 digest
    decision = resolve_route({"title": "示例快讯", "change_pct": 1.0}, rules)
    assert decision.mode == "digest"
    assert decision.reason == "conservative_default"


# ---------------------------------------------------------------------------
# 模板渲染:Jinja2 快照(stocks 示例)+ 沙箱 + 未定义字段
# ---------------------------------------------------------------------------


def test_renderer_stocks_example_template_snapshot():
    items = [
        {"symbol": "NVDA", "change_pct": 3.2, "price": 132.5},
        {"symbol": "TSLA", "change_pct": -4.1, "price": 245.0},
    ]
    rendered = TemplateRenderer().render(
        STOCKS_EXAMPLE_TEMPLATE, items, SendContext(slot="pm", date="2026-10-01", category="stocks")
    )
    assert rendered == "**股票异动 · 2026-10-01**\nNVDA 3.2% 现价 132.5\nTSLA -4.1% 现价 245.0"


def test_renderer_sandbox_blocks_dunder_attribute_access():
    with pytest.raises(TemplateRenderError) as excinfo:
        TemplateRenderer().render(
            "{{ ''.__class__ }}", [], SendContext(slot="am", date="2026-10-01")
        )
    assert excinfo.value.__cause__ is not None


def test_renderer_undefined_variable_raises_template_render_error():
    with pytest.raises(TemplateRenderError, match="渲染失败"):
        TemplateRenderer().render(
            "{{ item.price }}", [{"symbol": "NVDA"}], SendContext(slot="am", date="2026-10-01")
        )


def test_renderer_exposes_date_slot_count_category_context():
    rendered = TemplateRenderer().render(
        "{{ date }} {{ slot }} {{ count }} {{ category }}",
        [{"title": "x"}],
        SendContext(slot="pm", date="2026-10-01", category="羊毛"),
    )
    assert rendered == "2026-10-01 pm 1 羊毛"


# ---------------------------------------------------------------------------
# feishu_card:卡片结构 + mock 请求体 + 结构化错误(真实推送不做)
# ---------------------------------------------------------------------------


def test_build_card_aligns_production_card_json_shape():
    card = build_card(
        [
            {"title": "示例条目", "url": "https://example.com/a"},
            {"title": "示例条目二", "url": "https://example.com/b"},
        ],
        title="📡 测试日报",
    )
    assert card["config"] == {"wide_screen_mode": True}
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"] == {"tag": "plain_text", "content": "📡 测试日报"}
    assert [e["tag"] for e in card["elements"]] == ["div", "hr", "div", "note"]
    divs = [e for e in card["elements"] if e["tag"] == "div"]
    assert divs[0]["text"]["tag"] == "lark_md"
    assert "**[示例条目](https://example.com/a)**" in divs[0]["text"]["content"]


def test_feishu_send_posts_interactive_card_with_expected_body(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo_123")
    capture: dict = {}
    client = httpx.AsyncClient(transport=_mock_feishu(capture))
    channel = FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID", token="test-token", client=client)
    items = [{"title": "公开示例福利", "url": "https://example.com/a", "category": "freebie"}]
    try:
        asyncio.run(
            channel.send(
                items, SendContext(slot="am", date="2026-10-01", category="羊毛", kind="digest")
            )
        )
    finally:
        asyncio.run(client.aclose())
    assert capture["url"].startswith("https://open.feishu.cn/open-apis/im/v1/messages")
    assert "receive_id_type=chat_id" in capture["url"]
    assert capture["auth"] == "Bearer test-token"
    body = capture["body"]
    assert body["receive_id"] == "oc_demo_123"
    assert body["msg_type"] == "interactive"
    card = json.loads(body["content"])
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"]["content"] == "📡 羊毛日报 10-01 · 上午摘要"
    assert "**[公开示例福利](https://example.com/a)**" in card["elements"][0]["text"]["content"]
    assert card["elements"][-1]["tag"] == "note"


def test_feishu_send_resolves_bot_token_from_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "env-token-xyz")
    capture: dict = {}
    client = httpx.AsyncClient(transport=_mock_feishu(capture))
    channel = FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID", client=client)
    try:
        asyncio.run(
            channel.send([{"title": "t"}], SendContext(slot="pm", date="2026-10-01", kind="immediate"))
        )
    finally:
        asyncio.run(client.aclose())
    assert capture["auth"] == "Bearer env-token-xyz"
    card = json.loads(capture["body"]["content"])
    assert card["header"]["title"]["content"] == "🔔 情报 · 10-01"


def test_feishu_send_template_renders_into_single_markdown_div(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    capture: dict = {}
    client = httpx.AsyncClient(transport=_mock_feishu(capture))
    channel = FeishuCardChannel(
        target="env:MYIA_TEST_CHAT_ID", token="t", template=STOCKS_EXAMPLE_TEMPLATE, client=client
    )
    try:
        asyncio.run(
            channel.send(
                [{"symbol": "NVDA", "change_pct": 3.2, "price": 132.5}],
                SendContext(slot="pm", date="2026-10-01", kind="digest"),
            )
        )
    finally:
        asyncio.run(client.aclose())
    card = json.loads(capture["body"]["content"])
    divs = [e for e in card["elements"] if e["tag"] == "div"]
    assert len(divs) == 1
    assert "NVDA 3.2% 现价 132.5" in divs[0]["text"]["content"]


def test_feishu_send_api_error_raises_push_send_error(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    client = httpx.AsyncClient(
        transport=_mock_feishu({}, {"code": 99991663, "msg": "invalid access token"})
    )
    channel = FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID", token="t", client=client)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send([{"title": "t"}], SendContext(slot="am", date="2026-10-01"))
        )
    assert excinfo.value.code == "feishu_api_error"
    assert "99991663" in str(excinfo.value)


def test_feishu_send_http_failure_wraps_original_error(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID", token="t", client=client)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send([{"title": "t"}], SendContext(slot="am", date="2026-10-01"))
        )
    assert excinfo.value.code == "http_error"
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


def test_feishu_send_missing_env_token_raises_structured_error(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    monkeypatch.delenv("FEISHU_BOT_TOKEN", raising=False)
    channel = FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send([{"title": "t"}], SendContext(slot="am", date="2026-10-01"))
        )
    assert excinfo.value.code == "env_var_missing"
    assert "FEISHU_BOT_TOKEN" in str(excinfo.value)


def test_feishu_send_noncanonical_keychain_target_raises_structured_error():
    """keychain: target 已实装(v0.2);非规范扁平名结构化拒绝,零网络。"""
    channel = FeishuCardChannel(target="keychain:feishu_chat", token="t")
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send([{"title": "t"}], SendContext(slot="am", date="2026-10-01"))
        )
    assert excinfo.value.code == "invalid_secret_name"


# ---------------------------------------------------------------------------
# stdout 通道:结构化 JSON 输出(调试 / --dry-run / CI)
# ---------------------------------------------------------------------------


def test_stdout_channel_prints_structured_json_line():
    out = io.StringIO()
    asyncio.run(
        StdoutChannel(out=out).send(
            [{"title": "示例", "url": "https://example.com/a"}],
            SendContext(slot="am", date="2026-10-01", category="羊毛", kind="digest"),
        )
    )
    payload = json.loads(out.getvalue())
    assert payload["channel"] == "stdout"
    assert payload["kind"] == "digest"
    assert payload["slot"] == "am"
    assert payload["date"] == "2026-10-01"
    assert payload["category"] == "羊毛"
    assert payload["count"] == 1
    assert payload["items"][0]["title"] == "示例"
    assert "text" not in payload


def test_stdout_channel_with_template_includes_rendered_text():
    out = io.StringIO()
    asyncio.run(
        StdoutChannel(out=out, template="{{ date }} 共 {{ count }} 条").send(
            [{"title": "a"}, {"title": "b"}], SendContext(slot="pm", date="2026-10-01")
        )
    )
    payload = json.loads(out.getvalue())
    assert payload["text"] == "2026-10-01 共 2 条"


# ---------------------------------------------------------------------------
# digest 聚合:同槽位多条目合并一张卡 + 共享注册表防重发
# ---------------------------------------------------------------------------


def test_digest_flush_merges_pool_into_one_card_per_channel():
    channel = RecordingChannel()
    aggregator = DigestAggregator(channels=[channel], tz=TIMEZONE)
    for index in range(3):
        aggregator.add({"title": f"示例条目{index}", "url": f"https://example.com/{index}"})
    reports = asyncio.run(aggregator.flush(now=local_dt(9), category="羊毛"))
    assert len(channel.calls) == 1  # 三条目合并一张卡(一次调用)
    assert [v["title"] for v in channel.calls[0]["items"]] == [
        "示例条目0",
        "示例条目1",
        "示例条目2",
    ]
    context = channel.calls[0]["context"]
    assert (context.slot, context.kind, context.date) == (SLOT_AM, "digest", "2026-10-01")
    assert len(reports) == 1
    assert reports[0].ok is True and reports[0].item_count == 3
    assert len(aggregator) == 0


def test_digest_flush_suppresses_same_slot_repush_shared_registry(registry):
    channel = RecordingChannel()
    aggregator = DigestAggregator(channels=[channel], registry=registry, tz=TIMEZONE)
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="https://example.com/a")
    asyncio.run(aggregator.flush(now=local_dt(9)))
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="https://example.com/a")
    reports = asyncio.run(aggregator.flush(now=local_dt(10, 30)))
    assert reports == []  # 同槽位拦截,不再发送
    assert len(channel.calls) == 1
    assert len(aggregator) == 0  # 被拦截条目不再滞留池中


def test_digest_flush_releases_across_slots_pm_after_am(registry):
    channel = RecordingChannel()
    aggregator = DigestAggregator(channels=[channel], registry=registry, tz=TIMEZONE)
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="k-cross")
    asyncio.run(aggregator.flush(now=local_dt(9)))
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="k-cross")
    asyncio.run(aggregator.flush(now=local_dt(13)))
    assert len(channel.calls) == 2
    assert channel.calls[1]["context"].slot == SLOT_PM


def test_digest_flush_records_push_slot_in_registry(registry):
    channel = RecordingChannel()
    aggregator = DigestAggregator(channels=[channel], registry=registry, tz=TIMEZONE)
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="k-slot")
    asyncio.run(aggregator.flush(now=local_dt(9)))
    entry = registry.get_entry("k-slot")
    assert entry is not None
    assert entry.last_push_slot == SLOT_AM
    assert entry.last_pushed_at is not None


def test_digest_flush_empty_pool_sends_nothing():
    channel = RecordingChannel()
    aggregator = DigestAggregator(channels=[channel], tz=TIMEZONE)
    reports = asyncio.run(aggregator.flush(now=local_dt(9)))
    assert reports == []
    assert channel.calls == []


def test_digest_flush_partial_failure_reports_and_clears_pool():
    bad = RecordingChannel(fail=True)
    ok = RecordingChannel()
    aggregator = DigestAggregator(channels=[bad, ok], registry=None, tz=TIMEZONE)
    aggregator.add({"title": "示例", "url": "https://example.com/a"}, dedup_key="k-partial")
    reports = asyncio.run(aggregator.flush(now=local_dt(14)))
    assert [r.ok for r in reports] == [False, True]
    assert reports[0].error is not None and reports[0].error.startswith("[http_error]")
    assert len(aggregator) == 0  # 有通道成功 → 条目出池


def test_digest_flush_all_channels_failed_keeps_pool_for_retry():
    flaky = FlakyChannel()
    aggregator = DigestAggregator(channels=[flaky], tz=TIMEZONE)
    aggregator.add({"title": "示例", "url": "https://example.com/a"})
    first = asyncio.run(aggregator.flush(now=local_dt(9)))
    assert all(r.ok is False for r in first)
    assert len(aggregator) == 1  # 全部失败 → 留池
    second = asyncio.run(aggregator.flush(now=local_dt(9, 30)))
    assert all(r.ok is True for r in second)
    assert len(aggregator) == 0
    assert len(flaky.calls) == 1


def test_digest_slot_boundary_follows_local_noon():
    aggregator = DigestAggregator(channels=[], tz=TIMEZONE)
    assert aggregator.current_slot(local_dt(11, 59)) == SLOT_AM
    assert aggregator.current_slot(local_dt(12, 0)) == SLOT_PM


# ---------------------------------------------------------------------------
# 立即推送:逐条卡片 + 部分失败 + 同槽位防重发
# ---------------------------------------------------------------------------


def test_send_immediate_sends_each_item_and_records_push(registry):
    channel = RecordingChannel()
    items = [
        {"title": "示例一", "url": "https://example.com/1"},
        {"title": "示例二", "url": "https://example.com/2"},
    ]
    reports = asyncio.run(
        send_immediate(
            items, channels=[channel], registry=registry, tz=TIMEZONE, now=local_dt(10), category="羊毛"
        )
    )
    assert len(reports) == 2 and all(r.ok for r in reports)
    assert len(channel.calls) == 2  # 一条目一张卡
    assert all(len(c["items"]) == 1 for c in channel.calls)
    assert channel.calls[0]["context"].kind == "immediate"
    assert registry.get_entry("https://example.com/1").last_push_slot == SLOT_AM


def test_send_immediate_continues_after_channel_failure():
    bad = RecordingChannel(fail=True)
    ok = RecordingChannel()
    items = [{"title": "a", "url": "https://example.com/1"}, {"title": "b", "url": "https://example.com/2"}]
    reports = asyncio.run(
        send_immediate(items, channels=[bad, ok], tz=TIMEZONE, now=local_dt(10))
    )
    assert len(reports) == 4  # 2 条目 × 2 通道
    assert [r.ok for r in reports] == [False, True, False, True]
    assert len(ok.calls) == 2  # 单通道失败不中断整批


def test_send_immediate_skips_same_slot_repush(registry):
    channel = RecordingChannel()
    item = {"title": "示例", "url": "https://example.com/a"}
    asyncio.run(
        send_immediate([item], channels=[channel], registry=registry, tz=TIMEZONE, now=local_dt(10))
    )
    again = asyncio.run(
        send_immediate(
            [item], channels=[channel], registry=registry, tz=TIMEZONE, now=local_dt(10, 30)
        )
    )
    assert again == []  # 同槽位已发过 → 跳过并记日志
    assert len(channel.calls) == 1
    cross = asyncio.run(
        send_immediate([item], channels=[channel], registry=registry, tz=TIMEZONE, now=local_dt(13))
    )
    assert all(r.ok for r in cross)
    assert len(channel.calls) == 2


# ---------------------------------------------------------------------------
# base:item_view 归一化 / SendContext 校验 / 通道协议
# ---------------------------------------------------------------------------


def test_item_view_merges_metadata_without_overriding_top_level():
    obj = SimpleNamespace(
        url="https://example.com/x", title="顶层标题", metadata={"symbol": "NVDA", "title": "元数据标题"}
    )
    view = item_view(obj)
    assert view["title"] == "顶层标题"  # 顶层字段优先
    assert view["symbol"] == "NVDA"
    mapping = item_view({"title": "示例", "metadata": {"change_pct": 3.2}})
    assert mapping["change_pct"] == 3.2
    assert mapping["metadata"] == {"change_pct": 3.2}


def test_send_context_rejects_unknown_slot_or_kind():
    with pytest.raises(ValueError, match="slot"):
        SendContext(slot="noon", date="2026-10-01")
    with pytest.raises(ValueError, match="kind"):
        SendContext(slot="am", date="2026-10-01", kind="batch")


def test_feishu_and_stdout_channels_conform_to_channel_protocol(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    assert isinstance(FeishuCardChannel(target="env:MYIA_TEST_CHAT_ID", token="t"), Channel)
    assert isinstance(StdoutChannel(), Channel)
    assert isinstance(TelegramChannel(), Channel)
    assert isinstance(WebhookChannel(), Channel)
    # 注册表钉死:ntfy/dingtalk/wecom 随 10-03-messaging-w2-platforms 落地,
    # weixin 随 10-03-messaging-weixin-bridge 落地(可选桥接:出站经本机
    # Hermes CLI,无目录发现)——钉死集随通道注册表演进同步(蓝图注记见
    # push/__init__.py CHANNELS 定义处;新通道自身的协议符合性由
    # tests/test_messaging_{ntfy,dingtalk,wecom,weixin_bridge}.py 专测覆盖)。
    # W3 长尾 22 家随 10-03-messaging-w3-longtail 终局接线落地(集成面专测
    # 在 tests/test_push_channels.py TestW3LongtailRegistry)。
    assert set(CHANNELS) == {
        "feishu_card",
        "telegram",
        "webhook",
        "stdout",
        "ntfy",
        "dingtalk",
        "wecom",
        "weixin",
        # ---- W3 长尾(10-03-messaging-w3-longtail)----
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
    }


@pytest.mark.parametrize("channel", [TelegramChannel(), WebhookChannel()])
def test_channel_send_failures_are_structured_reports(channel, monkeypatch):
    """telegram/webhook 已在 v0.2 实装;凭据未配置时发送失败仍经 _send_via
    转成结构化失败报告(不裸抛)。回归背景:v0.1 壳无 ``name`` 且 ``send(items)``
    签名错误,schema 合法的 ``push: [{channel: telegram}]`` 会在 _send_via 里
    裸抛 AttributeError,而不是承诺过的结构化失败。"""
    from shishi.push.digest import _send_via

    # 固定"凭据未配置"前提,不依赖宿主机环境变量
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("MYIA_WEBHOOK_URL", raising=False)

    report = asyncio.run(
        _send_via(
            channel,
            [{"title": "演示标题", "url": "https://example.com/t"}],
            SendContext(slot="am", date="2026-10-01", kind="digest"),
        )
    )
    assert report.ok is False
    assert report.channel == channel.name
    assert report.error.startswith("[env_var_missing]")
    assert report.item_count == 1


def test_feishu_template_render_error_wrapped_as_push_send_error(monkeypatch):
    """模板渲染失败(未定义变量)按契约包装为 PushSendError,不裸抛 TemplateRenderError。"""
    monkeypatch.setenv("MYIA_TEST_CHAT_ID", "oc_demo")
    channel = FeishuCardChannel(
        target="env:MYIA_TEST_CHAT_ID", token="t", template="{{ item.pirce }}"
    )
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send(
                [{"title": "演示标题", "url": "https://example.com/t"}],
                SendContext(slot="am", date="2026-10-01", kind="digest"),
            )
        )
    assert excinfo.value.code == "template_render_error"


def test_stdout_template_render_error_wrapped_as_push_send_error():
    """stdout 通道与 feishu 同契约:渲染失败包装为结构化 PushSendError。"""
    channel = StdoutChannel(template="{% for item in items %}")  # 语法错误(schema 在真实链路加载期拒)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(
            channel.send(
                [{"title": "演示标题", "url": "https://example.com/t"}],
                SendContext(slot="am", date="2026-10-01", kind="digest"),
            )
        )
    assert excinfo.value.code == "template_render_error"


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:mute 语义(0.0 是降权信号,不是真实评分)
# ---------------------------------------------------------------------------


def test_muted_item_without_rules_defaults_to_digest():
    """回归:mute 命中(enrich 置 score=0.0 + muted 标记)+ 无 route 配置时,
    不得落入 no_rules_default=immediate——否则用户明确静默的词条目反而每轮
    被立即推送(语义精确反转);应走保守缺省 digest。"""
    item = {"url": "https://a", "title": "t", "score": 0.0, "muted": "spam"}
    decision = resolve_route(item, rules=[])
    assert decision.mode == "digest"
    assert decision.reason == "conservative_default"


def test_unmuted_zero_score_without_rules_still_immediate():
    """无 muted 标记的真实 0 分仍视为「有分」:v1.7 兼容缺省 immediate 不变。"""
    decision = resolve_route({"url": "https://a", "title": "t", "score": 0.0}, rules=[])
    assert decision.mode == "immediate"
    assert decision.reason == "no_rules_default"


def test_muted_item_with_score_rule_still_archives():
    """显式 score 规则对 muted 条目照常生效(规则匹配先于缺省判定)。"""
    item = {"url": "https://a", "title": "t", "score": 0.0, "muted": "spam"}
    rules = routes_from_config([{"when": "score < 5", "mode": "archive"}])
    assert resolve_route(item, rules).mode == "archive"
