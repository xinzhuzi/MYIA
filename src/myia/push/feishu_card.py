"""Feishu interactive-card channel.

`target` uses `env:FEISHU_CHAT_ID` style references. Cards carry the
valuable / not-valuable feedback buttons (v1.7 feedback loop) that write
back to the SQLite feedback table to retune enrich prompts and thresholds.
"""

from __future__ import annotations


class FeishuCardChannel:
    async def send(self, items) -> None:
        raise NotImplementedError("feishu_card send is not implemented in the v0.1 skeleton yet")
