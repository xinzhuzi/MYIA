"""L3 engine: JS-rendered crawling via crawl4ai (default heavy engine).

Contract (PRD 10-01-v02-engine-crawl4ai):

- **optional dependency**: crawl4ai is imported lazily at *fetch* time (never
  at module import), so the core pipeline stays zero-heavy-dependency; when
  the package is absent the engine raises a structured :class:`FetchError`
  (``error_type=dependency_missing``) whose message carries the extras
  install command (``pip install myia[crawl4ai]``) and the browser-binary
  note (crawl4ai manages its own playwright browsers — first run may need
  ``crawl4ai-setup``), which ``myia doctor`` surfaces verbatim;
- with ``extract`` (CSS ``list``/``item``) the rendered HTML is parsed by the
  shared ``extract_html`` (same selectors as L2); ``json_path`` is rejected
  here so ``engine: auto`` degrades to the next engine instead of
  misbehaving (same rule as L2);
- without ``extract`` the engine falls back to crawl4ai's own
  auto-structuring capability: one ``{url, title, content}`` record per
  target URL built from the rendered markdown (yaml-schema rule 7:
  无 extract 时 L3 自动结构化兜底), same payload shape as firecrawl's
  no-extract path;
- politeness primitives (robots / rate limit) apply to the *target* site —
  crawl4ai drives its own browser on our behalf, mirroring the firecrawl
  split (target politeness vs. backend structured failures); every ``arun``
  is wrapped in ``asyncio.wait_for`` so a hung page cannot stall the run.

Raises:
    FetchError: crawl4ai not installed / broken install
        (``dependency_missing``), bad ``engine_options.crawl4ai``
        (``invalid_timeout``/``invalid_headless``), browser startup or run
        failure (``crawl4ai_error`` — 启动失败含 crawl4ai-setup 提示),
        run budget exhausted (``timeout``).
    ExtractionError: extract configured but the result carries no HTML.
    RobotsDisallowedError: robots.txt forbids the target URL.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

from myia.engines.fetch_base import (
    BaseEngine,
    ExtractionError,
    FetchError,
    extract_html,
    mask_proxy_url,
)

logger = logging.getLogger(__name__)

LAYER = "L3"

#: Install command surfaced verbatim in the dependency-missing error
#: (验收标准: 未安装依赖时错误信息含 ``pip install myia[crawl4ai]``).
INSTALL_COMMAND = "pip install myia[crawl4ai]"

#: Per-page run budget (seconds) when ``engine_options.crawl4ai.timeout`` is
#: absent — JS 渲染页比静态页慢,默认预算宽于管线 HTTP 默认 30s。
DEFAULT_PAGE_TIMEOUT_SECONDS = 60.0

__all__ = [
    "LAYER",
    "DEFAULT_PAGE_TIMEOUT_SECONDS",
    "INSTALL_COMMAND",
    "Crawl4AIEngine",
]


def load_crawl4ai() -> Any:
    """Return the optional ``crawl4ai`` module, imported lazily at call time.

    Lazy so that ``myia.engines.crawl4ai`` (and the whole engine registry)
    imports cleanly without the optional package, and so tests can inject a
    fake module via ``sys.modules``.

    Raises:
        FetchError: crawl4ai is not installed — structured
            ``dependency_missing`` carrying :data:`INSTALL_COMMAND` plus the
            playwright browser-binary hint (doctor 消费原文).
    """
    try:
        return importlib.import_module("crawl4ai")
    except ImportError as exc:
        raise FetchError(
            f"crawl4ai 引擎依赖未安装:请先执行 {INSTALL_COMMAND}"
            "(核心流水线零重依赖,crawl4ai 为可选 extras;"
            "安装后首次运行可能还需 crawl4ai-setup 安装 playwright 浏览器二进制)",
            error_type="dependency_missing",
        ) from exc
    except Exception as exc:  # 安装损坏(导入期抛非 ImportError)同样结构化,不裸逃
        raise FetchError(
            f"crawl4ai 包导入失败(安装可能损坏): {exc};可尝试重装({INSTALL_COMMAND})",
            error_type="dependency_missing",
        ) from exc


class Crawl4AIEngine(BaseEngine):
    """Render JS-heavy pages with crawl4ai into clean text/structured items."""

    LAYER = "L3"
    ENGINE_NAME = "crawl4ai"
    REQUIRES_EXTRACT = False  # 无 extract 时走自动结构化兜底(yaml-schema 规则 7)
    SUPPORTED_EXTRACT_TYPES = ("list", "item")

    # ------------------------------------------------------- configuration

    def _timeout(self) -> float:
        """Run budget in seconds from ``engine_options.crawl4ai.timeout``."""
        value = self.engine_options().get("timeout")
        if value is None:
            return DEFAULT_PAGE_TIMEOUT_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.crawl4ai.timeout 应为正数秒,当前为 {value!r}",
                error_type="invalid_timeout",
            )
        return float(value)

    def _headless(self) -> bool:
        """Headless preference (default True — 桌面/服务器都默认无头)."""
        value = self.engine_options().get("headless")
        if value is None:
            return True
        if not isinstance(value, bool):
            raise FetchError(
                f"engine_options.crawl4ai.headless 应为布尔值,当前为 {value!r}",
                error_type="invalid_headless",
            )
        return value

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        # 配置校验先于依赖加载:engine_options 拼错时先报配置错(fail-fast 于配置)。
        timeout = self._timeout()
        headless = self._headless()
        crawl4ai = load_crawl4ai()
        # 代理与 headers 必须真的到达浏览器:源配 pool: 代理时基座已在
        # _prepare_proxy_transport 解析出具体 upstream(_active_proxy_url),
        # 不传等于用户真实 IP 直连目标站(安全红线:显式代理意图不得静默丢弃)。
        browser_kwargs: dict[str, Any] = {"headless": headless}
        if self._active_proxy_url:
            browser_kwargs["proxy"] = self._active_proxy_url
        if self.source.headers:
            browser_kwargs["headers"] = dict(self._headers)  # 含解析后的 Cookie 等凭据
        browser_config = crawl4ai.BrowserConfig(**browser_kwargs)
        # 我们自己管变更指纹与缓存语义,crawl4ai 自带缓存一律旁路,保证行为确定。
        run_config = crawl4ai.CrawlerRunConfig(
            cache_mode=crawl4ai.CacheMode.BYPASS,
            page_timeout=int(timeout * 1000),
        )
        logger.info(
            "crawl4ai 后端就绪 headless=%s timeout=%s proxy=%s headers=%s targets=%s",
            headless,
            timeout,
            mask_proxy_url(self._active_proxy_url) if self._active_proxy_url else "direct",
            len(self._headers) if self.source.headers else 0,
            len(self._template_urls()),
        )
        items: list[dict] = []
        # 浏览器生命周期挂在 async context manager 上:CancelledError 穿透时
        # 由 __aexit__ 负责清理(async 约定:捕获取消后清理并重新抛出)。
        # __aenter__ 启动失败(playwright 二进制未装是最常见真实故障)也必须
        # 结构化——它发生在 _run 的 try 之外,裸逃会归为 unknown。
        try:
            async with crawl4ai.AsyncWebCrawler(config=browser_config) as crawler:
                for target_url in self._template_urls():
                    # 礼貌约束作用于目标站点(由 crawl4ai 代抓),而非我们自己的后端。
                    await self._ensure_robots_allowed(target_url)
                    await self._acquire_rate_limit(target_url)
                    result = await self._run(crawler, target_url, run_config, timeout)
                    items.extend(self._extract_target(result, target_url))
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转结构化(错误链保留)
            raise self._browser_failure(exc) from exc
        return items

    @staticmethod
    def _browser_failure(exc: Exception) -> FetchError:
        """Wrap a browser-lifecycle failure (startup included) as structured FetchError."""
        message = str(exc)
        lowered = message.lower()
        hint = ""
        if "executable" in lowered or "playwright install" in lowered or "crawl4ai-setup" in lowered:
            hint = "(浏览器二进制缺失?请执行 crawl4ai-setup 安装 playwright 浏览器;详见 doctor 诊断)"
        return FetchError(
            f"crawl4ai 浏览器启动/运行失败: {message}{hint}",
            error_type="crawl4ai_error",
        )

    async def _run(self, crawler: Any, target_url: str, run_config: Any, timeout: float) -> Any:
        """One ``arun`` under the outer wait_for guard (预算耗尽按超时分类)."""
        try:
            return await asyncio.wait_for(
                crawler.arun(url=target_url, config=run_config), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise FetchError(
                f"crawl4ai 抓取超时 url={target_url}(预算 {timeout}s)",
                error_type="timeout",
            ) from exc
        except Exception as exc:
            raise FetchError(
                f"crawl4ai 浏览器抓取失败 url={target_url}: {exc}",
                error_type="crawl4ai_error",
            ) from exc

    # ----------------------------------------------------------- extraction

    def _extract_target(self, result: Any, target_url: str) -> list[dict]:
        if not getattr(result, "success", False):
            raise FetchError(
                f"crawl4ai 抓取失败 url={target_url}: "
                f"{getattr(result, 'error_message', '') or '未知错误'}",
                error_type="crawl4ai_error",
            )
        if self.source.extract is not None:
            html = getattr(result, "html", None)
            if not html or not isinstance(html, str):
                raise ExtractionError(
                    f"crawl4ai 结果缺少 html,无法执行 extract 选择器 url={target_url}"
                )
            return extract_html(html, self.source.extract, base_url=target_url)
        return [
            {
                "url": target_url,
                "title": self._metadata_title(result),
                "content": self._markdown_text(result),
            }
        ]

    @staticmethod
    def _metadata_title(result: Any) -> str:
        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, dict):
            return metadata.get("title") or ""
        return ""

    @staticmethod
    def _markdown_text(result: Any) -> str:
        """Rendered markdown as plain text (兼容 str 与 MarkdownGenerationResult)."""
        markdown = getattr(result, "markdown", None)
        raw = getattr(markdown, "raw_markdown", None)
        if isinstance(raw, str):
            return raw
        return markdown if isinstance(markdown, str) else ""
