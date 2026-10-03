"""Telegram ``getUpdates`` feedback polling (桌面形态接收, grill Q7 定案).

The desktop form has no public endpoint for card callbacks, so the receiving
side polls the Bot API instead: every ``callback_query`` update whose
``data`` carries our feedback contract is turned into a callback record for
:func:`shishi.feedback.ingest_callbacks`. Long-polling (``getUpdates``
``timeout``) keeps this cheap; the pipeline's resident mode runs the loop in
the background whenever a ``telegram`` push channel is configured.

被动目录积累(10-03-messaging-telegram D2):Telegram Bot API 无「列出
会话」能力(蓝本事实轮核,Hermes 同款约束),目录条目唯一来源是入站
回填——轮询看到的每个 update 的 effective chat 归一为
:class:`~shishi.push.directory.ChannelEntry` 后经 ``on_chat`` sink 交回调方
(管线注入 ``ChannelDirectory.merge_entries("telegram", …)``)。sink 是
**旁路观察者**:解析/去重/错误路径零改动,sink 抛错只记日志不中断轮询;
不注入时行为与不带 sink 逐字节一致。

**一个 bot token 只允许一个轮询方**(素材 12):bot token 固定解析自
``env:TELEGRAM_BOT_TOKEN``,Telegram 对同一 token 的并发 ``getUpdates``
long-poll 回 **409 Conflict**。因此同一 token 下至多一个常驻进程开启反馈
轮询——多品类场景只给其中一个品类配 telegram 通道,或让其不常驻;跨进程
互斥不在库内实现(部署形态保证),``myia doctor`` 以
``telegram_token_poll_conflict`` finding 提示多品类共配的情形。

Callback-data contract (buttons belong to the desktop 正式版; the receiver
already speaks it): ``fb:<good|bad>:<dedup_key>`` — the dedup key may be a
URL and therefore contain colons, so the prefix splits at most twice. Note
the Bot API caps ``callback_data`` at 64 bytes; keys longer than that need a
short-reference scheme at *send* time (tracked gap, see the task's open
issues) — the receiver stays compatible with both.

Credentials stay references until send time (security baseline: 凭据零明文);
all HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import httpx

from shishi.push.base import DEFAULT_SEND_TIMEOUT_SECONDS
from shishi.push.directory import ChannelEntry
from shishi.push.telegram import DEFAULT_TOKEN_ENV_REF
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CALLBACK_PREFIX",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "PollResult",
    "TelegramCallback",
    "TelegramFeedbackError",
    "TelegramFeedbackPoller",
    "chat_entry_from_update",
    "entry_from_chat",
    "parse_callback_data",
]

logger = logging.getLogger(__name__)

#: Feedback callback-data prefix: ``fb:<verdict>:<dedup_key>``.
CALLBACK_PREFIX = "fb:"
#: Legal verdicts inside callback data (normalized again at ingestion).
_CALLBACK_VERDICTS = ("good", "bad")
#: Background-loop sleep between getUpdates calls (pipeline resident mode).
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
#: update 顶层携带 chat 的载荷键(首个命中即取;一条 update 只有一种载荷)。
_CHAT_BEARER_KEYS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "my_chat_member",
    "chat_member",
)
#: callback_query 的 chat 藏在 ``.message.chat`` 下(按钮回调的消息宿主)。
_CALLBACK_QUERY_KEY = "callback_query"


class TelegramFeedbackError(RuntimeError):
    """One feedback-poll failed (structured code; never a bare string).

    Attributes:
        code: ``http_error`` / ``invalid_response`` / ``telegram_api_error``
            / credential codes re-exported from
            :class:`shishi.schema.CredentialResolveError`.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_callback_data(data: Any) -> tuple[str, str] | None:
    """Parse ``fb:<verdict>:<dedup_key>``; None when not our feedback data.

    The dedup key keeps everything after the second colon (URLs contain
    colons themselves); verdicts are matched exactly against good/bad —
    anything else (foreign bots' callbacks, malformed data) returns None and
    the update is counted as skipped by the caller.
    """
    if not isinstance(data, str) or not data.startswith(CALLBACK_PREFIX):
        return None
    parts = data[len(CALLBACK_PREFIX) :].split(":", 1)  # maxsplit=1:URL 键自带冒号
    if len(parts) != 2:
        return None
    verdict, dedup_key = parts
    if verdict not in _CALLBACK_VERDICTS or not dedup_key.strip():
        return None
    return verdict, dedup_key


def entry_from_chat(chat: Any) -> ChannelEntry | None:
    """One Bot API ``chat`` object → :class:`ChannelEntry`(design D2 归一).

    归一规则(蓝本:Hermes telegram adapter ``_normalize_chat_type`` 的
    private→dm / supergroup→group 语汇,NousResearch/Hermes-Agent,MIT):

    - ``group``/``supergroup``:``title`` 为名,type=group;
    - ``channel``:``title`` 为名,type=channel;
    - ``private``(及未知形态按私聊兜底):``first_name [+ last_name]`` 为名,
      type=dm;有 username 拼进 name 尾注(``名 (@user)``)——不扩
      ChannelEntry schema,实现取简(design D2 定案)。

    名字缺失退回 username / chat_id 占位(条目仍可按 id 寻址);``id``
    缺失或载荷非 dict → None(静默丢弃,不毒化目录)。
    """
    if not isinstance(chat, Mapping):
        return None
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        return None
    chat_type = str(chat.get("type") or "").strip().lower()
    if chat_type in ("group", "supergroup", "channel"):
        # supergroup 归一为 group(design D2:两者都按群记,ENTRY_TYPES 无
        # supergroup 形态;Hermes 的 forum 话题形态属非目标)。
        name = str(chat.get("title") or "").strip() or chat_id
        return ChannelEntry(
            platform="telegram",
            chat_id=chat_id,
            name=name,
            type="group" if chat_type != "channel" else "channel",
        )
    first = str(chat.get("first_name") or "").strip()
    last = str(chat.get("last_name") or "").strip()
    username = str(chat.get("username") or "").strip()
    name = " ".join(part for part in (first, last) if part) or username or chat_id
    if username and username not in name:
        name = f"{name} (@{username})"
    return ChannelEntry(platform="telegram", chat_id=chat_id, name=name, type="dm")


def chat_entry_from_update(update: Any) -> ChannelEntry | None:
    """One getUpdates update → its effective chat as :class:`ChannelEntry`.

    消息类载荷(``message``/``edited_message``/``channel_post``/
    ``edited_channel_post``)与成员事件(``my_chat_member``/``chat_member``)
    的 chat 在 ``.chat``;``callback_query`` 在 ``.message.chat``。一条
    update 只有一种载荷,首个命中即取;无 chat / 载荷坏 → None。
    """
    if not isinstance(update, Mapping):
        return None
    for key in _CHAT_BEARER_KEYS:
        payload = update.get(key)
        if isinstance(payload, Mapping):
            return entry_from_chat(payload.get("chat"))
    query = update.get(_CALLBACK_QUERY_KEY)
    if isinstance(query, Mapping):
        message = query.get("message")
        if isinstance(message, Mapping):
            return entry_from_chat(message.get("chat"))
    return None


@dataclass(frozen=True)
class TelegramCallback:
    """One parsed feedback callback (ingestion input, 见 shishi.feedback)."""

    update_id: int
    verdict: str
    dedup_key: str
    channel: str = "telegram"

    @property
    def external_id(self) -> str:
        """幂等身份:同一 update 双击/重试/重启重放只入库一次(见 shishi.feedback)."""
        return str(self.update_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id,
            "channel": self.channel,
            "verdict": self.verdict,
            "dedup_key": self.dedup_key,
            "external_id": self.external_id,
        }


@dataclass
class PollResult:
    """One ``getUpdates`` round: parsed callbacks + the offset bookmark."""

    callbacks: list[TelegramCallback] = field(default_factory=list)
    update_count: int = 0
    skipped: int = 0
    next_offset: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "callbacks": [callback.to_dict() for callback in self.callbacks],
            "update_count": self.update_count,
            "skipped": self.skipped,
            "next_offset": self.next_offset,
        }


class TelegramFeedbackPoller:
    """Polls Bot API ``getUpdates`` for feedback callbacks (桌面形态).

    Args:
        token: pre-resolved bot token (constructor injection for tests);
            resolved from ``token_ref`` when omitted — resolution happens
            here (fail fast), so an unusable credential disables the poller
            at construction, never mid-run.
        token_ref: bot-token credential reference; defaults to the same
            ``env:TELEGRAM_BOT_TOKEN`` the send channel uses.
        on_chat: 被动目录 sink(10-03-messaging-telegram D2,可选):每条
            update 的 effective chat 归一为 :class:`ChannelEntry` 后回调;
            缺省 None = 行为与不带 sink 完全一致。sink 抛错只记日志,
            绝不中断轮询(旁路观察者契约)。
        client: injectable ``httpx.AsyncClient`` (tests mock here); when
            omitted a per-poll client is created with ``timeout``.
        timeout: per-request timeout for the self-managed client.
        poll_timeout: Bot API long-poll seconds (0 = short poll; the client
            timeout grows accordingly so a long poll is never cut locally).

    Raises:
        TelegramFeedbackError: the bot-token reference cannot be resolved
            (structured, reference name only — never the value).
    """

    name = "telegram_feedback"

    def __init__(
        self,
        *,
        token: str | None = None,
        token_ref: str | None = None,
        on_chat: Callable[[ChannelEntry], None] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        poll_timeout: int = 0,
    ) -> None:
        self._token = token
        self._token_ref = token_ref or DEFAULT_TOKEN_ENV_REF
        self._on_chat = on_chat
        self._client = client
        self._timeout = timeout
        self._poll_timeout = max(0, int(poll_timeout))
        if self._token is None:
            self._token = self._resolve_token()

    def _resolve_token(self) -> str:
        try:
            return resolve_credential(self._token_ref)
        except CredentialResolveError as exc:
            raise TelegramFeedbackError(exc.code, f"telegram bot 凭据解析失败: {exc}") from exc

    async def poll(self, *, offset: int | None = None) -> PollResult:
        """Run one ``getUpdates`` round; return parsed feedback callbacks.

        Args:
            offset: the ``next_offset`` bookmark from the previous round
                (Telegram re-delivers unconfirmed updates without it).

        Returns:
            :class:`PollResult` — callbacks in arrival order plus the next
            bookmark (``max(update_id) + 1``; None when the round saw no
            updates).注入了 ``on_chat`` 时,本轮每个 update 的 effective
            chat 另经 sink 回调(旁路;不影响本返回值,sink 失败只记日志)。

        Raises:
            TelegramFeedbackError: transport failure, non-JSON response, or
                the API answered ``ok != true`` (structured codes; callers
                log-and-continue — 轮询失败不拖垮常驻调度).
        """
        params: dict[str, Any] = {"timeout": self._poll_timeout}
        if offset is not None:
            params["offset"] = offset
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        try:
            if self._client is not None:
                response = await self._client.get(url, params=params)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout + self._poll_timeout + 1.0
                ) as client:
                    response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise TelegramFeedbackError(
                "http_error", f"telegram getUpdates 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        result = self._parse_response(response)
        self._observe_chats(response)
        return result

    def _observe_chats(self, response: httpx.Response) -> None:
        """被动目录积累(设计 D2):sink 旁路,绝不影响轮询本体。

        响应体已由 :meth:`_parse_response` 校验(ok=true,非 JSON/坏包在
        那里已抛);这里重读同一份 JSON(httpx 缓存响应内容,无二次网络
        I/O)提取每条 update 的 effective chat,归一后交 ``on_chat``。
        任何异常——含 sink 抛错——只记日志(轮询契约零改动)。
        """
        if self._on_chat is None:
            return
        try:
            data = response.json()
        except ValueError:
            return  # 理论不可达(_parse_response 已校验);防御性静默
        updates = data.get("result") if isinstance(data, Mapping) else None
        if not isinstance(updates, list):
            return
        for update in updates:
            entry = chat_entry_from_update(update)
            if entry is None:
                continue
            try:
                self._on_chat(entry)
            except Exception as exc:  # noqa: BLE001 - sink 抛错不中断轮询(设计 D2)
                logger.warning(
                    "TG 目录 sink 回调失败(轮询继续): chat_id=%s error=%s",
                    entry.chat_id,
                    exc,
                )

    @staticmethod
    def _parse_response(response: httpx.Response) -> PollResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramFeedbackError(
                "invalid_response",
                f"telegram 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, Mapping) or data.get("ok") is not True:
            error_code = data.get("error_code") if isinstance(data, Mapping) else None
            description = (
                data.get("description") if isinstance(data, Mapping) else response.text[:200]
            )
            raise TelegramFeedbackError(
                "telegram_api_error",
                f"telegram API 返回错误: error_code={error_code} description={description}",
            )
        result = PollResult()
        updates = data.get("result")
        if not isinstance(updates, list):
            return result
        max_update_id: int | None = None
        for update in updates:
            if not isinstance(update, Mapping):
                result.skipped += 1
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, bool) or not isinstance(update_id, int):
                result.skipped += 1
                continue
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)
            result.update_count += 1
            query = update.get("callback_query")
            if not isinstance(query, Mapping):
                continue  # 普通消息等更新:非反馈回调,计入但不跳过(offset 仍前进)
            parsed = parse_callback_data(query.get("data"))
            if parsed is None:
                result.skipped += 1
                logger.debug("跳过非反馈 callback_query update_id=%s", update_id)
                continue
            verdict, dedup_key = parsed
            result.callbacks.append(
                TelegramCallback(update_id=update_id, verdict=verdict, dedup_key=dedup_key)
            )
        if max_update_id is not None:
            result.next_offset = max_update_id + 1
        logger.debug(
            "getUpdates 完成 updates=%s callbacks=%s skipped=%s next_offset=%s",
            result.update_count, len(result.callbacks), result.skipped, result.next_offset,
        )
        return result
