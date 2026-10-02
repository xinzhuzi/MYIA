"""myia-proxy 进程内插件(PRD 10-02-v11-plugins-source-arch 迁移)契约测试.

myia-proxy v1.1 迁移为 desktop 分级的进程内插件:适配器 adapter.py 参照
上游 proxy_pool(jhao104/proxy_pool,MIT)「fetch → 校验 → 取用」思路
**自实现精简版**(零源码复制、零 vendored、零 Redis 零 docker)。四组被
钉住的契约:

1. **manifest 形状**:tier=desktop、adapter(in_process)声明、vendor 缺省
   None(自实现,零上游 vendored)、零 requires;
2. **适配器纯函数**:ip:port 行解析(去重/非法行/有界)、逐源抓取(失败
   互相隔离、结构化逐源报告)、单代理测活(<400 记可用并记延迟);
3. **适配器 run 组装**:成功装配结构化 JSON;全部源失败 → ``fetch_failed``;
   测活零可用 → ``no_alive_proxy``;count/timeout 非法 → 用法错误码;
   测活有界(MAX_CHECKS),凑够即提前停;
4. **CLI ``myia proxy`` + 铁律**:适配器缺失退 1、用法错误退 1、采集失败
   (fetch_failed/no_alive_proxy)退 2;任何失败形态下核心品类加载与
   Pipeline 构造完全无感(装不上不拦核心)。

测试纪律:HTTP 一律 httpx.MockTransport 注入 client_factory(零真实网络)、
时钟注入口(零 sleep)、每测独立 tmp_path。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import httpx
import pytest

from myia.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_ALL_FAILED,
    EXIT_OK,
    PROXY_FETCH_FAILURE_CODES,
    _import_plugin_adapter,
    main,
)
from myia.pipeline import Pipeline
from myia.plugins.manifest import load_manifest_file
from myia.schema import load_category_file

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "myia-proxy"


def load_adapter() -> Any:
    """按 CLI 同款加载器(compile+exec)加载真实适配器."""
    return _import_plugin_adapter(PLUGINS_DIR, "myia-proxy")


@pytest.fixture()
def adapter() -> Any:
    return load_adapter()


# ---------------------------------------------------------------------------
# 契约一:manifest 形状(desktop 分级 + in_process 适配器 + 零 vendored)
# ---------------------------------------------------------------------------


class TestProxyManifest:
    def test_desktop_tier_with_in_process_adapter(self):
        manifest = load_manifest_file(PLUGIN_DIR / "plugin.yaml")
        assert manifest.tier == "desktop"
        assert manifest.requires == []
        assert manifest.adapter is not None
        assert manifest.adapter.entry == "adapter.py"
        assert manifest.adapter.mode == "in_process"
        assert manifest.vendor is None, "自实现精简版,不得 vendored 上游"
        assert manifest.modes.local is None
        assert manifest.modes.remote is not None


# ---------------------------------------------------------------------------
# 契约二:适配器纯函数(解析 / 抓取 / 测活)
# ---------------------------------------------------------------------------


class TestParseProxyLines:
    def test_parses_dedupes_and_keeps_order(self, adapter):
        text = "1.2.3.4:8080\nnot-a-proxy\n5.6.7.8:3128\n1.2.3.4:8080\n  9.9.9.9:80 \n"
        assert adapter.parse_proxy_lines(text) == ["1.2.3.4:8080", "5.6.7.8:3128", "9.9.9.9:80"]

    @pytest.mark.parametrize(
        "line",
        [
            "1.2.3:8080",          # 段数不足
            "1.2.3.4.5:8080",      # 段数超
            "1.2.3.4:",            # 端口缺失
            "1.2.3.4:0",           # 端口下界外
            "1.2.3.4:65536",       # 端口上界外
            "1.2.3.4:abc",         # 端口非数字
            "# comment",
            "",
        ],
    )
    def test_rejects_garbage_lines(self, adapter, line):
        assert adapter.parse_proxy_lines(line) == []

    def test_limit_bounds_output(self, adapter):
        text = "".join(f"10.0.0.{i}:8080\n" for i in range(1, 51))
        assert adapter.parse_proxy_lines(text, limit=10) == [f"10.0.0.{i}:8080" for i in range(1, 11)]


def make_client_factory(handler: Any) -> Any:
    """client 工厂注入口:MockTransport 拦截(零真实网络).

    生产适配器给测活传 ``proxy=``;httpx 里 proxy 会以 mount 优先于自定义
    transport,导致 MockTransport 被绕过、测试打出真实连接 —— 故测试工厂
    剥离 ``proxy`` kwargs(仅记录),MockTransport 直接应答。
    """
    seen_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> httpx.Client:
        seen_kwargs.update(kwargs)
        kwargs.pop("proxy", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    factory.seen_kwargs = seen_kwargs  # type: ignore[attr-defined]
    return factory


def fetch_and_check_handler(request: httpx.Request) -> httpx.Response:
    """默认桩:抓取源返回两行列表;测活目标返回 200。"""
    if request.url.host == "example.com":
        return httpx.Response(200, text="ok")
    return httpx.Response(200, text="1.2.3.4:8080\n5.6.7.8:3128\n")


class TestFetchAndCheck:
    def test_fetch_collects_across_sources_and_reports_each(self, adapter):
        # 三个源:前两行来自 TheSpeedX,proxyscrape 与其一重复(跨源去重)。
        sources_text = {
            "/TheSpeedX/PROXY-List/master/http.txt": "1.2.3.4:8080\n5.6.7.8:3128\n",
            "/monosans/proxy-list/main/proxies/http.txt": "",
            "/v2/": "5.6.7.8:3128\n9.9.9.9:80\n",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sources_text.get(request.url.path, ""))

        candidates, reports = adapter.fetch_candidates(client_factory=make_client_factory(handler))
        assert candidates == ["1.2.3.4:8080", "5.6.7.8:3128", "9.9.9.9:80"]
        assert [report["proxies"] for report in reports] == [2, 0, 1]
        assert all(report["error"] is None for report in reports)

    def test_fetch_isolates_per_source_failure(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.proxyscrape.com":
                return httpx.Response(503)
            return httpx.Response(200, text="1.2.3.4:8080\n")

        candidates, reports = adapter.fetch_candidates(client_factory=make_client_factory(handler))
        assert candidates == ["1.2.3.4:8080"]
        failed = next(report for report in reports if report["error"])
        assert "503" in failed["error"], "单源失败结构化进报告,不拖垮其余源"
        assert any(report["proxies"] == 1 for report in reports)

    def test_check_alive_reports_proxy_with_latency(self, adapter):
        ticks = iter([0.0, 0.25])
        factory = make_client_factory(lambda request: httpx.Response(200))

        verdict = adapter.check_proxy(
            "1.2.3.4:8080", client_factory=factory, clock=lambda: next(ticks)
        )
        assert factory.seen_kwargs["proxy"] == "http://1.2.3.4:8080", "测活必须经代理拨号"
        assert verdict == {"proxy": "http://1.2.3.4:8080", "latency_ms": 250}

    @pytest.mark.parametrize("status", [403, 502])
    def test_check_http_error_status_is_not_alive(self, adapter, status):
        verdict = adapter.check_proxy(
            "1.2.3.4:8080", client_factory=make_client_factory(lambda request: httpx.Response(status))
        )
        assert verdict is None

    def test_check_network_error_is_not_alive(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        verdict = adapter.check_proxy(
            "1.2.3.4:8080", client_factory=make_client_factory(handler)
        )
        assert verdict is None


# ---------------------------------------------------------------------------
# 契约三:run 组装(结构化成功 / 结构化失败词表 / 有界测活)
# ---------------------------------------------------------------------------


class _TickingClock:
    """假单调时钟:每次调用 +0.01s(供 run 与其内层 check_proxy 共用)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


class TestProxyRun:
    def test_success_assembles_structured_payload(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(200)
            return httpx.Response(200, text="\n".join(f"10.0.0.{i}:8080" for i in range(1, 11)))

        result = adapter.run(count=2, client_factory=make_client_factory(handler), clock=_TickingClock())
        assert result["plugin"] == "myia-proxy"
        assert result["mode"] == "in_process"
        assert result["status"] == "success"
        assert result["requested_count"] == 2
        assert result["fetched"] == 10
        assert len(result["alive"]) == 2
        assert all(item["proxy"].startswith("http://") for item in result["alive"])
        assert result["duration_seconds"] > 0
        assert result["sources"] and all(report["url"] for report in result["sources"])

    def test_stops_checking_once_count_reached(self, adapter):
        checked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                checked.append(str(request.url))
                return httpx.Response(200)
            return httpx.Response(200, text="\n".join(f"10.0.0.{i}:8080" for i in range(1, 31)))

        result = adapter.run(count=3, client_factory=make_client_factory(handler))
        assert len(result["alive"]) == 3
        assert len(checked) == 3, "凑够 count 即提前停止测活"

    def test_checks_are_bounded_by_max_checks(self, adapter):
        checked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                checked.append(str(request.url))
                return httpx.Response(502)  # 全部不可用
            return httpx.Response(200, text="\n".join(f"10.0.0.{i}:8080" for i in range(1, 501)))

        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(count=1, client_factory=make_client_factory(handler))
        assert exc_info.value.code == "no_alive_proxy"
        assert len(checked) == adapter.MAX_CHECKS, "测活必须有界,绝不逐个测完数千候选"

    def test_all_sources_failed_maps_to_fetch_failed(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(client_factory=make_client_factory(handler))
        error = exc_info.value
        assert error.code == "fetch_failed"
        assert "fetch_failed" in PROXY_FETCH_FAILURE_CODES, "采集失败码必须映射退出码 2"
        assert len(error.details["sources"]) == len(adapter.FETCH_SOURCES)

    def test_zero_alive_maps_to_no_alive_proxy(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(502)
            return httpx.Response(200, text="1.2.3.4:8080\n")

        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(client_factory=make_client_factory(handler))
        error = exc_info.value
        assert error.code == "no_alive_proxy"
        assert "no_alive_proxy" in PROXY_FETCH_FAILURE_CODES
        assert error.details["fetched"] == 1

    @pytest.mark.parametrize(("count", "timeout"), [(0, 10.0), (-3, 10.0)])
    def test_invalid_count_is_structured_usage_error(self, adapter, count, timeout):
        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(count=count, check_timeout=timeout)
        assert exc_info.value.code == "invalid_count"
        assert "invalid_count" not in PROXY_FETCH_FAILURE_CODES

    @pytest.mark.parametrize("timeout", [0.0, -1.0])
    def test_invalid_timeout_is_structured_usage_error(self, adapter, timeout):
        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(check_timeout=timeout)
        assert exc_info.value.code == "invalid_timeout"
        assert "invalid_timeout" not in PROXY_FETCH_FAILURE_CODES

    def test_error_payload_is_json_serializable(self, adapter):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(client_factory=make_client_factory(handler))
        assert json.loads(json.dumps(exc_info.value.to_dict()))["code"] == "fetch_failed"


# ---------------------------------------------------------------------------
# 契约四:CLI myia proxy(退出码 0/1/2)+ 铁律
# ---------------------------------------------------------------------------


STUB_ADAPTER = textwrap.dedent(
    """
    SCENARIO = "success"  # success | fetch_failed | no_alive | invalid_count

    class FakeError(Exception):
        def __init__(self, payload):
            super().__init__(payload["message"])
            self.payload = payload

        def to_dict(self):
            return self.payload

    def run(count=5, check_timeout=10.0, **kwargs):
        if SCENARIO == "fetch_failed":
            raise FakeError({"code": "fetch_failed", "message": "全部源抓取失败(桩)", "sources": []})
        if SCENARIO == "no_alive":
            raise FakeError({"code": "no_alive_proxy", "message": "测活零可用(桩)", "fetched": 3, "checked": 3})
        if SCENARIO == "invalid_count":
            raise FakeError({"code": "invalid_count", "message": "count 必须 ≥ 1(桩)", "count": count})
        return {
            "plugin": "myia-proxy",
            "mode": "in_process",
            "status": "success",
            "requested_count": count,
            "sources": [{"url": "stub", "proxies": 2, "error": None}],
            "fetched": 2,
            "checked": 2,
            "alive": [{"proxy": "http://1.2.3.4:8080", "latency_ms": 120}],
            "duration_seconds": 0.1,
        }
    """
)


def make_stub_plugins(tmp_path: Path, *, scenario: str = "success") -> Path:
    plugins = tmp_path / "plugins"
    plugin = plugins / "myia-proxy"
    plugin.mkdir(parents=True)
    source = STUB_ADAPTER.replace('SCENARIO = "success"', f'SCENARIO = {scenario!r}')
    (plugin / "adapter.py").write_text(source, encoding="utf-8")
    return plugins


class TestCliProxy:
    def test_success_json_contract(self, tmp_path, capsys):
        plugins = make_stub_plugins(tmp_path)
        code = main(["proxy", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugin"] == "myia-proxy"
        assert payload["status"] == "success"
        assert payload["alive"][0]["proxy"] == "http://1.2.3.4:8080"

    def test_success_human_output_is_not_json(self, tmp_path, capsys):
        plugins = make_stub_plugins(tmp_path)
        code = main(["proxy", "--plugins-dir", str(plugins)])
        out = capsys.readouterr().out
        assert code == EXIT_OK
        assert "MYIA proxy:" in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)

    def test_fetch_failed_exits_2(self, tmp_path, capsys):
        plugins = make_stub_plugins(tmp_path, scenario="fetch_failed")
        code = main(["proxy", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_FETCH_ALL_FAILED
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "fetch_failed"

    def test_no_alive_proxy_exits_2(self, tmp_path, capsys):
        plugins = make_stub_plugins(tmp_path, scenario="no_alive")
        code = main(["proxy", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_FETCH_ALL_FAILED
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "no_alive_proxy"

    def test_invalid_count_exits_1(self, tmp_path, capsys):
        plugins = make_stub_plugins(tmp_path, scenario="invalid_count")
        code = main(["proxy", "--count", "0", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "invalid_count"

    def test_adapter_file_missing_exits_1(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        code = main(["proxy", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "proxy_adapter_missing"

    def test_broken_adapter_source_exits_1_not_crash(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        (plugins / "myia-proxy").mkdir(parents=True)
        (plugins / "myia-proxy" / "adapter.py").write_text("def run(: broken", encoding="utf-8")
        code = main(["proxy", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "proxy_adapter_missing"


class TestIronLawProxyFailureNeverBlocksCore:
    """铁律回归:proxy 适配器任何装不上/失败形态,核心品类流水线完全无感。"""

    @staticmethod
    def _assert_core_unblocked() -> None:
        category_files = sorted(PLUGINS_DIR.glob("*.yaml"))
        assert category_files, "顶层品类示例不应为空"
        for path in category_files:
            config = load_category_file(path)
            assert config.sources, f"{path.name} 必须可加载且有源"
        Pipeline(load_category_file(PLUGINS_DIR / "wool.yaml"))

    def test_real_adapter_network_failure_keeps_core_unblocked(self, tmp_path):
        adapter = load_adapter()

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network unreachable")

        with pytest.raises(adapter.ProxyAdapterError) as exc_info:
            adapter.run(client_factory=make_client_factory(handler))
        assert exc_info.value.code == "fetch_failed"
        self._assert_core_unblocked()

    def test_adapter_not_importable_keeps_core_unblocked(self, tmp_path):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        assert main(["proxy", "--plugins-dir", str(plugins), "--json"]) == EXIT_CONFIG_ERROR
        self._assert_core_unblocked()
