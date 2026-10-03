"""Generic webhook channel: POST the rendered payload as JSON to any endpoint.

The plugin-ecosystem integration point (myia-monitor / n8n / 自建接收端):
the payload is the render data (kind/slot/date/category/count, plus the user
template's ``text`` when configured) plus per-item metadata via
:func:`shishi.push.base.item_view` — the same shape the ``stdout`` channel
prints, so consumers treat both as one contract.

The endpoint URL goes through a **credential reference** (``env:`` /
``keychain:``, security baseline: 端点零明文) resolved at send time; the
resolved URL never appears in logs or error messages — reference name only.

Transient failures (429 / 500 / 502 / 503 / 504 / transport errors) retry
with exponential backoff (``retries`` / ``retry_backoff_seconds``
configurable, sleeper injectable so tests never really wait); other 4xx are
permanent and fail on the first attempt. All HTTP I/O goes through an
injectable ``httpx.AsyncClient`` — tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Sequence

import httpx

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
    item_view,
)
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_ENDPOINT_ENV_REF",
    "DEFAULT_RETRIES",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "RETRYABLE_STATUS_CODES",
    "WebhookChannel",
    "build_payload",
]

logger = logging.getLogger(__name__)

#: Endpoint credential reference used when the constructor gets no ``target``.
DEFAULT_ENDPOINT_ENV_REF = "env:MYIA_WEBHOOK_URL"
#: Retried with backoff (transient); other 4xx are permanent, single attempt.
#: Same policy as the fetch layer (shishi.engines.fetch_base), kept local so the
#: push layer does not import the engine layer.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0


def build_payload(
    items: Sequence[Any], context: SendContext, *, text: str | None = None
) -> dict[str, Any]:
    """Webhook JSON payload: render data + per-item metadata (stdout 同构)."""
    payload: dict[str, Any] = {
        "channel": "webhook",
        "kind": context.kind,
        "slot": context.slot,
        "date": context.date,
        "category": context.category,
        "count": len(items),
        "items": [item_view(item) for item in items],
    }
    if text is not None:
        payload["text"] = text
    return payload


class WebhookChannel(TrendAwareChannel):
    """``webhook`` channel: one JSON POST per send, transient failures retried.

    Args:
        target: endpoint credential reference (``env:MY_WEBHOOK_URL`` style,
            resolved at send time); omitted → :data:`DEFAULT_ENDPOINT_ENV_REF`.
            A plaintext URL is refused (``invalid_credential_ref``).
        template: optional user template (Jinja2); its rendered output lands
            in the payload ``text`` field next to the structured data.
        renderer: template renderer; defaults to a shared sandboxed one.
        client: injectable ``httpx.AsyncClient`` (tests mock here); when
            omitted a per-send client is created with ``timeout``.
        timeout: per-send timeout in seconds for the self-managed client.
        retries: extra attempts after the first on transient failures
            (429/5xx/transport); permanent 4xx never retry.
        retry_backoff_seconds: exponential-backoff base delay in seconds
            (delay before retry ``n`` is ``base * 2**n``, capped at
            :data:`BACKOFF_CAP_SECONDS`).
        sleep: awaitable backoff sleeper (constructor injection for tests);
            defaults to :func:`asyncio.sleep`.

    Raises:
        ValueError: ``retries`` or ``retry_backoff_seconds`` negative
            (fail fast on programmer error).
        PushSendError: credential resolution failed, transport error after
            the retry budget was exhausted, a permanent non-2xx response, or
            template rendering failed at send time.
    """

    name = "webhook"
    #: webhook 永不支持目录寻址(schema 配 targets 即拒);显式声明保住
    #: isinstance(Channel) 判定。
    supports_targeting = False

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retries < 0:
            raise ValueError(f"字段校验失败: webhook retries 不能为负,得到 {retries}")
        if retry_backoff_seconds < 0:
            raise ValueError(
                f"字段校验失败: webhook retry_backoff_seconds 不能为负,得到 {retry_backoff_seconds}"
            )
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Build the JSON payload and POST it (with retries) to the endpoint.

        Raises:
            PushSendError: on any credential/transport/endpoint failure
                (callers isolate per channel; nothing is raised on success).
        """
        endpoint = self._resolve_endpoint()
        payload = self._build_payload(items, context)
        await self._post(endpoint, payload)
        # 日志只带凭据引用名,不带解析后的端点 URL(值可能内嵌 token)。
        logger.debug(
            "webhook 已提交: target=%s slot=%s kind=%s count=%d",
            self._target or DEFAULT_ENDPOINT_ENV_REF,
            context.slot,
            context.kind,
            len(items),
        )

    def _build_payload(self, items: Sequence[Any], context: SendContext) -> dict[str, Any]:
        if self._template is None:
            return build_payload(items, context)
        # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
        # template_render_error(语法错误已在加载期被 schema 拒绝)。
        try:
            text = self._renderer.render(self._template, items, context, **self.trend_render_kwargs())
        except TemplateRenderError as exc:
            raise PushSendError(
                "template_render_error", f"push[].template 渲染失败: {exc}"
            ) from exc
        return build_payload(items, context, text=text)

    def _resolve_endpoint(self) -> str:
        reference = self._target or DEFAULT_ENDPOINT_ENV_REF
        try:
            resolved = resolve_credential(reference)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"webhook 端点凭据解析失败: {exc}") from exc
        endpoint = resolved.strip()
        if not endpoint:
            raise PushSendError(
                "invalid_credential_ref",
                f"webhook 端点解析结果为空: 引用 {reference!r} 未指向有效 URL",
            )
        return endpoint

    def _backoff_delay(self, attempt: int) -> float:
        return min(BACKOFF_CAP_SECONDS, self._retry_backoff_seconds * 2**attempt)

    async def _sleep_before_retry(self, attempt: int, reason: str) -> None:
        delay = self._backoff_delay(attempt)
        logger.warning(
            "webhook 发送失败,%.1fs 后重试 target=%s reason=%s attempt=%s/%s",
            delay,
            self._target or DEFAULT_ENDPOINT_ENV_REF,
            reason,
            attempt + 1,
            self._retries,
        )
        await self._sleep(delay)

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> None:
        if self._client is not None:
            await self._post_with_retries(self._client, endpoint, payload)
            return
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            await self._post_with_retries(client, endpoint, payload)

    async def _post_with_retries(
        self, client: httpx.AsyncClient, endpoint: str, payload: dict[str, Any]
    ) -> None:
        for attempt in range(self._retries + 1):
            try:
                response = await client.post(endpoint, json=payload)
            except httpx.HTTPError as exc:
                if attempt < self._retries:
                    await self._sleep_before_retry(attempt, f"{type(exc).__name__}")
                    continue
                raise PushSendError(
                    "http_error",
                    f"webhook 请求失败(已重试 {self._retries} 次): {type(exc).__name__}: {exc}",
                ) from exc
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._retries:
                await self._sleep_before_retry(attempt, f"HTTP {response.status_code}")
                continue
            if not response.is_success:
                # 4xx 永久性错误立即抛出;429/5xx 走到这里说明重试预算已耗尽。
                raise PushSendError(
                    "webhook_api_error",
                    f"webhook 端点返回错误: HTTP {response.status_code}: {response.text[:200]!r}",
                )
            return
        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover
