"""LLM enrichment: precision scoring after the keyword pre-filter.

Second funnel (v1.7 分析侧, PRD 10-01-v02-enrich-llm): items that survive the
zero-token keyword classifier are scored 0-10 on the configured dimensions
(``value`` / ``relevance`` / ``credibility``; relevance is baselined against
``watchlist.keywords``) by any OpenAI-compatible endpoint, and a scalar
composite ``score`` is backfilled onto each item — that scalar is what wakes
``push.route`` score-threshold rules (``when: "score >= 8"``); items without
a score keep the v0.1 category-default routing.

Cost guardrails (桌面用户挂自己的 key,没有护栏会烧穿):

- **batch** — items are scored in batches (default 20/request);
- **cache** — same-URL results are cached in the store's ``enrich_cache``
  table, keyed by URL + model + scores/prompt fingerprint; a cache hit never
  re-scores (schema/prompt 变更或手动失效才重评);
- **budget** — a per-run token budget (``budget_per_run``); once spent, the
  run *degrades* to pure keyword filtering (unscored items flow on with no
  score, WARNING logged, degrade visible in the stage report) instead of
  failing — a cost guardrail must never kill the run it protects;
- **mute** — titles hitting a ``watchlist.mute`` word are demoted locally
  (all dimensions 0, ``muted`` tag) without spending a single token, so
  ``score < 5 → archive`` style routes catch them.

Configuration: ``schema.EnrichConfig`` carries enabled/model/scores/batch/
cache/budget_per_run; the endpoint itself arrives via :class:`EnrichSettings`
— explicit ``env:``/``keychain:`` references only, validated and resolved at
construction (grill Q6: MYIA 无内置端点、无默认 key;缺失/明文 = 结构化
:class:`EnrichConfigError`,启动即拒).

Event aggregation (v0.4, PRD 10-01-v04-event-aggregation): the same package
also hosts the multi-source same-event dedup (:mod:`shishi.enrich.aggregate`)
— a local title-similarity coarse screen plus LLM confirmation that reuses
the batch/cache/budget rails described above (and one shared per-run token
budget with scoring when the pipeline runs both stages).

Failure model: endpoint timeouts / unparseable responses are isolated per
batch (structured failures on :class:`EnrichOutcome` / :class:`AggregateOutcome`,
affected items simply stay unscored / unmerged) — 降级优于中断, the push
stage still runs with v0.1 routing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shishi.enrich.aggregate import (
    AggregateOutcome,
    EventAggregator,
    coarse_groups,
    pair_cache_key,
    parse_dedupe_payload,
    title_similarity,
)
from shishi.enrich.client import INSTALL_COMMAND, OpenAICompatClient
from shishi.enrich.errors import EnrichConfigError
from shishi.enrich.prompt import PromptTemplate, load_prompt
from shishi.enrich.scoring import (
    BudgetTracker,
    ScoreParseError,
    composite_score,
    mute_hit,
    parse_score_payload,
)
from shishi.enrich.settings import EnrichSettings, resolve_endpoint
from shishi.schema import EnrichConfig
from shishi.store import Store

if TYPE_CHECKING:  # 循环避免:仅类型注解引用 pipeline.Item(运行时鸭子类型)
    from shishi.pipeline import Item

__all__ = [
    "CONTENT_SNIPPET_CHARS",
    "IMAGE_CAPTION_SNIPPET_CHARS",
    "IMAGE_OCR_SNIPPET_CHARS",
    "INSTALL_COMMAND",
    "AggregateOutcome",
    "EnrichConfigError",
    "EnrichOutcome",
    "EnrichSettings",
    "EventAggregator",
    "LLMEnricher",
    "coarse_groups",
    "pair_cache_key",
    "parse_dedupe_payload",
    "title_similarity",
]

logger = logging.getLogger(__name__)

#: Per-item content snippet cap inside the prompt (token 成本护栏的一部分;
#: 精评只需要摘要,不需要全文).
CONTENT_SNIPPET_CHARS = 600
#: 图析 OCR 文本截断(10-03-vision-pipeline 拍板⑤:image_ocr 进 payload 的
#: 上限——OCR 全文动辄数屏,精评只要要点)。
IMAGE_OCR_SNIPPET_CHARS = 800
#: 图析视觉描述截断(同上;VL caption 比 OCR 短,上限更紧)。
IMAGE_CAPTION_SNIPPET_CHARS = 300


@dataclass
class EnrichOutcome:
    """One ``enrich`` call's observable result (stage report / doctor 消费).

    Attributes:
        model: scoring model used.
        requested: items handed to the enricher.
        scored: items carrying scores after this call (LLM 本轮 + 缓存命中,
            ``cached`` 是其子集).
        cached: items served from the enrich_cache table.
        muted: items demoted locally by a watchlist.mute hit (zero token).
        unscored: items left without a score (budget exhausted / batch failed).
        tokens_used: observed token usage spent this call.
        degraded: True when items were left unscored (降级纯粗筛发生了).
        degrade_reason: ``budget_exhausted`` | ``llm_batch_failed`` | None.
        failures: structured per-item failure records (url/title/error_type/
            message) — same shape as pipeline stage failures.
    """

    model: str
    requested: int = 0
    scored: int = 0
    cached: int = 0
    muted: int = 0
    unscored: int = 0
    tokens_used: int = 0
    degraded: bool = False
    degrade_reason: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for run stats / ``myia doctor`` (JSON)."""
        return {
            "model": self.model,
            "requested": self.requested,
            "scored": self.scored,
            "cached": self.cached,
            "muted": self.muted,
            "unscored": self.unscored,
            "tokens_used": self.tokens_used,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "failures": list(self.failures),
        }


class LLMEnricher:
    """Scores items with an OpenAI-compatible endpoint (batch/cache/budget).

    Built once per pipeline (fail fast): endpoint references are validated
    and resolved here, the prompt data file is loaded here, so a broken
    enrich configuration is a startup failure, never a mid-run surprise.
    One deliberate exception to the startup timing: the optional ``openai``
    package is **not** checked at construction — it is imported lazily at
    the first completion (core stays importable without the extra), so a
    missing extra surfaces on first call, not here.

    Args:
        config: the schema ``enrich:`` section (enabled/model/scores/batch/
            cache/budget_per_run).
        settings: endpoint settings (``base_url_ref`` / ``api_key_ref`` ...).
        client: injected completion client (tests pass fakes; default builds
            an :class:`OpenAICompatClient`, which lazily imports ``openai``).
        clock: monotonic clock for duration logging (injectable, tests).

    Raises:
        EnrichConfigError: at construction — missing/plaintext endpoint
            references, unresolvable env vars, non-http(s) resolved base
            URL, or a malformed prompt file. A missing ``openai`` package
            (when no client is injected) is *not* a construction error: it
            surfaces at the first LLM call and, like any batch failure, is
            isolated into ``EnrichOutcome.failures`` (降级不中断).
    """

    def __init__(
        self,
        config: EnrichConfig,
        settings: EnrichSettings | None = None,
        *,
        client: Any | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.config = config
        self.settings = settings or EnrichSettings()
        self._clock = clock
        self.base_url, self.api_key = resolve_endpoint(self.settings)
        self.prompt: PromptTemplate = load_prompt(self.settings.prompt_path)
        if client is not None:
            self._client = client
        else:
            self._client = OpenAICompatClient(
                self.base_url,
                self.api_key,
                timeout_seconds=self.settings.timeout_seconds,
            )
        logger.info(
            "enrich 已构建 model=%s scores=%s batch=%s cache=%s budget_per_run=%s "
            "prompt_version=%s",
            config.model,
            config.scores,
            config.batch,
            config.cache,
            config.budget_per_run,
            self.prompt_version,
        )

    @property
    def prompt_version(self) -> int:
        """Effective prompt fingerprint version (settings override wins)."""
        if self.settings.prompt_version is not None:
            return self.settings.prompt_version
        return self.prompt.version

    @property
    def scores_key(self) -> str:
        """Cache fingerprint: dimensions + prompt version (变更才重评)."""
        joined = "+".join(self.config.scores)
        return f"{joined}@p{self.prompt_version}"

    # ------------------------------------------------------------------- enrich

    async def enrich(
        self,
        items: Sequence[Item],
        *,
        watchlist: Any,
        store: Store,
        budget: BudgetTracker | None = None,
    ) -> EnrichOutcome:
        """Score a run's items; backfill ``Item.scores`` + scalar ``metadata['score']``.

        Per item the outcome is one of: **muted** (mute word hit → all-zero
        dims, ``muted`` tag, zero token), **cached** (scored result from the
        enrich_cache table), **scored** (LLM this call; result written to the
        cache and the ``items`` table row), or **unscored** (budget spent /
        batch failed — flows on with no score, v0.1 routing applies).

        Args:
            items: pipeline items (duck-typed: ``url``/``title``/``metadata``/
                ``scores``/``add_tags``); already deduped (``dedup_key`` set).
            watchlist: the category's watchlist section (``keywords`` boost,
                ``mute`` demote); mapping or attributes both accepted.
            store: the run's storage backend (cache reads/writes + items-table
                score backfill); dry-run passes its in-memory store.
            budget: optional shared per-run token budget (v0.4 事件聚合与精评
                共用同一个 ``budget_per_run`` 池, pipeline 注入; ``None`` 自建).

        Returns:
            :class:`EnrichOutcome` — never raises for endpoint/parse problems
            (isolated per batch; structured on ``outcome.failures``).
        """
        outcome = EnrichOutcome(model=self.config.model, requested=len(items))
        if not items:
            return outcome
        keywords, mute_words = _watchlist_lists(watchlist)

        pending: list[Any] = []
        for item in items:
            word = mute_hit(item.title, mute_words)
            if word is not None:
                self._apply_muted(item, word)
                outcome.muted += 1
                logger.info("mute 降权(不消耗 token) url=%s word=%r", item.url, word)
                continue
            pending.append(item)

        budget = budget or BudgetTracker(limit=self.config.budget_per_run)
        cache_hits: dict[str, dict[str, Any]] = {}
        if self.config.cache:
            cache_hits = self._load_cache(pending, store)
        to_score = [item for item in pending if item.url not in cache_hits]
        outcome.unscored = len(to_score)

        for batch in _chunks(list(to_score), self.config.batch):
            if not budget.can_spend():
                outcome.degraded = True
                outcome.degrade_reason = "budget_exhausted"
                logger.warning(
                    "enrich 预算耗尽,本轮剩余 %s 条自动降级纯粗筛 "
                    "(budget_per_run=%s used=%s)",
                    outcome.unscored, self.config.budget_per_run, budget.used,
                )
                break
            started = self._clock()
            try:
                user_msg = self._render_batch(batch, keywords)
                completion = await asyncio.wait_for(
                    self._client.complete(
                        model=self.config.model,
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
                for item in batch:
                    outcome.failures.append(
                        {
                            "url": item.url,
                            "title": item.title,
                            "error_type": "enrich_batch_failed",
                            "message": f"批量精评失败: {type(exc).__name__}: {exc}",
                        }
                    )
                logger.warning(
                    "enrich 批次失败(%s 条不评分,本轮降级) error_type=%s: %s",
                    len(batch), type(exc).__name__, exc,
                )
                continue
            outcome.tokens_used += completion.total_tokens
            budget.spend(completion.total_tokens)
            self._apply_batch(batch, completion.text, store, outcome)
            logger.debug(
                "enrich 批次完成 items=%s tokens=%s duration=%.2fs",
                len(batch), completion.total_tokens, self._clock() - started,
            )

        outcome.scored = outcome.requested - outcome.muted - outcome.unscored
        outcome.cached = len(cache_hits)
        logger.info(
            "精评完成 requested=%s scored=%s(含缓存 %s) muted=%s unscored=%s "
            "tokens=%s degraded=%s(%s)",
            outcome.requested, outcome.scored, outcome.cached, outcome.muted,
            outcome.unscored, outcome.tokens_used, outcome.degraded,
            outcome.degrade_reason,
        )
        return outcome

    # ------------------------------------------------------------------ helpers

    def _load_cache(
        self, pending: list[Item], store: Store
    ) -> dict[str, dict[str, Any]]:
        """Apply cached scores to ``pending``; return url → cached payload.

        缓存命中同样回填 ``items`` 表(PRD「score 回填 items 表」):dated
        dedup key 轮换/断点续跑会产生新插入的行,只回内存会让该行 scores 恒
        NULL。走 :meth:`_persist_scores`(items 行按 dedup_key 更新;cache 重写
        为幂等 upsert,失败仅告警)。
        """
        hits: dict[str, dict[str, Any]] = {}
        for item in pending:
            try:
                cached = store.get_enrich_cache(item.url, self.config.model, self.scores_key)
            except Exception as exc:  # noqa: BLE001 - 缓存读失败按未命中处理
                logger.warning("enrich 缓存读取失败(按未命中处理) url=%s: %s", item.url, exc)
                continue
            if not isinstance(cached, dict):
                continue
            scores = cached.get("scores")
            scalar = cached.get("score")
            if not isinstance(scores, dict) or not isinstance(scalar, (int, float)):
                logger.warning("enrich 缓存行不完整(按未命中处理) url=%s", item.url)
                continue
            self._apply_scores(item, dict(scores), float(scalar), cached.get("reason"))
            self._persist_scores(store, item, dict(scores), float(scalar))
            hits[item.url] = cached
        return hits

    def _render_batch(self, batch: list[Item], keywords: list[str]) -> str:
        """Render the user message for one batch (items JSON + watchlist)."""
        payload = []
        for item in batch:
            entry: dict[str, Any] = {"url": item.url, "title": item.title}
            # content 在 Item.content(真实管线,from_extracted 接线);
            # metadata["content"] 兜底(测试 FakeItem / 旧注入路径)。
            content = getattr(item, "content", None) or item.metadata.get("content")
            if isinstance(content, str) and content.strip():
                entry["content"] = content[:CONTENT_SNIPPET_CHARS]
            # 图析产物(10-03-vision-pipeline 拍板⑤):有图条目携带配图 OCR
            # 文本与视觉描述,评分与正文同权参考;无图条目不带键,payload
            # 不膨胀(空串/非字符串同样不带)。
            image_ocr = item.metadata.get("image_ocr")
            if isinstance(image_ocr, str) and image_ocr.strip():
                entry["image_ocr"] = image_ocr[:IMAGE_OCR_SNIPPET_CHARS]
            image_caption = item.metadata.get("image_caption")
            if isinstance(image_caption, str) and image_caption.strip():
                entry["image_caption"] = image_caption[:IMAGE_CAPTION_SNIPPET_CHARS]
            payload.append(entry)
        return self.prompt.render_user(
            scores=list(self.config.scores),
            watchlist=keywords,
            items_json=json.dumps(payload, ensure_ascii=False),
        )

    def _apply_batch(
        self,
        batch: list[Item],
        text: str,
        store: Store,
        outcome: EnrichOutcome,
    ) -> None:
        """Parse one completion, backfill items + cache + items 表."""
        urls = [item.url for item in batch]
        try:
            scores, reasons, failures = parse_score_payload(
                text, urls, list(self.config.scores)
            )
        except ScoreParseError as exc:
            outcome.degraded = True
            outcome.degrade_reason = "llm_batch_failed"
            for item in batch:
                outcome.failures.append(
                    {
                        "url": item.url,
                        "title": item.title,
                        "error_type": "enrich_parse_error",
                        "message": str(exc),
                    }
                )
            logger.warning("enrich 批次响应不可解析(%s 条不评分): %s", len(batch), exc)
            return
        outcome.failures.extend(
            {
                "url": failure.get("url", ""),
                "title": next((i.title for i in batch if i.url == failure.get("url")), ""),
                "error_type": failure["error_type"],
                "message": failure["message"],
            }
            for failure in failures
        )
        for item in batch:
            dims = scores.get(item.url)
            if dims is None:  # 模型漏评该条:条目级容错,保持未评分
                continue
            scalar = composite_score(dims, list(self.config.scores))
            self._apply_scores(item, dims, scalar, reasons.get(item.url))
            outcome.unscored -= 1
            self._persist_scores(store, item, dims, scalar)

    def _apply_scores(
        self,
        item: Item,
        dims: dict[str, int],
        scalar: float,
        reason: Any = None,
    ) -> None:
        """Backfill one item: dimension dict + scalar ``score`` (route 依赖位)."""
        item.scores = dict(dims)
        item.metadata["score"] = scalar
        item.metadata["enrich_model"] = self.config.model
        if isinstance(reason, str) and reason.strip():
            item.metadata["score_reason"] = reason.strip()

    def _apply_muted(self, item: Item, word: str) -> None:
        """mute 命中:三维直接 0 分(降权/归档由 score 路由规则承接),零 token."""
        dims = {name: 0 for name in self.config.scores}
        self._apply_scores(item, dims, 0.0, reason=f"命中静默词: {word}")
        item.metadata["muted"] = word
        item.add_tags(["muted"])

    def _persist_scores(
        self, store: Store, item: Item, dims: dict[str, int], scalar: float
    ) -> None:
        """Cache the result + backfill the ``items`` 表 row (best-effort).

        缓存/回填是旁路优化,失败只告警:条目内存态已带分,本轮路由不受影响.
        两种持久化形态:items 表扁平(维度字段平铺 + ``score`` 标量,便于
        直接查询);enrich_cache 嵌套(``scores`` 子对象 + 标量 + reason,
        与 :meth:`_load_cache` 的读取契约一致).
        """
        if self.config.cache:
            cache_payload: dict[str, Any] = {"scores": dict(dims), "score": scalar}
            reason = item.metadata.get("score_reason")
            if isinstance(reason, str) and reason.strip():
                cache_payload["reason"] = reason
            try:
                store.set_enrich_cache(
                    item.url, self.config.model, self.scores_key, cache_payload
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("enrich 缓存写入失败 url=%s: %s", item.url, exc)
        if item.dedup_key:
            payload: dict[str, Any] = {**dims, "score": scalar}
            try:
                updated = store.update_item_scores(item.dedup_key, payload)
                if not updated:
                    logger.debug("评分回填未找到 items 行 dedup_key=%s", item.dedup_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("评分回填 items 表失败 dedup_key=%s: %s", item.dedup_key, exc)


# --------------------------------------------------------------------- helpers


def _watchlist_lists(watchlist: Any) -> tuple[list[str], list[str]]:
    """Normalize the watchlist section into (keywords, mute) string lists."""
    def _get(name: str) -> list[str]:
        if isinstance(watchlist, Mapping):
            value = watchlist.get(name)
        else:
            value = getattr(watchlist, name, None)
        if not isinstance(value, (list, tuple)):
            return []
        return [word for word in value if isinstance(word, str) and word]

    return _get("keywords"), _get("mute")


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    """Split into consecutive chunks of ``size`` (size >= 1)."""
    if size < 1:
        raise ValueError(f"字段校验失败: batch 必须 >= 1,得到 {size}")
    return [items[i : i + size] for i in range(0, len(items), size)]
