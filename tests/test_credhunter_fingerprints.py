"""myia-credhunter 插件(R3 指纹库 + 骨架)契约测试.

任务 10-03-aipocket-fusion 插件包纯增量段的配套测试,只读插件目录、
零网络、零真实凭据(fixture 全部为合成脱敏键,不对应任何真实账号)。
五组被钉住的契约:

1. **manifest 形状**:tier=desktop、adapter(in_process)、provides 三能力
   (credhunt/credcheck/exposure)、modes.local(纯源码形态)、vendor=None
   (功能重实现,零上游 vendored);
2. **指纹库 loader**:发现层 20 包 + 验证层 25 规格(数据文件化,形状
   fail-fast,坏数据结构化报错)、github 查询池拼接去重、resolve 三级
   归因(域名 → 前缀 → unknown);
3. **指纹匹配**:联合正则十大族全覆盖、17 条细正则、变量名归因(含
   后缀剥离与 azure_openai 先于 openai 的特异性排序)、apiurl 前缀归因、
   噪声过滤(长度/占位子串/字母序递增段/阻断格式/跨位置过曝);
4. **items 形状 + Q9 掩码**:前 8 后 4 掩码(短键退化)、dedup 键为结构化
   字段组合且 {title} 永不进键(用 myia.dedup.DedupRegistry 证明管线可
   渲染)、全文密钥永不进 item(含 content 摘录的先替换后截断);
5. **适配器双入口**:run 装配掩码-only payload、空文档/无凭据 lane 的
   显式空态(credential_missing,不报错)、async fetch 引擎面、坏文档
   结构化错误(invalid_document)。

测试纪律:经 myia.cli._import_plugin_adapter 按 CLI 同款加载器
(compile+exec)加载真实适配器(与 tests/test_proxy_plugin.py 同款先例)。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from myia.cli import _import_plugin_adapter
from myia.dedup import DedupRegistry
from myia.plugins.manifest import load_manifest_file

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "myia-credhunter"

# ---------------------------------------------------------------------------
# 合成脱敏 fixture 键(全部人工构造、不对应任何真实账号;形态只服务于
# 正则行为验证)。注意避开噪声子串与 ≥8 字母序递增段。
# ---------------------------------------------------------------------------
KEY_OPENAI = "sk-4f1c9d2e8b7a6305abcd"
KEY_OPENROUTER = "sk-or-v1-7c3f9a1e5d2b4086bbccddeeff00112233445566"
KEY_ANTHROPIC = "sk-ant-api03-Xq2w9r8t7y6u5i4o3p"
KEY_GEMINI = "AIzaQy77bXn2mKs4Pw9rTtZv8u1C"
KEY_GROQ = "gsk_Qw7tXk2nRf9mBc4vZa6p"
KEY_NVIDIA = "nvapi-Fr8kNm3qWx7cJv2zBt5y"
KEY_REPLICATE = "r8_Kq3wZx9cVm7bNt4pJs6y"
KEY_XAI = "xai-Hs5vBn8mKj2wQr7tYc3f"
KEY_KIRO = "ksk_Pz6xWc4vNk9mQt2bGj7y"
KEY_CURSOR = "crsr_Ax7vNm3kQp9wZt5bHc2jLr8sYd4fGu61e"
KEY_QODER = "pt-Dw8cXm5nBq3vKr7tZj9y"
KEY_BEDROCK = "ABSKMn4kQp7wXz2cVb9nRt5yJh3="
KEY_GITHUB = "ghp_Rt8nKm3wQx7cVb2zJy5dFs9aLp4wGu6Xq1eZ"
KEY_JWT = "eyJhbGciOiJIUzI1NiJ9.c3ViamVjdC1ib2R5.Ao7Bq3Xd9Km2Nv5Rs8T"
KEY_GLM_LEGACY = "0f1e2d3c4b5a69788796a5b4c3d2e1f0.Zq8wXc5vNk2mJb7y"
KEY_PPLX = "pplx-Nm3wQx8cVb5rTt9yKj2f"
KEY_HF = "hf_Wq7cXn4vMk9bJr2tYd5s"
KEY_FW = "fw_Er6yXm3cVb8nKq2wJt7d"


def load_adapter() -> Any:
    """按 CLI 同款加载器(compile+exec)加载真实适配器."""
    return _import_plugin_adapter(PLUGINS_DIR, "myia-credhunter")


@pytest.fixture()
def adapter() -> Any:
    return load_adapter()


@pytest.fixture()
def fp(adapter) -> Any:
    return adapter.fingerprints


@pytest.fixture()
def fnd(adapter) -> Any:
    return adapter.findings


# ---------------------------------------------------------------------------
# 契约一:manifest 形状(desktop 分级 + in_process 适配器 + 零 vendored)
# ---------------------------------------------------------------------------


class TestCredhunterManifest:
    def test_desktop_tier_with_in_process_adapter(self):
        manifest = load_manifest_file(PLUGIN_DIR / "plugin.yaml")
        assert manifest.id == "myia-credhunter"
        assert manifest.tier == "desktop"
        assert manifest.requires == []
        assert manifest.provides == ["credhunt", "credcheck", "exposure"]
        assert manifest.adapter is not None
        assert manifest.adapter.entry == "adapter.py"
        assert manifest.adapter.mode == "in_process"
        assert manifest.vendor is None, "功能重实现:零上游 vendored"
        assert manifest.modes.local is not None, "纯源码形态:local 安装声明"
        assert manifest.modes.remote is None, "无服务端点(远程聚合走 myia-credentials)"


# ---------------------------------------------------------------------------
# 契约二:指纹库 loader(20 包 + 25 规格,数据文件化 + fail-fast)
# ---------------------------------------------------------------------------


class TestPackLoader:
    def test_twenty_packs_with_unique_ids(self, adapter):
        loaded = adapter.packs.load_packs()
        assert len(loaded) == 20
        ids = [pack.id for pack in loaded]
        assert len(set(ids)) == 20
        assert {"openai", "anthropic", "azure_openai", "together"} <= set(ids)

    def test_github_only_packs_have_empty_fofa_shodan(self, adapter):
        loaded = {pack.id: pack for pack in adapter.packs.load_packs()}
        github_only = {"cohere", "deepseek", "fireworks", "glm", "kimi", "longcat", "minimax", "qwen", "replicate", "together"}
        for pack_id in github_only:
            assert loaded[pack_id].fofa_queries == ()
            assert loaded[pack_id].shodan_queries == ()
            assert loaded[pack_id].github_terms, f"{pack_id} 应有 GitHub 键名查询"

    def test_openai_pack_carries_env_query(self, adapter):
        openai = next(pack for pack in adapter.packs.load_packs() if pack.id == "openai")
        assert "sk- filename:.env" in openai.github_terms
        assert openai.fofa_queries and openai.shodan_queries

    def test_github_query_pool_dedupes_in_order(self, adapter):
        loaded = adapter.packs.load_packs()
        pool = adapter.packs.github_query_pool(loaded)
        assert len(pool) == len(set(pool)), "查询池必须去重"
        assert "sk- filename:.env" in pool

    def test_query_counts_are_diagnostic_shaped(self, adapter):
        counts = adapter.packs.discovery_query_counts(adapter.packs.load_packs())
        assert len(counts) == 20
        assert set(counts[0]) == {"id", "fofa", "shodan", "github"}, "计数口径,不回显查询文本"

    def test_broken_data_file_fails_fast(self, adapter, tmp_path):
        bad = tmp_path / "bad_packs.yaml"
        bad.write_text("packs:\n  - id: openai\n    nonsense: true\n", encoding="utf-8")
        with pytest.raises(adapter.packs.PackDataError) as exc_info:
            adapter.packs.load_packs(bad)
        assert exc_info.value.code == "packs_invalid"
        assert "nonsense" in exc_info.value.message


class TestSpecLoader:
    def test_twenty_five_specs_with_unique_names(self, adapter):
        loaded = adapter.specs.load_specs()
        assert len(loaded) == 25
        names = [spec.name for spec in loaded]
        assert len(set(names)) == 25
        # 规格差集:SPECS 多 nvidia/ksyun/siliconflow/groq/openrouter(fingerprints.md §3)
        assert {"nvidia", "ksyun", "siliconflow", "groq", "openrouter"} <= set(names)

    def test_openai_and_openrouter_match_behavior_spec(self, adapter):
        registry = adapter.specs.ProviderRegistry(adapter.specs.load_specs())
        openai = registry.spec("openai")
        assert openai.category == "international"
        assert openai.domain_suffixes == ("openai.com", "oaiusercontent.com")
        assert openai.key_prefixes == ("sk-proj-", "sk-admin-", "sk-svcacct-")
        assert openai.protocol == "openai_compatible"
        assert openai.models == ("gpt-4o-mini",)
        assert openai.official_api_url == "https://api.openai.com/v1"
        openrouter = registry.spec("openrouter")
        assert openrouter.domain_suffixes == ("openrouter.ai",)
        assert openrouter.key_prefixes == ("sk-or-",)
        assert openrouter.models == ("openai/gpt-4o-mini",)
        assert openrouter.official_api_url == "https://openrouter.ai/api"

    def test_azure_openai_official_url_is_empty(self, adapter):
        registry = adapter.specs.ProviderRegistry(adapter.specs.load_specs())
        assert registry.spec("azure_openai").official_api_url == ""
        assert registry.spec("azure_openai").domain_suffixes == ("openai.azure.com",)

    def test_generic_sk_prefix_stays_out_of_key_prefixes(self, adapter):
        for spec in adapter.specs.load_specs():
            assert "sk-" not in spec.key_prefixes, "泛前缀会吞掉所有 sk-* 网关键(规格 §4)"

    def test_resolve_prefers_domain_then_prefix_then_unknown(self, adapter):
        registry = adapter.specs.ProviderRegistry(adapter.specs.load_specs())
        by_domain = registry.resolve("https://api.openai.com/v1", "whatever")
        assert (by_domain.provider, by_domain.reason, by_domain.apiurl) == (
            "openai", "domain", "https://api.openai.com/v1",
        )
        by_prefix = registry.resolve("", KEY_OPENROUTER)
        assert (by_prefix.provider, by_prefix.reason) == ("openrouter", "key_prefix")
        assert by_prefix.apiurl == "https://openrouter.ai/api", "无传入 URL 回落官方基址"
        unknown = registry.resolve("", "opaque-key-without-any-hint")
        assert (unknown.provider, unknown.reason, unknown.apiurl) == ("unknown", "unknown", "")

    def test_resolve_bedrock_host_special_case(self, adapter):
        registry = adapter.specs.ProviderRegistry(adapter.specs.load_specs())
        bedrock = registry.resolve("https://bedrock-runtime.us-east-1.amazonaws.com", "ABSKopaque")
        assert (bedrock.provider, bedrock.reason) == ("aws_bedrock", "domain")
        # 非 Bedrock 的 amazonaws.com host 不误归因(如 S3)
        s3 = registry.resolve("https://bucket.s3.us-east-1.amazonaws.com", "opaque")
        assert s3.provider == "unknown", "aws_bedrock 特判只认 bedrock./bedrock- 前缀 host"

    def test_broken_data_file_fails_fast(self, adapter, tmp_path):
        bad = tmp_path / "bad_specs.yaml"
        bad.write_text("specs:\n  - name: openai\n    category: alien\n", encoding="utf-8")
        with pytest.raises(adapter.specs.SpecDataError) as exc_info:
            adapter.specs.load_specs(bad)
        assert exc_info.value.code == "specs_invalid"
        assert "category" in exc_info.value.message

    def test_data_files_are_valid_yaml_with_declared_roots(self):
        packs_raw = yaml.safe_load((PLUGIN_DIR / "credhunter/data/provider_packs.yaml").read_text(encoding="utf-8"))
        specs_raw = yaml.safe_load((PLUGIN_DIR / "credhunter/data/provider_specs.yaml").read_text(encoding="utf-8"))
        assert set(packs_raw) == {"packs"} and len(packs_raw["packs"]) == 20
        assert set(specs_raw) == {"specs"} and len(specs_raw["specs"]) == 25


# ---------------------------------------------------------------------------
# 契约三:指纹匹配(联合正则 + 细正则 + 归因 + 噪声过滤)
# ---------------------------------------------------------------------------


class TestJointRegex:
    @pytest.mark.parametrize(
        ("key", "provider"),
        [
            (KEY_OPENAI, "unknown"),      # 泛 sk- 无上下文不归因(规格 §4:sk- 无前缀分支)
            (KEY_GEMINI, "gemini"),
            (KEY_GROQ, "groq"),
            (KEY_NVIDIA, "nvidia"),
            (KEY_REPLICATE, "replicate"),
            (KEY_XAI, "xai"),
            (KEY_KIRO, "kiro"),
            (KEY_CURSOR, "cursor"),
            (KEY_QODER, "qoder"),
            (KEY_BEDROCK, "aws_bedrock"),
        ],
    )
    def test_ten_key_families_all_hit(self, fp, key, provider):
        hits = fp.extract_secrets(key)
        assert len(hits) == 1
        assert hits[0].apikey == key
        assert hits[0].provider == provider

    def test_same_string_deduped_within_text(self, fp):
        text = f"first {KEY_OPENROUTER} then again {KEY_OPENROUTER}"
        hits = fp.extract_secrets(text)
        assert [hit.apikey for hit in hits] == [KEY_OPENROUTER]

    def test_empty_and_no_hit_text_return_empty(self, fp):
        assert fp.extract_secrets("") == []
        assert fp.extract_secrets("nothing interesting here") == []


class TestFinePatterns:
    def test_exactly_seventeen_patterns(self, fp):
        assert len(fp.FINE_KEY_PATTERNS) == 17

    @pytest.mark.parametrize(
        ("key", "provider"),
        [
            (KEY_OPENROUTER, "openrouter"),
            (KEY_ANTHROPIC, "anthropic"),
            (KEY_GEMINI, "gemini"),
            (KEY_GROQ, "groq"),
            (KEY_NVIDIA, "nvidia"),
            (KEY_REPLICATE, "replicate"),
            (KEY_XAI, "xai"),
            (KEY_KIRO, "kiro"),
            (KEY_CURSOR, "cursor"),
            (KEY_QODER, "qoder"),
            (KEY_PPLX, "perplexity"),
            (KEY_HF, "huggingface"),
            (KEY_FW, "fireworks"),
            (KEY_GITHUB, "github"),
            (KEY_JWT, "jwt"),
            (KEY_GLM_LEGACY, "glm"),
        ],
    )
    def test_fine_patterns_attribute_provider(self, fp, key, provider):
        hits = fp.extract_secrets(key)
        assert len(hits) == 1
        assert hits[0].provider == provider

    def test_openrouter_hex_constraint_rejects_non_hex(self, fp):
        loose = "sk-or-v1-" + "Zq7wXc5vNk2mJb7yTd5sQw3e"  # 大写字母不在 hex 类
        # 联合正则仍会命中(sk- 泛族),但细正则不得误归因 openrouter
        hits = fp.extract_secrets(loose)
        assert hits and all(hit.provider != "openrouter" for hit in hits)


class TestVariableAttribution:
    def test_suffix_stripping(self, fp):
        assert fp.strip_variable_suffix("GROK_API_KEY") == "GROK"
        assert fp.strip_variable_suffix("QODER_PERSONAL_ACCESS_TOKEN") == "QODER"
        assert fp.strip_variable_suffix("WINDSURF_SERVICE_KEY") == "WINDSURF"
        assert fp.strip_variable_suffix("REPLICATE_API_TOKEN") == "REPLICATE"

    @pytest.mark.parametrize(
        ("variable", "provider"),
        [
            ("GROK_API_KEY", "xai"),
            ("ZHIPUAI_API_KEY", "glm"),
            ("DASHSCOPE_API_KEY", "qwen"),
            ("AZURE_OPENAI_API_KEY", "azure_openai"),
            ("OPENAI_API_KEY", "openai"),
            ("WINDSURF_SERVICE_KEY", "windsurf"),
            ("CODEIUM_SERVICE_KEY", "windsurf"),
            ("AWS_BEARER_TOKEN_BEDROCK", "aws_bedrock"),
        ],
    )
    def test_provider_hints(self, fp, variable, provider):
        assert fp.attribute_by_variable(variable) == provider

    def test_specific_hint_wins_over_generic(self, fp):
        # azure_openai(12 字符)必须先于 openai(6 字符)匹配(长度降序排序保证)
        substrings = [substring for _provider, substring in fp.PROVIDER_HINTS]
        assert substrings.index("azure_openai") < substrings.index("openai")

    def test_generic_sk_attributed_via_variable_in_context(self, fp):
        text = f"OPENAI_API_KEY={KEY_OPENAI}"
        hits = fp.extract_secrets(text)
        assert len(hits) == 1
        assert hits[0].provider == "openai"
        assert hits[0].variable == "OPENAI_API_KEY"
        assert hits[0].matched_by == "joint"

    def test_bare_generic_sk_stays_unknown(self, fp):
        hits = fp.extract_secrets(f"loose mention of {KEY_OPENAI} without context")
        assert hits[0].provider == "unknown"


class TestApiurlAttribution:
    @pytest.mark.parametrize(
        ("key", "url"),
        [
            (KEY_XAI, "https://api.x.ai/v1"),
            (KEY_KIRO, "https://app.kiro.dev"),
            (KEY_CURSOR, "https://api.cursor.com"),
            (KEY_QODER, "https://api.qoder.com"),
            (KEY_BEDROCK, "https://bedrock-runtime.us-east-1.amazonaws.com"),
            (KEY_GEMINI, "https://generativelanguage.googleapis.com"),
            (KEY_OPENROUTER, "https://openrouter.ai/api"),
        ],
    )
    def test_prefix_to_official_url(self, fp, key, url):
        assert fp.attribute_apiurl(key) == url

    def test_endpoint_from_caller_wins(self, fp):
        assert fp.attribute_apiurl(KEY_XAI, endpoint="https://hit-page.example.com/raw") == (
            "https://hit-page.example.com/raw"
        )

    def test_windsurf_context_variable_maps_to_codeium(self, fp):
        assert fp.attribute_apiurl("opaque-service-key", variable="WINDSURF_SERVICE_KEY") == (
            "https://server.codeium.com/api/v1"
        )

    def test_unattributed_returns_empty(self, fp):
        assert fp.attribute_apiurl("opaque-key-no-hint-at-all") == ""


class TestNoiseFiltering:
    def test_min_length(self, fp):
        assert fp.is_noise("sk-shortkey1") is True
        assert fp.is_noise(KEY_OPENAI) is False

    @pytest.mark.parametrize(
        "key",
        [
            "sk-this-is-an-example-key-12345",
            "sk-your-key-goes-right-here-9",
            "sk-proj-xxxxxxxxxxxxxxxxxxxx",
        ],
    )
    def test_placeholder_substrings(self, fp, key):
        assert fp.is_noise(key) is True

    def test_alphabet_run_detector(self, fp):
        assert fp.has_alphabet_run("xxabcdefghzz") is True
        assert fp.has_alphabet_run("Zq7wXc5vNk") is False
        assert fp.is_noise("sk-abcdefgh12345678pqrst") is True

    def test_blocked_formats(self, fp):
        assert fp.is_blocked_format("gocspx-tutorial-key-123456") is True
        assert fp.is_blocked_format("AKIA1234ABCD5678EFGH") is True
        assert fp.is_blocked_format("0f1e2d3c4b5a69788796a5b4c3d2e1f0") is True
        assert fp.is_blocked_format(KEY_OPENAI) is False

    def test_scan_drops_noise_hits(self, fp):
        text = f"real={KEY_XAI} fake=sk-this-is-an-example-key-12345"
        hits = fp.extract_secrets(text)
        assert [hit.apikey for hit in hits] == [KEY_XAI]

    def test_overexposed_keys_dropped_across_locations(self, fp):
        key = KEY_XAI
        locations = {f"https://repo-{index}.example.com/blob": [key] for index in range(6)}
        dropped = fp.drop_overexposed(locations)
        assert dropped == {key}
        five = {f"https://repo-{index}.example.com/blob": [key] for index in range(5)}
        assert fp.drop_overexposed(five) == set(), "≤5 个位置保留(教程键阈值 >5)"


# ---------------------------------------------------------------------------
# 契约四:items 形状 + Q9 掩码(前 8 后 4;全文永不入 item)
# ---------------------------------------------------------------------------


class TestMasking:
    def test_head8_tail4(self, fnd):
        masked = fnd.mask_apikey("sk-4f1c9d2e8b7a6305abcd")
        assert masked == "sk-4f1c9…MASKED…abcd"  # 前 8 = "sk-4f1c9",后 4 = "abcd"

    def test_short_key_variants_never_reveal_whole(self, fnd):
        assert fnd.mask_apikey("shortkey1234") == "shortkey…MASKED…"
        assert fnd.mask_apikey("shrt1234") == "sh…MASKED…"

    def test_fingerprint_stable_and_discriminating(self, fnd):
        assert fnd.key_fingerprint(KEY_OPENAI) == fnd.key_fingerprint(KEY_OPENAI)
        assert fnd.key_fingerprint(KEY_OPENAI) != fnd.key_fingerprint(KEY_XAI)

    def test_redact_secrets_replaces_full_key(self, fnd):
        text = f"config uses {KEY_OPENAI} inline"
        redacted = fnd.redact_secrets(text, [KEY_OPENAI])
        assert KEY_OPENAI not in redacted
        assert fnd.mask_apikey(KEY_OPENAI) in redacted


class TestFindingItems:
    def make_item(self, fnd, *, apikey=KEY_XAI, provider="xai", context=None):
        return fnd.build_finding_item(
            apikey=apikey,
            provider=provider,
            source_url="https://github.com/example/repo/blob/abc/config.env",
            apiurl="https://api.x.ai/v1",
            source_type="code_snapshot",
            matched_by="joint",
            variable=None,
            file_path="config.env",
            context_excerpt=context,
        )

    def test_item_shape_is_pipeline_ready(self, fnd):
        item = self.make_item(fnd)
        assert item["url"] and item["title"] and item["source"] == "credhunter"
        assert item["provider"] == "xai"
        assert item["apikey_masked"] == fnd.mask_apikey(KEY_XAI)
        assert item["key_fingerprint"] == fnd.key_fingerprint(KEY_XAI)
        assert item["source_type"] == "code_snapshot"

    def test_full_key_never_appears_anywhere_in_item(self, fnd):
        context = f"export XAI_KEY={KEY_XAI}  # leaked in config"
        item = self.make_item(fnd, context=context)
        assert fnd.find_full_key_leak(item, KEY_XAI) == [], "Q9 红线:全文密钥永不进 item"
        assert fnd.mask_apikey(KEY_XAI) in item["content"]

    def test_missing_source_url_rejected(self, fnd):
        with pytest.raises(ValueError, match="source_url"):
            fnd.build_finding_item(apikey=KEY_XAI, provider="xai", source_url="  ")

    def test_dedup_key_template_is_field_composite_without_title(self, fnd):
        assert "{title}" not in fnd.DEDUP_KEY_TEMPLATE
        assert "{provider}" in fnd.DEDUP_KEY_TEMPLATE and "{key_fingerprint}" in fnd.DEDUP_KEY_TEMPLATE
        item = self.make_item(fnd)
        rendered = DedupRegistry.make_key(fnd.DEDUP_KEY_TEMPLATE, item)
        assert rendered == f"xai-{fnd.key_fingerprint(KEY_XAI)}"

    def test_dedup_key_helper_matches_template_semantics(self, fnd):
        assert fnd.dedup_key("xai", KEY_XAI) == f"xai-{fnd.key_fingerprint(KEY_XAI)}"


# ---------------------------------------------------------------------------
# 契约五:适配器双入口(run / fetch)+ 显式空态
# ---------------------------------------------------------------------------


class TestAdapterRun:
    def test_success_payload_is_masked_only(self, adapter):
        payload = adapter.run(
            documents=[
                {
                    "text": f"XAI_API_KEY={KEY_XAI}",
                    "url": "https://github.com/example/repo/blob/abc/config.env",
                    "source_type": "code_snapshot",
                    "file_path": "config.env",
                }
            ]
        )
        assert payload["plugin"] == "myia-credhunter"
        assert payload["mode"] == "in_process"
        assert payload["status"] == "success"
        assert payload["provides"] == ["credhunt", "credcheck", "exposure"]
        assert payload["library"]["packs"] == 20
        assert payload["library"]["specs"] == 25
        assert payload["library"]["github_query_pool"] > 0
        assert len(payload["findings"]) == 1
        item = payload["findings"][0]
        assert item["provider"] == "xai"
        # Q9 红线:整个 payload(含嵌套 findings/content)不得出现全文密钥
        assert adapter.findings.find_full_key_leak(payload, KEY_XAI) == []
        assert json.loads(json.dumps(payload))["status"] == "success", "payload 必须 JSON 可序列化"

    def test_empty_documents_is_explicit_empty_state(self, adapter):
        payload = adapter.run(documents=[], github_token=None)
        assert payload["status"] == "empty"
        assert payload["findings"] == []
        assert payload["lanes"]["credhunt"] == "credential_missing"

    def test_lane_states_without_credentials_are_explicit(self, adapter):
        payload = adapter.run(documents=[], github_token=None, fofa_key=None, shodan_key=None)
        assert payload["lanes"] == {
            "credhunt": "credential_missing",
            "exposure_fofa": "credential_missing",
            "exposure_shodan": "credential_missing",
            "credcheck": "scheduled",
        }

    def test_lane_with_credential_reports_scheduled(self, adapter):
        payload = adapter.run(documents=[], github_token="host-injected-placeholder")
        assert payload["lanes"]["credhunt"] == "scheduled"

    def test_invalid_document_raises_structured_error(self, adapter):
        with pytest.raises(adapter.CredhunterError) as exc_info:
            adapter.run(documents=[{"text": "sk-4f1c9d2e8b7a6305abcd"}])  # 缺 url
        assert exc_info.value.code == "invalid_document"
        assert exc_info.value.to_dict()["code"] == "invalid_document"

    def test_broken_data_file_maps_to_structured_error(self, adapter, tmp_path, monkeypatch):
        # 指向空目录:数据文件缺失 → PackDataError → 结构化 CredhunterError
        monkeypatch.setattr(adapter.packs, "PACKS_DATA_FILE", tmp_path / "missing.yaml")
        with pytest.raises(adapter.CredhunterError) as exc_info:
            adapter.run(documents=[])
        assert exc_info.value.code == "packs_file_missing"

    def test_overexposed_key_dropped_across_documents(self, adapter):
        documents = [
            {
                "text": f"XAI_API_KEY={KEY_XAI}",
                "url": f"https://github.com/example/repo-{index}/blob/abc/config.env",
            }
            for index in range(6)
        ]
        payload = adapter.run(documents=documents)
        assert payload["findings"] == [], "同一 key 出现于 >5 个位置按教程/蜜罐键剔除"
        assert payload["counts"]["overexposed_dropped"] == 1

    def test_same_key_two_locations_kept_with_same_dedup_fields(self, adapter):
        documents = [
            {"text": f"XAI_API_KEY={KEY_XAI}", "url": f"https://github.com/example/repo-{index}/blob/abc/.env"}
            for index in range(2)
        ]
        payload = adapter.run(documents=documents)
        assert len(payload["findings"]) == 2
        fingerprints = {item["key_fingerprint"] for item in payload["findings"]}
        assert fingerprints == {adapter.findings.key_fingerprint(KEY_XAI)}, "同 key 同指纹,下游 dedup 收敛"


class TestAdapterFetch:
    def test_async_fetch_returns_items_list(self, adapter):
        documents = [
            {
                "text": f"const config = {{ key: '{KEY_ANTHROPIC}' }};",
                "url": "https://github.com/example/repo/commit/abc",
                "source_type": "commit_message",
            }
        ]
        items = asyncio.run(adapter.fetch(documents=documents))
        assert isinstance(items, list) and len(items) == 1
        assert items[0]["provider"] == "anthropic"
        assert items[0]["source_type"] == "commit_message"

    def test_async_fetch_empty_documents(self, adapter):
        assert asyncio.run(adapter.fetch(documents=None)) == []
