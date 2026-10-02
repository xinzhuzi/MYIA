"""Tests for the SQLite store layer and the dedup registry (PRD 10-01-v01-store-dedup).

Covers: same-slot suppression / cross-slot release / the local-time 12:00
boundary / composite dedup keys (title fingerprint ban) / change-baseline
read-write / engine hints / items & runs round-trips / WAL concurrent writes.

Time-dependent behavior uses injected clocks (`now=`) with a fixed-offset
timezone — no freezegun needed, tests stay deterministic on any machine.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from myia.dedup import DedupRegistry
from myia.store import (
    PUSH_SLOTS,
    SLOT_AM,
    SLOT_PM,
    ChangeBaseline,
    ItemRecord,
    SQLiteStore,
    Store,
)

# Fixed +08:00 offset: deterministic slot math regardless of machine TZ.
TIMEZONE = timezone(timedelta(hours=8))
NOW_UTC = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


def local_dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    """A fixed local datetime in TIMEZONE (2026-10-DD by default)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TIMEZONE)


def make_item(**overrides) -> ItemRecord:
    base = dict(
        url="https://example.com/a",
        dedup_key="https://example.com/a",
        title="公开论坛示例标题",
        source="example",
        content="脱敏摘要文本",
        content_hash="deadbeef",
        tags=["freebie"],
        category="freebie",
        scores={"total": 7.5},
        raw={"extra": 1},
    )
    base.update(overrides)
    return ItemRecord(**base)


@pytest.fixture()
def store(tmp_path):
    s = SQLiteStore(tmp_path / "myia.db")
    yield s
    s.close()


@pytest.fixture()
def registry(store):
    return DedupRegistry(store, tz=TIMEZONE)


# ----------------------------------------------------------------- slot semantics


def test_slot_of_noon_boundary_am_and_pm(registry):
    assert registry.slot_of(local_dt(0, 0)) == SLOT_AM
    assert registry.slot_of(local_dt(11, 59)) == SLOT_AM
    assert registry.slot_of(local_dt(12, 0)) == SLOT_PM
    assert registry.slot_of(local_dt(23, 59)) == SLOT_PM


def test_dedup_same_slot_blocks_resend(registry):
    registry.record_push("AAPL-2026-10-01", now=local_dt(13))
    assert registry.should_send("AAPL-2026-10-01", now=local_dt(14)) is False


def test_dedup_cross_slot_allows_resend(registry):
    registry.record_push("k", now=local_dt(9))  # AM
    assert registry.should_send("k", now=local_dt(14)) is True  # PM, same day


def test_dedup_noon_boundary_releases_key(registry):
    registry.record_push("k", now=local_dt(11, 59))  # last minute of AM
    assert registry.should_send("k", now=local_dt(12, 0)) is True  # first minute of PM


def test_dedup_pm_window_blocks_until_day_end(registry):
    registry.record_push("k", now=local_dt(12, 0))
    assert registry.should_send("k", now=local_dt(23, 59)) is False


def test_dedup_next_day_same_slot_allows_resend(registry):
    registry.record_push("k", now=local_dt(13, day=1))
    assert registry.should_send("k", now=local_dt(13, day=2)) is True


def test_dedup_unseen_key_allows_send(registry):
    assert registry.should_send("never-pushed") is True


def test_dedup_entry_records_first_seen_and_slot(registry):
    registry.record_push("k", now=local_dt(13))
    entry = registry.get_entry("k")
    assert entry is not None
    assert entry.first_seen == local_dt(13)
    assert entry.last_pushed_at == local_dt(13)
    assert entry.last_push_slot == SLOT_PM


def test_record_push_preserves_earlier_first_seen(registry):
    registry.mark_seen("k", now=local_dt(8))
    registry.record_push("k", now=local_dt(13))
    entry = registry.get_entry("k")
    assert entry.first_seen == local_dt(8)
    assert entry.last_pushed_at == local_dt(13)


def test_dedup_is_seen_and_mark_seen(registry):
    assert registry.is_seen("k") is False
    registry.mark_seen("k", now=local_dt(9))
    assert registry.is_seen("k") is True


def test_digest_and_immediate_share_one_registry(store):
    immediate = DedupRegistry(store, tz=TIMEZONE)
    digest = DedupRegistry(store, tz=TIMEZONE)
    immediate.record_push("k", now=local_dt(13))
    assert digest.should_send("k", now=local_dt(14)) is False


def test_slot_of_defaults_to_system_local_timezone(store):
    registry = DedupRegistry(store)  # no tz injected: system local zone
    assert registry.slot_of(datetime.now(timezone.utc)) in PUSH_SLOTS


# -------------------------------------------------------------- composite keys


def test_make_key_composite_template_renders():
    key = DedupRegistry.make_key(
        "{symbol}-{date}", {"symbol": "AAPL", "date": "2026-10-01"}
    )
    assert key == "AAPL-2026-10-01"


def test_make_key_distinct_values_produce_distinct_keys():
    template = "{symbol}-{date}"
    base = {"symbol": "AAPL", "date": "2026-10-01"}
    keys = {
        DedupRegistry.make_key(template, base),
        DedupRegistry.make_key(template, base | {"symbol": "MSFT"}),
        DedupRegistry.make_key(template, base | {"date": "2026-10-02"}),
    }
    assert len(keys) == 3


def test_composite_key_drives_slot_suppression(registry):
    key_day1 = DedupRegistry.make_key(
        "{symbol}-{date}", {"symbol": "AAPL", "date": "2026-10-01"}
    )
    key_day2 = DedupRegistry.make_key(
        "{symbol}-{date}", {"symbol": "AAPL", "date": "2026-10-02"}
    )
    registry.record_push(key_day1, now=local_dt(13, day=1))
    assert registry.should_send(key_day1, now=local_dt(14, day=1)) is False
    assert registry.should_send(key_day2, now=local_dt(14, day=1)) is True


def test_make_key_title_placeholder_rejected():
    with pytest.raises(ValueError, match="标题指纹"):
        DedupRegistry.make_key("{title}", {"title": "某论坛帖子标题"})


def test_make_key_missing_field_raises_with_field_name():
    with pytest.raises(ValueError, match="symbol"):
        DedupRegistry.make_key("{symbol}-{date}", {"date": "2026-10-01"})


def test_make_key_rejects_attribute_and_index_access():
    with pytest.raises(ValueError, match="非法"):
        DedupRegistry.make_key("{url.host}", {"url.host": "example.com"})
    with pytest.raises(ValueError, match="非法"):
        DedupRegistry.make_key("{page[0]}", {"page[0]": "1"})


def test_make_key_rejects_conversion_and_format_spec():
    with pytest.raises(ValueError, match="格式说明"):
        DedupRegistry.make_key("{date!r}", {"date": "2026-10-01"})
    with pytest.raises(ValueError, match="格式说明"):
        DedupRegistry.make_key("{date:%Y-%m-%d}", {"date": "2026-10-01"})


def test_make_key_empty_template_raises():
    with pytest.raises(ValueError, match="不能为空"):
        DedupRegistry.make_key("   ", {"url": "https://example.com"})


def test_make_key_template_without_placeholder_raises():
    with pytest.raises(ValueError, match="至少一个字段占位符"):
        DedupRegistry.make_key("constant-key", {"url": "https://example.com"})


def test_make_key_malformed_template_raises():
    with pytest.raises(ValueError, match="语法错误"):
        DedupRegistry.make_key("{symbol", {"symbol": "AAPL"})


# ------------------------------------------------------------ change baseline


def test_baseline_roundtrip(store):
    changed = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    store.set_baseline(
        "https://example.com/feed",
        etag='"v1"',
        last_modified="Wed, 01 Oct 2026 00:00:00 GMT",
        content_hash="h1",
        last_changed=changed,
    )
    baseline = store.get_baseline("https://example.com/feed")
    assert baseline == ChangeBaseline(
        url="https://example.com/feed",
        etag='"v1"',
        last_modified="Wed, 01 Oct 2026 00:00:00 GMT",
        content_hash="h1",
        last_changed=changed,
    )


def test_baseline_missing_returns_none(store):
    assert store.get_baseline("https://example.com/never-fetched") is None


def test_baseline_upsert_fully_replaces_row(store):
    url = "https://example.com/feed"
    store.set_baseline(url, etag="e1", last_modified="m1", content_hash="h1")
    store.set_baseline(url, etag="e2")  # response no longer carries validators
    baseline = store.get_baseline(url)
    assert baseline.etag == "e2"
    assert baseline.last_modified is None
    assert baseline.content_hash is None
    assert baseline.last_changed is None


def test_set_baseline_rejects_empty_url(store):
    with pytest.raises(ValueError, match="change_baseline.url"):
        store.set_baseline("")


# -------------------------------------------------------------- engine hints


def test_engine_hint_write_then_read(store):
    source_key = "https://example.com/rss"
    assert store.get_engine_hint(source_key) is None
    store.set_engine_hint(source_key, "static_html")
    assert store.get_engine_hint(source_key) == "static_html"


def test_engine_hint_overwrite_and_clear(store):
    source_key = "https://example.com/rss"
    store.set_engine_hint(source_key, "direct_api")
    store.set_engine_hint(source_key, "static_html")  # hint failed, degrade re-writes
    assert store.get_engine_hint(source_key) == "static_html"
    store.clear_engine_hint(source_key)
    assert store.get_engine_hint(source_key) is None


def test_set_engine_hint_rejects_empty_values(store):
    with pytest.raises(ValueError, match="source_key"):
        store.set_engine_hint("", "static_html")
    with pytest.raises(ValueError, match="engine"):
        store.set_engine_hint("https://example.com", "")


# ---------------------------------------------------------------------- items


def test_item_roundtrip_with_json_fields(store):
    item_id = store.save_item(make_item())
    loaded = store.get_item(item_id)
    assert loaded is not None
    assert loaded.id == item_id
    assert loaded.url == "https://example.com/a"
    assert loaded.tags == ["freebie"]
    assert loaded.scores == {"total": 7.5}
    assert loaded.raw == {"extra": 1}
    assert loaded.first_seen is not None  # store fills first_seen on save


def test_mark_item_pushed_sets_slot_and_time(store):
    item_id = store.save_item(make_item())
    pushed = local_dt(13)
    store.mark_item_pushed(item_id, SLOT_PM, pushed_at=pushed)
    loaded = store.get_item(item_id)
    assert loaded.push_slot == SLOT_PM
    assert loaded.pushed_at == pushed


def test_mark_item_pushed_unknown_id_raises(store):
    with pytest.raises(ValueError, match="不存在"):
        store.mark_item_pushed(999, SLOT_AM)


def test_mark_item_pushed_rejects_unknown_slot(store):
    with pytest.raises(ValueError, match="push_slot"):
        store.mark_item_pushed(1, "noon")


def test_save_item_rejects_empty_required_fields(store):
    with pytest.raises(ValueError, match="items.url"):
        store.save_item(make_item(url=""))
    with pytest.raises(ValueError, match="items.dedup_key"):
        store.save_item(make_item(dedup_key=""))
    with pytest.raises(ValueError, match="items.title"):
        store.save_item(make_item(title=""))


def test_list_items_filters_category_and_limit(store):
    store.save_item(make_item(dedup_key="k1", category="freebie"))
    store.save_item(make_item(dedup_key="k2", category="token"))
    store.save_item(make_item(dedup_key="k3", category="freebie"))
    freebies = store.list_items(category="freebie")
    assert [i.dedup_key for i in freebies] == ["k3", "k1"]  # newest first
    assert [i.dedup_key for i in store.list_items(limit=2)] == ["k3", "k2"]


def test_list_items_since_filters_first_seen(store):
    early = local_dt(9)
    late = local_dt(14)
    store.save_item(make_item(dedup_key="early", first_seen=early))
    store.save_item(make_item(dedup_key="late", first_seen=late))
    keys = [i.dedup_key for i in store.list_items(since=local_dt(12))]
    assert keys == ["late"]


# ----------------------------------------------------------------------- runs


def test_run_lifecycle_and_latest(store):
    run_id = store.start_run("stocks")
    store.finish_run(
        run_id, status="partial", stats={"fetched": 3, "failed": 1}, error="1 个源失败"
    )
    record = store.get_run(run_id)
    assert record is not None
    assert record.category == "stocks"
    assert record.status == "partial"
    assert record.stats == {"fetched": 3, "failed": 1}
    assert record.error == "1 个源失败"
    assert record.finished_at is not None
    latest = store.latest_run("stocks")
    assert latest is not None and latest.id == run_id


def test_latest_run_without_category_returns_most_recent(store):
    first = store.start_run("stocks")
    second = store.start_run("ai-news")
    assert store.latest_run().id == second
    assert store.latest_run("stocks").id == first


def test_finish_run_rejects_unknown_status(store):
    run_id = store.start_run("stocks")
    with pytest.raises(ValueError, match="status"):
        store.finish_run(run_id, status="ok")


def test_finish_run_unknown_run_id_raises(store):
    with pytest.raises(ValueError, match="不存在"):
        store.finish_run(999, status="success")


# ------------------------------------------------------- schema / WAL / protocol


def test_store_creates_all_five_tables(store):
    names = {
        row[0]
        for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"items", "dedup_registry", "change_baseline", "engine_hints", "runs"} <= names


def test_store_satisfies_store_protocol(store):
    assert isinstance(store, Store)


def test_store_wal_mode_enabled(tmp_path):
    with SQLiteStore(tmp_path / "myia.db") as fresh_store:
        mode = fresh_store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_wal_concurrent_writers_persist_all_rows(tmp_path):
    path = tmp_path / "myia.db"
    writer_count, per_writer = 4, 5
    barrier = threading.Barrier(writer_count)
    errors: list[BaseException] = []

    def worker(writer: int) -> None:
        writer_store = SQLiteStore(path)
        try:
            barrier.wait(timeout=10)
            for i in range(per_writer):
                writer_store.mark_dedup_seen(f"w{writer}-{i}", NOW_UTC)
        except BaseException as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)
        finally:
            writer_store.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(writer_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    reader = SQLiteStore(path)
    try:
        count = reader.conn.execute("SELECT COUNT(*) FROM dedup_registry").fetchone()[0]
    finally:
        reader.close()
    assert count == writer_count * per_writer


def test_record_dedup_push_rejects_unknown_slot(store):
    with pytest.raises(ValueError, match="push_slot"):
        store.record_dedup_push("k", NOW_UTC, "noon")


def test_mark_dedup_seen_rejects_empty_key(store):
    with pytest.raises(ValueError, match="不能为空"):
        store.mark_dedup_seen("", NOW_UTC)
