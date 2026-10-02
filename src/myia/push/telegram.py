"""Telegram Bot API channel (``sendMessage``, HTML parse mode).

One ``POST {TELEGRAM_API_BASE}/bot{token}/sendMessage`` per message chunk.
Telegram caps one message at 4096 characters (:data:`MESSAGE_LIMIT`); a
longer digest is **auto-split** (:func:`split_message`) at newline boundaries
and sent in order — the built-in HTML layout keeps every item on one capped
line, so a chunk boundary always falls between items and never cuts a tag
mid-way (template output carries no parse_mode, so even a hard mid-line cut
there is plain-text safe).

标题与飞书卡片共用 :func:`myia.push.feishu_card.card_title`,跨通道标题一致。
With a user template the rendered text is sent **without** ``parse_mode``
(user-controlled plain text; HTML-escaping it would corrupt their intent).

Credentials stay references until send time (security baseline: 凭据零明文):
the bot token and chat id resolve from ``env:``/``keychain:`` references at
send time, and error messages carry reference names only, never values.

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real API.
"""

from __future__ import annotations

import html
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
from myia.push.feishu_card import card_title
from myia.push.templates import TemplateRenderError, TemplateRenderer
from myia.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MAX_ITEM_LINE_LENGTH",
    "MESSAGE_LIMIT",
    "TELEGRAM_API_BASE",
    "TelegramChannel",
    "build_message",
    "split_message",
]

logger = logging.getLogger(__name__)

#: Telegram Bot API base; the send endpoint is ``{base}/bot{token}/sendMessage``.
TELEGRAM_API_BASE = "https://api.telegram.org"
#: Bot-token credential reference (resolved at send time; value never logged).
DEFAULT_TOKEN_ENV_REF = "env:TELEGRAM_BOT_TOKEN"
#: Receiving chat id credential reference (constructor ``target`` overrides).
DEFAULT_TARGET_ENV_REF = "env:TELEGRAM_CHAT_ID"
#: Bot API sendMessage limit: 1-4096 characters per message.
MESSAGE_LIMIT = 4096
#: Built-in layout caps one item line well below ``MESSAGE_LIMIT`` so newline
#: splitting never has to cut inside HTML tags (a >4096-char title/URL would
#: otherwise hard-split into invalid HTML and Telegram would reject the chunk).
MAX_ITEM_LINE_LENGTH = 1024


def split_message(text: str, *, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split one message into send-sized chunks, newest-safe for Telegram.

    Splitting prefers newline boundaries (item lines in the built-in layout);
    a single line longer than ``limit`` is hard-split at exactly ``limit``
    characters. Empty input yields ``[]``; chunks keep declaration order, are
    never empty, and each stays within ``limit``.

    Note: the Bot API counts characters of the post-parse text — the raw-text
    chunks produced here are therefore an upper bound (built-in HTML chunks
    only shrink after tags are parsed; template chunks carry no parse_mode).
    """
    if not text:
        return []
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].rstrip("\n"))
        remaining = remaining[cut:].lstrip("\n")
    return [part for part in parts if part]


def _item_line(view: Mapping[str, Any]) -> str:
    """One item as an HTML line: linked title (optional category suffix).

    截断只作用于**文本**,永不切进标签(整行硬截会把行切成 ``▸ <a href="…``
    这类未闭合标签,Telegram 以 400 拒收整个 chunk——digest 留池重试会让同一
    非法 chunk 逐槽位永久失败,形成毒池):标题过长先缩标题;URL 本身超限时
    整行降级为纯文本。被截断的文本若停在转义实体(&amp;)中间,回退到实体前,
    避免产出残缺实体。
    """
    prefix = "▸ "
    ellipsis = "…"
    title = html.escape(str(view.get("title") or "(无标题)"))
    category = view.get("category")
    suffix = f" · {html.escape(str(category))}" if category else ""

    def _clip(text: str) -> str:
        """Trim a partial trailing escape entity (``&am``) after truncation."""
        amp = text.rfind("&")
        if amp != -1 and ";" not in text[amp:]:
            text = text[:amp]
        return text

    url = view.get("url")
    if url:
        href = html.escape(str(url), quote=True)
        link = f'<a href="{href}">{title}</a>'
        if len(prefix) + len(link) + len(suffix) <= MAX_ITEM_LINE_LENGTH:
            return f"{prefix}{link}{suffix}"
        fixed = len(prefix) + len(f'<a href="{href}">') + len("</a>") + len(suffix) + len(ellipsis)
        room = MAX_ITEM_LINE_LENGTH - fixed
        if room >= 1:
            shown = _clip(title[:room])
            return f'{prefix}<a href="{href}">{shown}{ellipsis}</a>{suffix}'
        # URL 本身超限:降级纯文本(title + suffix 一并截断,无任何标签)
        budget = MAX_ITEM_LINE_LENGTH - len(prefix) - len(ellipsis)
        text = _clip(f"{title}{suffix}"[:budget])
        return f"{prefix}{text}{ellipsis}"

    budget = MAX_ITEM_LINE_LENGTH - len(prefix) - len(ellipsis)
    text = _clip(f"{title}{suffix}"[:budget])
    return f"{prefix}{text}{ellipsis}"


def _item_also_line(view: Mapping[str, Any]) -> str:
    """Merged-card 「另见 N 源」 line (v0.4 事件聚合), HTML-escaped.

    独立成行且同样受 :data:`MAX_ITEM_LINE_LENGTH` 约束:split_message 只在
    行边界切割,行内永不出现未闭合标签(与 :func:`_item_line` 同一安全论证);
    无合并信息返回空串,不占行。源列表过长时截断文本(链接逐个保留会超限,
    降级为计数说明——安全方向是少给链接,不是产出坏 HTML)。
    """
    also = also_seen_list(view)
    if not also:
        return ""
    prefix = f"　└ 另见 {len(also)} 源: "
    refs: list[str] = []
    for entry in also:
        title = html.escape(entry["title"])
        if entry["url"]:
            href = html.escape(entry["url"], quote=True)
            refs.append(f'<a href="{href}">{title}</a>')
        else:
            refs.append(title)
    line = prefix + "、".join(refs)
    if len(line) <= MAX_ITEM_LINE_LENGTH:
        return line
    # 截断:保留前缀与已放下的源,尾部以「…等 N 源」说明收尾(纯文本,无标签)
    shown: list[str] = []
    used = len(prefix)
    for ref in refs:
        addition = len(ref) + (1 if shown else 0)
        if used + addition > MAX_ITEM_LINE_LENGTH - len("…"):
            break
        shown.append(ref)
        used += addition
    return prefix + "、".join(shown) + f"…等 {len(also)} 源"


def build_message(items: Sequence[Any], context: SendContext) -> str:
    """Built-in HTML layout: production-style title + one link line per item.

    合并单卡条目额外携带一行「另见 N 源」(v0.4 事件聚合);每行独立受限,
    换行边界切割安全性与单条目布局一致。
    """
    lines = [card_title(context)]
    for item in items:
        view = item_view(item)
        lines.append(_item_line(view))
        also_line = _item_also_line(view)
        if also_line:
            lines.append(also_line)
    return "\n".join(lines)


class TelegramChannel(TrendAwareChannel):
    """``telegram`` channel: one ``sendMessage`` per ≤4096-char chunk.

    Args:
        target: chat id credential reference (``env:TELEGRAM_CHAT_ID`` style,
            resolved at send time); omitted → :data:`DEFAULT_TARGET_ENV_REF`.
        template: optional user template (Jinja2); when set its rendered
            output is the whole message (sent without ``parse_mode``), still
            auto-split at :data:`MESSAGE_LIMIT`; omitted → built-in HTML layout.
        token: pre-resolved bot token (constructor injection for tests);
            resolved from :data:`DEFAULT_TOKEN_ENV_REF` when omitted.
        renderer: template renderer; defaults to a shared sandboxed one.
        client: injectable ``httpx.AsyncClient`` (tests mock here); when
            omitted a per-send client is created with ``timeout``.
        timeout: per-send timeout in seconds for the self-managed client.

    Raises:
        PushSendError: credential resolution failed, HTTP transport failed,
            non-JSON response, or the API answered ``ok != true``. Chunks are
            sent in order; a failure mid-sequence fails the whole channel
            send (a flush-level retry may re-deliver earlier chunks — the
            digest.py 留池重试 contract, accepted trade-off).
    """

    name = "telegram"

    def __init__(
        self,
        *,
        target: str | None = None,
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
        """Send the item batch as one or more sequential ``sendMessage`` calls.

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        chat_id = self._resolve_chat_id()
        parts, parse_mode = self._compose(items, context)
        for text in parts:
            await self._post_message(token, chat_id, text, parse_mode)
        logger.debug(
            "telegram 发送完成: slot=%s kind=%s count=%d parts=%d",
            context.slot,
            context.kind,
            len(items),
            len(parts),
        )

    def _compose(self, items: Sequence[Any], context: SendContext) -> tuple[list[str], str | None]:
        """Message text chunks plus the parse mode to declare (None = plain)."""
        if self._template is not None:
            # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
            # template_render_error(语法错误已在加载期被 schema 拒绝)。
            try:
                text = self._renderer.render(self._template, items, context, **self.trend_render_kwargs())
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
            return split_message(text), None
        return split_message(build_message(items, context)), "HTML"

    def _resolve_token(self) -> str:
        if self._token is not None:
            return self._token
        try:
            return resolve_credential(DEFAULT_TOKEN_ENV_REF)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"telegram bot 凭据解析失败: {exc}") from exc

    def _resolve_chat_id(self) -> str:
        try:
            return resolve_credential(self._target or DEFAULT_TARGET_ENV_REF)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"telegram target 解析失败: {exc}") from exc

    async def _post_message(
        self, token: str, chat_id: str, text: str, parse_mode: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"telegram 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"telegram 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, Mapping) or data.get("ok") is not True:
            error_code = data.get("error_code") if isinstance(data, Mapping) else None
            description = (
                data.get("description") if isinstance(data, Mapping) else response.text[:200]
            )
            raise PushSendError(
                "telegram_api_error",
                f"telegram API 返回错误: error_code={error_code} description={description}",
            )
        return dict(data)
