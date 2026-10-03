"""Tests for the v0.3 feedback loop (PRD 10-01-v03-feedback-loop).

验收标准逐条:

- TG callback(mock getUpdates)→ 入库 → stats 单测;
- CLI mark → 入库单测;
- 演示:一轮负反馈后同类条目下轮被降权(demo 级 —— 词表降权链路全真:
  pipeline run 起点调参 → analyze 降权 → route 归档);
- 飞书回调端点默认关闭,开启时鉴权校验单测(默认关 + token + 仅内网)。

外加:feedback/feedback_tuning 表与 v3→v4 迁移兼容、批量入库部分失败语义、
统计词切分、调参幂等/释放/比值护栏。全部 I/O 走 httpx.MockTransport 与
tmp_path 存储,零真实网络(TG/飞书真实回调需主人手动验证,见任务 openIssues)。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from conftest import run

import shishi.pipeline as pipeline_module
from shishi.cli import main as cli_main
from shishi.feedback import (
    FeedbackTuner,
    TuningPolicy,
    candidate_words,
    compute_feedback_stats,
    ingest_callbacks,
    load_active_tuning,
    normalize_verdict,
    record_feedback,
    resolve_item_ref,
)
from shishi.pipeline import Pipeline
from shishi.push import (
    PollResult,
    TelegramCallback,
    TelegramFeedbackError,
    TelegramFeedbackPoller,
    parse_callback_data,
)
from shishi.push.feishu_callback import (
    FeishuCallbackConfig,
    FeishuCallbackConfigError,
    FeishuCallbackHandler,
    build_server,
)
from shishi.schema import load_category
from shishi.store import (
    FEEDBACK_CHANNEL_CLI,
    FEEDBACK_CHANNEL_TELEGRAM,
    SCHEMA_VERSION,
    FeedbackRecord,
    ItemRecord,
    SQLiteStore,
    TuningRecord,
)

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
TOKEN = "test-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """A throwaway SQLiteStore per test (zero shared state between tests)."""
    db = SQLiteStore(tmp_path / "feedback.db")
    yield db
    db.close()


def add_item(
    store: SQLiteStore,
    *,
    url: str = "https://items.example/a",
    title: str = "显卡降价了",
    category: str | None = "羊毛",
) -> ItemRecord:
    """Seed one item and return the stored row (with its id)."""
    store.save_item(ItemRecord(url=url, dedup_key=url, title=title, category=category))
    item = store.get_item_by_dedup_key(url)
    assert item is not None
    return item


def add_feedback(
    store: SQLiteStore,
    item: ItemRecord,
    verdict: str,
    *,
    channel: str = FEEDBACK_CHANNEL_CLI,
) -> FeedbackRecord:
    return record_feedback(store, verdict=verdict, channel=channel, item=item, now=NOW)


def make_enricher(score: float = 9.0):
    """Pipeline-injected fake enricher: everything scores ``score``."""

    class ScoreAllEnricher:
        async def enrich(self, items, *, watchlist, store):  # noqa: ANN001, ARG002
            from shishi.enrich import EnrichOutcome

            for item in items:
                dims = {"value": int(score), "relevance": int(score), "credibility": int(score)}
                item.scores = dims
                item.metadata["score"] = score
                store.update_item_scores(item.dedup_key, {**dims, "score": score})
            return EnrichOutcome(model="fake", requested=len(items), scored=len(items))

    return ScoreAllEnricher()


def make_category(**overrides: Any):
    """Minimal plugin: JSON source, score-threshold route (test_enrich 同款)."""
    data: dict[str, Any] = {
        "id": "feedback-demo",
        "name": "反馈演示",
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
        "watchlist": {"keywords": [], "mute": ["spam"]},
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


# ---------------------------------------------------------------------------
# store: feedback / feedback_tuning 表与迁移
# ---------------------------------------------------------------------------


class TestStoreFeedback:
    def test_save_and_list_feedback_roundtrip(self, store):
        item = add_item(store)

        feedback_id = store.save_feedback(
            FeedbackRecord(
                dedup_key=item.dedup_key,
                verdict="bad",
                channel="telegram",
                item_id=item.id,
                title=item.title,
                category=item.category,
                created_at=NOW,
            )
        )

        rows = store.list_feedback()
        assert feedback_id >= 1
        assert len(rows) == 1
        row = rows[0]
        assert row.id == feedback_id
        assert row.verdict == "bad"
        assert row.channel == "telegram"
        assert row.item_id == item.id
        assert row.title == "显卡降价了"  # 快照:items 被清理后统计仍可用
        assert row.category == "羊毛"
        assert row.created_at == NOW

    def test_save_feedback_rejects_verdict_outside_vocabulary(self, store):
        with pytest.raises(ValueError, match="verdict"):
            store.save_feedback(
                FeedbackRecord(dedup_key="k", verdict="meh", channel="cli")
            )

    def test_save_feedback_requires_dedup_key_and_channel(self, store):
        with pytest.raises(ValueError, match="dedup_key"):
            store.save_feedback(FeedbackRecord(dedup_key="", verdict="good", channel="cli"))
        with pytest.raises(ValueError, match="channel"):
            store.save_feedback(FeedbackRecord(dedup_key="k", verdict="good", channel=""))

    def test_list_feedback_filters_by_verdict_channel_limit(self, store):
        item = add_item(store)
        add_feedback(store, item, "good")
        add_feedback(store, item, "bad", channel=FEEDBACK_CHANNEL_TELEGRAM)
        add_feedback(store, item, "bad")

        assert len(store.list_feedback()) == 3
        assert [row.verdict for row in store.list_feedback(verdict="bad")] == ["bad", "bad"]
        assert [row.id for row in store.list_feedback(verdict="bad")] == [3, 2]  # 新→旧
        assert len(store.list_feedback(channel=FEEDBACK_CHANNEL_TELEGRAM)) == 1
        assert len(store.list_feedback(limit=2)) == 2
        with pytest.raises(ValueError, match="verdict"):
            store.list_feedback(verdict="meh")

    def test_get_item_by_dedup_key_returns_newest(self, store):
        store.save_item(
            ItemRecord(url="https://x/1", dedup_key="shared-key", title="旧")
        )
        store.save_item(
            ItemRecord(url="https://x/2", dedup_key="shared-key", title="新")
        )
        assert store.get_item_by_dedup_key("shared-key").title == "新"
        assert store.get_item_by_dedup_key("missing") is None
        with pytest.raises(ValueError, match="dedup_key"):
            store.get_item_by_dedup_key("")

    def test_tuning_roundtrip_newest_first(self, store):
        first = TuningRecord(kind="mute_weight", payload={"word": "显卡", "weight": 0.0}, created_at=NOW)
        second = TuningRecord(kind="category_penalty", payload={"category": "羊毛", "weight": 0.3})
        store.save_tuning(first)
        store.save_tuning(second)

        rows = store.list_tuning()
        assert [row.kind for row in rows] == ["category_penalty", "mute_weight"]
        assert rows[1].payload == {"word": "显卡", "weight": 0.0}
        assert rows[1].created_at == NOW
        assert [row.kind for row in store.list_tuning(kind="mute_weight")] == ["mute_weight"]
        with pytest.raises(ValueError, match="kind"):
            store.save_tuning(TuningRecord(kind="", payload={}))
        with pytest.raises(ValueError, match="payload"):
            store.save_tuning(TuningRecord(kind="mute_weight", payload="not-a-map"))

    def test_v3_database_migrates_to_v4_preserving_rows(self, tmp_path):
        """迁移兼容:v3 库(无 feedback 表)打开即补表,既有数据零丢失。"""
        path = tmp_path / "legacy.db"
        seed = SQLiteStore(path)
        seed.save_item(ItemRecord(url="https://old/1", dedup_key="https://old/1", title="旧条目"))
        seed.close()
        # 降回 v3 形态:删反馈表 + 回拨版本戳(模拟升级前的库文件)。
        raw = sqlite3.connect(path)
        raw.executescript("DROP TABLE feedback; DROP TABLE feedback_tuning;")
        raw.execute("UPDATE store_meta SET value='3' WHERE key='schema_version'")
        raw.commit()
        raw.close()

        migrated = SQLiteStore(path)
        try:
            assert int(migrated.get_meta("schema_version")) == SCHEMA_VERSION
            tables = {
                row[0]
                for row in migrated.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"feedback", "feedback_tuning"} <= tables
            assert [item.title for item in migrated.list_items()] == ["旧条目"]
            # 迁移后的表立即可写。
            item = migrated.get_item_by_dedup_key("https://old/1")
            assert item is not None
            record_feedback(migrated, verdict="bad", channel="cli", item=item, now=NOW)
            assert migrated.list_feedback(verdict="bad") != []
        finally:
            migrated.close()


# ---------------------------------------------------------------------------
# feedback.record:归一化 / 关联 / 批量入库
# ---------------------------------------------------------------------------


class TestRecordIngest:
    def test_normalize_verdict_is_case_insensitive_and_strict(self):
        assert normalize_verdict("Good") == "good"
        assert normalize_verdict(" BAD ") == "bad"
        with pytest.raises(ValueError, match="good"):
            normalize_verdict("meh")

    def test_record_feedback_snapshots_item_fields(self, store):
        item = add_item(store)

        record = record_feedback(
            store, verdict="bad", channel="cli", item=item, now=NOW
        )

        assert record.id is not None
        assert record.item_id == item.id
        assert record.dedup_key == item.dedup_key
        assert record.title == item.title
        assert record.category == item.category
        assert record.created_at == NOW

    def test_record_feedback_requires_an_item_identity(self, store):
        with pytest.raises(ValueError, match="条目身份"):
            record_feedback(store, verdict="good", channel="cli", now=NOW)

    def test_record_feedback_rejects_unknown_verdict(self, store):
        item = add_item(store)
        with pytest.raises(ValueError, match="判定"):
            record_feedback(store, verdict="不错", channel="cli", item=item)

    def test_resolve_item_ref_accepts_id_and_dedup_key(self, store):
        item = add_item(store, url="https://x/1")
        assert resolve_item_ref(store, item.id).url == "https://x/1"
        assert resolve_item_ref(store, str(item.id)).url == "https://x/1"
        assert resolve_item_ref(store, "https://x/1").url == "https://x/1"
        assert resolve_item_ref(store, "https://none/1") is None
        assert resolve_item_ref(store, "") is None

    def test_ingest_callbacks_mixed_batch_isolates_failures(self, store):
        item = add_item(store)
        callbacks = [
            {"channel": "telegram", "verdict": "good", "dedup_key": item.dedup_key},
            {"channel": "telegram", "verdict": "超棒", "dedup_key": item.dedup_key},  # 坏判定
            {"channel": "telegram", "verdict": "bad", "dedup_key": ""},  # 坏身份
        ]

        report = ingest_callbacks(store, callbacks, now=NOW)

        assert report.saved == 1
        assert report.skipped == 2
        assert len(report.failures) == 2
        assert all(f["error_type"] == "invalid_callback" for f in report.failures)
        assert len(store.list_feedback()) == 1  # 单条失败不拖垮整批

    def test_ingest_callbacks_tolerates_pruned_items_by_default(self, store):
        report = ingest_callbacks(
            store,
            [{"channel": "telegram", "verdict": "bad", "dedup_key": "https://gone/1"}],
            now=NOW,
        )

        assert report.saved == 1
        row = store.list_feedback()[0]
        assert row.item_id is None  # 条目已被 retention 清理,快照位留空
        assert row.dedup_key == "https://gone/1"

    def test_ingest_callbacks_strict_mode_skips_missing_items(self, store):
        report = ingest_callbacks(
            store,
            [{"channel": "cli", "verdict": "bad", "dedup_key": "https://gone/1"}],
            now=NOW,
            allow_missing=False,
        )

        assert report.saved == 0
        assert report.skipped == 1


# ---------------------------------------------------------------------------
# feedback.stats:词切分与 Top 统计
# ---------------------------------------------------------------------------


class TestFeedbackStats:
    def test_candidate_words_latin_tokens_and_cjk_bigrams(self):
        words = candidate_words("RTX 4090 显卡降价了")
        assert "rtx" in words
        assert "4090" in words
        assert {"显卡", "卡降", "降价", "价了"} <= set(words)  # CJK → 重叠二元组
        assert candidate_words("") == []
        assert candidate_words("ab") == ["ab"]  # 最短 latin 词
        assert "a" not in candidate_words("a 显")  # 单字符(CJK/latin)都不成词

    def test_compute_feedback_stats_tops_and_channels(self, store):
        gpu = add_item(store, url="https://x/gpu", title="显卡降价了", category="羊毛")
        vpn = add_item(store, url="https://x/vpn", title="vpn 教程分享", category="节点")
        add_feedback(store, gpu, "bad", channel=FEEDBACK_CHANNEL_TELEGRAM)
        add_feedback(store, gpu, "bad")
        add_feedback(store, gpu, "good")
        add_feedback(store, vpn, "bad")

        stats = compute_feedback_stats(store.list_feedback(), top_n=2)

        assert (stats.total, stats.good, stats.bad) == (4, 1, 3)
        assert stats.by_channel == {"telegram": 1, "cli": 3}
        assert stats.top_bad_categories[0] == ("羊毛", 2)  # 2 bad / 3 total
        assert stats.top_bad_words[0][1] == 2  # 「显卡」在两条负反馈标题中各计一次
        assert stats.category_totals["羊毛"] == 3
        payload = stats.to_dict()
        assert payload["bad_ratio"] == 0.75
        assert payload["top_bad_words"][0]["key"] == stats.top_bad_words[0][0]

    def test_stats_rows_without_snapshots_count_totals_only(self, store):
        store.save_feedback(
            FeedbackRecord(dedup_key="https://gone/1", verdict="bad", channel="telegram")
        )

        stats = compute_feedback_stats(store.list_feedback())

        assert stats.total == 1 and stats.bad == 1
        assert stats.top_bad_categories == []
        assert stats.top_bad_words == []


# ---------------------------------------------------------------------------
# TG getUpdates 轮询(桌面形态接收;验收 1)
# ---------------------------------------------------------------------------


def get_updates_handler(
    updates: list[dict[str, Any]], calls: list[dict[str, Any]]
) -> httpx.MockTransport:
    """Mock the Bot API getUpdates endpoint, capturing every request.

    Behaves like the real API on ``offset``: updates with
    ``update_id < offset`` are already confirmed and never re-delivered.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "params": dict(request.url.params)})
        offset_raw = request.url.params.get("offset")
        offset = int(offset_raw) if offset_raw and offset_raw.isdigit() else None
        visible = [u for u in updates if offset is None or u["update_id"] >= offset]
        return httpx.Response(200, json={"ok": True, "result": visible})

    return httpx.MockTransport(handler)


def make_poller(updates: list[dict[str, Any]], calls: list[dict[str, Any]]):
    client = httpx.AsyncClient(transport=get_updates_handler(updates, calls))
    return TelegramFeedbackPoller(token=TOKEN, client=client), client


class TestTelegramCallbackData:
    def test_parse_accepts_url_keys_with_colons(self):
        assert parse_callback_data("fb:good:https://x.example/a:b") == (
            "good",
            "https://x.example/a:b",
        )
        assert parse_callback_data("fb:bad:plain-key") == ("bad", "plain-key")

    def test_parse_rejects_non_feedback_data(self):
        assert parse_callback_data("hello") is None
        assert parse_callback_data("fb:") is None
        assert parse_callback_data("fb:weird:key") is None  # 判定词表外
        assert parse_callback_data("fb:good:") is None  # 空键
        assert parse_callback_data(None) is None


class TestTelegramPolling:
    def test_poll_parses_callbacks_and_advances_offset(self):
        updates = [
            {
                "update_id": 11,
                "callback_query": {"id": "c1", "data": "fb:bad:https://items.example/a"},
            },
            {"update_id": 12, "message": {"message_id": 1, "text": "普通消息"}},  # 非回调
            {
                "update_id": 13,
                "callback_query": {"id": "c2", "data": "fb:good:https://items.example/b"},
            },
        ]
        calls: list[dict[str, Any]] = []
        poller, client = make_poller(updates, calls)
        try:
            result = run(poller.poll())
        finally:
            run(client.aclose())

        assert result.update_count == 3
        assert len(result.callbacks) == 2
        assert result.skipped == 0  # 普通消息不是「坏数据」,只是非反馈更新
        assert result.next_offset == 14
        assert result.callbacks[0].verdict == "bad"
        assert result.callbacks[0].channel == "telegram"
        # 下一轮应带 offset 书签(Telegram 未确认更新会重发)。
        assert calls[0]["params"].get("timeout") == "0"

    def test_poll_offset_bookmark_is_sent_next_round(self):
        updates = [
            {
                "update_id": 7,
                "callback_query": {"id": "c1", "data": "fb:good:https://x/1"},
            }
        ]
        calls: list[dict[str, Any]] = []
        poller, client = make_poller(updates, calls)
        try:
            first = run(poller.poll())
            second = run(poller.poll(offset=first.next_offset))
        finally:
            run(client.aclose())

        assert calls[1]["params"]["offset"] == "8"
        assert second.callbacks == []

    def test_poll_skips_foreign_callback_data_without_dying(self):
        updates = [
            {"update_id": 1, "callback_query": {"id": "c1", "data": "/start@other_bot"}},
            {"update_id": 2, "callback_query": {"id": "c2", "data": "fb:maybe:x"}},
        ]
        calls: list[dict[str, Any]] = []
        poller, client = make_poller(updates, calls)
        try:
            result = run(poller.poll())
        finally:
            run(client.aclose())

        assert result.callbacks == []
        assert result.skipped == 2
        assert result.next_offset == 3  # 坏数据也推进书签,不重复消费

    def test_poll_api_error_is_structured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

        poller = TelegramFeedbackPoller(
            token=TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(TelegramFeedbackError) as excinfo:
            run(poller.poll())
        assert excinfo.value.code == "telegram_api_error"

    def test_poll_non_json_response_is_structured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        poller = TelegramFeedbackPoller(
            token=TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(TelegramFeedbackError) as excinfo:
            run(poller.poll())
        assert excinfo.value.code == "invalid_response"

    def test_poller_unresolvable_token_fails_fast_with_reference_name(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(TelegramFeedbackError) as excinfo:
            TelegramFeedbackPoller()
        assert excinfo.value.code == "env_var_missing"
        assert "TELEGRAM_BOT_TOKEN" in str(excinfo.value)
        assert "secret-value" not in str(excinfo.value)  # 值永不入错误消息

    def test_mock_getupdates_ingest_stats_end_to_end(self, store):
        """验收 1:mock getUpdates → 入库 → stats 全链(单测)。"""
        item = add_item(store, url="https://items.example/a")
        updates = [
            {
                "update_id": 21,
                "callback_query": {"id": "c1", "data": f"fb:bad:{item.dedup_key}"},
            },
            {
                "update_id": 22,
                "callback_query": {"id": "c2", "data": "fb:bad:https://items.example/a"},
            },
        ]
        calls: list[dict[str, Any]] = []
        poller, client = make_poller(updates, calls)
        try:
            result = run(poller.poll())
        finally:
            run(client.aclose())

        report = ingest_callbacks(store, result.callbacks, now=NOW)
        assert report.saved == 2 and report.skipped == 0

        stats = compute_feedback_stats(store.list_feedback())
        assert stats.bad == 2 and stats.good == 0
        assert stats.by_channel == {"telegram": 2}
        assert stats.top_bad_categories == [("羊毛", 2)]
        # 入库行关联 items 且带快照(item 存在时)。
        row = store.list_feedback()[0]
        assert row.item_id == item.id
        assert row.title == "显卡降价了"


# ---------------------------------------------------------------------------
# CLI mark / list / stats(桌面形态第三接收路;验收 2)
# ---------------------------------------------------------------------------


class TestCliFeedback:
    def test_cli_mark_by_id_saves_feedback(self, tmp_path, capsys):
        """验收 2:CLI mark → 入库(真实 tmp 库,不经任何 mock)。"""
        db_path = tmp_path / "cli.db"
        seed = SQLiteStore(db_path)
        item = add_item(seed)
        seed.close()

        exit_code = cli_main(
            ["feedback", "mark", str(item.id), "bad", "--db", str(db_path), "--json"]
        )

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "feedback" and payload["action"] == "mark"
        assert payload["verdict"] == "bad"
        assert payload["item_id"] == item.id
        verify = SQLiteStore(db_path)
        try:
            rows = verify.list_feedback()
            assert len(rows) == 1
            assert rows[0].channel == "cli"  # CLI 手动标记的渠道语义
            assert rows[0].title == item.title  # 入库带快照
        finally:
            verify.close()

    def test_cli_mark_by_dedup_key_url(self, tmp_path):
        db_path = tmp_path / "cli.db"
        seed = SQLiteStore(db_path)
        item = add_item(seed, url="https://x/via-url")
        seed.close()

        exit_code = cli_main(
            ["feedback", "mark", item.dedup_key, "good", "--db", str(db_path)]
        )

        assert exit_code == 0
        verify = SQLiteStore(db_path)
        try:
            assert verify.list_feedback()[0].verdict == "good"
        finally:
            verify.close()

    def test_cli_mark_unknown_item_exits_one_structured(self, tmp_path, capsys):
        db_path = tmp_path / "cli.db"
        seed = SQLiteStore(db_path)
        add_item(seed)
        seed.close()

        exit_code = cli_main(
            ["feedback", "mark", "99999", "bad", "--db", str(db_path), "--json"]
        )

        assert exit_code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "item_not_found"
        verify = SQLiteStore(db_path)
        try:
            assert verify.list_feedback() == []  # 未解析到条目:零写入
        finally:
            verify.close()

    def test_cli_list_and_stats_json_contract(self, tmp_path, capsys):
        db_path = tmp_path / "cli.db"
        seed = SQLiteStore(db_path)
        item = add_item(seed, url="https://x/stats", title="显卡促销", category="羊毛")
        record_feedback(seed, verdict="bad", channel=FEEDBACK_CHANNEL_CLI, item=item, now=NOW)
        seed.close()

        assert cli_main(["feedback", "list", "--db", str(db_path), "--json"]) == 0
        listing = json.loads(capsys.readouterr().out)
        assert listing["count"] == 1
        assert listing["items"][0]["channel"] == "cli"

        assert cli_main(
            ["feedback", "stats", "--db", str(db_path), "--json", "--window-days", "7"]
        ) == 0
        stats = json.loads(capsys.readouterr().out)
        assert stats["window_days"] == 7
        assert stats["stats"]["bad"] == 1
        assert stats["stats"]["top_bad_categories"][0]["key"] == "羊毛"
        assert "active_tuning" in stats and "tuning_history" in stats

    def test_cli_stats_rejects_bad_window(self, tmp_path, capsys):
        exit_code = cli_main(
            ["feedback", "stats", "--db", str(tmp_path / "x.db"), "--window-days", "0", "--json"]
        )

        assert exit_code == 1
        assert json.loads(capsys.readouterr().out)["error"] == "feedback"


# ---------------------------------------------------------------------------
# 调参:阈值 / 幂等 / 释放 / 权重语义
# ---------------------------------------------------------------------------


class TestFeedbackTuner:
    def make_policy(self, **overrides: Any) -> TuningPolicy:
        data: dict[str, Any] = {"min_bad_count": 2, "min_bad_ratio": 0.5, "window_days": 14}
        data.update(overrides)
        return TuningPolicy(**data)

    def test_apply_writes_category_word_and_note_adjustments(self, store):
        item = add_item(store)
        add_feedback(store, item, "bad", channel=FEEDBACK_CHANNEL_TELEGRAM)
        add_feedback(store, item, "bad")
        tuner = FeedbackTuner(self.make_policy())

        written = tuner.apply(store, now=NOW)

        kinds = {record.kind for record in written}
        assert "category_penalty" in kinds  # 类目:羊毛 2/2 负 → weight 0.0
        assert "mute_weight" in kinds  # 标题词二元组过阈值
        assert "prompt_note" in kinds  # enrich 评分要点,可追溯
        category_row = next(r for r in written if r.kind == "category_penalty")
        assert category_row.payload["category"] == "羊毛"
        assert category_row.payload["weight"] == 0.0
        assert category_row.payload["bad_count"] == 2
        # 活跃调参立即可查,历史入表可追溯。
        active = load_active_tuning(store)
        assert active.category_penalties["羊毛"] == 0.0
        assert active.penalty_for(category="羊毛") == 0.0
        assert active.effective_mute_words()  # weight 0.0 → 并入 enrich mute 词表
        assert len(store.list_tuning()) == len(written)

    def test_apply_is_idempotent_when_state_unchanged(self, store):
        item = add_item(store)
        add_feedback(store, item, "bad")
        add_feedback(store, item, "bad")
        tuner = FeedbackTuner(self.make_policy())

        first = tuner.apply(store, now=NOW)
        assert first  # 首轮写调整
        assert tuner.apply(store, now=NOW + timedelta(days=1)) == []  # 状态未变:零新行
        assert len(store.list_tuning()) == len(first)  # 历史不膨胀

    def test_apply_respects_min_bad_count(self, store):
        item = add_item(store)
        add_feedback(store, item, "bad")  # 只有 1 条负反馈:一次点击不锁死词条
        tuner = FeedbackTuner(self.make_policy(min_bad_count=2))

        assert tuner.apply(store, now=NOW) == []
        assert load_active_tuning(store).has_penalties is False

    def test_apply_respects_bad_ratio_guard(self, store):
        item = add_item(store)
        for verdict in ("bad", "bad", "good", "good", "good"):
            add_feedback(store, item, verdict)  # 2/5 = 0.4 < 0.5:不调参
        tuner = FeedbackTuner(self.make_policy())

        assert tuner.apply(store, now=NOW) == []

    def test_partial_weight_penalizes_without_full_mute(self, store):
        item = add_item(store)
        add_feedback(store, item, "bad")
        add_feedback(store, item, "bad")
        add_feedback(store, item, "good")  # 2/3 → weight ≈ 0.33
        tuner = FeedbackTuner(self.make_policy())

        tuner.apply(store, now=NOW)

        active = load_active_tuning(store)
        assert active.category_penalties["羊毛"] == pytest.approx(0.33)
        assert active.effective_mute_words() == []  # 未到全降权,不进 mute
        assert active.penalty_for(category="羊毛") == pytest.approx(0.33)
        assert active.penalty_for(title="无关标题") == 1.0

    def test_release_row_written_when_negative_share_recovers(self, store):
        item = add_item(store)
        add_feedback(store, item, "bad")
        add_feedback(store, item, "bad")
        tuner = FeedbackTuner(self.make_policy())
        tuner.apply(store, now=NOW)  # weight 0.0

        for _ in range(4):
            add_feedback(store, item, "good")  # 2/6 ≈ 0.33 < 0.5:应释放
        written = tuner.apply(store, now=NOW + timedelta(days=1))

        releases = [r for r in written if r.payload.get("released")]
        assert releases, "负反馈占比回落应写释放行(可追溯的恢复)"
        assert all(r.payload["weight"] == 1.0 for r in releases)
        assert load_active_tuning(store).has_penalties is False

    def test_policy_rejects_invalid_thresholds(self):
        with pytest.raises(ValueError, match="window_days"):
            TuningPolicy(window_days=0)
        with pytest.raises(ValueError, match="min_bad_ratio"):
            TuningPolicy(min_bad_ratio=1.5)
        with pytest.raises(ValueError, match="min_bad_count"):
            TuningPolicy(min_bad_count=0)

    def test_load_active_tuning_newest_row_wins_per_key(self, store):
        store.save_tuning(TuningRecord(kind="mute_weight", payload={"word": "显卡", "weight": 0.0}))
        store.save_tuning(TuningRecord(kind="mute_weight", payload={"word": "显卡", "weight": 1.0}))

        active = load_active_tuning(store)

        assert active.mute_weights == {"显卡": 1.0}  # 最新行生效,旧行留作历史


# ---------------------------------------------------------------------------
# 飞书卡片回调端点(服务端/compose 形态;验收 4)
# ---------------------------------------------------------------------------


def feishu_button_value(verdict: str, dedup_key: str) -> dict[str, Any]:
    return {"feedback": verdict, "item": dedup_key}


class TestFeishuCallbackConfig:
    def test_default_config_is_disabled(self):
        assert FeishuCallbackConfig().enabled is False

    def test_disabled_handler_answers_404(self):
        handler = FeishuCallbackHandler(FeishuCallbackConfig(), token="t")

        response = handler.handle(headers={}, body=b"{}")

        assert response.status == 404
        assert response.payload["error"] == "callback_disabled"
        assert response.callbacks == []

    def test_enabled_requires_token_ref(self):
        with pytest.raises(FeishuCallbackConfigError) as excinfo:
            FeishuCallbackConfig(enabled=True)
        assert excinfo.value.code == "missing_token_ref"

    def test_enabled_rejects_plaintext_token(self):
        with pytest.raises(FeishuCallbackConfigError):
            FeishuCallbackConfig(enabled=True, token_ref="plain-secret-value")

    def test_enabled_refuses_public_bind_hosts(self):
        for host in ("0.0.0.0", "8.8.8.8", "::"):
            with pytest.raises(FeishuCallbackConfigError) as excinfo:
                FeishuCallbackConfig(enabled=True, host=host, token_ref="env:T")
            assert excinfo.value.code == "public_bind_refused"

    def test_enabled_allows_loopback_and_private_hosts(self):
        for host in ("127.0.0.1", "192.168.1.5", "10.0.0.3", "localhost"):
            FeishuCallbackConfig(enabled=True, host=host, token_ref="env:T")  # 不抛即过


class TestFeishuCallbackHandler:
    def make_enabled_handler(self) -> FeishuCallbackHandler:
        config = FeishuCallbackConfig(enabled=True, token_ref="env:T")
        return FeishuCallbackHandler(config, token="secret-token")

    def test_missing_token_header_is_unauthorized(self):
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("good", "https://x/1")}}
        ).encode()

        response = handler.handle(headers={}, body=body)

        assert response.status == 401
        assert response.payload["error"] == "unauthorized"

    def test_wrong_token_is_unauthorized(self):
        handler = self.make_enabled_handler()

        response = handler.handle(
            headers={"X-Myia-Token": "wrong"}, body=json.dumps({}).encode()
        )

        assert response.status == 401

    def test_non_ascii_header_token_is_structured_401_not_500(self):
        """非 ASCII token(素材 11):encode 后比较,回结构化 401 而非裸 TypeError。"""
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("good", "https://x/1")}}
        ).encode()

        response = handler.handle(headers={"X-Myia-Token": "令牌-密钥✓"}, body=body)

        assert response.status == 401
        assert response.payload["error"] == "unauthorized"

    def test_non_ascii_payload_token_is_structured_401(self):
        """负载内验证 token 同样允许非 ASCII:mismatch → 401,不抛 TypeError。"""
        handler = self.make_enabled_handler()
        body = json.dumps(
            {
                "header": {"token": "秘密トークン"},
                "action": {"value": feishu_button_value("good", "https://x/1")},
            }
        ).encode()

        response = handler.handle(headers={}, body=body)

        assert response.status == 401
        assert response.payload["error"] == "unauthorized"

    def test_non_ascii_configured_token_roundtrip(self):
        """配置侧本身是非 ASCII token:相等 → 200,不等 → 401(双向 encode)。"""
        config = FeishuCallbackConfig(enabled=True, token_ref="env:T")
        handler = FeishuCallbackHandler(config, token="密钥-カギ")
        body = json.dumps(
            {"action": {"value": feishu_button_value("good", "https://x/1")}}
        ).encode()

        ok = handler.handle(headers={"X-Myia-Token": "密钥-カギ"}, body=body)
        bad = handler.handle(headers={"X-Myia-Token": "别的密钥"}, body=body)

        assert ok.status == 200 and ok.payload["code"] == "ok"
        assert bad.status == 401

    def test_valid_header_token_parses_button_callback(self):
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("bad", "https://items.example/a")}}
        ).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.status == 200
        assert response.payload["code"] == "ok"
        assert response.callbacks == [
            {
                "channel": "feishu",
                "verdict": "bad",
                "dedup_key": "https://items.example/a",
                "external_id": None,  # 无事件信封 → 幂等身份缺失,降级 NULL(恒入库)
            }
        ]

    def test_payload_verification_token_accepted(self):
        """飞书原生验证 token(负载内 header.token)与自定义 header 等效。"""
        handler = self.make_enabled_handler()
        body = json.dumps(
            {
                "header": {"token": "secret-token", "event_id": "e1"},
                "event": {
                    "action": {"form": {"value": feishu_button_value("good", "https://x/2")}}
                },
            }
        ).encode()

        response = handler.handle(headers={}, body=body)

        assert response.status == 200
        assert response.callbacks[0]["verdict"] == "good"

    def test_url_verification_handshake_echoes_challenge_after_auth(self):
        handler = self.make_enabled_handler()
        body = json.dumps({"type": "url_verification", "challenge": "ajls384kdd"}).encode()

        authorized = handler.handle(
            headers={"X-Myia-Token": "secret-token"}, body=body
        )
        anonymous = handler.handle(headers={}, body=body)

        assert authorized.status == 200
        assert authorized.payload["challenge"] == "ajls384kdd"
        assert anonymous.status == 401  # 开启即强制鉴权,握手也不例外

    def test_invalid_json_is_400(self):
        handler = self.make_enabled_handler()

        response = handler.handle(
            headers={"X-Myia-Token": "secret-token"}, body=b"not-json{"
        )

        assert response.status == 400
        assert response.payload["error"] == "invalid_json"

    def test_invalid_verdict_is_400_structured(self):
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("非常好", "https://x/1")}}
        ).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.status == 400
        assert response.payload["error"] == "invalid_callback"
        assert "good" in response.payload["message"]  # 错误携带合法范围

    def test_missing_item_identity_is_400(self):
        handler = self.make_enabled_handler()
        body = json.dumps({"action": {"value": {"feedback": "good"}}}).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.status == 400

    def test_non_button_event_is_acked_but_ignored(self):
        handler = self.make_enabled_handler()
        body = json.dumps({"event": {"action": {"value": {"other": "event"}}}}).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.status == 200
        assert response.payload["code"] == "ignored"
        assert response.callbacks == []

    def test_stringified_action_value_is_parsed(self):
        handler = self.make_enabled_handler()
        value = json.dumps(feishu_button_value("bad", "https://x/3"), ensure_ascii=False)
        body = json.dumps({"action": {"value": value}}).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.status == 200
        assert response.callbacks[0]["dedup_key"] == "https://x/3"

    def test_public_client_ip_is_forbidden(self):
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("good", "https://x/1")}}
        ).encode()

        response = handler.handle(
            headers={"X-Myia-Token": "secret-token"}, body=body, client_ip="8.8.8.8"
        )

        assert response.status == 403
        assert response.payload["error"] == "forbidden_source"

    def test_loopback_client_ip_passes(self):
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("good", "https://x/1")}}
        ).encode()

        response = handler.handle(
            headers={"X-Myia-Token": "secret-token"}, body=body, client_ip="127.0.0.1"
        )

        assert response.status == 200

    def test_endpoint_to_store_integration(self, store):
        """端点解析的回调经 ingest 入库(runner 的真实装配路径)。"""
        handler = self.make_enabled_handler()
        body = json.dumps(
            {"action": {"value": feishu_button_value("bad", "https://x/1")}}
        ).encode()

        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)
        ingest_callbacks(store, response.callbacks, now=NOW)

        rows = store.list_feedback()
        assert len(rows) == 1
        assert rows[0].channel == "feishu"
        assert rows[0].verdict == "bad"

    def test_build_server_binds_loopback_and_ingests(self, store, tmp_path):
        """runner 装配:回环端口真实起服,POST → 200 → 反馈入库(仅本机回环)。"""
        config = FeishuCallbackConfig(enabled=True, token_ref="env:T", host="127.0.0.1", port=0)
        handler = FeishuCallbackHandler(config, token="secret-token")
        server = build_server(config, store, handler=handler)
        host, port = server.server_address[:2]
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    f"http://{host}:{port}/callback",
                    headers={"X-Myia-Token": "secret-token"},
                    json={"action": {"value": feishu_button_value("good", "https://x/9")}},
                )
        finally:
            server.shutdown()
            server.server_close()

        assert response.status_code == 200
        assert response.json()["code"] == "ok"
        rows = store.list_feedback()
        assert len(rows) == 1 and rows[0].channel == "feishu"


# ---------------------------------------------------------------------------
# pipeline 接线:run 起点调参 + 负反馈降权 demo(验收 3)
# ---------------------------------------------------------------------------


def decisions_by_url(result: Any) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for push in result.pushes:
        for decision in push.decisions:
            decisions[decision["url"]] = decision
    return decisions


def page_handler(pages: list[list[dict[str, str]]]):
    """Stateful direct_api source: each request returns the next page."""
    state = {"call": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        index = min(state["call"], len(pages) - 1)
        state["call"] += 1
        return httpx.Response(200, json=pages[index])

    return httpx.MockTransport(handler)


class TestPipelineFeedbackLoop:
    def test_one_round_negative_feedback_downweights_next_round(self, tmp_path):
        """验收 3(demo):一轮负反馈后,同类词条目下轮被降权归档。

        全真链路:run1 推送 → 用户标 bad ×2 → run2 起点 apply 调参(历史入表)
        → analyze 命中降权 → score 9.0×0=0.0 → route 按 score<5 归档。
        """
        store = SQLiteStore(tmp_path / "loop.db")
        config = make_category()
        client = httpx.AsyncClient(
            transport=page_handler(
                [
                    [  # 第 1 轮:显卡条目 + 无关条目
                        {"title": "显卡降价了", "url": "https://items.example/gpu1"},
                        {"title": "普通流水账一篇", "url": "https://items.example/misc1"},
                    ],
                    [  # 第 2 轮:同类新条目(标题同词)+ 无关新条目
                        {"title": "显卡又降价了", "url": "https://items.example/gpu2"},
                        {"title": "另一篇普通流水", "url": "https://items.example/misc2"},
                    ],
                ]
            )
        )
        try:
            round1 = run(Pipeline(config, store=store, client=client, enricher=make_enricher(9.0)).run())
            assert round1.feedback_tuning is not None  # run 起点即调参(首轮无反馈:applied=0)
            assert round1.feedback_tuning["applied"] == 0
            assert decisions_by_url(round1)["https://items.example/gpu1"]["mode"] == "immediate"

            # 用户对上轮显卡条目标「没价值」×2(TG 回调 + CLI 两种渠道)。
            bad_item = store.get_item_by_dedup_key("https://items.example/gpu1")
            assert bad_item is not None
            record_feedback(store, verdict="bad", channel=FEEDBACK_CHANNEL_TELEGRAM, item=bad_item)
            record_feedback(store, verdict="bad", channel=FEEDBACK_CHANNEL_CLI, item=bad_item)

            round2 = run(Pipeline(config, store=store, client=client, enricher=make_enricher(9.0)).run())
        finally:
            store.close()
            run(client.aclose())

        # 调参历史入表(run2 起点应用,可追溯)。
        assert round2.feedback_tuning is not None and round2.feedback_tuning["applied"] >= 1
        tuning = round2.feedback_tuning["adjustments"]
        assert any(adj["kind"] == "mute_weight" for adj in tuning)
        # 同类条目(标题同词)下轮被降权:score 9.0 → 0.0 → 路由归档。
        decisions = decisions_by_url(round2)
        demoted = decisions["https://items.example/gpu2"]
        assert demoted["mode"] == "archive", f"同类条目应被降权归档,实际 {demoted}"
        demoted_item = next(i for i in round2.items if i.url == "https://items.example/gpu2")
        assert demoted_item.metadata["feedback_weight"] == 0.0
        assert demoted_item.metadata["score"] == 0.0
        assert "feedback-muted" in demoted_item.metadata["tags"]
        # 无关条目不受影响,照常 immediate。
        assert decisions["https://items.example/misc2"]["mode"] == "immediate"
        analyze = round2.stage("analyze")
        assert analyze.skips.get("feedback_downweighted") == 1

    def test_dry_run_never_applies_tuning(self, tmp_path):
        store = SQLiteStore(tmp_path / "dry.db")
        client = httpx.AsyncClient(
            transport=page_handler([[{"title": "显卡", "url": "https://x/1"}]])
        )
        try:
            result = run(
                Pipeline(make_category(), store=store, client=client, enricher=make_enricher()).run(
                    dry_run=True
                )
            )
        finally:
            store.close()
            run(client.aclose())

        assert result.feedback_tuning is None  # dry-run 零持久化副作用


# ---------------------------------------------------------------------------
# 常驻反馈轮询循环(素材 13):offset 书签 / store 复用 / 单轮异常隔离
# ---------------------------------------------------------------------------


class ScriptedPoller:
    """``_feedback_poll_loop`` 的剧本化替身:逐轮返回预置结果或抛异常。

    记录每轮收到的 ``offset`` 实参(书签推进的直接证据);轮次用尽后重复
    最后一轮(与真实 API 的稳态等价:书签之后的轮次不再有新更新)。
    """

    def __init__(self, rounds: list[Any]) -> None:
        self._rounds = list(rounds)
        self.offsets: list[int | None] = []

    async def poll(self, *, offset: int | None = None) -> Any:
        self.offsets.append(offset)
        outcome = self._rounds[min(len(self.offsets) - 1, len(self._rounds) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestFeedbackPollLoop:
    """``_feedback_poll_loop`` 是唯一常驻接线(素材 13):零测试 → 逐契约补齐。"""

    def make_tg_pipeline(self, store: SQLiteStore) -> Pipeline:
        """配 telegram 通道的 Pipeline(轮询循环只触 store,不跑采集)。"""
        pipeline = Pipeline(
            make_category(
                push=[{"channel": "telegram", "target": "env:MYIA_TG_CHAT_ID"}]
            ),
            store=store,
            enricher=make_enricher(),
        )
        # __init__ 把轮询间隔钳到 ≥1s(生产防打爆);单测把属性直接调小,
        # 让三轮循环在毫秒级完成——被测的是循环契约,不是钳制逻辑。
        pipeline._feedback_poll_interval = 0.01
        return pipeline

    async def _drive(self, pipeline: Pipeline, poller: ScriptedPoller, *, rounds: int):
        """把循环跑起来,推进到第 ``rounds`` 轮后取消;返回任务(可断言取消态)。"""
        loop = asyncio.create_task(pipeline._feedback_poll_loop(poller))
        try:
            for _ in range(2000):
                if len(poller.offsets) >= rounds:
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError(f"循环 {rounds} 轮未推进(offsets={poller.offsets})")
        finally:
            loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop
        return loop

    def test_offset_bookmark_advances_and_callbacks_ingest(self, store):
        """offset 书签逐轮推进:首轮 None,其后各带上一轮 next_offset;回调入库。"""
        pipeline = self.make_tg_pipeline(store)
        poller = ScriptedPoller(
            [
                PollResult(
                    callbacks=[TelegramCallback(update_id=11, verdict="bad", dedup_key="k-11")],
                    update_count=1,
                    next_offset=12,
                ),
                PollResult(
                    callbacks=[TelegramCallback(update_id=12, verdict="good", dedup_key="k-12")],
                    update_count=1,
                    next_offset=13,
                ),
                PollResult(update_count=0, next_offset=None),  # 无更新:书签原地保持
            ]
        )

        task = asyncio.run(self._drive(pipeline, poller, rounds=3))

        assert poller.offsets == [None, 12, 13]  # 书签跨轮保持(首轮无书签)
        assert task.cancelled()  # CancelledError 穿透循环(正常停机路径不受破坏)
        rows = store.list_feedback()
        assert [(row.verdict, row.dedup_key) for row in rows] == [
            ("good", "k-12"),  # list_feedback 新→旧
            ("bad", "k-11"),
        ]
        assert {row.channel for row in rows} == {"telegram"}
        assert {row.channel for row in rows} == {"telegram"}
        # update_id 即幂等身份(TelegramCallback.external_id → 唯一索引),
        # 重放语义在 TestFeedbackIdempotency 覆盖;list_feedback 读路径不
        # 回读 external_id 列(store 层既有缺口,不在本任务边界内修)。
        assert all(row.external_id is None for row in rows)

    def test_loop_reuses_the_pipeline_store_across_rounds(self, store, monkeypatch):
        """store 复用:每轮入库拿到的是同一个注入 store(不逐轮开新库)。"""
        pipeline = self.make_tg_pipeline(store)
        real_ingest = pipeline_module.ingest_callbacks
        seen_stores: list[Any] = []

        def spy(current_store, callbacks, **kwargs):
            seen_stores.append(current_store)
            return real_ingest(current_store, callbacks, **kwargs)

        monkeypatch.setattr(pipeline_module, "ingest_callbacks", spy)
        poller = ScriptedPoller(
            [
                PollResult(
                    callbacks=[TelegramCallback(update_id=21, verdict="bad", dedup_key="k-21")],
                    next_offset=22,
                ),
                PollResult(
                    callbacks=[TelegramCallback(update_id=22, verdict="bad", dedup_key="k-22")],
                    next_offset=23,
                ),
            ]
        )

        asyncio.run(self._drive(pipeline, poller, rounds=2))

        assert len(seen_stores) == 2
        assert seen_stores[0] is store and seen_stores[1] is store  # 两轮同一 store 对象
        assert len(store.list_feedback()) == 2  # 数据都落在注入库里

    def test_single_round_failure_does_not_kill_loop(self, store, caplog):
        """异常隔离:单轮 poll 抛错 → 告警并继续,下一轮照常书签轮询与入库。"""
        pipeline = self.make_tg_pipeline(store)
        poller = ScriptedPoller(
            [
                TelegramFeedbackError("telegram_api_error", "409 Conflict: terminated by other getUpdates request"),
                PollResult(
                    callbacks=[TelegramCallback(update_id=31, verdict="good", dedup_key="k-31")],
                    update_count=1,
                    next_offset=32,
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="shishi.pipeline"):
            asyncio.run(self._drive(pipeline, poller, rounds=2))

        assert poller.offsets == [None, None]  # 失败轮不推进书签,下轮仍从头轮询
        assert any("TG 反馈轮询失败" in record.message for record in caplog.records)
        assert [(row.verdict, row.dedup_key) for row in store.list_feedback()] == [
            ("good", "k-31")
        ]  # 恢复轮照常入库(循环未终止)



# ---------------------------------------------------------------------------
# 幂等去重(回归:双击/平台重试/重启重放曾凑满 min_bad_count 击穿护栏)
# ---------------------------------------------------------------------------


class TestFeedbackIdempotency:
    """同 (channel, external_id) 只记一次;CLI(NULL)行为不变。"""

    def test_same_tg_update_id_saved_once(self, store):
        from shishi.feedback import ingest_callbacks

        callback = {
            "channel": "telegram",
            "verdict": "bad",
            "dedup_key": "k-1",
            "external_id": "9001",
        }
        first = ingest_callbacks(store, [callback])
        second = ingest_callbacks(store, [callback])  # 双击/重试/重启重放

        assert first.saved == 1 and first.duplicates == 0
        assert second.saved == 0 and second.duplicates == 1
        assert len(store.list_feedback()) == 1  # 表里只有一行

    def test_replayed_good_after_bad_is_distinct(self, store):
        from shishi.feedback import ingest_callbacks

        base = {"channel": "telegram", "dedup_key": "k-2"}
        ingest_callbacks(store, [{**base, "verdict": "bad", "external_id": "42"}])
        # 改判是第二次点击 = 新的事件身份(新 update_id),不与首次冲突
        ingest_callbacks(store, [{**base, "verdict": "good", "external_id": "43"}])
        # 同一事件重放(同 update_id)即便判定字段不同也只记首次
        ingest_callbacks(store, [{**base, "verdict": "good", "external_id": "42"}])

        verdicts = sorted(row.verdict for row in store.list_feedback())
        assert verdicts == ["bad", "good"]  # list_feedback 倒序,只比集合

    def test_cli_rows_without_external_id_always_insert(self, store):
        from shishi.feedback import record_feedback

        first = record_feedback(store, verdict="bad", channel="cli", dedup_key="k-3")
        second = record_feedback(store, verdict="bad", channel="cli", dedup_key="k-3")

        assert first.id is not None and second.id is not None and first.id != second.id
        assert len(store.list_feedback()) == 2  # NULL 不受唯一索引约束

    def test_tg_callback_carries_update_id_as_external_id(self, store):
        from shishi.feedback import ingest_callbacks
        from shishi.push.telegram_feedback import TelegramCallback

        report = ingest_callbacks(
            store,
            [TelegramCallback(update_id=77, verdict="bad", dedup_key="k-4")],
        )
        assert report.saved == 1
        report = ingest_callbacks(
            store,
            [TelegramCallback(update_id=77, verdict="bad", dedup_key="k-4")],
        )
        assert report.saved == 0 and report.duplicates == 1

    def test_feishu_event_id_extracted_and_deduped(self, store):
        import json

        from shishi.push.feishu_callback import FeishuCallbackConfig, FeishuCallbackHandler

        handler = FeishuCallbackHandler(
            FeishuCallbackConfig(enabled=True, token_ref="env:T"), token="secret-token"
        )
        body = json.dumps(
            {
                "header": {"event_id": "evt-1"},
                "action": {"value": {"feedback": "bad", "item": "k-5"}},
            }
        ).encode()
        response = handler.handle(headers={"X-Myia-Token": "secret-token"}, body=body)

        assert response.callbacks[0]["external_id"] == "evt-1"
        from shishi.feedback import ingest_callbacks

        first = ingest_callbacks(store, response.callbacks)
        second = ingest_callbacks(store, response.callbacks)  # 平台重试同一事件
        assert first.saved == 1 and second.saved == 0 and second.duplicates == 1


class TestFeedbackV6Migration:
    """v5 → v6:feedback 表补 external_id + (channel, external_id) 唯一索引。"""

    def _make_legacy_v5_db(self, path):
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                dedup_key TEXT NOT NULL,
                verdict TEXT NOT NULL,
                channel TEXT NOT NULL,
                title TEXT,
                category TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO feedback (dedup_key, verdict, channel, created_at)
                VALUES ('k-old', 'bad', 'telegram', '2026-09-01T00:00:00+00:00');
            INSERT INTO store_meta (key, value) VALUES ('schema_version', '5');
            """
        )
        conn.commit()
        conn.close()

    def test_v5_database_gains_external_id_and_keeps_rows(self, tmp_path):
        db_path = tmp_path / "legacy5.db"
        self._make_legacy_v5_db(db_path)
        store = SQLiteStore(db_path)
        try:
            assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
            rows = store.list_feedback()
            assert len(rows) == 1 and rows[0].verdict == "bad"  # 旧行零损失
            # 新列立即可用:同 (channel, external_id) 幂等,旧行(NULL)不受限
            from shishi.feedback import ingest_callbacks

            callback = {"channel": "telegram", "verdict": "bad", "dedup_key": "k-new",
                        "external_id": "1"}
            first = ingest_callbacks(store, [callback])
            second = ingest_callbacks(store, [callback])
            assert first.saved == 1 and second.duplicates == 1
            assert len(store.list_feedback()) == 2  # 旧行 + 新事件一行
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 调参释放正确性(回归:Top-N 截断曾把仍越线的键误标「已恢复」并伪造 0/0 计数)
# ---------------------------------------------------------------------------


class TestTuningReleaseCorrectness:
    def _seed(self, store, *, category: str, title: str, verdict: str, at) -> None:
        store.save_feedback(
            FeedbackRecord(
                dedup_key=f"https://x/{title}/{verdict}/{at.isoformat()}",
                verdict=verdict,
                channel="telegram",
                title=title,
                category=category,
                created_at=at,
            )
        )

    def test_active_key_still_crossed_outside_top_n_is_not_released(self, store):
        """挤出 Top-N ≠ 已恢复:窗口内仍越线的活跃键不写释放行(权重保持)。"""
        from datetime import datetime, timezone

        from shishi.feedback import FeedbackTuner, TuningPolicy

        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        tuner = FeedbackTuner(TuningPolicy(min_bad_count=2, min_bad_ratio=0.5, top_n=3))
        # 第 1 轮:d 类目 2 bad / 2 total → 降权,weight 0.0
        for i in range(2):
            self._seed(store, category="d", title=f"折扣 d{i}", verdict="bad", at=now)
        tuner.apply(store, now=now)
        active = tuner.active(store).category_penalties
        assert active.get("d") == 0.0

        # 第 2 轮:a/b/c 各 2 bad 越线把 d 挤出 Top-3,但 d 窗口内又添 1 bad(3 bad/3 total 仍 100%)
        for name in ("a", "b", "c"):
            for i in range(2):
                self._seed(store, category=name, title=f"折扣 {name}{i}", verdict="bad", at=now)
        self._seed(store, category="d", title="折扣 d2", verdict="bad", at=now)

        written = tuner.apply(store, now=now)
        releases = [
            r for r in written
            if r.kind == "category_penalty" and r.payload.get("category") == "d"
        ]
        assert releases == []  # d 仍越线:绝不释放

    def test_release_row_carries_real_window_counts(self, store):
        """释放行审计计数 = 窗口真实计数(曾固定伪造 bad_count=0/total=0)。"""
        from datetime import datetime, timezone, timedelta

        from shishi.feedback import FeedbackTuner, TuningPolicy

        day = timedelta(days=1)
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        tuner = FeedbackTuner(TuningPolicy(min_bad_count=2, min_bad_ratio=0.5))
        # 第 1 轮:降权(2 bad / 2 total)
        for i in range(2):
            self._seed(store, category="e", title=f"折扣 e{i}", verdict="bad", at=now)
        tuner.apply(store, now=now)

        # 第 2 轮(窗口内):又来 3 good → e 变 2 bad / 5 total(0.4 < 0.5)→ 释放
        for i in range(3):
            self._seed(store, category="e", title=f"折扣 good{i}", verdict="good", at=now + day)
        written = tuner.apply(store, now=now + day)

        release = [
            r for r in written
            if r.kind == "category_penalty" and r.payload.get("category") == "e"
        ]
        assert len(release) == 1
        assert release[0].payload["released"] is True
        assert release[0].payload["bad_count"] == 2  # 真实计数,不是伪造的 0
        assert release[0].payload["total"] == 5
