"""Tests for storage hardening (PRD 10-01-v02-storage-hardening).

Covers:

- schema 版本校验: fresh stamping / legacy v0.1 migration (数据保留) /
  newer-version refusal / corrupt-file refusal (structured, chained cause);
- runs.steps 断点续跑: step accumulation + payload round-trip, previous_run
  anchor, pipeline checkpoint recording, kill-after-checkpoint resume
  (handler not re-called, logs show the skip), same-slot duplicate-push
  suppression, cross-slot refusal, corrupt-checkpoint fallback;
- retention cleanup: pushed vs never-pushed windows (90d / 2×90d), stale
  baseline+hint pruning via active URLs, old-run removal, :memory: and
  tmp-file databases;
- storage.vacuum: monthly fires once per month, weekly/daily/never, unknown
  cadence refused, file rebuild keeps data;
- maintenance rides the run cycle (随调度周期执行);
- storage 节缺省值 (90d / monthly).

All clocks are injected (wall_clock pinned datetimes, FakeClock waits) — no
real waiting, no real network (httpx.MockTransport only).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from conftest import FakeClock

import myia.pipeline as pipeline_module
from myia.dedup import DedupRegistry
from myia.pipeline import (
    RESUME_CHECKPOINT_STAGES,
    Item,
    Pipeline,
)
from myia.schema import StorageConfig, load_category
from myia.store import (
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    SCHEMA_VERSION,
    ItemRecord,
    SQLiteStore,
    StoreSchemaError,
)

# Fixed +08:00 offset: deterministic slot math regardless of machine TZ.
TIMEZONE = timezone(timedelta(hours=8))
#: 2026-10-01 14:00 local (+08) — inside the PM slot window (starts 12:00).
PM_NOW = datetime(2026, 10, 1, 14, 0, tzinfo=TIMEZONE)
PM_WINDOW_START = PM_NOW.replace(hour=12)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def make_config(**overrides: Any):
    """A minimal valid CategoryConfig (schema-validated) with overrides."""
    data: dict[str, Any] = {
        "id": "demo",
        "name": "演示品类",
        "schedule": "0 9 * * *",
        "timezone": "Asia/Shanghai",
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
        "push": [{"channel": "stdout"}],
    }
    data.update(overrides)
    return load_category(data)


def list_payload(titles_and_urls: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"title": title, "url": url} for title, url in titles_and_urls]


def make_handler(counter: dict[str, int], payload: Any):
    """MockTransport handler counting non-robots hits (断点续跑不重复抓取的证词)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        counter["n"] += 1
        return httpx.Response(200, json=payload)

    return handler


def make_pipeline(
    config,
    *,
    handler: Any,
    store: SQLiteStore,
    wall_clock: Any = lambda: PM_NOW,
    **kwargs: Any,
) -> Pipeline:
    """Pipeline on an injected mock client + pinned wall clock (零真实等待)."""
    clock = FakeClock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Pipeline(
        config,
        store=store,
        client=client,
        clock=clock.time,
        sleep=clock.sleep,
        wall_clock=wall_clock,
        **kwargs,
    )


def resume_items() -> list[Item]:
    """Two classified+deduped items as a dedup checkpoint would carry them."""
    return [
        Item(
            url="https://api.demo.local/a",
            title="免费送 NAS 券",
            source="api",
            category="freebie",
            dedup_key="https://api.demo.local/a",
            metadata={},
        ),
        Item(
            url="https://api.demo.local/b",
            title="白嫖机场体验",
            source="api",
            category="freebie",
            dedup_key="https://api.demo.local/b",
            metadata={},
        ),
    ]


def craft_interrupted_run(
    store: SQLiteStore,
    *,
    payload: dict[str, Any],
    started_at: datetime = PM_NOW - timedelta(hours=1),
    finish_status: str | None = None,
) -> int:
    """Simulate a killed run: a run row with a dedup checkpoint, no finish.

    ``finish_status`` optionally finishes it (infra-abort style) while keeping
    the steps — the "failed run" resume anchor.
    """
    run_id = store.start_run("demo")
    store.update_run_step(run_id, "dedup", status="ok", payload=payload)
    store.conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?", (iso(started_at), run_id)
    )
    store.conn.commit()
    if finish_status is not None:
        store.finish_run(run_id, status=finish_status, error="运行中断(基础设施异常)")
    return run_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["file", "memory"])
def db_store(request, tmp_path):
    """Store over a temp-file DB or an in-memory DB (验收两种形态都覆盖)."""
    store = (
        SQLiteStore(":memory:")
        if request.param == "memory"
        else SQLiteStore(tmp_path / "hardening.db")
    )
    yield store
    store.close()


@pytest.fixture()
def store(tmp_path):
    store = SQLiteStore(tmp_path / "hardening.db")
    yield store
    store.close()


@pytest.fixture()
def registry(store):
    return DedupRegistry(store, tz=TIMEZONE)


# ---------------------------------------------------------------------------
# Schema versioning & migration
# ---------------------------------------------------------------------------


def test_fresh_db_stamps_schema_version_and_steps_column(db_store):
    assert int(db_store.get_meta("schema_version")) == SCHEMA_VERSION
    columns = {row[1] for row in db_store.conn.execute("PRAGMA table_info(runs)")}
    assert "steps" in columns
    assert "store_meta" in {
        row[0]
        for row in db_store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_legacy_v01_db_migrates_and_preserves_rows(tmp_path):
    """v0.1 库(无 steps/store_meta)打开即迁移,既有数据保留。"""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
            dedup_key TEXT NOT NULL, source TEXT, title TEXT NOT NULL,
            content TEXT, content_hash TEXT, tags TEXT, category TEXT,
            scores TEXT, pushed_at TEXT, push_slot TEXT, first_seen TEXT NOT NULL,
            raw TEXT
        );
        CREATE TABLE dedup_registry (
            key TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
            last_pushed_at TEXT, last_push_slot TEXT
        );
        CREATE TABLE change_baseline (
            url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT,
            content_hash TEXT, last_changed TEXT
        );
        CREATE TABLE engine_hints (
            source_key TEXT PRIMARY KEY, engine TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            stats TEXT, error TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO runs (category, started_at, status) VALUES ('demo', ?, 'success')",
        (iso(PM_NOW - timedelta(days=1)),),
    )
    conn.execute(
        "INSERT INTO items (url, dedup_key, title, first_seen) VALUES (?, ?, ?, ?)",
        ("https://old.example.com/a", "https://old.example.com/a", "旧条目", iso(PM_NOW)),
    )
    conn.commit()
    conn.close()

    migrated = SQLiteStore(path)
    try:
        assert int(migrated.get_meta("schema_version")) == SCHEMA_VERSION
        columns = {row[1] for row in migrated.conn.execute("PRAGMA table_info(runs)")}
        assert "steps" in columns
        legacy_run = migrated.get_run(1)
        assert legacy_run is not None and legacy_run.category == "demo"
        assert [item.title for item in migrated.list_items()] == ["旧条目"]
    finally:
        migrated.close()


def test_newer_schema_version_refused_with_structured_error(store):
    store.set_meta("schema_version", str(SCHEMA_VERSION + 1))
    store.close()
    with pytest.raises(StoreSchemaError) as excinfo:
        SQLiteStore(store.sqlite_path)
    assert excinfo.value.code == "schema_version_newer"
    assert excinfo.value.details["found"] == SCHEMA_VERSION + 1
    assert excinfo.value.details["expected"] == SCHEMA_VERSION
    assert excinfo.value.to_dict()["error_type"] == "schema_version_newer"


def test_corrupt_db_file_refused_with_chained_cause(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 8)
    with pytest.raises(StoreSchemaError) as excinfo:
        SQLiteStore(path)
    assert excinfo.value.code == "store_corrupt"
    assert isinstance(excinfo.value.__cause__, sqlite3.DatabaseError)


# ---------------------------------------------------------------------------
# runs.steps (断点续跑地基)
# ---------------------------------------------------------------------------


def test_update_run_step_accumulates_and_round_trips_payload(store):
    run_id = store.start_run("demo")
    payload = {"items": [{"url": "https://api.demo.local/a", "title": "标题", "tags": ["羊毛"]}]}
    store.update_run_step(run_id, "fetch", status="ok", payload=payload)
    store.update_run_step(run_id, "classify", status="ok")
    record = store.get_run(run_id)
    assert record is not None and record.steps is not None
    assert record.steps["fetch"]["status"] == "ok"
    assert record.steps["fetch"]["payload"] == payload  # unicode 完整往返
    assert record.steps["classify"]["status"] == "ok"
    assert "completed_at" in record.steps["fetch"]


def test_update_run_step_preserves_earlier_entries(store):
    run_id = store.start_run("demo")
    store.update_run_step(run_id, "fetch", status="ok", payload={"items": [1]})
    store.update_run_step(run_id, "fetch", status="failed")  # 同一步骤覆盖
    record = store.get_run(run_id)
    assert record is not None
    assert record.steps["fetch"]["status"] == "failed"
    assert "payload" not in record.steps["fetch"]


def test_update_run_step_rejects_bad_input(store):
    run_id = store.start_run("demo")
    with pytest.raises(ValueError, match="步骤状态"):
        store.update_run_step(run_id, "fetch", status="ok-ish")
    with pytest.raises(ValueError, match="不能为空"):
        store.update_run_step(run_id, "", status="ok")
    with pytest.raises(ValueError, match="payload"):
        store.update_run_step(run_id, "fetch", status="ok", payload=["not-a-mapping"])
    with pytest.raises(ValueError, match="不存在"):
        store.update_run_step(999, "fetch", status="ok")


def test_previous_run_returns_latest_run_before_id(store):
    first = store.start_run("demo")
    store.finish_run(first, status=RUN_STATUS_SUCCESS)
    second = store.start_run("demo")
    assert store.previous_run("demo", before_run_id=second).id == first
    assert store.previous_run("demo", before_run_id=first) is None
    other = store.start_run("other")
    assert store.previous_run("other", before_run_id=other + 1).id == other


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


def save_item(store, **overrides) -> int:
    base: dict[str, Any] = {
        "url": "https://api.demo.local/a",
        "dedup_key": "https://api.demo.local/a",
        "title": "公开示例标题",
    }
    base.update(overrides)
    return store.save_item(ItemRecord(**base))


def test_cleanup_pushed_vs_unpushed_distinct_windows(db_store):
    """已推送按 pushed_at+retention 清理;未推送按 first_seen+2×retention。"""
    now = PM_NOW
    pushed_old = save_item(db_store, dedup_key="p-old")
    db_store.mark_item_pushed(pushed_old, "am", pushed_at=now - timedelta(days=100))
    save_item(  # pushed recent — kept
        db_store, url="https://api.demo.local/b", dedup_key="p-new"
    )
    unpushed_medium = save_item(  # 122d 未推送 < 180d 窗口 — kept
        db_store, url="https://api.demo.local/c", dedup_key="u-mid",
        first_seen=now - timedelta(days=122),
    )
    save_item(  # 273d 未推送 > 180d 窗口 — dropped
        db_store, url="https://api.demo.local/d", dedup_key="u-old",
        first_seen=now - timedelta(days=273),
    )
    counts = db_store.cleanup_expired(90, now=now)
    assert counts["items_pushed"] == 1
    assert counts["items_unpushed"] == 1
    assert db_store.get_item(pushed_old) is None
    assert db_store.get_item(unpushed_medium) is not None
    keys = {item.dedup_key for item in db_store.list_items()}
    assert keys == {"p-new", "u-mid"}


def test_cleanup_prunes_stale_baselines_and_hints_via_active_urls(db_store):
    db_store.set_baseline("https://api.demo.local/list", etag='"active"')
    db_store.set_baseline("https://gone.example.com/feed", etag='"stale"')
    db_store.set_engine_hint("https://api.demo.local/list", "direct_api")
    db_store.set_engine_hint("https://gone.example.com/feed", "static_html")
    counts = db_store.cleanup_expired(90, now=PM_NOW, active_urls={"https://api.demo.local/list"})
    assert counts["baselines"] == 1
    assert counts["engine_hints"] == 1
    assert db_store.get_baseline("https://api.demo.local/list").etag == '"active"'
    assert db_store.get_baseline("https://gone.example.com/feed") is None
    assert db_store.get_engine_hint("https://gone.example.com/feed") is None


def test_cleanup_without_active_urls_leaves_url_caches_alone(db_store):
    """共享库安全缺省:无 active_urls 时 baseline/hint 一律保留。

    跨品类行不可删(同一 myia.db 服务所有品类);last_changed 只在内容真变时
    刷新,按龄删还会误杀长期未变的安静源(指纹 skip 被毁)。"""
    db_store.set_baseline(
        "https://gone.example.com/feed", etag="e", last_changed=PM_NOW - timedelta(days=200)
    )
    db_store.set_baseline("https://fresh.example.com/feed", etag="e")  # 无年龄 → 保守保留
    db_store.set_engine_hint("https://gone.example.com/feed", "static_html")
    counts = db_store.cleanup_expired(90, now=PM_NOW)
    assert counts["baselines"] == 0
    assert counts["engine_hints"] == 0
    assert db_store.get_baseline("https://gone.example.com/feed") is not None
    assert db_store.get_baseline("https://fresh.example.com/feed") is not None
    assert db_store.get_engine_hint("https://gone.example.com/feed") == "static_html"


def test_cleanup_removes_old_finished_and_stale_running_runs(db_store):
    old_finished = db_store.start_run("demo")
    db_store.finish_run(old_finished, status=RUN_STATUS_SUCCESS)
    db_store.conn.execute(
        "UPDATE runs SET started_at = ?, finished_at = ? WHERE id = ?",
        (
            iso(PM_NOW - timedelta(days=200)),
            iso(PM_NOW - timedelta(days=200)),
            old_finished,
        ),
    )
    stale_running = db_store.start_run("demo")  # 崩溃残留:running 挂了很久
    db_store.conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?",
        (iso(PM_NOW - timedelta(days=200)), stale_running),
    )
    fresh_running = db_store.start_run("demo")
    db_store.conn.commit()
    counts = db_store.cleanup_expired(90, now=PM_NOW)
    assert counts["runs"] == 2
    assert db_store.get_run(old_finished) is None
    assert db_store.get_run(stale_running) is None
    assert db_store.get_run(fresh_running) is not None


def test_cleanup_rejects_nonpositive_retention(db_store):
    with pytest.raises(ValueError, match="retention_days"):
        db_store.cleanup_expired(0, now=PM_NOW)
    with pytest.raises(ValueError, match="retention_days"):
        db_store.cleanup_expired(-5, now=PM_NOW)


# ---------------------------------------------------------------------------
# Vacuum
# ---------------------------------------------------------------------------


def test_maybe_vacuum_monthly_fires_once_per_calendar_month(db_store):
    assert db_store.maybe_vacuum("monthly", now=datetime(2026, 1, 15, tzinfo=timezone.utc)) is True
    assert db_store.get_meta("last_vacuum_at") is not None
    assert db_store.maybe_vacuum("monthly", now=datetime(2026, 1, 28, tzinfo=timezone.utc)) is False
    assert db_store.maybe_vacuum("monthly", now=datetime(2026, 2, 1, tzinfo=timezone.utc)) is True


def test_maybe_vacuum_weekly_daily_and_never(db_store):
    jan4 = datetime(2026, 1, 4, tzinfo=timezone.utc)  # ISO week 1, Sunday
    jan8 = datetime(2026, 1, 8, tzinfo=timezone.utc)  # ISO week 2
    assert db_store.maybe_vacuum("never", now=jan4) is False
    assert db_store.get_meta("last_vacuum_at") is None  # never 不盖章
    assert db_store.maybe_vacuum("weekly", now=jan4) is True
    assert db_store.maybe_vacuum("weekly", now=jan8) is True  # 新 ISO 周
    assert db_store.maybe_vacuum("weekly", now=jan8 + timedelta(days=1)) is False
    assert db_store.maybe_vacuum("daily", now=jan8 + timedelta(days=2)) is True
    assert db_store.maybe_vacuum("daily", now=jan8 + timedelta(days=2)) is False


def test_maybe_vacuum_rejects_unknown_cadence(db_store):
    with pytest.raises(ValueError, match="vacuum"):
        db_store.maybe_vacuum("hourly", now=PM_NOW)


def test_vacuum_rebuilds_file_and_keeps_data(tmp_path):
    path = tmp_path / "vacuum.db"
    store = SQLiteStore(path)
    run_id = store.start_run("demo")
    store.update_run_step(run_id, "fetch", status="ok", payload={"items": [{"url": "u"}]})
    store.conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))  # 制造空闲页
    store.conn.commit()
    store.vacuum()
    assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store.close()
    reopened = SQLiteStore(path)
    try:
        assert int(reopened.get_meta("schema_version")) == SCHEMA_VERSION
        assert reopened.latest_run() is None
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# Pipeline: checkpoint recording & 断点续跑
# ---------------------------------------------------------------------------


def test_resume_across_restart_on_file_db(tmp_path, monkeypatch):
    """kill 后重跑(进程重启语义):新 Pipeline 自己打开同一库文件并续跑。"""
    sent: list[list[str]] = []

    class Capturing(_CapturingChannel):
        async def send(self, items, context) -> None:
            await super().send(items, context)
            sent.append([getattr(item, "title", "") for item in items])

    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": Capturing})
    db_path = tmp_path / "restart.db"
    crash_store = SQLiteStore(db_path)
    craft_interrupted_run(
        crash_store, payload=pipeline_module._checkpoint_payload(resume_items())
    )
    crash_store.close()  # 「进程」带着未收尾的 run 消失

    config = make_config()
    pipeline = Pipeline(config, db_path=db_path, wall_clock=lambda: PM_NOW)  # 无注入:自开库
    try:
        result = asyncio.run(pipeline.run())
        assert result.resumed_from_run_id is not None
        assert result.sources == []  # 采集步骤被跳过,零网络请求
        assert [t for batch in sent for t in batch] == ["免费送 NAS 券", "白嫖机场体验"]
    finally:
        pipeline.close()
        reopened = SQLiteStore(db_path)
        adopted = reopened.get_run(result.resumed_from_run_id)
        assert adopted is not None and adopted.status == RUN_STATUS_FAILED
        reopened.close()


def test_run_records_step_checkpoints(tmp_path, monkeypatch):
    """每次 run 把已完成步骤(含条目载荷)写进 runs.steps。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "cp.db")
    config = make_config()
    counter = {"n": 0}
    handler = make_handler(
        counter,
        list_payload(
            [
                ("免费送 NAS 券", "https://api.demo.local/a"),
                ("白嫖机场体验", "https://api.demo.local/b"),
                ("广告贴不要点", "https://api.demo.local/c"),
            ]
        ),
    )
    pipeline = make_pipeline(config, handler=handler, store=store)
    result = asyncio.run(pipeline.run())

    record = store.get_run(result.run_id)
    assert record is not None and record.steps is not None
    assert {name: record.steps[name]["status"] for name in record.steps} == {
        "fetch": "ok",
        "classify": "ok",
        "dedup": "ok",
        "analyze": "ok",
        "push": "ok",
    }
    assert [i["url"] for i in record.steps["fetch"]["payload"]["items"]] == [
        "https://api.demo.local/a",
        "https://api.demo.local/b",
        "https://api.demo.local/c",
    ]
    assert len(record.steps["classify"]["payload"]["items"]) == 2  # 广告贴被分类丢弃
    dedup_payload = record.steps["dedup"]["payload"]["items"]
    assert all(item["dedup_key"] for item in dedup_payload)
    assert "payload" not in record.steps["push"]  # 非检查点步骤不带载荷
    assert result.maintenance is not None  # 清理任务随 run 执行
    store.close()


def test_resume_after_kill_skips_completed_stages(tmp_path, monkeypatch, caplog):
    """kill 后重跑:dedup 检查点命中 → 不重复抓取,条目照常推送,日志可见跳过。"""
    sent: list[list[str]] = []

    class Capturing(_CapturingChannel):
        async def send(self, items, context) -> None:
            await super().send(items, context)
            sent.append([getattr(item, "title", "") for item in items])

    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": Capturing})
    store = SQLiteStore(tmp_path / "resume.db")
    payload = pipeline_module._checkpoint_payload(resume_items())
    crash_run = craft_interrupted_run(store, payload=payload)

    counter = {"n": 0}
    handler = make_handler(counter, list_payload([("不该被抓取", "https://api.demo.local/x")]))
    config = make_config()
    pipeline = make_pipeline(config, handler=handler, store=store)
    with caplog.at_level(logging.INFO, logger="myia.pipeline"):
        result = asyncio.run(pipeline.run())

    assert result.resumed_from_run_id == crash_run
    assert counter["n"] == 0  # 零重复抓取
    for name in ("fetch", "classify", "dedup"):
        stage = result.stage(name)
        assert stage.status == "skipped"
        assert stage.skip_reason == "resumed"
    assert [title for batch in sent for title in batch] == ["免费送 NAS 券", "白嫖机场体验"]
    assert result.status == "success"
    assert "断点续跑" in caplog.text  # 日志可见跳过
    # 被接管的中断 run 落终态,错误信息结构化注明
    adopted = store.get_run(crash_run)
    assert adopted is not None and adopted.status == RUN_STATUS_FAILED
    assert "接管" in adopted.error
    # 本 run 自身的 steps 继承检查点(再次崩溃仍可续)
    record = store.get_run(result.run_id)
    assert record is not None and record.steps is not None
    assert record.steps["dedup"]["status"] == "resumed"
    assert record.steps["dedup"]["payload"] == payload
    assert record.steps["push"]["status"] == "ok"
    store.close()


def test_resume_same_slot_records_no_duplicate_push(tmp_path, monkeypatch):
    """检查点重放受同槽位拦截兜底:已推送过的条目不再发第二遍。"""
    sent: list[list[str]] = []

    class Capturing(_CapturingChannel):
        async def send(self, items, context) -> None:
            await super().send(items, context)
            sent.append([getattr(item, "title", "") for item in items])

    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": Capturing})
    store = SQLiteStore(tmp_path / "dup.db")
    registry = DedupRegistry(store, tz=TIMEZONE)
    for item in resume_items():
        registry.record_push(item.dedup_key, now=PM_NOW)  # 同槽位已发过
    craft_interrupted_run(store, payload=pipeline_module._checkpoint_payload(resume_items()))

    counter = {"n": 0}
    handler = make_handler(counter, [])
    pipeline = make_pipeline(make_config(), handler=handler, store=store)
    result = asyncio.run(pipeline.run())

    assert sent == []  # 无重复推送
    assert result.stage("push").skips["slot_suppressed"] == 2
    assert result.pushes[0].reports == []
    assert result.status == "success"
    store.close()


def test_resume_refused_across_slot_window(tmp_path, monkeypatch):
    """上次运行早于当前槽位窗口:不续跑(防跨槽位重放已推送条目),全新运行。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "crossslot.db")
    craft_interrupted_run(
        store,
        payload=pipeline_module._checkpoint_payload(resume_items()),
        started_at=PM_NOW - timedelta(days=1),  # 昨天崩的,早已跨槽
    )
    counter = {"n": 0}
    handler = make_handler(counter, list_payload([("新条目", "https://api.demo.local/n")]))
    pipeline = make_pipeline(make_config(), handler=handler, store=store)
    result = asyncio.run(pipeline.run())

    assert result.resumed_from_run_id is None
    assert counter["n"] == 1  # 正常发起抓取
    assert result.stage("fetch").status == "ok"
    store.close()


def test_resume_from_failed_run_with_checkpoint(tmp_path, monkeypatch):
    """失败收尾的 run(基础设施中断)若留有检查点,同样可续。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "failed.db")
    crash_run = craft_interrupted_run(
        store,
        payload=pipeline_module._checkpoint_payload(resume_items()),
        finish_status=RUN_STATUS_FAILED,
    )
    counter = {"n": 0}
    handler = make_handler(counter, [])
    pipeline = make_pipeline(make_config(), handler=handler, store=store)
    result = asyncio.run(pipeline.run())
    assert result.resumed_from_run_id == crash_run
    assert counter["n"] == 0
    store.close()


def test_no_resume_after_success_or_partial(tmp_path, monkeypatch):
    """上一 run 成功/部分成功收尾:不续跑(partial 的业务由进程内摘要留池重试)。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "nopartial.db")
    config = make_config()
    payload = list_payload([("免费送 NAS 券", "https://api.demo.local/a")])

    counter = {"n": 0}
    ok_pipeline = make_pipeline(config, handler=make_handler(counter, payload), store=store)
    first = asyncio.run(ok_pipeline.run())
    assert first.status == "success"

    def dead_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        counter["n"] += 1
        return httpx.Response(500, text="boom")

    second = make_pipeline(config, handler=dead_handler, store=store)
    result = asyncio.run(second.run())
    assert result.resumed_from_run_id is None  # success 前任不续跑
    assert counter["n"] >= 1  # 照常尝试抓取
    store.close()


def test_corrupt_checkpoint_falls_back_to_fresh_run(tmp_path, monkeypatch, caplog):
    """检查点损坏(items 非列表):告警后按全新运行处理,绝不因续跑炸 run。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "corruptcp.db")
    craft_interrupted_run(store, payload={"items": "not-a-list"})
    counter = {"n": 0}
    handler = make_handler(counter, list_payload([("免费送 NAS 券", "https://api.demo.local/a")]))
    pipeline = make_pipeline(make_config(), handler=handler, store=store)
    with caplog.at_level(logging.WARNING, logger="myia.pipeline"):
        result = asyncio.run(pipeline.run())
    assert result.resumed_from_run_id is None
    assert counter["n"] == 1
    assert result.status == "success"
    assert "检查点损坏" in caplog.text
    store.close()


# ---------------------------------------------------------------------------
# Maintenance rides the run cycle
# ---------------------------------------------------------------------------


def test_run_maintenance_cleans_and_vacuums(tmp_path):
    """run_maintenance:retention 计数 + VACUUM 盖章;URL 缓存不受单品类的
    active_urls 修剪(共享库多品类安全,见 test_run_maintenance_preserves_other_categories)。"""
    store = SQLiteStore(tmp_path / "maint.db")
    pushed_old = save_item(store, dedup_key="p-old")
    store.mark_item_pushed(pushed_old, "am", pushed_at=PM_NOW - timedelta(days=100))
    save_item(store, url="https://api.demo.local/keep", dedup_key="keep")
    store.set_baseline("https://gone.example.com/feed", etag="stale")
    store.set_engine_hint("https://gone.example.com/feed", "static_html")
    old_run = store.start_run("demo")
    store.finish_run(old_run, status=RUN_STATUS_SUCCESS)
    store.conn.execute(
        "UPDATE runs SET started_at = ?, finished_at = ? WHERE id = ?",
        (iso(PM_NOW - timedelta(days=200)), iso(PM_NOW - timedelta(days=200)), old_run),
    )
    store.conn.commit()

    config = make_config()
    pipeline = make_pipeline(config, handler=make_handler({"n": 0}, []), store=store)
    report = asyncio.run(pipeline.run_maintenance())

    assert report["deleted"] == {
        "items_pushed": 1,
        "items_unpushed": 0,
        "baselines": 0,
        "engine_hints": 0,
        "runs": 1,
        "enrich_cache": 0,
        "metrics": 0,  # v0.4 metric_history 老化计数(2× retention 窗口,此处无旧行)
    }
    assert report["vacuumed"] is True  # monthly 首次必触发
    assert store.get_item(pushed_old) is None
    assert store.get_baseline("https://gone.example.com/feed") is not None
    assert store.get_run(old_run) is None

    again = asyncio.run(pipeline.run_maintenance())
    assert again["vacuumed"] is False  # 当月已 VACUUM 过
    store.close()


def test_run_maintenance_preserves_other_categories_in_shared_store(tmp_path):
    """回归(2026-10 复盘 high):同一 myia.db 服务所有品类,A 品类的
    run_maintenance 不得把 B 品类的 baseline/engine_hint 当「过期源」删掉
    ——否则其它品类每轮全量重抓 + 引擎全链重探,两套核心缓存机制永久失效。"""
    store = SQLiteStore(tmp_path / "shared.db")
    # 品类 B(另一个 YAML)写入的缓存行
    store.set_baseline("https://news.example/rss", etag='"b"')
    store.set_engine_hint("https://news.example/rss", "crawl4ai")
    # 品类 A 自己已从 YAML 移除的源行(单品类视角同样无权删)
    store.set_baseline("https://gone.example.com/feed", etag='"stale"')
    store.set_engine_hint("https://gone.example.com/feed", "static_html")

    pipeline = make_pipeline(make_config(), handler=make_handler({"n": 0}, []), store=store)
    report = asyncio.run(pipeline.run_maintenance())

    assert report["deleted"]["baselines"] == 0
    assert report["deleted"]["engine_hints"] == 0
    assert store.get_baseline("https://news.example/rss").etag == '"b"'
    assert store.get_engine_hint("https://news.example/rss") == "crawl4ai"
    assert store.get_baseline("https://gone.example.com/feed") is not None
    assert store.get_engine_hint("https://gone.example.com/feed") == "static_html"
    store.close()


def test_run_maintenance_respects_never_cadence(tmp_path):
    store = SQLiteStore(tmp_path / "never.db")
    config = make_config(storage={"retention": "2w", "vacuum": "never"})
    pipeline = make_pipeline(config, handler=make_handler({"n": 0}, []), store=store)
    report = asyncio.run(pipeline.run_maintenance())
    assert report["vacuumed"] is False
    assert store.get_meta("last_vacuum_at") is None
    assert report["deleted"]["items_pushed"] == 0  # 2w 窗口内无过期条目
    store.close()


def test_run_end_triggers_maintenance(tmp_path, monkeypatch):
    """每次 run 收尾自动执行清理(随调度周期,无需独立任务)。"""
    monkeypatch.setattr(pipeline_module, "CHANNELS", {"stdout": _CapturingChannel})
    store = SQLiteStore(tmp_path / "ride.db")
    pushed_old = save_item(store, dedup_key="ancient")
    store.mark_item_pushed(pushed_old, "am", pushed_at=PM_NOW - timedelta(days=100))
    config = make_config()
    pipeline = make_pipeline(
        config,
        handler=make_handler(
            {"n": 0}, list_payload([("免费送 NAS 券", "https://api.demo.local/a")])
        ),
        store=store,
    )
    result = asyncio.run(pipeline.run())

    assert result.maintenance is not None
    assert result.maintenance["deleted"]["items_pushed"] == 1
    assert store.get_item(pushed_old) is None
    assert result.maintenance["vacuumed"] is True
    store.close()


# ---------------------------------------------------------------------------
# Schema defaults (验收:storage 节缺省值)
# ---------------------------------------------------------------------------


def test_storage_section_defaults_90d_monthly():
    cfg = StorageConfig()
    assert cfg.retention == "90d"
    assert cfg.retention_days == 90
    assert cfg.vacuum == "monthly"


def test_storage_section_accepts_weeks_and_cadences():
    data = make_config(storage={"retention": "2w", "vacuum": "weekly"})
    assert data.storage.retention_days == 14
    assert data.storage.vacuum == "weekly"


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class _CapturingChannel:
    """stdout 通道替身:记录 send 调用,不产生输出。"""

    name = "stdout"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def send(self, items, context) -> None:  # noqa: ANN001 - 测试替身
        return None


# sanity: checkpoint stage vocabulary stays in sync with EXECUTED_STAGES
def test_checkpoint_stages_are_pipeline_prefix():
    assert RESUME_CHECKPOINT_STAGES == ("fetch", "classify", "dedup")


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:enrich_cache 生命周期 + dedup_registry 手动治理
# ---------------------------------------------------------------------------


def test_cleanup_ages_enrich_cache(db_store):
    """enrich_cache 随 retention 老化:过期行删除,新鲜行保留(纯缓存,重评即可)。"""
    db_store.set_enrich_cache(
        "https://old.example/a", "glm-4-flash", "k1", {"scores": {"v": 1}, "score": 1.0},
        created_at=PM_NOW - timedelta(days=200),
    )
    db_store.set_enrich_cache(
        "https://fresh.example/b", "glm-4-flash", "k1", {"scores": {"v": 9}, "score": 9.0},
        created_at=PM_NOW - timedelta(days=1),
    )
    counts = db_store.cleanup_expired(90, now=PM_NOW)
    assert counts["enrich_cache"] == 1
    assert db_store.get_enrich_cache("https://old.example/a", "glm-4-flash", "k1") is None
    assert db_store.get_enrich_cache("https://fresh.example/b", "glm-4-flash", "k1") is not None


def test_cleanup_never_touches_dedup_registry(db_store):
    """dedup_registry 是 all-time dedup + 防重发底座:cleanup_expired 不自动删行。"""
    db_store.mark_dedup_seen("https://old.example/x", PM_NOW - timedelta(days=400))
    db_store.record_dedup_push("https://old.example/y", PM_NOW - timedelta(days=400), "am")
    counts = db_store.cleanup_expired(90, now=PM_NOW)
    assert db_store.get_dedup_entry("https://old.example/x") is not None
    assert db_store.get_dedup_entry("https://old.example/y") is not None
    assert "dedup_registry" not in counts


def test_prune_dedup_registry_manual_channel(db_store):
    """手动治理通道:仅删锚点(COALESCE(last_pushed_at, first_seen))早于界值的行。"""
    db_store.mark_dedup_seen("https://a.example/1", PM_NOW - timedelta(days=10))
    db_store.record_dedup_push("https://a.example/2", PM_NOW - timedelta(days=10), "am")
    db_store.mark_dedup_seen("https://a.example/3", PM_NOW - timedelta(days=1))
    db_store.record_dedup_push("https://a.example/4", PM_NOW - timedelta(days=1), "pm")

    deleted = db_store.prune_dedup_registry(before=PM_NOW - timedelta(days=7))

    assert deleted == 2
    assert db_store.get_dedup_entry("https://a.example/1") is None
    assert db_store.get_dedup_entry("https://a.example/2") is None
    assert db_store.get_dedup_entry("https://a.example/3") is not None
    assert db_store.get_dedup_entry("https://a.example/4") is not None
