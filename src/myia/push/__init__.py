"""Push layer: channel registry + threshold routing (immediate/digest/archive).

Threshold routing semantics (v1.7, from production):
- score >= 8: immediate — push right away
- score >= 5: digest    — batched into the daily digest (AM/PM slots)
- score < 5:  archive   — stored only, never pushed
"""

from __future__ import annotations

from .feishu_card import FeishuCardChannel
from .telegram import TelegramChannel
from .webhook import WebhookChannel

CHANNELS = {
    "feishu_card": FeishuCardChannel,
    "telegram": TelegramChannel,
    "webhook": WebhookChannel,
}


def route(items, rules):
    """Bucket items into immediate/digest/archive per the `push.route` rules."""
    raise NotImplementedError("push.route is not implemented in the v0.1 skeleton yet")
