"""Default SQLite backend for the pluggable Store interface.

Single-file database (default ``myia.db``, path configurable), WAL journal
mode for the single-process asyncio workload, ``busy_timeout`` so concurrent
writers from separate connections wait instead of failing with
"database is locked". A PostgreSQL backend plugs in behind
:class:`shishi.store.base.Store` in v0.2+ — no Redis/PG dependency in v0.1.

Schema versioning (v0.2 storage hardening): ``store_meta['schema_version']``
stamps the layout; opening a database runs idempotent migrations forward and
refuses — with :class:`shishi.store.errors.StoreSchemaError` — a file that is
not a usable SQLite database or was written by a *newer* MYIA. Versions:

- 1 — v0.1: items / dedup_registry / change_baseline / engine_hints / runs.
- 2 — v0.2 storage hardening: ``runs.steps`` JSON progress column
  (断点续跑) + ``store_meta`` key-value table (schema version, vacuum stamp).
- 3 — v0.2 LLM enrich (PRD 10-01-v02-enrich-llm): ``enrich_cache`` table
  (per-URL score cache, keyed by url+model+scores-fingerprint) — additive,
  legacy databases migrate without touching existing rows.
- 4 — v0.3 feedback loop (PRD 10-01-v03-feedback-loop): ``feedback`` table
  (good/bad verdicts on pushed items, associated with ``items`` via
  ``item_id`` + redundant ``dedup_key`` snapshot) and ``feedback_tuning``
  (append-only parameter-adjustment history) — additive, legacy databases
  migrate without touching existing rows.
- 5 — v0.4 trend baseline (PRD 10-01-v04-trend-baseline): ``metric_history``
  table (品类级数值快照:per-(category, metric_key, field) numeric history for
  the 「vs 昨日 / vs 上周」 comparison and keyword mention week-over-week) —
  additive, legacy databases migrate without touching existing rows.

Retention & vacuum: :meth:`SQLiteStore.cleanup_expired` deletes pushed items
after ``retention`` days (anchored at ``pushed_at``), never-pushed items after
a longer grace window (anchored at ``first_seen`` — undelivered content stays
one extra window for diagnosis), stale per-URL baselines/hints and old run
rows; numeric metric history deliberately outlives items
(``BASELINE_RETENTION_MULTIPLIER × retention``, 基线期长于条目期 — week
over week needs at least two full windows of history);
:meth:`SQLiteStore.maybe_vacuum` gates ``VACUUM`` by calendar cadence
against the ``last_vacuum_at`` stamp.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from collections.abc import Collection, Mapping
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from shishi.schema import VACUUM_CADENCES
from shishi.store.errors import StoreSchemaError
from shishi.store.models import (
    FEEDBACK_VERDICTS,
    METRIC_WINDOW_DAY,
    METRIC_WINDOWS,
    PUSH_SLOTS,
    RUN_STATUS_RUNNING,
    RUN_STATUSES,
    STEP_STATUSES,
    ChangeBaseline,
    DedupEntry,
    FeedbackRecord,
    ItemRecord,
    MetricRecord,
    RunRecord,
    TuningRecord,
)

logger = logging.getLogger(__name__)

#: Current layout version; bump + add a migration entry when the DDL changes.
SCHEMA_VERSION = 6

# 同进程并发「首次打开同一数据库」的串行化锁(见 SQLiteStore.__init__)。
_OPEN_LOCK = threading.Lock()

_META_SCHEMA_VERSION = "schema_version"
_META_LAST_VACUUM_AT = "last_vacuum_at"

#: Never-pushed items get this multiple of ``retention`` before cleanup:
#: delivered content has served its purpose, undelivered content stays one
#: extra window for doctor-style diagnosis / manual recovery (清理策略区分
#: 已推送与未推送条目, PRD 10-01-v02-storage-hardening).
UNPUSHED_RETENTION_MULTIPLIER = 2

#: 数值历史快照(metric_history)的保留期倍数:基线期必须长于条目期
#: (PRD 10-01-v04-trend-baseline)——周环比至少要两个完整窗口的历史,日环比
#: 亦然;按 2× retention 保留(默认 90d 条目 → 180d 数值基线)。
BASELINE_RETENTION_MULTIPLIER = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    source TEXT,
    title TEXT NOT NULL,
    content TEXT,
    content_hash TEXT,
    tags TEXT,          -- JSON array of strings
    category TEXT,
    scores TEXT,        -- JSON object, reserved for v0.1 score backfill
    pushed_at TEXT,     -- ISO-8601 UTC
    push_slot TEXT,     -- 'am' | 'pm'
    first_seen TEXT NOT NULL,
    raw TEXT            -- optional JSON object
);
CREATE INDEX IF NOT EXISTS idx_items_url ON items(url);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);

CREATE TABLE IF NOT EXISTS dedup_registry (
    key TEXT PRIMARY KEY,          -- composite dedup key or bare URL
    first_seen TEXT NOT NULL,
    last_pushed_at TEXT,
    last_push_slot TEXT
);

CREATE TABLE IF NOT EXISTS change_baseline (
    url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    content_hash TEXT,
    last_changed TEXT
);

CREATE TABLE IF NOT EXISTS engine_hints (
    source_key TEXT PRIMARY KEY,   -- stable per-source id (v0.1: source URL)
    engine TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,          -- running | success | partial | failed
    stats TEXT,                    -- JSON object
    error TEXT,
    steps TEXT                     -- JSON object: per-step progress (断点续跑)
);

CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,          -- schema_version / last_vacuum_at
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrich_cache (
    url TEXT NOT NULL,             -- cached item URL (同 URL 结果缓存)
    model TEXT NOT NULL,           -- scoring model (换模型即失效)
    scores_key TEXT NOT NULL,      -- dimensions + prompt-version fingerprint
    result TEXT NOT NULL,          -- JSON: {"scores": {...}, "score": x}
    created_at TEXT NOT NULL,      -- ISO-8601 UTC
    PRIMARY KEY (url, model, scores_key)
);
CREATE INDEX IF NOT EXISTS idx_enrich_cache_url ON enrich_cache(url);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,           -- items.id 关联(条目被 retention 清理后留 NULL 语义由快照兜底)
    dedup_key TEXT NOT NULL,   -- 条目稳定身份(冗余快照,items 清理后统计仍可用)
    verdict TEXT NOT NULL,     -- 'good' | 'bad'
    channel TEXT NOT NULL,     -- 'cli' | 'telegram' | 'feishu'
    title TEXT,                -- 入库时条目标题快照(负反馈词频统计)
    category TEXT,             -- 入库时条目类目快照(负反馈类目统计)
    external_id TEXT,          -- 渠道事件身份(TG update_id/飞书 event_id;CLI 为 NULL)
    created_at TEXT NOT NULL   -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_feedback_dedup_key ON feedback(dedup_key);
CREATE INDEX IF NOT EXISTS idx_feedback_verdict_created ON feedback(verdict, created_at);
-- 注意:(channel, external_id) 幂等唯一索引在 _migrate_v6 里创建 —— 旧库要先
-- ALTER 出 external_id 列,否则 _SCHEMA 直接建索引会 no such column。

CREATE TABLE IF NOT EXISTS feedback_tuning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,        -- mute_weight | category_penalty | prompt_note
    payload TEXT NOT NULL,     -- JSON(词/类目/权重/依据计数,自描述)
    created_at TEXT NOT NULL   -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS metric_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,    -- 品类 id
    metric_key TEXT NOT NULL,  -- 条目 dedup_key/url,或品类级约定键 keyword:<词>
    field TEXT NOT NULL,       -- 数值字段名(baseline.fields)或 "mentions"
    value REAL NOT NULL,       -- 数值快照(finite;NaN/inf 拒写)
    recorded_at TEXT NOT NULL  -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_metric_history_lookup
    ON metric_history(category, metric_key, field, recorded_at);
"""


def _migrate_v2_add_run_steps(conn: sqlite3.Connection) -> None:
    """v1 → v2: add ``runs.steps`` (断点续跑进度).

    Idempotent: fresh v2 databases already created the column via
    ``_SCHEMA``; legacy v0.1 databases get it here. Rows predate the column
    and keep ``steps = NULL`` (no progress = nothing resumable, still valid).
    """
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "steps" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN steps TEXT")
        logger.info("存储迁移完成: runs 表新增 steps 列(断点续跑)")


def _migrate_v3_add_enrich_cache(conn: sqlite3.Connection) -> None:
    """v2 → v3: add ``enrich_cache`` (LLM 精评缓存表, PRD 10-01-v02-enrich-llm).

    Idempotent and purely additive: fresh v3 databases already have the table
    via ``_SCHEMA``; existing tables and rows are untouched, so a v2 database
    migrates with zero data loss (迁移兼容).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS enrich_cache (
            url TEXT NOT NULL,
            model TEXT NOT NULL,
            scores_key TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (url, model, scores_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enrich_cache_url ON enrich_cache(url)")
    logger.info("存储迁移完成: 新增 enrich_cache 表(LLM 精评缓存)")


def _migrate_v4_add_feedback(conn: sqlite3.Connection) -> None:
    """v3 → v4: add ``feedback`` + ``feedback_tuning`` (反馈闭环, PRD 10-01-v03-feedback-loop).

    Idempotent and purely additive: fresh v4 databases already have both
    tables via ``_SCHEMA``; existing tables and rows are untouched, so a v3
    database migrates with zero data loss (迁移兼容).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            dedup_key TEXT NOT NULL,
            verdict TEXT NOT NULL,
            channel TEXT NOT NULL,
            title TEXT,
            category TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_dedup_key ON feedback(dedup_key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_verdict_created ON feedback(verdict, created_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_tuning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    logger.info("存储迁移完成: 新增 feedback / feedback_tuning 表(反馈闭环)")


def _migrate_v5_add_metric_history(conn: sqlite3.Connection) -> None:
    """v4 → v5: add ``metric_history`` (品类级数值快照, PRD 10-01-v04-trend-baseline).

    Idempotent and purely additive: fresh v5 databases already have the table
    via ``_SCHEMA``; existing tables and rows are untouched, so a v4 database
    migrates with zero data loss (迁移兼容).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            field TEXT NOT NULL,
            value REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metric_history_lookup "
        "ON metric_history(category, metric_key, field, recorded_at)"
    )
    logger.info("存储迁移完成: 新增 metric_history 表(趋势基线数值快照)")


def _migrate_v6_add_feedback_external_id(conn: sqlite3.Connection) -> None:
    """v5 → v6: ``feedback.external_id`` + (channel, external_id) 唯一索引.

    幂等去重(PRD 10-01-v03-feedback-loop 护栏:「one angry click must not
    mute a word forever」——一次双击/平台重试/重启重放不得记两条)。纯增量:
    ALTER 前先查列,旧库零数据损失;CLI 行 external_id 为 NULL,不受唯一索引
    约束(SQLite UNIQUE 对 NULL 放行),历史行为不变。
    """
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(feedback)").fetchall()
    }
    if columns and "external_id" not in columns:
        conn.execute("ALTER TABLE feedback ADD COLUMN external_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_external_id "
        "ON feedback(channel, external_id)"
    )
    logger.info("存储迁移完成: feedback.external_id 幂等去重列与唯一索引")


#: target version → migration (runs with the connection inside the caller's
#: transaction; every migration must be idempotent — fresh databases replay
#: them after ``CREATE TABLE IF NOT EXISTS`` already produced the new shape).
_MIGRATIONS: dict[int, object] = {
    2: _migrate_v2_add_run_steps,
    3: _migrate_v3_add_enrich_cache,
    4: _migrate_v4_add_feedback,
    5: _migrate_v5_add_metric_history,
    6: _migrate_v6_add_feedback_external_id,
}


def _to_iso(value: datetime | None) -> str | None:
    """Serialize an aware datetime to ISO-8601 UTC (naive input assumed UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp back to an aware datetime (UTC fallback)."""
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _json_dumps(value: object) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _json_loads(raw: str | None) -> object | None:
    return None if not raw else json.loads(raw)


def _cadence_elapsed(cadence: str, last: datetime, now: datetime) -> bool:
    """Calendar-based cadence check (UTC): did ``cadence`` elapse since ``last``?

    daily = a later date, weekly = a later ISO week, monthly = a later
    (year, month) — calendar semantics without extra dependencies.
    """
    if cadence == "daily":
        return last.date() != now.date()
    if cadence == "weekly":
        return last.isocalendar()[:2] != now.isocalendar()[:2]
    if cadence == "monthly":
        return (last.year, last.month) != (now.year, now.month)
    raise ValueError(f"字段校验失败: vacuum 周期必须是 {sorted(VACUUM_CADENCES)} 之一,得到 {cadence!r}")


def _row_to_item(row: sqlite3.Row) -> ItemRecord:
    return ItemRecord(
        id=int(row["id"]),
        url=row["url"],
        dedup_key=row["dedup_key"],
        source=row["source"],
        title=row["title"],
        content=row["content"],
        content_hash=row["content_hash"],
        tags=_json_loads(row["tags"]) or [],
        category=row["category"],
        scores=_json_loads(row["scores"]),
        pushed_at=_from_iso(row["pushed_at"]),
        push_slot=row["push_slot"],
        first_seen=_from_iso(row["first_seen"]),
        raw=_json_loads(row["raw"]),
    )


def _row_to_feedback(row: sqlite3.Row) -> FeedbackRecord:
    return FeedbackRecord(
        id=int(row["id"]),
        item_id=row["item_id"] if row["item_id"] is not None else None,
        dedup_key=row["dedup_key"],
        verdict=row["verdict"],
        channel=row["channel"],
        title=row["title"],
        category=row["category"],
        created_at=_from_iso(row["created_at"]),
    )


def _row_to_tuning(row: sqlite3.Row) -> TuningRecord:
    payload = _json_loads(row["payload"])
    return TuningRecord(
        id=int(row["id"]),
        kind=row["kind"],
        payload=payload if isinstance(payload, dict) else {},
        created_at=_from_iso(row["created_at"]),
    )


def _row_to_metric(row: sqlite3.Row) -> MetricRecord:
    return MetricRecord(
        id=int(row["id"]),
        category=row["category"],
        metric_key=row["metric_key"],
        field=row["field"],
        value=float(row["value"]),
        recorded_at=_from_iso(row["recorded_at"]),
    )


def _require_finite_number(value: object, label: str) -> float:
    """Validate one numeric snapshot value (bool 不是数值;NaN/inf 拒写)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"字段校验失败: {label} 必须是数值,得到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"字段校验失败: {label} 必须是有限数值,得到 {value!r}")
    return number


def metric_window_start(now: datetime, window: str, tz: tzinfo | None = None) -> datetime:
    """Local start of the compare window containing ``now`` (趋势基线窗口数学).

    ``day`` = 当日本地零点(vs 昨日的分界),``week`` = 本 ISO 周周一零点
    (vs 上周的分界)。naive ``now`` 按 UTC 解释(store 全线约定);返回值
    携带 ``tz``(缺省 UTC),序列化比较前统一转 UTC。供
    :meth:`SQLiteStore.get_metric_baseline` 与推送层关键词周环比共用同一份
    窗口语义(单一实现,两处不漂)。

    Raises:
        ValueError: ``window`` 不在 :data:`shishi.store.models.METRIC_WINDOWS`。
    """
    if window not in METRIC_WINDOWS:
        raise ValueError(
            f"字段校验失败: 对比窗口必须是 {sorted(METRIC_WINDOWS)} 之一,得到 {window!r}"
        )
    zone = tz or timezone.utc
    aware = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    local = aware.astimezone(zone)
    if window == METRIC_WINDOW_DAY:
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = local.date() - timedelta(days=local.weekday())  # ISO 周:周一为零点
    return datetime.combine(monday, datetime.min.time(), tzinfo=zone)


class SQLiteStore:
    """SQLite-backed :class:`shishi.store.base.Store`.

    Safe for cross-thread use within one process (guarded writes plus
    ``busy_timeout`` for other connections opening the same file).
    """

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            # 并发首次开库串行化:journal_mode=WAL 换模式需要独占锁,且
            # busy handler 对该操作不重试(busy_timeout 挡不住),同进程
            # 多线程同时构造实例必有一个撞 database is locked。
            with _OPEN_LOCK:
                self._configure()
        except sqlite3.DatabaseError as exc:
            # PRAGMA journal_mode 是第一条真正触碰文件_bytes 的语句:
            # 损坏文件在这里暴露,统一转结构化错误(错误链保留)。
            raise self._corrupt_error(exc) from exc
        self.init_schema()

    def _corrupt_error(self, exc: sqlite3.DatabaseError) -> StoreSchemaError:
        return StoreSchemaError(
            "store_corrupt",
            f"数据库文件无法使用(损坏或不是 SQLite 库): {self.sqlite_path}({exc})",
            details={"path": str(self.sqlite_path)},
        )

    # ------------------------------------------------------------------ setup

    def _configure(self) -> None:
        # busy_timeout 必须最先设置:journal_mode=WAL 要拿写锁,并发首次开库时
        # 若另一条连接正在迁移/建表,未设超时的连接会立刻 database is locked。
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def init_schema(self) -> None:
        """Create/upgrade the layout, validating the schema version.

        Raises:
            StoreSchemaError: the file is not a usable SQLite database
                (``store_corrupt``, original error chained as ``__cause__``),
                or its ``schema_version`` is newer than this binary knows
                (``schema_version_newer``).
        """
        # executescript 之前先看库里有没有我们的表:区分「全新文件」与
        # 「无版本戳的 v0.1 旧库」——前者只是首次盖章,不应谎报迁移。
        legacy = (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
            ).fetchone()
            is not None
        )
        try:
            with self._lock:
                self.conn.executescript(_SCHEMA)
                self._migrate(legacy=legacy)
                self.conn.commit()
        except sqlite3.DatabaseError as exc:
            raise self._corrupt_error(exc) from exc
        mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
        logger.debug("存储已初始化 path=%s journal_mode=%s", self.sqlite_path, mode)

    # ------------------------------------------------------------- migrations

    def _table_columns(self, table: str) -> list[str]:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row[1]) for row in rows]

    def _migrate(self, *, legacy: bool) -> None:
        """Bring an opened database forward to :data:`SCHEMA_VERSION`.

        Version-stamp absence means a pre-hardening (v0.1) database
        (``legacy``) or a fresh file — both are migrated idempotently (each
        step checks before it ALTERs) and then stamped; only the former logs
        a migration line. Newer-than-binary versions are refused: silently
        misreading a future layout would corrupt user data.
        """
        raw = self.conn.execute(
            "SELECT value FROM store_meta WHERE key = ?", (_META_SCHEMA_VERSION,)
        ).fetchone()
        current = int(raw[0]) if raw is not None else 1
        if current > SCHEMA_VERSION:
            raise StoreSchemaError(
                "schema_version_newer",
                f"数据库 schema 版本过新: 库文件为 v{current},当前程序支持到 "
                f"v{SCHEMA_VERSION}(请升级 MYIA,不要降级打开新库)",
                details={"path": str(self.sqlite_path), "expected": SCHEMA_VERSION, "found": current},
            )
        if current < SCHEMA_VERSION and legacy:
            logger.info("存储迁移: v%s → v%s path=%s", current, SCHEMA_VERSION, self.sqlite_path)
        for version in range(current, SCHEMA_VERSION):
            migration = _MIGRATIONS.get(version + 1)
            if migration is None:  # 防御:没有登记迁移路径的版本缺口
                raise StoreSchemaError(
                    "schema_version_mismatch",
                    f"数据库 schema 版本 v{current} 缺少到 v{SCHEMA_VERSION} 的迁移路径",
                    details={"path": str(self.sqlite_path), "expected": SCHEMA_VERSION, "found": current},
                )
            migration(self.conn)
        self.conn.execute(
            "INSERT INTO store_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_META_SCHEMA_VERSION, str(SCHEMA_VERSION)),
        )

    # -------------------------------------------------------------- key-value

    def get_meta(self, key: str) -> str | None:
        """Return the ``store_meta`` value for ``key``, or None when absent."""
        row = self._query_one("SELECT value FROM store_meta WHERE key = ?", (key,))
        return row["value"] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        """Upsert one ``store_meta`` value (schema version, vacuum stamp)."""
        if not key:
            raise ValueError("字段校验失败: store_meta.key 不能为空")
        self._write(
            "INSERT INTO store_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -------------------------------------------------------------- internals

    def _query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def _query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor

    # ------------------------------------------------------------------ items

    def save_item(self, item: ItemRecord) -> int:
        if not item.url:
            raise ValueError("字段校验失败: items.url 不能为空")
        if not item.dedup_key:
            raise ValueError("字段校验失败: items.dedup_key 不能为空")
        if not item.title:
            raise ValueError("字段校验失败: items.title 不能为空")
        first_seen = item.first_seen or datetime.now(timezone.utc)
        with self._lock:
            cursor = self.conn.execute(
                """
                INSERT INTO items (
                    url, dedup_key, source, title, content, content_hash,
                    tags, category, scores, pushed_at, push_slot, first_seen, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.url,
                    item.dedup_key,
                    item.source,
                    item.title,
                    item.content,
                    item.content_hash,
                    _json_dumps(item.tags),
                    item.category,
                    _json_dumps(item.scores),
                    _to_iso(item.pushed_at),
                    item.push_slot,
                    _to_iso(first_seen),
                    _json_dumps(item.raw),
                ),
            )
            item_id = int(cursor.lastrowid)
            self.conn.commit()
        logger.debug(
            "条目入库 url=%s dedup_key=%s item_id=%s", item.url, item.dedup_key, item_id
        )
        return item_id

    def get_item(self, item_id: int) -> ItemRecord | None:
        row = self._query_one("SELECT * FROM items WHERE id = ?", (item_id,))
        return _row_to_item(row) if row is not None else None

    def get_item_by_dedup_key(self, dedup_key: str) -> ItemRecord | None:
        """Return the newest item carrying ``dedup_key``, or None when absent.

        The feedback-loop lookup (CLI ``myia feedback mark <条目>``): the
        dedup key is the stable identity the pipeline assigns at the dedup
        stage. Dated-key templates can rotate, so multiple rows may share a
        historical key — newest wins.
        """
        if not dedup_key:
            raise ValueError("字段校验失败: items.dedup_key 不能为空")
        row = self._query_one(
            "SELECT * FROM items WHERE dedup_key = ? ORDER BY id DESC LIMIT 1",
            (dedup_key,),
        )
        return _row_to_item(row) if row is not None else None

    def list_items(
        self,
        *,
        category: str | None = None,
        since: datetime | None = None,
        before: datetime | None = None,
        before_id: int | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[ItemRecord]:
        """List items, newest first(桌面情报流分页的查询面).

        游标参数(10-03-v112-desktop-batch C1,与 feed-ux G1 合流形状):
        ``before`` = first_seen 严格小于;``before_id`` 与 ``before`` 组成
        ``(first_seen, id)`` 元组比较 —— 同刻(相同 first_seen)条目数超过
        单页 limit 时,单靠 ``before`` 会把同刻更旧条目整批跳过,复合游标
        才能推进直至取尽。``query`` = title/content/source 三列 LIKE
        (NOCASE,无索引单机万级可接受,如实注记)。
        """
        if limit is not None and limit < 0:
            raise ValueError(f"字段校验失败: limit 不能为负数,得到 {limit}")
        if before is None and before_id is not None:
            raise ValueError("字段校验失败: before_id 需与 before 同传(复合游标)")
        sql = "SELECT * FROM items"
        conditions: list[str] = []
        params: list[object] = []
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if since is not None:
            conditions.append("first_seen >= ?")
            params.append(_to_iso(since))
        if before is not None:
            if before_id is not None:
                conditions.append("(first_seen < ? OR (first_seen = ? AND id < ?))")
                params.extend([_to_iso(before), _to_iso(before), before_id])
            else:
                conditions.append("first_seen < ?")
                params.append(_to_iso(before))
        if query:
            # LIKE 转义:%/_ 按字面匹配(ESCAPE '\';方括号通配符非 SQLite 语法不涉)
            escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            like = f"%{escaped}%"
            conditions.append(
                "(title LIKE ? ESCAPE '\\' COLLATE NOCASE"
                " OR content LIKE ? ESCAPE '\\' COLLATE NOCASE"
                " OR source LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend([like, like, like])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY first_seen DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_item(row) for row in self._query_all(sql, tuple(params))]

    def daily_item_counts(
        self, *, days: int = 14, category: str | None = None
    ) -> list[tuple[str, int]]:
        """Per-day item counts for the last ``days`` days(采集量趋势,桌面 store.trend).

        Groups on ``substr(first_seen, 1, 10)``(UTC calendar day,如实口径 ——
        不做时区换算);窗口下界 = UTC now − days 天(仅作 SQL 过滤,零数日
        补齐归调用方/前端 ``fillDailyCounts``,store 只回有数日)。Returns
        ``[(date, count)]`` old → new,窗口内零条目时为空列表(合法空态)。

        Raises:
            ValueError: ``days`` 非正整数(与 list_feedback 的 limit 校验同口径)。
        """
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise ValueError(f"字段校验失败: days 必须为正整数,得到 {days!r}")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sql = (
            "SELECT substr(first_seen, 1, 10) AS day, COUNT(*) AS count "
            "FROM items WHERE first_seen IS NOT NULL AND first_seen >= ?"
        )
        params: list[object] = [since]
        if category is not None:
            sql += " AND category = ?"
            params.append(category)
        sql += " GROUP BY day ORDER BY day ASC"
        return [
            (str(row["day"]), int(row["count"]))
            for row in self._query_all(sql, tuple(params))
        ]

    def mark_item_pushed(
        self, item_id: int, slot: str, pushed_at: datetime | None = None
    ) -> None:
        if slot not in PUSH_SLOTS:
            raise ValueError(
                f"字段校验失败: push_slot 必须是 {sorted(PUSH_SLOTS)} 之一,得到 {slot!r}"
            )
        cursor = self._write(
            "UPDATE items SET pushed_at = ?, push_slot = ? WHERE id = ?",
            (_to_iso(pushed_at or datetime.now(timezone.utc)), slot, item_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"items 记录不存在: id={item_id}")
        logger.debug("条目推送登记 item_id=%s slot=%s", item_id, slot)

    def update_item_scores(self, dedup_key: str, scores: Mapping[str, object]) -> bool:
        """Backfill one item's ``scores`` JSON column (LLM 精评回填 items 表).

        Matches on ``dedup_key`` — the stable identity assigned at the dedup
        stage and stored on the row (rows are inserted before analyze runs).

        Args:
            dedup_key: the item's dedup key (must be non-empty).
            scores: JSON-serializable payload, e.g. ``{"value": 8,
                "relevance": 9, "credibility": 7, "score": 8.0}``.

        Returns:
            True when a row was updated, False when no item carries the key
            (caller decides whether that is worth a warning).

        Raises:
            ValueError: empty ``dedup_key`` or a non-mapping ``scores``.
        """
        if not dedup_key:
            raise ValueError("字段校验失败: items.dedup_key 不能为空")
        if not isinstance(scores, Mapping):
            raise ValueError(
                f"字段校验失败: scores 必须是键值映射,得到 {type(scores).__name__}"
            )
        cursor = self._write(
            "UPDATE items SET scores = ? WHERE dedup_key = ?",
            (_json_dumps(dict(scores)), dedup_key),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.debug("条目评分回填 dedup_key=%s dims=%s", dedup_key, sorted(scores))
        return updated

    # ---------------------------------------------------------- dedup_registry

    def get_dedup_entry(self, key: str) -> DedupEntry | None:
        row = self._query_one("SELECT * FROM dedup_registry WHERE key = ?", (key,))
        if row is None:
            return None
        return DedupEntry(
            key=row["key"],
            first_seen=_from_iso(row["first_seen"]),
            last_pushed_at=_from_iso(row["last_pushed_at"]),
            last_push_slot=row["last_push_slot"],
        )

    def mark_dedup_seen(self, key: str, first_seen: datetime) -> None:
        if not key:
            raise ValueError("字段校验失败: dedup_registry.key 不能为空")
        self._write(
            "INSERT OR IGNORE INTO dedup_registry (key, first_seen) VALUES (?, ?)",
            (key, _to_iso(first_seen)),
        )

    def record_dedup_push(self, key: str, pushed_at: datetime, slot: str) -> None:
        if not key:
            raise ValueError("字段校验失败: dedup_registry.key 不能为空")
        if slot not in PUSH_SLOTS:
            raise ValueError(
                f"字段校验失败: push_slot 必须是 {sorted(PUSH_SLOTS)} 之一,得到 {slot!r}"
            )
        self._write(
            """
            INSERT INTO dedup_registry (key, first_seen, last_pushed_at, last_push_slot)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                last_pushed_at = excluded.last_pushed_at,
                last_push_slot = excluded.last_push_slot
            """,
            (key, _to_iso(pushed_at), _to_iso(pushed_at), slot),
        )
        logger.debug("去重推送登记 key=%s slot=%s", key, slot)

    # ---------------------------------------------------------- change_baseline

    def get_baseline(self, url: str) -> ChangeBaseline | None:
        row = self._query_one("SELECT * FROM change_baseline WHERE url = ?", (url,))
        if row is None:
            return None
        return ChangeBaseline(
            url=row["url"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            content_hash=row["content_hash"],
            last_changed=_from_iso(row["last_changed"]),
        )

    def set_baseline(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        content_hash: str | None = None,
        last_changed: datetime | None = None,
    ) -> None:
        if not url:
            raise ValueError("字段校验失败: change_baseline.url 不能为空")
        self._write(
            """
            INSERT INTO change_baseline (url, etag, last_modified, content_hash, last_changed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                content_hash = excluded.content_hash,
                last_changed = excluded.last_changed
            """,
            (url, etag, last_modified, content_hash, _to_iso(last_changed)),
        )
        logger.debug("变更基线更新 url=%s etag=%s", url, bool(etag))

    # ------------------------------------------------------------- engine_hints

    def get_engine_hint(self, source_key: str) -> str | None:
        row = self._query_one(
            "SELECT engine FROM engine_hints WHERE source_key = ?", (source_key,)
        )
        return row["engine"] if row is not None else None

    def set_engine_hint(self, source_key: str, engine: str) -> None:
        if not source_key:
            raise ValueError("字段校验失败: engine_hints.source_key 不能为空")
        if not engine:
            raise ValueError("字段校验失败: engine_hints.engine 不能为空")
        self._write(
            """
            INSERT INTO engine_hints (source_key, engine, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                engine = excluded.engine,
                updated_at = excluded.updated_at
            """,
            (source_key, engine, _to_iso(datetime.now(timezone.utc))),
        )
        logger.debug("引擎提示回写 source_key=%s engine=%s", source_key, engine)

    def clear_engine_hint(self, source_key: str) -> None:
        self._write("DELETE FROM engine_hints WHERE source_key = ?", (source_key,))
        logger.debug("引擎提示清除 source_key=%s", source_key)

    # ----------------------------------------------------------- enrich_cache

    def get_enrich_cache(self, url: str, model: str, scores_key: str) -> dict | None:
        """Return the cached score result for one URL, or None on a miss.

        Cache identity is ``(url, model, scores_key)``: switching the model or
        the score dimensions / prompt version (both fingerprinted into
        ``scores_key``) invalidates old entries — schema 变更才重评 semantics.

        Raises:
            ValueError: any key component is empty.
        """
        if not url or not model or not scores_key:
            raise ValueError("字段校验失败: enrich_cache 查询键(url/model/scores_key)不能为空")
        row = self._query_one(
            "SELECT result FROM enrich_cache "
            "WHERE url = ? AND model = ? AND scores_key = ?",
            (url, model, scores_key),
        )
        if row is None:
            return None
        try:
            result = _json_loads(row["result"])
        except (TypeError, ValueError) as exc:
            logger.warning("enrich_cache 行损坏(按未命中处理) url=%s: %s", url, exc)
            return None
        return result if isinstance(result, dict) else None

    def set_enrich_cache(
        self,
        url: str,
        model: str,
        scores_key: str,
        result: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> None:
        """Upsert one cached score result (same URL re-score overwrites).

        Raises:
            ValueError: empty key component or a non-mapping ``result``.
        """
        if not url or not model or not scores_key:
            raise ValueError("字段校验失败: enrich_cache 写入键(url/model/scores_key)不能为空")
        if not isinstance(result, Mapping):
            raise ValueError(
                f"字段校验失败: enrich_cache.result 必须是键值映射,得到 {type(result).__name__}"
            )
        self._write(
            """
            INSERT INTO enrich_cache (url, model, scores_key, result, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url, model, scores_key) DO UPDATE SET
                result = excluded.result,
                created_at = excluded.created_at
            """,
            (
                url,
                model,
                scores_key,
                _json_dumps(dict(result)),
                _to_iso(created_at or datetime.now(timezone.utc)),
            ),
        )
        logger.debug("enrich 缓存写入 url=%s model=%s", url, model)

    def clear_enrich_cache(self, url: str | None = None) -> int:
        """Drop cached scores — manual invalidation (手动失效才重评).

        Args:
            url: drop only this URL's entries; ``None`` clears the whole table.

        Returns:
            Number of rows deleted.
        """
        if url is None:
            with self._lock:
                cursor = self.conn.execute("DELETE FROM enrich_cache")
                self.conn.commit()
        else:
            cursor = self._write("DELETE FROM enrich_cache WHERE url = ?", (url,))
        logger.info("enrich 缓存失效 url=%s deleted=%s", url or "(全部)", cursor.rowcount)
        return cursor.rowcount

    # ---------------------------------------------------------------- feedback

    def save_feedback(self, feedback: FeedbackRecord) -> int | None:
        """Insert one good/bad verdict; return its row id, ``None`` on duplicate.

        ``title`` / ``category`` snapshots travel on the record; the caller
        resolves them from the item (feedback package). Append-only: verdicts
        are never updated (同一卡片改判 = 新增一行,历史可追溯). Idempotent on
        the provider event identity: 同一 ``(channel, external_id)`` 只记一次
        —— 双击/平台重试/重启重放不再凑出第二条 bad(护栏「one angry click
        must not mute a word forever」不被一次双击击穿);``external_id`` 为
        NULL(CLI 手动路径)不受约束,恒插入。

        Returns:
            New row id, or ``None`` when a row with the same
            ``(channel, external_id)`` already exists (duplicate skipped).

        Raises:
            ValueError: empty ``dedup_key`` / ``channel``, or ``verdict``
                outside :data:`shishi.store.models.FEEDBACK_VERDICTS`.
        """
        if not feedback.dedup_key:
            raise ValueError("字段校验失败: feedback.dedup_key 不能为空")
        if feedback.verdict not in FEEDBACK_VERDICTS:
            raise ValueError(
                f"字段校验失败: feedback.verdict 必须是 {sorted(FEEDBACK_VERDICTS)} 之一,"
                f"得到 {feedback.verdict!r}"
            )
        if not feedback.channel:
            raise ValueError("字段校验失败: feedback.channel 不能为空")
        with self._lock:
            cursor = self.conn.execute(
                """
                INSERT INTO feedback (
                    item_id, dedup_key, verdict, channel, title, category,
                    external_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_id) DO NOTHING
                """,
                (
                    feedback.item_id,
                    feedback.dedup_key,
                    feedback.verdict,
                    feedback.channel,
                    feedback.title,
                    feedback.category,
                    feedback.external_id,
                    _to_iso(feedback.created_at or datetime.now(timezone.utc)),
                ),
            )
            inserted = cursor.rowcount > 0
            feedback_id = int(cursor.lastrowid) if inserted else None
            self.conn.commit()
        if inserted:
            logger.debug(
                "反馈入库 feedback_id=%s verdict=%s channel=%s dedup_key=%s",
                feedback_id, feedback.verdict, feedback.channel, feedback.dedup_key,
            )
        else:
            logger.info(
                "重复反馈已忽略(幂等) channel=%s external_id=%s dedup_key=%s",
                feedback.channel, feedback.external_id, feedback.dedup_key,
            )
        return feedback_id

    def list_feedback(
        self,
        *,
        verdict: str | None = None,
        channel: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FeedbackRecord]:
        """List feedback rows, newest first, with optional filters.

        Raises:
            ValueError: ``verdict`` given but outside the vocabulary, negative
                ``limit``.
        """
        if verdict is not None and verdict not in FEEDBACK_VERDICTS:
            raise ValueError(
                f"字段校验失败: verdict 必须是 {sorted(FEEDBACK_VERDICTS)} 之一,得到 {verdict!r}"
            )
        if limit is not None and limit < 0:
            raise ValueError(f"字段校验失败: limit 不能为负数,得到 {limit}")
        sql = "SELECT * FROM feedback"
        conditions: list[str] = []
        params: list[object] = []
        if verdict is not None:
            conditions.append("verdict = ?")
            params.append(verdict)
        if channel is not None:
            conditions.append("channel = ?")
            params.append(channel)
        if since is not None:
            conditions.append("created_at >= ?")
            params.append(_to_iso(since))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_feedback(row) for row in self._query_all(sql, tuple(params))]

    def save_tuning(self, adjustment: TuningRecord) -> int:
        """Append one tuning-adjustment row and return its id (append-only).

        Raises:
            ValueError: empty ``kind`` or a non-mapping ``payload``.
        """
        if not adjustment.kind:
            raise ValueError("字段校验失败: feedback_tuning.kind 不能为空")
        if not isinstance(adjustment.payload, Mapping):
            raise ValueError(
                f"字段校验失败: feedback_tuning.payload 必须是键值映射,"
                f"得到 {type(adjustment.payload).__name__}"
            )
        with self._lock:
            cursor = self.conn.execute(
                "INSERT INTO feedback_tuning (kind, payload, created_at) VALUES (?, ?, ?)",
                (
                    adjustment.kind,
                    _json_dumps(dict(adjustment.payload)),
                    _to_iso(adjustment.created_at or datetime.now(timezone.utc)),
                ),
            )
            adjustment_id = int(cursor.lastrowid)
            self.conn.commit()
        logger.debug(
            "调参历史入库 tuning_id=%s kind=%s payload_keys=%s",
            adjustment_id, adjustment.kind, sorted(adjustment.payload),
        )
        return adjustment_id

    def list_tuning(
        self, *, kind: str | None = None, limit: int | None = None
    ) -> list[TuningRecord]:
        """List tuning rows, newest first (调整历史), optionally by kind."""
        if limit is not None and limit < 0:
            raise ValueError(f"字段校验失败: limit 不能为负数,得到 {limit}")
        sql = "SELECT * FROM feedback_tuning"
        params: list[object] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_tuning(row) for row in self._query_all(sql, tuple(params))]

    # ---------------------------------------------------------- metric_history
    # Consumed by the v0.4 trend baseline (PRD 10-01-v04-trend-baseline):
    # numeric snapshots per (category, metric_key, field) power the
    # 「vs 昨日 / vs 上周」 template functions and keyword week-over-week.
    # Retention keeps this table longer than items (基线期长于条目期).

    def save_metric(
        self,
        category: str,
        metric_key: str,
        field: str,
        value: float,
        *,
        recorded_at: datetime | None = None,
    ) -> int:
        """Append one numeric snapshot and return its row id (append-only 历史).

        Raises:
            ValueError: empty ``category`` / ``metric_key`` / ``field``, or a
                non-numeric / non-finite ``value`` (bool 也不是数值).
        """
        if not category:
            raise ValueError("字段校验失败: metric_history.category 不能为空")
        if not metric_key:
            raise ValueError("字段校验失败: metric_history.metric_key 不能为空")
        if not field:
            raise ValueError("字段校验失败: metric_history.field 不能为空")
        number = _require_finite_number(value, "metric_history.value")
        with self._lock:
            cursor = self.conn.execute(
                "INSERT INTO metric_history (category, metric_key, field, value, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    category,
                    metric_key,
                    field,
                    number,
                    _to_iso(recorded_at or datetime.now(timezone.utc)),
                ),
            )
            self.conn.commit()
            metric_id = int(cursor.lastrowid)
        logger.debug(
            "数值快照入库 category=%s key=%s field=%s value=%s", category, metric_key, field, number
        )
        return metric_id

    def latest_metric(
        self, category: str, metric_key: str, field: str
    ) -> MetricRecord | None:
        """Return the newest snapshot for the key, or None when never recorded."""
        row = self._query_one(
            "SELECT * FROM metric_history "
            "WHERE category = ? AND metric_key = ? AND field = ? "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (category, metric_key, field),
        )
        return _row_to_metric(row) if row is not None else None

    def get_metric_baseline(
        self,
        category: str,
        metric_key: str,
        field: str,
        *,
        now: datetime,
        window: str,
        tz: tzinfo | None = None,
    ) -> MetricRecord | None:
        """Newest snapshot recorded strictly before the window start (窗口对比).

        ``window='day'`` → vs 昨日:the newest snapshot before 当地零点
        (today's own snapshots never answer as their own baseline);
        ``window='week'`` → vs 上周:the newest before 本 ISO 周周一零点.
        ``tz`` is the category schedule timezone (slot-boundary semantics
        follow the same zone); None means UTC. Naive ``now`` is read as UTC.

        Returns:
            The baseline snapshot, or None when the key has no history before
            the window start (first observation — callers render no
            comparison rather than inventing one).

        Raises:
            ValueError: ``window`` outside
                :data:`shishi.store.models.METRIC_WINDOWS`.
        """
        start = metric_window_start(now, window, tz)
        row = self._query_one(
            "SELECT * FROM metric_history "
            "WHERE category = ? AND metric_key = ? AND field = ? AND recorded_at < ? "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (category, metric_key, field, _to_iso(start)),
        )
        return _row_to_metric(row) if row is not None else None

    def sum_metrics(
        self,
        category: str,
        metric_key: str,
        field: str,
        *,
        since: datetime,
        until: datetime,
    ) -> float:
        """Sum snapshots recorded in the half-open ``[since, until)`` window.

        周环比的品类级聚合:per-run 关键词命中数逐 run 入库后,本周累计 =
        ``sum_metrics(week_start, next_week_start)``,上周累计同理(半开区间
        保证两次调用恰好铺满相邻两周、无重叠无遗漏)。

        Raises:
            ValueError: empty key components, or ``since >= until``.
        """
        if not category or not metric_key or not field:
            raise ValueError("字段校验失败: sum_metrics 查询键(category/metric_key/field)不能为空")
        if since >= until:
            raise ValueError(
                f"字段校验失败: sum_metrics 窗口为空(since={_to_iso(since)} >= until={_to_iso(until)})"
            )
        row = self._query_one(
            "SELECT COALESCE(SUM(value), 0.0) AS total FROM metric_history "
            "WHERE category = ? AND metric_key = ? AND field = ? "
            "AND recorded_at >= ? AND recorded_at < ?",
            (category, metric_key, field, _to_iso(since), _to_iso(until)),
        )
        return float(row["total"]) if row is not None else 0.0

    # -------------------------------------------------------------------- runs

    def start_run(self, category: str) -> int:
        if not category:
            raise ValueError("字段校验失败: runs.category 不能为空")
        with self._lock:
            cursor = self.conn.execute(
                "INSERT INTO runs (category, started_at, status) VALUES (?, ?, ?)",
                (category, _to_iso(datetime.now(timezone.utc)), RUN_STATUS_RUNNING),
            )
            run_id = int(cursor.lastrowid)
            self.conn.commit()
        logger.debug("run 开始 run_id=%s category=%s", run_id, category)
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        stats: dict | None = None,
        error: str | None = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(
                f"字段校验失败: runs.status 必须是 {sorted(RUN_STATUSES)} 之一,得到 {status!r}"
            )
        cursor = self._write(
            "UPDATE runs SET finished_at = ?, status = ?, stats = ?, error = ? WHERE id = ?",
            (_to_iso(datetime.now(timezone.utc)), status, _json_dumps(stats), error, run_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"runs 记录不存在: id={run_id}")
        logger.debug("run 结束 run_id=%s status=%s", run_id, status)

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        # sqlite3.Row 不是 dict:`in` 走 __iter__(值而非键),必须用 row.keys()。
        steps = _json_loads(row["steps"]) if "steps" in row.keys() else None  # noqa: SIM118
        return RunRecord(
            id=int(row["id"]),
            category=row["category"],
            started_at=_from_iso(row["started_at"]),
            status=row["status"],
            finished_at=_from_iso(row["finished_at"]),
            stats=_json_loads(row["stats"]),
            error=row["error"],
            steps=steps if isinstance(steps, dict) else None,
        )

    def list_runs(self, *, category: str | None = None, limit: int = 50) -> list[RunRecord]:
        """List persisted runs, newest first(桌面 runs.list 直读,C3).

        与内存注册表(entry.py ``_RUNS``)互补:重启后历史 run 由此可达。
        ``limit`` 负数即拒(照 :meth:`list_feedback` 口径)。
        """
        if limit < 0:
            raise ValueError(f"字段校验失败: limit 不能为负数,得到 {limit}")
        if category is not None:
            rows = self._query_all(
                "SELECT * FROM runs WHERE category = ? ORDER BY id DESC LIMIT ?",
                (category, limit),
            )
        else:
            rows = self._query_all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row_to_run(row) for row in rows]

    def get_run(self, run_id: int) -> RunRecord | None:
        row = self._query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._row_to_run(row) if row is not None else None

    def previous_run(self, category: str, *, before_run_id: int) -> RunRecord | None:
        """Return the most recent run of ``category`` with ``id < before_run_id``.

        The 断点续跑 lookup anchor: the pipeline asks "what did the run right
        before mine leave behind?" — a crashed (status ``running``) or
        failed/partial predecessor with recorded step checkpoints.
        """
        if not category:
            raise ValueError("字段校验失败: runs.category 不能为空")
        row = self._query_one(
            "SELECT * FROM runs WHERE category = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (category, before_run_id),
        )
        return self._row_to_run(row) if row is not None else None

    def update_run_step(
        self,
        run_id: int,
        step: str,
        *,
        status: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Record one pipeline step's progress on the ``runs.steps`` JSON column.

        Existing entries for other steps are preserved (read-modify-write
        under the connection lock). ``payload`` is the resumable checkpoint
        (e.g. ``{"items": [...]}``) — it must be JSON-serializable.

        Raises:
            ValueError: unknown ``run_id``, empty ``step``, ``status`` outside
                :data:`shishi.store.models.STEP_STATUSES`, or a non-mapping
                payload.
        """
        if not step:
            raise ValueError("字段校验失败: runs.steps.step 不能为空")
        if status not in STEP_STATUSES:
            raise ValueError(
                f"字段校验失败: 步骤状态必须是 {sorted(STEP_STATUSES)} 之一,得到 {status!r}"
            )
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError(
                f"字段校验失败: 步骤 payload 必须是键值映射或 None,得到 {type(payload).__name__}"
            )
        with self._lock:
            row = self.conn.execute("SELECT steps FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"runs 记录不存在: id={run_id}")
            try:
                steps: dict[str, object] = dict(_json_loads(row["steps"]) or {})
            except (TypeError, ValueError) as exc:
                raise ValueError(f"runs.steps 已有数据无法解析 run_id={run_id}: {exc}") from exc
            entry: dict[str, object] = {
                "status": status,
                "completed_at": _to_iso(datetime.now(timezone.utc)),
            }
            if payload is not None:
                entry["payload"] = dict(payload)
            steps[step] = entry
            self.conn.execute(
                "UPDATE runs SET steps = ? WHERE id = ?",
                (json.dumps(steps, ensure_ascii=False), run_id),
            )
            self.conn.commit()
        logger.debug("run 步骤进度 run_id=%s step=%s status=%s", run_id, step, status)

    def latest_run(self, category: str | None = None) -> RunRecord | None:
        if category is None:
            row = self._query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        else:
            row = self._query_one(
                "SELECT * FROM runs WHERE category = ? ORDER BY id DESC LIMIT 1",
                (category,),
            )
        return self._row_to_run(row) if row is not None else None

    # ------------------------------------------------------- retention/vacuum

    def cleanup_expired(
        self,
        retention_days: int,
        *,
        now: datetime | None = None,
        active_urls: Collection[str] | None = None,
        metrics_retention_days: int | None = None,
    ) -> dict[str, int]:
        """Delete retention-expired rows; return per-table deletion counts.

        Policy (区分已推送与未推送条目):

        - pushed items — anchored at ``pushed_at``, dropped after
          ``retention_days`` (delivered content has served its purpose);
        - never-pushed items — anchored at ``first_seen``, kept for
          ``UNPUSHED_RETENTION_MULTIPLIER × retention_days`` (undelivered
          content stays one extra window for diagnosis/recovery);
        - change baselines + engine hints — per-URL caches, pruned ONLY via an
          explicit ``active_urls`` set (the caller must own the whole DB
          namespace: one shared ``myia.db`` serves every category, so a single
          category's source list is NOT authoritative for "stale URL").
          Without ``active_urls`` they are left untouched: rows are tiny (one
          per source URL ever seen), and both wrong kinds of deletion are
          catastrophic — cross-category pruning kills other categories'
          fingerprints / engine learning, and age-pruning by ``last_changed``
          kills quiet sources (the stamp only moves when content actually
          changes);
        - runs — finished rows and stale ``running`` rows (crashed processes)
          after ``retention_days`` (checkpoint payloads are the bulk);
        - metric_history (数值基线) — after
          ``BASELINE_RETENTION_MULTIPLIER × retention_days`` anchored at
          ``recorded_at``: 基线期长于条目期 — week-over-week (and day-over-day)
          comparisons need at least two full windows of history to survive
          (PRD 10-01-v04-trend-baseline retention 协同).

        All timestamps compared as normalized ISO-8601 UTC strings.

        Args:
            retention_days: retention window in days; must be >= 1.
            now: evaluation instant; defaults to the current UTC time.
            active_urls: currently configured source URLs across the WHOLE
                database (baselines/hints outside the set are stale); None —
                the safe default for the shared multi-category DB — leaves
                both URL-keyed caches alone.

            metrics_retention_days: ``metric_history`` retention override;
                ``None`` keeps the ``BASELINE_RETENTION_MULTIPLIER ×
                retention_days`` default. Pipeline 传入声明窗口下限
                (两个完整窗口 + 余量),短 retention + week 窗口时防基线被清空.

        Returns:
            Counts: ``items_pushed`` / ``items_unpushed`` / ``baselines`` /
            ``engine_hints`` / ``runs`` / ``enrich_cache`` / ``metrics``.

        Raises:
            ValueError: ``retention_days`` < 1.
        """
        if retention_days < 1:
            raise ValueError(f"字段校验失败: retention_days 必须为正整数,得到 {retention_days}")
        now = now or datetime.now(timezone.utc)
        pushed_cutoff = _to_iso(now - timedelta(days=retention_days))
        unpushed_cutoff = _to_iso(
            now - timedelta(days=retention_days * UNPUSHED_RETENTION_MULTIPLIER)
        )
        counts: dict[str, int] = {}
        with self._lock:
            counts["items_pushed"] = self.conn.execute(
                "DELETE FROM items WHERE pushed_at IS NOT NULL AND pushed_at < ?",
                (pushed_cutoff,),
            ).rowcount
            counts["items_unpushed"] = self.conn.execute(
                "DELETE FROM items WHERE pushed_at IS NULL AND first_seen < ?",
                (unpushed_cutoff,),
            ).rowcount
            if active_urls is not None:
                counts["baselines"] = self._delete_stale_urls(
                    "change_baseline", "url", active_urls
                )
                counts["engine_hints"] = self._delete_stale_urls(
                    "engine_hints", "source_key", active_urls
                )
            else:
                # 共享库安全缺省:同一 myia.db 服务所有品类,单品类的源清单
                # 无权判定「别的 URL 已失效」;而 last_changed 只在内容真变时
                # 刷新,按龄删会误杀安静源的指纹 skip。URL 缓存行极小(每源
                # 一行)、误删代价是两套核心缓存机制失效,故只在调用方显式给
                # 出全库 active_urls 时才修剪。
                counts["baselines"] = 0
                counts["engine_hints"] = 0
            counts["runs"] = self.conn.execute(
                "DELETE FROM runs WHERE "
                "(finished_at IS NOT NULL AND finished_at < ?) OR "
                f"(status = '{RUN_STATUS_RUNNING}' AND started_at < ?)",
                (pushed_cutoff, pushed_cutoff),
            ).rowcount
            # enrich_cache 是纯缓存(命中省 token,未命中重评即可):随 retention
            # 老化清除,防长期桌面运行无界膨胀(2026-10 复盘:该表此前无任何
            # 过期路径,clear_enrich_cache 只能手动调用)。
            counts["enrich_cache"] = self.conn.execute(
                "DELETE FROM enrich_cache WHERE created_at < ?",
                (pushed_cutoff,),
            ).rowcount
            # metric_history(趋势基线)保留期 = max(2× retention, 调用方给的
            # 声明窗口下限):基线期长于条目期,且必须覆盖「两个完整声明窗口 +
            # 余量」—— 否则 retention: 3d + windows: [week] 时,周中后段的
            # 「窗口开始前最新快照」被清空,vs 上周结构性失效且无告警。纯数值
            # 行极小,老化上界仍需存在(防长期运行无界膨胀)。
            metrics_days = (
                metrics_retention_days
                if metrics_retention_days is not None
                else retention_days * BASELINE_RETENTION_MULTIPLIER
            )
            baseline_cutoff = _to_iso(now - timedelta(days=metrics_days))
            counts["metrics"] = self.conn.execute(
                "DELETE FROM metric_history WHERE recorded_at < ?",
                (baseline_cutoff,),
            ).rowcount
            # 注意:dedup_registry 不做自动按龄删除——它是 all-time dedup
            # (is_seen)与同槽位防重发的底座,删行会让重新出现的旧条目被再次
            # 推送。手动治理走 :meth:`prune_dedup_registry`。
            self.conn.commit()
        total = sum(counts.values())
        log = logger.info if total else logger.debug
        log(
            "过期清理完成 retention_days=%s deleted=%s(items_pushed=%s items_unpushed=%s "
            "baselines=%s engine_hints=%s runs=%s enrich_cache=%s metrics=%s)",
            retention_days, total, counts["items_pushed"], counts["items_unpushed"],
            counts["baselines"], counts["engine_hints"], counts["runs"],
            counts["enrich_cache"], counts["metrics"],
        )
        return counts

    def _delete_stale_urls(
        self, table: str, column: str, active_urls: Collection[str]
    ) -> int:
        """Delete ``table`` rows whose key URL is not in ``active_urls``."""
        active = sorted(set(active_urls))
        if not active:  # 无活跃源 → 全部按过期处理
            return self.conn.execute(f"DELETE FROM {table}").rowcount
        placeholders = ", ".join("?" for _ in active)
        return self.conn.execute(
            f"DELETE FROM {table} WHERE {column} NOT IN ({placeholders})",
            tuple(active),
        ).rowcount

    def prune_dedup_registry(self, *, before: datetime) -> int:
        """Manually delete dedup_registry rows older than ``before``; return count.

        ⚠️ dedup_registry 承载两层语义:all-time dedup(:meth:`DedupRegistry.is_seen`,
        防同一 item 反复入库/推送)与同槽位防重发(should_send)。自动按龄删除会
        让「重新出现的旧条目」再次通过 dedup 被推送——防重发兜底失效(storage PRD
        明言依赖该表兜底)。因此**不挂自动策略**,只提供这个显式手动治理通道:
        调用方须确认 ``before`` 之前的条目确无重现风险(如 before 距今 < 1 天,
        槽位窗口之外的行)。

        Age anchor: ``COALESCE(last_pushed_at, first_seen)`` — a row last pushed
        (or first seen) before ``before`` is pruned.
        """
        cutoff = _to_iso(before)
        with self._lock:
            deleted = self.conn.execute(
                "DELETE FROM dedup_registry WHERE COALESCE(last_pushed_at, first_seen) < ?",
                (cutoff,),
            ).rowcount
            self.conn.commit()
        if deleted:
            logger.info("dedup_registry 手动修剪 before=%s deleted=%s", cutoff, deleted)
        return deleted

    def vacuum(self) -> None:
        """Rebuild the database file, reclaiming free pages (anti-bloat).

        VACUUM cannot run inside a transaction; pending writes are committed
        first. Page counts before/after land in the log (膨胀观测).
        """
        with self._lock:
            self.conn.commit()
            before = self.conn.execute("PRAGMA page_count").fetchone()[0]
            self.conn.execute("VACUUM")
            after = self.conn.execute("PRAGMA page_count").fetchone()[0]
        logger.info(
            "数据库 VACUUM 完成 path=%s page_count %s → %s",
            self.sqlite_path, before, after,
        )

    def maybe_vacuum(self, cadence: str, *, now: datetime | None = None) -> bool:
        """Run :meth:`vacuum` when the configured cadence is due.

        The ``last_vacuum_at`` stamp lives in ``store_meta``; cadence is
        calendar-based in UTC (daily = another date, weekly = another ISO
        week, monthly = another month), so "monthly" fires once per month no
        matter how often runs execute.

        Args:
            cadence: ``daily`` / ``weekly`` / ``monthly`` / ``never``
                (schema ``storage.vacuum`` vocabulary).
            now: evaluation instant; defaults to the current UTC time.

        Returns:
            True when a VACUUM ran, False when skipped (never / not yet due).

        Raises:
            ValueError: unknown cadence.
        """
        if cadence not in VACUUM_CADENCES:
            raise ValueError(
                f"字段校验失败: vacuum 周期必须是 {sorted(VACUUM_CADENCES)} 之一,得到 {cadence!r}"
            )
        now = now or datetime.now(timezone.utc)
        if cadence == "never":
            return False
        last_raw = self.get_meta(_META_LAST_VACUUM_AT)
        last = _from_iso(last_raw) if last_raw else None
        if last is not None and not _cadence_elapsed(cadence, last, now):
            logger.debug("VACUUM 未到周期 cadence=%s last=%s", cadence, last_raw)
            return False
        self.vacuum()
        self.set_meta(_META_LAST_VACUUM_AT, _to_iso(now) or "")
        logger.info("定时 VACUUM 已执行 cadence=%s", cadence)
        return True

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
