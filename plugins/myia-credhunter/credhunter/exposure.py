"""曝面发现线:FOFA/Shodan 客户端 + L0 被动探测(credhunter R4)。

本模块由 MYIA 仓库创作与维护,是上游(AGPL-3.0)行为的**功能重实现**:
实现只认行为规格(``.trellis/tasks/10-03-aipocket-fusion/research/
behavior-specs/exposure.md``),零上游代码/标识符/文案复制。

授权边界(先读):仅用于已授权安全研究与自有/已授权资产的暴露面排查;
被动探测只做 **L0 未授权读**(unauth_read),weak_password/idor/ssrf/
sqli/rce 级探测一律 fail-closed 拒绝(授权范围机制留二期,PRD §7)。

行为要点(规格节号见括号):
- **FOFA**(§1):``GET {base}/api/v1/search/all``,单 query 参数 ``key``
  (多 key 逗号分隔时只用第一个);``qbase64`` = 查询串 UTF-8 再 base64;
  固定 12 字段 ``fields``;页 1..=10 × size 100,**返回行数 < size 提前停页**,
  页间 0.3s;每 run 只取前 24 条查询(Q8 预算)。**base 不写死**——上游
  默认第三方代理域,本件把 endpoint 做成可配占位,官方/代理由部署者自决
  (PRD §7);``check()`` 用探针查询 ``title="123"`` page=1 size=1。
- **Shodan**(§2):``GET {base}/shodan/host/search``(默认官方
  api.shodan.io)与 ``/shodan/host/count``;search 参数 key/query/page,
  页大小固定 100,本页 matches < 100 提前停,页间 1.0s,预算 16 条查询;
  FOFA 查询机械翻译:``body="X"``→``http.html:"X"``、``&&``→空格、
  ``||``→`` OR ``。行归一化三级兜底:host = hostnames[0] → http.host →
  ip_str → host;port 非 80/443 且 host 无冒号则拼 ``:port``;443→https
  否则 http;``data``→header、``http.html``→banner、ssl 证书 subject/
  issuer commonName 拼进 cert;产出 FOFA 同形 12 字段。
- **被动探测**(§4):目标 = hit 的 host(缺失退 url);产品 hint =
  查询→产品映射打标(无映射=generic);按声明顺序 GET 每个产品路径,
  **累计 findings ≥ 请求预算(12)即停**,任一 2xx → finding
  ``{product, vuln_class: "unauth_read", risk: 0, evidence 前缀 512 字符}``,
  请求错误吞掉只计数;重定向链 > 2 跳按失败丢弃。**探测前先做 scheme
  归一**(规格「未核实清单」记录的上游坑:Shodan 归一化产物不带 scheme,
  不补 scheme 拼出的 URL 在上游探测不可用——本件在探测入口统一补齐)。
- **降级语义**(§5):无 key → lane 显式空态(``credential_missing``,
  不报错不静默,AC6);查询页失败记入 lane errors 后终止该查询分页、
  继续下一查询;单源 0 命中 ≠ 失败(status=empty)。

Q9 掩码红线:探测证据(2xx 正文前 512 字符)里出现任何密钥形态串,
  一律先经指纹库抽取 + 掩码替换再进 item(全文永不进 items/模板)。
"""

from __future__ import annotations

import base64
import re
import sys
import time
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "DEFAULT_PRODUCT",
    "DEFAULT_TIMEOUT_SECONDS",
    "EVIDENCE_CHARS",
    "ERROR_PREVIEW_CHARS",
    "FOFA_CHECK_QUERY",
    "FOFA_FIELDS",
    "FOFA_MAX_PAGES",
    "FOFA_PAGE_DELAY",
    "FOFA_PAGE_SIZE",
    "FOFA_QUERY_BUDGET",
    "FOFA_SEARCH_PATH",
    "L0",
    "PRODUCT_PATHS",
    "PROBE_MAX_REDIRECTS",
    "PROBE_REQUEST_BUDGET",
    "PROBE_VULN_CLASS",
    "SHODAN_BASE_URL",
    "SHODAN_COUNT_PATH",
    "SHODAN_MAX_PAGES",
    "SHODAN_PAGE_DELAY",
    "SHODAN_PAGE_SIZE",
    "SHODAN_QUERY_BUDGET",
    "SHODAN_SEARCH_PATH",
    "SPEC_UNAUTH_EXTRA_PATHS",
    "ExposureClientError",
    "ExposureHit",
    "ExposurePolicyError",
    "FofaClient",
    "ShodanClient",
    "apply_query_budget",
    "build_exposure_item",
    "ensure_l0_policy",
    "fofa_search_all",
    "fofa_to_shodan",
    "normalize_scheme",
    "probe_target",
    "redact_evidence",
    "resolve_product",
    "run_exposure",
    "shodan_search_all",
]

#: 探测 UA(自有标识;上游无对应物)。
_USER_AGENT = "myia-credhunter-exposure/0.1"

# ---------------------------------------------------------------------------
# 兄弟模块自举:与 adapter._load_module 同一 compile+exec 手法(零 __pycache__)
# ---------------------------------------------------------------------------


def _require_sibling(name: str) -> types.ModuleType:
    """取同目录 ``credhunter/<name>.py`` 子模块(登记 sys.modules 后 compile+exec)。

    与适配器 ``_load_module`` 同名同法(``myia_credhunter_<name>``):适配器
    先加载过的兄弟模块直接复用,独立加载本模块时按需自举。登记先于 exec,
    供兄弟模块内 dataclass 的字符串注解按 ``cls.__module__`` 反查。
    """
    module_name = f"myia_credhunter_{name}"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    module_file = Path(__file__).resolve().parent / f"{name}.py"
    if not module_file.is_file():
        raise FileNotFoundError(f"credhunter 子模块不存在:{module_file}")
    module = types.ModuleType(module_name)
    module.__file__ = str(module_file)
    sys.modules[module_name] = module
    executable = compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码,非任意输入
    return module


#: 证据掩码/密钥抽取依赖(findings 掩码 + fingerprints 抽取,Q9 红线)。
_findings = _require_sibling("findings")
_fingerprints = _require_sibling("fingerprints")

# ---------------------------------------------------------------------------
# 缺省参数(grill Q8 定案 + 行为规格 §7 默认值速查)
# ---------------------------------------------------------------------------

#: FOFA 搜索端点路径(拼在可配 base 之后)。
FOFA_SEARCH_PATH = "/api/v1/search/all"

#: FOFA 固定 12 字段(顺序即数组行的映射顺序,规格 §1)。
FOFA_FIELDS = (
    "host", "ip", "port", "protocol", "title", "header",
    "banner", "server", "product", "link", "domain", "cert",
)

#: FOFA 页大小(默认 100)。
FOFA_PAGE_SIZE = 100

#: FOFA 最大页数(默认 10)。
FOFA_MAX_PAGES = 10

#: FOFA 页间停顿秒数(默认 0.3s)。
FOFA_PAGE_DELAY = 0.3

#: FOFA 每 run 查询条数预算(默认 24;配额=查询条数,不是点数)。
FOFA_QUERY_BUDGET = 24

#: FOFA 连通性探针查询(check() 用,规格 §1)。
FOFA_CHECK_QUERY = 'title="123"'

#: Shodan 官方 API 基址(公开官方端点,非第三方代理;可配覆盖)。
SHODAN_BASE_URL = "https://api.shodan.io"

#: Shodan 搜索/计数端点路径。
SHODAN_SEARCH_PATH = "/shodan/host/search"
SHODAN_COUNT_PATH = "/shodan/host/count"

#: Shodan 页大小(search 固定 100)。
SHODAN_PAGE_SIZE = 100

#: Shodan 最大页数(默认 10)。
SHODAN_MAX_PAGES = 10

#: Shodan 页间停顿秒数(默认 1.0s)。
SHODAN_PAGE_DELAY = 1.0

#: Shodan 每 run 查询条数预算(默认 16)。
SHODAN_QUERY_BUDGET = 16

#: 出网请求单次超时秒数(规格 §7:HTTP 客户端全局超时 = 15s)。
DEFAULT_TIMEOUT_SECONDS = 15.0

#: 被动探测每目标请求预算(GENERIC_MAX_REQUESTS_PER_TARGET 默认 12;宏语义
#: = 累计 findings 达预算即停,见 probe_target)。
PROBE_REQUEST_BUDGET = 12

#: 被动探测重定向跳数上限(规格 §7:max_probe_redirects=2;超跳按失败丢弃)。
PROBE_MAX_REDIRECTS = 2

#: 证据快照字符上限(正文前 512 字符)。
EVIDENCE_CHARS = 512

#: 错误消息里响应体预览的字符上限(规格 §1:前 200 字符预览)。
ERROR_PREVIEW_CHARS = 200

#: L0 = risk 0(未授权读;唯一放行的探测档位)。
L0 = 0

#: 被动探测唯一探测类(扫描器主路径硬编码 L0/intrusive=false/只许 unauth_read)。
PROBE_VULN_CLASS = "unauth_read"

#: 无产品映射时的兜底产品名。
DEFAULT_PRODUCT = "generic"

#: 产品路径集(规格 §4 宏集,按声明顺序 GET)。未知产品回落 generic。
PRODUCT_PATHS: dict[str, tuple[str, ...]] = {
    "dify": ("/console/api/system-features", "/v1/info"),
    "litellm": ("/health/readiness", "/v1/models"),
    "openwebui": ("/api/config", "/api/version"),
    "flowise": ("/api/v1/version",),
    "langflow": ("/api/v1/version",),
    "newapi": ("/api/status", "/v1/models"),
    "generic": ("/v1/models", "/api/status"),
    "anythingllm": ("/api/system", "/api/v1/system"),
    "chatgpt_next_web": ("/api/config",),
    "librechat": ("/api/config",),
    "lobechat": ("/api/config",),
    "fastgpt": ("/api/system/getInitData",),
    "openrouter": ("/v1/models", "/api/v1/models"),
    "portkey": ("/v1/models", "/api/v1/models"),
}

#: flowise/langflow 的 spec 版更强证据路径(/api/v1/credentials 等)。**刻意
#: 不进默认探测集**:L0 宏集为准(扫描器主路径配套),spec 引擎增强属后续件。
SPEC_UNAUTH_EXTRA_PATHS: dict[str, tuple[str, ...]] = {
    "flowise": ("/api/v1/credentials", "/api/v1/chatflows", "/api/v1/variables", "/api/v1/flows"),
    "langflow": ("/api/v1/credentials", "/api/v1/chatflows", "/api/v1/variables", "/api/v1/flows"),
}


class ExposureClientError(Exception):
    """上游(FOFA/Shodan)请求错误:统一字符串语义,无错误码细分(规格 §1/§2)。

    source 为 ``"fofa"`` / ``"shodan"``;message 带 HTTP 状态与响应体前
    :data:`ERROR_PREVIEW_CHARS` 字符预览(非 2xx)或解析失败说明(非 JSON)。
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


class ExposurePolicyError(Exception):
    """探测档位门控拒绝:非 L0 / 非 unauth_read / intrusive 一律 fail-closed。"""


# ---------------------------------------------------------------------------
# FOFA 同形 12 字段命中记录
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExposureHit:
    """一条曝面命中(FOFA 同形 12 字段 + 两个辅助字段)。

    ``product_hint`` = 查询→产品映射打的标(探测产品 hint 优先通道);
    ``url`` = host 缺失时的目标兜底(规格 §4:目标来源 host 缺失退 url)。
    """

    host: str = ""
    ip: str = ""
    port: str = ""
    protocol: str = ""
    title: str = ""
    header: str = ""
    banner: str = ""
    server: str = ""
    product: str = ""
    link: str = ""
    domain: str = ""
    cert: str = ""
    product_hint: str = ""
    url: str = ""

    def probe_target_value(self) -> str:
        """探测目标:host 优先,缺失退 url(规格 §4)。"""
        return self.host or self.url


def _scalar(value: Any) -> str:
    """把行字段值规整成干净字符串(数字转十进制;其余非字符串丢弃)。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _fofa_row_to_hit(row: Any) -> ExposureHit:
    """FOFA 一行 → :class:`ExposureHit`(数组按字段序;对象按键取)。

    ``body`` 为空时别名进 ``banner``(规格 §1 行映射);对象行额外的
    ``url`` 键作为 host 缺失兜底保留。
    """
    values: dict[str, str] = {}
    if isinstance(row, Mapping):
        for field in FOFA_FIELDS:
            values[field] = _scalar(row.get(field, ""))
        values["banner"] = values["banner"] or _scalar(row.get("body", ""))
        url = _scalar(row.get("url", ""))
    elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
        for field, value in zip(FOFA_FIELDS, row, strict=False):
            values[field] = _scalar(value)
        url = ""
    else:
        raise ExposureClientError("fofa", f"results 行既非数组也非对象: {type(row).__name__}")
    return ExposureHit(
        host=values.get("host", ""),
        ip=values.get("ip", ""),
        port=values.get("port", ""),
        protocol=values.get("protocol", ""),
        title=values.get("title", ""),
        header=values.get("header", ""),
        banner=values.get("banner", ""),
        server=values.get("server", ""),
        product=values.get("product", ""),
        link=values.get("link", ""),
        domain=values.get("domain", ""),
        cert=values.get("cert", ""),
        url=url,
    )


def _shodan_match_to_hit(match: Mapping[str, Any]) -> ExposureHit:
    """Shodan search 一条 match → FOFA 同形 :class:`ExposureHit`(规格 §2)。

    host 三级兜底:hostnames[0] → http.host → ip_str → host;port 非 80/443
    且 host 无冒号则拼 ``:port``;protocol = 443→https 否则 http;``data``→
    header、``http.html``→banner、ssl 证书 subject/issuer commonName 拼进 cert。
    """
    def _first_text(*candidates: Any) -> str:
        for candidate in candidates:
            text = _scalar(candidate)
            if text:
                return text
        return ""

    http_info = match.get("http")
    http_info = http_info if isinstance(http_info, Mapping) else {}
    hostnames = match.get("hostnames")
    first_hostname = ""
    if isinstance(hostnames, Sequence) and not isinstance(hostnames, (str, bytes)) and hostnames:
        first_hostname = _scalar(hostnames[0])
    host = _first_text(first_hostname, http_info.get("host"), match.get("ip_str"), match.get("host"))

    port_text = _scalar(match.get("port"))
    port = int(port_text) if port_text.isdigit() else None
    host_field = host
    if port is not None and port not in (80, 443) and ":" not in host:
        host_field = f"{host}:{port}"

    cert_parts: list[str] = []
    ssl_cert = match.get("ssl")
    ssl_cert = ssl_cert.get("cert") if isinstance(ssl_cert, Mapping) else None
    if isinstance(ssl_cert, Mapping):
        for side in ("subject", "issuer"):
            node = ssl_cert.get(side)
            if isinstance(node, Mapping):
                common_name = _scalar(node.get("CN"))
                if common_name:
                    cert_parts.append(common_name)

    return ExposureHit(
        host=host_field,
        ip=_scalar(match.get("ip_str")),
        port=port_text,
        protocol="https" if port == 443 else "http",
        title=_scalar(http_info.get("title")),
        header=_scalar(match.get("data")),
        banner=_scalar(http_info.get("html")),
        server=_scalar(http_info.get("server")),
        product=_scalar(match.get("product")),
        cert="; ".join(cert_parts),
    )


# ---------------------------------------------------------------------------
# 查询预算与机械翻译
# ---------------------------------------------------------------------------


def apply_query_budget(queries: Sequence[str], *, budget: int) -> list[str]:
    """查询池去重(保序)后截前 ``budget`` 条(每 run 查询条数预算)。

    与上游 plan_query_ids 的差异:上游按历史指标挑选,本件无指标库,
    取确定性前 N 条(已在模块 docstring 记档)。
    """
    seen: set[str] = set()
    pool: list[str] = []
    for query in queries:
        cleaned = query.strip() if isinstance(query, str) else ""
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            pool.append(cleaned)
    return pool[:budget]


def fofa_to_shodan(query: str) -> str:
    """FOFA 查询 → Shodan 查询的机械翻译(规格 §2,三条规则)。

    ``body="X"`` → ``http.html:"X"``;``&&`` → 空格;``||`` → `` OR ``。
    只做这三条已文档化的变换(空白收敛为单空格),其余片段原样保留。
    """
    translated = re.sub(r'\bbody="([^"]*)"', r'http.html:"\1"', query)
    translated = translated.replace("&&", " ")
    translated = re.sub(r"\s*\|\|\s*", " OR ", translated)
    return re.sub(r"\s+", " ", translated).strip()


# ---------------------------------------------------------------------------
# FOFA 客户端
# ---------------------------------------------------------------------------


def _first_key(raw: str | None) -> str:
    """多 key 逗号分隔时只用第一个(search 语义,无轮询;规格 §1/§2)。"""
    return (raw or "").split(",")[0].strip()


def _require_absolute_base(source: str, base_url: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        raise ExposureClientError(source, f"base URL 必须是 http(s) 绝对地址,当前为 {base_url!r}")
    return cleaned


class FofaClient:
    """FOFA 搜索客户端(端点/参数/认证照规格 §1;base 必须由部署者传入)。

    本件刻意不提供 base 缺省值:上游默认第三方代理域,官方/代理的选择
    归部署者(PRD §7「endpoint 可配占位,不写死」)。
    """

    def __init__(
        self,
        *,
        key: str,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._key = _first_key(key)
        self._base = _require_absolute_base("fofa", base_url)
        self._timeout = timeout
        self._client_factory = client_factory

    def search(self, query: str, *, page: int = 1, size: int = FOFA_PAGE_SIZE) -> tuple[list[ExposureHit], dict[str, str]]:
        """单页搜索:``qbase64``(UTF-8→base64)+ 固定 12 字段 fields。

        Returns:
            (hits, meta):hits 为 FOFA 同形记录;meta 为响应里的标量诊断
            字段(size/page/mode/query)。

        Raises:
            ExposureClientError: 未配置 key/base;非 2xx(带前 200 字符
            预览);非 JSON;响应 error 字段为真;results 缺失或行形状坏。
        """
        if not self._key:
            raise ExposureClientError("fofa", "not configured: 缺 API key")
        params = {
            "key": self._key,
            "qbase64": base64.b64encode(query.encode("utf-8")).decode("ascii"),
            "page": page,
            "size": size,
            "fields": ",".join(FOFA_FIELDS),
        }
        payload = self._get_json(FOFA_SEARCH_PATH, params)
        if payload.get("error"):
            message = _scalar(payload.get("errmsg")) or "上游返回 error 标记"
            raise ExposureClientError("fofa", message[:ERROR_PREVIEW_CHARS])
        results = payload.get("results")
        if not isinstance(results, list):
            raise ExposureClientError("fofa", "响应缺 results 数组")
        hits = [_fofa_row_to_hit(row) for row in results]
        meta = {name: _scalar(payload.get(name)) for name in ("size", "page", "mode", "query")}
        return hits, meta

    def check(self) -> bool:
        """连通性探针:``title="123"`` page=1 size=1(规格 §1)。失败抛错。"""
        self.search(FOFA_CHECK_QUERY, page=1, size=1)
        return True

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        client = self._client_factory(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ExposureClientError("fofa", f"请求失败: {type(exc).__name__}: {exc}") from exc
        finally:
            client.close()
        if not response.is_success:
            preview = (response.text or "").strip()[:ERROR_PREVIEW_CHARS]
            raise ExposureClientError("fofa", f"HTTP {response.status_code}: {preview}")
        try:
            payload = response.json()
        except ValueError as exc:
            preview = (response.text or "").strip()[:ERROR_PREVIEW_CHARS]
            raise ExposureClientError("fofa", f"非 JSON 响应: {preview}") from exc
        if not isinstance(payload, dict):
            raise ExposureClientError("fofa", f"响应不是 JSON 对象: {type(payload).__name__}")
        return payload


def fofa_search_all(
    client: FofaClient,
    query: str,
    *,
    max_pages: int = FOFA_MAX_PAGES,
    page_size: int = FOFA_PAGE_SIZE,
    page_delay: float = FOFA_PAGE_DELAY,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[ExposureHit], list[str]]:
    """逐页搜一条查询:返回行数 < size 提前停页,页间停顿。

    Returns:
        (hits, errors):某页失败 → 记一条错误并**终止该查询的分页、保留
        已取页**(规格 §5:push errors 后 break,继续下一查询是调用方的事)。
    """
    hits: list[ExposureHit] = []
    errors: list[str] = []
    for page in range(1, max_pages + 1):
        try:
            page_hits, _meta = client.search(query, page=page, size=page_size)
        except ExposureClientError as exc:
            errors.append(str(exc))
            break
        hits.extend(page_hits)
        if len(page_hits) < page_size:
            break
        if page < max_pages:
            sleeper(page_delay)
    return hits, errors


# ---------------------------------------------------------------------------
# Shodan 客户端
# ---------------------------------------------------------------------------


class ShodanClient:
    """Shodan 搜索/计数客户端(端点/参数照规格 §2;默认官方基址可配覆盖)。"""

    def __init__(
        self,
        *,
        key: str,
        base_url: str = SHODAN_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._key = _first_key(key)
        self._base = _require_absolute_base("shodan", base_url)
        self._timeout = timeout
        self._client_factory = client_factory

    def search(self, query: str, *, page: int = 1) -> tuple[list[ExposureHit], int]:
        """单页搜索(页大小固定 100)。

        Returns:
            (hits, total):hits 为 FOFA 同形记录;total 为上游报告的总数。

        Raises:
            ExposureClientError: 未配置 key;非 2xx;非 JSON;缺 matches。
        """
        if not self._key:
            raise ExposureClientError("shodan", "not configured: 缺 API key")
        params = {"key": self._key, "query": query, "page": page}
        payload = self._get_json(SHODAN_SEARCH_PATH, params)
        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise ExposureClientError("shodan", "响应缺 matches 数组")
        hits = [_shodan_match_to_hit(match) if isinstance(match, Mapping) else
                _fofa_row_to_hit(match) for match in matches]
        total_text = _scalar(payload.get("total"))
        return hits, int(total_text) if total_text.lstrip("-").isdigit() else 0

    def count(self, query: str) -> int:
        """计数查询:只带 key/query,取响应 ``total``(规格 §2)。"""
        if not self._key:
            raise ExposureClientError("shodan", "not configured: 缺 API key")
        payload = self._get_json(SHODAN_COUNT_PATH, {"key": self._key, "query": query})
        total_text = _scalar(payload.get("total"))
        if not total_text.lstrip("-").isdigit():
            raise ExposureClientError("shodan", f"count 响应 total 非整数: {total_text!r}")
        return int(total_text)

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        client = self._client_factory(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ExposureClientError("shodan", f"请求失败: {type(exc).__name__}: {exc}") from exc
        finally:
            client.close()
        if not response.is_success:
            preview = (response.text or "").strip()[:ERROR_PREVIEW_CHARS]
            raise ExposureClientError("shodan", f"HTTP {response.status_code}: {preview}")
        try:
            payload = response.json()
        except ValueError as exc:
            preview = (response.text or "").strip()[:ERROR_PREVIEW_CHARS]
            raise ExposureClientError("shodan", f"非 JSON 响应: {preview}") from exc
        if not isinstance(payload, dict):
            raise ExposureClientError("shodan", f"响应不是 JSON 对象: {type(payload).__name__}")
        return payload


def shodan_search_all(
    client: ShodanClient,
    query: str,
    *,
    max_pages: int = SHODAN_MAX_PAGES,
    page_size: int = SHODAN_PAGE_SIZE,
    page_delay: float = SHODAN_PAGE_DELAY,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[ExposureHit], list[str]]:
    """逐页搜一条查询:本页 matches < 100 提前停,页间停顿(规格 §2)。"""
    hits: list[ExposureHit] = []
    errors: list[str] = []
    for page in range(1, max_pages + 1):
        try:
            page_hits, _total = client.search(query, page=page)
        except ExposureClientError as exc:
            errors.append(str(exc))
            break
        hits.extend(page_hits)
        if len(page_hits) < page_size:
            break
        if page < max_pages:
            sleeper(page_delay)
    return hits, errors


# ---------------------------------------------------------------------------
# L0 被动探测
# ---------------------------------------------------------------------------


def ensure_l0_policy(vuln_class: str, risk: int, *, intrusive: bool = False) -> None:
    """探测档位门控:**非 L0 一律 fail-closed 拒**(规格 §4 风险门控)。

    本模块只放行 ``unauth_read`` + ``risk<=L0`` + 非 intrusive;更高档位
    (weak_password/idor/ssrf/sqli/rce)与 intrusive 探测在授权范围机制
    落地前(二期)不存在可开路径——显式拒绝而不是静默降级。

    Raises:
        ExposurePolicyError: 任一档位越界。
    """
    if vuln_class != PROBE_VULN_CLASS:
        raise ExposurePolicyError(
            f"探测类 {vuln_class!r} 被拒:本模块只做 {PROBE_VULN_CLASS!r}(L0 fail-closed)"
        )
    if risk > L0:
        raise ExposurePolicyError(f"risk={risk} 超 L0 被拒(fail-closed;PROBE_MAX_RISK=L0)")
    if intrusive:
        raise ExposurePolicyError("intrusive 探测被拒:授权范围机制留二期,当前无开启路径")


def normalize_scheme(target: str) -> str:
    """探测目标 scheme 归一:去空白与尾斜杠,无 scheme 时补齐。

    规格「未核实清单」记录的上游坑:Shodan 归一化产物不带 scheme,上游
    探测路径上不做 scheme 归一导致拼出的 URL 不可用。本件在探测入口统一
    补齐:已有 ``scheme://`` 原样;裸 ``host:443`` 按 https;其余按 http。
    """
    value = (target or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" in value:
        return value
    if value.endswith(":443"):
        return f"https://{value}"
    return f"http://{value}"


def resolve_product(
    hit: ExposureHit,
    product_hints: Mapping[str, str] | None = None,
    *,
    query: str = "",
) -> str:
    """探测产品归因:查询→产品映射打标 > 命中行自身 product 字段 > generic。

    映射打标是主通道(规格 §4);行内 product(如 FOFA 的 nginx 指纹)只有
    恰好命中已知产品路径集的键才采用,否则回落 generic。
    """
    hint = hit.product_hint or (product_hints or {}).get(query, "")
    if hint in PRODUCT_PATHS:
        return hint
    if hit.product in PRODUCT_PATHS:
        return hit.product
    return DEFAULT_PRODUCT


def redact_evidence(text: str) -> str:
    """证据快照掩码:指纹库抽取密钥形态串 → 前 8 后 4 掩码替换(Q9)。"""
    secrets = [hit.apikey for hit in _fingerprints.extract_secrets(text)]
    return _findings.redact_secrets(text, secrets)


def build_exposure_item(
    *,
    probe_url: str,
    product: str,
    host: str,
    snippet: str,
    source_engine: str = "",
    query: str = "",
    ip: str = "",
    port: str = "",
    protocol: str = "",
    evidence_path: str = "",
) -> dict[str, Any]:
    """把一次 2xx 被动探测装配成管线 item(url 必填;证据已掩码+512 截断)。

    item 形状对齐 :func:`credhunter.findings.build_finding_item` 的管线
    契约(url/title/source/content + metadata),但这是**服务暴露面**命中
    (无 apikey 字段),dedup 面走 host/product 结构化字段(由品类 YAML
    接线段引用),不套密钥指纹模板。
    """
    item: dict[str, Any] = {
        "url": probe_url,
        "title": f"{product} 未授权读暴露({host or probe_url})",
        "source": "credhunter",
        "content": snippet[:EVIDENCE_CHARS],
        "product": product,
        "vuln_class": PROBE_VULN_CLASS,
        "risk": L0,
        "host": host,
        "source_type": "exposure_probe",
    }
    if ip:
        item["ip"] = ip
    if port:
        item["port"] = port
    if protocol:
        item["protocol"] = protocol
    if evidence_path:
        item["evidence_path"] = evidence_path
    if source_engine:
        item["engine"] = source_engine
    if query:
        item["query"] = query
    return item


def probe_target(
    target: str,
    product: str = DEFAULT_PRODUCT,
    *,
    vuln_class: str = PROBE_VULN_CLASS,
    risk: int = L0,
    intrusive: bool = False,
    request_budget: int = PROBE_REQUEST_BUDGET,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    source_engine: str = "",
    query: str = "",
    ip: str = "",
    port: str = "",
    protocol: str = "",
) -> dict[str, Any]:
    """对一个目标跑 L0 被动探测:按声明顺序 GET 产品路径,任一 2xx → finding。

    宏语义(规格 §4):累计 findings ≥ request_budget 即停;请求错误吞掉
    只计数;重定向链 > :data:`PROBE_MAX_REDIRECTS` 跳按失败丢弃;证据 =
    正文前 :data:`EVIDENCE_CHARS` 字符(先掩码后截断)。目标先过
    :func:`normalize_scheme`(scheme 归一是本件对上游坑的修正)。

    Returns:
        报告 ``{"target", "product", "findings"(items), "requests", "errors"}``。

    Raises:
        ExposurePolicyError: 档位越界(非 L0/unauth_read/intrusive)。
    """
    ensure_l0_policy(vuln_class, risk, intrusive=intrusive)
    base = normalize_scheme(target)
    report: dict[str, Any] = {"target": target, "product": product, "findings": [], "requests": 0, "errors": 0}
    if not base:
        return report
    paths = PRODUCT_PATHS.get(product, PRODUCT_PATHS[DEFAULT_PRODUCT])
    client = client_factory(timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT})
    try:
        for path in paths:
            if len(report["findings"]) >= request_budget or report["requests"] >= request_budget:
                break
            probe_url = f"{base}{path}"
            try:
                response = client.get(probe_url)
            except httpx.HTTPError:
                report["errors"] += 1
                continue
            report["requests"] += 1
            if len(response.history) > PROBE_MAX_REDIRECTS:
                continue
            if response.is_success:
                snippet = redact_evidence(response.text)[:EVIDENCE_CHARS]
                report["findings"].append(
                    build_exposure_item(
                        probe_url=probe_url,
                        product=product,
                        host=target,
                        snippet=snippet,
                        source_engine=source_engine,
                        query=query,
                        ip=ip,
                        port=port,
                        protocol=protocol,
                        evidence_path=path,
                    )
                )
    finally:
        client.close()
    return report


# ---------------------------------------------------------------------------
# lane 编排(源级结构化降级,不报错)
# ---------------------------------------------------------------------------


def _run_fofa_lane(
    *,
    key: str | None,
    base_url: str | None,
    queries: Sequence[str],
    product_hints: Mapping[str, str] | None,
    timeout: float,
    client_factory: Callable[..., httpx.Client],
    sleeper: Callable[[float], None],
) -> tuple[dict[str, Any], list[tuple[str, ExposureHit]]]:
    """FOFA lane:无 key/无 base 显式空态;逐查询搜页,失败记错继续。"""
    if not _first_key(key):
        return {"status": "credential_missing", "queries_planned": 0, "queries_total": len(queries),
                "hits": 0, "errors": 0, "per_query": []}, []
    if not (base_url or "").strip():
        return {"status": "base_missing", "queries_planned": 0, "queries_total": len(queries),
                "hits": 0, "errors": 0, "per_query": []}, []
    planned = apply_query_budget(queries, budget=FOFA_QUERY_BUDGET)
    try:
        client = FofaClient(key=key or "", base_url=base_url or "", timeout=timeout, client_factory=client_factory)
    except ExposureClientError as exc:  # base 形状坏:源级降级,不拖垮整 run(规格 §5)
        return {"status": "degraded", "queries_planned": 0, "queries_total": len(queries),
                "hits": 0, "errors": 1, "per_query": [], "source_error": str(exc)[:ERROR_PREVIEW_CHARS]}, []
    collected: list[tuple[str, ExposureHit]] = []
    per_query: list[dict[str, Any]] = []
    error_count = 0
    for query in planned:
        hint = (product_hints or {}).get(query, "")
        hits, errors = fofa_search_all(client, query, sleeper=sleeper)
        collected.extend((query, replace(hit, product_hint=hint)) for hit in hits)
        per_query.append({"query": query, "hits": len(hits), "errors": errors})
        error_count += len(errors)
    hits_total = len(collected)
    status = "degraded" if error_count else ("ok" if hits_total else "empty")
    lane = {"status": status, "queries_planned": len(planned), "queries_total": len(queries),
            "hits": hits_total, "errors": error_count, "per_query": per_query}
    return lane, collected


def _run_shodan_lane(
    *,
    key: str | None,
    base_url: str,
    queries: Sequence[str],
    product_hints: Mapping[str, str] | None,
    timeout: float,
    client_factory: Callable[..., httpx.Client],
    sleeper: Callable[[float], None],
) -> tuple[dict[str, Any], list[tuple[str, ExposureHit]]]:
    """Shodan lane:无 key 显式空态;逐查询搜页,失败记错继续。"""
    if not _first_key(key):
        return {"status": "credential_missing", "queries_planned": 0, "queries_total": len(queries),
                "hits": 0, "errors": 0, "per_query": []}, []
    planned = apply_query_budget(queries, budget=SHODAN_QUERY_BUDGET)
    try:
        client = ShodanClient(key=key or "", base_url=base_url, timeout=timeout, client_factory=client_factory)
    except ExposureClientError as exc:  # base 形状坏:源级降级,不拖垮整 run(规格 §5)
        return {"status": "degraded", "queries_planned": 0, "queries_total": len(queries),
                "hits": 0, "errors": 1, "per_query": [], "source_error": str(exc)[:ERROR_PREVIEW_CHARS]}, []
    collected: list[tuple[str, ExposureHit]] = []
    per_query: list[dict[str, Any]] = []
    error_count = 0
    for query in planned:
        hint = (product_hints or {}).get(query, "")
        hits, errors = shodan_search_all(client, query, sleeper=sleeper)
        collected.extend((query, replace(hit, product_hint=hint)) for hit in hits)
        per_query.append({"query": query, "hits": len(hits), "errors": errors})
        error_count += len(errors)
    hits_total = len(collected)
    status = "degraded" if error_count else ("ok" if hits_total else "empty")
    lane = {"status": status, "queries_planned": len(planned), "queries_total": len(queries),
            "hits": hits_total, "errors": error_count, "per_query": per_query}
    return lane, collected


def run_exposure(
    *,
    fofa_key: str | None = None,
    fofa_base: str | None = None,
    shodan_key: str | None = None,
    shodan_base: str = SHODAN_BASE_URL,
    fofa_queries: Sequence[str] = (),
    shodan_queries: Sequence[str] = (),
    product_hints: Mapping[str, str] | None = None,
    probe: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """跑一次曝面发现:FOFA + Shodan 搜索 → L0 被动探测 → 掩码-only findings。

    降级语义(规格 §5,AC6):无 key 的源报 ``credential_missing``、有 FOFA
    key 无 base 报 ``base_missing``,均为显式空态**不抛错**;查询页失败记
    入 lane errors 后继续下一查询;单源 0 命中(status=empty)≠ 失败。
    密钥经参数注入(宿主解析 keychain: 引用),本函数不读环境变量。

    Returns:
        结构化 payload:``status``(success/empty)、``lanes``(逐源报告:
        status/queries_planned/queries_total/hits/errors/per_query)、
        ``counts``、``findings``(管线 items;证据掩码 + 512 截断)与
        ``duration_seconds``。原始 12 字段命中(banner/header/cert)只存在
        于函数作用域内,不进 payload(私有情报红线:中间物不外发)。
    """
    started = clock()
    fofa_lane, fofa_hits = _run_fofa_lane(
        key=fofa_key, base_url=fofa_base, queries=fofa_queries, product_hints=product_hints,
        timeout=timeout, client_factory=client_factory, sleeper=sleeper,
    )
    shodan_lane, shodan_hits = _run_shodan_lane(
        key=shodan_key, base_url=shodan_base, queries=shodan_queries, product_hints=product_hints,
        timeout=timeout, client_factory=client_factory, sleeper=sleeper,
    )
    findings: list[dict[str, Any]] = []
    counts = {
        "fofa_hits": fofa_lane["hits"],
        "shodan_hits": shodan_lane["hits"],
        "probe_targets": 0,
        "probe_requests": 0,
        "probe_errors": 0,
        "findings": 0,
    }
    if probe:
        seen: set[tuple[str, str]] = set()
        for engine, lane_hits in (("fofa", fofa_hits), ("shodan", shodan_hits)):
            for query, hit in lane_hits:
                target = hit.probe_target_value()
                base = normalize_scheme(target)
                if not base:
                    continue
                product = resolve_product(hit, product_hints, query=query)
                if (base, product) in seen:
                    continue
                seen.add((base, product))
                counts["probe_targets"] += 1
                report = probe_target(
                    target, product,
                    timeout=timeout, client_factory=client_factory,
                    source_engine=engine, query=query,
                    ip=hit.ip, port=hit.port, protocol=hit.protocol,
                )
                counts["probe_requests"] += report["requests"]
                counts["probe_errors"] += report["errors"]
                findings.extend(report["findings"])
    counts["findings"] = len(findings)
    return {
        "status": "success" if findings else "empty",
        "lanes": {"fofa": fofa_lane, "shodan": shodan_lane},
        "counts": counts,
        "findings": findings,
        "duration_seconds": round(clock() - started, 3),
    }
