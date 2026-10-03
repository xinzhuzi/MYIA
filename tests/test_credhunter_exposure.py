"""myia-credhunter 插件(R4 曝面发现线)契约测试.

任务 10-03-aipocket-fusion 曝面段(FOFA/Shodan 客户端 + L0 被动探测)的
配套测试。零网络(HTTP 一律 httpx.MockTransport 注入 client_factory,与
tests/test_proxy_plugin.py 同款先例)、零真实凭据(fixture 全部为合成
脱敏键与保留网段主机:203.0.113.0/24 = RFC 5737 文档专用段,*.example.com
= RFC 2606 保留域)。六组被钉住的契约:

1. **FOFA 客户端**:qbase64(UTF-8→base64)/单 key(多 key 只用第一个)/
   固定 12 字段 fields/数组与对象两形行映射(body 空时别名进 banner)/
   非 2xx 带 200 字符预览/非 JSON/响应 error 标记/check() 探针查询;
2. **FOFA 分页与预算**:返回行数 < size 提前停页、页间 0.3s、每 run 前
   24 条查询(去重保序)、页失败终止该查询保留已取页;
3. **Shodan 客户端**:FOFA 查询机械翻译三规则(body=→http.html:/&&→
   空格/||→OR)、行归一化三级兜底(hostnames[0]→http.host→ip_str)、
   非 80/443 拼端口、443→https、data→header、http.html→banner、ssl
   CN 拼进 cert、count 取 total、页间 1.0s、预算 16;
4. **L0 门控(fail-closed)**:非 unauth_read / risk>0 / intrusive 一律
   拒绝;产品路径集照规格 §4(flowise/langflow 更强证据路径刻意不进
   默认集);scheme 归一(裸 host 补 http、:443 补 https);
5. **被动探测**:2xx→finding(证据前 512 字符 + Q9 掩码,全文密钥永不
   进 item)、非 2xx 无 finding、请求错误吞掉只计数、预算即停、重定向
   链 >2 跳按失败丢弃、未知产品回落 generic 路径;
6. **lane 编排**:无 key 显式空态(credential_missing 不报错,AC6)、
   FOFA 有 key 无 base 显式 base_missing(base 可配不写死,PRD §7)、
   base 形状坏源级降级、查询失败隔离继续下一查询、同 (目标,产品) 只
   探测一次、原始 12 字段命中(banner/header)不进 payload。

测试纪律:exposure.py 经与 adapter._load_module 同款 compile+exec 手法
加载(适配器挂载 exposure 属接线段任务,此处独立自举;兄弟模块
findings/fingerprints 由 exposure._require_sibling 按同名登记复用)。
"""

from __future__ import annotations

import base64
import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugins" / "myia-credhunter"
MODULES_DIR = PLUGIN_DIR / "credhunter"

# 合成脱敏 fixture:人工构造、不对应任何真实账号;主机用保留域/保留网段。
KEY_LITELLM = "sk-7b2e9c4f8d1a6035qrst"
FOFA_BASE = "https://fofa-proxy.example.com"
SHODAN_BASE = "https://api.shodan.io"
DOC_IP = "203.0.113.10"


def load_plugin_module(name: str) -> Any:
    """与 adapter._load_module 同款 compile+exec 加载(同名登记,零 __pycache__)."""
    module_name = f"myia_credhunter_{name}"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    source_file = MODULES_DIR / f"{name}.py"
    module = types.ModuleType(module_name)
    module.__file__ = str(source_file)
    sys.modules[module_name] = module
    exec(compile(source_file.read_text(encoding="utf-8"), str(source_file), "exec"), module.__dict__)
    return module


@pytest.fixture()
def ex() -> Any:
    return load_plugin_module("exposure")


@pytest.fixture()
def fnd() -> Any:
    return load_plugin_module("findings")


class SleepRecorder:
    """sleep 注入口:记录页间停顿参数(测试零真实等待)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client_factory(handler, recorder: list[str] | None = None):
    """client 工厂注入口:MockTransport 拦截(零真实网络)."""

    def factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    def recording_handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(str(request.url))
        return handler(request)

    if recorder is None:
        return factory
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(recording_handler), **kwargs)


def fofa_row(
    host: str = "llm-gw.example.com", ip: str = DOC_IP, port: str = "8080", protocol: str = "http",
    title: str = "LLM Gateway", banner: str = "", product: str = "",
) -> list[str]:
    """FOFA 数组行(12 字段按固定顺序)."""
    return [host, ip, port, protocol, title, "", banner, "nginx", product, "", "", ""]


def fofa_json(rows: list[Any], **extra: Any) -> dict[str, Any]:
    return {"results": rows, "error": None, "size": len(rows), "page": 1, "mode": "extended", **extra}


def shodan_match(**overrides: Any) -> dict[str, Any]:
    match: dict[str, Any] = {
        "hostnames": ["gw.example.com"],
        "ip_str": DOC_IP,
        "port": 8080,
        "data": "HTTP/1.1 200 OK\nserver: nginx",
        "http": {"host": DOC_IP, "html": "<html>gateway</html>", "title": "LLM Gateway", "server": "nginx"},
        "ssl": {"cert": {"subject": {"CN": "gw.example.com"}, "issuer": {"CN": "Example CA"}}},
    }
    match.update(overrides)
    return match


# ---------------------------------------------------------------------------
# 契约一:FOFA 客户端
# ---------------------------------------------------------------------------


class TestFofaClient:
    def test_search_request_shape_and_first_key(self, ex):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured.update({key: request.url.params.get(key) or "" for key in request.url.params})
            return httpx.Response(200, json=fofa_json([fofa_row()]))

        client = ex.FofaClient(key="key-alpha,key-beta", base_url=FOFA_BASE + "/", client_factory=make_client_factory(handler))
        hits, meta = client.search('body="litellm"', page=2, size=50)

        assert captured["url"].startswith(FOFA_BASE + "/api/v1/search/all?")
        assert captured["key"] == "key-alpha", "多 key 逗号分隔时 search 只用第一个"
        assert captured["qbase64"] == base64.b64encode('body="litellm"'.encode("utf-8")).decode("ascii")
        assert captured["fields"] == ",".join(ex.FOFA_FIELDS) and len(ex.FOFA_FIELDS) == 12
        assert captured["page"] == "2" and captured["size"] == "50"
        assert len(hits) == 1 and hits[0].host == "llm-gw.example.com"
        assert meta["mode"] == "extended"

    def test_row_mapping_array_and_object_with_body_alias(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fofa_json([
                fofa_row(banner=""),
                {"host": "obj.example.com", "body": "leaked banner text", "port": 9000},
            ]))

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        hits, _meta = client.search("q")
        assert hits[0].port == "8080" and hits[0].server == "nginx" and hits[0].title == "LLM Gateway"
        assert hits[1].host == "obj.example.com" and hits[1].banner == "leaked banner text", "body 空别名进 banner"
        assert hits[1].port == "9000", "数字字段规整成字符串"
        assert [field for field in hits[0].__dataclass_fields__][:12] == list(ex.FOFA_FIELDS), "FOFA 同形 12 字段"

    def test_object_row_url_fallback_kept(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fofa_json([{"host": "", "url": "https://fallback.example.com"}]))

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        hits, _meta = client.search("q")
        assert hits[0].probe_target_value() == "https://fallback.example.com", "host 缺失退 url"

    def test_missing_key_is_structured_not_configured(self, ex):
        client = ex.FofaClient(key="", base_url=FOFA_BASE, client_factory=make_client_factory(lambda r: httpx.Response(200)))
        with pytest.raises(ex.ExposureClientError, match="not configured"):
            client.search("q")

    def test_non_2xx_error_carries_bounded_preview(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="x" * 500)

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        with pytest.raises(ex.ExposureClientError) as exc_info:
            client.search("q")
        assert str(exc_info.value).startswith("fofa: HTTP 500:")
        assert len(exc_info.value.message) <= len("HTTP 500: ") + ex.ERROR_PREVIEW_CHARS, "预览截到 200 字符"

    def test_non_json_response_raises(self, ex):
        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(lambda r: httpx.Response(200, text="not json")))
        with pytest.raises(ex.ExposureClientError, match="非 JSON"):
            client.search("q")

    def test_error_flag_true_raises_with_errmsg(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": True, "errmsg": "配额不足"})

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        with pytest.raises(ex.ExposureClientError, match="配额不足"):
            client.search("q")

    def test_check_uses_probe_query(self, ex):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update({key: request.url.params.get(key) or "" for key in request.url.params})
            return httpx.Response(200, json=fofa_json([]))

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        assert client.check() is True
        assert base64.b64decode(captured["qbase64"]).decode("utf-8") == 'title="123"'
        assert captured["page"] == "1" and captured["size"] == "1"

    def test_base_must_be_absolute(self, ex):
        with pytest.raises(ex.ExposureClientError, match="http"):
            ex.FofaClient(key="k", base_url="fofa.example.com", client_factory=make_client_factory(lambda r: httpx.Response(200)))


# ---------------------------------------------------------------------------
# 契约二:FOFA 分页与预算
# ---------------------------------------------------------------------------


class TestFofaPagination:
    def test_short_page_stops_early_without_sleep(self, ex):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fofa_json([fofa_row()]))

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler, requests))
        sleeper = SleepRecorder()
        hits, errors = ex.fofa_search_all(client, "q", sleeper=sleeper)
        assert len(hits) == 1 and errors == []
        assert len(requests) == 1, "返回行数 < size 即提前停页"
        assert sleeper.calls == []

    def test_full_page_continues_with_page_delay(self, ex):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page") or 1)
            rows = [fofa_row(host=f"host-{index}.example.com") for index in range(ex.FOFA_PAGE_SIZE)] if page == 1 else [fofa_row()]
            return httpx.Response(200, json=fofa_json(rows))

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler, requests))
        sleeper = SleepRecorder()
        hits, errors = ex.fofa_search_all(client, "q", sleeper=sleeper)
        assert len(hits) == ex.FOFA_PAGE_SIZE + 1 and errors == []
        assert len(requests) == 2
        assert sleeper.calls == [ex.FOFA_PAGE_DELAY], "页间 0.3s"

    def test_page_failure_breaks_query_keeps_prior_pages(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page") or 1)
            if page == 1:
                return httpx.Response(200, json=fofa_json([fofa_row(host=f"h{i}.example.com") for i in range(ex.FOFA_PAGE_SIZE)]))
            return httpx.Response(500, text="upstream boom")

        client = ex.FofaClient(key="k", base_url=FOFA_BASE, client_factory=make_client_factory(handler))
        hits, errors = ex.fofa_search_all(client, "q", sleeper=SleepRecorder())
        assert len(hits) == ex.FOFA_PAGE_SIZE, "已取页保留"
        assert len(errors) == 1 and "HTTP 500" in errors[0], "页失败终止该查询分页"


class TestQueryBudget:
    def test_apply_query_budget_dedupes_and_caps(self, ex):
        queries = ["", " body=a ", "body=a", *[f"body=\"v{index}\"" for index in range(30)]]
        planned = ex.apply_query_budget(queries, budget=ex.FOFA_QUERY_BUDGET)
        assert len(planned) == ex.FOFA_QUERY_BUDGET, "FOFA 每 run 前 24 条"
        assert len(set(planned)) == len(planned), "去重"
        assert planned[0] == "body=a", "保序 + 去空白"

    def test_shodan_budget_is_sixteen(self, ex):
        assert ex.SHODAN_QUERY_BUDGET == 16
        assert len(ex.apply_query_budget([f"q{i}" for i in range(40)], budget=ex.SHODAN_QUERY_BUDGET)) == 16


# ---------------------------------------------------------------------------
# 契约三:Shodan 客户端
# ---------------------------------------------------------------------------


class TestShodanClient:
    def test_search_request_shape(self, ex):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured.update({key: request.url.params.get(key) or "" for key in request.url.params})
            return httpx.Response(200, json={"matches": [shodan_match()], "total": 1})

        client = ex.ShodanClient(key="sh-key", client_factory=make_client_factory(handler))
        hits, total = client.search("http.html:sk-", page=3)
        assert captured["url"].startswith(ex.SHODAN_BASE_URL + "/shodan/host/search?")
        assert captured["key"] == "sh-key" and captured["query"] == "http.html:sk-" and captured["page"] == "3"
        assert total == 1

    def test_match_mapping_host_primary_and_port_append(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"matches": [shodan_match()], "total": 1})

        client = ex.ShodanClient(key="k", client_factory=make_client_factory(handler))
        hits, _total = client.search("q")
        hit = hits[0]
        assert hit.host == "gw.example.com:8080", "port 非 80/443 且 host 无冒号则拼 :port"
        assert hit.protocol == "http" and hit.ip == DOC_IP and hit.port == "8080"
        assert hit.header.startswith("HTTP/1.1 200 OK"), "data→header"
        assert hit.banner == "<html>gateway</html>", "http.html→banner"
        assert hit.cert == "gw.example.com; Example CA", "ssl subject/issuer commonName 拼进 cert"
        assert hit.title == "LLM Gateway" and hit.server == "nginx"

    def test_match_port_443_https_without_append(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"matches": [shodan_match(hostnames=["secure.example.com"], port=443)], "total": 1})

        client = ex.ShodanClient(key="k", client_factory=make_client_factory(handler))
        hits, _ = client.search("q")
        assert hits[0].host == "secure.example.com" and hits[0].protocol == "https"

    def test_match_host_fallback_chain(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"matches": [
                shodan_match(hostnames=[], http={"host": "via-http.example.com"}),
                shodan_match(hostnames=[], http={}, ip_str="192.0.2.20"),
            ], "total": 2})

        client = ex.ShodanClient(key="k", client_factory=make_client_factory(handler))
        hits, _ = client.search("q")
        assert hits[0].host == "via-http.example.com:8080", "hostnames 空 → http.host"
        assert hits[1].host == "192.0.2.20:8080", "http.host 也空 → ip_str"

    def test_count_reads_total(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/shodan/host/count"
            assert "page" not in request.url.params, "count 只带 key/query"
            return httpx.Response(200, json={"total": 123})

        client = ex.ShodanClient(key="k", client_factory=make_client_factory(handler))
        assert client.count("http.html:sk-") == 123

    def test_count_invalid_total_raises(self, ex):
        client = ex.ShodanClient(key="k", client_factory=make_client_factory(lambda r: httpx.Response(200, json={"total": "many"})))
        with pytest.raises(ex.ExposureClientError, match="total"):
            client.count("q")

    def test_non_2xx_error(self, ex):
        client = ex.ShodanClient(key="k", client_factory=make_client_factory(lambda r: httpx.Response(403, text="denied")))
        with pytest.raises(ex.ExposureClientError, match="HTTP 403"):
            client.search("q")


class TestFofaToShodan:
    def test_body_translation(self, ex):
        assert ex.fofa_to_shodan('body="litellm"') == 'http.html:"litellm"'

    def test_and_or_operators(self, ex):
        assert ex.fofa_to_shodan('body="A" && body="B"') == 'http.html:"A" http.html:"B"'
        assert ex.fofa_to_shodan('body="A" || body="B"') == 'http.html:"A" OR http.html:"B"'
        assert ex.fofa_to_shodan('body="A" && body="B" || body="C"') == 'http.html:"A" http.html:"B" OR http.html:"C"'

    def test_non_body_fragments_untouched(self, ex):
        assert ex.fofa_to_shodan('header="authorization: bearer sk-"') == 'header="authorization: bearer sk-"'
        assert ex.fofa_to_shodan("") == ""


class TestShodanPagination:
    def test_early_stop_and_page_delay(self, ex):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page") or 1)
            matches = [shodan_match(ip_str=f"203.0.113.{index}") for index in range(ex.SHODAN_PAGE_SIZE)] if page == 1 else []
            return httpx.Response(200, json={"matches": matches, "total": 100})

        client = ex.ShodanClient(key="k", client_factory=make_client_factory(handler, requests))
        sleeper = SleepRecorder()
        hits, errors = ex.shodan_search_all(client, "q", sleeper=sleeper)
        assert len(hits) == ex.SHODAN_PAGE_SIZE and errors == []
        assert len(requests) == 2, "本页 matches < 100 提前停"
        assert sleeper.calls == [ex.SHODAN_PAGE_DELAY], "页间 1.0s"


# ---------------------------------------------------------------------------
# 契约四:L0 门控 + 产品路径集 + scheme 归一
# ---------------------------------------------------------------------------


class TestL0Policy:
    @pytest.mark.parametrize(
        ("vuln_class", "risk", "intrusive"),
        [
            ("weak_password", 1, False),
            ("idor", 0, False),
            ("sqli", 2, False),
            ("unauth_read", 1, False),
            ("unauth_read", 0, True),
        ],
    )
    def test_non_l0_refused_fail_closed(self, ex, vuln_class, risk, intrusive):
        with pytest.raises(ex.ExposurePolicyError):
            ex.ensure_l0_policy(vuln_class, risk, intrusive=intrusive)

    def test_l0_defaults_pass(self, ex):
        ex.ensure_l0_policy("unauth_read", 0, intrusive=False)

    def test_probe_target_gate_keeps_refusal(self, ex):
        with pytest.raises(ex.ExposurePolicyError):
            ex.probe_target("llm-gw.example.com", "litellm", vuln_class="weak_password", risk=1)
        with pytest.raises(ex.ExposurePolicyError):
            ex.probe_target("llm-gw.example.com", "litellm", risk=3)


class TestProductPaths:
    def test_macro_path_sets_match_spec(self, ex):
        assert set(ex.PRODUCT_PATHS) == {
            "dify", "litellm", "openwebui", "flowise", "langflow", "newapi", "generic",
            "anythingllm", "chatgpt_next_web", "librechat", "lobechat", "fastgpt", "openrouter", "portkey",
        }
        assert ex.PRODUCT_PATHS["dify"] == ("/console/api/system-features", "/v1/info")
        assert ex.PRODUCT_PATHS["litellm"] == ("/health/readiness", "/v1/models")
        assert ex.PRODUCT_PATHS["fastgpt"] == ("/api/system/getInitData",)
        assert ex.PRODUCT_PATHS["generic"] == ("/v1/models", "/api/status")

    def test_flowise_stronger_paths_stay_out_of_default_set(self, ex):
        for path in ex.SPEC_UNAUTH_EXTRA_PATHS["flowise"]:
            assert path not in ex.PRODUCT_PATHS["flowise"], "spec 版更强证据路径刻意不进 L0 默认集"

    def test_resolve_product_channels(self, ex):
        assert ex.resolve_product(ex.ExposureHit(), {"body=\"litellm\"": "litellm"}, query='body="litellm"') == "litellm"
        assert ex.resolve_product(ex.ExposureHit(product_hint="newapi"), None) == "newapi"
        assert ex.resolve_product(ex.ExposureHit(product="nginx"), None) == "generic", "行内 product 非已知产品回落 generic"
        assert ex.resolve_product(ex.ExposureHit(product="dify"), None) == "dify", "行内 product 恰为已知产品时采用"
        assert ex.resolve_product(ex.ExposureHit(), None) == "generic"


class TestSchemeNormalization:
    @pytest.mark.parametrize(
        ("target", "normalized"),
        [
            ("llm.example.com", "http://llm.example.com"),
            ("llm.example.com:443", "https://llm.example.com:443"),
            ("https://llm.example.com/", "https://llm.example.com"),
            ("  llm.example.com  ", "http://llm.example.com"),
            ("", ""),
        ],
    )
    def test_scheme_rules(self, ex, target, normalized):
        assert ex.normalize_scheme(target) == normalized


# ---------------------------------------------------------------------------
# 契约五:L0 被动探测
# ---------------------------------------------------------------------------


class TestProbeTarget:
    def test_2xx_produces_masked_finding(self, ex, fnd):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path in ("/health/readiness", "/v1/models")
            if request.url.path == "/health/readiness":
                return httpx.Response(404)
            return httpx.Response(200, text=f'{{"data": [{{"id": "demo-model"}}], "leaked": "{KEY_LITELLM}"}}')

        report = ex.probe_target(
            "https://llm-gw.example.com", "litellm",
            source_engine="fofa", query='body="litellm"', ip=DOC_IP, port="443", protocol="https",
            client_factory=make_client_factory(handler),
        )
        assert report["requests"] == 2 and report["errors"] == 0
        assert len(report["findings"]) == 1
        item = report["findings"][0]
        assert item["url"] == "https://llm-gw.example.com/v1/models"
        assert item["source"] == "credhunter" and item["source_type"] == "exposure_probe"
        assert item["vuln_class"] == "unauth_read" and item["risk"] == 0
        assert item["product"] == "litellm" and item["evidence_path"] == "/v1/models"
        assert item["engine"] == "fofa" and item["query"] == 'body="litellm"'
        assert item["ip"] == DOC_IP and item["port"] == "443" and item["protocol"] == "https"
        assert KEY_LITELLM not in json.dumps(item), "Q9 红线:全文密钥永不进 item"
        assert fnd.mask_apikey(KEY_LITELLM) in item["content"], "证据里密钥为掩码形态"

    def test_evidence_capped_to_512_chars(self, ex):
        body = "x" * 1000 + KEY_LITELLM
        report = ex.probe_target("llm.example.com", "generic", client_factory=make_client_factory(lambda r: httpx.Response(200, text=body)))
        assert len(report["findings"][0]["content"]) == 512, "证据 = 正文前 512 字符"

    @pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
    def test_non_2xx_no_finding(self, ex, status):
        report = ex.probe_target("llm.example.com", "generic", client_factory=make_client_factory(lambda r: httpx.Response(status)))
        assert report["findings"] == [] and report["requests"] >= 1

    def test_request_error_swallowed_and_counted(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        report = ex.probe_target("llm.example.com", "generic", client_factory=make_client_factory(handler))
        assert report["findings"] == [] and report["errors"] == 2 and report["requests"] == 0, "请求错误吞掉只计数"

    def test_budget_stops_after_first_finding(self, ex):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        report = ex.probe_target(
            "llm.example.com", "newapi", request_budget=1,
            client_factory=make_client_factory(handler, requests),
        )
        assert len(report["findings"]) == 1, "累计 findings ≥ 预算即停"
        assert requests == ["http://llm.example.com/api/status"], "第二个路径不再请求"

    def test_redirect_chain_over_limit_discarded(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/status":
                return httpx.Response(307, headers={"Location": "http://llm.example.com/r1"})
            if path == "/r1":
                return httpx.Response(307, headers={"Location": "http://llm.example.com/r2"})
            if path == "/r2":
                return httpx.Response(307, headers={"Location": "http://llm.example.com/r3"})
            return httpx.Response(404)

        report = ex.probe_target("llm.example.com", "newapi", client_factory=make_client_factory(handler))
        assert report["findings"] == [] and report["requests"] == 2, "3 跳重定向链按失败丢弃"

    def test_redirect_within_limit_still_finding(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/status":
                return httpx.Response(302, headers={"Location": "http://llm.example.com/landed"})
            return httpx.Response(200, text="ok")

        report = ex.probe_target("llm.example.com", "newapi", client_factory=make_client_factory(handler))
        assert len(report["findings"]) == 2, "≤2 跳的重定向正常落 finding"

    def test_empty_target_short_circuits(self, ex):
        report = ex.probe_target("", "generic", client_factory=make_client_factory(lambda r: httpx.Response(200)))
        assert report["findings"] == [] and report["requests"] == 0

    def test_unknown_product_uses_generic_paths(self, ex):
        requests: list[str] = []
        report = ex.probe_target("llm.example.com", "mystery-product", client_factory=make_client_factory(lambda r: httpx.Response(404), requests))
        assert requests == [f"http://llm.example.com{path}" for path in ex.PRODUCT_PATHS["generic"]]


# ---------------------------------------------------------------------------
# 契约六:lane 编排(源级结构化降级,不报错)
# ---------------------------------------------------------------------------


def lane_handler(
    fofa_pages: dict[str, Any] | None = None,
    shodan_pages: dict[str, Any] | None = None,
    probe_responses: dict[str, tuple[int, str]] | None = None,
):
    """run_exposure 测试总 handler:按路径分派 FOFA/Shodan/探测三类请求.

    探测路径缺省 404(无 finding);``probe_responses`` 按 path 给 (状态, 正文)。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/search/all":
            query = base64.b64decode(request.url.params.get("qbase64") or "").decode("utf-8")
            page = fofa_pages or {}
            if query in page:
                return httpx.Response(200, json=fofa_json(page[query]))
            return httpx.Response(200, json=fofa_json([]))
        if path == "/shodan/host/search":
            matches = (shodan_pages or {}).get(str(request.url.params.get("query")), [])
            return httpx.Response(200, json={"matches": matches, "total": len(matches)})
        status, body = (probe_responses or {}).get(path, (404, ""))
        return httpx.Response(status, text=body)
    return handler


class TestRunExposure:
    def test_no_keys_is_explicit_empty_state(self, ex):
        payload = ex.run_exposure(fofa_key=None, shodan_key=None)
        assert payload["status"] == "empty" and payload["findings"] == []
        assert payload["lanes"]["fofa"]["status"] == "credential_missing"
        assert payload["lanes"]["shodan"]["status"] == "credential_missing"
        assert payload["counts"]["findings"] == 0

    def test_fofa_key_without_base_is_base_missing(self, ex):
        payload = ex.run_exposure(fofa_key="k", fofa_base=None, fofa_queries=['body="sk-"'])
        assert payload["lanes"]["fofa"]["status"] == "base_missing", "base 可配占位不写死(PRD §7)"

    def test_malformed_base_degrades_at_source_level(self, ex):
        payload = ex.run_exposure(fofa_key="k", fofa_base="not-a-url.example.com", fofa_queries=['body="sk-"'])
        assert payload["lanes"]["fofa"]["status"] == "degraded"
        assert "http" in payload["lanes"]["fofa"]["source_error"]

    def test_full_fofa_flow_masked_findings(self, ex, fnd):
        requests: list[str] = []
        handler = lane_handler(
            fofa_pages={'body="litellm"': [fofa_row()]},
            probe_responses={"/v1/models": (200, f"gateway config leaked {KEY_LITELLM} inline")},
        )
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE,
            fofa_queries=['body="litellm"'],
            product_hints={'body="litellm"': "litellm"},
            client_factory=make_client_factory(handler, requests),
            sleeper=SleepRecorder(),
        )
        assert payload["status"] == "success"
        assert payload["lanes"]["fofa"]["status"] == "ok" and payload["lanes"]["fofa"]["hits"] == 1
        assert len(payload["findings"]) == 1
        item = payload["findings"][0]
        assert item["product"] == "litellm" and item["engine"] == "fofa" and item["host"] == "llm-gw.example.com"
        assert any(url == "http://llm-gw.example.com/v1/models" for url in requests), "探测 URL = 归一目标 + 产品路径"
        assert fnd.find_full_key_leak(payload, KEY_LITELLM) == [], "Q9 红线:全文密钥不进 payload 任何角落"
        assert json.loads(json.dumps(payload))["status"] == "success", "payload JSON 可序列化"
        assert "banner" not in json.dumps(payload), "原始 12 字段命中(banner/header)不进 payload"
        assert payload["counts"] == {"fofa_hits": 1, "shodan_hits": 0, "probe_targets": 1, "probe_requests": 2, "probe_errors": 0, "findings": 1}

    def test_query_budget_caps_run_queries(self, ex):
        requests: list[str] = []
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE,
            fofa_queries=[f'body="v{index}"' for index in range(30)],
            client_factory=make_client_factory(lane_handler(), requests),
            sleeper=SleepRecorder(),
        )
        assert payload["lanes"]["fofa"]["queries_planned"] == 24, "FOFA 每 run 24 条查询预算"
        assert len([url for url in requests if "/api/v1/search/all" in url]) == 24

    def test_query_failure_isolated_to_its_lane_entry(self, ex):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/search/all":
                query = base64.b64decode(request.url.params.get("qbase64") or "").decode("utf-8")
                if query == 'body="bad"':
                    return httpx.Response(500, text="boom")
                return httpx.Response(200, json=fofa_json([]))
            return httpx.Response(404)

        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE,
            fofa_queries=['body="bad"', 'body="good"'],
            client_factory=make_client_factory(handler), sleeper=SleepRecorder(),
        )
        lane = payload["lanes"]["fofa"]
        assert lane["status"] == "degraded" and lane["errors"] == 1
        assert lane["per_query"][0]["errors"] and "HTTP 500" in lane["per_query"][0]["errors"][0]
        assert lane["per_query"][1]["hits"] == 0, "失败查询不拖垮下一查询"

    def test_zero_hits_is_empty_not_failure(self, ex):
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE, fofa_queries=['body="sk-"'],
            client_factory=make_client_factory(lane_handler()), sleeper=SleepRecorder(),
        )
        assert payload["lanes"]["fofa"]["status"] == "empty", "单源 0 命中 ≠ 失败"
        assert payload["status"] == "empty"

    def test_same_target_probed_once_across_queries(self, ex):
        requests: list[str] = []
        handler = lane_handler(fofa_pages={'body="a"': [fofa_row()], 'body="b"': [fofa_row()]})
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE, fofa_queries=['body="a"', 'body="b"'],
            client_factory=make_client_factory(handler, requests), sleeper=SleepRecorder(),
        )
        assert payload["counts"]["probe_targets"] == 1, "同 (目标, 产品) 只探测一次"
        probe_urls = [url for url in requests if "/api/v1/search/all" not in url]
        assert sorted(probe_urls) == ["http://llm-gw.example.com/api/status", "http://llm-gw.example.com/v1/models"]

    def test_probe_disabled_keeps_hits_counted(self, ex):
        requests: list[str] = []
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE, fofa_queries=['body="litellm"'], probe=False,
            client_factory=make_client_factory(lane_handler(fofa_pages={'body="litellm"': [fofa_row()]}), requests),
            sleeper=SleepRecorder(),
        )
        assert payload["counts"]["fofa_hits"] == 1 and payload["counts"]["probe_targets"] == 0
        assert payload["findings"] == [] and payload["status"] == "empty"
        assert all("/api/v1/search/all" in url for url in requests), "关探测后零出网探测请求"

    def test_shodan_lane_scheme_normalized_before_probe(self, ex):
        requests: list[str] = []
        handler = lane_handler(
            shodan_pages={"http.html:litellm": [shodan_match(hostnames=["llm2.example.com"], port=8443)]},
            probe_responses={"/v1/models": (200, "ok")},
        )
        payload = ex.run_exposure(
            shodan_key="sk-key", shodan_queries=["http.html:litellm"],
            product_hints={"http.html:litellm": "litellm"},
            client_factory=make_client_factory(handler, requests), sleeper=SleepRecorder(),
        )
        assert payload["lanes"]["shodan"]["status"] == "ok" and payload["lanes"]["shodan"]["hits"] == 1
        assert payload["status"] == "success" and len(payload["findings"]) == 1
        probe_urls = [url for url in requests if "/shodan/host/search" not in url]
        assert probe_urls and all(url.startswith("http://llm2.example.com:8443/") for url in probe_urls), (
            "Shodan 无 scheme host 探测前先补 scheme(上游坑修正)"
        )
        item = payload["findings"][0]
        assert item["engine"] == "shodan" and item["host"] == "llm2.example.com:8443"

    def test_shodan_budget_sixteen(self, ex):
        requests: list[str] = []
        payload = ex.run_exposure(
            shodan_key="k", shodan_queries=[f"http.html:v{index}" for index in range(20)],
            client_factory=make_client_factory(lane_handler(), requests), sleeper=SleepRecorder(),
        )
        assert payload["lanes"]["shodan"]["queries_planned"] == 16
        assert len([url for url in requests if "/shodan/host/search" in url]) == 16

    def test_hostless_hit_skipped_in_probe(self, ex):
        handler = lane_handler(fofa_pages={'body="x"': [fofa_row(host="")]})
        payload = ex.run_exposure(
            fofa_key="k", fofa_base=FOFA_BASE, fofa_queries=['body="x"'],
            client_factory=make_client_factory(handler), sleeper=SleepRecorder(),
        )
        assert payload["counts"]["fofa_hits"] == 1 and payload["counts"]["probe_targets"] == 0
