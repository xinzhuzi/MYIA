"""stdout channel: machine-readable JSON lines for debug / --dry-run / CI.

One ``json.dumps`` line per send on stdout; the logging framework writes to
stderr, so stdout stays pipeable. Optional user template renders into the
``text`` field next to the structured payload.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, IO, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel, item_view
from shishi.push.templates import TemplateRenderError, TemplateRenderer

__all__ = ["StdoutChannel"]

logger = logging.getLogger(__name__)


class StdoutChannel(TrendAwareChannel):
    """``stdout`` channel: structured JSON payload; never fails on transport
    (no network), a broken user template raises a structured ``PushSendError``
    (``template_render_error``) like every other channel.

    Args:
        template: optional user template (Jinja2); rendered text lands in
            the ``text`` field alongside the structured ``items``.
        renderer: template renderer; defaults to a shared sandboxed one.
        out: writable stream (constructor injection for tests); default
            ``sys.stdout``.
    """

    name = "stdout"
    #: stdout 永不支持目录寻址(schema 配 targets 即拒);显式声明保住
    #: isinstance(Channel) 判定。
    supports_targeting = False

    def __init__(
        self,
        *,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        out: IO[str] | None = None,
    ) -> None:
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._out = out or sys.stdout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Print one JSON line describing the message and its items."""
        payload: dict[str, Any] = {
            "channel": "stdout",
            "kind": context.kind,
            "slot": context.slot,
            "date": context.date,
            "category": context.category,
            "count": len(items),
            "items": [item_view(item) for item in items],
        }
        if self._template is not None:
            # 与 feishu_card 同契约:渲染失败包装为结构化 PushSendError,
            # 不以裸 TemplateRenderError 逃出 Channel 协议。
            try:
                payload["text"] = self._renderer.render(
                    self._template, items, context, **self.trend_render_kwargs()
                )
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
        print(json.dumps(payload, ensure_ascii=False), file=self._out)
        logger.debug(
            "stdout 通道输出: slot=%s kind=%s count=%d", context.slot, context.kind, len(items)
        )
