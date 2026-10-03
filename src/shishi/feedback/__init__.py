"""Feedback loop (v0.3, PRD 10-01-v03-feedback-loop): 回写 → 统计 → 调参.

The cheapest AI-native closed loop: pushed cards carry 有价值/没价值 feedback;
verdicts land in the ``feedback`` table; a periodic tuning task (riding the
pipeline's maintenance phase) turns 负反馈 Top 类目/词 into demotion
adjustments (``feedback_tuning`` history, watchlist.mute weights, enrich
prompt notes).

Submodules:

- :mod:`shishi.feedback.record` — verdict normalization + ingestion from the
  three receiving paths (CLI mark / TG ``getUpdates`` callback / 飞书卡片回调);
- :mod:`shishi.feedback.stats` — pure statistics over feedback rows (Top 负反馈
  类目/词, simple counting only — 不引入学习库);
- :mod:`shishi.feedback.tuning` — the periodic tuner (thresholds → append-only
  adjustments) and the active-tuning view the pipeline applies as demotion.

Receiving forms (grill Q7 定案): 桌面 = TG ``getUpdates`` polling + CLI
marking; 服务端/compose = 飞书卡片回调端点 (default off; token 鉴权 + 仅内网,
see :mod:`shishi.push.feishu_callback`).
"""

from shishi.feedback.record import (
    IngestReport,
    ingest_callbacks,
    normalize_verdict,
    record_feedback,
    resolve_item_ref,
)
from shishi.feedback.stats import (
    DEFAULT_TOP_N,
    FeedbackStats,
    candidate_words,
    compute_feedback_stats,
)
from shishi.feedback.tuning import (
    DEFAULT_MIN_BAD_COUNT,
    DEFAULT_MIN_BAD_RATIO,
    DEFAULT_WINDOW_DAYS,
    ActiveTuning,
    FeedbackTuner,
    TuningPolicy,
    load_active_tuning,
)

__all__ = [
    "DEFAULT_MIN_BAD_COUNT",
    "DEFAULT_MIN_BAD_RATIO",
    "DEFAULT_TOP_N",
    "DEFAULT_WINDOW_DAYS",
    "ActiveTuning",
    "FeedbackStats",
    "FeedbackTuner",
    "IngestReport",
    "TuningPolicy",
    "candidate_words",
    "compute_feedback_stats",
    "ingest_callbacks",
    "load_active_tuning",
    "normalize_verdict",
    "record_feedback",
    "resolve_item_ref",
]
