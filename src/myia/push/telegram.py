"""Telegram bot channel (HTML or MarkdownV2 messages)."""

from __future__ import annotations


class TelegramChannel:
    async def send(self, items) -> None:
        raise NotImplementedError("telegram send is not implemented in the v0.1 skeleton yet")
