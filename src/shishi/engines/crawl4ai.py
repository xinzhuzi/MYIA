"""L3 engine: JS-rendered crawling via crawl4ai (default heavy engine).

Contract (PRD 10-01-v02-engine-crawl4ai):

- **optional dependency**: crawl4ai is imported lazily at *fetch* time (never
  at module import), so the core pipeline stays zero-heavy-dependency; when
  the package is absent the engine raises a structured :class:`FetchError`
  (``error_type=dependency_missing``) whose message carries the extras
  install command (``pip install shishi[crawl4ai]``) and the browser-binary
  note (crawl4ai manages its own playwright browsers — first run may need
  ``crawl4ai-setup``), which ``myia doctor`` surfaces verbatim;
- with ``extract`` (CSS ``list``/``item``) the rendered HTML is parsed by the
  shared ``extract_html`` (same selectors as L2); ``json_path`` is rejected
  here so ``engine: auto`` degrades to the next engine instead of
  misbehaving (same rule as L2);
- the full crawl4ai config surface is reachable via
  ``engine_options.crawl4ai.browser_options`` / ``run_options`` (dicts merged
  into ``BrowserConfig`` / ``CrawlerRunConfig`` — v12 task 10-03-v12-crawl4ai-l3);
  the engine's *semantic* keys (browser: ``headless``/``proxy``(+
  ``proxy_config``)/``headers``; run: ``cache_mode``/``page_timeout``) are
  single-source and cannot be overridden through the passthrough (double
  source of truth = 配置冲突, fail-fast 结构化拒绝); a passthrough key/value
  the crawl4ai config class rejects at construction time (``TypeError`` for
  unknown keys, ``ValueError`` for its ``__init__`` validation) surfaces as a
  structured ``invalid_browser_options`` / ``invalid_run_options`` instead of
  an ``unknown``;
- identity consistency (robots UA 一致性): robots 判定身份与浏览器实际抓取
  身份同源——无源级 headers 的源把基座注入的默认 UA 同步传给浏览器
  (``user_agent``),robots ``can_fetch`` 放行的身份即目标站看到的身份;
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
        (``invalid_timeout``/``invalid_headless``/``invalid_browser_options``/
        ``invalid_run_options``), browser startup or run
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

from shishi.engines.fetch_base import (
    DEFAULT_USER_AGENT,
    BaseEngine,
    ExtractionError,
    FetchError,
    extract_html,
    mask_proxy_url,
)


def _markdown_image_urls(markdown: str, base_url: str) -> list[str]:
    """同域图片链接收集(10-03-vision-pipeline 拍板⑥),依赖未提交前的可选降级。

    shishi.vision.collect 由 vision 会话按自己的节奏发行;引擎对它是软依赖——
    模块缺席时跳过图片收集(零行为差异),不挡采集主路径。
    """
    try:
        from shishi.vision.collect import markdown_image_urls
    except ImportError:
        return []
    return markdown_image_urls(markdown, base_url)

logger = logging.getLogger(__name__)

LAYER = "L3"

#: Install command surfaced verbatim in the dependency-missing error
#: (验收标准: 未安装依赖时错误信息含 ``pip install shishi[crawl4ai]``).
INSTALL_COMMAND = "pip install shishi[crawl4ai]"

#: Per-page run budget (seconds) when ``engine_options.crawl4ai.timeout`` is
#: absent — JS 渲染页比静态页慢,默认预算宽于管线 HTTP 默认 30s。
DEFAULT_PAGE_TIMEOUT_SECONDS = 60.0

#: Engine-owned BrowserConfig keys — semantic knobs whose value the engine
#: derives itself (headless 校验 / proxy 解析+脱敏 / headers 凭据解析+脱敏日志);
#: passthrough ``browser_options`` may not override them (单一来源,冲突即拒).
#: ``proxy`` 与 ``proxy_config`` 同为代理语义(后者是新版库的注入键),自管不拆.
_RESERVED_BROWSER_KEYS = frozenset({"headless", "proxy", "proxy_config", "headers"})

#: 代理注入回落(旧版库无 ``ProxyConfig.from_string``)的 WARNING 进程内只报
#: 一次——回落是兼容行为而非每轮新故障,重复刷屏只会淹没日志。
_PROXY_FALLBACK_WARNED = False

#: Engine-owned CrawlerRunConfig keys — cache semantics (BYPASS, 我们自管变更
#: 指纹) and the page budget (timeout 预算护栏) stay single-source.
_RESERVED_RUN_KEYS = frozenset({"cache_mode", "page_timeout"})

__all__ = [
    "LAYER",
    "DEFAULT_PAGE_TIMEOUT_SECONDS",
    "INSTALL_COMMAND",
    "Crawl4AIEngine",
]


def load_crawl4ai() -> Any:
    """Return the optional ``crawl4ai`` module, imported lazily at call time.

    Lazy so that ``shishi.engines.crawl4ai`` (and the whole engine registry)
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

    def _passthrough_options(
        self, key: str, *, reserved: frozenset[str], error_type: str
    ) -> dict[str, Any]:
        """Validated ``engine_options.crawl4ai.<key>`` passthrough dict.

        Opens the rest of crawl4ai's config surface (v12-crawl4ai-l3) without
        giving up the semantic keys the engine derives itself: dict shape is
        validated here (fail-fast 于配置、先于依赖加载,与 timeout/headless 同序),
        and a key colliding with ``reserved`` is a structured config error —
        双来源(引擎推导值 vs 透传值)不是覆盖关系,是配置冲突。

        Raises:
            FetchError: not a mapping (``error_type``), or any key in
                ``reserved`` present (same class).
        """
        value = self.engine_options().get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise FetchError(
                f"engine_options.crawl4ai.{key} 应为键值映射,当前为 {type(value).__name__}",
                error_type=error_type,
            )
        conflicts = sorted(reserved & set(value))
        if conflicts:
            raise FetchError(
                f"engine_options.crawl4ai.{key} 不可覆盖引擎自管键 {conflicts}"
                "(语义值由引擎单一来源推导:代理解析/凭据脱敏/缓存旁路/预算护栏;"
                "请直接配置对应顶层键 engine_options.crawl4ai.*)",
                error_type=error_type,
            )
        return dict(value)

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        # 配置校验先于依赖加载:engine_options 拼错时先报配置错(fail-fast 于配置)。
        timeout = self._timeout()
        headless = self._headless()
        browser_extra = self._passthrough_options(
            "browser_options", reserved=_RESERVED_BROWSER_KEYS, error_type="invalid_browser_options"
        )
        run_extra = self._passthrough_options(
            "run_options", reserved=_RESERVED_RUN_KEYS, error_type="invalid_run_options"
        )
        crawl4ai = load_crawl4ai()
        # 代理与 headers 必须真的到达浏览器:源配 pool: 代理时基座已在
        # _prepare_proxy_transport 解析出具体 upstream(_active_proxy_url),
        # 不传等于用户真实 IP 直连目标站(安全红线:显式代理意图不得静默丢弃)。
        browser_kwargs: dict[str, Any] = {"headless": headless}
        if self._active_proxy_url:
            browser_kwargs.update(self._proxy_browser_kwarg(crawl4ai))
        if self.source.headers:
            browser_kwargs["headers"] = dict(self._headers)  # 含解析后的 Cookie 等凭据
        else:
            # robots UA 一致性:robots 判定身份(fetch_base._ensure_robots_allowed
            # 以 self._headers 的 UA can_fetch)必须等于浏览器实际抓取身份——
            # 无源级 headers 时基座已补默认 UA,同步传给浏览器,否则 robots 以
            # MYIA 身份放行、目标站看到的却是 crawl4ai 内置默认 UA(身份分裂,
            # 礼貌约定失真)。透传 browser_options.user_agent 仍可覆盖
            # (update 在后,显式配置优先)。
            browser_kwargs["user_agent"] = next(
                (value for key, value in self._headers.items() if key.lower() == "user-agent"),
                DEFAULT_USER_AGENT,
            )
        browser_kwargs.update(browser_extra)
        # 透传键/值被 crawl4ai 配置类拒绝必须结构化:真库配置类是
        # @_with_defaults 装饰的普通类(非 dataclass)——未知关键字参数因显式
        # __init__ 签名抛 TypeError,已知键的非法值在 __init__ 校验中抛
        # ValueError(真库 __init__ 内多处 raise,如 enable_stealth×browser_mode、
        # 非正数超时),两类同拦 —— 否则裸异常逃到 registry 被归为 unknown,
        # 排障看不到键名/非法值。
        try:
            browser_config = crawl4ai.BrowserConfig(**browser_kwargs)
        except (TypeError, ValueError) as exc:
            raise FetchError(
                f"engine_options.crawl4ai.browser_options 被 crawl4ai.BrowserConfig "
                f"拒绝(未知键或非法值): {exc}",
                error_type="invalid_browser_options",
            ) from exc
        # 我们自己管变更指纹与缓存语义,crawl4ai 自带缓存一律旁路,保证行为确定。
        run_kwargs: dict[str, Any] = {
            "cache_mode": crawl4ai.CacheMode.BYPASS,
            "page_timeout": int(timeout * 1000),
            **run_extra,
        }
        try:
            run_config = crawl4ai.CrawlerRunConfig(**run_kwargs)
        except (TypeError, ValueError) as exc:
            raise FetchError(
                f"engine_options.crawl4ai.run_options 被 crawl4ai.CrawlerRunConfig "
                f"拒绝(未知键或非法值): {exc}",
                error_type="invalid_run_options",
            ) from exc
        logger.info(
            "crawl4ai 后端就绪 headless=%s timeout=%s proxy=%s headers=%s "
            "browser_options=%s run_options=%s targets=%s",
            headless,
            timeout,
            mask_proxy_url(self._active_proxy_url) if self._active_proxy_url else "direct",
            len(self._headers) if self.source.headers else 0,
            sorted(browser_extra),
            sorted(run_extra),
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

    def _proxy_browser_kwarg(self, crawl4ai: Any) -> dict[str, Any]:
        """池代理 → BrowserConfig 代理键(双路径探测:proxy_config 优先)。

        crawl4ai 新版已弃用 ``proxy`` 参数,注入走 ``proxy_config``(经
        ``ProxyConfig.from_string`` 构造);旧版库(无 ProxyConfig/from_string,
        或解析失败)回落弃用参数并记一次性 WARNING——回落是兼容行为,
        进程内只报一次,不逐源刷屏。

        Returns:
            单键 dict:``{"proxy_config": ProxyConfig}``(新版)或
            ``{"proxy": url}``(回落),交由 :meth:`_fetch_impl` 合并。
        """
        proxy_config_cls = getattr(crawl4ai, "ProxyConfig", None)
        from_string = getattr(proxy_config_cls, "from_string", None)
        if callable(from_string):
            try:
                return {"proxy_config": from_string(self._active_proxy_url)}
            except Exception as exc:  # noqa: BLE001 - 构造失败即回落,不裸逃
                self._warn_proxy_fallback(f"ProxyConfig.from_string 解析失败({exc})")
        else:
            self._warn_proxy_fallback("当前 crawl4ai 版本无 ProxyConfig.from_string(旧版库)")
        return {"proxy": self._active_proxy_url}

    @staticmethod
    def _warn_proxy_fallback(reason: str) -> None:
        """代理注入回落的进程级一次性 WARNING(global flag 去重)。"""
        global _PROXY_FALLBACK_WARNED
        if _PROXY_FALLBACK_WARNED:
            return
        _PROXY_FALLBACK_WARNED = True
        logger.warning(
            "crawl4ai 代理注入回退弃用 proxy 参数:%s;浏览器仍走该代理,"
            "建议升级 crawl4ai 以使用 proxy_config", reason,
        )

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
        markdown = self._markdown_text(result)
        record: dict[str, Any] = {
            "url": target_url,
            "title": self._metadata_title(result),
            "content": markdown,
        }
        # 图片处理环供给(10-03-vision-pipeline 拍板⑥):无 extract 时图片链接
        # 本会随 markdown 纯文本化丢光;此处收集**同域**图 URL 进 metadata
        # (跨域广告/追踪像素不收),品类 images: 节开启时由管线消费——未开启
        # 则该键静默随 metadata 入库,零行为差异。非空才带键,payload 不膨胀。
        images = _markdown_image_urls(markdown, target_url)
        if images:
            record["images"] = images
        return [record]

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
