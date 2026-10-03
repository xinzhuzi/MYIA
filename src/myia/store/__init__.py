"""Default SQLite storage: single file, zero external dependencies (no Redis/PG).

Tables (v0.4, PRD 10-01-v01-store-dedup + 10-01-v02-storage-hardening
+ 10-01-v02-enrich-llm + 10-01-v03-feedback-loop + 10-01-v04-trend-baseline):
- items           — intelligence entries flowing through pipelines
- dedup_registry  — seen dedup keys (see myia.dedup for slot semantics)
- change_baseline — per-URL change fingerprints (consumed by engines/fetch_base)
- engine_hints    — engine auto-degrade write-back (consumed by engines/registry)
- runs            — one row per pipeline execution, including per-step
                    progress (``runs.steps``, 断点续跑)
- enrich_cache    — per-URL LLM score cache (consumed by myia.enrich)
- feedback        — good/bad verdicts on pushed items (consumed by myia.feedback)
- feedback_tuning — append-only parameter-adjustment history (反馈调参可追溯)
- metric_history  — numeric snapshots per (category, metric_key, field) for
                    the v0.4 trend baseline (vs 昨日/上周, 关键词提及量周环比);
                    retention keeps it longer than items (基线期长于条目期)
- store_meta      — key-value housekeeping (schema version, vacuum stamp)

Storage hardening (v0.2): schema versioning with forward migrations and
structured refusals (:class:`StoreSchemaError`) at open time; retention
cleanup distinguishing pushed vs never-pushed items
(:meth:`SQLiteStore.cleanup_expired`); cadence-gated ``VACUUM``
(:meth:`SQLiteStore.maybe_vacuum`).

The public surface is the :class:`Store` protocol plus the default
:class:`SQLiteStore` backend; a PostgreSQL backend slots in behind the same
protocol in v0.2+.
"""

from myia.store.base import Store
from myia.store.errors import StoreSchemaError
from myia.store.models import (
    FEEDBACK_BAD,
    FEEDBACK_CHANNEL_CLI,
    FEEDBACK_CHANNEL_DESKTOP,
    FEEDBACK_CHANNEL_FEISHU,
    FEEDBACK_CHANNEL_TELEGRAM,
    FEEDBACK_GOOD,
    FEEDBACK_VERDICTS,
    METRIC_WINDOW_DAY,
    METRIC_WINDOW_WEEK,
    METRIC_WINDOWS,
    PUSH_SLOTS,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    RUN_STATUSES,
    SLOT_AM,
    SLOT_PM,
    STEP_STATUS_FAILED,
    STEP_STATUS_OK,
    STEP_STATUS_RESUMED,
    STEP_STATUS_SKIPPED,
    STEP_STATUSES,
    TUNING_CATEGORY_PENALTY,
    TUNING_MUTE_WEIGHT,
    TUNING_PROMPT_NOTE,
    ChangeBaseline,
    DedupEntry,
    EngineHint,
    FeedbackRecord,
    ItemRecord,
    MetricRecord,
    RunRecord,
    TuningRecord,
)
from myia.store.sqlite import (
    BASELINE_RETENTION_MULTIPLIER,
    SCHEMA_VERSION,
    UNPUSHED_RETENTION_MULTIPLIER,
    SQLiteStore,
    metric_window_start,
)

__all__ = [
    "BASELINE_RETENTION_MULTIPLIER",
    "SCHEMA_VERSION",
    "ChangeBaseline",
    "DedupEntry",
    "EngineHint",
    "FEEDBACK_BAD",
    "FEEDBACK_CHANNEL_CLI",
    "FEEDBACK_CHANNEL_DESKTOP",
    "FEEDBACK_CHANNEL_FEISHU",
    "FEEDBACK_CHANNEL_TELEGRAM",
    "FEEDBACK_GOOD",
    "FEEDBACK_VERDICTS",
    "FeedbackRecord",
    "ItemRecord",
    "METRIC_WINDOW_DAY",
    "METRIC_WINDOW_WEEK",
    "METRIC_WINDOWS",
    "MetricRecord",
    "PUSH_SLOTS",
    "RUN_STATUSES",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_PARTIAL",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_SUCCESS",
    "RunRecord",
    "SLOT_AM",
    "SLOT_PM",
    "UNPUSHED_RETENTION_MULTIPLIER",
    "STEP_STATUSES",
    "STEP_STATUS_FAILED",
    "STEP_STATUS_OK",
    "STEP_STATUS_RESUMED",
    "STEP_STATUS_SKIPPED",
    "TUNING_CATEGORY_PENALTY",
    "TUNING_MUTE_WEIGHT",
    "TUNING_PROMPT_NOTE",
    "TuningRecord",
    "SQLiteStore",
    "Store",
    "StoreSchemaError",
    "metric_window_start",
]
