"""myia-credhunter 插件 R2(credcheck:验证 + 余额探测)契约测试.

任务 10-03-aipocket-fusion R2 配套测试:只读插件目录、**零网络**
(transport 全部注入 FakeTransport,路由命中即返回 canned 响应)、零真实
凭据(全部为人工合成脱敏键,不对应任何真实账号)。钉住的契约:

1. **安全闸 + 三态归类**(credcheck.md §2/§3):header ASCII/无 CRLF 闸、
   final_verified / rejected(401/403 unauthorized、2xx 无模型
   invalid-response-schema)/ transient(429/5xx/传输层失败);
2. **resolve 链 + 前缀改道**:域名 → 前缀 → unknown 经真实 specs registry
   走通;openai/anthropic/gemini/openrouter 官方族前缀强制改道官方 URL;
3. **专用端点矩阵**(§2.3):anthropic/gemini/azure_openai/openai/qoder/
   cursor/openrouter(两段式)/aws_bedrock 的 URL 与 auth 头形状;
4. **余额矩阵 13 家逐家对齐**(§5):每供应商 happy path + 401 判死;仅存
   活性家族("N/A")、glm/longcat 被动标记、无探测家族诚实 unknown;
5. **Q7/Q8/Q9 纪律**:余额默认关显式开;串行 + 每供应商 2s 间隔(Pacer);
   输出掩码-only(snippet/detail 全文密钥永不出现,经
   adapter.findings.find_full_key_leak 复核)。

测试纪律:credcheck 子模块经 adapter._load_module(compile+exec,与宿主
同款加载器)加载;registry 用 adapter.specs 的真实数据文件(25 规格)。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from myia.cli import _import_plugin_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "myia-credhunter"

# ---------------------------------------------------------------------------
# 合成脱敏 fixture 键(人工构造,不对应任何真实账号;形态只服务于归因分支)
# ---------------------------------------------------------------------------
KEY_OPENAI = "sk-proj-synth0q7wXc5vNk2mJb4tYd"
KEY_OPENAI_ADMIN = "sk-admin-synth0q7wXc5vNk2mJb4t"
KEY_OPENAI_ORDINARY = "sk-synthordinary0q7wXc5vNk2m"
KEY_ANTHROPIC = "sk-ant-api03-synthQw7tXk2nRf"
KEY_ANTHROPIC_ADMIN = "sk-ant-admin-synthQw7tXk2nR"
KEY_ANTHROPIC_OAT = "sk-ant-oat01-synthQw7tXk2nRf"
KEY_GEMINI = "AIzaSy-synthQw7tXk2nRf9mBc4"
KEY_OPENROUTER = "sk-or-v1-7c3f9a1e5d2b4086bbccddeeff00112233445566"
KEY_CURSOR = "crsr_Ax7vNm3kQp9wZt5bHc2jLr8sYd4fGu61e"
KEY_QODER = "pt-synthQw7tXk2nRf9mBc4vZx1"
KEY_BEDROCK = "ABSKsynthQw7tXk2nRf9mBc4vZx2="
KEY_GENERIC = "opaque-synth-key-0q7wXc5vNk2m"

_ADAPTER: Any = None
_CREDCHECK: Any = None


def _adapter() -> Any:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = _import_plugin_adapter(PLUGINS_DIR, "myia-credhunter")
    return _ADAPTER


def _credcheck() -> Any:
    """credcheck 子模块(经 adapter 的 compile+exec 加载器,与宿主同款)。"""
    global _CREDCHECK
    if _CREDCHECK is None:
        _CREDCHECK = _adapter()._load_module("credcheck")
    return _CREDCHECK


@pytest.fixture()
def cc() -> Any:
    return _credcheck()


@pytest.fixture()
def registry() -> Any:
    return _adapter().specs.ProviderRegistry(_adapter().specs.load_specs())


# ---------------------------------------------------------------------------
# 测试替身:FakeTransport(路由命中即返回,零网络)+ FakePacer(零等待)
# ---------------------------------------------------------------------------


class FakeTransport:
    """按 (method, url) 精确路由的假传输:命中即消费一条,未命中 404。

    calls 记录 (method, url, headers, json_body) 供端点形状断言。
    """

    def __init__(self, routes: list[tuple] | None = None) -> None:
        self.routes: list[tuple] = list(routes or [])
        self.calls: list[tuple] = []

    def request(self, method: str, url: str, *, headers: Any = None, json_body: Any = None) -> Any:
        self.calls.append((method, url, dict(headers or {}), json_body))
        for index, (want_method, want_url, status, body, response_headers) in enumerate(self.routes):
            if want_method == method and want_url == url:
                self.routes.pop(index)
                return _credcheck().HttpResponse(
                    status_code=status, body=body, headers=response_headers or {}
                )
        return _credcheck().HttpResponse(status_code=404, body='{"error":"route_miss"}', headers={})


class FakePacer:
    """零等待限速替身:只记录每次请求前的 wait(组名)。"""

    def __init__(self) -> None:
        self.waits: list[str] = []

    def wait(self, group: str) -> None:
        self.waits.append(group)


def check(
    registry: Any,
    transport: FakeTransport,
    *,
    apikey: str,
    apiurl: str = "",
    probe_balance: bool = False,
    pacer: Any = None,
) -> Any:
    return _credcheck().check_credential(
        apikey=apikey,
        apiurl=apiurl,
        registry=registry,
        transport=transport,
        pacer=pacer or FakePacer(),
        probe_balance=probe_balance,
    )


def assert_balance(result: Any, **expected: Any) -> None:
    """断言 result.balance 的子集字段(balance 为 None 直接失败)。"""
    balance = result.balance
    assert balance is not None, f"balance 应已探测:{result!r}"
    for key, value in expected.items():
        actual = getattr(balance, key)
        assert actual == value, f"balance.{key}:期望 {value!r},实际 {actual!r}"


# ---------------------------------------------------------------------------
# 契约一:安全闸 + 纯函数助手(§3/§4)
# ---------------------------------------------------------------------------


class TestSafetyGateAndHelpers:
    def test_ascii_without_crlf_passes(self, cc):
        assert cc.is_safe_header_value(KEY_GENERIC) is True
        assert cc.is_safe_header_value("") is True

    @pytest.mark.parametrize("key", ["opaque-synth-kéy-0q7wXc5v", "opaque-synth-key\r\n0q7wXc", "bad\nkey-synth-0q7w"])
    def test_non_ascii_or_crlf_rejected(self, cc, key):
        assert cc.is_safe_header_value(key) is False

    def test_gate_blocks_request_entirely(self, cc, registry):
        transport = FakeTransport()
        result = check(registry, transport, apikey="opaque-synth-kéy-0q7wXc5v", apiurl="https://api.deepseek.com")
        assert transport.calls == [], "安全闸失败必须零出网"
        assert result.validation_state == "transient"
        assert result.key_state == "unavailable"
        assert result.error == "unsafe-key"

    def test_gate_blocks_probe_models_too(self, cc, registry):
        transport = FakeTransport()
        probe = cc.probe_models(apikey="opaque\r\nsynth", apiurl="https://api.deepseek.com",
                                registry=registry, transport=transport, pacer=FakePacer())
        assert transport.calls == []
        assert (probe.key_state, probe.error) == ("unavailable", "unsafe-key")

    def test_format_amount_strips_trailing_zeros(self, cc):
        assert cc.format_amount("110.50") == "110.5"
        assert cc.format_amount(0) == "0"
        assert cc.format_amount(3.14159265) == "3.1416"
        assert cc.format_amount("not-a-number") == ""

    def test_is_definitive_auth_rejection(self, cc):
        assert cc.is_definitive_auth_rejection(401) is True
        assert cc.is_definitive_auth_rejection(403) is True
        assert cc.is_definitive_auth_rejection(429) is False
        assert cc.is_definitive_auth_rejection(200) is False

    def test_extract_models_three_shapes(self, cc):
        assert cc.extract_models({"data": [{"id": "m-a"}, {"id": "m-b"}]}) == ("m-a", "m-b")
        assert cc.extract_models({"models": [{"name": "gemini-x"}]}) == ("gemini-x",)
        assert cc.extract_models({"modelSummaries": [{"modelId": "bedrock-y"}]}) == ("bedrock-y",)
        assert cc.extract_models({"data": "corrupt"}) == ()
        assert cc.extract_models(None) == ()

    @pytest.mark.parametrize(
        ("apikey", "provider", "kind"),
        [
            (KEY_OPENAI_ADMIN, "openai", "admin"),
            (KEY_OPENAI, "openai", "standard"),
            (KEY_ANTHROPIC_ADMIN, "anthropic", "admin"),
            (KEY_ANTHROPIC_OAT, "anthropic", "oauth"),
            ("sk-ant-sid01-synthQw7tXk", "anthropic", "oauth"),
            (KEY_ANTHROPIC, "anthropic", "standard"),
            ("", "openai", "unknown"),
            (KEY_GENERIC, "deepseek", "standard"),
        ],
    )
    def test_credential_kind(self, cc, apikey, provider, kind):
        assert cc.credential_kind(apikey, provider) == kind


# ---------------------------------------------------------------------------
# 契约二:三态归类(§2.6;deepseek 走通用 OpenAiCompatible 路径)
# ---------------------------------------------------------------------------


def _deepseek_routes(status: int, body: str, headers: dict | None = None) -> list[tuple]:
    return [("GET", "https://api.deepseek.com/v1/models", status, body, headers or {})]


class TestThreeStateClassification:
    def test_2xx_with_models_final_verified(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(200, '{"data": [{"id": "deepseek-chat"}]}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "final_verified"
        assert result.error == ""
        assert result.key_state == "active"
        assert result.models == ("deepseek-chat",)
        assert result.status_code == 200
        assert result.provider == "deepseek"
        assert result.resolve_reason == "domain"

    @pytest.mark.parametrize("status", [401, 403])
    def test_401_403_rejected_unauthorized(self, cc, registry, status):
        transport = FakeTransport(_deepseek_routes(status, '{"error":"auth"}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "rejected"
        assert result.error == "unauthorized"
        assert result.key_state == "expired"

    def test_2xx_without_models_rejected_invalid_schema(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(200, '{"data": []}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "rejected"
        assert result.error == "invalid-response-schema"
        assert result.key_state == "invalid_response"

    def test_429_transient_rate_limited(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(429, '{"error":"slow down"}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "transient"
        assert result.key_state == "rate_limited"

    @pytest.mark.parametrize("status", [500, 503])
    def test_5xx_transient_read_failed(self, cc, registry, status):
        transport = FakeTransport(_deepseek_routes(status, '{"error":"boom"}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "transient"
        assert result.error == "read-failed"
        assert result.key_state == "unavailable"

    def test_transport_level_failure_is_transient(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(0, ""))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.validation_state == "transient"
        assert result.status_code == 0

    def test_no_api_url_rejected_without_requests(self, cc, registry):
        transport = FakeTransport()
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="")
        assert transport.calls == []
        assert result.validation_state == "rejected"
        assert result.error == "no API URL"
        assert result.provider == "unknown"

    def test_snippet_redacted_and_capped(self, cc, registry):
        body = '{"echo": "' + KEY_GENERIC + '", "pad": "' + "x" * 700 + '"}'
        transport = FakeTransport(_deepseek_routes(200, body))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert KEY_GENERIC not in result.response_snippet, "snippet 必须先掩码"
        assert cc.mask_apikey(KEY_GENERIC) in result.response_snippet
        assert len(result.response_snippet) <= cc.RESPONSE_SNIPPET_CHARS

    def test_scope_and_tier_evidence_from_body(self, cc, registry):
        body = '{"data": [{"id": "m"}], "organization_id": "org-synth-9", "tier": "tier-4-synth"}'
        transport = FakeTransport(_deepseek_routes(200, body))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.scope == "org-synth-9"
        assert result.tier_evidence == "tier-4-synth"


# ---------------------------------------------------------------------------
# 契约三:resolve 链 + 前缀改道(§2.1-§2.3)
# ---------------------------------------------------------------------------


class TestResolveChainAndReroute:
    def test_domain_resolves_before_prefix(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(200, '{"data":[{"id":"m"}]}'))
        result = check(registry, transport, apikey=KEY_OPENROUTER, apiurl="https://api.deepseek.com")
        assert result.provider == "deepseek", "域名优先于密钥前缀"
        assert result.resolve_reason == "domain"

    def test_prefix_fallback_uses_official_url(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://openrouter.ai/api/v1/auth/key", 200, '{"data":{"label":"k"}}', {}),
            ("GET", "https://openrouter.ai/api/v1/models", 200, '{"data":[{"id":"openai/gpt-4o-mini"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENROUTER, apiurl="")
        assert result.provider == "openrouter"
        assert result.resolve_reason == "key_prefix"
        assert result.apiurl == "https://openrouter.ai/api", "无传入 URL 回落官方基址"

    def test_bedrock_host_special_case_and_s3_guard(self, cc, registry):
        bedrock = FakeTransport([
            ("GET", "https://bedrock-runtime.us-east-1.amazonaws.com/foundation-models", 200,
             '{"modelSummaries":[{"modelId":"anthropic.claude-3-5-sonnet"}]}', {}),
        ])
        result = check(registry, bedrock, apikey=KEY_BEDROCK, apiurl="https://bedrock-runtime.us-east-1.amazonaws.com")
        assert result.provider == "aws_bedrock"
        assert result.validation_state == "final_verified"
        assert result.models == ("anthropic.claude-3-5-sonnet",)

        s3 = FakeTransport()
        s3_result = check(registry, s3, apikey=KEY_GENERIC, apiurl="https://bucket.s3.us-east-1.amazonaws.com")
        assert s3_result.provider == "unknown", "非 bedrock 的 amazonaws.com host 不误归因"
        # unknown 但带传入 URL:按通用兼容路径验证该网关地址,而非 no API URL
        assert s3.calls[0][1] == "https://bucket.s3.us-east-1.amazonaws.com/v1/models"
        assert s3_result.validation_state == "transient"

    def test_openrouter_prefix_reroutes_away_from_gateway_url(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://openrouter.ai/api/v1/auth/key", 200, '{"data":{"label":"k"}}', {}),
            ("GET", "https://openrouter.ai/api/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENROUTER, apiurl="https://gateway-synth.example.com/api")
        assert result.apiurl == "https://openrouter.ai/api", "官方族前缀强制改道官方 URL"
        assert all("gateway-synth" not in url for _m, url, _h, _b in transport.calls)

    def test_anthropic_prefix_reroutes_to_official(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3-5-haiku"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC, apiurl="https://proxy-synth.example.com")
        assert result.provider == "anthropic"
        assert result.apiurl == "https://api.anthropic.com"
        assert result.validation_state == "final_verified"


# ---------------------------------------------------------------------------
# 契约四:专用验证端点矩阵(§2.3-§2.5)
# ---------------------------------------------------------------------------


class TestSpecializedEndpoints:
    def test_openai_models_url_and_bearer(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.openai.com/v1/models", 200, '{"data":[{"id":"gpt-4o-mini"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1")
        assert result.validation_state == "final_verified"
        method, url, headers, _body = transport.calls[0]
        assert url == "https://api.openai.com/v1/models", "base 已以 /v1 结尾不重复拼接"
        assert headers["Authorization"] == f"Bearer {KEY_OPENAI}"

    def test_anthropic_headers(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC, apiurl="https://api.anthropic.com")
        assert result.validation_state == "final_verified"
        _m, _u, headers, _b = transport.calls[0]
        assert headers["x-api-key"] == KEY_ANTHROPIC
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers

    def test_gemini_key_in_query_not_header(self, cc, registry):
        transport = FakeTransport([
            ("GET", f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY_GEMINI}", 200,
             '{"models":[{"name":"models/gemini-2.0-flash"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GEMINI,
                       apiurl="https://generativelanguage.googleapis.com")
        assert result.validation_state == "final_verified"
        assert result.models == ("models/gemini-2.0-flash",)
        _m, _u, headers, _b = transport.calls[0]
        assert "Authorization" not in headers and "x-api-key" not in headers

    def test_azure_openai_api_version_and_header(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://synth-res.openai.azure.com/openai/models?api-version=2024-10-21", 200,
             '{"data":[{"id":"gpt-4o-mini"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://synth-res.openai.azure.com")
        assert result.provider == "azure_openai"
        assert result.validation_state == "final_verified"
        _m, _u, headers, _b = transport.calls[0]
        assert headers["api-key"] == KEY_GENERIC

    def test_qoder_cloud_models_endpoint(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.qoder.com/api/v1/cloud/models", 200, '{"models":[{"name":"claude-sonnet-4-5"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_QODER, apiurl="https://api.qoder.com")
        assert result.provider == "qoder"
        assert result.models == ("claude-sonnet-4-5",)

    def test_cursor_identity_evidence_gate(self, cc, registry):
        happy = FakeTransport([
            ("GET", "https://api.cursor.com/v1/me", 200, '{"apiKeyName":"k-1","userEmail":"u@synth.example"}', {}),
        ])
        result = check(registry, happy, apikey=KEY_CURSOR, apiurl="https://api.cursor.com")
        assert result.provider == "cursor"
        assert result.validation_state == "final_verified"
        assert result.models == (), "cursor 证据是身份字段而非模型列表"

        bare = FakeTransport([("GET", "https://api.cursor.com/v1/me", 200, '{"name":"n"}', {})])
        bare_result = check(registry, bare, apikey=KEY_CURSOR, apiurl="https://api.cursor.com")
        assert bare_result.validation_state == "rejected"
        assert bare_result.error == "invalid-response-schema"

    def test_openrouter_two_phase_auth_key_gate(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://openrouter.ai/api/v1/auth/key", 401, '{"error":"bad key"}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENROUTER, apiurl="")
        assert result.validation_state == "rejected"
        assert result.error == "unauthorized"
        assert len(transport.calls) == 1, "auth/key 失败即终,不再打 models"

    def test_bedrock_foundation_models_bearer(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://bedrock-runtime.us-east-1.amazonaws.com/foundation-models", 200,
             '{"modelSummaries":[{"modelId":"bm-m"}]}', {}),
        ])
        check(registry, transport, apikey=KEY_BEDROCK, apiurl="https://bedrock-runtime.us-east-1.amazonaws.com")
        _m, _u, headers, _b = transport.calls[0]
        assert headers["Authorization"] == f"Bearer {KEY_BEDROCK}"

    def test_versioned_base_join_for_glm_and_groq(self, cc, registry):
        glm = FakeTransport([
            ("GET", "https://open.bigmodel.cn/api/paas/v4/models", 200, '{"data":[{"id":"glm-4-flash"}]}', {}),
        ])
        result = check(registry, glm, apikey=KEY_GENERIC, apiurl="https://open.bigmodel.cn/api/paas/v4")
        assert result.provider == "glm"
        assert glm.calls[0][1].endswith("/api/paas/v4/models"), "版本尾段(/v4)不重复拼 /v1"

        groq = FakeTransport([
            ("GET", "https://api.groq.com/openai/v1/models", 200, '{"data":[{"id":"llama-3.1-8b-instant"}]}', {}),
        ])
        groq_result = check(registry, groq, apikey="gsk_synthQw7tXk2nRf9mBc4v", apiurl="https://api.groq.com/openai/v1")
        assert groq_result.provider == "groq"
        assert groq.calls[0][1] == "https://api.groq.com/openai/v1/models"


# ---------------------------------------------------------------------------
# 契约五:BalanceResult 17 字段(§4)+ Q7 开关
# ---------------------------------------------------------------------------


class TestBalanceResultShape:
    def test_seventeen_fields_with_spec_names(self, cc):
        fields = [f.name for f in dataclasses.fields(cc.BalanceResult())]
        assert len(fields) == 17
        assert fields == [
            "gateway", "provider", "balance_usd", "tier", "plan", "account_type",
            "balance_native", "currency", "source", "evidence_kind", "detail",
            "quota", "usage", "entitlements", "identity", "alive", "matched",
        ]

    def test_default_balance_is_unmatched_and_honest(self, cc):
        result = cc.BalanceResult()
        assert result.matched is False
        assert result.balance_usd == ""
        assert result.alive is None

    def test_gateway_aliases_provider_on_match(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.deepseek.com/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("GET", "https://api.deepseek.com/user/balance", 200,
             '{"balance_infos":[{"currency":"CNY","total_balance":"10.00"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com", probe_balance=True)
        assert result.balance.gateway == result.balance.provider == "deepseek"

    def test_q7_balance_off_by_default(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.deepseek.com/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        assert result.balance is None, "Q7:余额/身份探测默认关"
        assert len(transport.calls) == 1, "默认只做存活探测"

    def test_balance_skipped_for_rejected_credentials(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(401, '{"error":"x"}'))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com", probe_balance=True)
        assert result.balance is None, "已判死的凭证无探测价值"
        assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# 契约六:13 家匿名余额矩阵逐家对齐(§5)—— happy path
# ---------------------------------------------------------------------------

_DEEPSEEK_MODELS = ("GET", "https://api.deepseek.com/v1/models", 200, '{"data":[{"id":"deepseek-chat"}]}', {})


class TestAnonymousBalanceMatrix:
    @pytest.mark.parametrize(
        ("case_name", "apikey", "apiurl", "routes", "expected"),
        [
            (
                "deepseek",
                KEY_GENERIC, "https://api.deepseek.com",
                [_DEEPSEEK_MODELS,
                 ("GET", "https://api.deepseek.com/user/balance", 200,
                  '{"balance_infos":[{"currency":"CNY","total_balance":"110.50"},'
                  '{"currency":"USD","total_balance":"1.25"}]}', {})],
                {"matched": True, "alive": True, "balance_native": "110.5", "currency": "CNY",
                 "balance_usd": "1.25", "evidence_kind": "cash_balance", "source": "deepseek:user_balance"},
            ),
            (
                "kimi-cn",
                KEY_GENERIC, "https://api.moonshot.cn/v1",
                [("GET", "https://api.moonshot.cn/v1/models", 200, '{"data":[{"id":"moonshot-v1-8k"}]}', {}),
                 ("GET", "https://api.moonshot.cn/v1/users/me/balance", 200,
                  '{"data":{"available_balance":"38.60"}}', {})],
                {"matched": True, "alive": True, "balance_native": "38.6", "currency": "CNY",
                 "evidence_kind": "cash_balance", "source": "kimi:users_me_balance"},
            ),
            (
                "kimi-ai-usd",
                KEY_GENERIC, "https://api.moonshot.ai/v1",
                [("GET", "https://api.moonshot.ai/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
                 ("GET", "https://api.moonshot.ai/v1/users/me/balance", 200,
                  '{"data":{"available_balance":"9.25"}}', {})],
                {"matched": True, "balance_usd": "9.25", "evidence_kind": "cash_balance",
                 "source": "kimi:users_me_balance"},
            ),
            (
                "minimax",
                KEY_GENERIC, "https://api.minimaxi.com/v1",
                [("GET", "https://api.minimaxi.com/v1/models", 200, '{"data":[{"id":"MiniMax-Text-01"}]}', {}),
                 ("GET", "https://api.minimaxi.com/v1/token_plan/remains", 200,
                  '{"base_resp":{"status_code":0},"model_remains":12345}', {})],
                {"matched": True, "alive": True, "quota": 12345, "evidence_kind": "quota",
                 "source": "minimax:token_plan_remains"},
            ),
            (
                "cohere",
                KEY_GENERIC, "https://api.cohere.com",
                [("GET", "https://api.cohere.com/v1/models", 200, '{"data":[{"id":"command-r"}]}', {}),
                 ("POST", "https://api.cohere.com/v1/check-api-key", 200,
                  '{"valid":true,"organization_id":"org-synth","owner_id":"ow-synth"}', {})],
                {"matched": True, "alive": True, "evidence_kind": "identity",
                 "identity": {"organization_id": "org-synth", "owner_id": "ow-synth"},
                 "source": "cohere:check_api_key"},
            ),
            (
                "together",
                KEY_GENERIC, "https://api.together.xyz/v1",
                [("GET", "https://api.together.xyz/v1/models", 200, '{"data":[{"id":"llama"}]}', {}),
                 ("GET", "https://api.together.xyz/v1/whoami", 200,
                  '{"id":"u-1","name":"n","email":"e@synth.example","project_id":"p-1","organization_id":"o-1"}', {})],
                {"matched": True, "alive": True, "evidence_kind": "identity", "source": "together:whoami",
                 "identity": {"id": "u-1", "name": "n", "email": "e@synth.example",
                              "project_id": "p-1", "organization_id": "o-1"}},
            ),
            (
                "replicate",
                "r8_synthQw7tXk2nRf9mBc4vZ", "https://api.replicate.com/v1",
                [("GET", "https://api.replicate.com/v1/models", 200, '{"data":[{"id":"llama"}]}', {}),
                 ("GET", "https://api.replicate.com/v1/account", 200,
                  '{"type":"user","username":"u","name":"n","github_url":"g"}', {})],
                {"matched": True, "alive": True, "evidence_kind": "identity", "source": "replicate:account",
                 "identity": {"type": "user", "username": "u", "name": "n", "github_url": "g"}},
            ),
            (
                "fireworks",
                "fw_synthQw7tXk2nRf9mBc4vZ", "https://api.fireworks.ai/inference/v1",
                [("GET", "https://api.fireworks.ai/inference/v1/models", 200, '{"data":[{"id":"llama"}]}', {}),
                 ("GET", "https://api.fireworks.ai/inference/v1/accounts", 200,
                  '{"accounts":[{"name":"acct-synth"}]}', {}),
                 ("GET", "https://api.fireworks.ai/inference/v1/acct-synth", 200,
                  '{"accountType":"ORGANIZATION","suspendState":"NONE"}', {}),
                 ("GET", "https://api.fireworks.ai/inference/v1/acct-synth/quotas", 200,
                  '{"quotas":[{"name":"monthly-spend-usd","maxValue":"500"}]}', {})],
                {"matched": True, "alive": True, "balance_usd": "N/A", "tier": "tier2",
                 "evidence_kind": "entitlement", "source": "fireworks:accounts",
                 "identity": {"accountType": "ORGANIZATION", "suspendState": "NONE"}},
            ),
            (
                "openrouter",
                KEY_OPENROUTER, "https://openrouter.ai/api",
                [("GET", "https://openrouter.ai/api/v1/auth/key", 200, '{"data":{"label":"k"}}', {}),
                 ("GET", "https://openrouter.ai/api/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
                 ("GET", "https://openrouter.ai/api/v1/auth/key", 200,
                  '{"data":{"limit_remaining":10.5,"is_free_tier":false}}', {})],
                {"matched": True, "alive": True, "balance_usd": "10.5", "evidence_kind": "cash_balance",
                 "source": "openrouter:auth_key"},
            ),
            (
                "qoder",
                KEY_QODER, "https://api.qoder.com",
                [("GET", "https://api.qoder.com/api/v1/cloud/models", 200,
                  '{"models":[{"name":"claude-sonnet-4-5"},{"name":"claude-opus-4"}],"plan":"pro"}', {})],
                {"matched": True, "alive": True, "plan": "pro", "evidence_kind": "entitlement",
                 "entitlements": ["claude-sonnet-4-5", "claude-opus-4"], "source": "qoder:cloud_models"},
            ),
            (
                "cursor",
                KEY_CURSOR, "https://api.cursor.com",
                [("GET", "https://api.cursor.com/v1/me", 200,
                  '{"apiKeyName":"k-1","userEmail":"u@synth.example","name":"Name S"}', {})],
                {"matched": True, "alive": True, "evidence_kind": "identity", "source": "cursor:me",
                 "identity": {"apiKeyName": "k-1", "userEmail": "u@synth.example", "name": "Name S"}},
            ),
            (
                "windsurf",
                KEY_GENERIC, "https://server.codeium.com/api/v1",
                [("GET", "https://server.codeium.com/api/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
                 ("POST", "https://server.codeium.com/api/v1/GetTeamCreditBalance", 200,
                  '{"addOnCreditsAvailable": 42.5}', {})],
                {"matched": True, "alive": True, "balance_native": "42.5", "currency": "credits",
                 "evidence_kind": "cash_balance", "source": "windsurf:GetTeamCreditBalance"},
            ),
        ],
    )
    def test_happy_path_per_provider(self, cc, registry, case_name, apikey, apiurl, routes, expected):
        transport = FakeTransport(routes)
        result = check(registry, transport, apikey=apikey, apiurl=apiurl, probe_balance=True)
        assert result.validation_state == "final_verified", f"{case_name}: {result.error}"
        assert_balance(result, **{"provider": case_name.split("-")[0], **expected})

    def test_deepseek_balance_401_matched_alive_false(self, cc, registry):
        """规格 §5 钉死:deepseek 余额端点 401 → matched 且 alive=false。"""
        transport = FakeTransport([
            _DEEPSEEK_MODELS,
            ("GET", "https://api.deepseek.com/user/balance", 401, '{"error":"x"}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com", probe_balance=True)
        assert result.validation_state == "final_verified"
        assert_balance(result, matched=True, alive=False, source="deepseek:unauthorized")

    def test_together_rate_limit_header_upgrades_to_quota(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.together.xyz/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("GET", "https://api.together.xyz/v1/whoami", 200, '{"id":"u-1"}',
             {"x-ratelimit-limit-requests": "1000"}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.together.xyz/v1", probe_balance=True)
        assert_balance(result, evidence_kind="quota", alive=True)

    def test_openrouter_free_tier_and_credits_fallback(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://openrouter.ai/api/v1/auth/key", 200, '{"data":{"label":"k"}}', {}),
            ("GET", "https://openrouter.ai/api/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("GET", "https://openrouter.ai/api/v1/auth/key", 200,
             '{"data":{"is_free_tier":true,"limit_remaining":null}}', {}),
            ("GET", "https://openrouter.ai/api/v1/credits", 200,
             '{"total_credits": 100, "total_usage": 100}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENROUTER, apiurl="https://openrouter.ai/api",
                       probe_balance=True)
        assert_balance(result, balance_usd="0", matched=True, alive=True)

    def test_minimax_bad_base_resp_falls_back_unmatched(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.minimaxi.com/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("GET", "https://api.minimaxi.com/v1/token_plan/remains", 200,
             '{"base_resp":{"status_code":1004}}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.minimaxi.com/v1",
                       probe_balance=True)
        assert result.balance is not None
        assert result.balance.matched is False

    @pytest.mark.parametrize("max_value", [("50", "tier1"), ("5000", "tier3"), ("50000", "tier4"), ("ENTERPRISE", "enterprise")])
    def test_fireworks_tier_ladder(self, cc, registry, max_value):
        raw, tier = max_value
        transport = FakeTransport([
            ("GET", "https://api.fireworks.ai/inference/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("GET", "https://api.fireworks.ai/inference/v1/accounts", 200, '{"accounts":[{"name":"a"}]}', {}),
            ("GET", "https://api.fireworks.ai/inference/v1/a", 200, '{"accountType":"ORG"}', {}),
            ("GET", "https://api.fireworks.ai/inference/v1/a/quotas", 200,
             f'{{"quotas":[{{"name":"monthly-spend-usd","maxValue":"{raw}"}}]}}', {}),
        ])
        result = check(registry, transport, apikey="fw_synthQw7tXk2nRf9mBc4vZ",
                       apiurl="https://api.fireworks.ai/inference/v1", probe_balance=True)
        assert_balance(result, tier=tier)


# ---------------------------------------------------------------------------
# 契约七:openai / anthropic 余额链(§5 两家多段式)
# ---------------------------------------------------------------------------

_OPENAI_MODELS = ("GET", "https://api.openai.com/v1/models", 200, '{"data":[{"id":"gpt-4o-mini"}]}', {})


class TestOpenaiBalanceChain:
    def test_credit_grants_hit(self, cc, registry):
        transport = FakeTransport([
            _OPENAI_MODELS,
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 200,
             '{"total_granted": 5.5}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1", probe_balance=True)
        assert_balance(result, balance_usd="5.5", account_type="project", alive=True,
                       evidence_kind="cash_balance", source="openai:credit_grants")

    def test_subscription_fallback(self, cc, registry):
        transport = FakeTransport([
            _OPENAI_MODELS,
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 404, "{}", {}),
            ("GET", "https://api.openai.com/dashboard/billing/subscription", 200,
             '{"hard_limit_usd": 120, "usage": 20}', {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1", probe_balance=True)
        assert_balance(result, balance_usd="100", source="openai:subscription")

    def test_liveness_fallback_with_ratelimit_tier(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.openai.com/v1/models", 200, '{"data":[{"id":"m"}]}',
             {"x-ratelimit-limit-requests": "10000"}),
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 404, "{}", {}),
            ("GET", "https://api.openai.com/dashboard/billing/subscription", 404, "{}", {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1", probe_balance=True)
        assert_balance(result, balance_usd="N/A", tier="tier5", account_type="project",
                       evidence_kind="liveness", source="openai:models", alive=True)

    def test_429_records_rate_limited_alive_true(self, cc, registry):
        transport = FakeTransport([
            _OPENAI_MODELS,
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 429, "{}",
             {"x-ratelimit-limit-requests": "600"}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1", probe_balance=True)
        assert_balance(result, matched=True, alive=True, evidence_kind="liveness", source="openai:credit_grants")

    def test_ordinary_key_account_type(self, cc, registry):
        transport = FakeTransport([
            _OPENAI_MODELS,
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 404, "{}", {}),
            ("GET", "https://api.openai.com/dashboard/billing/subscription", 404, "{}", {}),
        ])
        result = check(registry, transport, apikey=KEY_OPENAI_ORDINARY, apiurl="https://api.openai.com/v1",
                       probe_balance=True)
        assert_balance(result, account_type="ordinary", balance_usd="N/A")


class TestAnthropicBalanceChain:
    def test_admin_key_organizations_chain(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3"}]}', {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me", 200,
             '{"id":"org-1","name":"synth-org","usage_tier":"scale"}', {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me/cost_report", 200,
             '{"cost_by_date":{"2026-10-01": 5000, "2026-10-02": 2500}}', {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC_ADMIN, apiurl="https://api.anthropic.com",
                       probe_balance=True)
        assert_balance(result, matched=True, alive=True, tier="scale", evidence_kind="identity",
                       source="anthropic:organizations_me", identity={"id": "org-1", "name": "synth-org"},
                       usage={"usd_spend": "75"})
        assert len(transport.calls) == 3, "tier 已知时不再打 rate_limits"

    def test_admin_key_rate_limits_tier_fallback(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3"}]}', {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me", 200, '{"id":"org-1"}', {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me/cost_report", 404, "{}", {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me/rate_limits", 200,
             '{"usage_tier":"build"}', {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC_ADMIN, apiurl="https://api.anthropic.com",
                       probe_balance=True)
        assert_balance(result, tier="build")

    def test_ordinary_key_liveness_only(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC, apiurl="https://api.anthropic.com",
                       probe_balance=True)
        assert_balance(result, balance_usd="N/A", evidence_kind="liveness", matched=True, alive=True)
        assert len(transport.calls) == 1, "普通键零余额出网,只复用验证存活"

    def test_oauth_key_org_429_alive_true(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.anthropic.com/v1/models", 200, '{"data":[{"id":"claude-3"}]}', {}),
            ("GET", "https://api.anthropic.com/v1/organizations/me", 429, "{}", {}),
        ])
        result = check(registry, transport, apikey=KEY_ANTHROPIC_OAT, apiurl="https://api.anthropic.com",
                       probe_balance=True)
        assert_balance(result, matched=True, alive=True, evidence_kind="liveness")


# ---------------------------------------------------------------------------
# 契约八:家族兜底 —— 仅存活性 / 被动标记 / 无探测诚实 unknown(§5)
# ---------------------------------------------------------------------------


class TestBalanceFamilies:
    @pytest.mark.parametrize(
        ("provider", "apikey", "apiurl", "models_url"),
        [
            ("gemini", KEY_GEMINI, "https://generativelanguage.googleapis.com",
             f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY_GEMINI}"),
            ("xai", "xai-synthQw7tXk2nRf9mBc4", "https://api.x.ai/v1", "https://api.x.ai/v1/models"),
            ("aws_bedrock", KEY_BEDROCK, "https://bedrock-runtime.us-east-1.amazonaws.com",
             "https://bedrock-runtime.us-east-1.amazonaws.com/foundation-models"),
            ("glm", KEY_GENERIC, "https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4/models"),
            ("groq", "gsk_synthQw7tXk2nRf9mBc4v", "https://api.groq.com/openai/v1",
             "https://api.groq.com/openai/v1/models"),
        ],
    )
    def test_liveness_only_family_na_balance(self, cc, registry, provider, apikey, apiurl, models_url):
        transport = FakeTransport([("GET", models_url, 200, '{"data":[{"id":"m"}]}', {})])
        result = check(registry, transport, apikey=apikey, apiurl=apiurl, probe_balance=True)
        assert result.validation_state == "final_verified"
        assert_balance(result, balance_usd="N/A", evidence_kind="liveness", matched=True, alive=True,
                       **{"provider": provider})
        assert len(transport.calls) == 1, "仅存活性家族零余额出网"

    def test_glm_depleted_code_passive_quota(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://open.bigmodel.cn/api/paas/v4/models", 429,
             '{"error":{"code":"1308","message":"insufficient","reset_at":"2026-10-04T00:00:00Z"}}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://open.bigmodel.cn/api/paas/v4",
                       probe_balance=True)
        assert result.validation_state == "transient"
        assert_balance(result, matched=True, alive=True, evidence_kind="quota", source="glm:passive_error",
                       quota={"depleted_code": "1308", "reset_at": "2026-10-04T00:00:00Z"})

    def test_longcat_depleted_marker_in_raw_text(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://api.longcat.chat/openai/v1/models", 429,
             "HTTP 429: 账户余额不足,请充值后重试", {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.longcat.chat/openai",
                       probe_balance=True)
        assert result.provider == "longcat"
        assert result.validation_state == "transient"
        assert_balance(result, matched=True, alive=True, evidence_kind="quota",
                       source="longcat:passive_depleted", quota={"depleted": True})

    @pytest.mark.parametrize(
        ("provider", "apikey", "apiurl", "models_url"),
        [
            ("kiro", "ksk_synthQw7tXk2nRf9mBc4", "https://app.kiro.dev", "https://app.kiro.dev/v1/models"),
            ("azure_openai", KEY_GENERIC, "https://synth-res.openai.azure.com",
             "https://synth-res.openai.azure.com/openai/models?api-version=2024-10-21"),
            ("longcat", KEY_GENERIC, "https://api.longcat.chat/openai", "https://api.longcat.chat/openai/v1/models"),
        ],
    )
    def test_no_probe_family_honest_unknown(self, cc, registry, provider, apikey, apiurl, models_url):
        transport = FakeTransport([("GET", models_url, 200, '{"data":[{"id":"m"}]}', {})])
        result = check(registry, transport, apikey=apikey, apiurl=apiurl, probe_balance=True)
        assert result.validation_state == "final_verified"
        balance = result.balance
        assert balance is not None
        assert balance.matched is False, "无探测 = 诚实 unknown,不装懂"
        assert balance.balance_usd == "", "未匹配是空串,不是 N/A"
        assert balance.alive is True, "被动存活回填自验证上下文"
        assert len(transport.calls) == 1

    def test_kimi_non_moonshot_host_degrades_to_liveness(self, cc):
        assert cc._kimi_currency("proxy.example.com") == ""
        assert cc._kimi_currency("api.moonshot.cn") == "CNY"
        assert cc._kimi_currency("api.moonshot.ai") == "USD"

    def test_unlisted_provider_defaults_to_liveness(self, cc, registry):
        """未列矩阵的供应商(如 siliconflow)走存活性兜底(§5「其余未列」)。"""
        transport = FakeTransport([
            ("GET", "https://api.siliconflow.cn/v1/models", 200, '{"data":[{"id":"Qwen/Qwen2.5-7B-Instruct"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.siliconflow.cn/v1",
                       probe_balance=True)
        assert result.provider == "siliconflow"
        assert_balance(result, balance_usd="N/A", evidence_kind="liveness", matched=True)


# ---------------------------------------------------------------------------
# 契约九:Q8 限速(串行 + 每供应商 RPM≤30)+ 批量
# ---------------------------------------------------------------------------


class TestPacingQ8:
    def test_constants_pin_q8(self, cc):
        assert cc.PER_PROVIDER_RPM_LIMIT == 30
        assert cc.MIN_REQUEST_INTERVAL_SECONDS == 2.0
        assert 60 / cc.MIN_REQUEST_INTERVAL_SECONDS == cc.PER_PROVIDER_RPM_LIMIT

    def test_pacer_sleeps_only_within_interval(self, cc):
        now = {"t": 100.0}
        sleeps: list[float] = []
        pacer = cc.Pacer(cc.MIN_REQUEST_INTERVAL_SECONDS, clock=lambda: now["t"], sleep=sleeps.append)
        pacer.wait("openai")
        assert sleeps == [], "首次请求不等待"
        now["t"] += 0.5
        pacer.wait("openai")
        assert sleeps == [pytest.approx(1.5)], "间隔不足补睡差值"
        now["t"] += 3.0
        pacer.wait("openai")
        assert len(sleeps) == 1, "间隔已足不再睡"
        pacer.wait("deepseek")
        assert len(sleeps) == 1, "不同供应商独立计时"

    def test_every_request_goes_through_pacer(self, cc, registry):
        pacer = FakePacer()
        transport = FakeTransport([
            _OPENAI_MODELS,
            ("GET", "https://api.openai.com/dashboard/billing/credit_grants", 404, "{}", {}),
            ("GET", "https://api.openai.com/dashboard/billing/subscription", 404, "{}", {}),
        ])
        check(registry, transport, apikey=KEY_OPENAI, apiurl="https://api.openai.com/v1",
              probe_balance=True, pacer=pacer)
        assert pacer.waits == ["openai"] * 3, "每次出网前都过同供应商限速闸"

    def test_check_credentials_serial_with_shared_pacer(self, cc, registry):
        pacer = FakePacer()
        transport = FakeTransport([
            _DEEPSEEK_MODELS,
            ("GET", "https://api.x.ai/v1/models", 401, "{}", {}),
        ])
        results = cc.check_credentials(
            [{"apikey": KEY_GENERIC, "apiurl": "https://api.deepseek.com"},
             {"apikey": "xai-synthQw7tXk2nRf9mBc4", "apiurl": "https://api.x.ai/v1"}],
            registry=registry, transport=transport, pacer=pacer,
        )
        assert [r.validation_state for r in results] == ["final_verified", "rejected"]
        assert pacer.waits == ["deepseek", "xai"], "串行:按记录顺序逐个出网"

    @pytest.mark.parametrize("bad", [{"apikey": ""}, {"apikey": 42}, {"apiurl": 7}, ["not-a-map"]])
    def test_check_credentials_bad_shape_raises(self, cc, registry, bad):
        with pytest.raises(ValueError, match=r"records\[0\]"):
            cc.check_credentials([bad], registry=registry, transport=FakeTransport(), pacer=FakePacer())

    def test_check_credentials_empty_records(self, cc, registry):
        assert cc.check_credentials([], registry=registry, transport=FakeTransport()) == []


# ---------------------------------------------------------------------------
# 契约十:Q9 掩码红线(输出永不带全文密钥)+ 独立 probe_models
# ---------------------------------------------------------------------------


class TestMaskingRedLine:
    def test_echoed_body_never_leaks_full_key(self, cc, registry):
        body = json.dumps({"data": [{"id": "m"}], "echo": KEY_GENERIC})
        transport = FakeTransport([
            ("GET", "https://api.deepseek.com/v1/models", 200, body, {}),
            ("GET", "https://api.deepseek.com/user/balance", 200,
             json.dumps({"balance_infos": [{"currency": "CNY", "total_balance": "1.00"}], "echo": KEY_GENERIC}), {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com", probe_balance=True)
        leaked = _adapter().findings.find_full_key_leak(dataclasses.asdict(result), KEY_GENERIC)
        assert leaked == [], f"Q9 红线:全文密钥出现在 {leaked}"
        assert cc.mask_apikey(KEY_GENERIC) in result.response_snippet
        assert cc.mask_apikey(KEY_GENERIC) in json.dumps(result.balance.detail, ensure_ascii=False)

    def test_gemini_key_in_url_stays_out_of_results(self, cc, registry):
        transport = FakeTransport([
            ("GET", f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY_GEMINI}", 200,
             '{"models":[{"name":"models/gemini-2.0-flash"}]}', {}),
        ])
        result = check(registry, transport, apikey=KEY_GEMINI,
                       apiurl="https://generativelanguage.googleapis.com", probe_balance=True)
        leaked = _adapter().findings.find_full_key_leak(dataclasses.asdict(result), KEY_GEMINI)
        assert leaked == []

    def test_windsurf_body_key_stays_out_of_results(self, cc, registry):
        transport = FakeTransport([
            ("GET", "https://server.codeium.com/api/v1/models", 200, '{"data":[{"id":"m"}]}', {}),
            ("POST", "https://server.codeium.com/api/v1/GetTeamCreditBalance", 200,
             '{"addOnCreditsAvailable": 1.0, "echo": "%s"}' % KEY_GENERIC, {}),
        ])
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://server.codeium.com/api/v1",
                       probe_balance=True)
        leaked = _adapter().findings.find_full_key_leak(dataclasses.asdict(result), KEY_GENERIC)
        assert leaked == []

    def test_result_to_dict_is_json_serializable_and_masked(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(200, '{"data":[{"id":"m"}],"echo":"%s"}' % KEY_GENERIC))
        result = check(registry, transport, apikey=KEY_GENERIC, apiurl="https://api.deepseek.com")
        payload = cc.result_to_dict(result)
        roundtrip = json.loads(json.dumps(payload))
        assert roundtrip["provider"] == "deepseek"
        assert KEY_GENERIC not in json.dumps(roundtrip)


class TestProbeModelsStandalone:
    def test_happy_path(self, cc, registry):
        transport = FakeTransport(_deepseek_routes(200, '{"data":[{"id":"m"}]}'))
        probe = cc.probe_models(apikey=KEY_GENERIC, apiurl="https://api.deepseek.com",
                                registry=registry, transport=transport, pacer=FakePacer())
        assert (probe.key_state, probe.models, probe.provider, probe.status_code) == ("active", ("m",), "deepseek", 200)

    def test_no_url_unavailable(self, cc, registry):
        probe = cc.probe_models(apikey=KEY_GENERIC, apiurl="", registry=registry,
                                transport=FakeTransport(), pacer=FakePacer())
        assert (probe.key_state, probe.error) == ("unavailable", "no API URL")
