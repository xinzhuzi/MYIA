"""Event aggregation: multi-source same-event merging (v0.4 分析侧深水件).

URL dedup cannot catch the same event reported by N sources (PRD
10-01-v04-event-aggregation: 不合并会一事件推五遍). This module implements the
two-level dedup that folds them into one merged card:

- **Level 1 — coarse screen (local, zero token)**: normalized titles become
  character shingle sets (:func:`title_shingles`); pairs whose Jaccard
  similarity reaches the configured threshold *and* whose parseable
  publish timestamps (when both items carry one) sit inside the aggregation
  time window are unioned into candidate groups (:func:`coarse_groups`).
  The coarse screen only generates candidates — precision comes from L2.
- **Level 2 — LLM confirmation**: candidate groups are batched
  (``enrich.batch`` groups per request) to the shared OpenAI-compatible
  endpoint, which partitions each group into same-event clusters. The
  endpoint settings, model, batch size, token budget and cache table are
  reused from the enrich scoring rails (PRD: 并入 enrich 批量与预算护栏) —
  pair verdicts live in the store's ``enrich_cache`` under a
  dedupe-prompt-versioned key, so identical pair questions are never billed
  twice (验收: LLM 判重走缓存与预算,不重复计费).

Failure model: budget exhausted / endpoint failure / unparseable response →
the affected groups stay **unmerged** and flow on as separate cards
(structured failures + WARNING; 降级优于中断). The degradation direction is
deliberately conservative: 漏合并只多推几张卡,误合并会永久丢事件 — so any
doubt resolves to "not the same event", never to a merge.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from shishi.enrich.client import OpenAICompatClient
from shishi.enrich.errors import EnrichConfigError
from shishi.enrich.prompt import PromptTemplate, load_dedupe_prompt
from shishi.enrich.scoring import BudgetTracker, ScoreParseError, extract_json_entries
from shishi.enrich.settings import EnrichSettings, resolve_endpoint
from shishi.schema import AggregateConfig, EnrichConfig
from shishi.store import Store

if TYPE_CHECKING:  # 循环避免:仅类型注解引用 pipeline.Item(运行时鸭子类型)
    from shishi.pipeline import Item

__all__ = [
    "GROUP_ITEM_CONTENT_CHARS",
    "SHINGLE_SIZE",
    "TIMESTAMP_METADATA_KEYS",
    "AggregateOutcome",
    "DedupeParseError",
    "EventAggregator",
    "coarse_groups",
    "jaccard_similarity",
    "pair_cache_key",
    "parse_dedupe_payload",
    "parse_item_time",
    "title_shingles",
    "title_similarity",
    "within_window",
]

logger = logging.getLogger(__name__)

#: Character shingle size for the title coarse screen (char bigrams work for
#: CJK and Latin alike without a tokenizer — 简单可靠优先).
SHINGLE_SIZE = 2

#: Metadata keys probed (in order) for an item publish timestamp. Items
#: without any parseable timestamp cannot be proven out-of-window and always
#: count as in-window (无法排除 ≠ 排除).
TIMESTAMP_METADATA_KEYS = (
    "published_at",
    "published",
    "pub_date",
    "time",
    "datetime",
    "created_at",
    "date",
)

#: Per-item content snippet cap inside the dedup prompt (token 成本护栏;
#: 标题加一小段摘录已足以区分「相似但不同」).
GROUP_ITEM_CONTENT_CHARS = 200

#: Cache-key namespace for pair verdicts (scores_key slot of enrich_cache):
#: dedupe prompt version fingerprints in, prompt 变更即全部重问.
DEDUPE_CACHE_KEY_PREFIX = "event-dedupe"

_NOISE_RE = re.compile(r"[\W_]+", re.UNICODE)

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M")


class DedupeParseError(ValueError):
    """The LLM response carried no usable dedup payload at all (batch-level)."""


@dataclass
class AggregateOutcome:
    """One ``aggregate`` call's observable result (stage report 逐字段消费).

    消费方是 pipeline ``_stage_aggregate`` 的显式字段接线(skips/warnings/
    合并决策),没有整体序列化形态——``to_dict`` 曾虚构「run stats / myia
    doctor」消费方且全仓零调用,v1.1 已删除(PRD 10-02-v11-low-baseline-
    aggregate #17)。

    Attributes:
        requested: items handed to the aggregator.
        coarse_groups: level-1 candidate groups (>= 2 items each).
        candidates: items inside those candidate groups.
        llm_calls: LLM requests actually fired (cache-served groups excluded).
        cached_groups: groups fully resolved from cached pair verdicts
            (zero LLM token — 不重复计费).
        merged_groups: groups actually merged after level 2.
        absorbed_items: items folded into a main entry (requested − pushed).
        components: merge decisions as index lists into the input item list
            (each length >= 2; the pipeline builds merged cards from these).
        tokens_used: observed token usage spent this call.
        degraded: True when candidate groups were left unmerged.
        degrade_reason: ``budget_exhausted`` | ``llm_batch_failed`` | None.
        failures: structured per-item failure records (url/title/error_type/
            message) — same shape as pipeline stage failures.
    """

    requested: int = 0
    coarse_groups: int = 0
    candidates: int = 0
    llm_calls: int = 0
    cached_groups: int = 0
    merged_groups: int = 0
    absorbed_items: int = 0
    components: list[list[int]] = field(default_factory=list)
    tokens_used: int = 0
    degraded: bool = False
    degrade_reason: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Level 1: local zero-token title similarity
# ---------------------------------------------------------------------------


def title_shingles(title: str, size: int = SHINGLE_SIZE) -> frozenset[str]:
    """Normalized title as a character-shingle set (case/punct-insensitive).

    Whitespace and punctuation are stripped before shingling so
    「iPhone 18 发布」 and ``iPhone18发布`` produce identical sets; titles
    shorter than ``size`` collapse to a single whole-string shingle (empty
    titles yield an empty set — they never match anything).
    """
    normalized = _NOISE_RE.sub("", (title or "").casefold())
    if not normalized:
        return frozenset()
    if len(normalized) <= size:
        return frozenset({normalized})
    return frozenset(normalized[i : i + size] for i in range(len(normalized) - size + 1))


def jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard coefficient of two shingle sets (0.0 when both are empty)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def title_similarity(a: str, b: str) -> float:
    """Coarse-screen similarity of two titles (shingle Jaccard)."""
    return jaccard_similarity(title_shingles(a), title_shingles(b))


def _parse_time_value(value: Any) -> datetime | None:
    """Best-effort parse of one timestamp-ish value; None on any failure.

    Accepts epoch numbers and a few common string shapes (ISO first); naive
    datetimes are interpreted as UTC so comparisons stay consistent. Dirty or
    missing timestamps are the norm across sources — fail-quiet by design.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in _TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_item_time(item: Any) -> datetime | None:
    """The item's publish timestamp from its metadata, or None (查不到不排除)."""
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    for key in TIMESTAMP_METADATA_KEYS:
        parsed = _parse_time_value(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def within_window(a: datetime, b: datetime, window_hours: float) -> bool:
    """Whether two timestamps sit inside the same-event window (hours)."""
    if window_hours <= 0:
        raise ValueError(f"字段校验失败: window_hours 必须为正数,得到 {window_hours}")
    gap = abs((a - b).total_seconds())
    return gap <= window_hours * 3600.0


def coarse_groups(
    items: Sequence[Any],
    *,
    threshold: float,
    window_hours: float,
) -> list[list[int]]:
    """Level-1 candidate groups over item indices (union-find clustering).

    A pair joins a group when ``title_similarity >= threshold`` *and* the
    pair is in-window (a pair where either side lacks a parseable timestamp
    is always in-window). Returns groups of length >= 2, each sorted, ordered
    by first index — deterministic for stable prompts and tests.

    Raises:
        ValueError: ``threshold`` outside (0, 1] or non-positive window.
    """
    if not 0 < threshold <= 1:
        raise ValueError(f"字段校验失败: similarity_threshold 应在 (0, 1] 内,得到 {threshold}")
    shingles = [title_shingles(getattr(item, "title", "") or "") for item in items]
    times = [parse_item_time(item) for item in items]
    parent = list(range(len(items)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if times[i] is not None and times[j] is not None and not within_window(
                times[i], times[j], window_hours  # type: ignore[arg-type]
            ):
                continue
            if jaccard_similarity(shingles[i], shingles[j]) < threshold:
                continue
            root_i, root_j = find(i), find(j)
            if root_i != root_j:
                parent[root_j] = root_i

    components: dict[int, list[int]] = {}
    for index in range(len(items)):
        components.setdefault(find(index), []).append(index)
    groups = [sorted(group) for group in components.values() if len(group) >= 2]
    groups.sort(key=lambda group: group[0])
    return groups


# ---------------------------------------------------------------------------
# Level 2: LLM confirmation with cache/budget rails
# ---------------------------------------------------------------------------


def pair_cache_key(url_a: str, url_b: str) -> str:
    """Order-independent cache key for one URL pair (enrich_cache url 槽位)."""
    lo, hi = sorted((url_a, url_b))
    digest = hashlib.sha256(f"{lo}\x00{hi}".encode("utf-8")).hexdigest()[:32]
    return f"pair:{digest}"


def parse_dedupe_payload(
    text: str,
    requested: Mapping[int, int],
) -> tuple[dict[int, list[list[int]]], list[dict[str, str]]]:
    """Parse one batch's LLM output into per-group same-event clusters.

    Args:
        text: raw model message content.
        requested: group id -> group size; cluster indices are validated
            against ``range(size)`` (out-of-range/duplicate indices are
            dropped, item-level tolerance).

    Returns:
        ``(clusters, failures)`` — ``clusters`` maps a requested group id to
        its same-event index clusters (each length >= 2, sorted); group ids
        answered with no valid cluster map to ``[]`` (explicitly "all
        different"). ``failures`` carries structured records for unusable
        entries. A group absent from the response is simply not merged —
        保守方向永远是不合并.

    Raises:
        DedupeParseError: no parseable payload at all (batch-level failure).
    """
    try:
        entries = extract_json_entries(text)
    except ScoreParseError as exc:  # 共享抽取器的载荷级失败 → 判重错误族
        raise DedupeParseError(f"LLM 判重响应不可解析: {exc}") from exc
    if len(entries) == 1 and isinstance(entries[0], dict) and isinstance(
        entries[0].get("groups"), list
    ):
        entries = entries[0]["groups"]  # {"groups": [...]} 包装容错
    clusters: dict[int, list[list[int]]] = {}
    failures: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append(
                {
                    "url": "",
                    "error_type": "aggregate_entry_not_object",
                    "message": f"判重条目必须是对象,当前为 {type(entry).__name__}",
                }
            )
            continue
        group_id = entry.get("id")
        if isinstance(group_id, str) and group_id.strip().lstrip("-").isdigit():
            group_id = int(group_id.strip())  # 容错:模型把 id 写成字符串
        if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id not in requested:
            failures.append(
                {
                    "url": "",
                    "error_type": "aggregate_unknown_group",
                    "message": f"判重条目 id 缺失或不在本批请求列表中,已忽略: {entry.get('id')!r}",
                }
            )
            continue
        raw_clusters = entry.get("clusters")
        if not isinstance(raw_clusters, list):
            failures.append(
                {
                    "url": "",
                    "error_type": "aggregate_missing_clusters",
                    "message": f"组 {group_id} 响应缺少 clusters 数组,该组按不合并处理",
                }
            )
            continue
        size = requested[group_id]
        group_clusters: list[list[int]] = []
        for cluster in raw_clusters:
            if not isinstance(cluster, list):
                failures.append(
                    {
                        "url": "",
                        "error_type": "aggregate_cluster_not_list",
                        "message": f"组 {group_id} 的 cluster 应为下标数组,当前为 {type(cluster).__name__}",
                    }
                )
                continue
            indices: list[int] = []
            for index in cluster:
                if isinstance(index, bool) or not isinstance(index, int):
                    continue
                if not 0 <= index < size or index in indices:
                    continue
                indices.append(index)
            if len(indices) >= 2:
                group_clusters.append(sorted(indices))
        clusters[group_id] = group_clusters
    return clusters, failures


class EventAggregator:
    """Two-level same-event dedup on the shared enrich LLM rails.

    Built once per pipeline (fail fast): endpoint references are validated
    and resolved here, the dedupe prompt data file is loaded here, so a
    broken aggregate configuration is a startup failure, never a mid-run
    surprise — same contract as :class:`~shishi.enrich.LLMEnricher`. One
    deliberate exception to the startup timing: the optional ``openai``
    package is **not** checked at construction — it is imported lazily at
    the first completion (core stays importable without the extra), so a
    missing extra surfaces on first call, not here.

    Args:
        config: the schema ``aggregate:`` section (enabled/window_hours/
            similarity_threshold).
        enrich_config: the schema ``enrich:`` section — the aggregator reuses
            its model / batch / budget_per_run (端点配置同源).
        settings: endpoint settings (``base_url_ref`` / ``api_key_ref`` ...;
            also carries the dedupe prompt overrides).
        client: injected completion client (tests pass fakes; default builds
            an :class:`OpenAICompatClient`, which lazily imports ``openai``).
        clock: monotonic clock for duration logging (injectable, tests).

    Raises:
        EnrichConfigError: at construction — missing/plaintext endpoint
            references, unresolvable env vars, non-http(s) resolved base
            URL, or a malformed prompt file. A missing ``openai`` package
            (when no client is injected) is *not* a construction error: it
            surfaces at the first LLM call and, like any batch failure, is
            isolated into ``AggregateOutcome.failures`` (降级不中断).
    """

    def __init__(
        self,
        config: AggregateConfig,
        enrich_config: EnrichConfig,
        settings: EnrichSettings | None = None,
        *,
        client: Any | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.config = config
        self.enrich_config = enrich_config
        self.settings = settings or EnrichSettings()
        self._clock = clock
        self.base_url, self.api_key = resolve_endpoint(self.settings)
        self.prompt: PromptTemplate = load_dedupe_prompt(self.settings.dedupe_prompt_path)
        if client is not None:
            self._client = client
        else:
            self._client = OpenAICompatClient(
                self.base_url,
                self.api_key,
                timeout_seconds=self.settings.timeout_seconds,
            )
        logger.info(
            "事件聚合已构建 window_hours=%s similarity_threshold=%s batch=%s budget_per_run=%s "
            "model=%s dedupe_prompt_version=%s",
            config.window_hours,
            config.similarity_threshold,
            enrich_config.batch,
            enrich_config.budget_per_run,
            enrich_config.model,
            self.prompt_version,
        )

    @property
    def prompt_version(self) -> int:
        """Effective dedupe prompt fingerprint version (settings override wins)."""
        if self.settings.dedupe_prompt_version is not None:
            return self.settings.dedupe_prompt_version
        return self.prompt.version

    @property
    def dedupe_scores_key(self) -> str:
        """Cache fingerprint: dedupe namespace + prompt version (变更才重问)."""
        return f"{DEDUPE_CACHE_KEY_PREFIX}@p{self.prompt_version}"

    # ----------------------------------------------------------------- aggregate

    async def aggregate(
        self,
        items: Sequence[Item],
        *,
        store: Store,
        budget: BudgetTracker | None = None,
    ) -> AggregateOutcome:
        """Run the two-level dedup; returns merge decisions as index components.

        Level 1 always runs (zero token). Level 2 asks the LLM only for
        candidate groups whose pair verdicts are not already cached; the
        shared per-run budget (scoring + dedup 同一个池) gates every request.
        Any degradation leaves the affected groups **unmerged** — 保守降级.

        Args:
            items: pipeline items (duck-typed: ``url``/``title``/``metadata``/
                ``content``); already deduped and scored by earlier stages.
            store: the run's storage backend (pair-verdict cache reads/writes;
                dry-run passes its in-memory store).
            budget: optional shared per-run budget; ``None`` builds a private
                one from ``enrich.budget_per_run`` (standalone 调用形态).

        Returns:
            :class:`AggregateOutcome` — never raises for endpoint/parse
            problems (isolated per batch; structured on ``outcome.failures``).
        """
        outcome = AggregateOutcome(requested=len(items))
        if len(items) < 2:
            return outcome
        groups = coarse_groups(
            items,
            threshold=self.config.similarity_threshold,
            window_hours=self.config.window_hours,
        )
        outcome.coarse_groups = len(groups)
        outcome.candidates = sum(len(group) for group in groups)
        if not groups:
            logger.info("事件聚合粗筛无候选组 items=%s", len(items))
            return outcome
        tracker = budget or BudgetTracker(limit=self.enrich_config.budget_per_run)
        parent = list(range(len(items)))

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: int, b: int) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        group_urls = [[items[index].url for index in group] for group in groups]
        pending: list[tuple[int, list[int]]] = []
        for position, group in enumerate(groups):
            verdicts = self._load_pair_verdicts(store, group_urls[position])
            if verdicts is None:
                pending.append((position, group))
                continue
            outcome.cached_groups += 1
            for (a, b), same in verdicts.items():
                if same:
                    union(group[a], group[b])
            logger.debug(
                "事件聚合缓存命中组 items=%s(零 LLM token)", len(group)
            )

        for position, batch in enumerate(_chunks(pending, self.enrich_config.batch)):
            if not tracker.can_spend():
                outcome.degraded = True
                outcome.degrade_reason = "budget_exhausted"
                unmerged_items = sum(
                    len(group) for _, group in pending[position * self.enrich_config.batch:]
                )
                logger.warning(
                    "事件聚合预算耗尽,剩余候选组不合并(照常推送) "
                    "(budget_per_run=%s used=%s 本轮剩余候选条目=%s)",
                    self.enrich_config.budget_per_run,
                    tracker.used,
                    unmerged_items,
                )
                break
            started = self._clock()
            try:
                user_msg = self._render_batch(batch, items)
                completion = await asyncio.wait_for(
                    self._client.complete(
                        model=self.enrich_config.model,
                        system=self.prompt.system,
                        user=user_msg,
                    ),
                    timeout=self.settings.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单批隔离:端点/网络/解析错误不拖垮本轮
                outcome.degraded = True
                outcome.degrade_reason = "llm_batch_failed"
                for _, group in batch:
                    self._record_group_failure(
                        outcome, items, group, "aggregate_batch_failed",
                        f"判重批次失败: {type(exc).__name__}: {exc}",
                    )
                logger.warning(
                    "事件聚合批次失败(%s 组不合并,本轮降级) error_type=%s: %s",
                    len(batch), type(exc).__name__, exc,
                )
                continue
            outcome.tokens_used += completion.total_tokens
            tracker.spend(completion.total_tokens)
            outcome.llm_calls += 1
            self._apply_batch(batch, items, completion.text, store, outcome, union)
            logger.debug(
                "事件聚合批次完成 groups=%s tokens=%s duration=%.2fs",
                len(batch), completion.total_tokens, self._clock() - started,
            )

        components: dict[int, list[int]] = {}
        for index in range(len(items)):
            components.setdefault(find(index), []).append(index)
        merged = sorted(
            (sorted(group) for group in components.values() if len(group) >= 2),
            key=lambda group: group[0],
        )
        outcome.components = merged
        outcome.merged_groups = len(merged)
        outcome.absorbed_items = sum(len(group) - 1 for group in merged)
        logger.info(
            "事件聚合完成 requested=%s coarse_groups=%s llm_calls=%s cached=%s merged=%s "
            "absorbed=%s tokens=%s degraded=%s(%s)",
            outcome.requested, outcome.coarse_groups, outcome.llm_calls,
            outcome.cached_groups, outcome.merged_groups, outcome.absorbed_items,
            outcome.tokens_used, outcome.degraded, outcome.degrade_reason,
        )
        return outcome

    # ------------------------------------------------------------------ helpers

    def _load_pair_verdicts(
        self, store: Store, urls: list[str]
    ) -> dict[tuple[int, int], bool] | None:
        """Cached verdict for every pair of the group, or None on any gap.

        一对未命中即整组走 LLM(部分缓存的组仍需整组判断);读取/行损坏按未
        命中处理(旁路优化,失败只告警).
        """
        verdicts: dict[tuple[int, int], bool] = {}
        for a in range(len(urls)):
            for b in range(a + 1, len(urls)):
                try:
                    cached = store.get_enrich_cache(
                        pair_cache_key(urls[a], urls[b]),
                        self.enrich_config.model,
                        self.dedupe_scores_key,
                    )
                except Exception as exc:  # noqa: BLE001 - 缓存读失败按未命中处理
                    logger.warning("判重缓存读取失败(按未命中处理): %s", exc)
                    return None
                if not isinstance(cached, dict) or not isinstance(cached.get("same"), bool):
                    return None
                verdicts[(a, b)] = cached["same"]
        return verdicts

    def _render_batch(
        self, batch: Sequence[tuple[int, list[int]]], items: Sequence[Item]
    ) -> str:
        """Render the user message for one batch (candidate groups JSON)."""
        payload = []
        for position, group in batch:
            entries = []
            for order, index in enumerate(group):
                item = items[index]
                entry: dict[str, Any] = {
                    "i": order,
                    "title": item.title,
                    "source": item.source,
                }
                content = getattr(item, "content", None) or item.metadata.get("content")
                if isinstance(content, str) and content.strip():
                    entry["content"] = content[:GROUP_ITEM_CONTENT_CHARS]
                entries.append(entry)
            payload.append({"id": position, "items": entries})
        return self.prompt.render_groups(
            window_hours=self.config.window_hours,
            groups_json=json.dumps(payload, ensure_ascii=False),
        )

    def _apply_batch(
        self,
        batch: Sequence[tuple[int, list[int]]],
        items: Sequence[Item],
        text: str,
        store: Store,
        outcome: AggregateOutcome,
        union: Any,
    ) -> None:
        """Parse one completion: union same-event pairs + cache all pair verdicts."""
        requested = {position: len(group) for position, group in batch}
        try:
            clusters, failures = parse_dedupe_payload(text, requested)
        except DedupeParseError as exc:
            outcome.degraded = True
            outcome.degrade_reason = "llm_batch_failed"
            for _, group in batch:
                self._record_group_failure(
                    outcome, items, group, "aggregate_parse_error", str(exc)
                )
            logger.warning("事件聚合响应不可解析(%s 组不合并): %s", len(batch), exc)
            return
        # 条目级容错记录(未知组 id/坏 cluster 等):结构化上报,不影响其余组。
        outcome.failures.extend(dict(failure) for failure in failures)
        for position, group in batch:
            group_clusters = clusters.get(position)
            if group_clusters is None:
                # 模型漏答该组:保守不合并,不写缓存(下次换个组组合再问)
                logger.debug("事件聚合组未获响应(按不合并处理) position=%s", position)
                continue
            # 组内局部 union-find:cluster 划分 → 每对的 same-event 判定
            local = _components_of(len(group), group_clusters)
            verdicts: dict[tuple[int, int], bool] = {}
            for a in range(len(group)):
                for b in range(a + 1, len(group)):
                    same = local[a] == local[b]
                    verdicts[(a, b)] = same
                    if same:
                        union(group[a], group[b])
            self._cache_group_verdicts(store, items, group, verdicts)

    def _cache_group_verdicts(
        self,
        store: Store,
        items: Sequence[Item],
        group: list[int],
        verdicts: Mapping[tuple[int, int], bool],
    ) -> None:
        """Persist one group's pair verdicts (best-effort; 失败只告警)."""
        for (a, b), same in verdicts.items():
            try:
                store.set_enrich_cache(
                    pair_cache_key(items[group[a]].url, items[group[b]].url),
                    self.enrich_config.model,
                    self.dedupe_scores_key,
                    {"same": same},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "判重缓存写入失败 url_a=%s url_b=%s: %s",
                    items[group[a]].url, items[group[b]].url, exc,
                )

    def _record_group_failure(
        self,
        outcome: AggregateOutcome,
        items: Sequence[Item],
        group: list[int],
        error_type: str,
        message: str,
    ) -> None:
        """Record one structured failure per item of an affected group."""
        for index in group:
            item = items[index]
            outcome.failures.append(
                {
                    "url": item.url,
                    "title": item.title,
                    "error_type": error_type,
                    "message": message,
                }
            )


# --------------------------------------------------------------------- helpers


def _components_of(size: int, clusters: Sequence[Sequence[int]]) -> list[int]:
    """Union-find over ``range(size)`` applying same-event clusters.

    Returns a fully resolved root id per index (equal roots ⇔ same cluster),
    so pair verdicts are plain root equality.
    """
    parent = list(range(size))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for cluster in clusters:
        if len(cluster) < 2:
            continue
        anchor = find(cluster[0])
        for index in cluster[1:]:
            root = find(index)
            if root != anchor:
                parent[root] = anchor
    return [find(node) for node in range(size)]


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    """Split into consecutive chunks of ``size`` (size >= 1)."""
    if size < 1:
        raise ValueError(f"字段校验失败: batch 必须 >= 1,得到 {size}")
    return [items[i : i + size] for i in range(0, len(items), size)]
