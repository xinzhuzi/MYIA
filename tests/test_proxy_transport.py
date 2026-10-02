"""Tests for the v0.2 proxy transport (PRD 10-01-v02-proxy-transport).

Acceptance list covered:

- direct / pool 选择:direct keeps the shared context client; ``pool:<name>``
  mounts ONE shared proxied AsyncClient per pool (mock-verified ``proxy=``
  kwarg — the recorder delegates to a MockTransport client, 零真实网络);
- http 与 socks5 代理 URL 解析:two-ref / single-full-credential / keychain
  (slash names) / anonymous,明文凭据加载期拒载;
- 代理失败错误分类:ProxyError -> ``proxy_error``,超时 -> ``proxy_timeout``,
  其余传输错误 -> ``proxy_network``,直连失败保持既有分类(代理挂 ≠ 源死);
- doctor 连通性检测(:func:`check_proxy_connectivity`)与 URL 打码。

All I/O runs on httpx.MockTransport; all waiting is recorded by FakeClock
(conftest helpers). 真实代理往返不在 CI:见任务 openIssues(需主人手动验证)。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from myia.engines.direct_api import DirectAPIEngine
from myia.engines.fetch_base import (
    DEFAULT_PROXY_PROBE_URL,
    ProxyConfigError,
    ProxyNotSupportedError,
    ProxyTransportError,
    BaseEngine,
    check_proxy_connectivity,
    classify_exception,
    classify_proxy_transport,
    expand_proxy_url,
    load_proxy_pools,
    load_proxy_pools_file,
    mask_proxy_url,
)
from myia.schema import CredentialResolveError, LoadError
from myia.secrets import InMemoryKeychainBackend

from conftest import make_client, make_context, make_handler, make_source, run


# ----------------------------------------------------------------- helpers


def json_source(**overrides: Any):
    """A minimal DirectAPIEngine-compatible source with json_path extract."""
    overrides.setdefault("extract", {"type": "json_path", "fields": {"url": "$[*].u"}})
    return make_source(**overrides)


def recorder_client(
    monkeypatch, handler: Callable[[httpx.Request], httpx.Response]
) -> tuple[list[dict[str, Any]], list[httpx.AsyncClient]]:
    """替身 ``httpx.AsyncClient``:记录每次构建的 ``proxy=`` 参数。

    The recorder captures the ``proxy=`` kwarg (MockTransport cannot observe
    it) and delegates to a real AsyncClient on MockTransport so requests flow
    through the injected handler — 验证的是「挂了什么代理」,流量走假传输层。
    Patch AFTER building the context base client (make_pool_context first),
    so only the pool-client constructions are recorded.
    Returns (recorded kwargs per construction, constructed clients).
    """
    recorded: list[dict[str, Any]] = []
    clients: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient  # 捕获真类:补丁替换的是 httpx 模块属性

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        recorded.append(dict(kwargs))
        kwargs.pop("proxy", None)  # MockTransport 与 proxy= 参数互斥
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        client = real_client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("myia.engines.fetch_base.httpx.AsyncClient", factory)
    return recorded, clients


def make_pool_context(handler, pools: dict[str, str] | None):
    """Context (with base client) carrying the given pool declaration."""
    client = make_client(make_handler(handler))
    context, _clock = make_context(client)
    context.proxy_pools = load_proxy_pools({"pools": pools}) if pools is not None else None
    return context


# ------------------------------------------- pools 声明与代理 URL 解析


def test_load_proxy_pools_two_refs_resolve_to_concrete_url(monkeypatch):
    monkeypatch.setenv("MYIA_T_PROXY_USER", "alice")
    monkeypatch.setenv("MYIA_T_PROXY_PASS", "s3cr et@x")
    pools = load_proxy_pools(
        {"pools": {"main": "http://env:MYIA_T_PROXY_USER:env:MYIA_T_PROXY_PASS@proxy.example.com:8080"}}
    )
    resolved = pools.resolve("main")
    assert resolved == "http://alice:s3cr%20et%40x@proxy.example.com:8080"  # 值被百分号编码
    assert "s3cr et@x" not in mask_proxy_url(resolved)


def test_load_proxy_pools_single_ref_resolves_full_user_pass(monkeypatch):
    monkeypatch.setenv("MYIA_T_PROXY_CRED", "bob:hunter2")
    pools = load_proxy_pools(
        {"pools": {"main": "http://env:MYIA_T_PROXY_CRED@proxy.example.com:8080"}}
    )
    assert pools.resolve("main") == "http://bob:hunter2@proxy.example.com:8080"


def test_load_proxy_pools_keychain_ref_resolves_via_injected_backend():
    backend = InMemoryKeychainBackend()
    backend.set_password("myia", "myia/proxy/main", "kcuser:kcpass")  # mock 钥匙串,零真实触碰
    pools = load_proxy_pools(
        {"pools": {"socks": "socks5://keychain:myia/proxy/main@10.0.0.1:1080"}}
    )
    assert pools.resolve("socks", backend=backend) == "socks5://kcuser:kcpass@10.0.0.1:1080"


def test_load_proxy_pools_anonymous_url_round_trips():
    pools = load_proxy_pools({"pools": {"anon": "http://10.0.0.2:3128"}})
    assert pools.resolve("anon") == "http://10.0.0.2:3128"


def test_load_proxy_pools_plaintext_userinfo_refused_at_load():
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools({"pools": {"main": "http://alice:secret@proxy.example.com:8080"}})
    detail = excinfo.value.errors[0]
    assert detail.error_type == "credential_plaintext"  # 明文凭据 = 启动即拒
    assert detail.path == "$.pools.main"


def test_load_proxy_pools_unsupported_scheme_refused_at_load():
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools({"pools": {"main": "ftp://proxy.example.com:21"}})
    assert excinfo.value.errors[0].error_type == "unsupported_proxy_scheme"


def test_load_proxy_pools_missing_host_refused_at_load():
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools({"pools": {"main": "http://"}})
    assert excinfo.value.errors[0].error_type == "invalid_proxy_url"


def test_load_proxy_pools_invalid_pool_name_refused_at_load():
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools({"pools": {"bad!": "http://proxy.example.com:8080"}})
    assert excinfo.value.errors[0].error_type == "invalid_pool_name"


def test_load_proxy_pools_non_mapping_section_refused_at_load():
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools({"pools": "main"})
    assert excinfo.value.errors[0].error_type == "invalid_pools"


def test_load_proxy_pools_missing_section_yields_empty_declaration():
    pools = load_proxy_pools({"other_section": 1})  # 全局配置可以无 pools 节
    assert pools.names() == []


def test_load_proxy_pools_file_reads_validates_and_refuses_bad_files(tmp_path):
    good = tmp_path / "config.yaml"
    good.write_text(
        'pools:\n  main: "http://env:MYIA_T_PROXY_USER@proxy.example.com:8080"\n',
        encoding="utf-8",
    )
    assert load_proxy_pools_file(good).names() == ["main"]

    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools_file(tmp_path / "missing.yaml")
    assert excinfo.value.errors[0].error_type == "file_not_found"

    duplicate = tmp_path / "dup.yaml"
    duplicate.write_text('pools:\n  a: "http://h:1"\n  a: "http://h:2"\n', encoding="utf-8")
    with pytest.raises(LoadError) as excinfo:
        load_proxy_pools_file(duplicate)
    assert excinfo.value.errors[0].error_type == "yaml_parse_error"  # 重复键拒载


def test_expand_proxy_url_missing_env_is_structured(monkeypatch):
    monkeypatch.delenv("MYIA_T_PROXY_MISSING", raising=False)
    with pytest.raises(CredentialResolveError) as excinfo:
        expand_proxy_url("http://env:MYIA_T_PROXY_MISSING@proxy.example.com:8080")
    assert excinfo.value.code == "env_var_missing"


def test_expand_proxy_url_noncanonical_keychain_name_is_structured():
    with pytest.raises(CredentialResolveError) as excinfo:
        expand_proxy_url("http://keychain:legacy_flat@proxy.example.com:8080")
    assert excinfo.value.code == "invalid_secret_name"  # 规范名 myia/<scope>/<name>


# ------------------------------------------------- direct / pool transport 选择


def test_fetch_with_pool_proxy_mounts_shared_proxied_client(monkeypatch):
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # 挂载后的传输层(机器人+数据)
        hits.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        return httpx.Response(200, json=[{"u": "https://a.example/1"}])

    def origin_responder(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("direct 传输层不应收到任何请求")

    monkeypatch.setenv("MYIA_T_PROXY_CRED", "bob:hunter2")
    context = make_pool_context(
        origin_responder, {"main": "http://env:MYIA_T_PROXY_CRED@proxy.example.com:8080"}
    )
    recorded, _clients = recorder_client(monkeypatch, handler)
    engine = DirectAPIEngine(json_source(proxy="pool:main"), context)

    items = run(engine.fetch())
    assert items == [{"url": "https://a.example/1"}]  # 请求真的走了挂载后的传输层
    assert recorded == [{"proxy": "http://bob:hunter2@proxy.example.com:8080", "timeout": 30.0}]
    assert engine._active_pool == "main"
    assert context.pool_clients["main"] is engine._active_client
    assert hits  # 机器人检查 + 数据请求都发生在代理传输层的 mock 上


def test_fetch_with_pool_proxy_reuses_one_client_across_sources(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        return httpx.Response(200, json=[{"u": "https://a.example/1"}])

    context = make_pool_context(handler, {"main": "http://10.0.0.1:8080"})
    recorded, _clients = recorder_client(monkeypatch, handler)
    first = DirectAPIEngine(json_source(proxy="pool:main"), context)
    second = DirectAPIEngine(json_source(name="second", proxy="pool:main"), context)
    run(first.fetch())
    run(second.fetch())
    assert len(recorded) == 1  # 同池只构建一次,多源共享
    assert first._active_client is second._active_client


def test_fetch_direct_keeps_context_client(monkeypatch):
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=[{"u": "https://a.example/1"}])

    context = make_pool_context(responder, pools=None)
    recorded, _clients = recorder_client(monkeypatch, make_handler(responder))
    engine = DirectAPIEngine(json_source(proxy="direct"), context)
    items = run(engine.fetch())
    assert items == [{"url": "https://a.example/1"}]
    assert recorded == []  # direct 不构建任何新客户端
    assert engine._active_client is context.client
    assert calls["count"] >= 1


def test_fetch_with_unknown_pool_refuses_before_network(monkeypatch):
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1  # pragma: no cover - 不应发生
        return httpx.Response(200, json=[])

    context = make_pool_context(responder, {"main": "http://10.0.0.1:8080"})
    recorder_client(monkeypatch, make_handler(responder))
    engine = DirectAPIEngine(json_source(proxy="pool:nope"), context)
    with pytest.raises(ProxyConfigError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.code == "proxy_pool_unknown"
    assert calls["count"] == 0  # 任何 I/O 之前拒绝


def test_fetch_pool_without_pools_declaration_refuses_before_network(monkeypatch):
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1  # pragma: no cover - 不应发生
        return httpx.Response(200, json=[])

    context = make_pool_context(responder, pools=None)
    engine = DirectAPIEngine(json_source(proxy="pool:main"), context)
    with pytest.raises(ProxyConfigError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.code == "proxy_pools_not_configured"
    assert calls["count"] == 0


def test_fetch_residential_refuses_before_network(monkeypatch):
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1  # pragma: no cover - 不应发生
        return httpx.Response(200, json=[])

    context = make_pool_context(responder, pools=None)
    engine = DirectAPIEngine(json_source(proxy="residential:us"), context)
    with pytest.raises(ProxyNotSupportedError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.scheduled_version == "v0.3"  # 住宅代理维持未实装(排期不变)
    assert calls["count"] == 0


def test_client_for_pool_bad_scheme_is_structured():
    context = make_pool_context(lambda request: httpx.Response(200, json=[]), pools=None)
    with pytest.raises(ProxyConfigError) as excinfo:
        context.client_for_pool("bad", "ftp://10.0.0.1:21")
    assert excinfo.value.code == "invalid_proxy_url"


def test_robots_check_rides_pool_transport(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, json=[{"u": "https://a.example/1"}])

    def origin_responder(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("direct 传输层不应收到任何请求")

    context = make_pool_context(origin_responder, {"main": "http://10.0.0.1:8080"})
    recorder_client(monkeypatch, handler)
    engine = DirectAPIEngine(json_source(proxy="pool:main"), context)
    run(engine.fetch())
    assert "/robots.txt" in seen  # robots.txt 与数据同出口(代理视角判定放行)
    assert context.pool_robots["main"] is engine._active_robots


# ----------------------------------------------------- 代理失败错误分类


def _pool_engine_failing(monkeypatch, fail_with: Exception, *, retry: int = 2):
    """Pool source whose proxied transport fails every data request."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        calls["count"] += 1
        raise fail_with

    def origin_responder(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("direct 传输层不应收到任何请求")

    context = make_pool_context(
        origin_responder,
        {"main": "http://env:MYIA_T_PU:env:MYIA_T_PP@10.0.0.1:8080"},
    )
    recorder_client(monkeypatch, handler)
    engine = DirectAPIEngine(json_source(proxy="pool:main", retry=retry), context)
    return engine, calls


def test_proxy_error_through_pool_classifies_proxy_error(monkeypatch):
    monkeypatch.setenv("MYIA_T_PU", "alice")
    monkeypatch.setenv("MYIA_T_PP", "hunter2")
    engine, calls = _pool_engine_failing(monkeypatch, httpx.ProxyError("proxy auth failed (407)"))
    with pytest.raises(ProxyTransportError) as excinfo:
        run(engine.fetch())
    failure = excinfo.value
    assert failure.error_type == "proxy_error"  # 代理握手/认证失败
    assert failure.pool == "main"
    assert calls["count"] == 3  # 重试预算照常生效(1 + retry 2)
    assert "pool=main" in str(failure)
    assert "hunter2" not in str(failure)  # 凭据不落错误信息
    assert classify_exception(failure) == "proxy_error"


def test_proxy_connect_error_classifies_proxy_network(monkeypatch):
    monkeypatch.setenv("MYIA_T_PU", "alice")
    monkeypatch.setenv("MYIA_T_PP", "hunter2")
    engine, _calls = _pool_engine_failing(monkeypatch, httpx.ConnectError("proxy host unreachable"))
    with pytest.raises(ProxyTransportError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "proxy_network"


def test_proxy_timeout_classifies_proxy_timeout(monkeypatch):
    monkeypatch.setenv("MYIA_T_PU", "alice")
    monkeypatch.setenv("MYIA_T_PP", "hunter2")
    engine, _calls = _pool_engine_failing(monkeypatch, httpx.ConnectTimeout("proxy did not answer"))
    with pytest.raises(ProxyTransportError) as excinfo:
        run(engine.fetch())
    assert excinfo.value.error_type == "proxy_timeout"


def test_direct_transport_failure_stays_unwrapped(monkeypatch):
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        calls["count"] += 1
        raise httpx.ConnectError("connection refused")

    context = make_pool_context(responder, pools=None)
    engine = BaseEngine(make_source(retry=1), context)
    with pytest.raises(httpx.ConnectError) as excinfo:  # 原样抛出,不包成代理错误
        run(engine.request("https://example.com/data"))
    assert not isinstance(excinfo.value, ProxyTransportError)
    assert classify_exception(excinfo.value) == "network"  # 源失败保持既有分类
    assert calls["count"] == 2


def test_classify_proxy_transport_maps_exception_kinds():
    assert classify_proxy_transport(httpx.ProxyError("auth")) == "proxy_error"
    assert classify_proxy_transport(httpx.ReadTimeout("slow")) == "proxy_timeout"
    assert classify_proxy_transport(httpx.ConnectError("down")) == "proxy_network"
    assert classify_proxy_transport(RuntimeError("not a transport error")) == "network"


# ------------------------------------------- doctor 连通性检测与 URL 打码


def test_check_proxy_connectivity_reports_ok_latency_and_exit_ip():
    ticks = iter([10.0, 10.25])

    def clock() -> float:
        return next(ticks)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == DEFAULT_PROXY_PROBE_URL  # 默认探测端点
        return httpx.Response(200, json={"ip": "203.0.113.7"})

    probe = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = run(
        check_proxy_connectivity(
            "http://bob:hunter2@proxy.example.com:8080", client=probe, clock=clock
        )
    )
    assert result.ok is True
    assert result.exit_ip == "203.0.113.7"
    assert result.status_code == 200
    assert result.latency_seconds == 0.25
    assert result.proxy_url_masked == "http://***@proxy.example.com:8080"
    assert result.to_dict()["ok"] is True  # doctor --json 消费形态
    assert probe.is_closed is False  # 注入的客户端生命周期归调用方
    run(probe.aclose())


def test_check_proxy_connectivity_classifies_proxy_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy authentication required")

    probe = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = run(check_proxy_connectivity("http://10.0.0.1:8080", client=probe))
    assert result.ok is False
    assert result.error_type == "proxy_error"
    run(probe.aclose())


def test_check_proxy_connectivity_classifies_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("proxy did not answer")

    probe = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = run(check_proxy_connectivity("http://10.0.0.1:8080", client=probe))
    assert result.ok is False
    assert result.error_type == "proxy_timeout"
    run(probe.aclose())


def test_check_proxy_connectivity_http_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    probe = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = run(check_proxy_connectivity("http://10.0.0.1:8080", client=probe))
    assert result.ok is False
    assert result.error_type == "http_502"
    assert result.status_code == 502
    run(probe.aclose())


def test_check_proxy_connectivity_builds_and_closes_own_client(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ip": "198.51.100.9"})

    recorded, clients = recorder_client(monkeypatch, handler)
    result = run(check_proxy_connectivity("socks5://10.0.0.1:1080"))
    assert result.ok is True
    assert recorded == [{"proxy": "socks5://10.0.0.1:1080", "timeout": 10.0}]  # 自建客户端带代理
    assert len(clients) == 1 and clients[0].is_closed  # 返回前关闭自建客户端


def test_mask_proxy_url_hides_credentials_but_keeps_host():
    masked = mask_proxy_url("socks5://bob:hunter2@proxy.example.com:1080")
    assert masked == "socks5://***@proxy.example.com:1080"
    assert "hunter2" not in masked
    assert mask_proxy_url("http://10.0.0.1:3128") == "http://10.0.0.1:3128"  # 无凭据原样
