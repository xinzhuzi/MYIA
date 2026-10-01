"""Generic webhook channel: POST the items as JSON to any endpoint."""

from __future__ import annotations


class WebhookChannel:
    async def send(self, items) -> None:
        raise NotImplementedError("webhook send is not implemented in the v0.1 skeleton yet")
