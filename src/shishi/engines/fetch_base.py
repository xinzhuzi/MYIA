"""Shared fetch-engine base: politeness, retry, change fingerprint, robots.

Everything the six engine layers share lives here once (PRD 10-01-v01-fetch-base):

- politeness: per-host QPS rate limiting with jitter, same-host sources merged
  onto one limiter (strictest qps wins), exponential/linear backoff with
  bounded retries on 429/5xx/transport errors, robots.txt respect (fetch +
  cache + parse via stdlib ``urllib.robotparser``);
- change fingerprint: ETag / Last-Modified negotiation first (304 short-cut,
  conditional requests), normalized-body hash fallback; baselines are read and
  written through the pluggable ``Store`` (change_baseline table);
- credentials: ``env:VAR`` / ``keychain:NAME`` header references resolved at
  engine construction — keychain goes through :mod:`shishi.secrets` (macOS
  Keychain / Windows DPAPI); tests inject a mock backend via
  :attr:`FetchContext.keychain_backend`;
- proxy: ``direct`` and (v0.2 task v02-proxy-transport) ``pool:<name>`` take
  effect — pools are declared once in the global config
  (:class:`ProxyPools` via :func:`load_proxy_pools_file`, credentials only as
  ``env:``/``keychain:`` references) and each pool mounts ONE shared proxied
  ``httpx.AsyncClient``; failures on the proxy chain are classified apart
  from source failures (代理挂 ≠ 源死,降级链决策依据不同).
  ``residential:`` stays a structured not-implemented error (v0.3,
  pool rotation with the myia-proxy plugin).

Extraction helpers (``json_path`` for APIs, CSS ``list``/``item`` for HTML,
``rss`` for feeds via feedparser) also live here so direct_api / static_html /
firecrawl reuse one implementation instead of three divergent ones.

All I/O is async (httpx.AsyncClient injected via :class:`FetchContext`);
clocks and sleepers are injectable so tests never really wait.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
import yaml
from selectolax.parser import HTMLParser

from shishi.dedup import DedupRegistry
from shishi.schema import (  # noqa: F401 - _UniqueKeyLoader 复用其重复键拒载行为
    ExtractConfig,
    RateLimitConfig,
    SchemaValueError,
    SourceConfig,
    CredentialResolveError,
    LoadError,
    LoadErrorDetail,
    _UniqueKeyLoader,
    parse_secret_value,
    resolve_credential,
)
from shishi.secrets import KeychainBackend
from shishi.store import Store

logger = logging.getLogger(__name__)

# 后端端点解析值不落日志(llm_browser/firecrawl 模块契约同旨):httpx 自身的
# INFO 请求行("HTTP Request: POST https://host/...")内嵌完整 URL —— 含凭据
# 引用解析出的后端端点 —— 本模块的脱敏盖不住第三方 logger,只能把它的 INFO
# 压掉;请求生命周期日志由 myia 自己的 logger 负责(logging.md 级别语义)。
logging.getLogger("httpx").setLevel(logging.WARNING)

__all__ = [
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_L3_PROBE_BUDGET",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "DEFAULT_PROXY_PROBE_URL",
    "KEY_ROTATE_STATUS_CODES",
    "RETRYABLE_STATUS_CODES",
    "SUPPORTED_PROXY_SCHEMES",
    "BaseEngine",
    "ChangeDetector",
    "ChangeVerdict",
    "EngineFailure",
    "EngineNotAvailableError",
    "ExtractionError",
    "FetchContext",
    "FetchError",
    "HostLimiterRegistry",
    "ProxyCheckResult",
    "ProxyConfigError",
    "ProxyNotSupportedError",
    "ProxyPools",
    "ProxyTransportError",
    "RateLimiter",
    "RobotsCache",
    "RobotsDisallowedError",
    "classify_exception",
    "classify_proxy_transport",
    "check_proxy_connectivity",
    "content_hash",
    "decode_response",
    "expand_proxy_url",
    "extract_html",
    "extract_json",
    "extract_rss",
    "json_path_resolve",
    "load_proxy_pools",
    "load_proxy_pools_file",
    "mask_proxy_url",
    "mask_endpoint_url",
    "normalize_text",
    "resolve_headers",
    "resolve_proxy",
    "split_attr_selector",
]

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "MYIA/0.1 (config-driven intelligence hub)"
BACKOFF_CAP_SECONDS = 60.0

#: 单 run L3 零结果探测上限(源数,10-04-crawl4ai-l3 拍板②):auto 链上
#: static_html 真零结果的首遇源降级 crawl4ai 探测一次,每 run 最多这么多源——
#: 浏览器冷启是真实开销,预算封顶防止一个全是 JS 壳的配置把首跑拖垮。
DEFAULT_L3_PROBE_BUDGET = 3

# Retried with backoff (transient); other 4xx are permanent, single attempt.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# DirectAPI key-pool rotation kicks in on these (auth/quota rejections).
KEY_ROTATE_STATUS_CODES = frozenset({401, 403, 429})

# grill Q4 (2026-10-01): pool -> single upstream transport shipped in v0.2
# (task v02-proxy-transport); residential -> pool rotation (v0.3, myia-proxy
# plugin) and stays a structured not-implemented error until then.
PROXY_SCHEDULE: dict[str, tuple[str, str]] = {
    "residential": ("v0.3", "住宅代理池轮换"),
}

# Proxy upstream URL schemes httpx accepts (socks5h resolves DNS at the proxy;
# socks support comes from the httpx[socks] extra, pyproject 已声明).
SUPPORTED_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")

# Lightweight unauthenticated endpoint for ``myia doctor`` connectivity probes
# (answers with the proxy egress IP as ``{"ip": ...}`` JSON; the pipeline
# itself never calls it).
DEFAULT_PROXY_PROBE_URL = "https://api.ipify.org/?format=json"

# Pool-name grammar — identical to the schema's ``pool:<名称>`` proxy value
# (schema.py _PROXY_RE), so a name accepted by the YAML is accepted here.
PROXY_POOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.IGNORECASE
)
_BOM_CANDIDATES: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
# Legacy Chinese forums serve gb18030/gbk/big5 without usable headers; utf-8
# runs first (strict) so modern sites win, and the final pass never raises.
_DECODE_FALLBACKS = ("utf-8", "gb18030", "big5")
# Only url-ish attributes get resolved against the page URL.
_URL_ATTRIBUTES = frozenset({"href", "src"})


# ---------------------------------------------------------------------------
# Structured errors (error_type feeds EngineFailure / doctor; messages 中文)
# ---------------------------------------------------------------------------


class FetchError(RuntimeError):
    """Structured fetch-layer failure.

    Attributes:
        error_type: machine-readable class (network / parse / robots_disallowed
            / ...), consumed by the degrade chain and ``myia doctor``.
    """

    def __init__(self, message: str, *, error_type: str = "fetch_error") -> None:
        super().__init__(message)
        self.error_type = error_type


class RobotsDisallowedError(FetchError):
    """robots.txt forbids the URL; the source is skipped and recorded."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="robots_disallowed")


class ProxyNotSupportedError(FetchError):
    """A proxy kind that is parsed but not implemented (residential: v0.3)."""

    def __init__(self, message: str, *, proxy: str, scheduled_version: str) -> None:
        super().__init__(message, error_type="proxy_not_implemented")
        self.proxy = proxy
        self.scheduled_version = scheduled_version


class ProxyConfigError(FetchError):
    """Proxy pool configuration problem (声明缺失/池名未知/URL 非法/依赖缺失).

    Attributes:
        code: ``proxy_pools_not_configured`` | ``proxy_pool_unknown`` |
            ``invalid_proxy_url`` | ``proxy_dependency_missing``.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, error_type="proxy_config")
        self.code = code


class ProxyTransportError(FetchError):
    """A failure on the proxy chain (代理挂 ≠ 源死,降级链决策依据不同).

    Raised only for requests that actually rode a proxied transport; the
    source behind it may be perfectly healthy, so ``proxy_*`` error classes
    are consumed differently from ``network`` / ``http_*`` / ``timeout``.

    Attributes:
        error_type: ``proxy_error`` (代理握手/认证失败) | ``proxy_timeout`` |
            ``proxy_network`` (代理链路网络错误) — see
            :func:`classify_proxy_transport`.
        pool: the pool name the failing transport was mounted for.
    """

    def __init__(self, message: str, *, error_type: str, pool: str) -> None:
        super().__init__(message, error_type=error_type)
        self.pool = pool


class EngineNotAvailableError(FetchError):
    """An explicitly selected engine scheduled for a later version."""

    def __init__(self, message: str, *, engine: str, scheduled_version: str) -> None:
        super().__init__(message, error_type="engine_not_available")
        self.engine = engine
        self.scheduled_version = scheduled_version


class ExtractionError(FetchError):
    """Extraction could not run (bad json_path syntax, missing html format)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="parse")


def classify_exception(exc: BaseException) -> str:
    """Map an exception to a machine error class for logs / store / doctor."""
    if isinstance(exc, FetchError):
        return exc.error_type
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "network"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, CredentialResolveError):
        return "credentials"
    if isinstance(exc, json.JSONDecodeError):
        return "json_decode"
    return "unknown"


def classify_proxy_transport(exc: BaseException) -> str:
    """Classify a transport failure that rode a proxied client.

    ``httpx.ProxyError`` (代理握手/认证被拒/隧道建立失败) -> ``proxy_error``;
    timeouts -> ``proxy_timeout``; every other transport error ->
    ``proxy_network``. The proxy_* classes are the degrade chain's signal that
    the *source* is not necessarily dead — 换代理或修代理,别急着拉黑源。
    """
    if isinstance(exc, httpx.ProxyError):
        return "proxy_error"
    if isinstance(exc, httpx.TimeoutException):
        return "proxy_timeout"
    if isinstance(exc, httpx.TransportError):
        return "proxy_network"
    return "network"


@dataclass(frozen=True)
class EngineFailure:
    """One structured engine attempt failure (source / engine / error class)."""

    source: str
    engine: str
    url: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "engine": self.engine,
            "url": self.url,
            "error_type": self.error_type,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Credentials & proxy
# ---------------------------------------------------------------------------


def resolve_headers(
    headers: Mapping[str, str],
    *,
    backend: KeychainBackend | None = None,
) -> dict[str, str]:
    """Resolve credential references in header values; plain values pass through.

    ``Cookie: env:LINUXSB_COOKIE``, ``Cookie: keychain:myia/stocks/name`` and
    ``Authorization: Bearer env:T`` expand at engine construction; keychain
    names go through :mod:`shishi.secrets` (canonical ``myia/<scope>/<name>``
    namespace). ``User-Agent``-style plain values are untouched.

    Args:
        headers: raw header mapping from the source config.
        backend: injected keychain backend (``FetchContext.keychain_backend``
            in production wiring); ``None`` = system keyring discovery.

    Returns:
        Headers with every credential reference replaced by its value
        (值不落日志).

    Raises:
        CredentialResolveError: env var missing, the keychain name is not in
            the canonical namespace / not set / no backend (structured, with
            ``env:`` guidance), or a malformed reference.
    """
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        try:
            parse_secret_value(value, label=f"请求头 {key}")
        except SchemaValueError:
            resolved[key] = value
        else:
            resolved[key] = resolve_credential(value, backend=backend)
            logger.debug("请求头 %s 凭据引用已解析(值不落日志)", key)
    return resolved


def resolve_proxy(proxy: str) -> None:
    """Validate the source proxy setting (grammar + schedule gate).

    ``direct`` passes; ``pool:<名称>`` is valid since v0.2 — the pool itself
    resolves against the global :class:`ProxyPools` declaration at engine
    fetch time (undeclared pool / unresolvable credential ->
    :class:`ProxyConfigError` / :class:`CredentialResolveError`, still before
    any network I/O). ``residential:`` stays not implemented.

    Raises:
        ProxyNotSupportedError: ``residential:`` — structured not-implemented
            error carrying the scheduled version.
        FetchError: an unrecognized proxy value (schema already rejects these
            at load time; this is defense in depth).
    """
    if not proxy or proxy == "direct":
        return
    kind = proxy.split(":", 1)[0]
    if kind == "pool":
        return  # v0.2 生效:fetch 时经 ProxyPools 解析(未声明/未配凭据 -> 结构化报错)
    if kind in PROXY_SCHEDULE:
        version, note = PROXY_SCHEDULE[kind]
        raise ProxyNotSupportedError(
            f"proxy {proxy!r} 未实装:{note}规划于 {version} 支持,"
            f"当前请改用 direct 或 pool:<名称>(全局配置 pools 声明)",
            proxy=proxy,
            scheduled_version=version,
        )
    raise FetchError(
        f"proxy 取值 {proxy!r} 无法识别(应为 direct / pool:<名称> / residential:<区域>)",
        error_type="invalid_proxy",
    )


# ---------------------------------------------------------------------------
# Proxy transport (v0.2: pools 全局声明 + 品类引用,单上游挂载)
# ---------------------------------------------------------------------------


def _split_proxy_url(raw: str) -> tuple[str, str | None, str]:
    """Proxy URL -> ``(scheme, userinfo | None, hostport)``.

    urllib's URL parser ends the authority at the first ``/``, which truncates
    a keychain userinfo (``socks5://keychain:myia/proxy/main@h:1080`` would
    yield netloc ``keychain:myia``). Credential references never contain
    ``@``, so the userinfo is everything before the LAST ``@`` of the URL and
    the hostport is what follows it (any path/query/fragment suffix is
    dropped — proxy upstreams have none). A userinfo that mis-splits on a
    stray ``@`` elsewhere fails reference validation downstream: fail-closed,
    never silently mis-resolved.

    Raises:
        ProxyConfigError: missing ``scheme://`` prefix or empty authority
            (``invalid_proxy_url``).
    """
    scheme, sep, rest = raw.partition("://")
    if not sep or not scheme:
        raise ProxyConfigError(
            f"代理 URL 缺少 scheme:// 前缀,当前为 {mask_proxy_url(raw)!r}",
            code="invalid_proxy_url",
        )
    if "@" in rest:
        userinfo, _, after = rest.rpartition("@")
        hostport = re.split(r"[/?#]", after, maxsplit=1)[0]
        return scheme, (userinfo or None), hostport
    hostport = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if not hostport:
        raise ProxyConfigError(
            f"代理 URL 缺少 host,当前为 {mask_proxy_url(raw)!r}", code="invalid_proxy_url"
        )
    return scheme, None, hostport


# userinfo = everything between ``://`` and the LAST ``@`` (引用不含 @).
_MASK_USERINFO_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://).*@")


def mask_proxy_url(proxy_url: str) -> str:
    """``http://user:pass@host:8080`` -> ``http://***@host:8080``(凭据不落日志).

    Pure regex over the last ``@`` — deliberately independent of URL parsing
    so it stays safe to call from error paths of unparseable input.
    """
    return _MASK_USERINFO_RE.sub(r"\g<scheme>***@", proxy_url)


def mask_endpoint_url(url: str) -> str:
    """``https://host:port/v1/x`` -> ``https://<endpoint>/v1/x``(后端端点不落日志).

    引擎后端 endpoint(llm_browser/firecrawl)是凭据引用:``scheme://host:port``
    段一律换成 ``<endpoint>``,只保留 path 供排障;query/fragment 整段丢弃
    (错误路径日志宁缺勿滥)。解析值不落日志 —— 展示形态(引用名/内置缺省)
    由引擎的就绪行负责。
    """
    parts = urlsplit(url)
    if not parts.netloc:
        return "<endpoint>"
    return urlunsplit((parts.scheme, "<endpoint>", parts.path, "", ""))


def expand_proxy_url(raw: str, *, backend: KeychainBackend | None = None) -> str:
    """Resolve ``env:``/``keychain:`` references in a proxy URL's userinfo.

    URL 语法(声明于全局配置 pools 节;凭据只走引用,明文在加载期拒载)::

        http://proxy.example.com:8080                            匿名上游
        http://env:MYIA_PROXY_MAIN@proxy.example.com:8080        单引用 = user:pass 整段
        http://env:MYIA_PU:env:MYIA_PP@proxy.example.com:8080    两段引用 = 用户 / 密码
        socks5://keychain:myia/proxy/main@proxy.example.com:1080 钥匙链引用

    Resolved values are percent-encoded into the returned concrete URL; the
    concrete values never reach logs (展示一律过 :func:`mask_proxy_url`).

    Args:
        raw: the configured proxy URL (credentials as references).
        backend: injected keychain backend for ``keychain:`` references
            (``None`` = system keyring discovery), same contract as
            :func:`shishi.schema.resolve_credential`.

    Returns:
        The concrete proxy URL, ready for ``httpx.AsyncClient(proxy=...)``.

    Raises:
        ProxyConfigError: URL 无 scheme:// 前缀,或凭据段不是合法引用组合
            (含明文,``invalid_proxy_url``)。
        CredentialResolveError: a reference is malformed or unresolvable
            (env 变量缺失 / 钥匙链未写入).
    """
    scheme, userinfo, hostport = _split_proxy_url(raw)
    if userinfo is None:
        return raw  # 匿名上游,无凭据段
    try:
        refs = _split_proxy_credential_refs(userinfo)
    except SchemaValueError as exc:
        raise ProxyConfigError(str(exc), code="invalid_proxy_url") from exc
    resolved = [resolve_credential(ref, backend=backend) for ref in refs]
    if len(resolved) == 1:
        user, _, password = resolved[0].partition(":")
    else:
        user, password = resolved
    auth = quote(user, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    return f"{scheme}://{auth}@{hostport}"


def _split_proxy_credential_refs(userinfo: str) -> list[str]:
    """Split a proxy URL userinfo into 1-2 credential references (只做语法解析).

    The reference grammar itself contains colons (``env:VAR``), so naive
    colon-splitting cannot work; instead the whole userinfo is tried as ONE
    reference (the whole ``user:pass`` segment) first, then every colon
    position as a 用户引用:密码引用 pair. Grammar stays canonical in
    :func:`shishi.schema.parse_secret_value` — 这里零重复定义.

    Raises:
        SchemaValueError: the userinfo is neither a valid single reference nor
            a valid reference pair (含明文凭据,code ``credential_plaintext``).
    """
    try:
        parse_secret_value(userinfo, label="代理凭据段", allow_scheme=False)
    except SchemaValueError:
        pass
    else:
        return [userinfo]
    for index, char in enumerate(userinfo):
        if char != ":":
            continue
        first, second = userinfo[:index], userinfo[index + 1 :]
        try:
            parse_secret_value(first, label="代理凭据段(用户)", allow_scheme=False)
            parse_secret_value(second, label="代理凭据段(密码)", allow_scheme=False)
        except SchemaValueError:
            continue
        return [first, second]
    raise SchemaValueError(
        "credential_plaintext",
        "代理 URL 凭据段只允许 env:/keychain: 引用 —— 单引用(user:pass 整段)或"
        " 用户引用:密码引用 两段;其余形态(含明文)一律拒绝",
    )


class ProxyPools:
    """Global proxy pool declaration: name -> upstream URL (凭据为引用).

    Declared once in the global config (:func:`load_proxy_pools` /
    :func:`load_proxy_pools_file`), referenced from category YAML as
    ``proxy: pool:<name>`` and injected into :attr:`FetchContext.proxy_pools`.
    品类 YAML 永不出现代理凭据 —— 引用即全部(安全红线)。
    """

    def __init__(self, pools: Mapping[str, str]) -> None:
        self._pools: dict[str, str] = dict(pools)

    def names(self) -> list[str]:
        """Declared pool names, sorted (doctor 展示用)."""
        return sorted(self._pools)

    def __contains__(self, name: object) -> bool:
        return name in self._pools

    def raw_url(self, name: str) -> str:
        """The configured URL as written (凭据仍是引用;展示需过 :func:`mask_proxy_url`).

        Raises:
            ProxyConfigError: the pool name is not declared
                (``proxy_pool_unknown``).
        """
        try:
            return self._pools[name]
        except KeyError:
            raise ProxyConfigError(
                f"代理池 {name!r} 未在全局配置 pools 节声明(已声明: {self.names() or '无'});"
                f"请在全局配置添加 pools.{name}: <代理URL>,或将源 proxy 改为 direct",
                code="proxy_pool_unknown",
            ) from None

    def resolve(self, name: str, *, backend: KeychainBackend | None = None) -> str:
        """Expand the pool URL to its concrete form (凭据解析;值不落日志).

        Args:
            name: declared pool name.
            backend: injected keychain backend (tests: mock; ``None`` =
                system keyring discovery).

        Raises:
            ProxyConfigError: pool name not declared.
            CredentialResolveError: a credential reference does not resolve.
        """
        return expand_proxy_url(self.raw_url(name), backend=backend)


def _validate_pool_url(raw_url: str, path: str) -> list[LoadErrorDetail]:
    """One pool URL's load-time rules (syntax only;凭据解析延后到 fetch 时).

    Checks: ``scheme://`` 前缀 + host 存在,scheme ∈ :data:`SUPPORTED_PROXY_SCHEMES`,
    userinfo(如有)恰为单引用或 用户引用:密码引用 两段。明文凭据在这里拒载
    (安全红线: 配置文件明文凭据 = 启动即报错拒跑)。
    """
    try:
        scheme, userinfo, hostport = _split_proxy_url(raw_url)
    except ProxyConfigError as exc:
        return [LoadErrorDetail(path, exc.code, str(exc))]
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        return [
            LoadErrorDetail(
                path,
                "unsupported_proxy_scheme",
                f"代理协议 {scheme!r} 不支持(允许 {'/'.join(SUPPORTED_PROXY_SCHEMES)})",
            )
        ]
    if urlsplit(f"//{hostport}").hostname is None:
        return [LoadErrorDetail(path, "invalid_proxy_url", f"代理 URL 缺少 host,当前为 {raw_url!r}")]
    if userinfo is None:
        return []
    try:
        _split_proxy_credential_refs(userinfo)
    except SchemaValueError as exc:
        return [LoadErrorDetail(path, exc.code, str(exc))]
    return []


def load_proxy_pools(data: Mapping[str, Any], *, source: str | None = None) -> ProxyPools:
    """Validate a global-config mapping's ``pools`` declaration.

    Global config shape(文档即契约;品类 YAML 只写 ``proxy: pool:<名称>`` 引用,
    不重复声明)::

        # 其余顶层节(未来的 CLI 设置等)由各自加载器消费,本加载器只提取 pools
        pools:
          main: "http://env:MYIA_PROXY_MAIN@proxy.example.com:8080"
          socks: "socks5://keychain:myia/proxy/socks@proxy.example.com:1080"

    Per-pool rules:名称须匹配 ``[A-Za-z0-9_-]+``(与 schema 的 ``pool:<名称>``
    语法一致);scheme 限 http/https/socks5/socks5h;必须带 host;userinfo 只允许
    ``env:``/``keychain:`` 引用。每条失败都是结构化 :class:`LoadError`
    (字段路径 + 错误类 + 中文原因,CLI 退出码 1 / doctor JSON 可消费)。

    Args:
        data: parsed global-config mapping (``yaml.safe_load`` output).
        source: optional file path carried on :class:`LoadError`.

    Returns:
        :class:`ProxyPools` — possibly empty: 全局配置可以不声明 pools,
        此时品类里的 ``pool:<名称>`` 引用在 fetch 前结构化报错。

    Raises:
        LoadError: the ``pools`` section exists but is malformed.
    """
    if not isinstance(data, Mapping):
        raise LoadError(
            [
                LoadErrorDetail(
                    "$", "invalid_root", f"全局配置必须是键值映射,当前为 {type(data).__name__}"
                )
            ],
            source=source,
        )
    if "pools" not in data:
        return ProxyPools({})
    pools_data = data["pools"]
    if not isinstance(pools_data, Mapping):
        raise LoadError(
            [
                LoadErrorDetail(
                    "$.pools",
                    "invalid_pools",
                    f"pools 节必须是键值映射,当前为 {type(pools_data).__name__}",
                )
            ],
            source=source,
        )
    errors: list[LoadErrorDetail] = []
    for name, raw_url in pools_data.items():
        path = f"$.pools.{name}"
        if not isinstance(name, str) or not PROXY_POOL_NAME_RE.match(name):
            errors.append(
                LoadErrorDetail(
                    path,
                    "invalid_pool_name",
                    f"代理池名只允许字母/数字/连字符/下划线,当前为 {name!r}",
                )
            )
            continue
        if not isinstance(raw_url, str) or not raw_url.strip():
            errors.append(
                LoadErrorDetail(path, "invalid_proxy_url", f"代理 URL 必须是非空字符串,当前为 {raw_url!r}")
            )
            continue
        errors.extend(_validate_pool_url(raw_url, path))
    if errors:
        raise LoadError(errors, source=source)
    return ProxyPools({str(name): str(url) for name, url in pools_data.items()})


def load_proxy_pools_file(path: str | Path) -> ProxyPools:
    """Read the global config file and validate its ``pools`` declaration.

    默认文件定位由 CLI 任务(v02-cli-full)决定;本函数只负责读取+校验。
    错误映射与 :func:`shishi.schema.load_category_file` 同契约,重复键同样拒载
    (复用 schema 的 :class:`_UniqueKeyLoader`,私有名引用是有意为之)。

    Args:
        path: global config file path (UTF-8 YAML).

    Returns:
        :class:`ProxyPools` (empty file / 无 pools 节 -> empty declaration).

    Raises:
        LoadError: file missing/unreadable/not UTF-8/YAML broken (含重复键)/
            pools 节不合法。
    """
    file_path = Path(path)
    source = str(file_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LoadError(
            [
                LoadErrorDetail(
                    "$",
                    "invalid_encoding",
                    f"全局配置文件不是有效的 UTF-8 编码({source}): {exc}(请另存为 UTF-8 后重试)",
                )
            ],
            source=source,
        ) from exc
    except OSError as exc:
        error_type = "file_not_found" if isinstance(exc, FileNotFoundError) else "file_read_error"
        raise LoadError(
            [LoadErrorDetail("$", error_type, f"无法读取全局配置文件 {source}: {exc}")],
            source=source,
        ) from exc
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise LoadError(
            [LoadErrorDetail("$", "yaml_parse_error", f"全局配置 YAML 语法无法解析: {exc}")],
            source=source,
        ) from exc
    if data is None:
        return ProxyPools({})  # 空文件 = 未声明任何池(合法)
    return load_proxy_pools(data, source=source)


@dataclass(frozen=True)
class ProxyCheckResult:
    """One proxy connectivity probe (``myia doctor`` 消费形态,to_dict 即 JSON).

    凭据永不入结果:上游只以 :func:`mask_proxy_url` 形态出现。
    """

    ok: bool
    message: str
    proxy_url_masked: str
    latency_seconds: float | None = None
    status_code: int | None = None
    exit_ip: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form for ``myia doctor --json`` and agents."""
        return {
            "ok": self.ok,
            "message": self.message,
            "proxy_url": self.proxy_url_masked,
            "latency_seconds": self.latency_seconds,
            "status_code": self.status_code,
            "exit_ip": self.exit_ip,
            "error_type": self.error_type,
        }


async def check_proxy_connectivity(
    proxy_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    probe_url: str = DEFAULT_PROXY_PROBE_URL,
    timeout: float = 10.0,
    clock: Callable[[], float] = time.monotonic,
) -> ProxyCheckResult:
    """Probe one proxy upstream with a lightweight GET (doctor 诊断项,已接线于 cli.py doctor 的代理池探测).

    CLI usage: ``result = await check_proxy_connectivity(pools.resolve(name))``
    per declared pool, then render ``result.to_dict()`` (JSON 友好,凭据已打码).
    The pipeline itself never calls this.

    Args:
        proxy_url: concrete upstream URL — pass the :meth:`ProxyPools.resolve`
            output (references already expanded); 凭据值不进结果/日志.
        client: injected ``httpx.AsyncClient`` (tests: MockTransport);
            ``None`` -> one is built on ``proxy_url`` and closed before
            returning.
        probe_url: lightweight unauthenticated endpoint; default answers the
            proxy egress IP as ``{"ip": ...}`` JSON, parsed into
            :attr:`ProxyCheckResult.exit_ip` when present.
        timeout: probe budget in seconds.
        clock: injectable monotonic clock (tests pin the latency).

    Returns:
        :class:`ProxyCheckResult` — ``ok`` means the probe answered with a
        sub-400 status through the proxy; proxy-chain failures carry the
        :func:`classify_proxy_transport` class (proxy_error / proxy_timeout /
        proxy_network), HTTP-level rejections an ``http_<status>`` class.
    """
    masked = mask_proxy_url(proxy_url)
    started = clock()
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(proxy=proxy_url, timeout=timeout)
    try:
        try:
            response = await client.get(probe_url, timeout=timeout)
        except httpx.TimeoutException as exc:
            return ProxyCheckResult(
                False,
                f"代理探测超时(>{timeout:.0f}s): {exc}",
                masked,
                latency_seconds=round(clock() - started, 3),
                error_type="proxy_timeout",
            )
        except httpx.TransportError as exc:
            return ProxyCheckResult(
                False,
                f"代理链路失败: {exc}",
                masked,
                latency_seconds=round(clock() - started, 3),
                error_type=classify_proxy_transport(exc),
            )
        latency = round(clock() - started, 3)
        if response.status_code >= 400:
            return ProxyCheckResult(
                False,
                f"探测端点返回 HTTP {response.status_code}",
                masked,
                latency_seconds=latency,
                status_code=response.status_code,
                error_type=f"http_{response.status_code}",
            )
        exit_ip: str | None = None
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, Mapping) and isinstance(payload.get("ip"), str):
            exit_ip = payload["ip"]
        return ProxyCheckResult(
            True,
            "代理连通",
            masked,
            latency_seconds=latency,
            status_code=response.status_code,
            exit_ip=exit_ip,
        )
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Rate limiting (per-host merge: 同域多源取最严 qps)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Async QPS limiter: minimum interval between acquires + uniform jitter.

    Concurrency-safe without a lock: each acquirer *reserves* its release slot
    (``_next_free``) before suspending, so N coroutines arriving together space
    out at ``_min_interval`` instead of all reading the same ``_last`` and
    bursting (同域多源合并限速在并发下才真正生效).
    """

    def __init__(
        self,
        qps: float,
        jitter: float = 0.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if qps <= 0:
            raise ValueError(f"qps 必须为正数,得到 {qps}")
        if jitter < 0:
            raise ValueError(f"jitter 不能为负数,得到 {jitter}")
        self._qps = float(qps)
        self._jitter = float(jitter)
        self._min_interval = 1.0 / self._qps
        self._clock = clock
        self._sleeper = sleeper
        self._next_free: float | None = None
        self._rng = random.Random()

    @property
    def qps(self) -> float:
        return self._qps

    @property
    def jitter(self) -> float:
        return self._jitter

    def tighten(self, qps: float, jitter: float) -> None:
        """Merge another source's limits keeping the strictest (min qps / max jitter)."""
        if qps <= 0:
            raise ValueError(f"qps 必须为正数,得到 {qps}")
        if jitter < 0:
            raise ValueError(f"jitter 不能为负数,得到 {jitter}")
        self._qps = min(self._qps, float(qps))
        self._min_interval = 1.0 / self._qps
        self._jitter = max(self._jitter, float(jitter))

    async def acquire(self) -> float:
        """Wait until the next request is polite; return the waited seconds.

        The release slot is reserved *before* the (awaitable) sleep, so
        concurrent acquirers queue up at ``_min_interval`` spacing instead of
        waking together (无锁并发安全:挂起期间到达的协程会排到更晚的槽位).
        """
        now = self._clock()
        base = self._next_free if self._next_free is not None else now
        wait = max(0.0, base - now)
        if self._jitter:
            wait += self._rng.uniform(0.0, self._jitter)
        self._next_free = base + self._min_interval
        if wait > 0:
            await self._sleeper(wait)
        return wait


class HostLimiterRegistry:
    """In-process per-host limiter registry: same-host sources share one
    limiter, merged to the strictest qps / largest jitter."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._limiters: dict[str, RateLimiter] = {}

    def limiter_for(self, host: str, rate_limit: RateLimitConfig) -> RateLimiter:
        if not host:
            raise ValueError("字段校验失败: 限速 host 不能为空")
        existing = self._limiters.get(host)
        if existing is None:
            limiter = RateLimiter(
                rate_limit.qps, rate_limit.jitter, clock=self._clock, sleeper=self._sleeper
            )
            self._limiters[host] = limiter
            logger.debug("限速器注册 host=%s qps=%s", host, rate_limit.qps)
            return limiter
        if existing.qps != rate_limit.qps or existing.jitter != rate_limit.jitter:
            existing.tighten(rate_limit.qps, rate_limit.jitter)
            logger.debug(
                "同域限速合并 host=%s 合并后 qps=%s jitter=%s", host, existing.qps, existing.jitter
            )
        return existing


# ---------------------------------------------------------------------------
# robots.txt (stdlib parser; fetch failure fails open by convention)
# ---------------------------------------------------------------------------


class RobotsCache:
    """robots.txt fetch + per-origin cache + parse (``urllib.robotparser``).

    Fetch errors / missing robots.txt fail OPEN (no rules = everything allowed,
    the RFC-9309 convention); the failure is logged so run records stay honest.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = await self._fetch(f"{origin}/robots.txt")
        parser = self._parsers[origin]
        if parser is None:
            return True
        allowed = parser.can_fetch(user_agent, url)
        if not allowed:
            logger.warning("robots.txt 禁止抓取,源将跳过 url=%s", url)
        return allowed

    async def _fetch(self, robots_url: str) -> RobotFileParser | None:
        try:
            response = await self._client.get(robots_url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt 拉取失败,按允许处理 url=%s error=%s", robots_url, exc)
            return None
        if response.status_code >= 400:
            logger.debug("robots.txt 不存在,按允许处理 url=%s status=%s", robots_url, response.status_code)
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        logger.debug("robots.txt 已解析 url=%s", robots_url)
        return parser


# ---------------------------------------------------------------------------
# Change fingerprint (增量抓取: 基线管变没变)
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Collapse whitespace runs and blank lines (模板噪声不进指纹)."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def content_hash(response: httpx.Response, *, decoded_text: str | None = None) -> str:
    """SHA-256 of the normalized body (decoded text when available, else raw)."""
    if decoded_text is None:
        return hashlib.sha256(response.content).hexdigest()
    return hashlib.sha256(normalize_text(decoded_text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChangeVerdict:
    """One change-fingerprint verdict (skip 是正常路径,reason 必须可见)."""

    changed: bool
    reason: str  # first_fetch | not_modified | validators_match/differ | hash_match/differ


class ChangeDetector:
    """Per-URL change fingerprint against the store's ``change_baseline``.

    Priority: ETag, then Last-Modified, then normalized body hash. A 304 is a
    negotiation hit — verdict unchanged and the baseline is left untouched
    (its body is empty and must not poison the stored hash).
    """

    def __init__(self, store: Store | None) -> None:
        self._store = store

    def check(
        self,
        url: str,
        response: httpx.Response,
        *,
        decoded_text: str | None = None,
    ) -> ChangeVerdict:
        baseline = self._store.get_baseline(url) if self._store is not None else None
        etag = response.headers.get("etag")
        last_modified = response.headers.get("last-modified")
        if response.status_code == 304:
            return ChangeVerdict(False, "not_modified")
        digest = content_hash(response, decoded_text=decoded_text)
        if baseline is None:
            verdict = ChangeVerdict(True, "first_fetch")
        elif baseline.etag and etag:
            verdict = ChangeVerdict(
                etag != baseline.etag,
                "validators_match" if etag == baseline.etag else "validators_differ",
            )
        elif baseline.last_modified and last_modified:
            verdict = ChangeVerdict(
                last_modified != baseline.last_modified,
                "validators_match" if last_modified == baseline.last_modified else "validators_differ",
            )
        elif baseline.content_hash is not None:
            verdict = ChangeVerdict(
                digest != baseline.content_hash,
                "hash_match" if digest == baseline.content_hash else "hash_differ",
            )
        else:
            # Baseline row exists but carries no comparable field yet.
            verdict = ChangeVerdict(True, "first_fetch")
        self._write_baseline(url, baseline, etag, last_modified, digest, verdict)
        return verdict

    def _write_baseline(
        self,
        url: str,
        baseline: Any,
        etag: str | None,
        last_modified: str | None,
        digest: str,
        verdict: ChangeVerdict,
    ) -> None:
        if self._store is None:
            return
        now = datetime.now(timezone.utc)
        # unchanged -> preserve the original last_changed; changed -> stamp now.
        last_changed = now if verdict.changed or baseline is None else baseline.last_changed
        self._store.set_baseline(
            url,
            etag=etag,
            last_modified=last_modified,
            content_hash=digest,
            last_changed=last_changed,
        )
        logger.debug("变更基线已更新 url=%s verdict=%s", url, verdict.reason)


# ---------------------------------------------------------------------------
# Extraction: json_path (APIs) and CSS list/item (HTML), shared by L1/L2/firecrawl
# ---------------------------------------------------------------------------
# extract.url_template (D1, task 10-03-games): payloads without a clickable
# page URL (Epic urlSlug / Steam numeric id) get their item URL rendered from
# ``{field}`` placeholders at the extraction outlets below.


def _render_url_template(template: str, item: Mapping[str, Any]) -> str:
    """Render one item's ``url_template`` ``{field}`` placeholders.

    复用 dedup 的迷你模板渲染器(:meth:`DedupRegistry.make_key`)——同一套
    Formatter 纯字段名语义,int 值渲染成 str(steam_id 形态)。dedup 渲染器对
    缺字段以 ValueError 拒绝,这里映射为空串:提取层保留该条目、不做半渲染
    的假 URL;空 url 条目随后在管线 fetch 阶段被 ``Item.from_extracted`` 记
    invalid_item 后丢弃(不带坏链接入库;schema 装载期已交叉校验占位符在
    fields 内并保证模板含占位符,运行期缺字段只剩逐条目缺「值」,如 Epic
    urlSlug=null 元素——R1 接受的损失面:该条目不产出,其余照常)。
    """
    try:
        return DedupRegistry.make_key(template, item)
    except ValueError as exc:
        logger.debug("url_template 占位字段缺值,条目 url 置空: %s", exc)
        return ""


def _apply_url_template(items: list[dict], extract: ExtractConfig) -> list[dict]:
    """提取出口统一填 url:url 字段值胜出,缺 url 才渲染模板(D1)。

    与 schema 的「url 字段或 url_template 二选一、都有 = url 字段胜出」
    对齐:fields 抽出的 url(非空)优先,模板静默不用;条目缺 url 键或为空
    才渲染。``type: item`` 配不了模板(schema 拒),这里天然 no-op。
    """
    template = extract.url_template
    if not template:
        return items
    for item in items:
        if item.get("url"):
            continue  # url 字段胜出(implement 步骤 1 定死)
        item["url"] = _render_url_template(template, item)
    return items

_JSONPATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][\w\-]*)|(\[\*\])|\[(\d+)\]")


def json_path_resolve(data: Any, path: str) -> list[Any]:
    """Evaluate the supported JSONPath subset; return every match.

    Supported: ``$`` root, ``.key``, ``[*]`` (array iteration), ``[n]`` (index).
    A path that dead-ends in the data yields ``[]`` (field miss, logged); only
    *syntax* errors raise.

    Raises:
        ExtractionError: the path is not ``$``-rooted or contains unsupported
            syntax.
    """
    if not path.startswith("$"):
        raise ExtractionError(f"JSONPath 必须以 $ 开头(仅支持 $.key / [*] / [n] 子集),当前为 {path!r}")
    tokens = _tokenize_jsonpath(path[1:], path)
    return _json_walk(data, tokens, path)


def _tokenize_jsonpath(rest: str, full_path: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(rest):
        match = _JSONPATH_TOKEN_RE.match(rest, pos)
        if match is None:
            raise ExtractionError(f"JSONPath {full_path!r} 含不支持的语法(仅支持 $.key / [*] / [n])")
        if match.group(1) is not None:
            tokens.append(("key", match.group(1)))
        elif match.group(2) is not None:
            tokens.append(("all", None))
        else:
            tokens.append(("index", int(match.group(3))))
        pos = match.end()
    return tokens


def _json_walk(node: Any, tokens: list[tuple[str, Any]], path: str) -> list[Any]:
    if not tokens:
        return [node]
    kind, arg = tokens[0]
    rest = tokens[1:]
    if kind == "key":
        if isinstance(node, dict) and arg in node:
            return _json_walk(node[arg], rest, path)
        logger.debug("JSONPath %s 未命中键 .%s", path, arg)
        return []
    if kind == "all":
        if not isinstance(node, list):
            logger.debug("JSONPath %s 的 [*] 未作用于数组", path)
            return []
        return [hit for element in node for hit in _json_walk(element, rest, path)]
    if isinstance(node, list) and 0 <= arg < len(node):
        return _json_walk(node[arg], rest, path)
    logger.debug("JSONPath %s 索引越界 [%s]", path, arg)
    return []


def extract_json(data: Any, extract: ExtractConfig) -> list[dict]:
    """Apply a ``json_path`` extract config to decoded JSON.

    When every field path resolves through the same array (identical prefix up
    to the last ``[*]``), fields are matched *per element*: an element missing
    one field keeps its other fields and simply omits the missing one. The
    previous index-zip shifted every later record by one whenever an element
    lacked a field (标题配到别人的 URL 的静默数据污染). Scalar paths (no
    ``[*]``, e.g. ``$.chart.result[0].meta.url``) and mixed prefixes fall back
    to per-field index zip (shorter lists omit the field for later indexes).

    When ``extract.url_template`` is set, each item's ``url`` is rendered from
    its fields at the outlet (a placeholder field missing on one element →
    empty url for that item, which the pipeline's fetch stage then rejects as
    ``invalid_item`` — no broken-link rows reach the store; an extracted
    ``url`` field value wins — D1, task 10-03-games).
    """
    splits: dict[str, tuple[list[tuple[str, Any]], list[tuple[str, Any]]] | None] = {}
    for name, path in extract.fields.items():
        tokens = _tokenize_jsonpath(path[1:], path)
        last_all = max((index for index, token in enumerate(tokens) if token[0] == "all"), default=-1)
        splits[name] = (tokens[: last_all + 1], tokens[last_all + 1 :]) if last_all >= 0 else None
    if splits and all(split is not None for split in splits.values()):
        prefixes = {tuple(prefix) for prefix, _suffix in splits.values() if prefix}
        if len(prefixes) == 1:
            (prefix,) = prefixes
            items: list[dict] = []
            for element in _json_walk(data, list(prefix), "?"):
                record = {
                    name: hits[0]
                    for name, (_prefix, suffix) in splits.items()
                    if (hits := _json_walk(element, list(suffix), extract.fields[name]))
                    and hits[0] is not None
                }
                if record:
                    items.append(record)
            return _apply_url_template(items, extract)
    matches = {name: json_path_resolve(data, path) for name, path in extract.fields.items()}
    longest = max((len(values) for values in matches.values()), default=0)
    items = []
    for index in range(longest):
        record = {
            name: values[index]
            for name, values in matches.items()
            if index < len(values) and values[index] is not None
        }
        if record:
            items.append(record)
    return _apply_url_template(items, extract)


def split_attr_selector(selector: str) -> tuple[str, str | None]:
    """``a.title@href`` -> ("a.title", "href"); plain selector -> (selector, None)."""
    if "@" in selector:
        css, _, attr = selector.rpartition("@")
        return css, attr
    return selector, None


#: 数值形态纯文本(可选货币前缀 + 千分位 + 小数);百分号/日期/版本号不收。
_NUMERIC_TEXT_RE = re.compile(r"^[¥$€£]?\s*-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def coerce_numeric_text(text: str) -> str | float:
    """数值形态的纯文本 → 数值(货币符号/千分位剥离);其余原样返回。

    基线快照(record_item_metrics)与阈值路由(``price >= 10000``)都消费
    数值语义,而选择器文本抽取天然是 str —— 不规整会让官方插件 gpu-prices
    的基线与路由双双静默失效(PRD 10-01-v04-trend-baseline)。只收「纯数值」:
    ``¥19800``/``19,800``/``19800.5`` 成数值;``50%``/``1.2.10``/电话号码
    等一律保持 str,不猜。
    """
    candidate = text.strip()
    if not candidate or not _NUMERIC_TEXT_RE.match(candidate):
        return text
    cleaned = candidate.lstrip("¥$€£").strip().replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:  # pragma: no cover - 正则已保证可解析,防御兜底
        return text
    return int(value) if "." not in cleaned else value


def _field_value(root: Any, selector: str, base_url: str) -> str | float | None:
    """First match of a field selector under ``root``: @attr value or stripped text.

    文本值经 :func:`coerce_numeric_text` 规整(数值语义字段如 price 在抽取层
    即为数值;@attr 值保持原样 —— href 等属性永远不是基线素材)。
    """
    css, attr = split_attr_selector(selector)
    nodes = root.css(css) if css else [root]
    if not nodes:
        return None
    node = nodes[0]
    if attr:
        value = node.attributes.get(attr)
        if value is None:
            return None
        if base_url and attr in _URL_ATTRIBUTES:
            return urljoin(base_url, value)
        return value
    text = node.text(separator=" ", strip=True)
    return coerce_numeric_text(text) if text else None


def extract_html(html: str, extract: ExtractConfig, *, base_url: str = "") -> list[dict]:
    """Apply a CSS ``list``/``item`` extract config to an HTML document.

    ``list`` iterates ``extract.item`` nodes; ``item`` runs field selectors
    against the whole document. Fields that match nothing are omitted; a
    record with zero hits is skipped. ``extract.url_template`` (list type
    only — schema rejects it on ``item``) renders item URLs at the outlet,
    same either/or semantics as :func:`extract_json`.
    """
    tree = HTMLParser(html)
    if extract.type == "item":
        record = {
            name: value
            for name, selector in extract.fields.items()
            if (value := _field_value(tree.root, selector, base_url)) is not None
        }
        return _apply_url_template([record] if record else [], extract)
    if not extract.item:
        raise ExtractionError("extract.type 为 list 时缺少 item 选择器")
    items: list[dict] = []
    for node in tree.css(extract.item):
        record = {
            name: value
            for name, selector in extract.fields.items()
            if (value := _field_value(node, selector, base_url)) is not None
        }
        if record:
            items.append(record)
    return _apply_url_template(items, extract)


def extract_rss(text: str, extract: ExtractConfig) -> list[dict]:
    """Apply an ``rss`` extract config to a fetched feed body (feedparser).

    ``fields`` values name feedparser entry attributes — closed whitelist
    :data:`shishi.schema.RSS_ENTRY_FIELDS`, enforced at load time (拼错即拒);
    per entry an attribute the feed does not carry is simply omitted from
    that record(逐条目语义,同 json_path;标题/链接齐、缺作者的条目照常
    产出)。A malformed feed never raises: feedparser surfaces it as the
    ``bozo`` flag and we only log a WARNING, entries that survived parsing
    still flow — bozo 容错(截断的 feed 常常仍带出前几条可用条目),条目
    为空自然返回空列表。``url_template`` is schema-rejected on rss,故无
    模板出口(条目 url = fields 里映射的 ``link``)。
    """
    parsed = feedparser.parse(text)
    if parsed.bozo:
        logger.warning(
            "RSS 源解析带 bozo 标志(容错解析继续,entries=%s): %s",
            len(parsed.entries),
            parsed.get("bozo_exception", ""),
        )
    items: list[dict] = []
    for entry in parsed.entries:
        record = {
            name: value
            for name, attr in extract.fields.items()
            if (value := getattr(entry, attr, None)) is not None
        }
        if record:
            items.append(record)
    return items


# ---------------------------------------------------------------------------
# Body decoding (gb18030/big5 legacy forums -> 生产源痛点)
# ---------------------------------------------------------------------------


def _decode_has_pua(text: str) -> bool:
    """Whether decoded text contains Private-Use-Area code points (U+E000-U+F8FF).

    GB18030 maps technically-valid-but-unassigned byte sequences into the PUA,
    so big5 bytes run through the gb18030 codec come out laced with PUA junk
    (真实中文文本几乎不会出现). A PUA hit on an *undeclared* fallback decode
    therefore means the candidate encoding is wrong — reject and try the next.
    """
    return any(0xE000 <= ord(char) <= 0xF8FF for char in text)


def decode_response(response: httpx.Response) -> str:
    """Decode a text body: BOM > Content-Type charset > HTML meta charset >
    utf-8/gb18030/big5 fallbacks, ending in a replace-decode that never raises.

    Declared charsets are trusted as-is; the *undeclared* fallbacks reject a
    strict decode laced with PUA characters (gb18030 静默解码 big5 字节的乱码
    signature) so the big5 branch stays reachable for legacy Big5 forums.
    """
    body = response.content
    for bom, encoding in _BOM_CANDIDATES:
        if body.startswith(bom):
            return body.decode(encoding, errors="replace")
    declared: list[str] = []
    charset = response.charset_encoding
    if charset:
        declared.append(charset)
    meta = _META_CHARSET_RE.search(body[:4096])
    if meta:
        declared.append(meta.group(1).decode("ascii", errors="ignore"))
    seen: set[str] = set()
    candidates: list[tuple[str, bool]] = [(item, True) for item in declared]
    candidates.extend((item, False) for item in _DECODE_FALLBACKS)
    for encoding, trusted in candidates:
        normalized = encoding.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            decoded = body.decode(normalized)
        except (UnicodeDecodeError, LookupError):
            continue
        if not trusted and _decode_has_pua(decoded):
            logger.debug("回退解码 %s 疑似错配(含私用区字符),尝试下一候选", normalized)
            continue
        return decoded
    logger.warning(
        "响应编码无法识别,按 utf-8 容错解码 url=%s", getattr(response.request, "url", "?")
    )
    return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Context & engine base
# ---------------------------------------------------------------------------


@dataclass
class FetchContext:
    """Shared per-run services injected into every engine (构造注入).

    One context per pipeline run: its limiter registry is what merges
    same-host sources. ``clock``/``sleep`` are injectable so tests never wait;
    production uses the defaults (monotonic clock, ``asyncio.sleep``).
    ``keychain_backend`` injects the keychain store for ``keychain:`` header
    references (tests: :class:`shishi.secrets.InMemoryKeychainBackend`;
    ``None`` = lazily discovered system keyring).
    ``proxy_pools`` injects the global pools declaration (v0.2 proxy
    transport): sources with ``proxy: pool:<name>`` share ONE proxied
    AsyncClient per pool (:attr:`pool_clients`), their robots.txt checks ride
    the same egress (:attr:`pool_robots`), and the clients are closed via
    :meth:`aclose_pool_clients` at run end. ``None`` = pools 未声明 — pool
    sources fail structured before any I/O.
    """

    client: httpx.AsyncClient
    store: Store | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    limiters: HostLimiterRegistry | None = None
    robots: RobotsCache | None = None
    keychain_backend: KeychainBackend | None = None
    proxy_pools: ProxyPools | None = None
    pool_clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)
    pool_robots: dict[str, RobotsCache] = field(default_factory=dict)
    # L3 零结果探测的 run 级剩余预算(源数):registry 每探测一源即减一,
    # 归零后首遇零结果源按健康空页收(10-04-crawl4ai-l3 拍板②;测试可注入小值)。
    l3_probe_budget: int = DEFAULT_L3_PROBE_BUDGET

    def __post_init__(self) -> None:
        if self.limiters is None:
            self.limiters = HostLimiterRegistry(clock=self.clock, sleeper=self.sleep)
        if self.robots is None:
            self.robots = RobotsCache(self.client, timeout=self.timeout)

    # -------------------------------------------------------- proxy transport

    def client_for_pool(
        self, name: str, proxy_url: str, *, timeout: float | None = None
    ) -> httpx.AsyncClient:
        """One shared proxied AsyncClient per pool (同池多源复用,凭据只解析一次).

        Args:
            name: pool name (cache key).
            proxy_url: concrete upstream URL (:meth:`ProxyPools.resolve` 输出).
            timeout: client default timeout; ``None`` = this context's.

        Returns:
            The cached or newly built proxied client; lifecycle via
            :meth:`aclose_pool_clients`.

        Raises:
            ProxyConfigError: client construction failed — socks 上游缺
                ``httpx[socks]``(socksio)或代理 URL 协议非法(``code``
                ``proxy_dependency_missing`` / ``invalid_proxy_url``)。
        """
        cached = self.pool_clients.get(name)
        if cached is not None:
            return cached
        try:
            client = httpx.AsyncClient(
                proxy=proxy_url, timeout=self.timeout if timeout is None else timeout
            )
        except (ImportError, ValueError) as exc:
            # ImportError: socks 上游缺 socksio(httpx[socks]);ValueError: 协议非法。
            code = "proxy_dependency_missing" if isinstance(exc, ImportError) else "invalid_proxy_url"
            raise ProxyConfigError(
                f"代理 transport 构建失败 pool={name} upstream={mask_proxy_url(proxy_url)}: {exc};"
                "socks 上游需安装 httpx[socks](pyproject 已声明,请检查环境)",
                code=code,
            ) from exc
        self.pool_clients[name] = client
        logger.debug("代理 transport 已创建 pool=%s upstream=%s", name, mask_proxy_url(proxy_url))
        return client

    def robots_for_pool(self, name: str, client: httpx.AsyncClient) -> RobotsCache:
        """robots.txt cache riding the pool's egress (同出口视角判定放行)."""
        cache = self.pool_robots.get(name)
        if cache is None:
            cache = RobotsCache(client, timeout=self.timeout)
            self.pool_robots[name] = cache
        return cache

    async def aclose_pool_clients(self) -> None:
        """Close every proxied client this context built (run 收尾/测试拆卸调用)."""
        for name, client in self.pool_clients.items():
            await client.aclose()
            logger.debug("代理 transport 已关闭 pool=%s", name)
        self.pool_clients.clear()
        self.pool_robots.clear()


class BaseEngine:
    """Common engine machinery; subclasses implement :meth:`_fetch_impl`.

    Engines receive ``(source, context)`` — politeness, retries, change
    fingerprints, credential resolution and the proxy transport live here once.
    Requests ride :attr:`_active_client`: the shared context client for
    ``direct``, the pool's shared proxied client for ``pool:<名称>``.

    Raises:
        ProxyNotSupportedError: ``residential:`` proxy (fetch time).
        ProxyConfigError: ``pool:<名称>`` whose pool is undeclared / whose
            transport cannot be built (fetch time, before any I/O).
        CredentialResolveError: a header or proxy credential reference cannot
            resolve (construction / fetch time).
        FetchError: extract config unsupported by this engine.
    """

    LAYER = "base"
    ENGINE_NAME = "base"
    REQUIRES_EXTRACT = True
    SUPPORTED_EXTRACT_TYPES: tuple[str, ...] = ()
    #: ``pagination.mode: scroll`` 仅 L4 scrapling 实装;子类显式开才放行
    #: (其余引擎在 fetch 前结构化拒绝 scroll_unsupported,链正确降级)。
    SUPPORTS_SCROLL = False

    def __init__(self, source: SourceConfig, context: FetchContext) -> None:
        self.source = source
        self.context = context
        self._headers = resolve_headers(source.headers, backend=context.keychain_backend)
        if "user-agent" not in {key.lower() for key in self._headers}:
            self._headers["User-Agent"] = DEFAULT_USER_AGENT
        self._change_detector = ChangeDetector(context.store)
        # Active transport: direct (shared context client) until fetch() mounts
        # the source's pool transport; robots cache follows the same egress.
        self._active_client = context.client
        self._active_robots = context.robots
        self._active_pool: str | None = None
        #: Resolved pool upstream URL (credentials expanded) when a pool is
        #: mounted — browsers (crawl4ai) consume it directly; NEVER log it raw
        #: (展示一律过 :func:`mask_proxy_url`).
        self._active_proxy_url: str | None = None
        # Set by engines when a walk aborts because the fingerprint is
        # unchanged; the registry surfaces it as FetchOutcome.skip_reason.
        self.last_skip_reason: str | None = None

    # ------------------------------------------------------------- options

    def engine_options(self) -> dict[str, Any]:
        """``sources[].engine_options.<ENGINE_NAME>`` (schema passes unknown keys through)."""
        raw = self.source.extra_params.get("engine_options")
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise FetchError(
                f"engine_options 应为键值映射,当前为 {type(raw).__name__}",
                error_type="invalid_engine_options",
            )
        value = raw.get(self.ENGINE_NAME, {})
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise FetchError(
                f"engine_options.{self.ENGINE_NAME} 应为键值映射,当前为 {type(value).__name__}",
                error_type="invalid_engine_options",
            )
        return value

    # ----------------------------------------------------------- lifecycle

    async def fetch(self) -> list[dict]:
        """Fetch all pages and return the extracted items (shell contract)."""
        self._check_pagination_support()
        resolve_proxy(self.source.proxy)
        self._prepare_proxy_transport()
        self._check_extract_support()
        return await self._fetch_impl()

    def _prepare_proxy_transport(self) -> None:
        """Resolve the source's proxy setting into the active transport.

        Runs before any network I/O: ``direct`` keeps the shared context
        client; ``pool:<名称>`` mounts the pool's shared proxied AsyncClient
        (凭据此刻才解析;声明缺失/池未知/解析失败都在这里结构化报错)并把
        robots.txt 切到同出口视角。``residential:`` already refused by
        :func:`resolve_proxy`; anything else falls back to direct defensively.

        Raises:
            ProxyConfigError: pools 未注入 / 池名未声明 / transport 构建失败.
            CredentialResolveError: 池 URL 的凭据引用解析失败.
        """
        assert self.context.robots is not None  # FetchContext.__post_init__ fills it
        self._active_client = self.context.client
        self._active_robots = self.context.robots
        self._active_pool = None
        self._active_proxy_url = None
        kind, _, pool_name = self.source.proxy.partition(":")
        if kind != "pool":
            return
        if self.context.proxy_pools is None:
            raise ProxyConfigError(
                f"源 proxy={self.source.proxy!r} 指向代理池,但运行上下文未注入全局 pools 声明;"
                "请在全局配置文件声明 pools 并在启动时加载(见 load_proxy_pools_file)",
                code="proxy_pools_not_configured",
            )
        self.context.proxy_pools.raw_url(pool_name)  # 未声明 -> proxy_pool_unknown(先于凭据解析)
        resolved = self.context.proxy_pools.resolve(
            pool_name, backend=self.context.keychain_backend
        )
        self._active_client = self.context.client_for_pool(
            pool_name, resolved, timeout=self.context.timeout
        )
        self._active_robots = self.context.robots_for_pool(pool_name, self._active_client)
        self._active_pool = pool_name
        self._active_proxy_url = resolved  # 浏览器引擎(crawl4ai)直读;日志只出掩码
        logger.info(
            "代理 transport 已挂载 source=%s pool=%s upstream=%s",
            self.source.name,
            pool_name,
            mask_proxy_url(resolved),
        )

    async def _fetch_impl(self) -> list[dict]:
        raise NotImplementedError

    def _check_extract_support(self) -> None:
        extract = self.source.extract
        if extract is None:
            if self.REQUIRES_EXTRACT:
                raise FetchError(
                    f"{self.ENGINE_NAME} 引擎需要配置 extract 节(v0.1 不做自动结构化)",
                    error_type="extract_required",
                )
            return
        if extract.type not in self.SUPPORTED_EXTRACT_TYPES:
            raise FetchError(
                f"{self.ENGINE_NAME} 引擎不支持 extract.type={extract.type!r}"
                f"(支持 {'/'.join(self.SUPPORTED_EXTRACT_TYPES)})",
                error_type="extract_unsupported",
            )

    def _check_pagination_support(self) -> None:
        """``pagination.mode: scroll`` 是 L4 scrapling 的专属语义(PRD
        10-01-v03-engine-scrapling / yaml-schema 规则 7),schema 层不强制
        (schema.py: enforced by the engine layer)。

        非 L4 引擎在此结构化拒绝(先于 robots/网络/代理,零 I/O),让
        ``engine: auto`` 沿链正确降级到 L4 —— 与「L4/L5 拒 json_path 让链
        降级」同一镜像先例(scrapling.py: ``json_path 属 L1 语义,此处拒绝``)。
        否则单页模板游走的 L1/L2 会把滚动页首屏当「成功」回写 hint,把降级链
        锁死在残缺快照上。
        """
        pagination = self.source.pagination
        if pagination is None or pagination.mode != "scroll":
            return
        if self.SUPPORTS_SCROLL:
            return
        raise FetchError(
            f"{self.ENGINE_NAME} 引擎不支持 pagination.mode='scroll'"
            "(无限滚动仅 L4 scrapling 实装;本引擎单页/模板游走会静默截断滚动页,"
            "故拒绝让 engine: auto 降级到 L4,显式指定请改 engine: scrapling)",
            error_type="scroll_unsupported",
        )

    # -------------------------------------------------------------- request

    async def request(
        self,
        url: str,
        *,
        method: str | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Robots-checked, rate-limited request with 304 negotiation and retry."""
        resolved_method = (method or self.source.method).upper()
        if json_body is None and resolved_method == "POST":
            json_body = self.source.post_body
        request_headers = dict(self._headers)
        request_headers.update(self._conditional_headers(url))
        if headers:
            request_headers.update(headers)
        await self._ensure_robots_allowed(url)
        await self._acquire_rate_limit(url)
        return await self._send_with_retry(
            resolved_method, url, json_body=json_body, headers=request_headers
        )

    def _conditional_headers(self, url: str) -> dict[str, str]:
        store = self.context.store
        if store is None:
            return {}
        baseline = store.get_baseline(url)
        if baseline is None:
            return {}
        headers: dict[str, str] = {}
        if baseline.etag:
            headers["If-None-Match"] = baseline.etag
        if baseline.last_modified:
            headers["If-Modified-Since"] = baseline.last_modified
        return headers

    async def _ensure_robots_allowed(self, url: str) -> None:
        if not self.source.rate_limit.respect_robots:
            return
        assert self._active_robots is not None  # __init__ / _prepare_proxy_transport fill it
        user_agent = next(
            (value for key, value in self._headers.items() if key.lower() == "user-agent"),
            "*",
        )
        if not await self._active_robots.is_allowed(url, user_agent):
            raise RobotsDisallowedError(f"robots.txt 禁止抓取,源已跳过 url={url}")

    async def _acquire_rate_limit(self, url: str) -> None:
        assert self.context.limiters is not None
        host = urlsplit(url).netloc
        limiter = self.context.limiters.limiter_for(host, self.source.rate_limit)
        waited = await limiter.acquire()
        if waited:
            logger.debug("限速等待 %.2fs host=%s", waited, host)

    async def _send_with_retry(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        log_url: str | None = None,
    ) -> httpx.Response:
        """Send one request, backing off 429/5xx/transport errors per ``retry``.

        ``timeout`` overrides the context default for this request — engines
        whose backend has its own (longer) budget (firecrawl) must not have
        the HTTP client cut the request before that budget can expire.

        ``log_url`` 重定向重试 WARNING 里的 URL 展示形态:后端 endpoint 是
        凭据引用的引擎(llm_browser/firecrawl)传 :func:`mask_endpoint_url`
        的结果 —— 解析值不落日志;缺省(None)原样记 url(目标站 URL 本就
        公开,排障需要完整形态)。
        """
        shown_url = url if log_url is None else log_url
        effective_timeout = self.context.timeout if timeout is None else timeout
        for attempt in range(self.source.retry + 1):
            try:
                response = await self._active_client.request(
                    method, url, headers=headers, json=json_body, timeout=effective_timeout
                )
            except httpx.TransportError as exc:
                if attempt < self.source.retry:
                    delay = self.backoff_delay(attempt)
                    logger.warning(
                        "网络错误,%.1fs 后重试 url=%s attempt=%s/%s",
                        delay, shown_url, attempt + 1, self.source.retry,
                    )
                    await self.context.sleep(delay)
                    continue
                raise self._wrap_proxy_transport_failure(exc) from exc
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.source.retry:
                delay = self.backoff_delay(attempt)
                logger.warning(
                    "HTTP %s,%.1fs 后重试 url=%s attempt=%s/%s",
                    response.status_code, delay, shown_url, attempt + 1, self.source.retry,
                )
                await self.context.sleep(delay)
                continue
            if response.status_code >= 400:
                # 4xx 是永久性错误立即抛出;429/5xx 走到这里说明重试预算已耗尽。
                response.raise_for_status()
            return response
        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover

    def _wrap_proxy_transport_failure(self, exc: httpx.TransportError) -> BaseException:
        """Classify a transport failure by the transport that produced it.

        Direct-transport failures pass through untouched (既有 network/timeout
        分类不变);proxied ones become :class:`ProxyTransportError` so the
        degrade chain can tell 代理挂 from 源死 (PRD: 错误分类区分,降级链
        决策依据不同)。
        """
        if self._active_pool is None:
            return exc
        upstream = ""
        if self.context.proxy_pools is not None and self._active_pool in self.context.proxy_pools:
            upstream = mask_proxy_url(self.context.proxy_pools.raw_url(self._active_pool))
        return ProxyTransportError(
            f"代理链路失败 pool={self._active_pool} upstream={upstream}: {exc}",
            error_type=classify_proxy_transport(exc),
            pool=self._active_pool,
        )

    def backoff_delay(self, attempt: int) -> float:
        """Backoff seconds before retry ``attempt`` (policy from rate_limit)."""
        policy = self.source.rate_limit.backoff
        if policy == "none":
            return 0.0
        if policy == "linear":
            return min(BACKOFF_CAP_SECONDS, float(max(attempt, 1)))
        return min(BACKOFF_CAP_SECONDS, float(2 ** max(0, attempt)))

    # ------------------------------------------------------------ helpers

    def check_change(
        self,
        url: str,
        response: httpx.Response,
        *,
        decoded_text: str | None = None,
    ) -> ChangeVerdict:
        """Compare one response against the store baseline (and update it)."""
        return self._change_detector.check(url, response, decoded_text=decoded_text)

    def _template_walks(self) -> list[list[str]]:
        """Expand URL placeholders into per-walk URL groups.

        Non-page placeholders resolve against source-level extra params by
        exact name, falling back to the plural param when it is a list
        (official showcase convention: ``{symbol}`` <- ``symbols: [...]``); a
        list value fans out into one walk per value — walks are independent
        (一个 symbol 的空页/指纹未变不得截断其余 symbol), pagination pages
        live *inside* a walk and ``max_pages`` caps them.
        """
        url = self.source.url
        names = sorted(set(_PLACEHOLDER_RE.findall(url)) - {"page"})
        combos: list[dict[str, str]] = [{}]
        for name in names:
            value = self.source.extra_params.get(name)
            if value is None and f"{name}s" in self.source.extra_params:
                value = self.source.extra_params[f"{name}s"]
            if value is None:
                raise FetchError(
                    f"URL 模板占位符 {{{name}}} 缺少对应的源级参数",
                    error_type="missing_template_param",
                )
            values = [str(item) for item in value] if isinstance(value, list) else [str(value)]
            combos = [dict(combo, **{name: item}) for combo in combos for item in values]
        pagination = self.source.pagination
        pages: list[int | None]
        if "{page}" in url:
            max_pages = pagination.max_pages if pagination else 1
            pages = list(range(1, max_pages + 1))
        else:
            pages = [None]
        walks: list[list[str]] = []
        for combo in combos:
            walk: list[str] = []
            for page in pages:
                mapping = dict(combo)
                if page is not None:
                    mapping["page"] = page
                walk.append(
                    _PLACEHOLDER_RE.sub(lambda match, m=mapping: str(m[match.group(1)]), url)
                )
            walks.append(walk)
        return walks

    def _template_urls(self) -> list[str]:
        """Flat view of :meth:`_template_walks` (combo-major, pages inside)."""
        return [url for walk in self._template_walks() for url in walk]
