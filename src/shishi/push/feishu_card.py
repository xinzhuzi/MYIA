"""Feishu interactive-card channel (open.feishu.cn, im/v1/messages).

蓝本归属(10-03-messaging-feishu):定向寻址语义移植自 Hermes
``plugins/platforms/feishu/``(NousResearch/Hermes-Agent,MIT;发送路由见
其 ``adapter.py`` 的 receive_id 选择,直达 id 前缀见 Hermes
``tools/send_message_targets.py`` 的 ``_FEISHU_TARGET_RE``)。MYIA 重写为
自有通道内的两个钩子(:meth:`FeishuCardChannel.parse_direct_ref` /
:meth:`FeishuCardChannel.discover_directory`),绝不整块复制上游。【偏离
注记(grill 事实轮):Hermes feishu 不调任何列表 API,目录纯靠入站会话
回填;MYIA 出站-only 无入站可回填,``im/v1/chats`` 列表发现是 MYIA 侧
新增设计,字段契约以官方文档为准】。

``target`` uses ``env:FEISHU_CHAT_ID`` style references. Card layout mirrors
the production wf-crawl card.json (local-only reference, never committed):
``config.wide_screen_mode`` + blue header + ``lark_md`` divs separated by
``hr`` + a trailing ``note``. With a user template the rendered text becomes
a single ``lark_md`` div inside the same shell. No in-card feedback buttons
yet: the valuable/not-valuable loop is CLI-first today (``myia feedback
mark``) and the Telegram/Feishu callback *receivers* already speak the
button contract — the buttons themselves land with the desktop UI
(deliberate v0.3 scoping, PRD 10-01-v03-feedback-loop Notes).

immediate 带图(看图 v2,PRD 10-03-vision-v2):``kind="immediate"`` 单条目
且 ``metadata.image_files`` 有本机存在文件时,先经 ``im/v1/images``
multipart 上传首图换 ``image_key``(同一 tenant token,应用需开
``im:resource`` 权限),卡片条目 div 后插 ``img`` 元素;上传失败/文件缺失
降级「图析摘要卡」文本形态(lark_md 图析摘要行 + 配图 N 张注记),只告警
不阻投递。digest 批量不带图;路由/when 逻辑零改动,仅组装层增强。

Credentials stay references until send time (security baseline: 凭据零明文):
the bot token comes from ``env:FEISHU_BOT_TOKEN`` (or an injected value for
tests). Errors carry reference names only, never resolved values.

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real API.
"""

from __future__ import annotations

import asyncio
import json
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
from shishi.push.directory import ChannelEntry
from shishi.push.targets import ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "API_URL",
    "CAPTION_EXCERPT_CHARS",
    "CHATS_API_URL",
    "CHATS_MAX_PAGES",
    "CHATS_PAGE_SIZE",
    "DEFAULT_TOKEN_ENV_REF",
    "DIRECT_REF_RE",
    "FeishuCardChannel",
    "IMAGES_API_URL",
    "build_card",
    "build_markdown_card",
    "card_title",
    "escape_lark_md",
]

logger = logging.getLogger(__name__)

#: Feishu open-platform message endpoint (chat-id receive mode).
API_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
#: Feishu open-platform chat-list endpoint(目录发现;官方字段:items[].chat_id/
#: name/chat_status,翻页 has_more/page_token,page_size 上限 100)。
CHATS_API_URL = "https://open.feishu.cn/open-apis/im/v1/chats"
#: Feishu open-platform image-upload endpoint(immediate 带图,看图 v2):卡片
#: ``img`` 元素的前置步骤——multipart 上传本地图换 ``image_key``。同一
#: tenant access token(Bearer);应用需具 ``im:resource``(上传图片)权限,
#: 部署侧未开通时上传返回非零 code,通道降级图析摘要卡(见 _attach_card_image)。
IMAGES_API_URL = "https://open.feishu.cn/open-apis/im/v1/images"
#: 图析摘要在卡片 lark_md 行 / img alt 内的截断长度(保持卡片可读)。
CAPTION_EXCERPT_CHARS = 240
#: 翻页 page_size(官方默认 20,上限 100;取上限减少往返)。
CHATS_PAGE_SIZE = 100
#: 翻页保底上限(design D2:防服务端 has_more 死循环;20 页 × 100 = 2000 群)。
CHATS_MAX_PAGES = 20
#: Bot credential reference; the value is a tenant access token.
DEFAULT_TOKEN_ENV_REF = "env:FEISHU_BOT_TOKEN"
DEFAULT_HEADER_COLOR = "blue"
CARD_FOOTER = "MYIA 自动聚合推送 · 条目来自公开论坛分享,注意甄别风险。"

#: 直达对象形态(Hermes ``send_message_targets.py`` 的 ``_FEISHU_TARGET_RE``
#: 同款:oc_ 群/私聊、ou_ open_id、on_ union_id、chat_/open_ 原生 id;可选
#: ``:thread`` 部分只解析不入发送路由)。
DIRECT_REF_RE = re.compile(r"^((?:oc|ou|on|chat|open)_[-A-Za-z0-9]+)(?::([-A-Za-z0-9_]+))?$")


def escape_lark_md(text: str) -> str:
    """lark_md 特殊字符字面化:``[`` ``]`` ``<`` 前加反斜杠。

    图析摘要(caption)是模型产物,可能携带 ``[链接式](文本)`` / ``<标签>``
    形态的字符序列 —— 不转义会被飞书解析成残缺链接/标签,摘要被吃掉。
    只处理这三个字符(lark_md 链接与标签语法的最小封闭集),纯装饰性的
    ``[配图 N 张未附]`` 注记是本通道自产文本,不经此函数。
    """
    return text.replace("[", "\\[").replace("]", "\\]").replace("<", "\\<")


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

    目录寻址(10-03-messaging-feishu):``supports_targeting=True``,
    ``context.target.chat_id`` 优先、退回 legacy ``target`` 引用(schema
    侧 targets 在场时 target 可省,故构造参数同样可选);``ou_`` 前缀按
    ``receive_id_type=open_id`` 投递(Hermes 发送路由同款),其余维持
    ``chat_id``。

    Args:
        target: credential reference for the receiving chat id
            (``env:FEISHU_CHAT_ID`` style, resolved at send time);与
            ``targets`` 定向配置互斥可省——两条寻址路径至少一条在场,
            否则发送期报 ``missing_target`` 结构化错误。
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
    #: 目录寻址已开(10-03-messaging-feishu):context.target 优先,legacy
    #: target 兜底;协议判定见 base.Channel docstring。
    supports_targeting = True
    #: 目录发现 429 退避秒数(design D2:退避一次再试;测试可钉 0)。
    discover_backoff_seconds = 1.0

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
        """Render and POST one interactive card carrying ``items``.

        定向优先(``context.target.chat_id`` > legacy ``target`` 引用);
        两条路径都缺席时报 ``missing_target`` 结构化错误(fail-fast,
        绝不猜默认群)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_token()
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_target()
        )
        card = self._build_card(items, context)
        # immediate 带图(看图 v2):先建卡(模板渲染错误在此抛出,零请求
        # 发出),再尝试附图——上传/降级均在组装层内闭环,不阻投递。
        card = await self._attach_card_image(token, card, items, context)
        body = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        await self._post(token, body)
        logger.debug(
            "飞书卡片已提交: slot=%s kind=%s count=%d target=%s",
            context.slot, context.kind, len(items), context.target,
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

    # ------------------------------------------------- immediate 带图(看图 v2)

    async def _attach_card_image(
        self, token: str, card: dict[str, Any], items: Sequence[Any], context: SendContext
    ) -> dict[str, Any]:
        """Immediate 单条目带图组装:img 元素(上传成功)或图析摘要行(降级)。

        仅 ``kind="immediate"`` 且恰一条目生效(digest 批量不带图,PRD
        10-03-vision-v2 定案);``metadata.image_files`` 缺席 → 卡片原样返回,
        零行为变化。两条路径:

        - 上传成功:条目 div 之后插入 ``img`` 元素(``img_key`` + alt=图析
          摘要截断,无摘要退回标题),内文布局不动;
        - 上传失败 / 文件缺失:降级「图析摘要卡」文本形态——lark_md 附
          「图析: …」摘要行 + 「配图 N 张未附」注记,只告警不阻投递。

        多图只上 ``paths[0]``;alt/摘要行均截断到
        :data:`CAPTION_EXCERPT_CHARS`;摘要行入 lark_md 前经
        :func:`escape_lark_md` 字面化(``[``/``]``/``<``),alt 回退 title 时
        同样截断。
        """
        if context.kind != "immediate" or len(items) != 1:
            return card
        info = item_images(items[0])
        if info is None:
            return card
        elements = card.get("elements")
        if not isinstance(elements, list) or not elements:
            return card  # 防御:非预期卡片形态不动(elements 恒非空,见 build_card)
        image_key: str | None = None
        if info.paths:
            try:
                image_key = await self._upload_image(token, info.paths[0])
            except (PushSendError, OSError) as exc:
                logger.warning("飞书图片上传失败,降级图析摘要卡(不阻推送): %s", exc)
        excerpt = clip_text(info.caption, CAPTION_EXCERPT_CHARS) if info.caption else ""
        if image_key is not None:
            # alt 是 plain_text(零解析),但回退 title 时同样截断到 CAPTION 上限
            # —— 超长标题会让 alt 失去「一眼可读」的辅助语义。
            alt = excerpt or clip_text(
                str(item_view(items[0]).get("title") or ""), CAPTION_EXCERPT_CHARS
            )
            elements.insert(
                1,
                {
                    "tag": "img",
                    "img_key": image_key,
                    "alt": {"tag": "plain_text", "content": alt},
                },
            )
            return card
        # lark_md 行:caption 是模型产物,[]< 序列先字面化再插入(防飞书
        # 误解析成链接/标签把摘要吃掉);「配图 N 张未附」是自产注记,原样。
        lines = [f"　└ 图析: {escape_lark_md(excerpt)}"] if excerpt else []
        lines.append(f"　└ [配图 {info.declared} 张未附]")
        elements.insert(
            1, {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        )
        logger.info(
            "飞书卡片采用图析摘要文本形态: 声明 %d 图,本机存在 %d", info.declared, len(info.paths)
        )
        return card

    async def _upload_image(self, token: str, image_path: str) -> str:
        """Upload one local image → ``image_key`` (``im/v1/images``, multipart).

        官方卡片 ``img`` 元素的前置步骤:multipart 携带 ``image_type=message``
        与图片文件,同一 tenant access token(Bearer)授权;应用需开
        ``im:resource`` 权限,未开通时返回非零 code(调用方降级,不硬造)。

        Raises:
            PushSendError: 文件读取失败、HTTP 传输失败、非 JSON 响应、飞书
                非零 code,或响应缺 ``data.image_key``。
        """
        path = Path(image_path)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise PushSendError(
                "image_read_error",
                f"飞书图片读取失败({path.name}): {type(exc).__name__}: {exc}",
            ) from exc
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {"Authorization": f"Bearer {token}"}
        data = {"image_type": "message"}
        files = {"image": (path.name, payload, content_type)}
        try:
            if self._client is not None:
                response = await self._client.post(
                    IMAGES_API_URL, data=data, files=files, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        IMAGES_API_URL, data=data, files=files, headers=headers
                    )
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"飞书图片上传请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        envelope = self._parse_response(response)
        payload_json = envelope.get("data")
        image_key = (
            payload_json.get("image_key") if isinstance(payload_json, Mapping) else None
        )
        if not isinstance(image_key, str) or not image_key.strip():
            raise PushSendError(
                "invalid_response",
                f"飞书图片上传响应缺 data.image_key: {str(envelope)[:200]!r}",
            )
        return image_key

    def _resolve_token(self) -> str:
        if self._token is not None:
            return self._token
        try:
            return resolve_credential(DEFAULT_TOKEN_ENV_REF)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"飞书 bot 凭据解析失败: {exc}") from exc

    def _resolve_target(self) -> str:
        if self._target is None:
            raise PushSendError(
                "missing_target",
                "feishu_card 未配置 legacy target(env:/keychain: 引用),"
                "本次发送也未携带 context.target——两条寻址路径至少一条在场",
            )
        try:
            return resolve_credential(self._target)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"飞书 target 解析失败: {exc}") from exc

    async def _post(self, token: str, body: dict[str, Any]) -> dict[str, Any]:
        receive_id = str(body.get("receive_id") or "")
        # ou_ 前缀是用户 open_id,须按 open_id 路由;其余(oc_/chat_/……)
        # 一律 chat_id(Hermes feishu adapter 发送路由同款形态)。
        params = {
            "receive_id_type": "open_id" if receive_id.startswith("ou_") else "chat_id"
        }
        headers = {"Authorization": f"Bearer {token}"}
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

    # ------------------------------------------------- 目录发现(design D1/D2)

    async def discover_directory(self) -> list[ChannelEntry]:
        """List every group chat the bot belongs to(目录发现,MYIA 新增设计)。

        ``GET im/v1/chats`` 官方契约(实现期已对照文档逐字段核对):items[]
        取 ``chat_id``/``name``,``chat_status`` 非 normal(已解散)跳过;
        ``has_more``/``page_token`` 翻页,保底 :data:`CHATS_MAX_PAGES` 页;
        429 退避一次再试。私聊不在该 API 返回内(官方明示排除 p2p)——
        私聊/话题靠别名文件手工补录(prd Q5 定案)。

        Raises:
            PushSendError: 凭据缺失/失效、HTTP 传输失败、非 JSON 响应或
                飞书返回非零 code(如 401 对应的 token 失效)。调用方
                (:meth:`shishi.push.directory.ChannelDirectory.refresh`)按
                发现失败隔离:告警 + 保留旧桶,不触碰投递死信账本。
        """
        token = self._resolve_token()
        entries: list[ChannelEntry] = []
        page_token: str | None = None
        for page_index in range(CHATS_MAX_PAGES):
            data = await self._fetch_chats_page(token, page_token)
            entries.extend(
                entry
                for raw in (data.get("items") or [])
                if (entry := self._entry_from_chat(raw)) is not None
            )
            if not data.get("has_more"):
                return entries
            page_token = str(data.get("page_token") or "").strip()
            if not page_token:
                # has_more 却不给游标:防御性止步(不依赖服务端守约)。
                logger.warning("飞书群列表 has_more 但缺 page_token,目录发现提前止步")
                return entries
        logger.warning(
            "飞书群列表翻页达保底上限 %d 页,目录可能不完整(服务端 has_more 未收敛)",
            CHATS_MAX_PAGES,
        )
        return entries

    @staticmethod
    def _entry_from_chat(raw: Any) -> ChannelEntry | None:
        """One ``items[]`` → :class:`ChannelEntry`;形态坏/已解散 → None。

        群列表条目恒为 ``type="group"``、无话题(``thread_id=None``);
        ``name`` 缺失时退回 chat_id 占位(条目仍可按 id 寻址)。
        """
        if not isinstance(raw, Mapping):
            return None
        chat_id = str(raw.get("chat_id") or "").strip()
        if not chat_id:
            return None
        status = str(raw.get("chat_status") or "normal").strip()
        if status and status != "normal":
            logger.debug("跳过非 normal 状态群: chat_id=%s status=%s", chat_id, status)
            return None
        name = str(raw.get("name") or "").strip() or chat_id
        return ChannelEntry(platform="feishu", chat_id=chat_id, name=name, type="group")

    async def _fetch_chats_page(self, token: str, page_token: str | None) -> dict[str, Any]:
        """One ``GET im/v1/chats`` page(429 退避一次再试,design D2)。"""
        params: dict[str, Any] = {"user_id_type": "open_id", "page_size": CHATS_PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        headers = {"Authorization": f"Bearer {token}"}
        response: httpx.Response | None = None
        for attempt in (1, 2):
            try:
                if self._client is not None:
                    response = await self._client.get(
                        CHATS_API_URL, params=params, headers=headers
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        response = await client.get(
                            CHATS_API_URL, params=params, headers=headers
                        )
            except httpx.HTTPError as exc:
                raise PushSendError(
                    "http_error", f"飞书群列表请求失败: {type(exc).__name__}: {exc}"
                ) from exc
            if response.status_code != 429 or attempt == 2:
                break
            logger.warning(
                "飞书群列表 429 限频,退避 %.1fs 重试一次", self.discover_backoff_seconds
            )
            await asyncio.sleep(self.discover_backoff_seconds)
        assert response is not None  # 循环体至少执行一次
        envelope = self._parse_response(response)
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise PushSendError(
                "invalid_response",
                f"飞书群列表响应缺 data 对象: {str(envelope)[:200]!r}",
            )
        return dict(data)

    # ------------------------------------------------- 直达解析(design D4)

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """显式 id 直达:``oc_/ou_/on_/chat_/open_`` 前缀不经目录。

        Hermes ``_FEISHU_TARGET_RE`` 同款形态;``oc_x:mt_x`` 的 ``:mt_x``
        部分解析进 ``thread_id``(发送侧不实装,prd 非目标)。非 id 形态
        返回 None(调用方回落目录四路径解析)。
        """
        match = DIRECT_REF_RE.fullmatch(ref.strip())
        if match is None:
            return None
        chat_id, thread_id = match.group(1), match.group(2)
        return ChannelTarget(
            platform="feishu",
            chat_id=chat_id,
            thread_id=thread_id,
            resolved_from="direct",
        )
