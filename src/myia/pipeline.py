"""Pipeline orchestration: fetch -> classify -> dedup -> analyze -> push.

Stage responsibilities (README architecture):
- fetch:    engines/* resolve one fetch engine per source along the degrade
            chain; sources run concurrently on asyncio, a single failing
            source is isolated (structured record, siblings proceed)
- classify: keyword pre-filter (classify/builtin + optional custom rules),
            zero token
- dedup:    URL/composite-key registry (dedup.DedupRegistry); never title
            fingerprints; survivors are persisted to the items table
- analyze:  v0.2 LLM precision scoring (enrich.LLMEnricher, replaces the
            v0.1 pass-through stub): backfills ``Item.scores`` + a scalar
            ``metadata['score']`` so push.route score rules wake up; disabled
            (= v0.1) or degraded (budget spent / endpoint down) it passes
            items through unscored -- 无 score 缺省行为与 v01-push 对齐
- aggregate: v0.4 event aggregation (enrich.EventAggregator, only when the
            category declares ``aggregate.enabled``): two-level same-event
            dedup AFTER dedup and BEFORE push -- title-similarity coarse
            screen (zero token) then LLM confirmation on the shared enrich
            batch/cache/budget rails; same-event items merge into one card
            (main entry + 「另见 N 源」), digest/immediate routing both see
            the merged form. Orthogonal with dedup: dedup kills identical
            URLs first, aggregation folds surviving near-duplicates.
- push:     channel dispatch (feishu_card / telegram / webhook / stdout) with
            threshold routing (immediate/digest/archive); digest merges into
            one card per channel per run, AM/PM slot suppression prevents
            resends within a slot, and a digest whose every channel failed
            stays pooled on the pipeline -- the next run's flush retries it
            (跨 run 重试;池在进程内:进程重启即丢,且 partial 前任不可续跑,
            留池条目会永久丢失——flush 全通道失败时有结构化告警,store 回填
            属后续版本工作,当前没有任何实现)

Scheduling (``run_forever``): APScheduler in-process cron built from the
category's ``schedule`` + ``timezone`` sections; ``run`` is a single
execution. Stages share one artifact chain (the item list); a stage failing
after its retries marks the downstream stages ``upstream_failed`` so
half-processed items never reach a channel.

Per-stage timeout/retry: :class:`StageOptions` per stage (constructor
injection; the 12-section schema has no pipeline section in v0.1, so the
defaults in ``DEFAULT_STAGE_TIMEOUT_SECONDS`` / ``DEFAULT_STAGE_RETRIES``
apply). For ``fetch`` the timeout applies per source -- one slow source times
out alone, siblings are unaffected.

Observability: every run emits stage-level structured logs (duration, item
counts, skip reasons -- fingerprint skip and dedup skip are normal paths and
stay visible), writes a ``runs`` row (status + stats + failure digest) and
returns a :class:`RunResult` serializable via :meth:`RunResult.to_dict` for
``myia run --json``.

Dry-run (``run(dry_run=True)``): the full chain executes against an in-memory
store (no dedup/baseline/engine-hint side effects), routing decisions are
computed and reported, but no channel send happens.

断点续跑 (v0.2 storage hardening): every completed stage checkpoints its
items into the run row (``runs.steps``). A run that starts right after an
interrupted/crashed predecessor adopts the last surviving checkpoint —
completed stages are skipped (no duplicate fetch, no duplicate classify;
重发语义仍由去重注册表同槽位拦截兜底,不因续跑改变). Resume is refused when
the predecessor started before the current push-slot window: replaying an
older checkpoint cross-slot would bypass same-slot suppression and resend
delivered items.

Storage maintenance (随调度周期执行): every ``run`` ends with retention
cleanup (pushed vs never-pushed items, stale baselines/hints, old runs) plus
a cadence-gated ``VACUUM`` (:meth:`Pipeline.run_maintenance`, off-loop via
``asyncio.to_thread``) — the periodic task rides the existing APScheduler
cycle, so ``run_forever`` needs no extra job. The same phase runs the v0.3
feedback loop's periodic tuning (负反馈 Top 类目/词 → demotion adjustments,
history in ``feedback_tuning``; see :mod:`myia.feedback`), and resident mode
polls TG ``getUpdates`` for card feedback callbacks when a ``telegram`` push
channel is configured (grill Q7 分形态接收).

Raises:
    LoadError: ``category_yaml`` is a path whose YAML the schema refuses
        (CLI maps this to exit code 1).
    ValueError: unknown stage name in ``stage_options``, or invalid
        schedule/timezone (defense in depth behind the schema validator).
    StoreSchemaError: the storage file is corrupt or its schema version does
        not match this binary (structured; surfaces when the store first
        opens, i.e. at the start of a non-dry run).
    ClassifyDataError: the keyword table is malformed (fail fast at
        construction).
    RuleConfigError / RuleSyntaxError: ``classify.rules`` invalid.
    RouteConfigError: ``push[].route`` invalid (fail fast at construction).
    EnrichConfigError: ``enrich.enabled`` but the endpoint settings are
        missing/plaintext/unresolvable or the ``openai`` extra is not
        installed (fail fast at construction; CLI exit code 1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, IO, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from myia.classify import (
    ClassifyDataError,
    classify_item,
    load_table,
    rules_from_config,
)
from myia.dedup import DedupRegistry
from myia.engines.fetch_base import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchContext,
    classify_exception,
)
from myia.engines.registry import FetchOutcome, fetch_source
from myia.enrich import (
    AggregateOutcome,
    EnrichConfigError,
    EnrichOutcome,
    EnrichSettings,
    EventAggregator,
    LLMEnricher,
)
from myia.enrich.scoring import BudgetTracker
from myia.feedback import ActiveTuning, FeedbackTuner, TuningPolicy, ingest_callbacks, load_active_tuning
from myia.push import (
    CHANNELS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DeliveryLedger,
    DigestAggregator,
    PLATFORMS,
    RouteConfigError,
    SendReport,
    TelegramFeedbackError,
    TelegramFeedbackPoller,
    resolve_route,
    routes_from_config,
    send_immediate,
)
from myia.push.directory import REFRESH_STALE_SECONDS, ChannelDirectory, ChannelEntry
from myia.push.templates import (
    build_keyword_trends,
    build_trend_table,
    record_item_metrics,
    record_keyword_mentions,
)
from myia.schema import CHANNEL_PLATFORMS, CategoryConfig, LoadError, load_category_file
from myia.store import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    STEP_STATUS_RESUMED,
    STEP_STATUS_SKIPPED,
    ItemRecord,
    SQLiteStore,
    Store,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DEFAULT_STAGE_RETRIES",
    "DEFAULT_STAGE_TIMEOUT_SECONDS",
    "EXECUTED_STAGES",
    "RESUMABLE_RUN_STATUSES",
    "RESUME_CHECKPOINT_STAGES",
    "STAGES",
    "STAGE_OPTION_NAMES",
    "AggregateOutcome",
    "ChannelPushReport",
    "EventAggregator",
    "Item",
    "Pipeline",
    "RunResult",
    "SourceReport",
    "StageOptions",
    "StageReport",
    "build_cron_trigger",
    "EnrichConfigError",
    "EnrichSettings",
]

# Full stage vocabulary (tests/test_smoke.py pins it). ``enrich`` is the v0.2
# interface slot (LLM precision scoring) -- not executed in v0.1.
STAGES = ["fetch", "classify", "dedup", "analyze", "enrich", "push"]

#: Stages actually executed in a v0.1 run, in order. The v0.4 ``aggregate``
#: stage (事件聚合) is inserted before ``push`` at run time, only when the
#: category declares ``aggregate.enabled`` (kept out of this base tuple so
#: ``result.stages`` stays identical for categories without aggregation).
EXECUTED_STAGES: tuple[str, ...] = ("fetch", "classify", "dedup", "analyze", "push")

#: Full per-stage options vocabulary: EXECUTED_STAGES plus the conditional
#: v0.4 aggregate stage (stage_options may target it even when disabled --
#: validating the knob is not the same as running the stage).
STAGE_OPTION_NAMES: tuple[str, ...] = ("fetch", "classify", "dedup", "analyze", "aggregate", "push")

#: Stages whose output is checkpointed into ``runs.steps`` (断点续跑): a rerun
#: resuming from the last surviving checkpoint skips every stage up to it.
RESUME_CHECKPOINT_STAGES: tuple[str, ...] = ("fetch", "classify", "dedup")

#: Run statuses a resume may adopt: ``running`` (killed mid-run) and
#: ``failed`` (infra abort — its completed checkpoints precede any push, so
#: replay is duplicate-safe). ``partial`` is deliberately excluded: replaying
#: its checkpoint would pool the same items twice within one slot. NOTE: the
#: in-process digest pool only retries while THIS process lives — a restart
#: drops the pool, a partial predecessor is not resumable, and its pooled
#: items are lost (structured warning at flush failure; store 回填属后续版本,
#: 当前无实现 — do not claim otherwise).
RESUMABLE_RUN_STATUSES = frozenset({RUN_STATUS_RUNNING, RUN_STATUS_FAILED})

DEFAULT_DB_PATH = "myia.db"
#: Per-stage timeouts (seconds). ``fetch`` applies per source; ``analyze`` and
#: ``aggregate`` span N batched LLM calls (v0.2 enrich / v0.4 事件聚合), hence
#: the larger caps — per-call timeouts live inside the enricher/aggregator
#: (EnrichSettings.timeout_seconds).
DEFAULT_STAGE_TIMEOUT_SECONDS: dict[str, float] = {
    "fetch": 120.0,
    "classify": 60.0,
    "dedup": 60.0,
    "analyze": 300.0,
    "aggregate": 300.0,
    "push": 120.0,
}
#: Per-stage retry budgets. Engines already retry requests, and retrying a
#: push risks duplicates -- so v0.1 defaults keep the mechanism, not the retries.
DEFAULT_STAGE_RETRIES: dict[str, int] = {
    "fetch": 0,
    "classify": 0,
    "dedup": 0,
    "analyze": 0,
    "aggregate": 0,
    "push": 0,
}
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
MAX_RETRY_BACKOFF_SECONDS = 30.0

_UPSTREAM_FAILED = "upstream_failed"


# ---------------------------------------------------------------------------
# Stage configuration & reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageOptions:
    """Per-stage timeout/retry knobs (constructor injection on Pipeline)."""

    timeout_seconds: float
    retries: int = 0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError(f"字段校验失败: 超时必须为正数,得到 {self.timeout_seconds}")
        if self.retries < 0:
            raise ValueError(f"字段校验失败: 重试次数不能为负,得到 {self.retries}")


def _default_stage_options() -> dict[str, StageOptions]:
    return {
        name: StageOptions(
            timeout_seconds=DEFAULT_STAGE_TIMEOUT_SECONDS[name],
            retries=DEFAULT_STAGE_RETRIES[name],
        )
        for name in STAGE_OPTION_NAMES
    }


@dataclass
class StageReport:
    """One stage's observable outcome (duration / counts / skips / failures)."""

    name: str
    status: str = "pending"  # pending | ok | failed | skipped
    attempts: int = 0
    duration_seconds: float = 0.0
    items_in: int = 0
    items_out: int = 0
    skips: Counter[str] = field(default_factory=Counter)  # skip_reason -> count
    failures: list[dict[str, Any]] = field(default_factory=list)  # item-level failures
    #: 降级注记(自动恢复,不翻 run 状态):enrich 条目级失败等。与 failures
    #: 的区别——failures 参与 resolve_status 的 partial 判定,warnings 只上报。
    warnings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None  # stage-level failure after retries
    skip_reason: str | None = None  # set when status == skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "attempts": self.attempts,
            "duration_seconds": round(self.duration_seconds, 4),
            "items_in": self.items_in,
            "items_out": self.items_out,
            "skips": dict(self.skips),
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "error": self.error,
            "skip_reason": self.skip_reason,
        }


@dataclass
class SourceReport:
    """One source's fetch outcome, including its structured failures."""

    name: str
    engine: str
    url: str
    item_count: int = 0
    attempts: int = 0
    duration_seconds: float = 0.0
    skipped: bool = False
    skip_reason: str | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)  # engine attempt failures
    error: dict[str, Any] | None = None  # step-level failure after retries/timeouts

    @property
    def failed(self) -> bool:
        """A source failed only when its fetch chain truly exhausted.

        ``error`` is set by :meth:`Pipeline._fetch_one` exactly when no engine
        in the chain succeeded (or an infra exception escaped) — earlier
        engine failures inside a *successful* chain (degrade + healthy empty
        page / fingerprint skip) stay observability-only and never flip the
        source to failed (registry 已认定该源成功).
        """
        return self.error is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "engine": self.engine,
            "url": self.url,
            "item_count": self.item_count,
            "attempts": self.attempts,
            "duration_seconds": round(self.duration_seconds, 4),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "failures": list(self.failures),
            "error": self.error,
            "failed": self.failed,
        }


@dataclass
class ChannelPushReport:
    """One push channel's routing buckets + send reports for a run."""

    channel: str
    dry_run: bool = False
    immediate: int = 0
    digest: int = 0
    archive: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)
    reports: list[SendReport] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "dry_run": self.dry_run,
            "immediate": self.immediate,
            "digest": self.digest,
            "archive": self.archive,
            "decisions": list(self.decisions),
            "reports": [
                {"channel": r.channel, "ok": r.ok, "item_count": r.item_count, "error": r.error}
                for r in self.reports
            ],
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Items & run results
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """One intelligence unit flowing through the pipeline."""

    url: str
    title: str
    source: str | None = None
    category: str | None = None
    scores: dict | None = None
    dedup_key: str | None = None
    #: 正文摘录(L3 无 extract 的 markdown / extract 显式映射的 content 字段):
    #: LLM 精评的 credibility 维度依赖它,items.content 列由它回填。
    content: str | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_extracted(cls, raw: Mapping[str, Any], source: str) -> "Item":
        """Build an Item from one engine-extracted record.

        ``url``/``title``/``source``/``content`` are the pipeline-known keys;
        every other extracted field lands in ``metadata`` (visible to dedup
        key templates, classify ``when`` rules and push route rules).
        ``content`` is kept on the Item itself (not metadata) so the enrich
        prompt and the ``items.content`` column actually receive it.

        Raises:
            ValueError: the record has no usable ``url`` (dedup keys are
                URL-based; 永不标题指纹).
        """
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"提取条目缺少有效 url 字段,当前为 {url!r}")
        known = ("url", "title", "source", "content")
        metadata = {key: value for key, value in raw.items() if key not in known}
        title = raw.get("title")
        content = raw.get("content")
        return cls(
            url=url,
            title=title if isinstance(title, str) else "",
            source=source,
            content=content if isinstance(content, str) and content.strip() else None,
            metadata=metadata,
        )

    def view(self) -> dict[str, Any]:
        """Flatten into a plain dict (top-level fields win over metadata)."""
        view: dict[str, Any] = dict(self.metadata)
        view.update(
            {
                "url": self.url,
                "title": self.title,
                "source": self.source,
                "category": self.category,
                "scores": self.scores,
                "dedup_key": self.dedup_key,
            }
        )
        return view

    def add_tags(self, tags: Sequence[str]) -> None:
        """Merge rule tags into metadata, preserving order and deduping."""
        if not tags:
            return
        existing = self.metadata.get("tags")
        existing_list = list(existing) if isinstance(existing, list) else []
        self.metadata["tags"] = list(dict.fromkeys([*existing_list, *tags]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "category": self.category,
            "dedup_key": self.dedup_key,
            "tags": list(self.metadata.get("tags") or []),
            "metadata": dict(self.metadata),
        }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    """Default wall clock (aware UTC) — tests inject a pinned replacement."""
    return datetime.now(timezone.utc)


@dataclass
class RunResult:
    """One pipeline execution: stage reports, sources, items, pushes, status."""

    category: str
    category_name: str
    run_id: int
    started_at: datetime
    dry_run: bool = False
    finished_at: datetime | None = None
    status: str = RUN_STATUS_SUCCESS
    stages: list[StageReport] = field(default_factory=list)
    sources: list[SourceReport] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    pushes: list[ChannelPushReport] = field(default_factory=list)
    resumed_from_run_id: int | None = None  # 断点续跑:被接管的 run
    maintenance: dict[str, Any] | None = None  # 随 run 的清理/VACUUM 结果
    #: 反馈闭环调参结果(run 起点,随调度周期执行;见 _tune_feedback_safely)
    feedback_tuning: dict[str, Any] | None = None

    @property
    def item_failures(self) -> list[dict[str, Any]]:
        """All item-level failures across stages (structured, with stage tag)."""
        return [failure for stage in self.stages for failure in stage.failures]

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def resolve_status(self) -> str:
        """success / partial / failed -- the CLI exit-code contract (0/2/3)."""
        if self.sources and all(source.failed for source in self.sources):
            return RUN_STATUS_FAILED
        any_failure = (
            any(source.failed for source in self.sources)
            or any(stage.status == "failed" for stage in self.stages)
            or bool(self.item_failures)
            or any(not push.ok for push in self.pushes)
        )
        return RUN_STATUS_PARTIAL if any_failure else RUN_STATUS_SUCCESS

    def stage(self, name: str) -> StageReport:
        for report in self.stages:
            if report.name == name:
                return report
        raise KeyError(f"未知阶段: {name!r}")

    def stats_dict(self) -> dict[str, Any]:
        """Compact stats for the runs table (items excluded, counts only)."""
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "resumed_from_run_id": self.resumed_from_run_id,
            "sources": [source.to_dict() for source in self.sources],
            "stages": [stage.to_dict() for stage in self.stages],
            "push": [
                {
                    "channel": push.channel,
                    "ok": push.ok,
                    "immediate": push.immediate,
                    "digest": push.digest,
                    "archive": push.archive,
                }
                for push in self.pushes
            ],
            "item_failures": len(self.item_failures),
            "items_retained": len(self.items),
            "maintenance": self.maintenance,
            "feedback_tuning": self.feedback_tuning,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full machine-readable form for ``myia run --json`` (AI/CI 消费路径)."""
        duration = self.duration_seconds
        return {
            "category": self.category,
            "category_name": self.category_name,
            "run_id": self.run_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "resumed_from_run_id": self.resumed_from_run_id,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "duration_seconds": round(duration, 4) if duration is not None else None,
            "stages": [stage.to_dict() for stage in self.stages],
            "sources": [source.to_dict() for source in self.sources],
            "items": [item.to_dict() for item in self.items],
            "push": [push.to_dict() for push in self.pushes],
            "failures": self.item_failures,
            "maintenance": self.maintenance,
            "feedback_tuning": self.feedback_tuning,
        }


# ---------------------------------------------------------------------------
# 断点续跑: checkpoints & resume plan
# ---------------------------------------------------------------------------


def _item_checkpoint(item: Item) -> dict[str, Any]:
    """Serialize one :class:`Item` for a ``runs.steps`` checkpoint payload."""
    return {
        "url": item.url,
        "title": item.title,
        "source": item.source,
        "category": item.category,
        "scores": item.scores,
        "dedup_key": item.dedup_key,
        "metadata": item.metadata,
    }


def _item_from_checkpoint(data: Any) -> Item:
    """Rebuild one :class:`Item` from a checkpoint entry.

    Raises:
        ValueError: the entry is not a mapping or has no usable ``url``
            (结构化消息;调用方按「检查点损坏」整体放弃续跑).
    """
    if not isinstance(data, Mapping):
        raise ValueError(f"检查点条目必须是键值映射,当前为 {type(data).__name__}")
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"检查点条目缺少有效 url 字段,当前为 {url!r}")
    metadata = data.get("metadata")
    return Item(
        url=url,
        title=data.get("title") if isinstance(data.get("title"), str) else "",
        source=data.get("source") if isinstance(data.get("source"), str) else None,
        category=data.get("category") if isinstance(data.get("category"), str) else None,
        scores=data.get("scores") if isinstance(data.get("scores"), dict) else None,
        dedup_key=data.get("dedup_key") if isinstance(data.get("dedup_key"), str) else None,
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
    )


def _checkpoint_payload(items: list[Item]) -> dict[str, Any] | None:
    """Wrap items as a checkpoint payload; None when not JSON-serializable.

    A serialization failure only downgrades resumability (the step records
    without a payload and resume falls back to an earlier checkpoint) — it
    never breaks the run.
    """
    try:
        return {"items": [_item_checkpoint(item) for item in items]}
    except (TypeError, ValueError) as exc:
        logger.warning("步骤检查点序列化失败,该步骤不作为续跑锚点: %s", exc)
        return None


@dataclass(frozen=True)
class _ResumePlan:
    """A resumable checkpoint adopted from the immediately previous run."""

    from_run_id: int
    completed: tuple[str, ...]  # checkpoint stages to mark skipped (resumed)
    payloads: dict[str, dict[str, Any]]  # stage name -> checkpoint payload
    items: list[Item]  # restored items entering the stage after the checkpoint


# ---------------------------------------------------------------------------
# Cron trigger
# ---------------------------------------------------------------------------


def build_cron_trigger(schedule: str, timezone_name: str | None = None) -> CronTrigger:
    """Build an APScheduler cron trigger from the category schedule sections.

    Args:
        schedule: 5-field cron expression (schema-validated; re-checked here).
        timezone_name: IANA timezone name; None follows the system zone.

    Raises:
        ValueError: invalid cron expression or unknown timezone (structured
            message; defense in depth behind the schema validator).
    """
    tz: Any = None
    if timezone_name is not None:
        try:
            tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"timezone 不是有效的 IANA 时区名称: {timezone_name!r}") from exc
    try:
        return CronTrigger.from_crontab(schedule, timezone=tz)
    except ValueError as exc:
        raise ValueError(f"schedule 不是合法的 5 段 cron 表达式({exc})") from exc


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Runs the stages for one category YAML (fetch -> ... -> push)."""

    def __init__(
        self,
        category_yaml: CategoryConfig | str | Path,
        *,
        store: Store | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stage_options: Mapping[str, StageOptions] | None = None,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        wall_clock: Callable[[], datetime] | None = None,
        enricher: Any | None = None,
        enrich_settings: EnrichSettings | None = None,
        aggregator: Any | None = None,
        proxy_pools: Any | None = None,
        feedback_policy: TuningPolicy | None = None,
        feedback_poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        stdout_stream: IO[str] | None = None,
    ) -> None:
        """Build a pipeline for one category.

        Args:
            category_yaml: validated :class:`CategoryConfig`, or a path to the
                plugin YAML (loaded via :func:`myia.schema.load_category_file`).
            store: injected storage backend (caller owns its lifecycle); when
                omitted, a :class:`SQLiteStore` at ``db_path`` is created and
                owned by the pipeline (released via :meth:`close`).
            db_path: SQLite path for the pipeline-owned store (default
                ``./myia.db``).
            client: injected ``httpx.AsyncClient`` (tests mock here); when
                omitted, one is created per run and closed at run end.
            timeout: per-request engine timeout in seconds.
            clock / sleep: injectable clock and sleeper -- tests never really
                wait (the sleeper also paces stage-retry backoff).
            stage_options: per-stage overrides merged over the defaults; keys
                must be stage names from :data:`EXECUTED_STAGES`.
            retry_backoff_seconds: base for exponential stage-retry backoff.
            wall_clock: injectable wall clock (aware datetimes) for run
                timestamps and the 断点续跑 slot-window check; tests pin it to
                make slot math deterministic.
            enricher: injected enrich object (v0.2 analyze stage; tests pass
                fakes -- 零真实网络). Used as-is when ``enrich.enabled``; the
                default builds an :class:`~myia.enrich.LLMEnricher`.
            enrich_settings: endpoint settings for the default enricher
                (``base_url``/``api_key`` as ``env:`` references, grill Q6).
                Shared with the v0.4 event aggregator; ``None`` reads the
                schema ``enrich:`` section instead.
            aggregator: injected event aggregator (v0.4 aggregate stage; tests
                pass fakes -- 零真实网络). Used as-is when
                ``aggregate.enabled``; the default builds an
                :class:`~myia.enrich.EventAggregator` on the same endpoint
                settings as the enricher.
            feedback_policy: 反馈闭环调参阈值 (v0.3, PRD 10-01-v03-feedback-loop);
                defaults to :class:`TuningPolicy` defaults. The tuner runs in
                the maintenance phase of every non-dry run.
            feedback_poll_interval: TG ``getUpdates`` feedback-poll interval
                in resident mode (desktop 形态接收); polling only activates
                when a ``telegram`` push channel is configured.
            stdout_stream: stream the ``stdout`` push channel writes to;
                default ``None`` keeps the channel default (``sys.stdout``).
                ``myia run --json`` 注入 ``sys.stderr``:stdout 通道的卡片行
                与 run 报告 JSON 不能同流,否则「--json 输出恰好一份 JSON
                文档(stdout)」的 CLI 契约被打破(AI 消费面 json.load 必失败)。

        Raises:
            ValueError: unknown stage name in ``stage_options``.
            EnrichConfigError: ``enrich.enabled`` with unusable endpoint
                configuration (missing/plaintext reference, unresolvable env
                var, missing ``openai`` extra) -- fail fast at construction.
        """
        if isinstance(category_yaml, CategoryConfig):
            self.config = category_yaml
        else:
            self.config = load_category_file(category_yaml)  # LoadError 传播(CLI 退出码 1)
        self._injected_store = store
        self._db_path = db_path
        self._injected_client = client
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._retry_backoff = retry_backoff_seconds
        self._wall_clock = wall_clock or _utc_now
        # 全局 pools 声明(v0.2 proxy-transport;myia run --config 加载注入)。
        # 缺省 None:pool: 源会在 fetch 前结构化报 proxy_pools_not_configured。
        self._proxy_pools = proxy_pools
        # 反馈闭环(v0.3):周期调参器随维护阶段执行;TG 轮询随常驻模式执行。
        self._feedback_tuner = FeedbackTuner(feedback_policy)
        self._feedback_poll_interval = max(1.0, float(feedback_poll_interval))
        # stdout 通道落笔流(缺省走通道默认 sys.stdout;--json 注入 stderr,
        # 见 __init__ docstring —— 保住「--json 恰好一份 JSON 在 stdout」契约)。
        self._stdout_stream = stdout_stream
        # 趋势基线渲染上下文(每轮 run 在 push 前装配一次;baseline 未启用为空)。
        self._run_trend_kwargs: dict[str, Any] = {}

        self._stage_options = _default_stage_options()
        if stage_options is not None:
            for name, options in stage_options.items():
                if name not in self._stage_options:
                    raise ValueError(
                        f"字段校验失败: stage_options 含未知阶段 {name!r},可选阶段 {list(STAGE_OPTION_NAMES)}"
                    )
                self._stage_options[name] = options

        # Fail fast on config that the schema alone cannot fully validate.
        self._classify_table = load_table() if self.config.classify.builtin else None
        self._classify_rules = rules_from_config(
            [rule.model_dump() for rule in self.config.classify.rules]
        )
        self._push_routes = [routes_from_config(push.route) for push in self.config.push]
        # enrich(v0.2 第二层漏斗):注入的 enricher 优先(测试/自定义实现);
        # 否则由 settings 构建默认 LLMEnricher —— 端点引用缺失/明文/无法解析
        # 在这里结构化报错(fail fast,CLI 退出码 1),绝不带病跑烧 token。
        self._enricher = enricher
        if self.config.enrich.enabled and self._enricher is None:
            self._enricher = LLMEnricher(
                self.config.enrich,
                enrich_settings if enrich_settings is not None else self._enrich_settings_from_config(),
                clock=self._clock,
            )
        # 事件聚合(v0.4, PRD 10-01-v04-event-aggregation):与 enrich 同一份
        # 端点配置(model/batch/budget 复用 enrich 节),注入的 aggregator 优先。
        # aggregate.enabled 但端点不可用 → 与 enrich 同一构造期结构化报错。
        self._aggregator = aggregator
        if self.config.aggregate and self.config.aggregate.enabled and self._aggregator is None:
            self._aggregator = EventAggregator(
                self.config.aggregate,
                self.config.enrich,
                enrich_settings if enrich_settings is not None else self._enrich_settings_from_config(),
                clock=self._clock,
            )
        try:
            self._tz = ZoneInfo(self.config.timezone) if self.config.timezone else None
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"timezone 不是有效的 IANA 时区名称: {self.config.timezone!r}") from exc

        self._owned_store: SQLiteStore | None = None
        # 单轮共享 token 预算(v0.4):每次 run() 开场重建,analyze(精评)与
        # aggregate(事件判重)合计消费,谁先到顶谁降级。
        self._run_budget: BudgetTracker | None = None
        # Per-push-entry digest pools, held across runs (digest.py 契约:全通道
        # 失败的摘要「留池待重试」——池挂在 Pipeline 上,下一次 run 的 flush
        # 会连带上次失败条目一起重发;注意:池是进程内的,进程重启即丢,
        # partial 前任也不可续跑——留池条目会永久丢失,见 _flush 摘要告警)。
        self._digest_pools: dict[int, DigestAggregator] = {}
        # 消息平台定向(v1.2, 10-03-messaging-core design D3):目录与死信账本
        # 落数据根(db 路径父目录;CLI cwd / 桌面 myia_home(),与任务
        # 10-03-v111-desktop-paths 收口一致)。读写 best-effort,损坏退化
        # 内存态,不阻塞推送。
        _data_root = Path(self._db_path).parent
        self._channel_directory = ChannelDirectory(_data_root)
        self._delivery_ledger = DeliveryLedger(_data_root)

    def _enrich_settings_from_config(self) -> EnrichSettings:
        """Schema 承载的端点引用 → :class:`EnrichSettings`(PRD: enrich 节承载 base_url)。

        ``myia run`` 的唯一用户入口:schema 的 ``enrich.base_url`` /
        ``enrich.api_key``(env:/keychain: 引用)在此装配,引用语法/明文由
        EnrichSettings 结构化拒绝。两字段都缺省时返回空 ``EnrichSettings()``
        ——其 __post_init__ 抛 missing_base_url / missing_api_key,与构造注入
        路径同一失败契约。
        """
        enrich = self.config.enrich
        if enrich.base_url is None and enrich.api_key is None:
            return EnrichSettings()
        return EnrichSettings(base_url_ref=enrich.base_url, api_key_ref=enrich.api_key)

    # -------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Release pipeline-owned resources (injected store/client untouched)."""
        if self._owned_store is not None:
            self._owned_store.close()
            self._owned_store = None

    def _store_for_run(self, dry_run: bool) -> Store:
        if dry_run:
            logger.info("dry-run:使用内存存储,不产生任何持久化副作用")
            return SQLiteStore(":memory:")
        if self._injected_store is not None:
            return self._injected_store
        if self._owned_store is None:
            self._owned_store = SQLiteStore(self._db_path)
            logger.debug("存储已创建 path=%s", self._db_path)
        return self._owned_store

    def _ensure_registry(self, store: Store) -> DedupRegistry:
        return DedupRegistry(store, tz=self._tz)

    def _context_for_run(self, client: httpx.AsyncClient, store: Store) -> FetchContext:
        return FetchContext(
            client=client,
            store=store,
            timeout=self._timeout,
            clock=self._clock,
            sleep=self._sleep,
            proxy_pools=self._proxy_pools,
        )

    def _build_channel(self, push: Any) -> Any:
        """Instantiate the channel for one ``push[]`` entry (dict registry + 注入).

        v0.2 起四通道全部实装:用户 YAML 里的 target/template 必须真的到达
        通道(裸构造会静默改走默认 env 引用——要么假失败,要么误投)。
        """
        channel_cls = CHANNELS[push.channel]  # schema Literal guarantees the name
        kwargs: dict[str, Any] = {}
        if push.target is not None:  # stdout 无 target(schema 保证 None)
            kwargs["target"] = push.target
        if push.template is not None:
            kwargs["template"] = push.template
        if push.channel == "webhook":
            # PRD:webhook 超时/重试可配(schema 校验仅 webhook 允许这些字段)
            kwargs["timeout"] = push.timeout
            kwargs["retries"] = push.retries
            kwargs["retry_backoff_seconds"] = push.retry_backoff_seconds
        if push.channel == "stdout" and self._stdout_stream is not None:
            # --json 模式:卡片行改写 stderr,run 报告独占 stdout(CLI 契约)
            kwargs["out"] = self._stdout_stream
        return channel_cls(**kwargs)

    # ------------------------------------------------------------------- run

    async def run(self, *, dry_run: bool = False) -> RunResult:
        """Execute fetch -> classify -> dedup -> analyze -> push once.

        Steps checkpoint into the run row as they complete (断点续跑); when an
        interrupted predecessor left a usable checkpoint, its completed stages
        are skipped. Storage maintenance (retention + cadence-gated VACUUM)
        runs at the end of every non-dry run — 随调度周期执行,无需独立任务.

        Args:
            dry_run: run the full chain but never send; state lives in an
                in-memory store (no dedup/baseline/engine-hint side effects,
                no resume adoption, no maintenance).

        Returns:
            RunResult with per-stage reports, per-source outcomes, retained
            items and per-channel push reports (``--json`` 消费形态).

        Raises:
            RuntimeError: infrastructure failure outside stage isolation
                (e.g. the runs row could not be written); stage failures do
                NOT raise -- they are recorded and skip downstream stages.
            StoreSchemaError: the storage file is corrupt or its schema
                version is incompatible (raised when the store first opens).
        """
        run_store = self._store_for_run(dry_run)
        run_id = run_store.start_run(self.config.id)
        result = RunResult(
            category=self.config.id,
            category_name=self.config.name,
            run_id=run_id,
            started_at=self._wall_clock(),
            dry_run=dry_run,
        )
        logger.info(
            "运行开始 category=%s run_id=%s sources=%s dry_run=%s",
            self.config.id, run_id, len(self.config.sources), dry_run,
        )
        # 反馈闭环(v0.3):调参随调度周期执行,取 run 起点使上轮收到的负反馈
        # 本轮生效;dry-run 无持久化副作用,不做调参。
        if not dry_run:
            result.feedback_tuning = self._tune_feedback_safely(run_store)

        client = self._injected_client or httpx.AsyncClient(timeout=self._timeout)
        own_client = self._injected_client is None
        # v0.4 事件聚合:品类声明 aggregate.enabled 时在 push 前插入 aggregate
        # 阶段(声明前 stage 清单与 v0.3 完全一致,不惊动无聚合的品类)。
        executed = EXECUTED_STAGES
        if self._aggregator is not None:
            executed = (*EXECUTED_STAGES[:-1], "aggregate", EXECUTED_STAGES[-1])
        # 单轮共享 token 预算(精评 + 事件判重同一个池,PRD: 并入 enrich 预算
        # 护栏;两个 LLM 阶段合计不越过 budget_per_run)。
        self._run_budget = (
            BudgetTracker(limit=self.config.enrich.budget_per_run)
            if self._enricher is not None or self._aggregator is not None
            else None
        )
        stages = {name: StageReport(name=name) for name in executed}
        result.stages = [stages[name] for name in executed]
        registry = self._ensure_registry(run_store)
        context: FetchContext | None = None
        try:
            context = self._context_for_run(client, run_store)

            # 断点续跑:接管上一个中断/失败 run 的检查点,已完成步骤不重复
            # (避免重复抓取/分类开销);dry-run 用内存库,天然无可继承检查点。
            resume = (
                None if dry_run
                else self._plan_resume(run_store, registry, run_id, result.started_at)
            )
            resumed: set[str] = set()
            resume_from: int | None = None
            resume_items: list[Item] = []
            resume_payloads: dict[str, dict[str, Any]] = {}
            if resume is not None:
                result.resumed_from_run_id = resume.from_run_id
                resume_from = resume.from_run_id
                resume_items = resume.items
                resume_payloads = resume.payloads
                resumed = set(resume.completed)
                for name in resume.completed:
                    stages[name].status = "skipped"
                    stages[name].skip_reason = "resumed"
                    self._record_step(
                        run_store, run_id, name, STEP_STATUS_RESUMED,
                        resume_payloads.get(name),
                    )

            fetch_report = stages["fetch"]
            source_reports: list[SourceReport] = []
            current: list[Item] = []
            if "fetch" in resumed:
                # fetch 检查点命中:直接取继承条目,本 run 不发起任何网络请求。
                current = resume_items
                broken = False
                logger.info(
                    "断点续跑:采集步骤跳过(继承 run_id=%s 条目=%s)",
                    resume_from, len(current),
                )
            else:
                # fetch: stage-level timeout is skipped here on purpose -- the
                # per-source wait_for in _fetch_one is what isolates slow sources;
                # a stage-level cap would abort slow-but-healthy siblings too.
                fetch_output = await self._attempt_stage(
                    "fetch",
                    lambda: self._stage_fetch(context, fetch_report),
                    fetch_report,
                    items_in=len(self.config.sources),
                    apply_timeout=False,
                )
                broken = fetch_output is None
                if fetch_output is not None:
                    source_reports, current = fetch_output
                    fetch_report.items_out = len(current)
                    self._record_step(
                        run_store, run_id, "fetch", fetch_report.status,
                        _checkpoint_payload(current),
                    )
                else:
                    self._record_step(run_store, run_id, "fetch", fetch_report.status)
            result.sources = source_reports

            flow: dict[str, Callable[..., Awaitable[Any]]] = {
                "classify": self._stage_classify,
                "dedup": self._stage_dedup,
                "analyze": self._stage_analyze,
                "aggregate": self._stage_aggregate,
                "push": self._stage_push,
            }
            for name in executed[1:]:
                report = stages[name]
                if name in resumed:
                    logger.info(
                        "断点续跑:步骤 %s 跳过(继承自 run_id=%s)", name, resume_from
                    )
                    continue
                if broken:
                    report.status = "skipped"
                    report.skip_reason = _UPSTREAM_FAILED
                    self._record_step(run_store, run_id, name, STEP_STATUS_SKIPPED)
                    logger.warning("步骤跳过(上游失败) stage=%s run_id=%s", name, run_id)
                    continue
                stage_fn = flow[name]
                # analyze/aggregate 与 fetch 一样豁免阶段级超时:每批已有
                # EnrichSettings.timeout_seconds 的 wait_for 批级隔离,批失败在
                # enricher/aggregator 内降级;外层阶段帽(N批×批超时)量纲错配,
                # 会整体杀掉 push——违背「端点故障只降级,push 仍以 v0.1 路由
                # 运行」的承诺。
                output = await self._attempt_stage(
                    name,
                    lambda fn=stage_fn, items=current, rep=report: fn(
                        items, rep, run_store, registry, dry_run
                    ),
                    report,
                    items_in=len(current),
                    apply_timeout=(name not in ("analyze", "aggregate")),
                )
                if output is None and report.status == "failed":
                    broken = True
                    self._record_step(run_store, run_id, name, report.status)
                    continue
                if name == "push":
                    result.pushes = output
                else:
                    current = output
                self._record_step(
                    run_store, run_id, name, report.status,
                    _checkpoint_payload(current) if name in RESUME_CHECKPOINT_STAGES else None,
                )
            # items 只统计走完全链的条目:链路中断(上游失败)不产出留存条目
            result.items = [] if broken else current

            result.finished_at = self._wall_clock()
            result.status = result.resolve_status()
        finally:
            error: str | None = None
            if result.finished_at is None:  # stage chain aborted with an exception
                result.finished_at = self._wall_clock()
                result.status = RUN_STATUS_FAILED
                error = "运行中断(基础设施异常)"
            elif result.status == RUN_STATUS_FAILED:
                failed_sources = [s for s in result.sources if s.error is not None]
                if failed_sources:
                    error = "; ".join(
                        f"source={s.name} {s.error.get('error_type')}: {s.error.get('message')}"
                        for s in failed_sources
                    )
            try:
                run_store.finish_run(
                    run_id, status=result.status, stats=result.stats_dict(), error=error
                )
            finally:
                # 池客户端先关(每池一条独立连接),再关直连 client——不关即
                # 每 run 泄漏一套代理连接(接线 pools 后必然发生)。
                try:
                    if context is not None:
                        await context.aclose_pool_clients()
                finally:
                    if own_client:
                        await client.aclose()
            # 清理任务随调度周期执行(每次 run 收尾;VACUUM 由 cadence 决定是否真正执行)
            if not dry_run:
                result.maintenance = await self._maintenance_safely(run_store)
        logger.info(
            "运行结束 category=%s run_id=%s status=%s items=%s sources_ok=%s/%s "
            "resumed_from=%s duration=%.2fs",
            self.config.id,
            run_id,
            result.status,
            len(result.items),
            sum(1 for s in result.sources if not s.failed),
            len(result.sources),
            result.resumed_from_run_id,
            result.duration_seconds or 0.0,
        )
        return result

    # -------------------------------------------------------- resume/maintenance

    def _record_step(
        self,
        store: Store,
        run_id: int,
        step: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort ``runs.steps`` write — 进度是观测数据,失败不拖垮运行."""
        try:
            store.update_run_step(run_id, step, status=status, payload=payload)
        except Exception as exc:  # noqa: BLE001 - 部分失败语义:记录后继续
            logger.warning("步骤进度写入失败 run_id=%s step=%s: %s", run_id, step, exc)

    def _plan_resume(
        self,
        store: Store,
        registry: DedupRegistry,
        run_id: int,
        now: datetime,
    ) -> _ResumePlan | None:
        """Adopt the previous run's last surviving checkpoint, when safe.

        Policy (无重复推送是红线,重复抓取/分类是开销):

        - only the run *immediately* before this one is inspected, and only
          when its status is in :data:`RESUMABLE_RUN_STATUSES` (running =
          killed mid-run / failed = infra abort) — a ``success`` predecessor
          has no unfinished business; ``partial`` is deliberately NOT
          adoptable (its digest business lived in the in-process pool, which
          a restart drops — see the RESUMABLE_RUN_STATUSES note);
        - only a checkpoint stage (:data:`RESUME_CHECKPOINT_STAGES`) whose
          payload survived qualifies; the *last* such stage wins;
        - only when the predecessor started inside the current push-slot
          window: replaying an older checkpoint cross-slot would bypass
          same-slot suppression and resend already-delivered items.

        Any inconsistency (unreadable steps, corrupt payload) logs a warning
        and returns None — a fresh run is always the fallback.
        """
        try:
            previous = store.previous_run(self.config.id, before_run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - 续跑探测失败按全新运行处理
            logger.warning("断点续跑探测失败(按全新运行处理): %s", exc)
            return None
        if previous is None or previous.status not in RESUMABLE_RUN_STATUSES:
            return None
        if previous.id is None:  # 防御:非持久化行无从接管
            return None
        previous_id = int(previous.id)
        steps = previous.steps or {}
        checkpoint: tuple[str, dict[str, Any]] | None = None
        for name in RESUME_CHECKPOINT_STAGES:
            entry = steps.get(name)
            if (
                isinstance(entry, Mapping)
                and entry.get("status") in ("ok", STEP_STATUS_RESUMED)
                and isinstance(entry.get("payload"), Mapping)
            ):
                checkpoint = (name, dict(entry["payload"]))
        if checkpoint is None:
            logger.debug("断点续跑:上一 run 无可用检查点 run_id=%s", previous.id)
            return None
        window_start = registry.slot_window_start(now).astimezone(timezone.utc)
        if previous.started_at is not None and previous.started_at < window_start:
            logger.info(
                "断点续跑放弃:上次运行 start=%s 早于当前槽位窗口起点 %s(跨槽位重放会绕过同槽位拦截)",
                previous.started_at, window_start,
            )
            return None
        step_name, payload = checkpoint
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            logger.warning("断点续跑检查点损坏 step=%s(按全新运行处理): items 缺失", step_name)
            return None
        try:
            items = [_item_from_checkpoint(raw) for raw in raw_items]
        except ValueError as exc:
            logger.warning("断点续跑检查点损坏 step=%s(按全新运行处理): %s", step_name, exc)
            return None
        completed = RESUME_CHECKPOINT_STAGES[: RESUME_CHECKPOINT_STAGES.index(step_name) + 1]
        payloads = {
            name: steps[name]["payload"]
            for name in completed
            if isinstance(steps.get(name), Mapping)
            and isinstance(steps[name].get("payload"), Mapping)
        }
        if previous.status == RUN_STATUS_RUNNING:
            # 被接管的 run 落终态,避免永远挂在 running 里污染 latest_run。
            try:
                store.finish_run(
                    previous_id,
                    status=RUN_STATUS_FAILED,
                    error=f"中断运行已由 run_id={run_id} 断点续跑接管",
                )
            except Exception as exc:  # noqa: BLE001 - 接管失败不影响续跑
                logger.warning("接管中断 run 失败 run_id=%s: %s", previous_id, exc)
        logger.info(
            "断点续跑:继承 run_id=%s 检查点 step=%s 条目=%s,从 %s 继续",
            previous_id, step_name, len(items),
            EXECUTED_STAGES[EXECUTED_STAGES.index(step_name) + 1],
        )
        return _ResumePlan(
            from_run_id=previous_id,
            completed=completed,
            payloads=payloads,
            items=items,
        )

    #: 声明窗口 → 天数(「两个完整窗口 + 1 天余量」按此折算)。
    _BASELINE_WINDOW_DAYS = {"day": 1, "week": 7}

    def _metrics_retention_days(self, retention_days: int) -> int:
        """``metric_history`` 保留期下限:覆盖声明的基线窗口。

        ``max(2× 条目期, 两个完整声明窗口 + 1 天)``:retention: 3d +
        windows: [week] 这类合法组合若只按 2× retention(6 天)老化,周中
        后段「窗口开始前最新快照」必被清空,vs 上周结构性失效且无告警
        (PRD 10-01-v04: retention 协同 = 基线期长于条目期 **且** 长于基线
        自身窗口)。默认 90d → 180d 恒高于窗口下限,行为不变。
        """
        floor = retention_days * 2
        baseline = self.config.baseline
        if baseline is not None and baseline.enabled and baseline.windows:
            longest = max(
                self._BASELINE_WINDOW_DAYS.get(window, 1) for window in baseline.windows
            )
            floor = max(floor, 2 * longest + 1)
        return floor

    async def run_maintenance(self, store: Store | None = None) -> dict[str, Any]:
        """Run retention cleanup + cadence-gated ``VACUUM`` for this category.

        随调度周期执行: every ``run`` ends with this (so ``run_forever``'s
        APScheduler cycle carries it with no extra job); it is also callable
        standalone. Sync SQLite work is off-loaded via ``asyncio.to_thread``
        so the event loop never blocks on VACUUM.

        Args:
            store: backend to clean; defaults to the pipeline's store
                (injected, or the owned one — created when missing).

        Returns:
            ``{"deleted": {per-table counts}, "vacuumed": bool}`` — also
            embedded in the run's ``maintenance`` stats (``--json`` 可见).
        """
        backend = store if store is not None else self._store_for_run(dry_run=False)
        now = self._wall_clock()
        retention_days = self.config.storage.retention_days
        cadence = self.config.storage.vacuum
        metrics_retention_days = self._metrics_retention_days(retention_days)

        def _sync() -> dict[str, Any]:
            # 不传 active_urls:同一 myia.db 被所有品类共享(sqlite.py cleanup
            # docstring),单品类源清单无权修剪别的品类的 baseline/engine_hint
            # ——否则每轮 run 都会把共享库中其它品类的两套缓存当「过期源」删掉。
            deleted = backend.cleanup_expired(
                retention_days, now=now, metrics_retention_days=metrics_retention_days
            )
            vacuumed = backend.maybe_vacuum(cadence, now=now)
            return {"deleted": deleted, "vacuumed": vacuumed}

        return await asyncio.to_thread(_sync)

    def _tune_feedback_safely(self, store: Store) -> dict[str, Any] | None:
        """Run one feedback-tuning round (随调度周期执行;失败只告警).

        放在每轮 run 的**起点**:上轮推送后用户标了负反馈,本轮 analyze/push
        立即按新调参降权(若放维护收尾,生效会滞后一轮)。apply 按状态幂等,
        状态未变化的一轮不写调整行(调整历史只记录真实变化,可追溯)。

        报告携带 ``effective``:enrich 未启用时降权/静音在运行链路上没有消费者
        (条目无 score/类目),调参只入库不入 pipeline —— 如实标注,不让
        「反馈调参完成」谎报生效。
        """
        try:
            records = self._feedback_tuner.apply(store, now=self._wall_clock())
        except Exception as exc:  # noqa: BLE001 - 调参是尽力而为的旁路任务
            logger.warning("反馈调参失败(不影响本次运行): %s", exc)
            return None
        effective = self.config.enrich.enabled and self._enricher is not None
        if not records:
            logger.debug("反馈调参:本窗口无变化,未写调整")
            return {"applied": 0, "effective": effective}
        adjustments = [
            {"kind": record.kind, "payload": dict(record.payload)} for record in records
        ]
        if effective:
            logger.info("反馈调参完成 adjustments=%s", len(adjustments))
        else:
            logger.info(
                "反馈调参已入库 adjustments=%s(enrich 未启用:本轮推送未应用,"
                "无 score/类目可降权)", len(adjustments),
            )
        return {
            "applied": len(records),
            "adjustments": adjustments,
            "effective": effective,
            **({} if effective else {"note": "enrich 未启用,调参仅入库未生效"}),
        }

    async def _maintenance_safely(self, store: Store) -> dict[str, Any] | None:
        """End-of-run maintenance wrapper: 失败只告警,不影响 run 结果."""
        try:
            return await self.run_maintenance(store)
        except Exception as exc:  # noqa: BLE001 - 维护是尽力而为的旁路任务
            logger.warning("清理任务失败(不影响本次运行结果): %s", exc)
            return None

    # ---------------------------------------------------------- stage plumbing

    async def _attempt_stage(
        self,
        name: str,
        runner: Callable[[], Awaitable[Any]],
        report: StageReport,
        *,
        items_in: int,
        apply_timeout: bool = True,
    ) -> Any:
        """Run one stage with its timeout and retry policy.

        Retry re-invokes ``runner`` (a fresh coroutine each attempt). A stage
        that exhausts its retries sets ``report.status = failed`` and returns
        None; the run chain then skips downstream stages. Stage failures are
        isolated -- they never raise to the caller.

        Args:
            name: stage name (options lookup key).
            runner: zero-arg callable producing the stage coroutine.
            report: the stage's report, updated in place.
            items_in: item count entering the stage (observability).
            apply_timeout: False for fetch (its timeout lives per source).
        """
        options = self._stage_options[name]
        report.items_in = items_in
        last_error: Exception | None = None
        for attempt in range(options.retries + 1):
            report.attempts = attempt + 1
            stage_started = self._clock()
            try:
                if apply_timeout:
                    output = await asyncio.wait_for(runner(), timeout=options.timeout_seconds)
                else:
                    output = await runner()
                report.duration_seconds += self._clock() - stage_started
                if report.status == "pending":
                    report.status = "ok"
                return output
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 步骤失败:记录、按策略重试,不拖垮进程
                report.duration_seconds += self._clock() - stage_started
                last_error = exc
                logger.warning(
                    "步骤失败 stage=%s attempt=%s/%s error_type=%s: %s",
                    name, attempt + 1, options.retries + 1, classify_exception(exc), exc,
                )
                if attempt < options.retries:
                    delay = min(MAX_RETRY_BACKOFF_SECONDS, self._retry_backoff * (2**attempt))
                    logger.info("步骤重试退避 stage=%s delay=%.1fs", name, delay)
                    await self._sleep(delay)
        report.status = "failed"
        report.error = f"{classify_exception(last_error)}: {last_error}"
        logger.error(
            "步骤重试耗尽 stage=%s attempts=%s error=%s", name, report.attempts, report.error
        )
        return None

    def _record_item_failure(
        self, report: StageReport, item: Item, error_type: str, message: str
    ) -> None:
        """Record one item-level failure (部分失败语义:记录后继续)."""
        report.failures.append(
            {
                "source": item.source,
                "url": item.url,
                "title": item.title,
                "error_type": error_type,
                "message": message,
            }
        )
        logger.warning(
            "条目处理失败 stage=%s url=%s error_type=%s: %s",
            report.name, item.url, error_type, message,
        )

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff seconds before retry ``attempt`` (0-based)."""
        return min(MAX_RETRY_BACKOFF_SECONDS, self._retry_backoff * (2**attempt))

    # ----------------------------------------------------------------- stages

    async def _stage_fetch(
        self, context: FetchContext, report: StageReport
    ) -> tuple[list[SourceReport], list[Item]]:
        """Fetch all sources concurrently with per-source isolation.

        Each source gets its own timeout + retry budget (:meth:`_fetch_one`);
        a failure is recorded and the remaining sources proceed (失败隔离).
        """
        options = self._stage_options["fetch"]
        tasks = [
            asyncio.create_task(
                self._fetch_one(source, context, options), name=f"fetch:{source.name}"
            )
            for source in self.config.sources
        ]
        outcomes = await asyncio.gather(*tasks)
        source_reports: list[SourceReport] = []
        items: list[Item] = []
        for source_report, raw_items in outcomes:
            source_reports.append(source_report)
            for raw in raw_items:
                try:
                    items.append(Item.from_extracted(raw, source_report.name))
                except ValueError as exc:  # 缺 url —— 条目级失败,结构化记录后继续
                    report.failures.append(
                        {
                            "source": source_report.name,
                            "url": str(raw.get("url")),
                            "title": str(raw.get("title", "")),
                            "error_type": "invalid_item",
                            "message": str(exc),
                        }
                    )
                    logger.warning("提取条目无效 source=%s: %s", source_report.name, exc)
            logger.info(
                "采集完成 source=%s engine=%s items=%s attempts=%s skip=%s failures=%s",
                source_report.name,
                source_report.engine,
                source_report.item_count,
                source_report.attempts,
                source_report.skip_reason,
                len(source_report.failures),
            )
        report.status = "ok"
        logger.info(
            "采集步骤完成 sources=%s items=%s source_failures=%s",
            len(source_reports),
            len(items),
            sum(1 for s in source_reports if s.failed),
        )
        return source_reports, items

    async def _fetch_one(
        self, source: Any, context: FetchContext, options: StageOptions
    ) -> tuple[SourceReport, list[dict]]:
        """Run one source through its degrade chain with timeout + retry."""
        source_report = SourceReport(name=source.name, engine=source.engine, url=source.url)
        raw_items: list[dict] = []
        last_error: str | None = None
        last_error_type: str | None = None
        for attempt in range(options.retries + 1):
            source_report.attempts = attempt + 1
            fetch_started = self._clock()
            try:
                outcome: FetchOutcome = await asyncio.wait_for(
                    fetch_source(source, context), timeout=options.timeout_seconds
                )
            except asyncio.TimeoutError:
                source_report.duration_seconds += self._clock() - fetch_started
                last_error_type = "timeout"
                last_error = f"采集超时(>{options.timeout_seconds:.0f}s)"
                logger.warning(
                    "采集超时 source=%s attempt=%s/%s timeout=%ss",
                    source.name, attempt + 1, options.retries + 1, options.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # registry 承诺不抛;此处兜底基础设施异常
                source_report.duration_seconds += self._clock() - fetch_started
                last_error_type = classify_exception(exc)
                last_error = str(exc)
                logger.warning(
                    "采集异常 source=%s error_type=%s: %s", source.name, last_error_type, exc
                )
            else:
                source_report.duration_seconds += self._clock() - fetch_started
                source_report.engine = outcome.engine or source.engine
                source_report.skipped = outcome.skipped
                source_report.skip_reason = outcome.skip_reason
                source_report.failures.extend(f.to_dict() for f in outcome.failures)
                if outcome.engine is not None or not outcome.failures:
                    # 成功:registry 认定某引擎成功(outcome.engine 就位)——
                    # 包括健康空页(0 条)与指纹 skip;前序引擎的失败只留作
                    # 观测记录,不算源失败。仅当链条彻底耗尽才算失败。
                    raw_items = outcome.items
                    last_error = None
                    last_error_type = None
                    break
                last_error_type = outcome.failures[-1].error_type
                last_error = "所有引擎尝试失败: " + "; ".join(
                    f"{f.engine}:{f.error_type}:{f.message}" for f in outcome.failures
                )
                logger.warning(
                    "引擎链耗尽 source=%s engines=%s",
                    source.name,
                    [f.engine for f in outcome.failures],
                )
            if attempt < options.retries:
                delay = self._retry_delay(attempt)
                logger.info("采集重试退避 source=%s delay=%.1fs", source.name, delay)
                await self._sleep(delay)
        if raw_items:
            source_report.item_count = len(raw_items)
        elif last_error is not None:
            source_report.error = {
                "error_type": last_error_type or "unknown",
                "message": last_error,
            }
            logger.error(
                "采集最终失败 source=%s attempts=%s error_type=%s: %s",
                source.name, source_report.attempts, last_error_type, last_error,
            )
        return source_report, raw_items

    async def _stage_classify(
        self,
        items: list[Item],
        report: StageReport,
        store: Store,  # noqa: ARG002 - 分类无副作用
        registry: DedupRegistry,  # noqa: ARG002
        dry_run: bool,  # noqa: ARG002
    ) -> list[Item]:
        """Keyword pre-filter: unmatched titles drop (skip 是正常路径,必须可见)."""
        kept: list[Item] = []
        for item in items:
            view = item.view()
            try:
                if self.config.classify.builtin:
                    verdict = classify_item(
                        view, table=self._classify_table, rules=self._classify_rules
                    )
                    if verdict.category is None:
                        report.skips["classify_unmatched"] += 1
                        logger.debug("分类未命中,丢弃 title=%r url=%s", item.title, item.url)
                        continue
                    item.category = verdict.category
                    item.add_tags(verdict.tags)
                else:
                    tags = [
                        rule.tag or rule.name
                        for rule in self._classify_rules
                        if rule.evaluate(view)
                    ]
                    item.add_tags(tags)
            except TypeError as exc:  # 缺 title / title 非字符串
                self._record_item_failure(report, item, "missing_title", str(exc))
                continue
            kept.append(item)
        report.items_out = len(kept)
        report.status = "ok"
        logger.info(
            "分类步骤完成 items_in=%s retained=%s discarded=%s",
            len(items), len(kept), report.skips.get("classify_unmatched", 0),
        )
        return kept

    async def _stage_dedup(
        self,
        items: list[Item],
        report: StageReport,
        store: Store,
        registry: DedupRegistry,
        dry_run: bool,  # noqa: ARG002 - store 已按 dry_run 选定
    ) -> list[Item]:
        """Composite-key dedup; survivors are persisted to the items table."""
        kept: list[Item] = []
        slot_context = registry.key_context()
        for item in items:
            view = item.view()
            try:
                # 保留字段({date}/{slot})仅兜底;条目同名字段优先。
                key = DedupRegistry.make_key(self.config.dedup.key, {**slot_context, **view})
            except ValueError as exc:
                self._record_item_failure(report, item, "dedup_key_error", str(exc))
                continue
            if registry.is_seen(key):
                report.skips["dedup_seen"] += 1
                logger.info("去重跳过(已见过) key=%s url=%s", key, item.url)
                continue
            try:
                # 先入库、后登记已见:save 失败(或两次提交间中断)不得把 key
                # 永久毒化进注册表——宁可下轮重入库(at-least-once),不可静默丢失。
                store.save_item(
                    ItemRecord(
                        url=item.url,
                        dedup_key=key,
                        title=item.title,
                        source=item.source,
                        content=item.content,
                        tags=list(item.metadata.get("tags") or []),
                        category=item.category,
                        raw=item.metadata,
                    )
                )
                registry.mark_seen(key)
            except ValueError as exc:  # store 校验失败(如空 title)
                self._record_item_failure(report, item, "store_error", str(exc))
                continue
            item.dedup_key = key
            kept.append(item)
        report.items_out = len(kept)
        report.status = "ok"
        logger.info(
            "去重步骤完成 items_in=%s new=%s seen=%s key_failed=%s",
            len(items),
            len(kept),
            report.skips.get("dedup_seen", 0),
            len(report.failures),
        )
        return kept

    async def _stage_analyze(
        self,
        items: list[Item],
        report: StageReport,
        store: Store,
        registry: DedupRegistry,  # noqa: ARG002 - 路由语义与去重注册表无关
        dry_run: bool,  # noqa: ARG002 - 缓存写入跟随 store(dry-run 即内存库)
    ) -> list[Item]:
        """LLM precision scoring (v0.2 enrich); disabled → v0.1 pass-through.

        Enabled: the enricher backfills ``Item.scores`` + scalar
        ``metadata['score']`` (what push.route score rules read) and the
        ``items`` 表 rows; budget exhaustion / endpoint failure degrade to
        pure keyword filtering inside the enricher (WARNING + stage skips),
        unscored items flow on with no score so routing falls back to the
        v0.1 category mapping (无 score 缺省行为对齐). Items are never dropped
        here — scoring decorates, it does not filter.

        Raises:
            Exception: only infrastructure-level surprises; enrichConfig was
                validated at construction. Endpoint/parse problems are
                isolated inside the enricher and surface as report skips.
        """
        if not self.config.enrich.enabled or self._enricher is None:
            report.items_out = len(items)
            report.status = "ok"
            logger.info("分析步骤直通(enrich 未启用,仅关键词粗筛) items=%s", len(items))
            return items
        # 反馈闭环(v0.3):负反馈调参并入 watchlist —— 权重 0.0 的词加入
        # mute(enricher 零 token 全降权),其余降权在评分后按条目施加。
        tuning = load_active_tuning(store)
        # v0.4 共享预算:管线自建的默认 enricher 消费 run 级预算池(与事件判重
        # 同一个池);注入的第三方 enricher 保持自有契约,不强加参数。
        enrich_kwargs: dict[str, Any] = {}
        if isinstance(self._enricher, LLMEnricher) and self._run_budget is not None:
            enrich_kwargs["budget"] = self._run_budget
        outcome: EnrichOutcome = await self._enricher.enrich(
            items, watchlist=self._effective_watchlist(tuning), store=store, **enrich_kwargs
        )
        report.skips["enrich_muted"] += outcome.muted
        report.skips["enrich_cache_hit"] += outcome.cached
        if outcome.degraded:
            report.skips[f"enrich_degraded_{outcome.degrade_reason or 'unknown'}"] += (
                outcome.unscored
            )
            logger.warning(
                "enrich 降级纯粗筛 reason=%s unscored=%s(无 score 条目按 v0.1 大类路由)",
                outcome.degrade_reason, outcome.unscored,
            )
        # 条目级 enrich 失败 = 降级注记,不并入 report.failures:条目本身已按
        # v0.1 路由成功投递,把常态性的模型漏评/批次超时翻成 partial(退出码 3)
        # 会让 agent/CI 无法区分真失败与免费端点抖动(降级优于中断)。
        report.warnings.extend(dict(failure) for failure in outcome.failures)
        report.skips["enrich_unscored_failures"] += len(outcome.failures)
        for failure in outcome.failures:
            logger.warning(
                "enrich 条目未评分(降级继续) url=%s error_type=%s: %s",
                failure.get("url"), failure.get("error_type"), failure.get("message"),
            )
        self._apply_feedback_penalties(items, tuning, report)
        report.items_out = len(items)
        report.status = "ok"
        logger.info(
            "精评步骤完成 items=%s scored=%s cached=%s muted=%s unscored=%s "
            "tokens=%s failures=%s feedback_downweighted=%s",
            len(items), outcome.scored, outcome.cached, outcome.muted,
            outcome.unscored, outcome.tokens_used, len(outcome.failures),
            report.skips.get("feedback_downweighted", 0),
        )
        return items

    # ------------------------------------------------------ event aggregation

    async def _stage_aggregate(
        self,
        items: list[Item],
        report: StageReport,
        store: Store,
        registry: DedupRegistry,  # noqa: ARG002 - 聚合与去重注册表正交(dedup 先行)
        dry_run: bool,  # noqa: ARG002 - 缓存写入跟随 store(dry-run 即内存库)
    ) -> list[Item]:
        """多源同事件合并(v0.4);dedup 之后、push 之前。

        两级判重:标题相似度粗筛(零 token)圈候选 → LLM 判重精筛确认(并入
        enrich 批量/缓存/预算护栏)。判为同事件的条目合并为单卡条目:主条目
        (评分最高,平分取先出现)+ metadata ``also_seen``(另见 N 源)+ ``merged_sources``
        计数 + ``aggregated`` 标签;digest/immediate 路由均消费合并后的形态。
        降级(预算耗尽/端点失败)= 候选组不合并照常推送(漏合并只多推几张卡,
        误合并会丢事件——保守方向永远是不合并)。
        """
        if self._aggregator is None or len(items) < 2:
            report.items_out = len(items)
            report.status = "ok"
            logger.info("聚合步骤直通(未启用或条目不足) items=%s", len(items))
            return items
        outcome: AggregateOutcome = await self._aggregator.aggregate(
            items, store=store, budget=self._run_budget
        )
        report.skips["aggregate_cached_groups"] += outcome.cached_groups
        if outcome.degraded:
            report.skips[f"aggregate_degraded_{outcome.degrade_reason or 'unknown'}"] += 1
            logger.warning(
                "事件聚合降级(候选组不合并,照常推送) reason=%s groups=%s",
                outcome.degrade_reason, outcome.coarse_groups,
            )
        # 条目级失败与 analyze 同风格:降级注记(warnings),不翻 partial——
        # 条目本身照常投递,只有合并这一增强动作失败了。
        report.warnings.extend(dict(failure) for failure in outcome.failures)
        merged = self._merge_items(items, outcome.components)
        report.items_out = len(merged)
        report.status = "ok"
        logger.info(
            "聚合步骤完成 items_in=%s coarse_groups=%s merged_groups=%s absorbed=%s "
            "llm_calls=%s cached=%s tokens=%s failures=%s",
            len(items), outcome.coarse_groups, outcome.merged_groups,
            outcome.absorbed_items, outcome.llm_calls, outcome.cached_groups,
            outcome.tokens_used, len(outcome.failures),
        )
        return merged

    def _merge_items(self, items: list[Item], components: list[list[int]]) -> list[Item]:
        """Apply merge components: one merged Item per group, others pass through.

        主条目取组内标量 ``metadata['score']`` 最高者(无分视为 -1,平分取先
        出现);被合并条目从列表消失(其 dedup key 已在 dedup 阶段入库登记,
        次轮同 URL 天然被拦)。组件外的条目原样保留,顺序不变。
        """
        if not components:
            return items
        main_of: dict[int, int] = {}  # absorbed index -> main index
        component_by_main: dict[int, list[int]] = {}
        for component in components:
            main_index = max(
                component,
                key=lambda i: (
                    items[i].metadata.get("score")
                    if isinstance(items[i].metadata.get("score"), (int, float))
                    and not isinstance(items[i].metadata.get("score"), bool)
                    else -1,
                    -i,  # 平分取先出现(下标小者优先)
                ),
            )
            component_by_main[main_index] = [i for i in component if i != main_index]
            for index in component_by_main[main_index]:
                main_of[index] = main_index
        merged_items: list[Item] = []
        for index, item in enumerate(items):
            if index in component_by_main:
                merged_items.append(self._merge_group(item, [items[i] for i in component_by_main[index]]))
            elif index in main_of:
                continue  # 被合并进主条目
            else:
                merged_items.append(item)
        return merged_items

    def _merge_group(self, main: Item, others: list[Item]) -> Item:
        """One merged card item: main entry + 「另见 N 源」 metadata."""
        merged = Item(
            url=main.url,
            title=main.title,
            source=main.source,
            category=main.category,
            scores=dict(main.scores) if main.scores is not None else None,
            dedup_key=main.dedup_key,
            content=main.content,
            metadata=dict(main.metadata),
        )
        merged.metadata["also_seen"] = [
            {"title": other.title, "url": other.url, "source": other.source}
            for other in others
        ]
        merged.metadata["merged_sources"] = 1 + len(others)
        merged.add_tags(["aggregated"])
        logger.info(
            "同事件合并主条目 url=%s title=%r merged_sources=%s",
            merged.url, merged.title, merged.metadata["merged_sources"],
        )
        return merged

    # ------------------------------------------------------- feedback tuning

    def _effective_watchlist(self, tuning: ActiveTuning) -> dict[str, list[str]]:
        """watchlist 视图:反馈调出权重 0.0 的词并入 mute(零 token 全降权)。

        enricher 接受 Mapping 形态的 watchlist(myia.enrich._watchlist_lists
        契约),因此无需修改 enrich 层即可施加负反馈词表;未触发降权(权重
        0.0)的词不进 mute,走 :meth:`_apply_feedback_penalties` 的分数乘法。
        """
        mute = list(self.config.watchlist.mute)
        for word in tuning.effective_mute_words():
            if word not in mute:
                mute.append(word)
        return {"keywords": list(self.config.watchlist.keywords), "mute": mute}

    def _apply_feedback_penalties(
        self, items: list[Item], tuning: ActiveTuning, report: StageReport
    ) -> None:
        """负反馈降权落地:命中类目/词的条目按权重缩放标量 score。

        ``metadata['score']`` 是 push.route 阈值规则读取的标量:权重 0.3 的
        条目 score 9.0 → 2.7,``score < 5 → archive`` 一类规则随即承接(降权
        → 归档由用户已声明的路由语义完成,反馈闭环不直接丢条目)。无 score
        条目只带标记(观测可见,路由语义不变——v0.1 无 score 行为对齐)。
        """
        if not tuning.has_penalties:
            return
        for item in items:
            weight = tuning.penalty_for(category=item.category, title=item.title)
            if weight >= 1.0:
                continue
            item.metadata["feedback_weight"] = weight
            score = item.metadata.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                item.metadata["score"] = round(float(score) * weight, 1)
            item.add_tags(["feedback-muted"])
            report.skips["feedback_downweighted"] += 1
            logger.info(
                "反馈降权生效 url=%s category=%s weight=%s score=%s",
                item.url, item.category, weight, item.metadata.get("score"),
            )

    def _prepare_trend_context(
        self, items: list[Item], store: Store | None, *, dry_run: bool = False
    ) -> None:
        """v0.4 趋势基线运行路径接线(PRD 10-01-v04-trend-baseline)。

        每轮 push 前执行一次:把本轮(post-dedup)条目的数值字段与关键词提及
        写入 ``metric_history`` 快照,再装配 ``TrendTable`` / 关键词周环比 /
        MSRP 对照,随后逐通道 :meth:`set_trend_context` —— 模板的
        ``vs_yesterday`` / ``vs_last_week`` / ``vs_msrp`` / ``keyword_trends``
        由此拿到真数据。baseline 未启用时为空集,渲染行为与旧契约一致
        (对比渲染为空)。快照只记本轮新条目:dedup 已把重复条目挡在 push 前,
        历史不会因未变更条目反复膨胀。
        """
        self._run_trend_kwargs = {}
        if dry_run:
            return  # dry-run 零副作用:不写快照,也不装配发送上下文
        baseline = self.config.baseline
        if baseline is None or not baseline.enabled or store is None or not items:
            return
        now = self._wall_clock()
        category = self.config.id
        if baseline.fields:
            record_item_metrics(
                store, category=category, items=items, fields=baseline.fields, now=now
            )
            self._run_trend_kwargs["trends"] = build_trend_table(
                store, category=category, items=items, fields=baseline.fields,
                now=now, tz=self._tz, windows=baseline.windows,
            )
        keywords = list(self.config.watchlist.keywords)
        if keywords:
            record_keyword_mentions(
                store, category=category, items=items, keywords=keywords, now=now
            )
            self._run_trend_kwargs["keyword_trends"] = build_keyword_trends(
                store, category=category, keywords=keywords, now=now, tz=self._tz
            )
        if baseline.msrp:
            self._run_trend_kwargs["msrp"] = dict(baseline.msrp)
        logger.info(
            "趋势基线上下文已装配 category=%s fields=%s keywords=%s msrp=%s",
            category, list(baseline.fields), len(keywords), bool(baseline.msrp),
        )

    async def _stage_push(
        self,
        items: list[Item],
        report: StageReport,
        store: Store,
        registry: DedupRegistry,
        dry_run: bool,
    ) -> list[ChannelPushReport]:
        """Route per channel (immediate/digest/archive) and dispatch.

        immediate 发完即推,digest 合并为一张卡(同槽位防重发共享 AM/PM 注册表),
        archive 仅入库不推送。dry_run 只产出路由判定,不发送。
        """
        pushes: list[ChannelPushReport] = []
        if not self.config.push:
            report.items_out = len(items)
            report.status = "ok"
            logger.info("未配置 push 通道,条目仅入库 items=%s", len(items))
            return pushes
        if not dry_run:
            # Q4 定案:run 前节流懒刷目录(发现属持久化副作用,dry-run 不做)。
            await self._refresh_directory_if_stale()
        self._prepare_trend_context(items, store, dry_run=dry_run)
        for index, (push, rules) in enumerate(zip(self.config.push, self._push_routes)):
            pushes.append(
                await self._push_channel(index, push, rules, items, registry, report, dry_run)
            )
        report.items_out = len(items)
        report.status = "ok"
        return pushes

    async def _refresh_directory_if_stale(self) -> None:
        """目录节流懒刷(grill Q4 定案,Hermes housekeeping 的 MYIA 等价物)。

        距上次刷新 > :data:`REFRESH_STALE_SECONDS` 且存在已注册平台
        (:data:`myia.push.PLATFORMS`)才触发 ``discover_directory``;单平台
        失败退回旧桶 + 结构化告警(directory.refresh 内隔离),整体失败也
        不阻塞推送。core 未注册平台时零开销短路。
        """
        if not PLATFORMS:
            return
        now = self._wall_clock().timestamp()
        age = self._channel_directory.age_seconds(now=now)
        if age is not None and age <= REFRESH_STALE_SECONDS:
            logger.debug("目录尚新鲜,跳过懒刷: age=%.0fs", age)
            return
        adapters: dict[str, Any] = {}
        for platform, platform_cls in PLATFORMS.items():
            # 平台 → 找到对应通道的 push 条目,用其凭据构建发现适配器。
            for push in self.config.push:
                channel_cls = CHANNELS.get(push.channel)
                if channel_cls is not None and channel_cls is platform_cls:
                    adapters[platform] = self._build_channel(push)
                    break
        if not adapters:
            logger.debug("无已注册平台的 push 条目,跳过目录懒刷")
            return
        try:
            counts = await self._channel_directory.refresh(adapters, now=now)
        except Exception as exc:  # noqa: BLE001 - 刷新失败退回旧目录,绝不阻塞推送
            logger.warning("目录懒刷失败,退回旧目录继续推送: error=%s", exc)
            return
        if counts:
            logger.info("目录懒刷完成: %s", counts)

    async def _push_channel(
        self,
        entry_index: int,
        push: Any,
        rules: list[Any],
        items: list[Item],
        registry: DedupRegistry,
        report: StageReport,
        dry_run: bool,
    ) -> ChannelPushReport:
        """Route + dispatch one channel; failures stay isolated per channel.

        digest 条目进 :attr:`_digest_pools` 里该 push 项的常驻聚合器:flush
        全通道失败时条目留池,下一次 run 的 flush 自动重试(digest.py 契约
        落地到管线接线;同槽位拦截照常生效,不会重发已成功的条目)。
        """
        out = ChannelPushReport(channel=push.channel, dry_run=dry_run)
        buckets: dict[str, list[Item]] = {"immediate": [], "digest": [], "archive": []}
        # 定向对象有效值(design D4 优先级):规则 targets > 通道级 targets >
        # legacy 单 target(最后者不走定向,由通道自带 target 兜底)。
        channel_specs = list(push.targets) if push.targets else None
        immediate_specs: list[list[str] | None] = []
        digest_specs: list[list[str] | None] = []
        for item in items:
            decision = resolve_route(item.view(), rules)
            buckets[decision.mode].append(item)
            specs: list[str] | None = None
            if decision.targets:
                specs = list(decision.targets)
            elif channel_specs:
                specs = list(channel_specs)
            if decision.mode == "immediate":
                immediate_specs.append(specs)
            elif decision.mode == "digest":
                digest_specs.append(specs)
            out.decisions.append(
                {
                    "url": item.url,
                    "title": item.title,
                    "mode": decision.mode,
                    "reason": decision.reason,
                    "rule_when": decision.rule_when,
                }
            )
        out.immediate = len(buckets["immediate"])
        out.digest = len(buckets["digest"])
        out.archive = len(buckets["archive"])

        if dry_run:
            logger.info(
                "dry-run 跳过实际推送 channel=%s immediate=%s digest=%s archive=%s",
                push.channel, out.immediate, out.digest, out.archive,
            )
            return out

        channel = self._build_channel(push)
        if self._run_trend_kwargs:
            channel.set_trend_context(**self._run_trend_kwargs)
        reports: list[SendReport] = []
        now = self._wall_clock()  # 槽位/防重发一律用注入时钟(测试钉死日期不漂移)
        if buckets["immediate"]:
            immediate_reports = await send_immediate(
                buckets["immediate"],
                channels=[channel],
                registry=registry,
                tz=self._tz,
                now=now,
                category=self.config.name,
                item_specs=immediate_specs,
                directory=self._channel_directory,
                ledger=self._delivery_ledger,
            )
            # 抑制计数:与 send_immediate 内部同一判定(should_send 纯读),
            # 定向条目一条多报告,不能用「条目数 − 报告数」推算。
            suppressed = sum(
                1
                for item in buckets["immediate"]
                if (key := item.dedup_key or item.url)
                and not registry.should_send(key, now=now)
            )
            if suppressed > 0:
                report.skips["slot_suppressed"] += suppressed
                logger.info(
                    "立即推送同槽位拦截 channel=%s suppressed=%s", push.channel, suppressed
                )
            reports.extend(immediate_reports)
        if buckets["digest"]:
            aggregator = self._digest_pools.get(entry_index)
            if aggregator is None:
                aggregator = DigestAggregator(channels=[channel], registry=registry, tz=self._tz)
                self._digest_pools[entry_index] = aggregator
            added_keys: list[str] = []
            digest_items = zip(buckets["digest"], digest_specs)
            for item, specs in digest_items:
                key = item.dedup_key or item.url
                if not registry.should_send(key, now=now):
                    report.skips["slot_suppressed"] += 1
                    logger.info("摘要同槽位拦截 channel=%s key=%s", push.channel, key)
                    continue
                aggregator.add(item, dedup_key=key, targets=specs)
                added_keys.append(key)
            if len(aggregator):
                digest_reports = await aggregator.flush(
                    now=now,
                    category=self.config.name,
                    directory=self._channel_directory,
                    ledger=self._delivery_ledger,
                )
                reports.extend(digest_reports)
                if digest_reports and all(not r.ok for r in digest_reports):
                    # 全通道失败:条目已留池(本进程内下次 flush 重试)。池在
                    # 进程内:进程重启即丢且 partial 前任不可续跑——留池条目
                    # 会永久丢失,这里必须把该风险说破,不假装有持久化兜底。
                    logger.warning(
                        "摘要全通道失败,条目留池待下轮 run 重试 channel=%s keys=%s;"
                        "注意:摘要池在进程内,进程重启将丢失这批条目(无 store 回填),"
                        "请尽快恢复通道并保持进程存活",
                        push.channel,
                        len(added_keys),
                    )
        if buckets["archive"]:
            logger.info(
                "归档条目(仅入库不推送) channel=%s count=%s", push.channel, len(buckets["archive"])
            )
        out.reports = reports
        failed = [r for r in reports if not r.ok]
        out.ok = not failed
        if failed:
            logger.warning(
                "通道存在发送失败 channel=%s failed=%s/%s", push.channel, len(failed), len(reports)
            )
        logger.info(
            "推送完成 channel=%s immediate=%s digest=%s archive=%s ok=%s",
            push.channel, out.immediate, out.digest, out.archive, out.ok,
        )
        return out

    # --------------------------------------------------------------- forever

    async def run_forever(
        self,
        *,
        trigger: CronTrigger | None = None,
        scheduler: AsyncIOScheduler | None = None,
        max_fires: int | None = None,
        dry_run: bool = False,
    ) -> int:
        """Resident mode: fire ``run`` on the category cron until stopped.

        Storage maintenance (retention cleanup + cadence-gated VACUUM +
        反馈调参) rides every fired run — the cleanup task follows the
        scheduling cycle and requires no separate APScheduler job. When a
        ``telegram`` push channel is configured, a background task polls
        ``getUpdates`` for feedback callbacks (桌面形态接收, grill Q7) and
        ingests them into the same store; polling failures log-and-continue.

        **一个 bot token 只允许一个轮询方**(素材 12):所有 telegram 通道的
        bot token 都解析自 ``env:TELEGRAM_BOT_TOKEN``,因此每个配置了 telegram
        通道的常驻进程都会轮询同一条 getUpdates 流——Telegram 对并发轮询方
        回 **409 Conflict** 互踢。同一 token 下至多一个品类以 ``--loop`` 常驻
        (其余品类换推送渠道,或不常驻);``myia doctor`` 以
        ``telegram_token_poll_conflict`` finding 提示多品类共配的情形。

        Args:
            trigger: prebuilt trigger override (tests use fine-grained cron);
                defaults to ``build_cron_trigger(schedule, timezone)``.
            scheduler: injected scheduler; defaults to a fresh
                :class:`AsyncIOScheduler` bound to the running loop.
            max_fires: stop after N fires (test seam; None = run forever).
            dry_run: forwarded to every fired :meth:`run`.

        Returns:
            Number of fires (a fire whose run raised is logged and still
            counted -- 调度不中断).
        """
        effective_trigger = trigger or build_cron_trigger(self.config.schedule, self.config.timezone)
        sched = scheduler or AsyncIOScheduler()
        fires = 0
        feedback_poller = self._build_feedback_poller()
        poll_task = (
            asyncio.create_task(
                self._feedback_poll_loop(feedback_poller),
                name=f"myia:feedback-poll:{self.config.id}",
            )
            if feedback_poller is not None
            else None
        )

        async def _fire() -> None:
            nonlocal fires
            fires += 1
            logger.info("定时触发 category=%s fire=%s", self.config.id, fires)
            try:
                result = await self.run(dry_run=dry_run)
                logger.info(
                    "定时运行完成 category=%s status=%s run_id=%s",
                    self.config.id, result.status, result.run_id,
                )
            except Exception as exc:  # 单次运行异常不拖垮调度
                logger.error(
                    "定时运行异常(调度继续) category=%s error=%s", self.config.id, exc, exc_info=True
                )
            if max_fires is not None and fires >= max_fires:
                sched.shutdown(wait=False)

        sched.add_job(
            _fire,
            effective_trigger,
            id=f"myia:run:{self.config.id}",
            name=f"MYIA run {self.config.id}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        sched.start()
        logger.info(
            "常驻调度已启动 category=%s schedule=%r timezone=%s",
            self.config.id, self.config.schedule, self.config.timezone or "系统本地时区",
        )
        try:
            while sched.running:
                await asyncio.sleep(0.1)
        finally:
            if poll_task is not None:
                poll_task.cancel()
                await asyncio.gather(poll_task, return_exceptions=True)
            if sched.running:
                sched.shutdown(wait=False)
        logger.info("常驻调度已停止 category=%s fires=%s", self.config.id, fires)
        return fires

    # ------------------------------------------------------ TG feedback poll

    def _build_feedback_poller(self) -> TelegramFeedbackPoller | None:
        """TG 反馈轮询器(桌面形态接收, grill Q7):仅配置了 telegram 通道时启用。

        bot 凭据不可解析(未设 env 等)= 未启用(结构化 INFO,不告警——桌面
        用户没配 TG 是正常态);轮询失败在循环内 log-and-continue。同 token
        并发轮询约束(409)见 :meth:`run_forever` docstring——本方法不判定
        其他进程是否也在轮询,跨进程互斥交由部署形态保证。

        目录 sink(10-03-messaging-telegram D2):轮询看到的每个会话被动
        merge 进 ``telegram`` 桶(Telegram Bot API 无「列出会话」能力,这是
        目录条目的唯一来源);sink 旁路,抛错只记日志不中断轮询。
        """
        if not any(push.channel == "telegram" for push in self.config.push):
            return None
        try:
            return TelegramFeedbackPoller(on_chat=self._merge_telegram_chat)
        except TelegramFeedbackError as exc:
            logger.info("TG 反馈轮询未启用(bot 凭据不可解析): %s", exc)
            return None

    def _merge_telegram_chat(self, entry: ChannelEntry) -> None:
        """poller 目录 sink 的落点:单条观测 → 通道目录 ``telegram`` 桶。

        CLI/server 单发形态不跑轮询(目录只靠手工别名 + 直达 id,合法态);
        常驻/桌面形态经 feedback 轮询逐条积累。best-effort:merge 内部
        原子落盘,失败退化内存态(directory 契约)。
        """
        self._channel_directory.merge_entries("telegram", [entry])

    async def _feedback_poll_loop(self, poller: TelegramFeedbackPoller) -> None:
        """Resident-mode background loop: poll getUpdates → ingest feedback.

        每 ``feedback_poll_interval`` 秒一轮;offset 书签在轮次间保持(Telegram
        对未确认更新会重发,书签避免重复入库);回调经
        :func:`myia.feedback.ingest_callbacks` 入库(单条失败不拖垮整批)。
        """
        logger.info(
            "TG 反馈轮询已启动 category=%s interval=%ss",
            self.config.id, self._feedback_poll_interval,
        )
        offset: int | None = None
        while True:
            try:
                result = await poller.poll(offset=offset)
                offset = result.next_offset or offset
                if result.callbacks:
                    store = self._store_for_run(dry_run=False)
                    report = ingest_callbacks(store, result.callbacks)
                    logger.info(
                        "TG 反馈轮询入库 saved=%s skipped=%s failures=%s",
                        report.saved, report.skipped, len(report.failures),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 轮询失败不拖垮常驻调度
                logger.warning("TG 反馈轮询失败(下个周期重试): %s", exc)
            await asyncio.sleep(self._feedback_poll_interval)
