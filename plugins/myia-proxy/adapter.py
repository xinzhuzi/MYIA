"""myia-proxy 适配器:进程内轻量代理抓取+测活(零 Redis 零 docker)——MYIA 侧代码.

本文件由 MYIA 仓库创作与维护,**不是**上游 proxy_pool(jhao104/proxy_pool,
MIT)的代码:参照其「fetch → 校验 → 取用」思路自实现的精简版,零上游源码
复制、零 vendored。桌面路径(默认)就是本文件:进程内一次性完成

1. **抓取**:逐个请求公开免费代理列表源(纯文本 ``ip:port`` 行,见
   :data:`FETCH_SOURCES`;每源一次礼貌 GET,失败互相隔离不拖垮);
2. **测活**:候选逐个经代理向 :data:`CHECK_TARGET` 发一次 GET(限速式串行,
   单代理超时 :data:`DEFAULT_CHECK_TIMEOUT_SECONDS`),有响应且状态码
   ``< 400`` 记可用,并记录往返延迟;
3. **装配**:抓取/测活统计 + 可用代理列表(``proxy`` + ``latency_ms``)构成
   结构化 JSON 结果(``myia proxy --json`` 的 AI 消费路径)。

精简边界(诚实声明):无定时抓取、无存储池、无 HTTP API —— 完整 proxy_pool
服务形态(定时 + Redis 池 + API)是**服务端可选**路径,compose 在仓库
``docker/plugins/myia-proxy/``(桌面零 docker;已部署实例按 remote 模式填
endpoint,见本插件 README)。

错误契约(.trellis/spec/python/error-handling):所有失败都抛
:class:`ProxyAdapterError`(code + message + 结构化 details,``to_dict()``
直接进 CLI 的 JSON 输出);禁止只有一句 str 的异常。code 词表:

- ``invalid_count``    count < 1(用法/配置错误 → CLI 退 1)
- ``invalid_timeout``  测活超时 ≤ 0(用法/配置错误 → CLI 退 1)
- ``fetch_failed``     全部源抓取失败(采集失败 → CLI 退 2)
- ``no_alive_proxy``   抓到候选但测活零可用(采集失败 → CLI 退 2)

失败码 → CLI 退出码的映射归 CLI 所有(``shishi.cli.PROXY_FETCH_FAILURE_CODES``,
spec python/error-handling 的退出码契约);适配器只负责如实上报 code。

铁律(security-baseline):适配器任何失败只影响 ``myia proxy`` 自身,
核心品类流水线照常跑通(测试钉在 tests/test_proxy_plugin.py)。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

__all__ = [
    "CHECK_TARGET",
    "DEFAULT_CHECK_TIMEOUT_SECONDS",
    "DEFAULT_COUNT",
    "FETCH_SOURCES",
    "MAX_CHECKS",
    "MAX_CANDIDATES",
    "ProxyAdapterError",
    "check_proxy",
    "fetch_candidates",
    "parse_proxy_lines",
    "run",
]

#: 公开免费代理列表源(纯文本,每行一条 ``ip:port``;仓库即公开,零凭据)。
FETCH_SOURCES = (
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000",
)

#: 测活目标(http,公开合法演示域;任何 <400 状态都算代理通)。
CHECK_TARGET = "http://example.com/"

#: 单代理测活超时缺省秒数。
DEFAULT_CHECK_TIMEOUT_SECONDS = 10.0

#: 期望可用代理数缺省值(凑够即提前停止测活)。
DEFAULT_COUNT = 5

#: 候选去重后的测活上限(诚实有界:免费列表动辄数千条,逐个测活必须有界)。
MAX_CHECKS = 60

#: 单源解析入池的行数上限(防御异常巨大的响应体)。
MAX_CANDIDATES_PER_SOURCE = 2000

#: 候选总池上限(跨源去重后再截断)。
MAX_CANDIDATES = 1000

#: 合法 ``ip:port`` 行(IPv4;端口 1-65535)。其余行(注释/IPv6/垃圾)静默跳过。
_PROXY_LINE_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$")

#: 结构化错误里响应体摘录的上限(保持 JSON 输出有界)。
_TAIL_CHARS = 500


class ProxyAdapterError(Exception):
    """结构化适配器错误:code + message + details,``to_dict()`` 进 JSON 输出."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """结构化形态:{"code", "message", **details}(供 CLI --json 与 agent 自修)."""
        return {"code": self.code, "message": self.message, **self.details}


def _tail(text: str | None) -> str:
    """响应体摘录(头部一段),保持结构化错误有界."""
    value = (text or "").strip()
    return value[:_TAIL_CHARS]


def parse_proxy_lines(text: str | None, *, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[str]:
    """从纯文本响应解析 ``ip:port`` 行:去空白、去重、丢弃非法行,截断到 limit.

    顺序保持源内出现序(测活按序进行,先到先测);重复条目只保留首个。
    """
    seen: set[str] = set()
    proxies: list[str] = []
    for raw in (text or "").splitlines():
        candidate = raw.strip()
        match = _PROXY_LINE_RE.match(candidate)
        if match is None:
            continue
        ip, port = match.group(1), match.group(2)
        if int(port) < 1 or int(port) > 65535 or ip.count(".") != 3:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        proxies.append(candidate)
        if len(proxies) >= limit:
            break
    return proxies


def fetch_candidates(
    *,
    timeout: float = 30.0,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> tuple[list[str], list[dict[str, Any]]]:
    """逐源抓取代理候选;返回 ``(candidates, per_source_reports)``.

    每源一次礼貌 GET(独立超时;失败互相隔离,结构化进报告不拖垮其余源);
    全部源失败由调用方据报告判定(:func:`run` 抛 ``fetch_failed``)。
    """
    candidates: list[str] = []
    seen: set[str] = set()
    reports: list[dict[str, Any]] = []
    for source in FETCH_SOURCES:
        report: dict[str, Any] = {"url": source, "proxies": 0, "error": None}
        client = client_factory(timeout=timeout, follow_redirects=True, headers={"User-Agent": "myia-proxy-adapter/1.1"})
        try:
            try:
                response = client.get(source)
                response.raise_for_status()
            finally:
                client.close()
        except httpx.HTTPError as exc:
            report["error"] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            reports.append(report)
            continue
        parsed = parse_proxy_lines(response.text)
        fresh = [proxy for proxy in parsed if proxy not in seen]
        seen.update(fresh)
        candidates.extend(fresh)
        report["proxies"] = len(fresh)
        reports.append(report)
    if len(candidates) > MAX_CANDIDATES:
        candidates = candidates[:MAX_CANDIDATES]
    return candidates, reports


def check_proxy(
    proxy: str,
    *,
    timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    check_target: str = CHECK_TARGET,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """对单个代理测活:经代理 GET 校验目标,可用返回 ``{"proxy", "latency_ms"}``,不可用返回 None.

    判活口径:有响应且状态码 ``< 400``(代理连通即算,不深究目标业务语义);
    网络异常/超时/代理拒绝一律不可用(免费代理的常态,静默跳过)。
    """
    client = client_factory(timeout=timeout, proxy=f"http://{proxy}")
    started = clock()
    try:
        try:
            response = client.get(check_target)
        finally:
            client.close()
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    latency_ms = int(round((clock() - started) * 1000))
    return {"proxy": f"http://{proxy}", "latency_ms": latency_ms}


def run(
    *,
    count: int = DEFAULT_COUNT,
    check_timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    fetch_timeout: float = 30.0,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """跑一次「抓取 + 测活」,返回结构化 JSON 结果.

    Args:
        count: 期望可用代理数(凑够即提前停止测活)。
        check_timeout: 单代理测活超时秒数。
        fetch_timeout: 单源列表抓取超时秒数。
        client_factory: httpx.Client 工厂注入口(测试 MockTransport 用)。
        clock: 单调时钟注入口(测试 mock 用)。

    Returns:
        结构化结果 dict:plugin/mode/status/requested_count/sources(逐源
        报告)/fetched(去重后候选)/checked(已测活数)/alive(可用代理)、
        duration_seconds。

    Raises:
        ProxyAdapterError: 全部失败形态都是结构化错误(见模块 docstring 词表)。
    """
    if count < 1:
        raise ProxyAdapterError(
            "invalid_count",
            f"--count 必须 ≥ 1,当前为 {count}",
            count=count,
        )
    if check_timeout <= 0:
        raise ProxyAdapterError(
            "invalid_timeout",
            f"测活超时必须为正秒数,当前为 {check_timeout}",
            check_timeout=check_timeout,
        )
    started = clock()
    candidates, reports = fetch_candidates(timeout=fetch_timeout, client_factory=client_factory)
    fetched = len(candidates)
    if fetched == 0:
        raise ProxyAdapterError(
            "fetch_failed",
            "全部代理列表源抓取失败(无任何候选);检查网络可达性后重试,"
            "或改用已部署 proxy_pool 服务的 remote 模式(见插件 README)",
            sources=reports,
        )
    alive: list[dict[str, Any]] = []
    checked = 0
    for candidate in candidates[:MAX_CHECKS]:
        if len(alive) >= count:
            break
        verdict = check_proxy(candidate, timeout=check_timeout, client_factory=client_factory, clock=clock)
        checked += 1
        if verdict is not None:
            alive.append(verdict)
    if not alive:
        raise ProxyAdapterError(
            "no_alive_proxy",
            f"抓到 {fetched} 个候选,测活 {checked} 个全部不可用(免费代理常态);"
            "可重试、调大 --timeout,或改用已部署 proxy_pool 服务的 remote 模式"
            "(见插件 README)",
            fetched=fetched,
            checked=checked,
            sources=reports,
        )
    return {
        "plugin": "myia-proxy",
        "mode": "in_process",
        "status": "success",
        "requested_count": count,
        "sources": reports,
        "fetched": fetched,
        "checked": checked,
        "alive": alive,
        "duration_seconds": round(clock() - started, 3),
    }
