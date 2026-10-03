"""L2 engine: server-rendered static HTML via httpx + selectolax.

Contract (PRD 10-01-v01-engine-l1-l2):

- ``extract.type: list`` (item selector + per-field CSS selectors, ``@href`` /
  ``@src`` attribute values, relative URLs resolved against the page URL) and
  ``item`` (single-page, document-level selectors); ``json_path`` is rejected
  here so ``engine: auto`` degrades correctly. ``rss`` (10-03-news-rss) rides
  the same text path: the body goes to :func:`fetch_base.extract_rss`
  (feedparser) instead of CSS selectors — RSS 的 ``<link>`` void 元素拿不到
  条目 url,硬接 CSS = 静默零产出;``direct_api`` 保持 JSON-only(rss 配
  direct_api 在引擎层拒);
- non-UTF8 responses (gb18030/big5 legacy forums — 生产源痛点) are decoded by
  ``fetch_base.decode_response``: BOM > Content-Type charset > meta charset >
  utf-8/gb18030/big5 fallbacks;
- pagination walks ``template`` (``{page}`` in the URL, ``max_pages`` cap) and
  ``selector`` (``a.next@href`` next-link following, loop-guarded) modes;
  ``scroll`` is L4+ and never reached by this engine;
- per-URL change fingerprints skip pages whose content did not change.

Raises:
    FetchError: extract config missing/unsupported, template param missing.
    ExtractionError: malformed extract selector configuration.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from shishi.engines.fetch_base import (
    BaseEngine,
    decode_response,
    extract_html,
    extract_rss,
    split_attr_selector,
)

logger = logging.getLogger(__name__)

LAYER = "L2"

__all__ = ["LAYER", "StaticHTMLEngine"]


class StaticHTMLEngine(BaseEngine):
    """Fetch static pages and apply the CSS ``extract`` selectors."""

    LAYER = "L2"
    ENGINE_NAME = "static_html"
    SUPPORTED_EXTRACT_TYPES = ("list", "item", "rss")

    async def _fetch_impl(self) -> list[dict]:
        items: list[dict] = []
        pagination = self.source.pagination
        mode = pagination.mode if pagination else "template"
        if mode == "selector":
            await self._walk_selector(items)
        else:
            await self._walk_template(items)
        return items

    async def _walk_template(self, items: list[dict]) -> None:
        """``?page={n}`` walks capped by ``max_pages``; stops on unchanged/empty."""
        for url in self._template_urls():
            page_items, _, stop = await self._fetch_page(url)
            items.extend(page_items)
            if stop:
                break

    async def _walk_selector(self, items: list[dict]) -> None:
        """Follow the next-page link, ``max_pages``-capped and loop-guarded."""
        pagination = self.source.pagination
        assert pagination is not None and pagination.selector  # schema-enforced
        url = self.source.url
        visited = {url}
        for _ in range(pagination.max_pages):
            page_items, next_url, stop = await self._fetch_page(url)
            items.extend(page_items)
            if stop or not next_url:
                break
            if next_url in visited:
                logger.warning("翻页检测到环路,提前收尾 url=%s", next_url)
                break
            visited.add(next_url)
            url = next_url

    async def _fetch_page(self, url: str) -> tuple[list[dict], Optional[str], bool]:
        """Fetch+extract one page; returns (items, next_url, stop_walk)."""
        response = await self.request(url)
        if response.status_code == 304:
            self.last_skip_reason = "not_modified"
            logger.info("变更指纹协商命中(304),跳过 url=%s", url)
            return [], None, True
        text = decode_response(response)
        verdict = self.check_change(url, response, decoded_text=text)
        if not verdict.changed:
            self.last_skip_reason = verdict.reason
            logger.info("变更指纹未变(%s),跳过 url=%s", verdict.reason, url)
            return [], None, True
        # 提取出口分流(10-03-news-rss):rss 走 feedparser 条目映射(fields
        # 值=entry 属性白名单),其余类型走 CSS 选择器。_check_extract_support
        # 已保证 extract 存在且 type ∈ SUPPORTED_EXTRACT_TYPES。
        extract = self.source.extract
        assert extract is not None
        if extract.type == "rss":
            page_items = extract_rss(text, extract)
        else:
            page_items = extract_html(text, extract, base_url=url)
        if not page_items:
            logger.debug("本页提取 0 条,提前收尾 url=%s", url)
            return [], None, True
        next_url = self._next_page_url(text, url)
        return page_items, next_url, False

    def _next_page_url(self, html: str, base_url: str) -> Optional[str]:
        """Resolve the ``pagination.selector`` next-link (absolute URL), or None."""
        pagination = self.source.pagination
        if pagination is None or not pagination.selector:
            return None
        css, attr = split_attr_selector(pagination.selector)
        if not css:
            return None
        nodes = HTMLParser(html).css(css)
        if not nodes:
            return None
        node = nodes[0]
        value = node.attributes.get(attr) if attr else node.text(separator=" ", strip=True)
        if not value:
            return None
        return urljoin(base_url, value)
