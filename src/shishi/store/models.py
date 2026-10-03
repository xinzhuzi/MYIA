"""Data models and vocabulary shared by all storage implementations.

Store methods accept and return these typed records; serialization
(JSON columns, ISO-8601 UTC timestamps) stays inside each implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# AM/PM push slots (grill Q3, PRD v01-store-dedup): the local day splits at
# 12:00 — AM = 00:00-11:59, PM = 12:00-23:59. digest and immediate pushes
# share one registry; this layer answers "发没发过", push route answers
# "推不推".
SLOT_AM = "am"
SLOT_PM = "pm"
PUSH_SLOTS = frozenset({SLOT_AM, SLOT_PM})

# Feedback verdicts (feedback table, PRD 10-01-v03-feedback-loop): the
# push-card 回写 is a binary judgement — 有价值 / 没价值.
FEEDBACK_GOOD = "good"
FEEDBACK_BAD = "bad"
FEEDBACK_VERDICTS = frozenset({FEEDBACK_GOOD, FEEDBACK_BAD})

# Receiving channels of one feedback (feedback.channel): CLI manual marking
# and the two channel-specific callback paths (grill Q7 分形态接收).
# desktop = 桌面 app 卡片 👍/👎(v1.1.2 桌面对齐批 B2;channel 为自由串无词表
# 校验,此常数只作常量家与 CLI/桌面互认锚点 —— `myia feedback list` 无过滤即见)。
FEEDBACK_CHANNEL_CLI = "cli"
FEEDBACK_CHANNEL_TELEGRAM = "telegram"
FEEDBACK_CHANNEL_FEISHU = "feishu"
FEEDBACK_CHANNEL_DESKTOP = "desktop"

# Tuning adjustment kinds (feedback_tuning table). The feedback loop's
# periodic task appends rows here; the LATEST row per key (word / category)
# is the active adjustment, so history stays fully traceable (可追溯).
TUNING_MUTE_WEIGHT = "mute_weight"  # payload: {word, weight, bad_count, ratio}
TUNING_CATEGORY_PENALTY = "category_penalty"  # payload: {category, weight, ...}
TUNING_PROMPT_NOTE = "prompt_note"  # payload: {note, categories, words}

# Pipeline run statuses (runs table). Exit-code mapping (0/1/2/3) lives in
# the CLI layer, not here.
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = frozenset(
    {RUN_STATUS_RUNNING, RUN_STATUS_SUCCESS, RUN_STATUS_PARTIAL, RUN_STATUS_FAILED}
)

# Trend-baseline compare windows (metric_history, PRD 10-01-v04-trend-baseline):
# ``day`` compares against the newest snapshot recorded before today's local
# midnight (vs 昨日), ``week`` against the newest before this ISO week's
# Monday 00:00 (vs 上周).
METRIC_WINDOW_DAY = "day"
METRIC_WINDOW_WEEK = "week"
METRIC_WINDOWS = frozenset({METRIC_WINDOW_DAY, METRIC_WINDOW_WEEK})

# Per-step progress recorded in ``runs.steps`` (JSON column, v0.2 storage
# hardening). ``ok``/``failed`` are written when a stage completes or exhausts
# its retries; ``skipped`` marks stages bypassed (upstream failed); ``resumed``
# marks steps inherited from an interrupted predecessor run (断点续跑) -- the
# payload travels with the entry so the resumed row stays self-sufficient.
STEP_STATUS_OK = "ok"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_SKIPPED = "skipped"
STEP_STATUS_RESUMED = "resumed"
STEP_STATUSES = frozenset(
    {STEP_STATUS_OK, STEP_STATUS_FAILED, STEP_STATUS_SKIPPED, STEP_STATUS_RESUMED}
)


@dataclass(slots=True)
class ItemRecord:
    """One intelligence entry persisted in the ``items`` table."""

    url: str
    dedup_key: str
    title: str
    source: str | None = None
    content: str | None = None  # sanitized digest of the body, not the full text
    content_hash: str | None = None
    tags: list[str] = field(default_factory=list)
    category: str | None = None
    scores: dict | None = None  # reserved column for v0.1 score backfill
    pushed_at: datetime | None = None
    push_slot: str | None = None
    first_seen: datetime | None = None  # filled by the store on save when absent
    raw: dict | None = None  # optional JSON payload
    id: int | None = None


@dataclass(slots=True)
class DedupEntry:
    """One ``dedup_registry`` row: a seen key plus its last push info."""

    key: str
    first_seen: datetime
    last_pushed_at: datetime | None = None
    last_push_slot: str | None = None


@dataclass(slots=True)
class ChangeBaseline:
    """One ``change_baseline`` row: per-URL change fingerprint for fetch_base."""

    url: str
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    last_changed: datetime | None = None


@dataclass(slots=True)
class EngineHint:
    """One ``engine_hints`` row: the engine that last succeeded for a source."""

    source_key: str
    engine: str
    updated_at: datetime


@dataclass(slots=True)
class MetricRecord:
    """One ``metric_history`` row: a category-level numeric snapshot.

    趋势基线的历史底座(PRD 10-01-v04-trend-baseline):条目级数值字段(如
    显卡 ``price``)按 ``(category, metric_key, field)`` 逐 run 存快照;品类
    级统计(关键词提及量)也走同一张表,约定 ``metric_key = "keyword:<词>"``、
    ``field = "mentions"``、值为该 run 的命中条目数。窗口对比
    (vs 昨日/上周)在查询时按 ``recorded_at`` 与本地日/ISO 周边界计算。
    """

    category: str
    metric_key: str  # 条目 dedup_key / url,或品类级约定键 keyword:<词>
    field: str  # 数值字段名(baseline.fields),或品类级 "mentions"
    value: float
    recorded_at: datetime | None = None  # filled by the store on save when absent
    id: int | None = None


@dataclass(slots=True)
class FeedbackRecord:
    """One ``feedback`` row: a good/bad verdict on a pushed item.

    ``item_id`` is the ``items.id`` association; ``dedup_key`` is stored
    redundantly (plus ``title`` / ``category`` snapshots) so per-word and
    per-category statistics keep working after retention prunes the item row
    itself. ``channel`` records which receiving path delivered the verdict
    (cli / telegram / feishu).
    """

    dedup_key: str
    verdict: str  # good | bad (FEEDBACK_VERDICTS)
    channel: str  # cli | telegram | feishu
    item_id: int | None = None
    title: str | None = None  # snapshot at feedback time (word stats)
    category: str | None = None  # snapshot at feedback time (category stats)
    #: provider event identity (TG update_id / Feishu event_id; NULL for CLI).
    #: Unique per (channel, external_id): 双击/平台重试/进程重启重放只记一次
    #: (幂等去重,防「一次点击凑满 min_bad_count」击穿护栏)。
    external_id: str | None = None
    created_at: datetime | None = None  # filled by the store on save when absent
    id: int | None = None


@dataclass(slots=True)
class TuningRecord:
    """One ``feedback_tuning`` row: an append-only parameter adjustment.

    Rows are never updated or deleted (调整历史可追溯); the active adjustment
    for a word/category is the newest matching row. ``payload`` is a
    JSON-serializable dict whose shape depends on ``kind``.
    """

    kind: str  # mute_weight | category_penalty | prompt_note
    payload: dict
    created_at: datetime | None = None  # filled by the store on save when absent
    id: int | None = None


@dataclass(slots=True)
class RunRecord:
    """One ``runs`` row: a pipeline execution record.

    ``steps`` carries per-step progress (断点续跑, v0.2 storage hardening):
    ``{step_name: {"status": ..., "completed_at": ..., "payload": ...}}``.
    Checkpoint payloads (fetched/classified/deduped items) let a rerun skip
    already-completed stages instead of re-fetching and re-classifying.
    """

    category: str
    started_at: datetime
    status: str
    finished_at: datetime | None = None
    stats: dict | None = None
    error: str | None = None
    steps: dict | None = None
    id: int | None = None
