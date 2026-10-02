"""Engine registry and auto-degrade orchestration (链终形态: L1→…→L6 llm_browser).

The degrade chain is exactly ``direct_api -> static_html -> crawl4ai ->
firecrawl -> scrapling -> stealth_browser -> llm_browser`` (PRD
10-01-v02-engine-crawl4ai: crawl4ai is pure-pip and therefore ranks ahead of
firecrawl, which needs an external service; PRD 10-01-v03-engine-scrapling:
scrapling — stealth fingerprints / adaptive selectors / infinite scroll —
closes the cheap half as L4; PRD 10-01-v04-engine-stealth: stealth_browser —
anti-detection real browser via invisible_playwright_mcp — joins as L5; PRD
10-01-v04-engine-llm-browser: llm_browser — skyvern 自然语言指挥浏览器 —
completes the chain as L6, the last resort 烧 token 只做兜底). Explicitly
selected engines that are scheduled but not implemented raise a structured
:class:`EngineNotAvailableError` carrying the version that ships them. The
optional-dependency engines (crawl4ai / scrapling / stealth_browser's MCP
server / skyvern backend) are implemented but their backends stay optional:
when a backend is missing the engine raises a structured failure
(``dependency_missing`` / ``mcp_server_missing``) and the chain continues
degrading.

L6 ``llm_browser`` is the chain tail: 失败即终止降级链并结构化上报(链尾无
「下一层」),and it enforces a hard per-source whitelist — only ``engine:
llm_browser`` explicitly, or ``engine: auto`` reached as the last rung (PRD:
硬护栏严于 L3;调用次数/token 预算护栏见 llm_browser 模块文档).

``engine: auto`` tries the chain in order; a persisted engine hint
(store ``engine_hints`` table — written back to SQLite, never to the user's
YAML, per yaml-schema rule 5) is tried first when it belongs to the chain.
A hinted engine that fails has its hint cleared and the chain continues in
canonical order. Every failed attempt is recorded as a structured
:class:`EngineFailure` (source / engine / error class) for logs and doctor.

All engine imports stay lazy so optional dependencies remain optional.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module

from myia.engines.fetch_base import (
    BaseEngine,
    EngineFailure,
    EngineNotAvailableError,
    FetchContext,
    FetchError,
    classify_exception,
)
from myia.schema import SourceConfig

logger = logging.getLogger(__name__)

__all__ = [
    "AUTO_CHAIN",
    "ENGINE_CLASSES",
    "ENGINE_REGISTRY",
    "ENGINE_SCHEDULED_VERSIONS",
    "FetchOutcome",
    "auto_degrade",
    "fetch_source",
    "resolve_engine",
]

# 链终形态 auto-degrade chain (yaml-schema rule 5 order; llm_browser 为 L6 链尾,
# PRD 10-01-v04-engine-llm-browser)。
AUTO_CHAIN: tuple[str, ...] = (
    "direct_api",
    "static_html",
    "crawl4ai",
    "firecrawl",
    "scrapling",
    "stealth_browser",
    "llm_browser",
)

# Explicitly selectable engines scheduled for later versions. Empty since
# v04-engine-llm-browser: every schema engine name is now implemented; the
# EngineNotAvailableError branch below stays for future scheduled rungs.
ENGINE_SCHEDULED_VERSIONS: dict[str, str] = {}

# name -> zero-arg factory returning the engine class (lazy import keeps the
# optional-dependency engines importable without their packages).
ENGINE_REGISTRY: dict[str, Callable[[], type[BaseEngine]]] = {
    "direct_api": lambda: _load("direct_api", "DirectAPIEngine"),
    "static_html": lambda: _load("static_html", "StaticHTMLEngine"),
    "crawl4ai": lambda: _load("crawl4ai", "Crawl4AIEngine"),
    "scrapling": lambda: _load("scrapling", "ScraplingEngine"),
    "stealth_browser": lambda: _load("stealth_browser", "StealthBrowserEngine"),
    "llm_browser": lambda: _load("llm_browser", "LLMBrowserEngine"),
    "firecrawl": lambda: _load("firecrawl", "FirecrawlEngine"),
}

# Static typing view of the registry (class names resolved lazily at runtime).
ENGINE_CLASSES = ENGINE_REGISTRY


def _load(module_name: str, class_name: str) -> type[BaseEngine]:
    module = import_module(f"myia.engines.{module_name}")
    return getattr(module, class_name)


def resolve_engine(name: str) -> type[BaseEngine]:
    """Return the engine class registered under ``name`` (lazy import)."""
    try:
        factory = ENGINE_REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown engine {name!r}; known: {sorted(set(ENGINE_REGISTRY) | {'auto'})}"
        ) from None
    return factory()


def auto_degrade(preferred: str) -> list[str]:
    """Ordered fallback chain starting at ``preferred`` (链终形态七层:L1→…→L6 llm_browser).

    ``auto`` -> the full chain; a chain member -> its suffix (``llm_browser``
    degrades nowhere — the L6 tail has no next layer; 失败即终止降级链并结构化
    上报); scheduled-but-unimplemented engines raise a structured
    :class:`EngineNotAvailableError`; anything else is a KeyError.

    Raises:
        EngineNotAvailableError: explicitly selected engine not yet implemented
            (none since llm_browser landed).
        KeyError: unknown engine name.
    """
    if preferred == "auto":
        return list(AUTO_CHAIN)
    if preferred in AUTO_CHAIN:
        return list(AUTO_CHAIN[AUTO_CHAIN.index(preferred):])
    if preferred in ENGINE_SCHEDULED_VERSIONS:
        raise EngineNotAvailableError(
            f"引擎 {preferred!r} 规划于 {ENGINE_SCHEDULED_VERSIONS[preferred]} 实现,"
            f"当前降级链仅支持 {' -> '.join(AUTO_CHAIN)}",
            engine=preferred,
            scheduled_version=ENGINE_SCHEDULED_VERSIONS[preferred],
        )
    raise KeyError(f"unknown engine {preferred!r}; known: {sorted(set(ENGINE_REGISTRY) | {'auto'})}")


@dataclass
class FetchOutcome:
    """Per-source fetch result: chosen engine, items, skip reason, failures."""

    source: str
    engine: str | None = None
    items: list[dict] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None  # 变更指纹 skip 是正常路径,必须可见
    failures: list[EngineFailure] = field(default_factory=list)


async def fetch_source(source: SourceConfig, context: FetchContext) -> FetchOutcome:
    """Run one source through its degrade chain (hint-first), isolating failures.

    The chain never raises for engine-level failures — every attempt is
    recorded as a structured :class:`EngineFailure`; per-source isolation for
    the rest of the category is the pipeline layer's job.
    """
    outcome = FetchOutcome(source=source.name)
    try:
        chain = auto_degrade(source.engine)
    except FetchError as exc:
        outcome.failures.append(
            EngineFailure(source.name, source.engine, source.url, classify_exception(exc), str(exc))
        )
        logger.error("引擎链不可用 source=%s engine=%s: %s", source.name, source.engine, exc)
        return outcome
    source_key = source.url  # v0.1 convention (store.base: source URL)
    hint = context.store.get_engine_hint(source_key) if context.store is not None else None
    if hint and hint in chain:
        chain = [hint] + [name for name in chain if name != hint]
        logger.debug("引擎提示命中 source=%s hint=%s", source.name, hint)
    for engine_name in chain:
        try:
            engine_cls = resolve_engine(engine_name)
            engine = engine_cls(source, context)
            items = await engine.fetch()
        except Exception as exc:  # 单引擎失败不拖垮源,记录后继续降级
            failure = EngineFailure(
                source=source.name,
                engine=engine_name,
                url=source.url,
                error_type=classify_exception(exc),
                message=str(exc),
            )
            outcome.failures.append(failure)
            logger.warning(
                "引擎尝试失败 source=%s engine=%s error_type=%s: %s",
                source.name, engine_name, failure.error_type, failure.message,
            )
            logger.debug("引擎异常详情 source=%s engine=%s", source.name, engine_name, exc_info=exc)
            # 代理挂 ≠ 源死(proxy-transport PRD:降级链决策依据不同):
            # proxy_* 失败不清 hint——引擎选择没错,是出口断了;一次代理抖动
            # 抹掉已习得的 hint 会让源在代理恢复前每轮全链重探。
            if (
                context.store is not None
                and hint == engine_name
                and not failure.error_type.startswith("proxy_")
            ):
                context.store.clear_engine_hint(source_key)
                logger.info("引擎提示已清除(失效回落) source=%s engine=%s", source.name, engine_name)
            if failure.error_type.startswith("proxy_"):
                # 链上其余引擎骑同一条池 transport,必然同错:继续降级只会重复
                # 烧完每级 retry+退避。短路并保留 hint,代理恢复后由成功者回写。
                logger.error(
                    "代理链路失败,中止降级链(其余引擎共享同一 proxy transport) "
                    "source=%s engine=%s error_type=%s",
                    source.name, engine_name, failure.error_type,
                )
                break
            continue
        if context.store is not None:
            context.store.set_engine_hint(source_key, engine_name)
        outcome.engine = engine_name
        outcome.items = items
        outcome.skipped = engine.last_skip_reason is not None
        outcome.skip_reason = engine.last_skip_reason
        logger.info(
            "采集成功 source=%s engine=%s items=%s%s",
            source.name,
            engine_name,
            len(items),
            f" skip={engine.last_skip_reason}" if engine.last_skip_reason else "",
        )
        return outcome
    logger.error(
        "所有引擎均失败 source=%s attempts=%s",
        source.name,
        [failure.to_dict() for failure in outcome.failures],
    )
    return outcome
