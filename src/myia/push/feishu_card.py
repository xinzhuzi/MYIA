"""Feishu interactive-card channel (open.feishu.cn, im/v1/messages).

``target`` uses ``env:FEISHU_CHAT_ID`` style references. Card layout mirrors
the production wf-crawl card.json (local-only reference, never committed):
``config.wide_screen_mode`` + blue header + ``lark_md`` divs separated by
``hr`` + a trailing ``note``. With a user template the rendered text becomes
a single ``lark_md`` div inside the same shell. No in-card feedback buttons
yet: the valuable/not-valuable loop is CLI-first today (``myia feedback
mark``) and the Telegram/Feishu callback *receivers* already speak the
button contract — the buttons themselves land with the desktop UI
(deliberate v0.3 scoping, PRD 10-01-v03-feedback-loop Notes).

Credentials stay references until send time (security baseline: 凭据零明文):
the bot token comes from ``env:FEISHU_BOT_TOKEN`` (or an injected value for
tests). Errors carry reference names only, never resolved values.

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real API.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

import httpx

from myia.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
    also_seen_list,
    item_view,
)
from myia.push.templates import TemplateRenderError, TemplateRenderer
from myia.schema import CredentialResolveError, resolve_credential

__all__ = [
    "API_URL",
    "DEFAULT_TOKEN_ENV_REF",
    "FeishuCardChannel",
    "build_card",
    "build_markdown_card",
    "card_title",
]

logger = logging.getLogger(__name__)

#: Feishu open-platform message endpoint (chat-id receive mode).
API_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
#: Bot credential reference; the value is a tenant access token.
DEFAULT_TOKEN_ENV_REF = "env:FEISHU_BOT_TOKEN"
DEFAULT_HEADER_COLOR = "blue"
CARD_FOOTER = "MYIA 自动聚合推送 · 条目来自公开论坛分享,注意甄别风险。"


def card_title(context: SendContext) -> str:
    """Production-style card title: 📡 聚合日报 / 🔔 立即推送."""
    subject = context.category or "情报"
    day = context.date[5:] if len(context.date) >= 10 else context.date
    if context.kind == "immediate":
        return f"🔔 {subject} · {day}"
    return f"📡 {subject}日报 {day} · {context.slot_label}摘要"


def _item_markdown(view: Mapping[str, Any]) -> str:
    """One item as a lark_md line: bold title linked to the source URL.

    合并单卡(v0.4 事件聚合):条目携带 ``also_seen`` 时在主行下追加
    「另见 N 源」列表(每源一个链接,无 url 的源退回纯标题)。
    """
    title = str(view.get("title") or "(无标题)")
    url = view.get("url")
    line = f"**[{title}]({url})**" if url else f"**{title}**"
    category = view.get("category")
    if category:
        line += f" · {category}"
    also = also_seen_list(view)
    if also:
        refs = "、".join(
            f"[{entry['title']}]({entry['url']})" if entry["url"] else entry["title"]
            for entry in also
        )
        line += f"\n　└ 另见 {len(also)} 源: {refs}"
    return line


def build_card(
    items: Sequence[Any],
    *,
    title: str,
    footer: str | None = CARD_FOOTER,
    header_color: str = DEFAULT_HEADER_COLOR,
) -> dict[str, Any]:
    """Build an interactive card with one lark_md div per item (hr between)."""
    elements: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if index:
            elements.append({"tag": "hr"})
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": _item_markdown(item_view(item))}}
        )
    if not elements:
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "本槽位没有待推送条目"}}
        )
    if footer:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    return _card_shell(title, elements, header_color)


def build_markdown_card(
    markdown: str,
    *,
    title: str,
    footer: str | None = CARD_FOOTER,
    header_color: str = DEFAULT_HEADER_COLOR,
) -> dict[str, Any]:
    """Build an interactive card wrapping one rendered-markdown div."""
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": markdown}}
    ]
    if footer:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    return _card_shell(title, elements, header_color)


def _card_shell(title: str, elements: list[dict[str, Any]], header_color: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": header_color, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


class FeishuCardChannel(TrendAwareChannel):
    """``feishu_card`` channel: one POST per message to open.feishu.cn.

    Args:
        target: credential reference for the receiving chat id
            (``env:FEISHU_CHAT_ID`` style, resolved at send time).
        template: optional user template (Jinja2); omitted → built-in layout.
        token: pre-resolved bot token (constructor injection for tests);
            resolved from :data:`DEFAULT_TOKEN_ENV_REF` when omitted.
        renderer: template renderer; defaults to a shared sandboxed one.
        client: injectable ``httpx.AsyncClient`` (tests mock here); when
            omitted a per-send client is created with ``timeout``.
        timeout: per-send timeout in seconds for the self-managed client.

    Raises:
        PushSendError: credential resolution failed, HTTP transport failed,
            non-JSON response, or Feishu answered a non-zero ``code``.
    """

    name = "feishu_card"

    def __init__(
        self,
        *,
        target: str,
        template: str | None = None,
        token: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._template = template
        self._token = token
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render and POST one interactive card carrying ``items``.

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        chat_id = self._resolve_target()
        card = self._build_card(items, context)
        body = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        await self._post(token, body)
        logger.debug(
            "飞书卡片已提交: slot=%s kind=%s count=%d", context.slot, context.kind, len(items)
        )

    def _build_card(self, items: Sequence[Any], context: SendContext) -> dict[str, Any]:
        title = card_title(context)
        if self._template is not None:
            # 契约:send 只抛 PushSendError —— 模板渲染失败(未定义变量/沙箱
            # 拦截等运行期错误;语法错误已在加载期被 schema 拒绝)包装为
            # 结构化的 template_render_error,由调用方按通道隔离。
            try:
                markdown = self._renderer.render(self._template, items, context, **self.trend_render_kwargs())
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
            return build_markdown_card(markdown, title=title)
        return build_card(items, title=title)

    def _resolve_token(self) -> str:
        if self._token is not None:
            return self._token
        try:
            return resolve_credential(DEFAULT_TOKEN_ENV_REF)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"飞书 bot 凭据解析失败: {exc}") from exc

    def _resolve_target(self) -> str:
        try:
            return resolve_credential(self._target)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"飞书 target 解析失败: {exc}") from exc

    async def _post(self, token: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        params = {"receive_id_type": "chat_id"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    API_URL, params=params, headers=headers, json=body
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        API_URL, params=params, headers=headers, json=body
                    )
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"飞书卡片请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"飞书响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, Mapping) or data.get("code") != 0:
            code = data.get("code") if isinstance(data, Mapping) else None
            message = data.get("msg") if isinstance(data, Mapping) else response.text[:200]
            raise PushSendError(
                "feishu_api_error", f"飞书 API 返回错误: code={code} msg={message}"
            )
        logger.debug("飞书 API 调用成功")
        return dict(data)
