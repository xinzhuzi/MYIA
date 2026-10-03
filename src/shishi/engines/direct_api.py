"""L1 engine: direct JSON/REST API calls via httpx (fastest, zero cost).

Contract (PRD 10-01-v01-engine-l1-l2):

- URL templates expand ``{symbol}``-style placeholders from source-level extra
  params plus ``{page}`` from template pagination;
- ``extract.type: json_path`` picks fields (shared implementation lives in
  fetch_base); ``list``/``item`` CSS selectors are rejected here so
  ``engine: auto`` degrades to L2 instead of misbehaving;
- an optional key pool (``engine_options.direct_api.api_keys``, each an
  ``env:``/``keychain:`` reference) rotates round-robin across requests and
  swaps keys on 401/403/429;
- per-URL change fingerprints (ETag / Last-Modified / body hash) skip URLs
  whose content did not change since the last run.

Raises:
    FetchError: extract config missing/unsupported, missing template param,
        invalid JSON payload, key-pool misconfiguration.
    CredentialResolveError: an ``api_keys`` reference cannot be resolved.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from shishi.engines.fetch_base import (
    KEY_ROTATE_STATUS_CODES,
    BaseEngine,
    FetchContext,
    FetchError,
    extract_json,
)
from shishi.schema import SourceConfig, resolve_credential

logger = logging.getLogger(__name__)

LAYER = "L1"

__all__ = ["LAYER", "DirectAPIEngine"]


class DirectAPIEngine(BaseEngine):
    """Fetch public JSON APIs: URL templates, key-pool rotation, json_path extract."""

    LAYER = "L1"
    ENGINE_NAME = "direct_api"
    SUPPORTED_EXTRACT_TYPES = ("json_path",)

    def __init__(self, source: SourceConfig, context: FetchContext) -> None:
        super().__init__(source, context)
        options = self.engine_options()
        self._api_key_header = str(options.get("api_key_header", "Authorization"))
        self._api_keys = self._resolve_key_pool(options.get("api_keys"))
        self._key_cursor = 0

    def _resolve_key_pool(self, refs: Any) -> list[str]:
        """Resolve ``api_keys`` credential references at construction time."""
        if refs is None:
            return []
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise FetchError(
                "engine_options.direct_api.api_keys 应为凭据引用字符串列表(如 [\"Bearer env:KEY_A\"])",
                error_type="invalid_api_keys",
            )
        return [resolve_credential(ref) for ref in refs]

    async def _fetch_impl(self) -> list[dict]:
        items: list[dict] = []
        for walk in self._template_walks():
            for url in walk:
                if await self._fetch_url(url, items):
                    # 提前收尾只作用于「本轮 walk」(同一 fan-out 值的翻页):
                    # 其余 walk(其余 symbol 等)相互独立,照常抓取——
                    # 一个 symbol 的 304/指纹未变/0 条提取不得截断其余 symbol。
                    break
        return items

    async def _fetch_url(self, url: str, items: list[dict]) -> bool:
        """Fetch+extract one URL; returns True when *this walk* should stop."""
        response = await self._request_rotating(url)
        if response.status_code == 304:
            self.last_skip_reason = "not_modified"
            logger.info("变更指纹协商命中(304),跳过 url=%s", url)
            return True
        text = response.text
        verdict = self.check_change(url, response, decoded_text=text)
        if not verdict.changed:
            self.last_skip_reason = verdict.reason
            logger.info("变更指纹未变(%s),跳过 url=%s", verdict.reason, url)
            return True
        data = self._decode_json(response, url)
        extracted = extract_json(data, self.source.extract)  # type: ignore[arg-type]
        items.extend(extracted)
        if not extracted:
            logger.debug("json_path 提取 0 条,提前收尾本轮 walk url=%s", url)
            return True
        return False

    def _decode_json(self, response: httpx.Response, url: str) -> Any:
        text = response.text
        if not text.strip():
            logger.debug("响应体为空,按无数据处理 url=%s", url)
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"响应不是有效 JSON url={url}: {exc}", error_type="json_decode") from exc

    async def _request_rotating(self, url: str) -> httpx.Response:
        """Send one request, rotating the key pool on 401/403/429 rejections."""
        if not self._api_keys:
            return await self.request(url)
        for offset in range(len(self._api_keys)):
            index = (self._key_cursor + offset) % len(self._api_keys)
            try:
                response = await self.request(
                    url, headers={self._api_key_header: self._api_keys[index]}
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in KEY_ROTATE_STATUS_CODES and offset + 1 < len(self._api_keys):
                    logger.warning(
                        "API key 池轮换:第 %s/%s 把 key 被拒(HTTP %s),换下一把 url=%s",
                        index + 1, len(self._api_keys), status, url,
                    )
                    continue
                raise
            self._key_cursor = (index + 1) % len(self._api_keys)
            return response
        raise AssertionError("unreachable: key rotation loop must return or raise")  # pragma: no cover
