"""Telegram ``getUpdates`` feedback polling (桌面形态接收, grill Q7 定案).

The desktop form has no public endpoint for card callbacks, so the receiving
side polls the Bot API instead: every ``callback_query`` update whose
``data`` carries our feedback contract is turned into a callback record for
:func:`myia.feedback.ingest_callbacks`. Long-polling (``getUpdates``
``timeout``) keeps this cheap; the pipeline's resident mode runs the loop in
the background whenever a ``telegram`` push channel is configured.

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
from typing import Any, Mapping

import httpx

from myia.push.base import DEFAULT_SEND_TIMEOUT_SECONDS
from myia.push.telegram import DEFAULT_TOKEN_ENV_REF
from myia.schema import CredentialResolveError, resolve_credential

__all__ = [
    "CALLBACK_PREFIX",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "PollResult",
    "TelegramCallback",
    "TelegramFeedbackError",
    "TelegramFeedbackPoller",
    "parse_callback_data",
]

logger = logging.getLogger(__name__)

#: Feedback callback-data prefix: ``fb:<good|bad>:<dedup_key>``.
CALLBACK_PREFIX = "fb:"
#: Legal verdicts inside callback data (normalized again at ingestion).
_CALLBACK_VERDICTS = ("good", "bad")
#: Background-loop sleep between getUpdates calls (pipeline resident mode).
DEFAULT_POLL_INTERVAL_SECONDS = 30.0


class TelegramFeedbackError(RuntimeError):
    """One feedback-poll failed (structured code; never a bare string).

    Attributes:
        code: ``http_error`` / ``invalid_response`` / ``telegram_api_error``
            / credential codes re-exported from
            :class:`myia.schema.CredentialResolveError`.
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


@dataclass(frozen=True)
class TelegramCallback:
    """One parsed feedback callback (ingestion input, 见 myia.feedback)."""

    update_id: int
    verdict: str
    dedup_key: str
    channel: str = "telegram"

    @property
    def external_id(self) -> str:
        """幂等身份:同一 update 双击/重试/重启重放只入库一次(见 myia.feedback)."""
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
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        poll_timeout: int = 0,
    ) -> None:
        self._token = token
        self._token_ref = token_ref or DEFAULT_TOKEN_ENV_REF
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
            updates).

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
        return self._parse_response(response)

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
