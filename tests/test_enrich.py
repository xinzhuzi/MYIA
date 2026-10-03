"""Tests for myia.enrich — LLM 精评:批量 / 缓存 / 预算护栏 / mute 降权.

Covers PRD 10-01-v02-enrich-llm acceptance criteria, all on **mock LLM**
(零真实网络、零真实 key):

- 打分解析/容错(JSON 数组 / 代码围栏 / dict 包装 / 越界钳制 / 缺维度单条失败 /
  整体不可解析仅废该批);
- 缓存命中不重评(第二次 run LLM 调用次数不增),prompt 版本变更即失效,
  手动失效(clear_enrich_cache)后重评;
- 预算耗尽触发降级(剩余条目不评分、WARNING 可见、路由退回 v0.1 行为);
- mute 命中本地降权(不消耗 token,三维 0 分 + muted 标签);
- 缺 base_url / base_url 明文(未用凭据引用)结构化报错(grill Q6),同 api_key;
- store:enrich_cache 表读写 + v2 数据库迁移兼容(数据零丢失);
- 端到端:同一插件开/关 enrich,route 按 score 三级分层 vs v0.1 大类缺省。

The ``openai`` package is never imported: every enricher runs on an injected
fake completion client; the dependency-missing path is exercised via a
``sys.modules`` stub (import returns None → ImportError).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
import pytest
from conftest import run

from myia.enrich import (
    IMAGE_CAPTION_SNIPPET_CHARS,
    IMAGE_OCR_SNIPPET_CHARS,
    EnrichConfigError,
    EnrichOutcome,
    EnrichSettings,
    LLMEnricher,
)
from myia.enrich.client import CompletionResult
from myia.enrich.prompt import load_prompt
from myia.enrich.scoring import (
    BudgetTracker,
    ScoreParseError,
    composite_score,
    mute_hit,
    parse_score_payload,
)
from myia.pipeline import Pipeline
from myia.schema import EnrichConfig, WatchlistConfig, load_category
from myia.store import SCHEMA_VERSION, SQLiteStore

BASE_URL = "https://llm.test.local/v1"
API_KEY = "test-key-not-real"

ENV_BASE = "MYIA_ENRICH_TEST_BASE"
ENV_KEY = "MYIA_ENRICH_TEST_KEY"


# ---------------------------------------------------------------------------
# Helpers (每测独立建库/独立 fake,零共享状态)
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """A throwaway SQLiteStore per test (cache 与 items 表互不污染)."""
    backend = SQLiteStore(tmp_path / "enrich.db")
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


def make_config(**overrides: Any) -> EnrichConfig:
    """EnrichConfig with overrides (schema-validated defaults otherwise)."""
    data: dict[str, Any] = {"enabled": True}
    data.update(overrides)
    return EnrichConfig(**data)


class FakeItem:
    """Duck-typed pipeline item (url/title/metadata/scores/add_tags/dedup_key)."""

    def __init__(self, url: str, title: str, content: str | None = None) -> None:
        self.url = url
        self.title = title
        self.scores: dict | None = None
        self.metadata: dict = {"content": content} if content else {}
        self.dedup_key = f"key-{url}"

    def add_tags(self, tags: list[str]) -> None:
        existing = self.metadata.get("tags")
        self.metadata["tags"] = list(dict.fromkeys([*(existing or []), *tags]))

    def view(self) -> dict[str, Any]:
        view = dict(self.metadata)
        view.update(
            {
                "url": self.url,
                "title": self.title,
                "scores": self.scores,
                "dedup_key": self.dedup_key,
            }
        )
        return view


@dataclass
class FakeCompletionClient:
    """Scripted completion client: pops one response per call, records all."""

    responses: list[str] = field(default_factory=list)
    tokens_per_call: int = 100
    fail_on_call: set[int] = field(default_factory=set)  # 1-based
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete(self, *, model: str, system: str, user: str) -> CompletionResult:
        self.calls.append({"model": model, "system": system, "user": user})
        text = self.responses.pop(0) if self.responses else "[]"
        if len(self.calls) in self.fail_on_call:
            raise RuntimeError("模拟端点故障")
        return CompletionResult(text=text, total_tokens=self.tokens_per_call)


def score_response(entries: list[dict[str, Any]], *, fence: bool = False) -> str:
    """Build one batch's model reply (optional markdown fence)."""
    text = json.dumps(entries, ensure_ascii=False)
    return f"```json\n{text}\n```" if fence else text


def dims_for(url: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((e for e in entries if e.get("url") == url), None)


def make_enricher(client: FakeCompletionClient, **config_overrides: Any) -> LLMEnricher:
    return LLMEnricher(make_config(**config_overrides), make_settings(), client=client)


# ---------------------------------------------------------------------------
# Endpoint settings: grill Q6 结构化报错(fail fast)
# ---------------------------------------------------------------------------


class TestEndpointSettings:
    def test_settings_missing_base_url_raises_missing_base_url(self):
        with pytest.raises(EnrichConfigError) as excinfo:
            EnrichSettings(base_url_ref=None, api_key_ref=f"env:{ENV_KEY}")
        assert excinfo.value.code == "missing_base_url"
        assert excinfo.value.to_dict()["error_type"] == "missing_base_url"

    def test_settings_plaintext_base_url_raises_credential_plaintext(self):
        # base_url 未用凭据引用(直接写 URL)→ 结构化报错(grill Q6)
        with pytest.raises(EnrichConfigError) as excinfo:
            EnrichSettings(
                base_url_ref="https://api.openai.com/v1",
                api_key_ref=f"env:{ENV_KEY}",
            )
        assert excinfo.value.code == "credential_plaintext"

    def test_settings_missing_api_key_raises_missing_api_key(self):
        with pytest.raises(EnrichConfigError) as excinfo:
            EnrichSettings(base_url_ref=f"env:{ENV_BASE}", api_key_ref=None)
        assert excinfo.value.code == "missing_api_key"

    def test_settings_plaintext_api_key_raises_credential_plaintext(self):
        with pytest.raises(EnrichConfigError) as excinfo:
            EnrichSettings(base_url_ref=f"env:{ENV_BASE}", api_key_ref="sk-123456")
        assert excinfo.value.code == "credential_plaintext"

    def test_enricher_missing_env_var_raises_credential_unresolved(self, endpoint_env, monkeypatch):
        monkeypatch.delenv(ENV_BASE, raising=False)
        with pytest.raises(EnrichConfigError) as excinfo:
            LLMEnricher(make_config(), make_settings(), client=FakeCompletionClient())
        assert excinfo.value.code == "credential_unresolved"
        assert ENV_BASE in str(excinfo.value)  # 引用名可见,值不可见

    def test_enricher_non_http_base_url_raises_invalid_base_url(self, endpoint_env, monkeypatch):
        monkeypatch.setenv(ENV_BASE, "not-a-url")
        with pytest.raises(EnrichConfigError) as excinfo:
            LLMEnricher(make_config(), make_settings(), client=FakeCompletionClient())
        assert excinfo.value.code == "invalid_base_url"

    def test_enricher_openai_missing_raises_dependency_missing(self, monkeypatch):
        # sys.modules 注入 None → import 必然 ImportError(不依赖环境是否装了 openai);
        # openai 在首次 complete() 时才被惰性加载(核心零重依赖)。
        monkeypatch.setitem(__import__("sys").modules, "openai", None)
        from myia.enrich.client import OpenAICompatClient

        client = OpenAICompatClient(BASE_URL, API_KEY)
        with pytest.raises(EnrichConfigError) as excinfo:
            run(client.complete(model="m", system="s", user="u"))
        assert excinfo.value.code == "dependency_missing"
        assert "myia[llm]" in str(excinfo.value)

    def test_enricher_constructs_without_openai(self, endpoint_env):
        # 懒加载契约(v1.1 low #16):缺依赖不是构造期失败——无注入 client
        # 也能构造成功;未安装 openai 的环境下构造后它也不进 sys.modules。
        openai_installed = importlib.util.find_spec("openai") is not None
        enricher = LLMEnricher(make_config(), make_settings())
        assert enricher.base_url == BASE_URL
        if not openai_installed:
            assert "openai" not in sys.modules

    def test_enricher_missing_openai_degrades_first_enrich_call(
        self, store, endpoint_env, monkeypatch
    ):
        # 时序契约:缺 openai 在**首次调用**才触发,且按单批失败隔离
        # (结构化 failures + 降级),不是构造期异常、不中断本轮。
        monkeypatch.setitem(sys.modules, "openai", None)
        enricher = LLMEnricher(make_config(), make_settings())  # 构造成功
        outcome = run(
            enricher.enrich([FakeItem("https://a", "标题")], watchlist={}, store=store)
        )
        assert outcome.degraded is True
        assert outcome.degrade_reason == "llm_batch_failed"
        assert outcome.failures[0]["error_type"] == "enrich_batch_failed"
        assert "openai 未安装" in outcome.failures[0]["message"]

    def test_enricher_resolves_refs_and_hides_key_from_repr(self, endpoint_env):
        enricher = LLMEnricher(make_config(), make_settings(), client=FakeCompletionClient())
        assert enricher.base_url == BASE_URL
        assert enricher.api_key == API_KEY
        assert API_KEY not in repr(enricher)  # 凭据值不进日志/异常面


# ---------------------------------------------------------------------------
# 打分解析 / 容错
# ---------------------------------------------------------------------------


class TestScoreParsing:
    DIMS: ClassVar[list[str]] = ["value", "relevance", "credibility"]
    URLS: ClassVar[list[str]] = ["https://a.example/x", "https://b.example/y"]

    def test_parse_payload_json_array_returns_scores(self):
        text = score_response(
            [
                {"url": self.URLS[0], "value": 8, "relevance": 9, "credibility": 7, "reason": "干货"},
                {"url": self.URLS[1], "value": 4, "relevance": 3, "credibility": 5},
            ]
        )
        scores, reasons, failures = parse_score_payload(text, self.URLS, self.DIMS)
        assert scores == {
            self.URLS[0]: {"value": 8, "relevance": 9, "credibility": 7},
            self.URLS[1]: {"value": 4, "relevance": 3, "credibility": 5},
        }
        assert reasons == {self.URLS[0]: "干货"}
        assert failures == []

    def test_parse_payload_fenced_and_wrapped_shapes_tolerated(self):
        fenced = score_response(
            [{"url": self.URLS[0], "value": 1, "relevance": 2, "credibility": 3}], fence=True
        )
        scores, _, failures = parse_score_payload(fenced, self.URLS, self.DIMS)
        assert scores and not failures
        wrapped = json.dumps({"items": [
            {"url": self.URLS[1], "value": 4, "relevance": 5, "credibility": 6}
        ]})
        scores, _, failures = parse_score_payload(wrapped, self.URLS, self.DIMS)
        assert self.URLS[1] in scores and not failures

    def test_parse_payload_out_of_range_clamped(self):
        text = score_response(
            [{"url": self.URLS[0], "value": 11, "relevance": -2, "credibility": 7.4}]
        )
        scores, _, failures = parse_score_payload(text, self.URLS, self.DIMS)
        assert scores[self.URLS[0]] == {"value": 10, "relevance": 0, "credibility": 7}
        assert failures == []

    def test_parse_payload_missing_dimension_fails_item_only(self):
        # 缺一个维度:该条不评分(结构化失败),同批其他条目照常解析
        text = score_response(
            [
                {"url": self.URLS[0], "value": 8, "credibility": 7},  # 缺 relevance
                {"url": self.URLS[1], "value": 4, "relevance": 3, "credibility": 5},
            ]
        )
        scores, _, failures = parse_score_payload(text, self.URLS, self.DIMS)
        assert self.URLS[0] not in scores
        assert scores[self.URLS[1]]["value"] == 4
        assert len(failures) == 1
        assert failures[0]["error_type"] == "enrich_invalid_dimension"
        assert failures[0]["url"] == self.URLS[0]

    def test_parse_payload_unknown_url_reported_not_fatal(self):
        text = score_response(
            [{"url": "https://unknown/z", "value": 1, "relevance": 1, "credibility": 1}]
        )
        scores, _, failures = parse_score_payload(text, self.URLS, self.DIMS)
        assert scores == {}
        assert failures[0]["error_type"] == "enrich_unknown_url"

    def test_parse_payload_unparseable_raises_score_parse_error(self):
        with pytest.raises(ScoreParseError):
            parse_score_payload("模型开始闲聊,没有 JSON", self.URLS, self.DIMS)
        with pytest.raises(ScoreParseError):
            parse_score_payload("[1, 2, ", self.URLS, self.DIMS)

    def test_composite_score_is_mean_of_dimensions(self):
        assert composite_score({"value": 8, "relevance": 9, "credibility": 7}, self.DIMS) == 8.0
        assert composite_score({"value": 10, "relevance": 0, "credibility": 5}, self.DIMS) == 5.0


class TestMuteAndBudget:
    def test_mute_hit_matches_case_insensitive_substring(self):
        assert mute_hit("限时 SPAM 带货指南", ["spam"]) == "spam"
        assert mute_hit("正常标题", ["spam"]) is None
        assert mute_hit("", ["spam"]) is None

    def test_mute_hit_skips_single_char_words(self):
        # 单字 mute 词会子串误伤('ai' 类短词),按约定忽略
        assert mute_hit("a normal title", ["a"]) is None

    def test_budget_tracker_blocks_after_limit(self):
        budget = BudgetTracker(limit=100)
        assert budget.can_spend()
        budget.spend(60)
        assert budget.can_spend()
        budget.spend(40)
        assert not budget.can_spend()

    def test_budget_tracker_records_overshoot(self):
        budget = BudgetTracker(limit=100)
        budget.spend(150)
        assert budget.overshoot == 50
        assert not budget.can_spend()


# ---------------------------------------------------------------------------
# LLMEnricher:缓存 / 预算 / mute(全部 mock LLM)
# ---------------------------------------------------------------------------


class TestLLMEnricher:
    URLS: ClassVar[list[str]] = ["https://a.example/x", "https://b.example/y"]

    @pytest.fixture(autouse=True)
    def _endpoint_env(self, endpoint_env):
        """Enricher 构建要解析 env: 引用;本组测试统一注入端点环境变量."""
        return endpoint_env

    def two_items(self) -> list[FakeItem]:
        return [FakeItem(self.URLS[0], "标题甲"), FakeItem(self.URLS[1], "标题乙")]

    def entries(self) -> list[dict[str, Any]]:
        return [
            {"url": self.URLS[0], "value": 9, "relevance": 9, "credibility": 9, "reason": "高价值"},
            {"url": self.URLS[1], "value": 3, "relevance": 3, "credibility": 3},
        ]

    def test_enrich_scores_items_backfills_dims_scalar_and_reason(self, store):
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)
        items = self.two_items()

        outcome = run(enricher.enrich(items, watchlist=WatchlistConfig(), store=store))

        assert isinstance(outcome, EnrichOutcome)
        assert (outcome.scored, outcome.cached, outcome.muted, outcome.unscored) == (2, 0, 0, 0)
        assert outcome.degraded is False
        first = items[0]
        assert first.scores == {"value": 9, "relevance": 9, "credibility": 9}
        assert first.metadata["score"] == 9.0  # 标量 = 三维均值(route 激活依赖位)
        assert first.metadata["score_reason"] == "高价值"
        # 批量:2 条默认 batch=20 → 单请求
        assert len(client.calls) == 1
        # prompt 携带评分维度与 watchlist 关键词(relevance 基准)
        assert "value" in client.calls[0]["user"]

    def test_enrich_cache_hit_does_not_rescore(self, store):
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)

        run(enricher.enrich(self.two_items(), watchlist=WatchlistConfig(), store=store))
        first_run_calls = len(client.calls)
        second_items = self.two_items()
        outcome = run(enricher.enrich(second_items, watchlist=WatchlistConfig(), store=store))

        # 同 URL 第二轮:缓存命中,LLM 调用次数不变,score 原样回填
        assert len(client.calls) == first_run_calls == 1
        assert outcome.cached == 2
        assert outcome.scored == 2
        assert second_items[0].metadata["score"] == 9.0

    def test_enrich_prompt_version_change_invalidates_cache(self, store):
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)
        run(enricher.enrich(self.two_items(), watchlist=WatchlistConfig(), store=store))

        rebased = LLMEnricher(
            make_config(),
            make_settings(prompt_version=99),  # 指纹变更 → 全部重评
            client=client,
        )
        outcome = run(rebased.enrich(self.two_items(), watchlist=WatchlistConfig(), store=store))

        assert outcome.cached == 0
        assert len(client.calls) == 2  # 重评发生

    def test_enrich_manual_cache_clear_rescores(self, store):
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)
        run(enricher.enrich(self.two_items(), watchlist=WatchlistConfig(), store=store))
        assert store.clear_enrich_cache() == 2

        outcome = run(enricher.enrich(self.two_items(), watchlist=WatchlistConfig(), store=store))

        assert outcome.cached == 0
        assert len(client.calls) == 2

    def test_enrich_budget_exhaust_degrades_to_keyword_only(self, store, caplog):
        # budget=100,每批 60 token:批1(used 60)→ 批2 后超限 → 剩余降级
        client = FakeCompletionClient(
            responses=[
                score_response([self.entries()[0]]),
                score_response([self.entries()[1]]),
            ],
            tokens_per_call=60,
        )
        enricher = make_enricher(client, batch=1, budget_per_run=100)
        items = [FakeItem(f"https://x.example/{i}", f"标题{i}") for i in range(4)]
        entries = [
            {"url": f"https://x.example/{i}", "value": 1, "relevance": 1, "credibility": 1}
            for i in range(4)
        ]
        client.responses = [score_response([entries[0]]), score_response([entries[1]])]

        with caplog.at_level(logging.WARNING, logger="myia.enrich"):
            outcome = run(enricher.enrich(items, watchlist=WatchlistConfig(), store=store))

        assert outcome.degraded is True
        assert outcome.degrade_reason == "budget_exhausted"
        assert outcome.scored == 2
        assert outcome.unscored == 2
        assert outcome.tokens_used == 120  # 单请求内越过上限,但不再发新批次
        assert len(client.calls) == 2
        assert "预算耗尽" in caplog.text  # 降级在日志可见(logging spec: WARNING)
        assert items[2].scores is None  # 未评分条目原样流转(v0.1 路由兜底)

    def test_enrich_mute_hit_demotes_without_llm(self, store):
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)
        muted = FakeItem(self.URLS[0], "SPAM 导购合集")
        normal = FakeItem(self.URLS[1], "正常技术文")

        outcome = run(
            enricher.enrich(
                [muted, normal],
                watchlist=WatchlistConfig(keywords=["技术"], mute=["spam"]),
                store=store,
            )
        )

        assert outcome.muted == 1
        assert muted.scores == {"value": 0, "relevance": 0, "credibility": 0}  # 三维降权
        assert muted.metadata["score"] == 0.0
        assert muted.metadata["muted"] == "spam"
        assert muted.metadata["tags"] == ["muted"]
        assert outcome.scored == 1  # normal 照常精评
        assert outcome.unscored == 0
        assert len(client.calls) == 1  # mute 判定零 token
        # muted 条目没进批:响应里它的条目按未知 URL 容错忽略
        assert any(f["error_type"] == "enrich_unknown_url" for f in outcome.failures)

    def test_enrich_single_batch_failure_isolated_others_continue(self, store):
        # 3 条、batch=1:第 2 批故障,第 1/3 批照常评分(批次隔离)
        entries = [
            {"url": f"https://y.example/{i}", "value": 5, "relevance": 5, "credibility": 5}
            for i in range(3)
        ]
        client = FakeCompletionClient(
            responses=[score_response([e]) for e in entries], fail_on_call={2}
        )
        enricher = make_enricher(client, batch=1)
        items = [FakeItem(f"https://y.example/{i}", f"标题{i}") for i in range(3)]

        outcome = run(enricher.enrich(items, watchlist=WatchlistConfig(), store=store))

        assert len(client.calls) == 3  # 故障批不中断后续批
        assert outcome.scored == 2
        assert outcome.unscored == 1
        assert outcome.degraded is True
        assert outcome.degrade_reason == "llm_batch_failed"
        assert outcome.failures[0]["url"] == "https://y.example/1"
        assert outcome.failures[0]["error_type"] == "enrich_batch_failed"
        assert items[0].metadata["score"] == 5.0
        assert items[1].scores is None

    def test_enrich_unparseable_response_fails_batch_not_run(self, store):
        client = FakeCompletionClient(responses=["抱歉,我无法评分"])
        enricher = make_enricher(client)
        items = self.two_items()

        outcome = run(enricher.enrich(items, watchlist=WatchlistConfig(), store=store))

        assert outcome.unscored == 2
        assert outcome.degrade_reason == "llm_batch_failed"
        assert {f["error_type"] for f in outcome.failures} == {"enrich_parse_error"}
        assert items[0].scores is None

    def test_enrich_backfills_store_cache_and_items_table(self, store):
        from myia.store import ItemRecord

        # 为 items 表准备两行(dedup 阶段的持久化形态)
        for url in self.URLS:
            store.save_item(ItemRecord(url=url, dedup_key=f"key-{url}", title="t"))
        client = FakeCompletionClient(responses=[score_response(self.entries())])
        enricher = make_enricher(client)
        items = self.two_items()

        run(enricher.enrich(items, watchlist=WatchlistConfig(), store=store))

        cached = store.get_enrich_cache(self.URLS[0], "glm-4-flash", enricher.scores_key)
        assert cached is not None
        assert cached["scores"] == {"value": 9, "relevance": 9, "credibility": 9}
        assert cached["score"] == 9.0
        row = next(r for r in store.list_items() if r.dedup_key == f"key-{self.URLS[0]}")
        assert row.scores == {"value": 9, "relevance": 9, "credibility": 9, "score": 9.0}

    def test_enrich_empty_items_short_circuits(self, store):
        outcome = run(make_enricher(FakeCompletionClient()).enrich([], watchlist=None, store=store))
        assert outcome.requested == 0
        assert outcome.scored == 0


# ---------------------------------------------------------------------------
# Prompt 模板外置(数据文件)
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_load_prompt_packaged_default_ok(self):
        template = load_prompt()
        assert template.version >= 1
        rendered = template.render_user(
            scores=["value", "relevance", "credibility"],
            watchlist=["免费", "白嫖"],
            items_json="[]",
        )
        assert "value, relevance, credibility" in rendered
        assert "免费, 白嫖" in rendered

    def test_load_prompt_unknown_placeholder_rejected(self, tmp_path):
        bad = tmp_path / "prompt.json"
        bad.write_text(
            json.dumps(
                {
                    "version": 1,
                    "system": "s",
                    "user_template": "评分 {{SCORES}} 未知 {{OOPS}}",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with pytest.raises(EnrichConfigError) as excinfo:
            load_prompt(bad)
        assert excinfo.value.code == "prompt_invalid"

    def test_load_prompt_malformed_json_rejected(self, tmp_path):
        bad = tmp_path / "prompt.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(EnrichConfigError) as excinfo:
            load_prompt(bad)
        assert excinfo.value.code == "prompt_invalid"


# ---------------------------------------------------------------------------
# store:enrich_cache 表 + 迁移兼容 + items 表评分回填
# ---------------------------------------------------------------------------


class TestStoreEnrichCache:
    def test_cache_roundtrip_and_key_invalidation(self, store):
        store.set_enrich_cache("https://a", "glm-4-flash", "value+relevance+credibility@p1",
                               {"scores": {"value": 8}, "score": 8.0})
        hit = store.get_enrich_cache("https://a", "glm-4-flash", "value+relevance+credibility@p1")
        assert hit == {"scores": {"value": 8}, "score": 8.0}
        # 换模型 / 换维度指纹 → 未命中(schema/变更才重评)
        assert store.get_enrich_cache("https://a", "glm-4.5", "value+relevance+credibility@p1") is None
        assert store.get_enrich_cache("https://a", "glm-4-flash", "value@p1") is None

    def test_cache_upsert_overwrites_same_key(self, store):
        key = "value@p1"
        store.set_enrich_cache("https://a", "m", key, {"score": 1.0})
        store.set_enrich_cache("https://a", "m", key, {"score": 2.0})
        assert store.get_enrich_cache("https://a", "m", key) == {"score": 2.0}

    def test_cache_clear_by_url(self, store):
        store.set_enrich_cache("https://a", "m", "k", {"score": 1.0})
        store.set_enrich_cache("https://b", "m", "k", {"score": 1.0})
        assert store.clear_enrich_cache(url="https://a") == 1
        assert store.get_enrich_cache("https://a", "m", "k") is None
        assert store.get_enrich_cache("https://b", "m", "k") is not None

    def test_update_item_scores_backfills_items_table(self, store):
        from myia.store import ItemRecord

        item_id = store.save_item(ItemRecord(url="https://a", dedup_key="k1", title="t"))
        assert store.update_item_scores("k1", {"value": 8, "score": 8.0}) is True
        assert store.get_item(item_id).scores == {"value": 8, "score": 8.0}

    def test_update_item_scores_missing_row_returns_false(self, store):
        assert store.update_item_scores("nope", {"score": 1.0}) is False

    def test_v2_database_migrates_forward_without_data_loss(self, tmp_path):
        # 手工造一个 v2 形态的库(有 runs/items/store_meta,无 enrich_cache)
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
                dedup_key TEXT NOT NULL, source TEXT, title TEXT NOT NULL,
                content TEXT, content_hash TEXT, tags TEXT, category TEXT,
                scores TEXT, pushed_at TEXT, push_slot TEXT, first_seen TEXT NOT NULL,
                raw TEXT
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                stats TEXT, error TEXT, steps TEXT
            );
            CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO store_meta (key, value) VALUES ('schema_version', '2');
            INSERT INTO items (url, dedup_key, title, first_seen)
                VALUES ('https://legacy', 'legacy-key', '旧条目', '2026-01-01T00:00:00+00:00');
            """
        )
        conn.commit()
        conn.close()

        reopened = SQLiteStore(db_path)

        assert int(reopened.get_meta("schema_version")) == SCHEMA_VERSION
        assert reopened.get_enrich_cache("https://legacy", "m", "k") is None  # 表可用
        assert reopened.update_item_scores("legacy-key", {"score": 7.0}) is True  # 旧行可回填
        reopened.set_enrich_cache("https://legacy", "m", "k", {"score": 7.0})
        assert reopened.get_enrich_cache("https://legacy", "m", "k") == {"score": 7.0}
        rows = reopened.list_items()
        assert len(rows) == 1 and rows[0].url == "https://legacy"  # 数据零丢失
        reopened.close()

    def test_fresh_database_stamped_current_version(self, store):
        assert int(store.get_meta("schema_version")) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 端到端:同一插件开/关 enrich,route 分层行为(acceptance #4)
# ---------------------------------------------------------------------------


def make_category(**overrides: Any):
    """Minimal plugin: one JSON source, classify off, score-threshold route."""
    data: dict[str, Any] = {
        "id": "enrich-demo",
        "name": "精评演示",
        "schedule": "0 9 * * *",
        "sources": [
            {
                "name": "api",
                "engine": "direct_api",
                "url": "https://api.demo.local/list",
                "extract": {
                    "type": "json_path",
                    "fields": {"title": "$[*].title", "url": "$[*].url"},
                },
            }
        ],
        "watchlist": {"keywords": ["数据库"], "mute": ["spam"]},
        "classify": {"builtin": False, "rules": []},
        "enrich": {"enabled": True},
        "push": [
            {
                "channel": "stdout",
                "route": [
                    {"when": "score >= 8", "mode": "immediate"},
                    {"when": "score >= 5", "mode": "digest"},
                    {"when": "score < 5", "mode": "archive"},
                ],
            }
        ],
    }
    data.update(overrides)
    return load_category(data)


def api_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404, text="")  # fail-open,允许抓取
    return httpx.Response(
        200,
        json=[
            {"title": "重磅:开源数据库发布新版本", "url": "https://items.example/hot"},
            {"title": "普通流水账一篇", "url": "https://items.example/dull"},
        ],
    )


class FakeRouteEnricher:
    """Pipeline-injected enricher: hot → 9.0,dull → 3.0(确定性分层验证).

    Mirrors the LLMEnricher contract: item backfill + items 表评分回填.
    """

    def __init__(self) -> None:
        self.called = False

    async def enrich(self, items, *, watchlist, store) -> EnrichOutcome:
        self.called = True
        outcome = EnrichOutcome(model="fake", requested=len(items))
        for item in items:
            scalar = 9.0 if "hot" in item.url else 3.0
            dims = {"value": int(scalar), "relevance": int(scalar), "credibility": int(scalar)}
            item.scores = dims
            item.metadata["score"] = scalar
            store.update_item_scores(item.dedup_key, {**dims, "score": scalar})
            outcome.scored += 1
        return outcome


def decisions_by_url(result) -> dict[str, dict[str, Any]]:
    decisions = {}
    for push in result.pushes:
        for decision in push.decisions:
            decisions[decision["url"]] = decision
    return decisions


class TestPipelineEnrichRouting:
    def run_pipeline(self, tmp_path, config, *, enricher=None):
        store = SQLiteStore(tmp_path / "pipe.db")
        client = httpx.AsyncClient(transport=httpx.MockTransport(api_handler))
        try:
            pipeline = Pipeline(config, store=store, client=client, enricher=enricher)
            return run(pipeline.run(dry_run=False))
        finally:
            store.close()
            run(client.aclose())

    def test_enrich_on_routes_by_score_three_tiers(self, tmp_path):
        config = make_category()
        enricher = FakeRouteEnricher()

        result = self.run_pipeline(tmp_path, config, enricher=enricher)

        assert enricher.called is True
        decisions = decisions_by_url(result)
        # score 9 → immediate;score 3 → archive(score 规则优先于大类缺省)
        assert decisions["https://items.example/hot"]["mode"] == "immediate"
        assert decisions["https://items.example/hot"]["reason"] == "rule"
        assert decisions["https://items.example/dull"]["mode"] == "archive"
        analyze = result.stage("analyze")
        assert analyze.status == "ok"
        assert analyze.skips.get("enrich_cache_hit") == 0
        # score 回填 items 表(PRD:score 回填 items 表)
        store = SQLiteStore(tmp_path / "pipe.db")
        try:
            rows = {r.url: r for r in store.list_items()}
            assert rows["https://items.example/hot"].scores["score"] == 9.0
        finally:
            store.close()

    def test_enrich_off_falls_back_to_v01_conservative_default(self, tmp_path):
        config = make_category(enrich={"enabled": False})

        result = self.run_pipeline(tmp_path, config)

        analyze = result.stage("analyze")
        assert analyze.status == "ok"
        assert analyze.items_out == 2  # 直通,条目不丢
        decisions = decisions_by_url(result)
        # 无 score:score 规则休眠,classify 关闭 → category None → 保守 digest
        assert decisions["https://items.example/hot"]["mode"] == "digest"
        assert decisions["https://items.example/hot"]["reason"] == "conservative_default"
        assert decisions["https://items.example/dull"]["mode"] == "digest"

    def test_enrich_enabled_without_enricher_or_settings_fails_fast(self):
        # enrich.enabled 但无注入、schema 又缺 base_url 字段 → 构建期结构化报错
        with pytest.raises(EnrichConfigError) as excinfo:
            Pipeline(make_category())
        assert excinfo.value.code == "missing_base_url"

    def test_enrich_degraded_items_route_via_v01_defaults(self, tmp_path):
        # 注入的 enricher 全部不评分(模拟预算降级后的条目形态)→ 大类缺省路由
        class NoScoreEnricher:
            async def enrich(self, items, *, watchlist, store) -> EnrichOutcome:
                return EnrichOutcome(model="fake", requested=len(items), unscored=len(items), degraded=True)

        result = self.run_pipeline(tmp_path, make_category(), enricher=NoScoreEnricher())

        decisions = decisions_by_url(result)
        assert decisions["https://items.example/hot"]["reason"] == "conservative_default"
        assert result.stage("analyze").skips.get("enrich_degraded_unknown") == 2


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:schema 装配 / 缓存命中回填 / 降级不翻 partial / 阶段超时豁免
# ---------------------------------------------------------------------------


class TestEnrichWiring:
    URLS: ClassVar[list[str]] = ["https://a.example/x", "https://b.example/y"]

    @pytest.fixture(autouse=True)
    def _endpoint_env(self, endpoint_env):
        """enricher 构建要解析 env: 引用;本组测试统一注入端点环境变量."""
        return endpoint_env

    def entries(self) -> list[dict[str, Any]]:
        return [
            {"url": self.URLS[0], "value": 9, "relevance": 9, "credibility": 9, "reason": "高价值"},
            {"url": self.URLS[1], "value": 3, "relevance": 3, "credibility": 3},
        ]

    def run_pipeline(self, tmp_path, config, *, enricher=None):
        store = SQLiteStore(tmp_path / "pipe.db")
        client = httpx.AsyncClient(transport=httpx.MockTransport(api_handler))
        try:
            pipeline = Pipeline(config, store=store, client=client, enricher=enricher)
            return run(pipeline.run(dry_run=False))
        finally:
            store.close()
            run(client.aclose())

    def test_enrich_endpoint_from_schema_config(self, tmp_path, endpoint_env):
        """回归:schema enrich.base_url/api_key(env: 引用)在 Pipeline 构造期
        装配默认 enricher——``myia run`` 唯一用户入口可达(PRD:桌面用户挂自己的
        key),不再只有 Python 构造注入一条路。"""
        config = make_category(
            enrich={
                "enabled": True,
                "base_url": f"env:{ENV_BASE}",
                "api_key": f"env:{ENV_KEY}",
            }
        )
        store = SQLiteStore(tmp_path / "cfg.db")
        client = httpx.AsyncClient(transport=httpx.MockTransport(api_handler))
        try:
            pipeline = Pipeline(config, store=store, client=client)
            assert pipeline._enricher is not None
            assert pipeline._enricher.settings.base_url_ref == f"env:{ENV_BASE}"
            assert pipeline._enricher.settings.api_key_ref == f"env:{ENV_KEY}"
        finally:
            store.close()
            run(client.aclose())

    def test_enrich_config_partial_refs_still_fail_fast(self):
        """schema 只配 base_url 没配 api_key → 构造期 missing_api_key(不静默)。"""
        config = make_category(enrich={"enabled": True, "base_url": f"env:{ENV_BASE}"})
        with pytest.raises(EnrichConfigError) as excinfo:
            Pipeline(config)
        assert excinfo.value.code == "missing_api_key"

    def test_enrich_cache_hit_backfills_new_items_row(self, store):
        """回归:缓存命中也必须回填 items 表——dated dedup key 轮换/断点续跑
        会产生新插入的行,只回内存会让该行 scores 恒 NULL(PRD「score 回填
        items 表」在官方推荐 dated key 配置下确定性落空)。"""
        from myia.store import ItemRecord

        url = self.URLS[0]
        store.save_item(ItemRecord(url=url, dedup_key="key-first", title="t"))
        client = FakeCompletionClient(responses=[score_response([self.entries()[0]])])
        enricher = make_enricher(client)
        first = FakeItem(url, "标题甲")
        run(enricher.enrich([first], watchlist=WatchlistConfig(), store=store))
        assert first.scores is not None

        # 第二轮:同一 URL、新 dedup key(轮换)→ 缓存命中 + 新行回填
        store.save_item(ItemRecord(url=url, dedup_key="key-second", title="t"))
        second = FakeItem(url, "标题甲")
        second.dedup_key = "key-second"
        again = FakeCompletionClient(responses=[])  # 命中缓存就不该再调 LLM
        outcome = run(
            make_enricher(again).enrich([second], watchlist=WatchlistConfig(), store=store)
        )

        assert outcome.cached == 1
        assert second.scores is not None
        row = next(r for r in store.list_items() if r.dedup_key == "key-second")
        assert row.scores == {"value": 9, "relevance": 9, "credibility": 9, "score": 9.0}

    def test_enrich_item_failures_are_warnings_not_partial(self, tmp_path):
        """回归:条目级 enrich 失败是降级注记——条目仍按 v0.1 路由成功投递,
        run 不翻 partial(退出码 3 必须留给真实部分失败);详情进 stage warnings。"""

        class FailingEnricher:
            async def enrich(self, items, *, watchlist, store) -> EnrichOutcome:
                outcome = EnrichOutcome(model="fake", requested=len(items), unscored=len(items))
                for item in items:
                    outcome.failures.append(
                        {
                            "url": item.url,
                            "title": item.title,
                            "error_type": "enrich_batch_failed",
                            "message": "批量精评失败: TimeoutError",
                        }
                    )
                return outcome

        result = self.run_pipeline(tmp_path, make_category(), enricher=FailingEnricher())

        assert result.status == "success"  # 不翻 partial(CLI 退出码 0)
        analyze = result.stage("analyze")
        assert analyze.status == "ok"
        assert analyze.failures == []
        assert analyze.skips.get("enrich_unscored_failures") == 2
        assert {w["error_type"] for w in analyze.warnings} == {"enrich_batch_failed"}
        assert all(report.ok for push in result.pushes for report in push.reports)

    def test_analyze_stage_is_exempt_from_stage_timeout(self, tmp_path, monkeypatch):
        """回归:analyze 与 fetch 一样豁免阶段级超时——批级 wait_for 已隔离端点
        慢/挂(批失败在 enricher 内降级),外层阶段帽量纲错配(≥6 批×60s)会
        整体杀掉 push,违背「push 仍以 v0.1 路由运行」的模块承诺。"""
        captured: dict[str, bool] = {}
        real = Pipeline._attempt_stage

        async def spy(self, name, runner, report, *, items_in, apply_timeout=True):
            captured[name] = apply_timeout
            return await real(
                self, name, runner, report, items_in=items_in, apply_timeout=apply_timeout
            )

        monkeypatch.setattr(Pipeline, "_attempt_stage", spy)
        self.run_pipeline(tmp_path, make_category(), enricher=FakeRouteEnricher())
        assert captured["fetch"] is False
        assert captured["analyze"] is False
        assert captured["dedup"] is True


# ---------------------------------------------------------------------------
# 图析产物进 payload(10-03-vision-pipeline 拍板⑤:image_ocr/image_caption
# 两键,无图条目不带;prompt version bump 让旧缓存整体失效一次)
# ---------------------------------------------------------------------------


class TestRenderBatchImageFields:
    @pytest.fixture(autouse=True)
    def _endpoint_env(self, endpoint_env):
        return endpoint_env

    @staticmethod
    def _items_json(user: str) -> list[dict[str, Any]]:
        """从渲染后的 user 消息里抠出条目 JSON(user_template 钉了单行嵌入)。"""
        line = next(part for part in user.splitlines() if part.startswith("["))
        return json.loads(line)

    def test_image_fields_carried_and_truncated(self):
        enricher = make_enricher(FakeCompletionClient())
        item = FakeItem("https://a.example/x", "带图条目")
        item.metadata["image_ocr"] = "字" * 1000
        item.metadata["image_caption"] = "描" * 500

        user = enricher._render_batch([item], ["关键词"])

        (entry,) = self._items_json(user)
        assert entry["image_ocr"] == "字" * IMAGE_OCR_SNIPPET_CHARS, "OCR 截 800 字"
        assert entry["image_caption"] == "描" * IMAGE_CAPTION_SNIPPET_CHARS, "caption 截 300 字"
        assert entry["url"] == "https://a.example/x"
        assert entry["title"] == "带图条目"

    def test_items_without_images_omit_both_keys(self):
        enricher = make_enricher(FakeCompletionClient())
        bare = FakeItem("https://a.example/plain", "无图条目")
        empty_ocr = FakeItem("https://a.example/empty", "空串条目")
        empty_ocr.metadata["image_ocr"] = "   "
        empty_ocr.metadata["image_caption"] = ""

        user = enricher._render_batch([bare, empty_ocr], ["关键词"])

        entries = {entry["url"]: entry for entry in self._items_json(user)}
        assert set(entries) == {"https://a.example/plain", "https://a.example/empty"}
        for entry in entries.values():
            assert "image_ocr" not in entry, "无图/空串条目不带键,payload 不膨胀"
            assert "image_caption" not in entry

    def test_content_and_image_fields_coexist(self):
        enricher = make_enricher(FakeCompletionClient())
        item = FakeItem("https://a.example/both", "图文条目", content="正文摘要")
        item.metadata["image_ocr"] = "图中文字"

        user = enricher._render_batch([item], [])

        (entry,) = self._items_json(user)
        assert entry["content"] == "正文摘要"
        assert entry["image_ocr"] == "图中文字"

    def test_prompt_version_bumped_and_documents_image_fields(self):
        """拍板⑤:version +1(旧缓存一次性失效)+ user 模板补图析说明一行。"""
        import json as _json
        from pathlib import Path

        data = _json.loads(
            (Path(__file__).resolve().parents[1] / "src" / "myia" / "enrich" / "data" / "prompt.json")
            .read_text(encoding="utf-8")
        )
        assert data["version"] == 2, "图析键进 payload 必须伴随 version bump(缓存指纹)"
        assert "image_ocr" in data["user_template"]
        assert "image_caption" in data["user_template"]

    def test_version_bump_invalidates_old_cache(self, store):
        """同条目:v1 指纹写入的缓存,v2 指纹不复用(第二跑起建新缓存)。"""
        client = FakeCompletionClient(responses=[
            score_response([{"url": "https://a.example/x", "value": 9, "relevance": 9, "credibility": 9}]),
            score_response([{"url": "https://a.example/x", "value": 8, "relevance": 8, "credibility": 8}]),
        ])
        v1 = LLMEnricher(make_config(), make_settings(prompt_version=1), client=client)
        run(v1.enrich([FakeItem("https://a.example/x", "标题")], watchlist=WatchlistConfig(), store=store))
        assert len(client.calls) == 1

        default = make_enricher(client)  # 数据文件已是 version 2
        assert default.prompt.version == 2
        outcome = run(default.enrich(
            [FakeItem("https://a.example/x", "标题")], watchlist=WatchlistConfig(), store=store
        ))
        assert outcome.cached == 0, "旧版本指纹的缓存行不得复用"
        assert len(client.calls) == 2
