"""L4 engine: adaptive anti-bot crawling via Scrapling (self-healing selectors).

Contract (PRD 10-01-v03-engine-scrapling):

- **optional dependency**: ``scrapling.fetchers`` is imported lazily at *fetch*
  time (never at module import), so the core pipeline stays zero-heavy-
  dependency; when the package is absent the engine raises a structured
  :class:`FetchError` (``error_type=dependency_missing``) whose message carries
  ``pip install shishi[scrapling]`` verbatim (doctor 消费原文); a broken install
  (non-ImportError at import time) is structured the same way;
- **适用场景与边界**:基础盾源(TLS/HTTP2 指纹检测、Cloudflare 基础质询)与
  改版频繁源(adaptive 自愈选择器:每次 ``css()`` 带 ``adaptive=True`` +
  ``auto_save=True``,定位器失效时按已存结构指纹自动重定位,选择器一改就死
  的痛点由 Scrapling 的相似度引擎兜住)、以及 ``pagination.mode: scroll``
  的无限滚动列表页。**企业级风控不支持**:CF 企业级高难 Turnstile、
  PerimeterX / DataDome、登录态+风控评分体系不在本层能力内 —— 失败沿链
  结构化上报,由 v0.4 的 L5 ``stealth_browser`` / L6 ``llm_browser`` 兜底;
  「真人验证+手机号」类源无解也不碰(安全基线,不做绕过);
- **three backends** via ``engine_options.scrapling.backend``: ``stealth``
  (默认,``StealthyFetcher`` 隐身指纹 + 可选 Turnstile 求解,过 CF 基础盾)、
  ``dynamic``(``DynamicFetcher`` 普通 Playwright,纯 JS 渲染不需隐身)、
  ``static``(``AsyncFetcher`` curl_cffi 轻量 TLS 伪装);
- **pagination.mode: scroll 在本引擎实装**(L1-L3 不支持;fetch_base 的
  walk 只管 ``{page}`` 模板展开且无人消费浏览器 ``page_action`` 钩子,故
  最小侵入点在引擎内):``max_pages`` 即无限滚动轮数上限,page_action 驱动
  页内 JS 滚动到底、页面高度不再增长即提前收尾;scroll 与 ``static`` 后端
  互斥(结构化 ``scroll_unsupported``,auto 链据此降级);scroll 与
  ``{page}`` 模板占位符互斥(语义冲突,``invalid_pagination``);
- extraction reuses the L2 field grammar(``a.title@href`` 属性 / 默认取文本)
  over Scrapling ``Selector`` objects(为保留自愈能力,不复用 selectolax 的
  ``extract_html``);``json_path`` 属 L1 语义,此处拒绝让 auto 链正确降级;
  无 ``extract`` 时自动结构化兜底 ``{url, title, content}``(yaml-schema
  规则 7,与 L3/firecrawl 无 extract 路径同形);
- politeness primitives (robots / rate limit) apply to the *target* site —
  Scrapling drives its own HTTP client / browser on our behalf, mirroring the
  crawl4ai/firecrawl split (target politeness vs. backend structured
  failures); every page fetch is wrapped in ``asyncio.wait_for`` so a hung
  page cannot stall the run. ``StealthyFetcher/DynamicFetcher.async_fetch``
  是无状态门面(每次调用自建自拆浏览器),模板翻页多页时逐页开合,qps 限速
  本就拉大页间隔,不复用 session 换实现简单性;
- **代理与 headers 必须真的到达后端**:源配 ``pool:`` 代理时基座已解析出
  具体 upstream(``_active_proxy_url``),不传等于真实 IP 直连目标站(显式
  代理意图不得静默丢弃);headers 凭据已解析后经 ``extra_headers``(浏览器)
  / ``headers``(static)透传;默认 UA 不透传 —— 隐身后端的 User-Agent 必须
  与浏览器指纹同源(由库生成真实 UA),写死 MYIA/0.1 反而破功。

Raises:
    FetchError: scrapling not installed / broken install
        (``dependency_missing``), bad ``engine_options.scrapling``
        (``invalid_backend``/``invalid_timeout``/``invalid_headless``/...),
        scroll 与 static 后端或 ``{page}`` 模板冲突
        (``scroll_unsupported``/``invalid_pagination``), HTTP >= 400
        (``http_<status>``), browser/HTTP 后端启动或运行失败
        (``scrapling_error`` — 启动失败含 scrapling install 提示),
        run budget exhausted (``timeout``).
    RobotsDisallowedError: robots.txt forbids the target URL(基座抛出).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from shishi.engines.fetch_base import (
    BaseEngine,
    FetchError,
    coerce_numeric_text,
    mask_proxy_url,
    split_attr_selector,
)
from shishi.engines.stealth_browser import classify_hard_wall

logger = logging.getLogger(__name__)

LAYER = "L4"

#: Install command surfaced verbatim in the dependency-missing error
#: (验收标准: 未安装依赖时错误信息含 ``pip install shishi[scrapling]``).
INSTALL_COMMAND = "pip install shishi[scrapling]"

#: ``shishi[scrapling]`` 只拉 scrapling 基础包;fetchers 子包还需其自带 extras。
FETCHERS_INSTALL_NOTE = '如导入 scrapling 后仍缺 fetchers,请补装 pip install "scrapling[fetchers]"'

#: 浏览器后端(stealth/dynamic)首次运行需要 scrapling 自装的浏览器内核。
BROWSER_SETUP_NOTE = "浏览器后端首次运行可能还需 scrapling install 安装 Chromium 内核(详见 doctor 诊断)"

#: Per-page run budget (seconds) when ``engine_options.scrapling.timeout`` is
#: absent — 隐身浏览器过盾 + 等质询比静态页慢,默认预算宽于管线 HTTP 默认 30s。
DEFAULT_PAGE_TIMEOUT_SECONDS = 60.0

BACKENDS: tuple[str, ...] = ("stealth", "dynamic", "static")

#: 每轮滚动后的沉降等待:600ms(页内脚本内嵌,给新内容的加载留时间)。
#: 无限滚动页内脚本:滚动到底 → 等沉降 → 高度不再增长即停;
#: ``maxRounds`` 由引擎从 ``pagination.max_pages`` 传入,硬顶轮数。
SCROLL_TO_BOTTOM_JS = """
async (maxRounds) => {
  let rounds = 0;
  let lastHeight = -1;
  while (rounds < maxRounds) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    await new Promise((resolve) => setTimeout(resolve, 600));
    const height = document.documentElement.scrollHeight;
    rounds += 1;
    if (height === lastHeight) { break; }
    lastHeight = height;
  }
  return document.documentElement.scrollHeight;
}
"""

__all__ = [
    "LAYER",
    "BACKENDS",
    "BROWSER_SETUP_NOTE",
    "DEFAULT_PAGE_TIMEOUT_SECONDS",
    "INSTALL_COMMAND",
    "SCROLL_TO_BOTTOM_JS",
    "ScraplingEngine",
    "load_scrapling",
]

#: url 属性选择器补全相对链接时视为地址(与 fetch_base._URL_ATTRIBUTES 同义;
#: fetch_base 未导出,两行常量不值得跨模块引私有名)。
_URL_ATTRIBUTES = frozenset({"href", "src"})


def load_scrapling() -> Any:
    """Return the optional ``scrapling.fetchers`` module, imported lazily.

    Lazy so that ``shishi.engines.scrapling`` (and the whole engine registry)
    imports cleanly without the optional package, and so tests can inject a
    fake module via ``sys.modules``. 导入的是 ``scrapling.fetchers`` 子包
    (引擎消费 AsyncFetcher/StealthyFetcher/DynamicFetcher,基础包 parser-only
    装法到不了 fetchers)。

    Raises:
        FetchError: scrapling is not installed — structured
            ``dependency_missing`` carrying :data:`INSTALL_COMMAND` verbatim
            plus the fetchers-extras and browser-kernel notes (doctor 消费).
        FetchError: 安装损坏(导入期抛非 ImportError)同样结构化,不裸逃。
    """
    try:
        return importlib.import_module("scrapling.fetchers")
    except ImportError as exc:
        raise FetchError(
            f"scrapling 引擎依赖未安装:请先执行 {INSTALL_COMMAND}"
            f"(核心流水线零重依赖,scrapling 为可选 extras;{FETCHERS_INSTALL_NOTE};"
            f"{BROWSER_SETUP_NOTE})",
            error_type="dependency_missing",
        ) from exc
    except Exception as exc:  # 安装损坏(导入期抛非 ImportError)同样结构化
        raise FetchError(
            f"scrapling 包导入失败(安装可能损坏): {exc};可尝试重装({INSTALL_COMMAND})",
            error_type="dependency_missing",
        ) from exc


@dataclass(frozen=True)
class _Options:
    """Validated ``engine_options.scrapling`` snapshot (全量先于依赖加载)."""

    backend: str
    timeout: float
    headless: bool
    solve_cloudflare: bool
    network_idle: bool
    wait_selector: str | None
    adaptive: bool


class ScraplingEngine(BaseEngine):
    """Adaptive, stealth-backed crawling that survives site redesigns."""

    LAYER = "L4"
    ENGINE_NAME = "scrapling"
    REQUIRES_EXTRACT = False  # 无 extract 时走自动结构化兜底(yaml-schema 规则 7)
    SUPPORTED_EXTRACT_TYPES = ("list", "item")
    SUPPORTS_SCROLL = True  # pagination.mode: scroll 在本引擎实装(PRD 10-01-v03)

    # ------------------------------------------------------- configuration

    def _backend(self) -> str:
        """``engine_options.scrapling.backend``(默认 stealth:过盾是本层卖点)."""
        value = self.engine_options().get("backend")
        if value is None:
            return "stealth"
        if value not in BACKENDS:
            raise FetchError(
                f"engine_options.scrapling.backend 应为 {'/'.join(BACKENDS)} 之一,当前为 {value!r}",
                error_type="invalid_backend",
            )
        return str(value)

    def _timeout(self) -> float:
        """Run budget in seconds from ``engine_options.scrapling.timeout``."""
        value = self.engine_options().get("timeout")
        if value is None:
            return DEFAULT_PAGE_TIMEOUT_SECONDS
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise FetchError(
                f"engine_options.scrapling.timeout 应为正数秒,当前为 {value!r}",
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
                f"engine_options.scrapling.headless 应为布尔值,当前为 {value!r}",
                error_type="invalid_headless",
            )
        return value

    def _solve_cloudflare(self) -> bool:
        """Cloudflare 质询求解(默认开:PRD 的「过 CF 基础盾」是本层验收场景)。"""
        value = self.engine_options().get("solve_cloudflare")
        if value is None:
            return True
        if not isinstance(value, bool):
            raise FetchError(
                f"engine_options.scrapling.solve_cloudflare 应为布尔值,当前为 {value!r}",
                error_type="invalid_solve_cloudflare",
            )
        return value

    def _network_idle(self) -> bool:
        """Wait for network idle after load (default True,滚动源多为瀑布流)."""
        value = self.engine_options().get("network_idle")
        if value is None:
            return True
        if not isinstance(value, bool):
            raise FetchError(
                f"engine_options.scrapling.network_idle 应为布尔值,当前为 {value!r}",
                error_type="invalid_network_idle",
            )
        return value

    def _wait_selector(self) -> str | None:
        """Optional ``wait_selector``(列表容器渲染完成的信号)."""
        value = self.engine_options().get("wait_selector")
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise FetchError(
                f"engine_options.scrapling.wait_selector 应为非空字符串选择器,当前为 {value!r}",
                error_type="invalid_wait_selector",
            )
        return value

    def _adaptive(self) -> bool:
        """自愈选择器开关(默认开:adaptive 是 L4 的存在理由)."""
        value = self.engine_options().get("adaptive")
        if value is None:
            return True
        if not isinstance(value, bool):
            raise FetchError(
                f"engine_options.scrapling.adaptive 应为布尔值,当前为 {value!r}",
                error_type="invalid_adaptive",
            )
        return value

    def _options(self) -> _Options:
        """engine_options.scrapling 全量校验快照(先于依赖加载,fail-fast 于配置)。

        逐项校验并在首个错误处结构化抛出(与 crawl4ai 同约定);全部通过才
        进入依赖加载与网络 I/O。
        """
        return _Options(
            backend=self._backend(),
            timeout=self._timeout(),
            headless=self._headless(),
            solve_cloudflare=self._solve_cloudflare(),
            network_idle=self._network_idle(),
            wait_selector=self._wait_selector(),
            adaptive=self._adaptive(),
        )

    def _scroll_rounds(self, backend: str) -> int | None:
        """滚动轮数上限(= ``pagination.max_pages``);非 scroll 模式返回 None。

        校验先于依赖加载(fail-fast 于配置):scroll 与 static 后端互斥
        (无 page_action 可挂),与 ``{page}`` 模板互斥(语义冲突)。

        Raises:
            FetchError: ``scroll_unsupported`` / ``invalid_pagination``.
        """
        pagination = self.source.pagination
        if pagination is None or pagination.mode != "scroll":
            return None
        if "{page}" in self.source.url:
            raise FetchError(
                f"pagination.mode 为 scroll 时 url 不应包含 {{page}} 模板占位符"
                f"(那是 template 模式语义)url={self.source.url}",
                error_type="invalid_pagination",
            )
        if backend == "static":
            raise FetchError(
                "pagination.mode 为 scroll 需要浏览器后端(stealth/dynamic),"
                "当前 backend='static'(static 后端无 page_action 可挂载滚动钩子)",
                error_type="scroll_unsupported",
            )
        return pagination.max_pages

    # -------------------------------------------------------------- fetch

    async def _fetch_impl(self) -> list[dict]:
        # 配置校验先于依赖加载:engine_options 拼错时先报配置错(fail-fast 于配置)。
        options = self._options()
        scroll_rounds = self._scroll_rounds(options.backend)
        scrapling = load_scrapling()
        targets = [self.source.url] if scroll_rounds is not None else self._template_urls()
        logger.info(
            "scrapling 后端就绪 backend=%s timeout=%ss scroll_rounds=%s proxy=%s headers=%s targets=%s",
            options.backend,
            options.timeout,
            scroll_rounds if scroll_rounds is not None else "off",
            mask_proxy_url(self._active_proxy_url) if self._active_proxy_url else "direct",
            len(self.source.headers),
            len(targets),
        )
        items: list[dict] = []
        fetch = self._entrypoint(scrapling, options.backend)
        for target_url in targets:
            # 礼貌约束作用于目标站点(由 scrapling 代抓),而非我们自己的后端。
            await self._ensure_robots_allowed(target_url)
            await self._acquire_rate_limit(target_url)
            page = await self._scrape_page(fetch, target_url, options, scroll_rounds)
            extract = self.source.extract
            if extract is None:
                # 无 extract:墙页会被 auto-structure 成条目,必须先于兜底查墙。
                self._refuse_hard_wall(page, target_url)
            page_items = self._extract_page(page, target_url)
            if extract is not None and not page_items:
                # 带 extract 的 200 硬墙页典型形态是 0 条静默:提取后仍需查墙。
                self._refuse_hard_wall(page, target_url)
            items.extend(page_items)
        return items

    def _entrypoint(self, scrapling: Any, backend: str) -> Callable[..., Awaitable[Any]]:
        """Backend -> its async fetch callable(调用面统一为 coroutine 工厂)."""
        if backend == "stealth":
            return scrapling.StealthyFetcher.async_fetch
        if backend == "dynamic":
            return scrapling.DynamicFetcher.async_fetch
        return scrapling.AsyncFetcher.get

    async def _scrape_page(
        self,
        fetch: Callable[..., Awaitable[Any]],
        target_url: str,
        options: _Options,
        scroll_rounds: int | None,
    ) -> Any:
        """One page fetch under the outer wait_for guard(预算耗尽按超时分类)."""
        try:
            page = await asyncio.wait_for(
                fetch(target_url, **self._page_kwargs(options, scroll_rounds)),
                timeout=options.timeout,
            )
        except TimeoutError as exc:  # 3.11 起 asyncio.TimeoutError 即内建 TimeoutError 别名
            raise FetchError(
                f"scrapling 抓取超时 url={target_url}(预算 {options.timeout}s)",
                error_type="timeout",
            ) from exc
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转结构化(错误链保留)
            raise self._scrapling_failure(exc) from exc
        status = getattr(page, "status", 0)
        if status >= 400:
            raise FetchError(
                f"scrapling 抓取失败 url={target_url}: HTTP {status}(盾拦截或源不可达,auto 链将据此降级)",
                error_type=f"http_{status}",
            )
        return page

    def _page_kwargs(self, options: _Options, scroll_rounds: int | None) -> dict[str, Any]:
        """Per-backend call kwargs(代理/headers 透传语义见模块 docstring)."""
        selector_config = {"adaptive": options.adaptive}
        headers = self._forwarded_headers()
        if options.backend == "static":
            kwargs: dict[str, Any] = {"timeout": options.timeout, "selector_config": selector_config}
            if headers:
                kwargs["headers"] = headers
        else:
            kwargs = {
                "headless": options.headless,
                "timeout": int(options.timeout * 1000),  # 浏览器侧单位毫秒
                "network_idle": options.network_idle,
                "selector_config": selector_config,
            }
            if options.backend == "stealth":
                kwargs["solve_cloudflare"] = options.solve_cloudflare
            if options.wait_selector:
                kwargs["wait_selector"] = options.wait_selector
            if headers:
                kwargs["extra_headers"] = headers
            if scroll_rounds is not None:
                kwargs["page_action"] = self._scroll_action(scroll_rounds)
        if self._active_proxy_url:
            kwargs["proxy"] = self._active_proxy_url
        return kwargs

    def _forwarded_headers(self) -> dict[str, str]:
        """源配置的 headers(凭据已解析);引擎默认 UA 不透传(见模块 docstring)."""
        has_custom_ua = any(key.lower() == "user-agent" for key in self.source.headers)
        return {key: value for key, value in self._headers.items() if has_custom_ua or key.lower() != "user-agent"}

    def _scroll_action(self, rounds: int) -> Callable[[Any], Awaitable[None]]:
        """无限滚动 page_action(库在导航完成后 ``await page_action(page)``)."""

        async def action(page: Any) -> None:
            await page.evaluate(SCROLL_TO_BOTTOM_JS, rounds)

        return action

    @staticmethod
    def _scrapling_failure(exc: Exception) -> FetchError:
        """Wrap a backend lifecycle failure (startup included) as structured FetchError."""
        message = str(exc)
        lowered = message.lower()
        hint = ""
        if "executable" in lowered or "install" in lowered:
            hint = f"({BROWSER_SETUP_NOTE})"
        return FetchError(
            f"scrapling 后端启动/运行失败: {message}{hint}",
            error_type="scrapling_error",
        )

    # ----------------------------------------------------------- extraction

    def _refuse_hard_wall(self, page: Any, target_url: str) -> None:
        """手机验证码/真人审核硬墙一票否决(security-baseline:无解也不碰)。

        HTTP 200 的硬墙页不该被静默提取:带 extract 会得到 0 条「成功」,
        无 extract 更会把墙页 title/body auto-structure 成条目入库推送。
        与 L5 同款检测(复用 :func:`classify_hard_wall`),零尝试零绕过;
        命中即结构化 ``captcha_phone_verification`` / ``captcha_human_review``。
        基础盾(CF 质询/hCaptcha)不在此列 —— 过基础盾正是本引擎的职责。
        """
        parts: list[str] = []
        for selector in ("title", "body"):
            nodes = page.css(selector)  # 普通查找:不写自适应指纹,不扰动自愈轨迹
            if nodes:
                text = getattr(nodes[0], "text", None)
                if text:
                    parts.append(str(text))
        kind = classify_hard_wall("\n".join(parts))
        if kind is not None:
            raise FetchError(
                f"scrapling 命中真人验证/手机号硬墙 url={target_url}: {kind}"
                "(安全基线:无解也不碰,零尝试不绕过;显式指定时请改用其他源)",
                error_type=f"captcha_{kind}",
            )

    def _extract_page(self, page: Any, target_url: str) -> list[dict]:
        extract = self.source.extract
        if extract is None:
            return self._auto_structure(page, target_url)
        if extract.type == "list":
            items: list[dict] = []
            for node in self._css(page, extract.item):
                record = {
                    name: value
                    for name, selector in extract.fields.items()
                    if (value := self._element_value(node, selector, target_url)) is not None
                }
                if record:
                    items.append(record)
            return items
        record = {
            name: value
            for name, selector in extract.fields.items()
            if (value := self._element_value(page, selector, target_url)) is not None
        }
        return [record] if record else []

    def _css(self, root: Any, selector: str) -> Any:
        """Adaptive css lookup:命中即存结构指纹,失效时按相似度重定位.

        返回库原生的 ``Selectors`` 序列(可迭代、带 ``.first``),不二次包装。
        """
        if self._adaptive():
            return root.css(selector, adaptive=True, auto_save=True)
        return root.css(selector)

    def _element_value(self, element: Any, selector: str, base_url: str) -> str | None:
        """First match of a field selector under a Scrapling element(@attr 或文本)."""
        css, attr = split_attr_selector(selector)
        if css:
            nodes = self._css(element, css)
            if not nodes:
                return None
            element = nodes[0]
        if attr:
            value = element.attrib.get(attr)
            if value is None:
                return None
            if base_url and attr in _URL_ATTRIBUTES:
                return urljoin(base_url, str(value))
            return str(value)
        text = element.text
        if not text:
            return None
        return coerce_numeric_text(str(text))

    def _auto_structure(self, page: Any, target_url: str) -> list[dict]:
        """无 extract 兜底:{url, title, content}(与 L3/firecrawl 同形)."""
        title_nodes = self._css(page, "title")
        title_node = title_nodes.first if title_nodes else None
        body_nodes = self._css(page, "body")
        title = str(title_node.text).strip() if title_node is not None and title_node.text else ""
        content = str(body_nodes[0].text).strip() if body_nodes and body_nodes[0].text else ""
        return [{"url": target_url, "title": title, "content": content}]
