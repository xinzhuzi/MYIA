"""Score parsing / tolerance, composite scalar, mute matching, token budget.

Pure functions — no I/O, no LLM — so the fragile part of the second funnel
(model output is *never* trusted JSON) is unit-testable in isolation.

Tolerance policy (容错, PRD 验收项):

- JSON payload may arrive fenced (```json ... ```) or wrapped
  (``{"items": [...]}`` / ``{"results": [...]}``); the extractor slices the
  outermost array/object and parses that.
- Per-entry problems are *item-level*: an entry missing a requested
  dimension, carrying a non-numeric score, or referencing an unknown URL is
  recorded as a structured failure and that item stays unscored — the rest
  of the batch still lands (批次内单条失败不中断整批).
- Out-of-range numerics are clamped into 0-10 (a model saying ``11`` means
  ``10``, not a rejected item).
- A wholly unparseable payload raises :class:`ScoreParseError` — the caller
  treats the *batch* as failed and moves on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

__all__ = [
    "MIN_MUTE_LENGTH",
    "SCORE_MAX",
    "SCORE_MIN",
    "BudgetTracker",
    "ScoreParseError",
    "clamp_score",
    "composite_score",
    "extract_json_entries",
    "mute_hit",
    "parse_score_payload",
]

logger = logging.getLogger(__name__)

SCORE_MIN = 0
SCORE_MAX = 10
#: mute matching below this length would substring-match inside unrelated
#: words (mute 词 ``ai`` 命中 ``chair``); shorter mute entries are ignored by
#: :func:`mute_hit` and reported by the enricher.
MIN_MUTE_LENGTH = 2


class ScoreParseError(ValueError):
    """The LLM response carried no usable score payload at all (batch-level)."""


def clamp_score(value: float) -> int:
    """Clamp a model-suggested score into the 0-10 integer scale."""
    return max(SCORE_MIN, min(SCORE_MAX, round(float(value))))


def composite_score(scores: dict[str, float], dimensions: list[str]) -> float:
    """Scalar ``score`` from the configured dimensions: plain mean, 1 decimal.

    The route layer's threshold rules (``when: "score >= 8"``) read this one
    number; a transparent mean keeps 「推不推」 explainable from the three
    dimensions (no hidden weighting — weighting is a feedback-loop (v0.3)
    decision, not a silent default).
    """
    if not dimensions:
        raise ValueError("字段校验失败: composite_score 需要至少一个评分维度")
    missing = [name for name in dimensions if name not in scores]
    if missing:
        raise ValueError(f"字段校验失败: 评分缺少维度 {missing},无法合成标量 score")
    total = sum(float(scores[name]) for name in dimensions)
    return round(total / len(dimensions), 1)


def mute_hit(title: str, mute_words: list[str]) -> str | None:
    """Return the first mute word contained in the title (case-insensitive).

    Substring semantics on purpose: mute entries are phrases like ``带货``
    or ``vpn 教程``. Words shorter than :data:`MIN_MUTE_LENGTH` are skipped
    (they would match inside unrelated words); whitespace-only titles never
    match.
    """
    if not title or not title.strip():
        return None
    folded = title.casefold()
    for word in mute_words:
        if len(word) < MIN_MUTE_LENGTH:
            continue
        if word.casefold() in folded:
            return word
    return None


@dataclass
class BudgetTracker:
    """Per-run token budget (单轮预算护栏, PRD budget_per_run).

    ``can_spend`` is checked *before* firing a request; ``spend`` records the
    observed usage afterwards, so one in-flight request may overshoot the cap
    (checked-inside semantics) but the run never starts another batch once
    the budget is gone.
    """

    limit: int
    used: int = 0
    overshoot: int = field(default=0)  # usage beyond the limit (observability)

    @property
    def exhausted(self) -> bool:
        """Whether the budget is spent (no further batch may start)."""
        return self.used >= self.limit

    def can_spend(self) -> bool:
        """Whether another batch may start (False → 降级纯粗筛)."""
        return not self.exhausted

    def spend(self, tokens: int | None) -> None:
        """Record observed usage; negative/unknown (None) usage is ignored."""
        if tokens is None or tokens <= 0:
            return
        self.used += int(tokens)
        if self.used > self.limit:
            self.overshoot = self.used - self.limit
            logger.warning(
                "enrich 单批用量越过预算上限 limit=%s used=%s(单请求内不中断,后续批次停止)",
                self.limit, self.used,
            )


def parse_score_payload(
    text: str,
    requested_urls: list[str],
    dimensions: list[str],
) -> tuple[dict[str, dict[str, int]], dict[str, str], list[dict[str, str]]]:
    """Parse one batch's LLM output into per-URL dimension scores.

    Args:
        text: raw model message content.
        requested_urls: URLs sent in this batch (response entries are matched
            against this set; unknown URLs are dropped as failures).
        dimensions: configured score dimensions (subset of value / relevance
            / credibility, in schema order).

    Returns:
        ``(scores, reasons, failures)`` — ``scores`` maps URL → dimension
        dict (all requested dimensions present, clamped 0-10); ``reasons``
        maps URL → the model's one-line rationale (absent when not given);
        ``failures`` is a list of structured records
        ``{"url", "error_type", "message"}`` for entries that could not be
        used (item-level tolerance, never raises for those).

    Raises:
        ScoreParseError: no JSON payload could be extracted at all, or the
            payload is not a list/object-of-list (batch-level failure).
    """
    entries = extract_json_entries(text)
    wanted = set(requested_urls)
    scores: dict[str, dict[str, int]] = {}
    reasons: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append(
                {
                    "url": "",
                    "error_type": "enrich_entry_not_object",
                    "message": f"评分条目必须是对象,当前为 {type(entry).__name__}",
                }
            )
            continue
        url = entry.get("url")
        if not isinstance(url, str) or url not in wanted:
            failures.append(
                {
                    "url": url if isinstance(url, str) else "",
                    "error_type": "enrich_unknown_url",
                    "message": "评分条目 url 缺失或不在本批请求列表中,已忽略",
                }
            )
            continue
        dims: dict[str, int] = {}
        problem: str | None = None
        for name in dimensions:
            value = entry.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problem = f"维度 {name} 缺失或不是数字,当前为 {value!r}(该条目不评分)"
                break
            dims[name] = clamp_score(value)
        if problem is not None:
            failures.append(
                {"url": url, "error_type": "enrich_invalid_dimension", "message": problem}
            )
            continue
        scores[url] = dims
        reason = entry.get("reason")
        if isinstance(reason, str) and reason.strip():
            reasons[url] = reason.strip()
    return scores, reasons, failures


def extract_json_entries(text: str) -> list:
    """Pull the JSON array of per-item entries out of a raw model message.

    Tolerates markdown code fences, leading prose, and single-object payloads
    (turned into a one-element list). ``{"items": [...]}`` /
    ``{"results": [...]}`` wrappers are unwrapped. Shared by the scoring
    parser (:func:`parse_score_payload`) and the v0.4 event-dedup parser
    (:func:`shishi.enrich.aggregate.parse_dedupe_payload`) — one tolerance
    contract for every LLM payload.

    Raises:
        ScoreParseError: no parseable payload found.
    """
    if not isinstance(text, str) or not text.strip():
        raise ScoreParseError("LLM 评分为空,无法解析")
    candidate = text.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1:]
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3]
    start = min(
        (i for i in (candidate.find("["), candidate.find("{")) if i != -1),
        default=-1,
    )
    if start == -1:
        raise ScoreParseError(f"LLM 评分中找不到 JSON 载荷: {text[:120]!r}")
    open_char = candidate[start]
    close_char = "]" if open_char == "[" else "}"
    end = candidate.rfind(close_char)
    if end <= start:
        raise ScoreParseError(f"LLM 评分的 JSON 载荷未闭合: {text[:120]!r}")
    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ScoreParseError(f"LLM 评分 JSON 解析失败: {exc}") from exc
    if isinstance(payload, dict):
        for wrapper in ("items", "results", "scores"):
            inner = payload.get(wrapper)
            if isinstance(inner, list):
                return inner
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ScoreParseError(
        f"LLM 评分载荷应为数组或对象,当前为 {type(payload).__name__}"
    )
