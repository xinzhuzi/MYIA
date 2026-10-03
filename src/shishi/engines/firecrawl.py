"""Firecrawl engine for heavily-rendered sources (cloud or self-hosted).

Contract (PRD 10-01-v01-engine-firecrawl):

- ``POST {endpoint}/v1/scrape`` per target URL; the endpoint defaults to the
  self-host address ``http://127.0.0.1:3002`` and the cloud API is a
  drop-in via ``MYIA_FIRECRAWL_URL`` / ``MYIA_FIRECRAWL_API_KEY``;
- endpoint and API key resolve at *call* time (never import time) and, when
  configured in YAML (``engine_options.firecrawl``), must be ``env:`` /
  ``keychain:`` references — plaintext is refused (安全基线: 配置零明文凭据);
- returns markdown / html; the ``extract`` section parses the HTML (v0.1
  supported path), no-extract sources surface the markdown payload;
- politeness primitives (robots / rate limit) apply to the *target* site,
  while retries and structured failures cover our own backend — a dead
  self-hosted service degrades cleanly instead of hanging the pipeline.

Raises:
    FetchError: plaintext endpoint/api_key, bad options, non-JSON payload,
        ``success: false`` scrape result, missing ``data`` section, HTTP >= 400
        (``http_<status>``;消息只记端点展示形态,解析值不落日志).
    CredentialResolveError: keychain reference (NotSupported until v0.2) or
        missing env var.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from shishi.engines.fetch_base import (
    BaseEngine,
    ExtractionError,
    FetchError,
    extract_html,
    mask_endpoint_url,
)
from shishi.schema import CredentialResolveError, resolve_credential

logger = logging.getLogger(__name__)

LAYER = "L3-alt"

ENV_FIRECRAWL_URL = "MYIA_FIRECRAWL_URL"
ENV_FIRECRAWL_API_KEY = "MYIA_FIRECRAWL_API_KEY"
DEFAULT_FIRECRAWL_ENDPOINT = "http://127.0.0.1:3002"
DEFAULT_FIRECRAWL_TIMEOUT_SECONDS = 60.0

__all__ = [
    "LAYER",
    "DEFAULT_FIRECRAWL_ENDPOINT",
    "DEFAULT_FIRECRAWL_TIMEOUT_SECONDS",
    "ENV_FIRECRAWL_API_KEY",
    "ENV_FIRECRAWL_URL",
    "FirecrawlEngine",
]


class FirecrawlEngine(BaseEngine):
    """Scrape rendered pages through a Firecrawl backend (v1 HTTP API)."""

    LAYER = "L3-alt"
    ENGINE_NAME = "firecrawl"
    REQUIRES_EXTRACT = False
    SUPPORTED_EXTRACT_TYPES = ("list", "item")

    # ------------------------------------------------------- configuration

    def _endpoint(self) -> tuple[str, str]:
        """Resolve endpoint -> ``(concrete value, log-safe display form)``.

        展示形态记来源 —— options 引用原样(``env:``/``keychain:`` 名)、env
        回退记 ``env:MYIA_FIRECRAWL_URL``、无配置记内置缺省 —— 解析值不落
        日志(安全基线「引用名可以,展开后的值禁止」;llm_browser 同款)。
        """
        configured = self.engine_options().get("endpoint")
        if configured is not None:
            if not isinstance(configured, str):
                raise FetchError(
                    f"engine_options.firecrawl.endpoint 应为字符串,当前为 {type(configured).__name__}",
                    error_type="invalid_endpoint",
                )
            return self._resolve_ref(configured, "engine_options.firecrawl.endpoint"), configured
        from_env = os.environ.get(ENV_FIRECRAWL_URL, "").strip()
        if from_env:
            return from_env, f"env:{ENV_FIRECRAWL_URL}"
        return DEFAULT_FIRECRAWL_ENDPOINT, f"默认({DEFAULT_FIRECRAWL_ENDPOINT})"

    def _resolve_api_key(self) -> str | None:
        configured = self.engine_options().get("api_key")
        if configured is not None:
            if not isinstance(configured, str):
                raise FetchError(
                    f"engine_options.firecrawl.api_key 应为字符串引用,当前为 {type(configured).__name__}",
                    error_type="invalid_endpoint",
                )
            return self._resolve_ref(configured, "engine_options.firecrawl.api_key")
        return os.environ.get(ENV_FIRECRAWL_API_KEY, "").strip() or None

    def _resolve_ref(self, value: str, label: str) -> str:
        """Resolve an ``env:``/``keychain:`` reference; plaintext is refused."""
        try:
            return resolve_credential(value)
        except CredentialResolveError as exc:
            if exc.code == "invalid_credential_ref":
                raise FetchError(
                    f"{label} 必须是 env:/keychain: 凭据引用,禁明文(安全基线)",
                    error_type="credentials_plaintext",
                ) from exc
            raise

    def _formats(self) -> list[str]:
        """Scrape formats; ``html`` is added automatically when extract needs it."""
        formats = self.engine_options().get("formats")
        if formats is None:
            return ["markdown", "html"] if self.source.extract else ["markdown"]
        if not isinstance(formats, list) or not all(isinstance(item, str) for item in formats):
            raise FetchError(
                f"engine_options.firecrawl.formats 应为字符串列表,当前为 {formats!r}",
                error_type="invalid_formats",
            )
        if self.source.extract and "html" not in formats:
            formats = [*formats, "html"]
        return formats

    def _timeout(self) -> float:
        value = self.engine_options().get("timeout")
        if value is None:
            return DEFAULT_FIRECRAWL_TIMEOUT_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.firecrawl.timeout 应为正数秒,当前为 {value!r}",
                error_type="invalid_timeout",
            )
        return float(value)

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        endpoint, endpoint_display = self._endpoint()
        api_key = self._resolve_api_key()
        formats = self._formats()
        timeout = self._timeout()
        logger.info(
            "firecrawl 后端就绪 endpoint=%s formats=%s timeout=%ss api_key=%s",
            # 展示形态 = 凭据引用名 / 内置缺省;解析值不落日志(fetch_base mask 先例)。
            endpoint_display, formats, timeout, "已配置" if api_key else "未配置",
        )
        items: list[dict] = []
        for target_url in self._template_urls():
            # 礼貌约束作用于目标站点(由 firecrawl 代抓),而非我们自己的后端。
            await self._ensure_robots_allowed(target_url)
            await self._acquire_rate_limit(target_url)
            data = await self._scrape(
                endpoint, endpoint_display, api_key, target_url, formats, timeout
            )
            items.extend(self._extract_target(data, target_url))
        return items

    def _http_status_failure(self, exc: httpx.HTTPStatusError, endpoint_display: str) -> FetchError:
        """HTTP >= 400 -> 结构化 FetchError:错误类 http_<status> 不变,但消息
        只记端点展示形态 —— httpx 异常的 str 内嵌完整解析 URL,直接透传会把
        后端端点带进 failures[] 与日志(解析值不落日志契约)。"""
        status = exc.response.status_code
        return FetchError(
            f"firecrawl 后端 HTTP {status} endpoint={endpoint_display}: {exc.response.reason_phrase}",
            error_type=f"http_{status}",
        )

    async def _scrape(
        self,
        endpoint: str,
        endpoint_display: str,
        api_key: str | None,
        target_url: str,
        formats: list[str],
        timeout: float,
    ) -> dict[str, Any]:
        url = endpoint.rstrip("/") + "/v1/scrape"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {"url": target_url, "formats": formats, "timeout": int(timeout * 1000)}
        # HTTP 客户端超时取 max(管线默认, 后端预算):客户端不得早于后端预算掐断,
        # 否则配置的 engine_options.firecrawl.timeout 超过默认 30s 的部分静默失效。
        try:
            response = await self._send_with_retry(
                "POST",
                url,
                json_body=body,
                headers=headers,
                timeout=max(timeout, self.context.timeout),
                # 重试 WARNING 记掩码形态:解析端点不落日志。
                log_url=mask_endpoint_url(url),
            )
        except httpx.HTTPStatusError as exc:
            raise self._http_status_failure(exc, endpoint_display) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise FetchError(
                f"firecrawl 响应不是有效 JSON url={target_url}: {exc}",
                error_type="json_decode",
            ) from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            raise FetchError(
                f"firecrawl 抓取失败 url={target_url}: {payload.get('error', '未知错误')}",
                error_type="firecrawl_error",
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise FetchError(
                f"firecrawl 响应缺少 data 节 url={target_url}",
                error_type="firecrawl_error",
            )
        return data

    def _extract_target(self, data: dict[str, Any], target_url: str) -> list[dict]:
        if self.source.extract is not None:
            html = data.get("html")
            if not html or not isinstance(html, str):
                raise ExtractionError(
                    f"firecrawl 响应缺少 html 格式,无法执行 extract 选择器 url={target_url}"
                )
            return extract_html(html, self.source.extract, base_url=target_url)
        markdown = data.get("markdown")
        metadata = data.get("metadata")
        return [
            {
                "url": target_url,
                "title": metadata.get("title") or "" if isinstance(metadata, dict) else "",
                "content": markdown if isinstance(markdown, str) else "",
            }
        ]
