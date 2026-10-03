"""10-03-aipocket-fusion 接线段契约测试(engine: credhunter + CLI 三子命令)。

接线段把 P1a 落地的插件包(指纹库 + 三 lane 模块)接进 MYIA 宿主,本文件
钉住四组接线契约(行为规格 = research/behavior-specs/*.md,集成落点 =
research/myia-integration-facts.md):

1. **引擎三锁**:schema ``ENGINES``/``EngineName`` 词表收录 ``credhunter``、
   ``ENGINE_REGISTRY`` 注册、**AUTO_CHAIN 恒不含**(auto 永不路过;显式选择
   = 单级链,不参与浏览器降级);
2. **引擎三 lane**(经 ``fetch_source`` 全链):scan(documents→掩码 items)、
   credhunt(MockTransport 全链:env: 引用 token 解析、查询注入、checkpoint
   落盘续传;无 token = 显式空态「该源不启用」,ghhunt.md §2 上游同款,
   与 exposure 的 AC6 空态同口径)、exposure(无 key 显式空态 AC6);
3. **结构化降级铁律**:适配器缺失 → ``EngineFailure``(
   ``credhunter_adapter_missing``),品类其余源不受影响;未知 lane /
   配了 extract 节均为可见的结构化失败,不是静默空结果;
4. **CLI 三子命令**:``--json`` 下 stdout 恒单份 JSON 可 ``json.load``、
   退出码族 0/1/2/3 对齐、凭据实参裸值与 ``env:`` 引用双形态、引用解析
   失败 = 配置错误退 1。

测试纪律:零真实网络(注入 transport)、零真实凭据(全部为人工合成脱敏
键,不对应任何真实账号)、零真实钥匙串(env: 引用 + 临时文件)。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import httpx
import pytest

from myia import schema
from myia.cli import main
from myia.engines.credhunter import import_credhunter_adapter
from myia.engines.fetch_base import FetchContext
from myia.engines.registry import AUTO_CHAIN, ENGINE_REGISTRY, auto_degrade, fetch_source
from myia.schema import load_category
from myia.store import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 合成脱敏 fixture(人工构造,不对应任何真实账号;避开噪声子串)。
# ---------------------------------------------------------------------------
KEY_XAI = "xai-Hs5vBn8mKj2wQr7tYc3f"
KEY_OPENAI = "sk-4f1c9d2e8b7a6305abcd"


def _category_data(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "wiring-demo",
        "name": "接线演示",
        "schedule": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "sources": [source],
    }


def _run_source(source: dict[str, Any], client: httpx.AsyncClient | None = None):
    """走真实装载入口 + 真实 fetch_source 链跑一个源。"""
    config = load_category(_category_data(source))
    owned = client is None
    client = client or httpx.AsyncClient()
    store = SQLiteStore(":memory:")
    try:
        context = FetchContext(client=client, store=store)
        outcome = asyncio.run(fetch_source(config.sources[0], context))
    finally:
        store.close()
        if owned:
            asyncio.run(client.aclose())
    return outcome


# ---------------------------------------------------------------------------
# 契约一:引擎三锁(schema / registry / auto 链)
# ---------------------------------------------------------------------------


class TestEngineLocks:
    def test_schema_vocabulary_and_registry_carry_credhunter(self):
        assert "credhunter" in schema.ENGINES
        assert "credhunter" in ENGINE_REGISTRY

    def test_auto_chain_never_contains_credhunter(self):
        """链外源引擎:auto 降级链恒不含(显式选择才生效)。"""
        assert "credhunter" not in AUTO_CHAIN
        assert "credhunter" not in auto_degrade("auto")
        # 链上任何一环的降级后缀也不含(它不在链上,不存在「路过」)
        for rung in AUTO_CHAIN:
            assert "credhunter" not in auto_degrade(rung)

    def test_explicit_selection_is_single_rung_chain(self):
        assert auto_degrade("credhunter") == ["credhunter"]

    def test_unknown_engine_still_raises_keyerror(self):
        with pytest.raises(KeyError):
            auto_degrade("definitely_not_an_engine")


# ---------------------------------------------------------------------------
# 契约二:引擎三 lane(fetch_source 全链)
# ---------------------------------------------------------------------------


class TestScanLane:
    def test_documents_produce_masked_pipeline_items(self):
        outcome = _run_source(
            {
                "name": "triage",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "engine_options": {
                    "credhunter": {
                        "lane": "scan",
                        "documents": [
                            {
                                "text": f"XAI_API_KEY={KEY_XAI}",
                                "url": "https://github.com/octo/demo/blob/abc/config.env",
                                "source_type": "manual",
                            }
                        ],
                    }
                },
            }
        )
        assert outcome.engine == "credhunter"
        assert len(outcome.items) == 1
        item = outcome.items[0]
        assert item["provider"] == "xai"
        assert KEY_XAI not in json.dumps(item), "Q9:全文密钥永不进 item"
        assert item["apikey_masked"].startswith("xai-Hs5v")

    def test_documents_absent_is_explicit_skip_not_failure(self):
        outcome = _run_source(
            {"name": "triage-empty", "engine": "credhunter", "url": "https://api.github.com"}
        )
        assert outcome.engine == "credhunter"  # 引擎成功(0 条合理)
        assert outcome.items == []
        assert outcome.skipped and outcome.skip_reason == "documents_empty"
        assert outcome.failures == []


class TestCredhuntLane:
    @pytest.fixture()
    def github_mock_client(self) -> httpx.AsyncClient:
        """单查询 GitHub 双泳道 mock:text_matches 命中 + blob 替换文本。

        形状照 ghhunt 测试 fixture(REST 文档形状,内容全合成)。
        """
        code_entry = {
            "name": ".env",
            "path": "config/.env",  # basename 以 .env 开头:永不判噪
            "sha": "bl0b11",
            "html_url": "https://github.com/octo/demo/blob/bl0b11/config/.env",
            "repository": {"full_name": "octo/demo", "private": False},
            "text_matches": [
                {"fragment": f"XAI_API_KEY={KEY_XAI}", "object_url": "https://github.com/octo/demo"}
            ],
        }
        blob = {
            "content": base64.b64encode(f"XAI_API_KEY={KEY_XAI}\n".encode()).decode("ascii"),
            "encoding": "base64",
            "size": len(KEY_XAI) + 1,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/search/code":
                return httpx.Response(200, json={"total_count": 1, "items": [code_entry]})
            if path == "/search/commits":
                return httpx.Response(200, json={"total_count": 0, "items": []})
            if path.startswith("/repos/octo/demo/git/blobs/"):
                return httpx.Response(200, json=blob)
            return httpx.Response(200, json={"resources": {"search": {"limit": 30, "remaining": 30}}})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_credhunt_lane_end_to_end_masked(
        self, github_mock_client, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("WIRING_GH_TOKEN", "gh-synthetic-token")
        checkpoint_file = tmp_path / "checkpoint.json"
        outcome = _run_source(
            {
                "name": "github-hunt",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "engine_options": {
                    "credhunter": {
                        "lane": "credhunt",
                        "github_tokens": ["env:WIRING_GH_TOKEN"],
                        "queries": ["xai filename:.env"],
                        "checkpoint_file": str(checkpoint_file),
                    }
                },
            },
            client=github_mock_client,
        )
        assert outcome.engine == "credhunter"
        assert outcome.failures == []
        assert outcome.items, "mock 命中至少 1 条 finding"
        dumped = json.dumps(outcome.items)
        assert KEY_XAI not in dumped, "Q9:全文密钥永不进 items"
        assert KEY_XAI not in checkpoint_file.read_text(encoding="utf-8")
        # checkpoint 落盘:code 泳道页游标可续传
        checkpoint = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        assert isinstance(checkpoint.get("code_pages"), dict)

    def test_no_tokens_is_explicit_empty_state(self):
        """无 token = 该源不启用(ghhunt.md §2:上游根本不注册该源)——
        引擎面显式空态:可见 skip、零失败、零条目,写好引用即恢复产出;
        「显式直跑」的 CLI 面才是 tokens_missing 配置错误退 1(见
        TestCliCommands.test_credhunt_no_token_is_config_error_exit_one)。"""
        outcome = _run_source(
            {
                "name": "github-hunt",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "engine_options": {"credhunter": {"lane": "credhunt"}},
            }
        )
        assert outcome.engine == "credhunter"  # 引擎成功:空态不是失败
        assert outcome.items == []
        assert outcome.skipped and outcome.skip_reason == "credential_missing"
        assert outcome.failures == []

    def test_unresolvable_token_ref_is_empty_state_not_failure(self):
        """github_tokens 配了引用但钥匙串没写 = 无可用 token(空态 + warning
        留痕),不是源失败——与 exposure lane 的降级口径一致(AC6)。零真实
        钥匙串:注入内存后端(未写该键)。"""
        from myia.secrets import InMemoryKeychainBackend

        config = load_category(
            _category_data(
                {
                    "name": "github-hunt",
                    "engine": "credhunter",
                    "url": "https://api.github.com",
                    "engine_options": {
                        "credhunter": {
                            "lane": "credhunt",
                            "github_tokens": ["keychain:myia/credhunter/github-token"],
                        }
                    },
                }
            )
        )
        store = SQLiteStore(":memory:")
        client = httpx.AsyncClient()
        try:
            context = FetchContext(
                client=client, store=store, keychain_backend=InMemoryKeychainBackend()
            )
            outcome = asyncio.run(fetch_source(config.sources[0], context))
        finally:
            store.close()
            asyncio.run(client.aclose())
        assert outcome.engine == "credhunter"  # 引擎成功:显式空态不是失败
        assert outcome.items == []
        assert outcome.skipped and outcome.skip_reason == "credential_missing"
        assert outcome.failures == []

    def test_one_broken_ref_does_not_sink_the_whole_pool(
        self, github_mock_client, monkeypatch
    ):
        """token 池语义(ghhunt.md §2):坏一个引用不废整池——解析失败的
        引用留 warning 后跳过,可解析的照常参与,引擎继续猎取而非空态。"""
        from myia.secrets import InMemoryKeychainBackend

        backend = InMemoryKeychainBackend()
        backend.set_password("myia", "myia/credhunter/github-token", "gh-synthetic-token")
        config = load_category(
            _category_data(
                {
                    "name": "github-hunt",
                    "engine": "credhunter",
                    "url": "https://api.github.com",
                    "engine_options": {
                        "credhunter": {
                            "lane": "credhunt",
                            "github_tokens": [
                                "keychain:myia/credhunter/github-token",
                                "keychain:myia/credhunter/github-token-2",  # 未写 → 跳过
                            ],
                            "queries": ["xai filename:.env"],
                        }
                    },
                }
            )
        )
        store = SQLiteStore(":memory:")
        try:
            context = FetchContext(
                client=github_mock_client, store=store, keychain_backend=backend
            )
            outcome = asyncio.run(fetch_source(config.sources[0], context))
        finally:
            store.close()
        assert outcome.engine == "credhunter"
        assert not outcome.skipped, "池里还有活 token:不该落空态"
        assert outcome.failures == []
        assert outcome.items, "活 token 驱动 mock 命中 ≥1 条 finding"


class TestExposureLane:
    def test_no_keys_is_explicit_empty_state(self):
        """AC6:FOFA/Shodan 双 lane 无凭据 = 显式空态(可见 skip,零失败)。"""
        outcome = _run_source(
            {
                "name": "exposure-scan",
                "engine": "credhunter",
                "url": "https://fofa.info",
                "engine_options": {"credhunter": {"lane": "exposure"}},
            }
        )
        assert outcome.engine == "credhunter"
        assert outcome.items == []
        assert outcome.skipped and outcome.skip_reason == "credential_missing"
        assert outcome.failures == []

    def test_unresolvable_keychain_ref_degrades_not_fails(self):
        """PRD P3 口径:引用配置了但钥匙串没写 = 源级结构化降级(显式空态
        + 可见 skip),不是源失败——配置错误由 warning 日志与 doctor 文案
        呈现,不静默也不拦品类。零真实钥匙串:注入内存后端(未写该键)。"""
        from myia.secrets import InMemoryKeychainBackend

        config = load_category(
            _category_data(
                {
                    "name": "exposure-scan",
                    "engine": "credhunter",
                    "url": "https://fofa.info",
                    "engine_options": {
                        "credhunter": {
                            "lane": "exposure",
                            "fofa_apikey": "keychain:myia/credhunter/fofa-key",
                            "shodan_apikey": "keychain:myia/credhunter/shodan-key",
                        }
                    },
                }
            )
        )
        store = SQLiteStore(":memory:")
        client = httpx.AsyncClient()
        try:
            context = FetchContext(
                client=client, store=store, keychain_backend=InMemoryKeychainBackend()
            )
            outcome = asyncio.run(fetch_source(config.sources[0], context))
        finally:
            store.close()
            asyncio.run(client.aclose())
        assert outcome.engine == "credhunter"  # 引擎成功:显式空态不是失败
        assert outcome.items == []
        assert outcome.skipped and outcome.skip_reason == "credential_missing"
        assert outcome.failures == []


# ---------------------------------------------------------------------------
# 契约三:结构化降级铁律(适配器缺失 / 配置坏)
# ---------------------------------------------------------------------------


class TestStructuredDegradation:
    def test_missing_adapter_degrades_to_engine_failure(self, tmp_path):
        outcome = _run_source(
            {
                "name": "triage",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "engine_options": {
                    "credhunter": {"lane": "scan", "plugins_dir": str(tmp_path)}
                },
            }
        )
        assert outcome.engine is None
        assert [failure.error_type for failure in outcome.failures] == [
            "credhunter_adapter_missing"
        ]

    def test_unknown_lane_is_visible_failure(self):
        outcome = _run_source(
            {
                "name": "triage",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "engine_options": {"credhunter": {"lane": "bogus"}},
            }
        )
        assert [failure.error_type for failure in outcome.failures] == ["invalid_lane"]

    def test_extract_node_rejected_structurally(self):
        """credhunter 引擎自装 items,extract 节配了即结构化拒(可见的错配,
        不是静默忽略)——auto 链上的引擎无此问题(它们消费 extract)。"""
        outcome = _run_source(
            {
                "name": "triage",
                "engine": "credhunter",
                "url": "https://api.github.com",
                "extract": {"type": "json_path", "fields": {"url": "$.url"}},
            }
        )
        assert [failure.error_type for failure in outcome.failures] == ["extract_unsupported"]


# ---------------------------------------------------------------------------
# 契约四:CLI 三子命令(--json 恒 JSON / 退出码族 / 凭据实参形态)
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> tuple[int, Any]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class TestCliCommands:
    def test_exposure_no_key_json_stdout_and_exit_zero(self):
        code, out = _run_cli(["exposure", "--json"])
        payload = json.loads(out)  # --json 下 stdout 恒单份可 load 的 JSON
        assert code == 0  # 无 key 显式空态不是错误(AC6)
        assert payload["status"] == "empty"
        assert payload["lanes"]["fofa"]["status"] == "credential_missing"
        assert payload["lanes"]["shodan"]["status"] == "credential_missing"

    def test_exposure_human_face_exit_zero(self):
        code, out = _run_cli(["exposure"])
        assert code == 0
        assert "exposure" in out and "empty" in out

    def test_credhunt_no_token_is_config_error_exit_one(self):
        code, out = _run_cli(["credhunt", "--json"])
        payload = json.loads(out)
        assert code == 1
        assert payload["error"] == "tokens_missing"

    def test_credcheck_rejected_verdict_is_data_not_error(self):
        """unknown 供应商 + 空 apiurl →「no_api_url」判死:零网络、零探测,
        rejected 是**结论**不是失败 → 退出码 0。"""
        code, out = _run_cli(
            ["credcheck", "--apikey", "totally-unattributable-key-shape", "--json"]
        )
        payload = json.loads(out)
        assert code == 0
        assert payload["status"] == "success"
        assert payload["counts"]["rejected"] == 1
        assert payload["results"][0]["error"] == "no_api_url"

    def test_credcheck_env_ref_resolution(self, monkeypatch):
        monkeypatch.setenv("WIRING_CHECK_KEY", "totally-unattributable-key-shape")
        code, out = _run_cli(["credcheck", "--apikey", "env:WIRING_CHECK_KEY", "--json"])
        payload = json.loads(out)
        assert code == 0
        assert payload["counts"]["records"] == 1  # env: 引用被解析成值参与验证

    def test_credcheck_broken_ref_is_config_error_exit_one(self):
        """写成 keychain: 引用但名空间不合法 → 解析失败 = 配置错误退 1,
        不把引用串本身当密钥用。"""
        code, out = _run_cli(["credcheck", "--apikey", "keychain:not-canonical", "--json"])
        payload = json.loads(out)
        assert code == 1
        assert payload["error"] == "invalid_secret_name"

    def test_credhunt_checkpoint_roundtrip_via_cli(self, tmp_path, monkeypatch):
        """--checkpoint-file:坏/缺文件从头起(不拦),跑完写回。无网络环境下
        单查询失败也必须落 checkpoint 文件(degraded 仍是完整一轮)。"""
        monkeypatch.setenv("WIRING_GH_TOKEN", "gh-synthetic-token")
        checkpoint = tmp_path / "cp.json"
        code, out = _run_cli(
            [
                "credhunt",
                "--github-token", "env:WIRING_GH_TOKEN",
                "--query", "xai filename:.env",
                "--checkpoint-file", str(checkpoint),
                "--json",
            ]
        )
        payload = json.loads(out)
        # 沙箱/CI 无论有没有网:要么命中(success/partial),要么查询全败
        # (degraded → 退 2);stdout 恒 JSON、checkpoint 恒落盘。
        assert payload["status"] in {"success", "partial", "degraded", "empty"}
        assert code in (0, 2, 3)
        assert json.loads(checkpoint.read_text(encoding="utf-8"))["code_pages"] == {}


class TestAdapterContract:
    """适配器对外入口面(接线后三 lane 双面)的存在性与掩码红线。"""

    @pytest.fixture()
    def adapter(self):
        return import_credhunter_adapter(REPO_ROOT / "plugins")

    def test_dual_faces_present(self, adapter):
        for name in ("fetch", "fetch_hunt", "fetch_exposure", "run", "run_credhunt", "run_credcheck", "run_exposure"):
            assert callable(getattr(adapter, name, None)), f"适配器缺少入口 {name}"

    def test_run_credhunt_without_tokens_structured(self, adapter):
        with pytest.raises(adapter.CredhunterError) as exc_info:
            adapter.run_credhunt(github_tokens=[])
        assert exc_info.value.code == "tokens_missing"

    def test_run_credcheck_masks_everything(self, adapter):
        payload = adapter.run_credcheck(
            records=[{"apikey": "totally-unattributable-key-shape", "apiurl": ""}]
        )
        dumped = json.dumps(payload)
        assert "totally-unattributable-key-shape" not in dumped, "Q9:密钥全文永不进 payload"
        assert payload["counts"]["rejected"] == 1

    def test_run_exposure_no_keys_explicit_empty(self, adapter):
        payload = adapter.run_exposure(fofa_key=None, shodan_key=None)
        assert payload["status"] == "empty"
        assert payload["lanes"]["fofa"]["status"] == "credential_missing"
