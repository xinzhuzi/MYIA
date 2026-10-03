"""Telegram Bot API channel (``sendMessage``, HTML parse mode).

One ``POST {TELEGRAM_API_BASE}/bot{token}/sendMessage`` per message chunk.
Telegram caps one message at 4096 characters (:data:`MESSAGE_LIMIT`); a
longer digest is **auto-split** (:func:`split_message`) at newline boundaries
and sent in order — the built-in HTML layout keeps every item on one capped
line, so a chunk boundary always falls between items and never cuts a tag
mid-way (template output carries no parse_mode, so even a hard mid-line cut
there is plain-text safe).

immediate 带图(看图 v2,PRD 10-03-vision-v2):``kind="immediate"`` 单条目
且 ``metadata.image_files`` 有本机存在文件时,先 ``sendPhoto`` 首图
(multipart,caption=标题+图析摘要截断 ≤ :data:`CAPTION_LIMIT`)再发正文
sendMessage;digest 批量不带图。文件缺失/发送失败回退纯文本并告警,
不阻推送(路由/when 逻辑零改动,仅组装层增强)。

目录寻址(10-03-messaging-telegram):``supports_targeting=True``,
``context.target.chat_id`` 优先、退回 legacy ``env:TELEGRAM_CHAT_ID`` 引用;
``parse_direct_ref`` 直达解析数字 chat_id / ``@username``(蓝本:Hermes
``plugins/platforms/telegram/telegram_ids.py`` 的 id/username 双形态,
NousResearch/Hermes-Agent,MIT——MYIA 按本档语义重写,不整块复制)。
Telegram Bot API 无「列出会话」能力(蓝本事实轮核),目录条目唯一来源是
被动积累:feedback 轮询的 ``on_chat`` sink 见 telegram_feedback.py。

标题与飞书卡片共用 :func:`shishi.push.feishu_card.card_title`,跨通道标题一致。
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
import mimetypes
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
    also_seen_list,
    clip_text,
    item_images,
    item_view,
)
from shishi.push.feishu_card import card_title
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CAPTION_LIMIT",
    "CHAT_ID_RE",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MAX_ITEM_LINE_LENGTH",
    "MESSAGE_LIMIT",
    "TELEGRAM_API_BASE",
    "TELEGRAM_USERNAME_RE",
    "TelegramChannel",
    "build_message",
    "build_photo_caption",
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
#: Bot API sendPhoto caption limit: 0-1024 characters per caption.
CAPTION_LIMIT = 1024
#: Built-in layout caps one item line well below ``MESSAGE_LIMIT`` so newline
#: splitting never has to cut inside HTML tags (a >4096-char title/URL would
#: otherwise hard-split into invalid HTML and Telegram would reject the chunk).
MAX_ITEM_LINE_LENGTH = 1024
#: 直达数字 chat_id(私聊正数 / 群负数 / 频道 ``-100…`` 前缀,均为纯数字形态)。
CHAT_ID_RE = re.compile(r"^-?\d+$")
#: 公开 ``@username``(Hermes ``telegram_ids`` 同款宽度:5-32 位字母数字下划线,
#: 容忍 4 位历史短名;**只对公开用户名有效**,私聊无公开用户名)。
TELEGRAM_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,32}$")


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
    降级为计数说明——安全方向是少给链接,不是产出坏 HTML)。截断预算计入
    「…等 N 源」后缀的完整宽度,截断路径行长同样不超上限。
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
    # 截断:保留前缀与已放下的源,尾部以「…等 N 源」说明收尾(纯文本,无标签)。
    # 预算必须计入**完整后缀**宽度——早期实现只预留一个省略号,截断路径行长
    # 可超自声明的 1024 上限(两位数 N 时最多 6 字符)。
    suffix = f"…等 {len(also)} 源"
    shown: list[str] = []
    used = len(prefix)
    for ref in refs:
        addition = len(ref) + (1 if shown else 0)
        if used + addition > MAX_ITEM_LINE_LENGTH - len(suffix):
            break
        shown.append(ref)
        used += addition
    return prefix + "、".join(shown) + suffix


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


def build_photo_caption(view: Mapping[str, Any], image_caption: str) -> str:
    """sendPhoto caption:条目标题 + 图析摘要,整体不超 :data:`CAPTION_LIMIT`。

    纯文本(不声明 parse_mode):图析摘要直出,不做 HTML 转义。标题优先
    保全文;超限预算下先压摘要(尾缀省略号计入上限),标题独占超限时只留
    标题截断——任一路径产物长度 ≤ 1024。无图析摘要时 caption 即标题。
    """
    title = str(view.get("title") or "(无标题)")
    excerpt = image_caption.strip()
    if not excerpt:
        return clip_text(title, CAPTION_LIMIT)
    if len(title) + 1 + len(excerpt) <= CAPTION_LIMIT:
        return f"{title}\n{excerpt}"
    room = CAPTION_LIMIT - len(title) - 1 - len("…")  # "\n" + 尾省略号
    if room < 1:
        return clip_text(title, CAPTION_LIMIT)
    return f"{title}\n{clip_text(excerpt, room)}"


class TelegramChannel(TrendAwareChannel):
    """``telegram`` channel: one ``sendMessage`` per ≤4096-char chunk.

    目录寻址(10-03-messaging-telegram):``context.target.chat_id`` 优先、
    退回 legacy ``target``/``env:TELEGRAM_CHAT_ID`` 引用(不配 targets 的
    旧配置行为逐字节不变);4096 分段对每目标各自生效。

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
    #: 目录寻址已开(10-03-messaging-telegram):context.target 优先,legacy
    #: target/env 兜底;协议判定见 base.Channel docstring。
    supports_targeting = True

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

        定向优先(``context.target.chat_id`` > legacy target 引用);
        ``@username`` 形态目标在 API 报 chat 不存在时,错误文案附「私聊必须
        用数字 chat_id」提示(design D1:@username 仅对公开用户名有效)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_chat_id()
        )
        parts, parse_mode = self._compose(items, context)
        # immediate 带图(看图 v2):先图后文,先 compose 后图——模板渲染
        # 失败时零请求发出,绝不产出「有图无文」的孤儿图。
        await self._send_item_photo(chat_id, items, context)
        for text in parts:
            try:
                await self._post_message(token, chat_id, text, parse_mode)
            except PushSendError as exc:
                if chat_id.startswith("@") and "not found" in str(exc).lower():
                    # @username 只解析公开用户名;私聊/未公开群以此提示指路。
                    # 原厂描述保留在文案里,死信分类(delivery)照常命中。
                    raise PushSendError(
                        exc.code,
                        f"{exc};@username 仅对公开频道/群有效,"
                        "私聊必须用数字 chat_id 定向(见 telegram:@username 约定)",
                    ) from exc
                raise
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

    # ------------------------------------------------- immediate 带图(看图 v2)

    async def _send_item_photo(
        self, chat_id: str, items: Sequence[Any], context: SendContext
    ) -> None:
        """Immediate 单条目带图:先 sendPhoto 首图,任何失败回退纯文本。

        仅 ``kind="immediate"`` 且恰一条目时生效(digest 批量不带图,PRD
        10-03-vision-v2 定案);``metadata.image_files`` 缺席 → 原路径零变化。
        文件缺失 / 上传或 API 失败只告警不抛:正文 sendMessage 照发(不阻
        推送)。多图只发首图(:meth:`send_photo` 契约内),余图不逐一发送。
        """
        if context.kind != "immediate" or len(items) != 1:
            return
        info = item_images(items[0])
        if info is None:
            return
        if not info.paths:
            logger.warning(
                "telegram sendPhoto 回退纯文本: 条目声明 %d 图但本机无一存在", info.declared
            )
            return
        caption = build_photo_caption(item_view(items[0]), info.caption)
        try:
            await self.send_photo(chat_id, info.paths[0], caption)
        except (PushSendError, OSError) as exc:
            logger.warning("telegram sendPhoto 失败,回退纯文本(不阻推送): %s", exc)

    async def send_photo(
        self, chat_id: str, photo_path: str, caption: str
    ) -> dict[str, Any]:
        """``sendPhoto`` one local image file (multipart upload, Bot API).

        Caption 为纯文本(不声明 parse_mode),调用方负责长度约束
        (:func:`build_photo_caption` ≤ :data:`CAPTION_LIMIT`)。超时/错误
        处理与 :meth:`_post_message` 对齐;本地图不可读抛
        ``photo_read_error`` 结构化错误。

        Raises:
            PushSendError: 文件读取失败、HTTP 传输失败、非 JSON 响应或
                API ``ok != true``。
        """
        token = self._resolve_token()
        path = Path(photo_path)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise PushSendError(
                "photo_read_error",
                f"telegram 图片读取失败({path.name}): {type(exc).__name__}: {exc}",
            ) from exc
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data: dict[str, str] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        files = {"photo": (path.name, payload, content_type)}
        url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
        try:
            if self._client is not None:
                response = await self._client.post(url, data=data, files=files)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, data=data, files=files)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"telegram 图片请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        return self._parse_response(response)

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

    # ------------------------------------------------- 直达解析(design D1)

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """显式 id 直达:``<纯数字>``(含负号)与 ``@username`` 不经目录。

        Hermes ``telegram_ids`` 同款双形态:数字 id(私聊正数 / 群负数 /
        频道 ``-100…``)与公开 ``@username``(4-32 位字母数字下划线)直达;
        其余(名称等)返回 None,调用方回落目录四路径解析。

        **@username 仅对公开用户名(公开频道/群)有效**——私聊没有公开
        用户名,必须用数字 chat_id;误用时发送期 API 报 chat not found,
        错误文案会附该提示(:meth:`send`)。
        """
        value = ref.strip()
        if CHAT_ID_RE.fullmatch(value) or TELEGRAM_USERNAME_RE.fullmatch(value):
            return ChannelTarget(
                platform="telegram", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
