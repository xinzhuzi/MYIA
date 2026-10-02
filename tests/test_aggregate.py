"""Tests for v0.4 event aggregation (PRD 10-01-v04-event-aggregation).

验收项逐条覆盖,全部 mock LLM(零真实网络、零真实 key):

- **两级判重**:标题相似度粗筛(字符 shingle Jaccard,零 token)圈候选 →
  LLM 判重精筛(并入 enrich 批量/缓存/预算护栏);
- **3 源同事件夹具 → 1 卡**:immediate 路由下 3 条目合并为 1 张卡(主条目 +
  「另见 N 源」),digest 路由同样消费合并形态;
- **精确率样例集**:相似但不同事件不误合并(LLM 判不同 → 不合并;相似度低
  → 连 LLM 都不问);
- **LLM 判重走缓存与预算**:同对 URL 二轮零 LLM 调用(不重复计费);与精评
  共享 budget_per_run,预算耗尽降级为不合并;
- **与 dedup 正交**:先 dedup(同 URL 拦截)后 aggregate(幸存近似条目合并);
- schema ``aggregate:`` sidecar 节:缺省值/未知字段 fail-fast/越界拒载;
- prompt 模板体系:dedupe prompt 数据文件(可被反馈闭环调整)。

The ``openai`` package is never imported: every aggregator/enricher runs on an
injected fake completion client.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from conftest import run

from myia.enrich import (
    AggregateOutcome,
    EnrichConfigError,
    EnrichSettings,
    EventAggregator,
    LLMEnricher,
    pair_cache_key,
    parse_dedupe_payload,
)
from myia.enrich.aggregate import (
    DEDUPE_CACHE_KEY_PREFIX,
    coarse_groups,
    parse_item_time,
    title_shingles,
    title_similarity,
    within_window,
)
from myia.enrich.client import CompletionResult
from myia.enrich.errors import EnrichConfigError as EnrichConfigErrorAlias
from myia.enrich.prompt import load_dedupe_prompt
from myia.enrich.scoring import BudgetTracker
from myia.pipeline import Pipeline
from myia.push import SendContext, TemplateRenderer, build_card
from myia.push.telegram import build_message
from myia.schema import AggregateConfig, EnrichConfig, LoadError, load_category
from myia.store import SQLiteStore

BASE_URL = "https://llm.test.local/v1"
API_KEY = "test-key-not-real"
ENV_BASE = "MYIA_AGGREGATE_TEST_BASE"
ENV_KEY = "MYIA_AGGREGATE_TEST_KEY"

SAME_EVENT_TITLE = "OpenAI 发布 GPT-6:推理能力大幅提升"


# ---------------------------------------------------------------------------
# Helpers(每测独立建库/独立 fake,零共享状态)
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """A throwaway SQLiteStore per test (缓存与 items 表互不污染)."""
    backend = SQLiteStore(tmp_path / "aggregate.db")
    yield backend
    backend.close()


@pytest.fixture()
def endpoint_env(monkeypatch):
    """Set the endpoint env vars; tests read them back via env: references."""
    monkeypatch.setenv(ENV_BASE, BASE_URL)
    monkeypatch.setenv(ENV_KEY, API_KEY)
    return {ENV_BASE: BASE_URL, ENV_KEY: API_KEY}


def make_settings(**overrides: Any) -> EnrichSettings:
    """Valid endpoint settings over the test env vars, with overrides."""
    data: dict[str, Any] = {
        "base_url_ref": f"env:{ENV_BASE}",
        "api_key_ref": f"env:{ENV_KEY}",
    }
    data.update(overrides)
    return EnrichSettings(**data)


def make_enrich_config(**overrides: Any) -> EnrichConfig:
    data: dict[str, Any] = {"enabled": True}
    data.update(overrides)
    return EnrichConfig(**data)


def make_aggregate_config(**overrides: Any) -> AggregateConfig:
    data: dict[str, Any] = {"enabled": True}
    data.update(overrides)
    return AggregateConfig(**data)


def make_aggregator(client: Any, **config_overrides: Any) -> EventAggregator:
    return EventAggregator(
        make_aggregate_config(**config_overrides),
        make_enrich_config(),
        make_settings(),
        client=client,
    )


class FakeItem:
    """Duck-typed pipeline item (url/title/source/metadata/scores/content)."""

    def __init__(
        self,
        url: str,
        title: str,
        *,
        source: str | None = "fake",
        category: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
        score: float | None = None,
    ) -> None:
        self.url = url
        self.title = title
        self.source = source
        self.category = category
        self.content = content
        self.scores: dict | None = None
        self.metadata: dict = dict(metadata or {})
        if content:
            self.metadata["content"] = content
        if score is not None:
            self.metadata["score"] = score
        self.dedup_key = f"key-{url}"

    def add_tags(self, tags: list[str]) -> None:
        existing = self.metadata.get("tags")
        self.metadata["tags"] = list(dict.fromkeys([*(existing or []), *tags]))

    def view(self) -> dict[str, Any]:
        view = dict(self.metadata)
        view.update({"url": self.url, "title": self.title, "dedup_key": self.dedup_key})
        return view


@dataclass
class FakeDedupeClient:
    """Scripted completion client: pops one response per call, records all."""

    responses: list[str] = field(default_factory=list)
    tokens_per_call: int = 60
    fail_on_call: set[int] = field(default_factory=set)  # 1-based
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete(self, *, model: str, system: str, user: str) -> CompletionResult:
        self.calls.append({"model": model, "system": system, "user": user})
        text = self.responses.pop(0) if self.responses else "[]"
        if len(self.calls) in self.fail_on_call:
            raise RuntimeError("模拟端点故障")
        return CompletionResult(text=text, total_tokens=self.tokens_per_call)


def dedupe_response(groups: list[dict[str, Any]], *, wrapper: bool = False) -> str:
    """Build one batch's model reply (optional {"groups": ...} wrapper)."""
    text = json.dumps(groups, ensure_ascii=False)
    return json.dumps({"groups": groups}, ensure_ascii=False) if wrapper else text


def same_cluster(group_id: int, indices: list[int]) -> dict[str, Any]:
    return {"id": group_id, "clusters": [indices]}


# ---------------------------------------------------------------------------
# Level 1: 标题相似度粗筛(零 token)
# ---------------------------------------------------------------------------


class TestTitleSimilarity:
    def test_identical_titles_normalize_to_full_similarity(self):
        # 空白/标点/大小写差异不降相似度(归一化后同集)
        assert title_similarity(SAME_EVENT_TITLE, SAME_EVENT_TITLE) == 1.0
        assert (
            title_similarity("iPhone 18 发布!", "iphone18发布")
            == 1.0
        )

    def test_disjoint_titles_have_zero_similarity(self):
        assert title_similarity("苹果发布新手机", "证监会处罚某券商") == 0.0
        assert title_similarity("", "") == 0.0  # 空标题永不匹配

    def test_shingles_char_bigrams(self):
        assert title_shingles("ab") == frozenset({"ab"})  # 短标题整串兜底
        assert title_shingles("abc") == frozenset({"ab", "bc"})
        assert title_shingles("   ") == frozenset()

    def test_precision_sample_set_similar_but_different_events(self):
        """精确率样例集:同一主体的不同场次/版本/产品,相似度必须低于阈值
        (粗筛不圈 → LLM 零 token;圈进候选的由 LLM 精筛兜底)。"""
        below_threshold = [
            ("苹果发布 iOS 19 正式版", "苹果发布 M5 芯片笔记本"),
            ("特斯拉 Q3 交付破纪录", "特斯拉 Q4 财报超预期"),
            ("某银行信用卡满减活动", "某银行储蓄卡利率上调"),
        ]
        for a, b in below_threshold:
            assert title_similarity(a, b) < 0.6, (a, b)
        # 同事件跨源转述(措辞略异)必须在阈值之上(召回方向)
        assert title_similarity(
            "OpenAI 发布 GPT-6:推理能力大幅提升", "OpenAI 发布 GPT-6 推理能力大幅提升"
        ) >= 0.6

    def test_coarse_groups_three_sources_same_event(self):
        items = [
            FakeItem(f"https://s{i}.example/post", SAME_EVENT_TITLE) for i in range(3)
        ]
        groups = coarse_groups(items, threshold=0.6, window_hours=24.0)
        assert groups == [[0, 1, 2]]

    def test_coarse_groups_unrelated_items_stay_separate(self):
        items = [
            FakeItem("https://a", "苹果发布 iOS 19 正式版"),
            FakeItem("https://b", "特斯拉 Q3 交付破纪录"),
        ]
        assert coarse_groups(items, threshold=0.6, window_hours=24.0) == []

    def test_coarse_groups_window_filters_far_apart_timestamps(self):
        late = "2026-10-02T12:00:00+00:00"
        early = "2026-09-30T12:00:00+00:00"  # 恰好 48h 前
        items = [
            FakeItem("https://a", SAME_EVENT_TITLE, metadata={"published_at": late}),
            FakeItem("https://b", SAME_EVENT_TITLE, metadata={"published_at": early}),
        ]
        assert coarse_groups(items, threshold=0.6, window_hours=24.0) == []
        assert coarse_groups(items, threshold=0.6, window_hours=48.0) == [[0, 1]]

    def test_coarse_groups_missing_timestamp_stays_in_window(self):
        # 缺时间戳无法排除在窗外:仍进候选(无法排除 ≠ 排除)
        items = [
            FakeItem("https://a", SAME_EVENT_TITLE, metadata={"published_at": "2026-10-02T12:00:00+00:00"}),
            FakeItem("https://b", SAME_EVENT_TITLE),
        ]
        assert coarse_groups(items, threshold=0.6, window_hours=24.0) == [[0, 1]]

    def test_parse_item_time_and_window(self):
        item = FakeItem("https://a", "t", metadata={"time": "2026-10-01T08:00:00+08:00"})
        parsed = parse_item_time(item)
        assert parsed == datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        assert parse_item_time(FakeItem("https://a", "t")) is None
        assert parse_item_time(FakeItem("https://a", "t", metadata={"time": "垃圾"})) is None
        assert within_window(parsed, parsed, 24.0)
        assert not within_window(
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 3, tzinfo=timezone.utc),  # 相差 48h
            24.0,
        )
        assert within_window(
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 2, tzinfo=timezone.utc),  # 恰好 24h:含边界
            24.0,
        )

    def test_coarse_groups_rejects_bad_threshold(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            coarse_groups([], threshold=0.0, window_hours=24.0)
        with pytest.raises(ValueError, match="window_hours"):
            within_window(
                datetime.now(timezone.utc), datetime.now(timezone.utc), 0.0
            )


# ---------------------------------------------------------------------------
# Level 2 响应解析:容错与结构化失败
# ---------------------------------------------------------------------------


class TestParseDedupePayload:
    def test_valid_clusters_with_string_ids_tolerated(self):
        clusters, failures = parse_dedupe_payload(
            dedupe_response([same_cluster("0", [0, 2]), {"id": 1, "clusters": []}]),
            {0: 3, 1: 2},
        )
        assert clusters == {0: [[0, 2]], 1: []}
        assert failures == []

    def test_groups_wrapper_tolerated(self):
        clusters, failures = parse_dedupe_payload(
            dedupe_response([same_cluster(0, [0, 1])], wrapper=True), {0: 2}
        )
        assert clusters == {0: [[0, 1]]}
        assert failures == []

    def test_unknown_group_id_reported_not_fatal(self):
        clusters, failures = parse_dedupe_payload(
            dedupe_response([{"id": 9, "clusters": [[0, 1]]}]), {0: 2}
        )
        assert clusters == {}
        assert failures[0]["error_type"] == "aggregate_unknown_group"

    def test_out_of_range_and_single_indices_dropped(self):
        clusters, failures = parse_dedupe_payload(
            dedupe_response([{"id": 0, "clusters": [[0, 5], [1], [0, 1, 0], "x"]}]),
            {0: 3},
        )
        assert clusters == {0: [[0, 1]]}  # 越界/单元素/重复/非数组全部丢弃
        assert len(failures) == 1  # "x" 不是数组 → 结构化失败记录
        assert failures[0]["error_type"] == "aggregate_cluster_not_list"

    def test_unparseable_payload_raises(self):
        with pytest.raises(Exception):  # DedupeParseError(batch 级)
            parse_dedupe_payload("模型开始闲聊", {0: 2})


# ---------------------------------------------------------------------------
# EventAggregator:合并 / 缓存 / 预算 / 降级(全部 mock LLM)
# ---------------------------------------------------------------------------


class TestEventAggregator:
    def three_sources(self) -> list[FakeItem]:
        return [
            FakeItem(f"https://s{i}.example/post", SAME_EVENT_TITLE) for i in range(3)
        ]

    def test_same_event_merges_and_reports_components(self, store, endpoint_env):
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        items = self.three_sources()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert isinstance(outcome, AggregateOutcome)
        assert (outcome.coarse_groups, outcome.merged_groups, outcome.absorbed_items) == (1, 1, 2)
        assert outcome.components == [[0, 1, 2]]
        assert outcome.llm_calls == 1
        assert outcome.tokens_used == 60
        assert outcome.degraded is False
        # prompt 携带组与条目(标题/来源/组 id)
        assert SAME_EVENT_TITLE in client.calls[0]["user"]
        assert '"id": 0' in client.calls[0]["user"]

    def test_similar_but_different_events_not_merged(self, store, endpoint_env):
        # LLM 判定全不同(空 clusters)→ 零合并,精确率由 L2 保证
        client = FakeDedupeClient(responses=[dedupe_response([{"id": 0, "clusters": []}])])
        items = self.three_sources()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert outcome.merged_groups == 0
        assert outcome.components == []
        assert outcome.absorbed_items == 0

    def test_partial_partition_merges_only_confirmed_pairs(self, store, endpoint_env):
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 2])])])
        items = self.three_sources()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert outcome.components == [[0, 2]]
        assert outcome.absorbed_items == 1

    def test_cache_hit_never_rebills_same_pair(self, store, endpoint_env):
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        aggregator = make_aggregator(client)

        first = run(aggregator.aggregate(self.three_sources(), store=store))
        assert first.llm_calls == 1
        second = run(aggregator.aggregate(self.three_sources(), store=store))

        # 同对 URL 二轮:全对缓存命中,零 LLM 调用(不重复计费)
        assert len(client.calls) == 1
        assert second.llm_calls == 0
        assert second.cached_groups == 1
        assert second.merged_groups == 1
        assert second.tokens_used == 0
        cached = store.get_enrich_cache(
            pair_cache_key("https://s0.example/post", "https://s1.example/post"),
            "glm-4-flash",
            f"{DEDUPE_CACHE_KEY_PREFIX}@p1",
        )
        assert cached == {"same": True}

    def test_cache_records_negative_verdicts_too(self, store, endpoint_env):
        client = FakeDedupeClient(responses=[dedupe_response([{"id": 0, "clusters": []}])])
        aggregator = make_aggregator(client)
        run(aggregator.aggregate(self.three_sources(), store=store))
        cached = store.get_enrich_cache(
            pair_cache_key("https://s0.example/post", "https://s1.example/post"),
            "glm-4-flash",
            f"{DEDUPE_CACHE_KEY_PREFIX}@p1",
        )
        assert cached == {"same": False}

    def test_dedupe_prompt_version_invalidates_cache(self, store, endpoint_env):
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        run(make_aggregator(client).aggregate(self.three_sources(), store=store))

        rebased = EventAggregator(
            make_aggregate_config(),
            make_enrich_config(),
            make_settings(dedupe_prompt_version=99),
            client=client,
        )
        assert rebased.prompt_version == 99
        outcome = run(rebased.aggregate(self.three_sources(), store=store))

        assert outcome.cached_groups == 0
        assert len(client.calls) == 2  # 指纹变更 → 重问

    def test_exhausted_budget_degrades_to_no_merge(self, store, endpoint_env):
        client = FakeDedupeClient()
        budget = BudgetTracker(limit=500)
        budget.spend(500)  # 精评已把本轮预算花完

        outcome = run(
            make_aggregator(client).aggregate(self.three_sources(), store=store, budget=budget)
        )

        assert outcome.degraded is True
        assert outcome.degrade_reason == "budget_exhausted"
        assert outcome.merged_groups == 0
        assert outcome.llm_calls == 0
        assert len(client.calls) == 0  # 预算到顶一个请求都不发

    def test_budget_exhausted_midway_only_first_batch_merges(self, store, endpoint_env):
        # 两组候选(两个不同事件各两源),batch=1、预算仅够一批:第二批降级
        event_a = "某厂发布 AI 编程助手 Copilot X"
        event_b = "某厂开源大模型 Qwen-99"
        items = [
            FakeItem("https://a1", event_a),
            FakeItem("https://a2", event_a),
            FakeItem("https://b1", event_b),
            FakeItem("https://b2", event_b),
        ]
        client = FakeDedupeClient(
            responses=[
                dedupe_response([same_cluster(0, [0, 1])]),
                dedupe_response([same_cluster(1, [0, 1])]),
            ]
        )

        outcome = run(
            EventAggregator(
                make_aggregate_config(),
                make_enrich_config(batch=1, budget_per_run=60),
                make_settings(),
                client=client,
            ).aggregate(items, store=store)
        )

        assert outcome.components == [[0, 1]]  # 仅第一组合并
        assert outcome.degraded is True
        assert outcome.degrade_reason == "budget_exhausted"
        assert outcome.coarse_groups == 2
        assert len(client.calls) == 1

    def test_batch_failure_isolated_and_stays_unmerged(self, store, endpoint_env):
        client = FakeDedupeClient(fail_on_call={1})
        items = self.three_sources()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert outcome.degraded is True
        assert outcome.degrade_reason == "llm_batch_failed"
        assert outcome.merged_groups == 0
        assert {f["error_type"] for f in outcome.failures} == {"aggregate_batch_failed"}
        assert len(outcome.failures) == 3  # 组内每条一条结构化记录

    def test_unparseable_response_fails_batch_not_run(self, store, endpoint_env):
        client = FakeDedupeClient(responses=["抱歉,我无法判断"])
        items = self.three_sources()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert outcome.degraded is True
        assert outcome.degrade_reason == "llm_batch_failed"
        assert {f["error_type"] for f in outcome.failures} == {"aggregate_parse_error"}
        assert outcome.merged_groups == 0

    def test_missing_group_answer_stays_unmerged_and_reasked(self, store, endpoint_env):
        # 两组候选:模型只答了组 0;组 1 不合并且不写缓存(下轮重问)
        event_a = "某厂发布 AI 编程助手 Copilot X"
        event_b = "某厂开源大模型 Qwen-99"
        items = [
            FakeItem("https://a1", event_a),
            FakeItem("https://a2", event_a),
            FakeItem("https://b1", event_b),
            FakeItem("https://b2", event_b),
        ]
        client = FakeDedupeClient(
            responses=[
                dedupe_response([same_cluster(0, [0, 1])]),
                dedupe_response([{"id": 9, "clusters": []}]),  # 答非所问
            ]
        )
        aggregator = EventAggregator(
            make_aggregate_config(), make_enrich_config(batch=1), make_settings(), client=client
        )
        first = run(aggregator.aggregate(items, store=store))
        assert first.components == [[0, 1]]

        second = run(aggregator.aggregate(items, store=store))

        assert second.cached_groups == 1  # 组 0 走缓存
        assert second.components == [[0, 1]]  # 组 1 重问后照常合并
        assert len(client.calls) == 3  # 2(首轮)+ 1(组 1 重问)

    def test_window_out_of_range_skips_llm_entirely(self, store, endpoint_env):
        late = "2026-10-02T12:00:00+00:00"
        early = "2026-09-30T12:00:00+00:00"
        items = [
            FakeItem("https://a", SAME_EVENT_TITLE, metadata={"published_at": late}),
            FakeItem("https://b", SAME_EVENT_TITLE, metadata={"published_at": early}),
        ]
        client = FakeDedupeClient()

        outcome = run(make_aggregator(client).aggregate(items, store=store))

        assert outcome.coarse_groups == 0  # 窗外连候选都不是
        assert len(client.calls) == 0  # 零 token

    def test_fewer_than_two_items_short_circuits(self, store, endpoint_env):
        outcome = run(make_aggregator(FakeDedupeClient()).aggregate([], store=store))
        assert outcome.requested == 0
        assert outcome.coarse_groups == 0

    def test_endpoint_env_missing_fails_fast(self, monkeypatch):
        monkeypatch.delenv(ENV_BASE, raising=False)
        with pytest.raises(EnrichConfigError) as excinfo:
            EventAggregator(
                make_aggregate_config(), make_enrich_config(), make_settings(), client=FakeDedupeClient()
            )
        assert excinfo.value.code == "credential_unresolved"

    def test_dedupe_prompt_override_and_bad_placeholder(self, tmp_path, endpoint_env):
        good = tmp_path / "dedupe.json"
        good.write_text(
            json.dumps(
                {
                    "version": 7,
                    "system": "判重系统提示",
                    "user_template": "窗口 {{WINDOW_HOURS}} 组 {{GROUPS_JSON}}",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        aggregator = EventAggregator(
            make_aggregate_config(),
            make_enrich_config(),
            make_settings(dedupe_prompt_path=str(good)),
            client=FakeDedupeClient(),
        )
        assert aggregator.prompt_version == 7

        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"version": 1, "system": "s", "user_template": "评分 {{SCORES}}"}),
            encoding="utf-8",
        )
        with pytest.raises(EnrichConfigErrorAlias) as excinfo:
            EventAggregator(
                make_aggregate_config(),
                make_enrich_config(),
                make_settings(dedupe_prompt_path=str(bad)),
                client=FakeDedupeClient(),
            )
        assert excinfo.value.code == "prompt_invalid"

    def test_packaged_dedupe_prompt_loads(self):
        template = load_dedupe_prompt()
        assert template.version >= 1
        rendered = template.render_groups(window_hours=24, groups_json="[]")
        assert "24" in rendered


# ---------------------------------------------------------------------------
# schema ``aggregate:`` sidecar 节
# ---------------------------------------------------------------------------


def load_with_aggregate(aggregate: object) -> Any:
    data: dict[str, Any] = {
        "id": "agg-schema",
        "name": "聚合模式",
        "schedule": "0 9 * * *",
        "sources": [{"name": "s", "url": "https://example.com/list"}],
    }
    if aggregate is not None:
        data["aggregate"] = aggregate
    return load_category(data)


class TestAggregateSchema:
    def test_defaults_are_conservative_off(self):
        config = AggregateConfig()
        assert config.enabled is False
        assert config.window_hours == 24.0
        assert config.similarity_threshold == 0.6

    def test_sidecar_loads_and_attaches_via_load_category(self):
        config = load_with_aggregate({"enabled": True, "window_hours": 12, "similarity_threshold": 0.5})
        assert isinstance(config.aggregate, AggregateConfig)
        assert (config.aggregate.enabled, config.aggregate.window_hours, config.aggregate.similarity_threshold) == (
            True,
            12.0,
            0.5,
        )

    def test_absent_and_null_both_yield_none(self):
        assert load_with_aggregate(None).aggregate is None
        assert load_category(
            {
                "id": "agg-schema",
                "name": "聚合模式",
                "schedule": "0 9 * * *",
                "sources": [{"name": "s", "url": "https://example.com/list"}],
            }
        ).aggregate is None

    def test_never_grows_the_twelve_section_contract(self):
        # sidecar 机制:12 节公开契约(SKILL.md 逐字段锁定)不随之增长
        assert "aggregate" not in type(load_with_aggregate(None)).model_fields
        assert "baseline" not in type(load_with_aggregate(None)).model_fields

    def test_unknown_field_fail_fast_with_section_path(self):
        with pytest.raises(LoadError) as excinfo:
            load_with_aggregate({"enabled": True, "oops": 1})
        assert any(e.path == "$.aggregate.oops" and e.error_type == "unknown_field" for e in excinfo.value.errors)

    def test_non_mapping_section_rejected(self):
        with pytest.raises(LoadError) as excinfo:
            load_with_aggregate(["enabled"])
        assert excinfo.value.errors[0].error_type == "invalid_aggregate_section"

    def test_threshold_and_window_bounds_rejected(self):
        for bad in ({"similarity_threshold": 0}, {"similarity_threshold": 1.5}, {"window_hours": 0},
                    {"window_hours": -1}, {"window_hours": float("nan")}):
            with pytest.raises(LoadError):
                load_with_aggregate(bad)
        edge = load_with_aggregate({"enabled": True, "similarity_threshold": 1})
        assert edge.aggregate.similarity_threshold == 1.0  # 上界含 1(全同才合并)


# ---------------------------------------------------------------------------
# 管线接线:dedup 之后、push 之前;3 源同事件 → 1 卡;与 dedup 正交
# ---------------------------------------------------------------------------


def make_category(
    *,
    aggregate: dict[str, Any] | None = None,
    enrich: dict[str, Any] | None = None,
    route_mode: str = "immediate",
    sources: list[dict[str, Any]] | None = None,
) -> Any:
    data: dict[str, Any] = {
        "id": "agg-demo",
        "name": "聚合演示",
        "schedule": "0 9 * * *",
        "sources": sources or [
            {
                "name": name,
                "engine": "direct_api",
                "url": f"https://{name}.demo.local/list",
                "extract": {"type": "json_path", "fields": {"title": "$[*].title", "url": "$[*].url"}},
            }
            for name in ("s1", "s2", "s3")
        ],
        "classify": {"builtin": False, "rules": []},
        "enrich": enrich
        if enrich is not None
        else {"enabled": False, "base_url": f"env:{ENV_BASE}", "api_key": f"env:{ENV_KEY}"},
        "push": [{"channel": "stdout", "route": [{"when": "url", "mode": route_mode}]}],
    }
    if aggregate is not None:
        data["aggregate"] = aggregate
    return load_category(data)


def demo_handler(titles_by_host: dict[str, list[dict[str, str]]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")  # fail-open,允许抓取
        return httpx.Response(200, json=titles_by_host[request.url.host])

    return handler


def run_pipeline(
    tmp_path,
    config,
    handler,
    *,
    aggregator=None,
    enricher=None,
    db_name="pipe.db",
    store=None,
):
    own_store = store is None
    backend = store or SQLiteStore(tmp_path / db_name)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        pipeline = Pipeline(
            config, store=backend, client=client, aggregator=aggregator, enricher=enricher
        )
        return run(pipeline.run()), backend
    finally:
        run(client.aclose())
        if own_store:
            backend.close()


class TestPipelineAggregateStage:
    def test_three_sources_same_event_merge_into_one_card(self, tmp_path, endpoint_env, capsys):
        """验收:3 源同事件夹具 → 1 卡(immediate,主条目 + 另见 2 源)。"""
        config = make_category(aggregate={"enabled": True})
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        aggregator = EventAggregator(
            config.aggregate, config.enrich, make_settings(), client=client
        )

        result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator)

        assert [s.name for s in result.stages] == [
            "fetch", "classify", "dedup", "analyze", "aggregate", "push",
        ]
        assert result.stage("aggregate").status == "ok"
        # 3 条目合并为 1:主条目 + 另见 2 源
        assert len(result.items) == 1
        merged = result.items[0]
        assert merged.metadata["merged_sources"] == 3
        assert [entry["source"] for entry in merged.metadata["also_seen"]] == ["s2", "s3"]
        assert "aggregated" in merged.metadata["tags"]
        assert merged.dedup_key == "https://s1.demo.local/post/1"  # 先出现者为主条目
        # 1 卡:immediate 桶 1 条、1 次发送
        assert result.pushes[0].immediate == 1
        assert len(result.pushes[0].reports) == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["count"] == 1
        assert payload["items"][0]["also_seen"][0]["url"] == "https://s2.demo.local/post/1"
        assert len(client.calls) == 1

    def test_digest_route_renders_merged_card_too(self, tmp_path, endpoint_env, capsys):
        """验收:digest 路由同样消费合并形态(单卡含另见)。"""
        config = make_category(aggregate={"enabled": True}, route_mode="digest")
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator)

        assert result.pushes[0].digest == 1
        assert len(result.pushes[0].reports) == 1  # 一张摘要卡
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["kind"] == "digest"
        assert payload["count"] == 1
        assert payload["items"][0]["merged_sources"] == 3

    def test_similar_but_different_events_not_merged_in_pipeline(self, tmp_path, endpoint_env):
        """验收:精确率样例集——相似标题、LLM 判不同 → 3 张卡,零误合并。"""
        config = make_category(aggregate={"enabled": True})
        similar_titles = [
            "苹果发布 iOS 19 正式版",
            "苹果发布 iOS 19.1 正式版",  # 相似但不同版本(不同事件)
            "苹果发布 iOS 19 正式版!",  # 同事件变体
        ]
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": similar_titles[i - 1], "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        # 粗筛会圈出候选组,LLM 判定:0 与 2 同事件,1 不同
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 2])])])
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator)

        assert len(result.items) == 2  # [0,2] 合并,1 独立
        merged_urls = {item.url for item in result.items if item.metadata.get("merged_sources")}
        assert merged_urls == {"https://s1.demo.local/post/1"}
        assert result.pushes[0].immediate == 2
        assert len(result.pushes[0].reports) == 2  # 2 张卡,不是 3 张也不是 1 张

    def test_aggregate_disabled_keeps_v03_stage_list(self, tmp_path, endpoint_env):
        config = make_category()  # 无 aggregate 节
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })

        result, _ = run_pipeline(tmp_path, config, handler)

        assert [s.name for s in result.stages] == ["fetch", "classify", "dedup", "analyze", "push"]
        assert len(result.items) == 3  # 不合并:3 条照推
        assert result.pushes[0].immediate == 3

    def test_dedup_first_then_aggregate_orthogonal(self, tmp_path, endpoint_env):
        """验收:与 dedup 正交——同 URL 先被 dedup 拦截,幸存近似条目再合并。"""
        config = make_category(aggregate={"enabled": True}, sources=[
            {
                "name": "s1",
                "engine": "direct_api",
                "url": "https://s1.demo.local/list",
                "extract": {"type": "json_path", "fields": {"title": "$[*].title", "url": "$[*].url"}},
            },
            {
                "name": "s2",
                "engine": "direct_api",
                "url": "https://s2.demo.local/list",
                "extract": {"type": "json_path", "fields": {"title": "$[*].title", "url": "$[*].url"}},
            },
        ])
        handler = demo_handler({
            # s2 与 s1 完全同 URL(标题都一样)→ dedup 拦截;唯一条目再无候选
            "s1.demo.local": [{"title": SAME_EVENT_TITLE, "url": "https://news.example/post/1"}],
            "s2.demo.local": [{"title": SAME_EVENT_TITLE, "url": "https://news.example/post/1"}],
        })
        client = FakeDedupeClient()
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator)

        assert result.stage("dedup").skips["dedup_seen"] == 1  # 同 URL 被去重层拦截
        assert len(result.items) == 1
        assert "merged_sources" not in result.items[0].metadata  # 剩 1 条,无可合并
        assert len(client.calls) == 0  # 聚合层零 LLM(不足两条)

    def test_llm_dedup_reuses_shared_budget_with_scoring(self, tmp_path, endpoint_env, caplog):
        """验收:LLM 判重与精评共享 budget_per_run——精评花完,判重零调用降级。"""
        enrich = {
            "enabled": True,
            "batch": 1,
            "budget_per_run": 60,
            "base_url": f"env:{ENV_BASE}",
            "api_key": f"env:{ENV_KEY}",
        }
        config = make_category(aggregate={"enabled": True}, enrich=enrich)
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        client = FakeDedupeClient(
            responses=[
                # 前 2 个响应给 enrich 批(batch=1 → 2 次调用),预算随之耗尽
                json.dumps([{"url": "https://s1.demo.local/post/1", "value": 9, "relevance": 9, "credibility": 9}]),
                json.dumps([{"url": "https://s2.demo.local/post/1", "value": 9, "relevance": 9, "credibility": 9}]),
            ]
        )
        enricher = LLMEnricher(config.enrich, make_settings(), client=client)
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        with caplog.at_level(logging.WARNING, logger="myia.pipeline"):
            result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator, enricher=enricher)

        # 精评第一批花掉 60 即到顶 → 剩余条目与判重阶段全部降级零调用
        assert len(client.calls) == 1
        aggregate_stage = result.stage("aggregate")
        assert aggregate_stage.status == "ok"
        assert aggregate_stage.skips.get("aggregate_degraded_budget_exhausted") == 1
        assert len(result.items) == 3  # 降级 = 不合并,照常推送
        assert "降级" in caplog.text

    def test_llm_dedup_and_scoring_share_budget_pool_happy_path(self, tmp_path, endpoint_env):
        enrich = {
            "enabled": True,
            "batch": 20,
            "budget_per_run": 100_000,
            "base_url": f"env:{ENV_BASE}",
            "api_key": f"env:{ENV_KEY}",
        }
        config = make_category(aggregate={"enabled": True}, enrich=enrich)
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        score_payload = json.dumps(
            [{"url": f"https://s{i}.demo.local/post/1", "value": 9, "relevance": 9, "credibility": 9} for i in (1, 2, 3)]
        )
        client = FakeDedupeClient(
            responses=[
                score_payload,  # enrich:一次批调用
                dedupe_response([same_cluster(0, [0, 1, 2])]),  # 判重:一次批调用
            ]
        )
        enricher = LLMEnricher(config.enrich, make_settings(), client=client)
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        result, _ = run_pipeline(tmp_path, config, handler, aggregator=aggregator, enricher=enricher)

        assert len(client.calls) == 2  # 精评 1 + 判重 1(共用一个客户端与预算)
        assert len(result.items) == 1  # 3 源合并为 1
        assert result.items[0].metadata["score"] == 9.0  # 主条目带精评分
        assert result.items[0].metadata["merged_sources"] == 3

    def test_aggregate_llm_cache_via_shared_store(self, tmp_path, endpoint_env):
        """判重缓存落 store:同 URL 对二轮(新管线实例)零 LLM 调用。"""
        config = make_category(aggregate={"enabled": True})
        handler = demo_handler({
            f"s{i}.demo.local": [{"title": SAME_EVENT_TITLE, "url": f"https://s{i}.demo.local/post/1"}]
            for i in (1, 2, 3)
        })
        client = FakeDedupeClient(responses=[dedupe_response([same_cluster(0, [0, 1, 2])])])
        aggregator = EventAggregator(config.aggregate, config.enrich, make_settings(), client=client)

        store = SQLiteStore(tmp_path / "shared.db")
        try:
            run_pipeline(tmp_path, config, handler, aggregator=aggregator, store=store)
            assert len(client.calls) == 1
            # 同 store 的第二轮:dedup 全拦(同 URL)→ 聚合层零候选零调用
            second, _ = run_pipeline(
                tmp_path, config, handler, aggregator=aggregator, store=store, db_name="unused.db"
            )
            assert len(client.calls) == 1  # 无新增调用
            assert second.stage("aggregate").items_in == 0
        finally:
            store.close()

    def test_merged_items_select_highest_score_as_main_entry(self, tmp_path, endpoint_env):
        """主条目选择:组内标量 score 最高者为主,其余进「另见」;无分平分取先。"""
        items = [
            FakeItem("https://s1.demo.local/post/1", SAME_EVENT_TITLE, score=3.0),
            FakeItem("https://s2.demo.local/post/1", SAME_EVENT_TITLE, score=9.0),
            FakeItem("https://s3.demo.local/post/1", SAME_EVENT_TITLE),  # 无分视为 -1
        ]
        # 管线实例仅承载合并方法(不跑 run,不触网)
        pipeline = Pipeline(make_category())

        merged = pipeline._merge_items(items, [[0, 1, 2]])

        assert len(merged) == 1
        assert merged[0].url == "https://s2.demo.local/post/1"  # 高分者为主条目
        assert merged[0].metadata["also_seen"][0]["url"] == "https://s1.demo.local/post/1"
        assert merged[0].metadata["merged_sources"] == 3

    def test_merge_items_passes_unmatched_items_through(self, tmp_path, endpoint_env):
        pipeline = Pipeline(make_category())
        items = [
            FakeItem("https://a", SAME_EVENT_TITLE),
            FakeItem("https://b", SAME_EVENT_TITLE),
            FakeItem("https://c", "无关条目"),
        ]

        merged = pipeline._merge_items(items, [[0, 1]])

        assert [item.url for item in merged] == ["https://a", "https://c"]
        assert merged[0].metadata["merged_sources"] == 2
        assert "merged_sources" not in merged[1].metadata
        # 空组件 = 原样返回(同一列表)
        assert pipeline._merge_items(items, []) is items

    def test_stage_options_accept_aggregate_stage(self, tmp_path, endpoint_env):
        config = make_category()
        pipeline = Pipeline(
            config,
            store=SQLiteStore(tmp_path / "opt.db"),
            stage_options={"aggregate": __import__("myia.pipeline", fromlist=["StageOptions"]).StageOptions(timeout_seconds=5.0)},
        )
        assert pipeline._stage_options["aggregate"].timeout_seconds == 5.0
        pipeline.close()


# ---------------------------------------------------------------------------
# push 渲染:合并单卡「另见 N 源」(feishu/telegram 内置布局 + 用户模板)
# ---------------------------------------------------------------------------


def merged_item() -> FakeItem:
    item = FakeItem("https://main.example/post", SAME_EVENT_TITLE)
    item.metadata["also_seen"] = [
        {"title": "转述甲", "url": "https://a.example/post", "source": "源甲"},
        {"title": "转述乙", "url": "https://b.example/post", "source": "源乙"},
    ]
    item.metadata["merged_sources"] = 3
    return item


class TestMergedCardRendering:
    def test_feishu_builtin_card_contains_also_seen(self):
        card = build_card([merged_item()], title="测试")
        content = card["elements"][0]["text"]["content"]
        assert "另见 2 源" in content
        assert "https://a.example/post" in content
        assert "https://b.example/post" in content

    def test_feishu_card_without_merge_unchanged(self):
        card = build_card([FakeItem("https://x", "普通条目")], title="测试")
        content = card["elements"][0]["text"]["content"]
        assert "另见" not in content

    def test_telegram_builtin_message_contains_also_seen_line(self):
        text = build_message([merged_item()], SendContext(slot="am", date="2026-10-02"))
        assert "另见 2 源" in text
        assert '<a href="https://a.example/post">转述甲</a>' in text
        # 每行独立受限:另见行是独立一行(行边界切割安全)
        assert any(line.startswith("　└ 另见") for line in text.splitlines())

    def test_telegram_also_line_truncates_over_limit_safely(self):
        item = FakeItem("https://main", "主")
        long_title = "长" * 900
        item.metadata["also_seen"] = [
            {"title": long_title, "url": "https://a.example/x", "source": "a"},
            {"title": long_title, "url": "https://b.example/x", "source": "b"},
        ]
        text = build_message([item], SendContext(slot="am", date="2026-10-02"))
        also_lines = [line for line in text.splitlines() if "另见" in line]
        assert len(also_lines) == 1
        line = also_lines[0]
        assert len(line) <= 1024  # 行级硬上限(与单条目布局同一安全论证)
        assert "…等 2 源" in line

    def test_user_template_reads_also_seen(self):
        rendered = TemplateRenderer().render(
            "{% for item in items %}{{ item.title }}(+{{ item.also_seen | length }}){% endfor %}",
            [merged_item()],
            SendContext(slot="am", date="2026-10-02", category="聚合演示"),
        )
        assert rendered == f"{SAME_EVENT_TITLE}(+2)"

    def test_also_seen_list_cleans_dirty_entries(self):
        from myia.push import also_seen_list

        item = FakeItem("https://x", "t")
        item.metadata["also_seen"] = [
            {"title": "好条目", "url": "https://ok", "source": "s"},
            {"title": "无链接", "source": "s2"},  # url 缺失 → 退回空串
            "垃圾条目",  # 非映射 → 丢弃
            {"url": "https://no-title"},  # 无标题 → 丢弃
        ]
        entries = also_seen_list(item)
        assert [e["title"] for e in entries] == ["好条目", "无链接"]
        assert entries[1]["url"] == ""
        assert also_seen_list(FakeItem("https://y", "t")) == []  # 未合并 → 空列表
