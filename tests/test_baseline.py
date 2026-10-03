"""Tests for the v0.4 trend baseline (PRD 10-01-v04-trend-baseline).

Covers:

- schema ``baseline:`` sidecar 节:缺省值、fail-fast(空字段/重复窗口/未知
  窗口/MSRP 坏值/未知字段)、load_category 挂载与 ``$.baseline`` 错误路径、
  12 节公开契约不随之增长(baseline 不进 ``CategoryConfig.model_fields``,
  test_skill_doc 的逐字段锁保持绿色);
- ``metric_history`` 数值历史快照表:写入/round-trip/校验拒写、v4→v5 迁移
  兼容(旧库行保留、新表可用)、窗口对比(day = 今日零点前最新 / week =
  本 ISO 周周一零点前最新,含时区语义与「当日快照不做自己的基线」)、
  ``sum_metrics`` 半开窗口求和(关键词周环比聚合);
- retention 协同:基线期(2× retention)长于条目期;
- 推送模板沙箱自定义函数:``vs_yesterday`` / ``vs_last_week`` / ``vs_msrp``
  与 ``keyword_trends`` 上下文、无基线渲染为空、沙箱逃逸拒绝、关键词提及
  量周环比统计与文案;
- gpu-prices.yaml 官方插件补全:schema 校验 + 12 节显式声明 + baseline 节 +
  路由两级 + 「vs 上周」真实卡片渲染快照。

All clocks are injected (fixed ``NOW``) — no real waiting, no real network.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shishi.classify import rules_from_config
from shishi.push.route import resolve_route, routes_from_config
from shishi.push.base import SendContext
from shishi.push.templates import (
    KeywordTrend,
    MetricComparison,
    TemplateRenderer,
    TemplateRenderError,
    build_keyword_trends,
    build_trend_table,
    count_keyword_mentions,
    format_change,
    format_msrp,
    record_item_metrics,
    record_keyword_mentions,
)
from shishi.schema import (
    BASELINE_WINDOWS,
    BaselineConfig,
    CategoryConfig,
    LoadError,
    load_category,
    load_category_file,
)
from shishi.store import (
    SCHEMA_VERSION,
    ItemRecord,
    SQLiteStore,
    metric_window_start,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GPU_PRICES = REPO_ROOT / "plugins" / "gpu-prices.yaml"

# Fixed +08:00 (gpu-prices.yaml 的 schedule 时区):deterministic window math.
TZ = timezone(timedelta(hours=8))
#: 2026-10-02 15:00 local(+08)= Friday — inside the ISO week 2026-W40
#: (Monday 2026-09-28), inside the local day 2026-10-02.
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=TZ)
LAST_WEEK = datetime(2026, 9, 25, 10, 0, tzinfo=TZ)  # previous ISO week
YESTERDAY = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)  # previous local day

#: Two demo catalog items (公开演示数据):price fields drive every comparison.
DEMO_ITEMS = [
    {
        "title": "索泰 RTX 5090 32GB 现货",
        "url": "https://detail.zol.com.cn/vga/1.html",
        "dedup_key": "https://detail.zol.com.cn/vga/1.html",
        "price": 19800,
    },
    {
        "title": "蓝宝石 RX 9070 XT 极地版",
        "url": "https://detail.zol.com.cn/vga/2.html",
        "dedup_key": "https://detail.zol.com.cn/vga/2.html",
        "price": 4799,
    },
]
KEYWORDS = ["RTX", "RX", "显卡"]


def _seed_history(store: SQLiteStore, category: str = "gpu-prices") -> None:
    """Seed last-week / yesterday price baselines + last-week keyword counts.

    Values are chosen to format exactly: 19800 vs 18000 → +10.0%,
    19800 vs 16500 → +20.0%, 4799 vs 4999 → −4.0% (yesterday) / +0.0% (week).
    """
    rows = [
        ("https://detail.zol.com.cn/vga/1.html", "price", 16500.0, LAST_WEEK),
        ("https://detail.zol.com.cn/vga/1.html", "price", 18000.0, YESTERDAY),
        ("https://detail.zol.com.cn/vga/2.html", "price", 4799.0, LAST_WEEK),
        ("https://detail.zol.com.cn/vga/2.html", "price", 4999.0, YESTERDAY),
        # 关键词提及量(品类维度):上周 RTX 提及 2 条,RX / 显卡 0 条。
        ("keyword:RTX", "mentions", 2.0, LAST_WEEK),
        ("keyword:RX", "mentions", 0.0, LAST_WEEK),
        ("keyword:显卡", "mentions", 0.0, LAST_WEEK),
    ]
    for key, field, value, recorded_at in rows:
        store.save_metric(category, key, field, value, recorded_at=recorded_at)


@pytest.fixture()
def db_store(tmp_path):
    """A throwaway SQLiteStore per test (zero shared state between tests)."""
    store = SQLiteStore(tmp_path / "baseline.db")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# 1. schema: baseline sidecar 节
# ---------------------------------------------------------------------------


def test_baseline_section_defaults():
    """Every field has an explicit default (铁律 1: AI 生成可用的节)."""
    config = BaselineConfig()
    assert config.enabled is False
    assert config.fields == []
    assert config.windows == list(BASELINE_WINDOWS) == ["day", "week"]
    assert config.msrp == {}


def test_baseline_sidecar_loads_and_attaches_via_load_category():
    """``baseline:`` validates outside the 12 节 model and lands on ``.baseline``."""
    config = load_category(
        {
            "id": "demo",
            "name": "演示",
            "schedule": "0 9 * * *",
            "sources": [
                {
                    "name": "s",
                    "url": "https://example.com/a",
                    "extract": {
                        "type": "list",
                        "item": "div.item",
                        "fields": {"title": "a.title", "url": "a.title@href",
                                   "price": "span.price"},
                    },
                }
            ],
            "baseline": {
                "enabled": True,
                "fields": ["price"],
                "windows": ["day", "week"],
                "msrp": {"RTX 5090": 16499},
            },
        }
    )
    assert isinstance(config.baseline, BaselineConfig)
    assert config.baseline.enabled is True
    assert config.baseline.fields == ["price"]
    assert config.baseline.msrp == {"RTX 5090": 16499.0}


def test_baseline_absent_and_null_both_yield_none():
    """未声明与显式 ``baseline: null`` 都是「无基线」(占位写法不拒载)."""
    minimal = {
        "id": "demo",
        "name": "演示",
        "schedule": "0 9 * * *",
        "sources": [{"name": "s", "url": "https://example.com/a"}],
    }
    assert load_category(dict(minimal)).baseline is None
    assert load_category({**minimal, "baseline": None}).baseline is None


def test_baseline_never_grows_the_twelve_section_contract():
    """baseline 是 sidecar,不是第 13 节(SKILL.md 逐字段锁保持有效)."""
    assert "baseline" not in CategoryConfig.model_fields


def _load_with_baseline(baseline: object):
    data = {
        "id": "demo",
        "name": "演示",
        "schedule": "0 9 * * *",
        "sources": [{"name": "s", "url": "https://example.com/a"}],
        "baseline": baseline,
    }
    try:
        return load_category(data), None
    except LoadError as exc:
        return None, exc


def test_baseline_enabled_requires_at_least_one_numeric_field():
    """enabled=true 且无字段 → 结构化拒绝(缺省报全部路径)."""
    _, exc = _load_with_baseline({"enabled": True})
    assert exc is not None
    detail = next(e for e in exc.errors if e.error_type == "missing_baseline_fields")
    assert detail.path == "$.baseline.fields"


def test_baseline_refuses_empty_and_duplicate_windows():
    _, empty = _load_with_baseline({"windows": []})
    assert empty is not None
    assert any(e.error_type == "missing_baseline_windows" and e.path == "$.baseline.windows" for e in empty.errors)

    _, dup = _load_with_baseline({"windows": ["day", "day"]})
    assert dup is not None
    assert any(e.error_type == "duplicate_baseline_windows" for e in dup.errors)


def test_baseline_refuses_unknown_window_value():
    """封闭词表:拼错的窗口拒绝并列出合法值,不静默跳过."""
    _, exc = _load_with_baseline({"windows": ["month"]})
    assert exc is not None
    detail = next(e for e in exc.errors if e.path == "$.baseline.windows[0]")
    assert detail.error_type == "invalid_value"


def test_baseline_refuses_duplicate_fields():
    _, exc = _load_with_baseline({"enabled": True, "fields": ["price", "price"]})
    assert exc is not None
    assert any(e.error_type == "duplicate_baseline_fields" for e in exc.errors)


@pytest.mark.parametrize(
    "msrp",
    [{"RTX 5090": 0}, {"RTX 5090": -1}, {"RTX 5090": "免费"}, {"RTX 5090": float("inf")}],
)
def test_baseline_refuses_bad_msrp_values(msrp):
    """MSRP 对照表值必须为正的有限数值(0/负数/字符串/inf 均拒载)."""
    _, exc = _load_with_baseline({"msrp": msrp})
    assert exc is not None
    assert any(e.path.startswith("$.baseline.msrp") for e in exc.errors)


def test_baseline_refuses_unknown_fields():
    """铁律 2:baseline 节内未知字段同样 fail-fast(路径带 $.baseline 前缀)."""
    _, exc = _load_with_baseline({"baseline_fields": ["price"]})
    assert exc is not None
    detail = next(e for e in exc.errors if e.error_type == "unknown_field")
    assert detail.path == "$.baseline.baseline_fields"


def test_baseline_section_must_be_a_mapping():
    _, exc = _load_with_baseline(["price"])
    assert exc is not None
    detail = next(e for e in exc.errors if e.error_type == "invalid_baseline_section")
    assert detail.path == "$.baseline"


def test_baseline_errors_merge_into_the_single_load_error():
    """12 节错误与 baseline 错误同报告输出(一次修完,不来回试)."""
    data = {
        "id": "坏 id",
        "name": "演示",
        "schedule": "0 9 * * *",
        "sources": [{"name": "s", "url": "https://example.com/a"}],
        "baseline": {"enabled": True},
    }
    with pytest.raises(LoadError) as excinfo:
        load_category(data)
    paths = {e.path for e in excinfo.value.errors}
    assert "$.id" in paths
    assert "$.baseline.fields" in paths


# ---------------------------------------------------------------------------
# 2. store: metric_history 历史写入 / 迁移兼容 / 窗口对比
# ---------------------------------------------------------------------------


def test_save_metric_round_trips(db_store):
    metric_id = db_store.save_metric(
        "gpu-prices", "https://x/1", "price", 100.5, recorded_at=YESTERDAY
    )
    assert metric_id > 0
    latest = db_store.latest_metric("gpu-prices", "https://x/1", "price")
    assert latest is not None
    assert latest.value == 100.5
    assert latest.recorded_at == YESTERDAY
    assert latest.category == "gpu-prices"
    assert latest.field == "price"


@pytest.mark.parametrize("value", [True, "12.5", None, float("nan"), float("inf")])
def test_save_metric_refuses_non_numeric_values(db_store, value):
    """bool/字符串/None/NaN/inf 一律结构化拒写(坏基线比缺基线更危险)."""
    with pytest.raises(ValueError):
        db_store.save_metric("gpu-prices", "k", "price", value)


@pytest.mark.parametrize("key_args", [("", "k", "price"), ("c", "", "price"), ("c", "k", "")])
def test_save_metric_refuses_empty_key_components(db_store, key_args):
    category, metric_key, field = key_args
    with pytest.raises(ValueError):
        db_store.save_metric(category, metric_key, field, 1.0)


def test_latest_metric_returns_newest_and_none_when_absent(db_store):
    assert db_store.latest_metric("gpu-prices", "k", "price") is None
    db_store.save_metric("gpu-prices", "k", "price", 1.0, recorded_at=YESTERDAY - timedelta(days=1))
    db_store.save_metric("gpu-prices", "k", "price", 2.0, recorded_at=YESTERDAY)
    # 同刻并列时 id 新者胜(insert 顺序即时间轴兜底)。
    db_store.save_metric("gpu-prices", "k", "price", 3.0, recorded_at=YESTERDAY)
    latest = db_store.latest_metric("gpu-prices", "k", "price")
    assert latest is not None and latest.value == 3.0


def test_day_window_compares_against_previous_local_day(db_store):
    """vs 昨日 = 当地零点前最新快照;当日快照永不做自己的基线."""
    db_store.save_metric("gpu-prices", "k", "price", 100.0, recorded_at=YESTERDAY)
    db_store.save_metric("gpu-prices", "k", "price", 110.0, recorded_at=NOW - timedelta(hours=1))
    baseline = db_store.get_metric_baseline(
        "gpu-prices", "k", "price", now=NOW, window="day", tz=TZ
    )
    assert baseline is not None and baseline.value == 100.0


def test_week_window_compares_against_previous_iso_week(db_store):
    """vs 上周 = 本 ISO 周周一零点前最新快照;本周一以来的快照不算基线."""
    db_store.save_metric("gpu-prices", "k", "price", 100.0, recorded_at=LAST_WEEK)
    monday_morning = datetime(2026, 9, 28, 9, 0, tzinfo=TZ)  # 本周一(窗口内)
    db_store.save_metric("gpu-prices", "k", "price", 111.0, recorded_at=monday_morning)
    baseline = db_store.get_metric_baseline(
        "gpu-prices", "k", "price", now=NOW, window="week", tz=TZ
    )
    assert baseline is not None and baseline.value == 100.0


def test_window_comparison_returns_none_without_history(db_store):
    assert (
        db_store.get_metric_baseline("gpu-prices", "k", "price", now=NOW, window="day", tz=TZ)
        is None
    )


def test_window_comparison_follows_the_category_timezone(db_store):
    """窗口分界跟随品类时区:同一时刻,tz 不同则基线不同."""
    # 2026-10-02 02:00 +08 = 2026-10-01 18:00 UTC。
    early_local = datetime(2026, 10, 2, 2, 0, tzinfo=TZ)
    db_store.save_metric("gpu-prices", "k", "price", 88.0, recorded_at=early_local)
    now = datetime(2026, 10, 2, 10, 0, tzinfo=TZ)
    # +08 视角:快照在当日本地零点之后 → 不是「昨日」基线。
    assert db_store.get_metric_baseline("gpu-prices", "k", "price", now=now, window="day", tz=TZ) is None
    # UTC 视角:窗口起点为 10-02 00:00 UTC(= 10-02 08:00 +08),快照在其前 → 是基线。
    baseline = db_store.get_metric_baseline("gpu-prices", "k", "price", now=now, window="day")
    assert baseline is not None and baseline.value == 88.0


def test_window_comparison_refuses_unknown_window(db_store):
    with pytest.raises(ValueError):
        db_store.get_metric_baseline("gpu-prices", "k", "price", now=NOW, window="month")


def test_metric_window_start_math():
    assert metric_window_start(NOW, "day", TZ) == datetime(2026, 10, 2, 0, 0, tzinfo=TZ)
    assert metric_window_start(NOW, "week", TZ) == datetime(2026, 9, 28, 0, 0, tzinfo=TZ)
    # naive 输入按 UTC 解释(store 全线约定)。
    naive = datetime(2026, 10, 2, 15, 0)
    assert metric_window_start(naive, "day").tzinfo is not None
    with pytest.raises(ValueError):
        metric_window_start(NOW, "month")


def test_sum_metrics_half_open_window(db_store):
    """[since, until) 半开窗口:相邻两次调用恰好铺满两周,无重叠无遗漏."""
    key = "keyword:RTX"
    db_store.save_metric("gpu-prices", key, "mentions", 2.0, recorded_at=datetime(2026, 9, 22, tzinfo=TZ))
    db_store.save_metric("gpu-prices", key, "mentions", 3.0, recorded_at=datetime(2026, 9, 25, tzinfo=TZ))
    db_store.save_metric("gpu-prices", key, "mentions", 4.0, recorded_at=datetime(2026, 9, 28, 1, 0, tzinfo=TZ))
    db_store.save_metric("gpu-prices", key, "mentions", 1.0, recorded_at=NOW)
    week_start = metric_window_start(NOW, "week", TZ)
    this_week = db_store.sum_metrics(
        "gpu-prices", key, "mentions", since=week_start, until=week_start + timedelta(days=7)
    )
    last_week = db_store.sum_metrics(
        "gpu-prices", key, "mentions", since=week_start - timedelta(days=7), until=week_start
    )
    assert this_week == 5.0
    assert last_week == 5.0


def test_sum_metrics_validates_window_and_keys(db_store):
    with pytest.raises(ValueError):
        db_store.sum_metrics("gpu-prices", "", "mentions", since=NOW, until=NOW)
    with pytest.raises(ValueError):
        db_store.sum_metrics("gpu-prices", "k", "mentions", since=NOW, until=NOW - timedelta(days=1))


def _make_legacy_v4_db(path: Path) -> None:
    """Hand-build a v4-shaped database (feedback era, no metric_history)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            dedup_key TEXT NOT NULL,
            source TEXT,
            title TEXT NOT NULL,
            content TEXT,
            content_hash TEXT,
            tags TEXT,
            category TEXT,
            scores TEXT,
            pushed_at TEXT,
            push_slot TEXT,
            first_seen TEXT NOT NULL,
            raw TEXT
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            stats TEXT,
            error TEXT,
            steps TEXT
        );
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
        CREATE TABLE feedback_tuning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO items (url, dedup_key, title, first_seen) VALUES (?, ?, ?, ?)",
        ("https://example.com/a", "https://example.com/a", "旧库条目", "2026-09-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO feedback (dedup_key, verdict, channel, created_at) VALUES (?, ?, ?, ?)",
        ("https://example.com/a", "good", "cli", "2026-09-01T00:00:00+00:00"),
    )
    conn.execute("INSERT INTO store_meta (key, value) VALUES ('schema_version', '4')")
    conn.commit()
    conn.close()


def test_v4_database_migrates_to_current_without_data_loss(tmp_path):
    """迁移兼容:v4 旧库打开即前滚(v5+v6 两步),旧行保留,新表立即可写."""
    db_path = tmp_path / "legacy.db"
    _make_legacy_v4_db(db_path)
    store = SQLiteStore(db_path)
    try:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION) == "6"
        item = store.get_item_by_dedup_key("https://example.com/a")
        assert item is not None and item.title == "旧库条目"
        feedback = store.list_feedback()
        assert len(feedback) == 1 and feedback[0].verdict == "good"
        # 迁移后新表立即可写可查。
        store.save_metric("gpu-prices", "k", "price", 1.0, recorded_at=NOW)
        assert store.latest_metric("gpu-prices", "k", "price").value == 1.0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 3. retention 协同: 基线期长于条目期
# ---------------------------------------------------------------------------


def test_cleanup_keeps_metric_history_longer_than_items(db_store):
    """条目 7 天清,数值基线 14 天才清(2× retention):基线期长于条目期."""
    pushed_at = NOW - timedelta(days=8)
    db_store.save_item(
        ItemRecord(
            url="https://example.com/a",
            dedup_key="https://example.com/a",
            title="已推送条目",
            pushed_at=pushed_at,
            push_slot="am",
        )
    )
    db_store.save_metric("gpu-prices", "https://example.com/a", "price", 100.0, recorded_at=pushed_at)
    db_store.save_metric("gpu-prices", "https://example.com/b", "price", 200.0, recorded_at=NOW - timedelta(days=15))

    counts = db_store.cleanup_expired(7, now=NOW)

    assert counts["items_pushed"] == 1  # 条目出窗
    assert counts["metrics"] == 1  # 只有 15 天前的基线出窗(> 2×7d)
    # 8 天前的价格基线仍在:周环比/日环比要跨窗口取历史。
    assert db_store.latest_metric("gpu-prices", "https://example.com/a", "price").value == 100.0
    assert db_store.latest_metric("gpu-prices", "https://example.com/b", "price") is None


# ---------------------------------------------------------------------------
# 4. 推送模板: 沙箱自定义函数 vs_yesterday / vs_last_week / vs_msrp
# ---------------------------------------------------------------------------


def _trend_context() -> SendContext:
    return SendContext(slot="am", date="2026-10-02", category="显卡行情", kind="digest")


def test_format_change_percent_and_absolute_fallback():
    up = MetricComparison(window="week", field="price", current=105.0, baseline=100.0)
    assert format_change(up) == "较上周 +5.0%"
    down = MetricComparison(window="day", field="price", current=97.0, baseline=100.0)
    assert format_change(down) == "较昨日 -3.0%"
    zero = MetricComparison(window="week", field="price", current=5.0, baseline=0.0)
    assert format_change(zero) == "较上周 +5"  # 基线 0 → 百分比无定义,退回绝对差
    negative = MetricComparison(window="week", field="price", current=-90.0, baseline=-100.0)
    assert format_change(negative) == "较上周 +10.0%"  # 负基线按绝对值作分母


def test_format_msrp_text():
    assert format_msrp(4600.0, 4999.0) == "低于 MSRP 8.0%"
    assert format_msrp(5000.0, 4999.0) == "高于 MSRP 0.0%"
    assert format_msrp(4999.0, 4999.0) == "MSRP 平价"


def test_vs_functions_render_empty_without_trend_data():
    """无表/无基线时渲染为空串 —— 首次观察没有对比是正常路径."""
    renderer = TemplateRenderer()
    template = (
        "{% for item in items %}"
        "A{{ vs_yesterday(item, 'price') }}B{{ vs_last_week(item, 'price') }}"
        "C{{ vs_msrp(item) }}D{{ keyword_trends }}"
        "{% endfor %}"
    )
    rendered = renderer.render(template, [DEMO_ITEMS[0]], _trend_context())
    assert rendered == "ABCD[]"


def test_vs_functions_render_comparisons_from_store(db_store):
    _seed_history(db_store)
    table = build_trend_table(
        db_store, category="gpu-prices", items=DEMO_ITEMS, fields=["price"], now=NOW, tz=TZ
    )
    renderer = TemplateRenderer()
    template = (
        "{% for item in items %}"
        "{{ vs_yesterday(item, 'price') }}|{{ vs_last_week(item, 'price') }}\n"
        "{% endfor %}"
    )
    rendered = renderer.render(template, DEMO_ITEMS, _trend_context(), trends=table)
    assert rendered == (
        "较昨日 +10.0%|较上周 +20.0%\n较昨日 -4.0%|较上周 +0.0%"
    )


def test_trend_table_skips_non_numeric_fields_and_unknown_keys(db_store):
    _seed_history(db_store)
    items = [{"title": "无价格条目", "url": "https://x/3", "price": "面议"}]
    table = build_trend_table(
        db_store, category="gpu-prices", items=items, fields=["price"], now=NOW, tz=TZ
    )
    assert len(table) == 0
    renderer = TemplateRenderer()
    template = "{% for item in items %}{{ vs_last_week(item, 'price') }}{% endfor %}"
    assert renderer.render(template, items, _trend_context(), trends=table) == ""


def test_build_trend_table_refuses_unknown_window(db_store):
    with pytest.raises(ValueError):
        build_trend_table(
            db_store,
            category="gpu-prices",
            items=DEMO_ITEMS,
            fields=["price"],
            now=NOW,
            windows=("month",),
        )


def test_vs_msrp_matches_longest_product_key():
    """多键命中取最长键(最具体的对照优先)."""
    renderer = TemplateRenderer()
    template = "{% for item in items %}{{ vs_msrp(item) }}{% endfor %}"
    msrp = {"RTX 5090": 16499.0, "5090": 9999.0}
    item = {"title": "索泰 RTX 5090 32GB", "price": 18999}
    assert renderer.render(template, [item], _trend_context(), msrp=msrp) == "高于 MSRP 15.2%"
    # 无匹配 → 空串。
    other = {"title": "intel Arc B580", "price": 2049}
    assert renderer.render(template, [other], _trend_context(), msrp=msrp) == ""


def test_sandbox_blocks_escape_through_trend_functions():
    """沙箱红线:模板函数不可成为属性逃逸入口."""
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError):
        renderer.render("{{ vs_last_week.__globals__ }}", [], _trend_context())


# ---------------------------------------------------------------------------
# 5. 关键词提及量周环比 (品类维度统计入卡)
# ---------------------------------------------------------------------------


def test_count_keyword_mentions_case_insensitive_per_item():
    items = [
        {"title": "rtx 5090 大降价"},
        {"title": "RTX RTX 双卡齐发"},  # 同条目同词只计 1(口径:提及条目数)
        {"title": "RX 9070 XT 首发评测"},
        {"title": None},  # 非字符串标题跳过
        {"url": "https://x/5"},  # 缺标题跳过
    ]
    counts = count_keyword_mentions(items, ["RTX", "RX", "显卡"])
    assert counts == {"RTX": 2, "RX": 1, "显卡": 0}


def test_record_keyword_mentions_writes_zero_rows_too(db_store):
    """零命中也入账:否则下周的周环比分不清「没统计」与「没提及」."""
    counts = record_keyword_mentions(
        db_store, category="gpu-prices", items=[DEMO_ITEMS[0]], keywords=KEYWORDS, now=NOW
    )
    assert counts == {"RTX": 1, "RX": 0, "显卡": 0}
    for word in KEYWORDS:
        latest = db_store.latest_metric("gpu-prices", f"keyword:{word}", "mentions")
        assert latest is not None and latest.value == counts[word]


def test_keyword_trends_week_over_week(db_store):
    _seed_history(db_store)
    record_keyword_mentions(
        db_store, category="gpu-prices", items=DEMO_ITEMS, keywords=KEYWORDS, now=NOW
    )
    trends = build_keyword_trends(
        db_store, category="gpu-prices", keywords=KEYWORDS, now=NOW, tz=TZ
    )
    assert [(t.word, t.count, t.previous) for t in trends] == [
        ("RTX", 1, 2),
        ("RX", 1, 0),
        ("显卡", 0, 0),
    ]
    assert trends[0].change_text == "环比 -50.0%"
    assert trends[1].change_text == "环比 新增"
    assert trends[2].change_text == "环比 持平"


def test_keyword_trend_text_variants():
    assert KeywordTrend(word="RTX", count=3, previous=2).change_text == "环比 +50.0%"
    assert KeywordTrend(word="RTX", count=2, previous=2).change_text == "环比 +0.0%"
    assert KeywordTrend(word="RTX", count=1, previous=0).change_text == "环比 新增"
    assert KeywordTrend(word="RTX", count=0, previous=0).change_text == "环比 持平"


def test_keyword_trends_render_into_the_digest_card(db_store):
    """关键词周环比作为上下文进入卡片(品类维度统计入 digest 卡)."""
    _seed_history(db_store)
    record_keyword_mentions(
        db_store, category="gpu-prices", items=DEMO_ITEMS, keywords=KEYWORDS, now=NOW
    )
    trends = build_keyword_trends(
        db_store, category="gpu-prices", keywords=KEYWORDS, now=NOW, tz=TZ
    )
    renderer = TemplateRenderer()
    template = (
        "**关键词提及周环比**:{% for t in keyword_trends %}"
        "{{ t.word }} {{ t.count }} 条({{ t.change_text }}) {% endfor %}"
    )
    rendered = renderer.render(template, [], _trend_context(), keyword_trends=trends)
    assert rendered == (
        "**关键词提及周环比**:RTX 1 条(环比 -50.0%) RX 1 条(环比 新增) 显卡 0 条(环比 持平)"
    )


# ---------------------------------------------------------------------------
# 6. gpu-prices.yaml 官方插件: schema 校验 + 「vs 上周」渲染快照
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gpu_config():
    return load_category_file(GPU_PRICES)


def test_gpu_prices_official_plugin_loads_with_baseline(gpu_config):
    """官方插件经真实入口装载,baseline sidecar 完整挂载."""
    assert gpu_config.id == "gpu-prices"
    baseline = gpu_config.baseline
    assert baseline is not None and baseline.enabled is True
    assert baseline.fields == ["price"]
    assert baseline.windows == ["day", "week"]
    assert baseline.msrp["RTX 5090"] == 16499.0


def test_gpu_prices_declares_all_twelve_sections_explicitly(gpu_config):
    """官方插件标准:12 节全部显式声明(AI 生成示范职责)."""
    required = {
        "id", "name", "schedule", "timezone", "sources", "watchlist",
        "classify", "dedup", "enrich", "push", "storage",
    }
    assert required <= gpu_config.model_fields_set
    assert gpu_config.sources[0].engine == "static_html"  # ZOL L2 源
    assert gpu_config.sources[0].extract is not None
    assert "price" in gpu_config.sources[0].extract.fields


def test_gpu_prices_route_covers_two_tiers(gpu_config):
    """旗舰高价即时推;未命中走缺省保守 digest(两级语义)."""
    rules = routes_from_config(gpu_config.push[0].route)
    assert resolve_route({"price": 19800, "title": "RTX 5090"}, rules).mode == "immediate"
    assert resolve_route({"price": 4799, "title": "RX 9070 XT"}, rules).mode == "digest"


def test_gpu_prices_classify_rules_parse(gpu_config):
    rules = rules_from_config([rule.model_dump() for rule in gpu_config.classify.rules])
    assert len(rules) == 1


def test_gpu_prices_render_snapshot_contains_vs_last_week_card(gpu_config, db_store):
    """验收:示例品类产出含「vs 上周」的真实卡片(渲染快照,逐字符钉死)."""
    _seed_history(db_store)
    # 与流水线装配顺序一致:先记本轮快照(数值 + 关键词),再装配对比,再渲染。
    record_item_metrics(
        db_store, category="gpu-prices", items=DEMO_ITEMS, fields=["price"], now=NOW
    )
    record_keyword_mentions(
        db_store, category="gpu-prices", items=DEMO_ITEMS, keywords=KEYWORDS, now=NOW
    )
    table = build_trend_table(
        db_store, category="gpu-prices", items=DEMO_ITEMS, fields=["price"], now=NOW, tz=TZ
    )
    keyword_trends = build_keyword_trends(
        db_store, category="gpu-prices", keywords=KEYWORDS, now=NOW, tz=TZ
    )
    renderer = TemplateRenderer()
    rendered = renderer.render(
        gpu_config.push[0].template,
        DEMO_ITEMS,
        _trend_context(),
        trends=table,
        keyword_trends=keyword_trends,
        msrp=gpu_config.baseline.msrp,
    )
    assert rendered == (
        "**显卡行情 · 2026-10-02**\n"
        "- [索泰 RTX 5090 32GB 现货](https://detail.zol.com.cn/vga/1.html) "
        "¥19800 高于 MSRP 20.0% 较昨日 +10.0% 较上周 +20.0%\n"
        "- [蓝宝石 RX 9070 XT 极地版](https://detail.zol.com.cn/vga/2.html) "
        "¥4799 低于 MSRP 4.0% 较昨日 -4.0% 较上周 +0.0%\n"
        "**关键词提及周环比**:RTX 1 条(环比 -50.0%) RX 1 条(环比 新增) "
        "显卡 0 条(环比 持平)"
    )


def test_gpu_prices_render_snapshot_uses_the_real_template_functions(gpu_config, db_store):
    """同快照走 vs_msrp / keyword_trends 的独立断言(失败时可定位到函数粒度)."""
    _seed_history(db_store)
    table = build_trend_table(
        db_store, category="gpu-prices", items=DEMO_ITEMS, fields=["price"], now=NOW, tz=TZ
    )
    renderer = TemplateRenderer()
    rendered = renderer.render(
        "{% for item in items %}{{ vs_msrp(item) }}#{{ vs_last_week(item, 'price') }}{% endfor %}",
        [DEMO_ITEMS[1]],
        _trend_context(),
        trends=table,
        msrp=gpu_config.baseline.msrp,
    )
    assert rendered == "低于 MSRP 4.0%#较上周 +0.0%"


# ---------------------------------------------------------------------------
# 管线集成(PRD 验收口径):趋势数据经 Pipeline.run → Channel.send 真实上卡
# ---------------------------------------------------------------------------


def test_pipeline_run_feeds_trend_context_into_rendered_card(tmp_path, monkeypatch):
    """回归(验收「示例品类产出含『vs 上周』的真实卡片」):

    此前 record/build 四函数在生产代码零调用,四个通道裸调三参 render,
    vs_* 恒空串、keyword_trends 恒空 —— 验收由一条生产从不执行的手工装配
    序列充当。本测试钉死真实链路:Pipeline.run 记录快照 → 通道渲染收到
    trends/keyword_trends/msrp,模板产出「较上周」「环比」「MSRP」文案。
    """
    import io

    from shishi.pipeline import Pipeline
    from conftest import FakeClock  # noqa: F401  (make_pipeline 语义);裸 pytest 下 tests/ 由 prepend 模式入 sys.path

    # 1) 配置:直接构造 CategoryConfig 并手工挂 baseline sidecar(与
    #    load_category 挂载语义一致),含 watchlist 关键词与 MSRP 对照。
    from datetime import datetime, timezone

    from shishi.schema import load_category

    config = load_category(
        {
            "id": "gpu-prices",
            "name": "显卡价格",
            "schedule": "0 9,21 * * *",
            "watchlist": {"keywords": ["RTX", "RX"]},
            "sources": [
                {
                    "name": "hub",
                    "url": "https://api.demo.local/gpus",
                    "extract": {
                        "type": "json_path",
                        "fields": {"title": "$[*].title", "url": "$[*].url",
                                   "price": "$[*].price"},
                    },
                }
            ],
            "classify": {"builtin": False, "rules": []},
            "push": [
                {
                    "channel": "stdout",
                    "template": (
                        "{% for i in items %}"
                        "- {{ i.title }}: {{ i.price }}"
                        " {{ vs_last_week(i, 'price') }}"
                        " {{ vs_yesterday(i, 'price') }}"
                        " {{ vs_msrp(i) }}\n"
                        "{% endfor %}"
                        "{% if keyword_trends %}关键词:\n"
                        "{% for t in keyword_trends %}- {{ t.word }} {{ t.change_text }}\n"
                        "{% endfor %}{% endif %}"
                    ),
                }
            ],
            "baseline": {
                "enabled": True,
                "fields": ["price"],
                "windows": ["day", "week"],
                "msrp": {"RTX 5090": 16500},
            },
        }
    )

    # 2) 历史快照:上周 + 昨日价格基线与上周关键词计数(确定性数值见 _seed_history)。
    store = SQLiteStore(tmp_path / "wiring.db")
    _seed_history(store, category="gpu-prices")

    # 3) 管线:mock 客户端返回本轮条目;真通道,stdout 落笔进 StringIO。
    import httpx

    handler_payload = [
        {"title": "索泰 RTX 5090 32GB 现货", "url": "https://detail.zol.com.cn/vga/1.html",
         "price": 19800},
        {"title": "蓝宝石 RX 9070 XT 极地版", "url": "https://detail.zol.com.cn/vga/2.html",
         "price": 4799},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=handler_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    card = io.StringIO()
    wall = {"now": NOW}

    pipeline = Pipeline(
        config,
        store=store,
        client=client,
        wall_clock=lambda: wall["now"],
        stdout_stream=card,
    )
    result = asyncio_run(pipeline.run())
    assert result.status == "success"

    # 4) 快照确已写入本轮数值(price 19800/4799 两行 + 关键词计数)。
    latest = store.latest_metric("gpu-prices", "https://detail.zol.com.cn/vga/1.html", "price")
    assert latest is not None and latest.value == 19800.0

    # 5) 卡片含真实对比文本(vs 上周 +20.0%、vs 昨日 +10.0%、高于 MSRP 20.0%、关键词环比)。
    text = card.getvalue()
    assert "较上周 +20.0%" in text
    assert "较昨日 +10.0%" in text
    assert "高于 MSRP 20.0%" in text  # 19800 vs 建议零售价 16500
    assert "RTX" in text and "环比" in text
    store.close()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_cleanup_respects_declared_window_over_short_retention(db_store):
    """retention: 3d + windows: [week]:metric_history 保 15 天(2×7+1),
    不再被 2×retention=6 天清空 —— 周中后段的 vs 上周不再结构性失效。"""
    from shishi.schema import load_category

    config = load_category(
        {
            "id": "short",
            "name": "短保留",
            "schedule": "0 9 * * *",
            "sources": [
                {
                    "name": "s",
                    "url": "https://example.com/a",
                    "extract": {"type": "list", "item": "div.item",
                                "fields": {"title": "a.t", "url": "a.t@href",
                                           "price": "span.p"}},
                }
            ],
            "storage": {"retention": "3d", "vacuum": "never"},
            "baseline": {"enabled": True, "fields": ["price"], "windows": ["week"]},
        }
    )
    from shishi.pipeline import Pipeline

    pipeline = Pipeline(config, store=db_store)
    metrics_days = pipeline._metrics_retention_days(3)
    assert metrics_days == 15  # max(2×3, 2×7+1)

    # 10 天前的快照:按旧 2×retention(6 天)必被删;按窗口下限(15 天)保留。
    old = NOW - timedelta(days=10)
    db_store.save_metric("short", "k1", "price", 100.0, recorded_at=old)
    counts = db_store.cleanup_expired(
        3, now=NOW, metrics_retention_days=metrics_days
    )
    assert counts["metrics"] == 0  # 窗口下限内,不清
    baseline = db_store.get_metric_baseline("short", "k1", "price", now=NOW, window="week", tz=TZ)
    assert baseline is not None and baseline.value == 100.0

    # 对照:不传声明下限(调用方旧契约)→ 2×3=6 天,10 天前快照被清。
    counts = db_store.cleanup_expired(3, now=NOW)
    assert counts["metrics"] == 1


def test_baseline_fields_typo_rejected_at_load_time():
    """baseline.fields 拼错(prce)装载即拒:静默零快照的漂移在加载期可检出
    (与 dedup.key 占位符校验同一 fail-fast 先例)。"""
    from shishi.schema import load_category

    data = {
        "id": "typo",
        "name": "拼错",
        "schedule": "0 9 * * *",
        "sources": [
            {
                "name": "s",
                "url": "https://example.com/a",
                "extract": {"type": "list", "item": "div.item",
                            "fields": {"title": "a.t", "url": "a.t@href",
                                       "price": "span.p"}},
            }
        ],
        "baseline": {"enabled": True, "fields": ["prce"], "windows": ["day"]},
    }
    with pytest.raises(Exception) as excinfo:
        load_category(data)
    assert "prce" in str(excinfo.value) and "$.baseline.fields" in str(excinfo.value)


def test_official_gpu_prices_plugin_baseline_fields_resolve():
    """官方插件是趋势基线的旗舰样例:其 baseline.fields 必须真的可由源产出
    (抽取层已把 '¥19800' 类文本规整为数值,见 test_fetch_base 数值规整)。"""
    from shishi.schema import load_category_file

    config = load_category_file(GPU_PRICES)
    assert config.baseline is not None and config.baseline.enabled
    produced: set[str] = set()
    for source in config.sources:
        if source.extract is not None:
            produced.update(source.extract.fields)
    assert set(config.baseline.fields) <= produced
