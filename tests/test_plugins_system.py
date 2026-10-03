"""插件市场基建(v0.3):plugin.yaml 规范、版本矩阵、装卸 CLI 与铁律.

三条被钉死的铁律(PRD 10-01-v03-plugin-market / security-baseline):

1. **任何 plugin 装不上 / 配置坏 / remote 不可达,核心流水线照常跑通** ——
   加载失败降级为结构化 warning finding,run 退出码与输出不受影响;
2. **remote token 只走 ``keychain:`` 引用**(规范名空间 myia/<scope>/<name>),
   明文/env: 引用一律加载期拒;
3. **plugin.yaml 校验 fail-fast 结构化**(字段路径 + 错误类 + 中文原因),
   版本矩阵不兼容在 install 期结构化拒绝。

测试纪律(与全仓一致):零真实网络(httpx.MockTransport / 注入
client_factory)、零真实钥匙串(InMemoryKeychainBackend)、零共享状态
(每测独立 tmp_path);异步用 asyncio.run 直驱(仓库未配 pytest-asyncio)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

import shishi.cli as cli_module
from shishi.cli import EXIT_CONFIG_ERROR, EXIT_OK, main
from shishi.pipeline import ChannelPushReport, RunResult, StageReport
from shishi.plugins import (
    PluginStoreError,
    VersionRange,
    VersionSpecError,
    check_category_plugin,
    check_remote_modes,
)
from shishi.plugins.installed import InstalledPluginStore, PluginFinding
from shishi.plugins.manifest import load_manifest, load_manifest_file
from shishi.schema import LoadError, load_category, load_category_file
from shishi.secrets import InMemoryKeychainBackend
from shishi.secrets import reset_backend as reset_keychain_backend
from shishi.secrets import set_backend as set_keychain_backend

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures & helpers (每测独立,零共享状态)
# ---------------------------------------------------------------------------

VALID_MANIFEST_YAML = """
id: myia-monitor
name: 变更监控(changedetection.io)
version: 1.0.0
compatible: ">=0.0.1,<0.1"
requires: docker
provides: [changedetection]
modes:
  local:
    compose: docker-compose.yml
    install: docker compose up -d
  remote:
    endpoint: https://my-monitor.example.com
    token: keychain:myia/monitor/token
install:
  source: https://github.com/myia-official/myia-monitor.git
"""

CATEGORY_WITH_PLUGIN_YAML = """
id: demo
name: 演示品类
schedule: "0 9 * * *"
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    extract:
      type: json_path
      fields:
        title: "$[*].title"
        url: "$[*].url"
push:
  - channel: stdout
plugin:
  id: myia-monitor
  requires: docker
  modes:
    remote:
      endpoint: https://my-monitor.example.com
      token: keychain:myia/monitor/token
"""

#: 兼容当前 myia 版本序列的标准窗口(版本序列归零 10-03-tag-release 决议 9:
#: 与根 pyproject 依赖窗同款;升入 0.1 系列时随五处版本源同步)。
ANY_MYIA = ">=0.0.1,<0.1"


def write_plugin_dir(tmp_path: Path, name: str = "myia-monitor", manifest_text: str = VALID_MANIFEST_YAML) -> Path:
    """一个带 manifest/README/compose 的完整插件来源目录(装卸测试的夹具)。"""
    source = tmp_path / "source" / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "plugin.yaml").write_text(manifest_text, encoding="utf-8")
    (source / "README.md").write_text("# myia-monitor\n", encoding="utf-8")
    (source / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return source


def write_category_yaml(tmp_path: Path, text: str = CATEGORY_WITH_PLUGIN_YAML, name: str = "demo.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def finding_codes(findings: list[PluginFinding]) -> list[str]:
    return [finding.code for finding in findings]


def make_run_result() -> RunResult:
    """Canned RunResult for the fake pipeline (test_cli.py 同款最小形状)."""
    return RunResult(
        category="demo",
        category_name="演示品类",
        run_id=1,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        status="success",
        stages=[StageReport(name=name, status="ok") for name in ("fetch", "classify", "dedup", "analyze", "push")],
        sources=[],
        items=[],
        pushes=[ChannelPushReport(channel="stdout")],
    )


class FakePipeline:
    """Records construction + run;核心 run 被换壳,零 I/O(test_cli.py 同款)."""

    last_instance: FakePipeline | None = None

    def __init__(self, config: Any, **kwargs: Any) -> None:
        self.config = config
        self.kwargs = kwargs
        self.run_calls: list[dict[str, Any]] = []
        self.closed = False
        self.result = make_run_result()
        FakePipeline.last_instance = self

    async def run(self, *, dry_run: bool = False) -> RunResult:
        self.run_calls.append({"dry_run": dry_run})
        return replace(self.result, dry_run=dry_run)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> type[FakePipeline]:
    """只换掉 Pipeline,加载走真 loader(plugin: 节要真实过 schema)。"""
    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)
    return FakePipeline


@pytest.fixture()
def plugin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把安装根指进 tmp_path(自检/CLI 都经 MYIA_PLUGIN_DIR 解析,零碰真目录)。"""
    root = tmp_path / "installed"
    monkeypatch.setenv("MYIA_PLUGIN_DIR", str(root))
    return root


@pytest.fixture()
def keychain_backend():
    """注入内存钥匙串(零真实钥匙串触碰,用后复位不泄漏)."""
    backend = InMemoryKeychainBackend()
    set_keychain_backend(backend)
    yield backend
    reset_keychain_backend()


@pytest.fixture()
def probe_client_factory(monkeypatch: pytest.MonkeyPatch):
    """注入 MockTransport 客户端工厂(端点探测零真实网络)."""

    def install(handler: Any) -> Any:
        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(cli_module, "_build_async_client", factory)
        return factory

    return install


# ---------------------------------------------------------------------------
# 版本矩阵(版本矩阵兼容范围检查单测 —— PRD 验收项)
# ---------------------------------------------------------------------------


class TestVersionMatrix:
    def test_version_range_contains_boundary_values(self):
        """>=0.3,<0.5:0.3.0/0.4.9 在内,0.2.9/0.5.0 在外(边界逐个钉住)."""
        spec = VersionRange(">=0.3,<0.5")
        assert spec.contains("0.3.0")
        assert spec.contains("0.4.9")
        assert not spec.contains("0.2.9")
        assert not spec.contains("0.5.0")

    def test_version_range_pads_missing_segments(self):
        """缺位补 0:==0.3 等价 ==0.3.0。"""
        assert VersionRange("==0.3").contains("0.3.0")
        assert not VersionRange("==0.3").contains("0.3.1")

    def test_version_range_all_constraints_are_anded(self):
        """>=0.2,<=0.4 同时约束:0.3.5 在内,0.4.0 被 <0.3 之外的组合挡住。"""
        assert VersionRange(">=0.2,<=0.4").contains("0.3.5")
        assert not VersionRange(">=0.2,<0.3").contains("0.4.0")

    def test_version_range_reports_all_bad_constraints_at_once(self):
        """一次性报告全部坏约束(AI 生成的 spec 一次改完)。"""
        with pytest.raises(VersionSpecError) as excinfo:
            VersionRange(">=0.3,banana,<apple")
        message = str(excinfo.value)
        assert "banana" in message and "apple" in message
        assert "2 处非法" in message

    def test_version_range_rejects_empty_spec(self):
        with pytest.raises(VersionSpecError) as excinfo:
            VersionRange("  ")
        assert excinfo.value.code == "invalid_version_range"


# ---------------------------------------------------------------------------
# plugin.yaml manifest 规范校验(fail-fast 结构化错误)
# ---------------------------------------------------------------------------


class TestManifestSchema:
    def test_manifest_roundtrip_all_fields(self):
        """全字段往返:requires 裸字符串归一为列表,双模式完整保留。"""
        manifest = load_manifest(yaml.safe_load(VALID_MANIFEST_YAML))
        assert manifest.id == "myia-monitor"
        assert manifest.version == "1.0.0"
        assert manifest.compatible == ">=0.0.1,<0.1"
        assert manifest.requires == ["docker"]
        assert manifest.provides == ["changedetection"]
        assert manifest.modes.local.install == "docker compose up -d"
        assert manifest.modes.remote.endpoint == "https://my-monitor.example.com"
        assert manifest.modes.remote.token == "keychain:myia/monitor/token"
        assert manifest.install.source.endswith(".git")

    def test_manifest_rejects_unknown_field(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["bogus"] = True
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "unknown_field"
        assert detail.path == "$.bogus"

    def test_manifest_rejects_bad_id(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["id"] = "MYIA-Monitor!"
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "invalid_plugin_id"

    def test_manifest_rejects_non_semver_version(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["version"] = "v1"
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "invalid_semver"

    def test_manifest_rejects_invalid_version_range(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["compatible"] = "banana"
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "invalid_version_range"

    def test_manifest_normalizes_bare_string_requires(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        assert load_manifest(data).requires == ["docker"]

    def test_manifest_rejects_unknown_requires_token(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["requires"] = ["docker", "quantum_computer"]
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "unknown_requires_token"
        assert "docker" in detail.message

    def test_manifest_rejects_duplicate_provides(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["provides"] = ["changedetection", "changedetection"]
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "duplicate_provides"

    def test_manifest_requires_at_least_one_mode(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["modes"] = {}
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "missing_plugin_mode"

    def test_manifest_rejects_local_mode_without_content(self):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["modes"] = {"local": {}}
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "missing_local_mode_content"

    @pytest.mark.parametrize(
        ("token", "code"),
        [
            ("sk-live-plaintext-token", "credential_plaintext"),
            ("env:MYIA_MONITOR_TOKEN", "plugin_token_requires_keychain"),
            ("keychain:monitor_token", "invalid_secret_name"),
        ],
        ids=["plaintext", "env-ref", "non-canonical-keychain"],
    )
    def test_manifest_token_must_be_canonical_keychain_ref(self, token: str, code: str):
        """铁律:remote token 只走 keychain:myia/<scope>/<name>,其余全拒。"""
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["modes"]["remote"]["token"] = token
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == code

    @pytest.mark.parametrize("endpoint", ["ftp://x.example.com", "my-monitor.example.com", ""])
    def test_manifest_rejects_non_http_endpoint(self, endpoint: str):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["modes"]["remote"]["endpoint"] = endpoint
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        assert excinfo.value.errors[0].error_type == "invalid_endpoint"

    def test_manifest_tier_defaults_to_desktop(self):
        """v1.1 分级:旧包不声明 tier → 缺省 desktop(向后兼容)."""
        manifest = load_manifest(yaml.safe_load(VALID_MANIFEST_YAML))
        assert manifest.tier == "desktop"

    @pytest.mark.parametrize("tier", ["desktop", "remote", "server-only"])
    def test_manifest_accepts_closed_tier_vocabulary(self, tier: str):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["tier"] = tier
        assert load_manifest(data).tier == tier

    @pytest.mark.parametrize("tier", ["Desktop", "server_only", "banana", ""])
    def test_manifest_rejects_unknown_tier_token(self, tier: str):
        data = yaml.safe_load(VALID_MANIFEST_YAML)
        data["tier"] = tier
        with pytest.raises(LoadError) as excinfo:
            load_manifest(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "invalid_tier"
        assert "server-only" in detail.message

    def test_installed_entry_payload_exposes_tier(self, tmp_path: Path, plugin_root: Path):
        """`plugin list --json` 的条目带 tier(v1.1 分级展示契约)."""
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        (entry,) = store.entries()
        assert entry.to_dict()["tier"] == "desktop"

    def test_manifest_file_load_roundtrip(self, tmp_path: Path):
        source = write_plugin_dir(tmp_path)
        manifest = load_manifest_file(source / "plugin.yaml")
        assert manifest.id == "myia-monitor"
        assert manifest.modes.local.compose == "docker-compose.yml"


# ---------------------------------------------------------------------------
# 已安装插件仓:装卸与扫描(装卸 fail-fast,扫描零异常)
# ---------------------------------------------------------------------------


class TestInstalledStore:
    def test_entries_missing_root_is_empty_list(self, tmp_path: Path):
        assert InstalledPluginStore(tmp_path / "nope").entries() == []

    def test_install_copies_whole_plugin_dir_and_lists_it(self, tmp_path: Path, plugin_root: Path):
        source = write_plugin_dir(tmp_path)
        store = InstalledPluginStore(plugin_root)
        result = store.install(source)
        assert result["id"] == "myia-monitor"
        assert result["compatible_current"] is True
        installed = Path(result["path"])
        assert (installed / "plugin.yaml").is_file()
        assert (installed / "README.md").is_file()
        assert (installed / "docker-compose.yml").is_file()
        (entry,) = store.entries()
        assert entry.plugin_id == "myia-monitor"
        assert entry.compatible_current is True
        assert entry.findings == []

    def test_install_rejects_source_without_manifest(self, tmp_path: Path, plugin_root: Path):
        bare = tmp_path / "bare"
        bare.mkdir()
        with pytest.raises(PluginStoreError) as excinfo:
            InstalledPluginStore(plugin_root).install(bare)
        assert excinfo.value.code == "invalid_source"

    def test_install_rejects_broken_manifest_with_structured_errors(self, tmp_path: Path, plugin_root: Path):
        source = write_plugin_dir(tmp_path, manifest_text="id: myia-broken\nbanana: true\n")
        with pytest.raises(PluginStoreError) as excinfo:
            InstalledPluginStore(plugin_root).install(source)
        assert excinfo.value.code == "manifest_invalid"
        assert excinfo.value.errors, "manifest 校验明细必须结构化透出"
        banana = next(error for error in excinfo.value.errors if error["path"] == "$.banana")
        assert banana["error_type"] == "unknown_field"

    def test_install_rejects_incompatible_version_then_force_wins(self, tmp_path: Path, plugin_root: Path):
        source = write_plugin_dir(tmp_path, manifest_text=VALID_MANIFEST_YAML.replace('">=0.0.1,<0.1"', '">=99.0"'))
        store = InstalledPluginStore(plugin_root)
        with pytest.raises(PluginStoreError) as excinfo:
            store.install(source, current_version="0.1.0")
        assert excinfo.value.code == "incompatible_version"
        forced = store.install(source, force=True, current_version="0.1.0")
        assert forced["compatible_current"] is False
        assert forced["forced"] is True

    def test_install_rejects_duplicate_without_force_and_force_overwrites(self, tmp_path: Path, plugin_root: Path):
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        with pytest.raises(PluginStoreError) as excinfo:
            store.install(write_plugin_dir(tmp_path / "second"))
        assert excinfo.value.code == "already_installed"
        store.install(write_plugin_dir(tmp_path / "second"), force=True)
        (entry,) = store.entries()
        assert entry.plugin_id == "myia-monitor"

    def test_remove_removes_and_then_reports_not_installed(self, tmp_path: Path, plugin_root: Path):
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        assert store.remove("myia-monitor")["removed"] is True
        assert store.entries() == []
        with pytest.raises(PluginStoreError) as excinfo:
            store.remove("myia-monitor")
        assert excinfo.value.code == "not_installed"

    def test_remove_rejects_path_traversal_id(self, tmp_path: Path, plugin_root: Path):
        with pytest.raises(PluginStoreError) as excinfo:
            InstalledPluginStore(plugin_root).remove("../escape")
        assert excinfo.value.code == "invalid_plugin_id"

    def test_entries_broken_manifest_yields_structured_finding_not_exception(self, tmp_path: Path, plugin_root: Path):
        broken = plugin_root / "myia-broken"
        broken.mkdir(parents=True)
        (broken / "plugin.yaml").write_text("id: [unclosed\n", encoding="utf-8")
        (entry,) = InstalledPluginStore(plugin_root).entries()
        assert entry.manifest is None
        (finding,) = entry.findings
        assert finding.code == "manifest_invalid"
        assert finding.severity == "error"
        assert finding.detail is not None and finding.detail["errors"]

    def test_entries_missing_manifest_is_warning_finding(self, plugin_root: Path):
        stray = plugin_root / "myia-empty"
        stray.mkdir(parents=True)
        (entry,) = InstalledPluginStore(plugin_root).entries()
        assert entry.manifest is None
        assert finding_codes(entry.findings) == ["manifest_missing"]

    def test_entries_ignores_stray_files_and_hidden_dirs(self, plugin_root: Path):
        (plugin_root / ".hidden").mkdir(parents=True)
        (plugin_root / "notes.txt").write_text("hi", encoding="utf-8")
        assert InstalledPluginStore(plugin_root).entries() == []

    def test_entries_flags_incompatible_version_as_warning(self, tmp_path: Path, plugin_root: Path):
        store = InstalledPluginStore(plugin_root)
        store.install(
            write_plugin_dir(tmp_path, manifest_text=VALID_MANIFEST_YAML.replace('">=0.0.1,<0.1"', '">=99.0"')),
            force=True,
        )
        (entry,) = store.entries()
        assert entry.compatible_current is False
        assert finding_codes(entry.findings) == ["incompatible_version"]


# ---------------------------------------------------------------------------
# 铁律:plugin 装不上 / 配置坏 / remote 不可达,核心照常跑通
# ---------------------------------------------------------------------------


class TestIronLaw:
    def test_category_with_uninstalled_plugin_loads_with_warning_finding(self, tmp_path: Path, plugin_root: Path):
        """插件未装:品类照常加载,自检只产一条 warning 级 plugin_not_installed。"""
        config = load_category_file(write_category_yaml(tmp_path))
        assert config.plugin is not None and config.plugin.id == "myia-monitor"
        findings = check_category_plugin(config.plugin, InstalledPluginStore(plugin_root))
        assert finding_codes(findings) == ["plugin_not_installed"]
        assert all(finding.severity == "warning" for finding in findings)

    def test_run_succeeds_when_plugin_not_installed(
        self, tmp_path: Path, plugin_root: Path, fake_pipeline, capsys, keychain_backend
    ):
        """铁律:插件未装,myia run 无感 —— 退出码 0,FakePipeline 照常被构造并跑完。"""
        yaml_path = write_category_yaml(tmp_path)
        code = main(["run", str(yaml_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        assert payload["status"] == "success"
        assert FakePipeline.last_instance is not None and len(FakePipeline.last_instance.run_calls) == 1

    def test_run_succeeds_when_installed_manifest_is_broken(
        self, tmp_path: Path, plugin_root: Path, fake_pipeline, capsys, caplog, keychain_backend
    ):
        """铁律:已装目录的 manifest 坏,run 照常 0,findings 以 warning 落日志。"""
        broken = plugin_root / "myia-monitor"
        broken.mkdir(parents=True)
        (broken / "plugin.yaml").write_text("id: [unclosed\n", encoding="utf-8")
        config = load_category_file(write_category_yaml(tmp_path))
        findings = check_category_plugin(config.plugin, InstalledPluginStore(plugin_root))
        assert finding_codes(findings) == ["manifest_invalid"]
        assert all(finding.severity == "warning" for finding in findings)
        caplog.set_level(logging.WARNING)
        cli_module._selfcheck_category_plugin(config)  # 不抛即通过(降级语义)
        assert any("manifest_invalid" in record.getMessage() for record in caplog.records)

    def test_run_succeeds_with_fully_installed_plugin_and_no_findings(
        self, tmp_path: Path, plugin_root: Path, fake_pipeline, capsys, keychain_backend
    ):
        """已装 + 兼容 + token 在钥匙串:零 findings,run 照常 0(健康路径)。"""
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        keychain_backend.set_password("myia", "myia/monitor/token", "token-value")
        yaml_path = write_category_yaml(tmp_path)
        code = main(["run", str(yaml_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK and payload["status"] == "success"
        config = load_category_file(yaml_path)
        assert check_category_plugin(config.plugin, store, backend=keychain_backend) == []

    def test_missing_keychain_token_is_warning_finding(self, tmp_path: Path, plugin_root: Path, keychain_backend):
        """token 未入钥匙串:plugin_token_missing warning,不抛、不拦核心。"""
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        config = load_category_file(write_category_yaml(tmp_path))
        findings = check_category_plugin(config.plugin, store, backend=keychain_backend)
        assert finding_codes(findings) == ["plugin_token_missing"]
        assert all(finding.severity == "warning" for finding in findings)

    def test_probe_remote_unreachable_is_warning_not_exception(self, probe_client_factory):
        """铁律:remote 不可达(HTTP 500)→ 结构化 warning,绝不抛。"""
        factory = probe_client_factory(lambda request: httpx.Response(500))
        config = load_category(
            {
                "id": "demo",
                "name": "演示",
                "schedule": "0 9 * * *",
                "sources": [{
                    "name": "api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*]", "url": "$[*]"}},
                }],
                "push": [{"channel": "stdout"}],
                "plugin": {
                    "id": "myia-monitor",
                    "modes": {"remote": {"endpoint": "https://my-monitor.example.com"}},
                },
            }
        )
        assert config.plugin is not None
        findings = check_remote_modes(
            config.plugin, probe_remote=True, timeout=2.0, client_factory=factory
        )
        assert finding_codes(findings) == ["plugin_remote_unreachable"]
        assert "HTTP 500" in findings[0].message

    def test_probe_connection_error_is_structured_finding(self, probe_client_factory):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        config = load_category(
            {
                "id": "demo",
                "name": "演示",
                "schedule": "0 9 * * *",
                "sources": [{
                    "name": "api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*]", "url": "$[*]"}},
                }],
                "push": [{"channel": "stdout"}],
                "plugin": {
                    "id": "myia-monitor",
                    "modes": {"remote": {"endpoint": "https://my-monitor.example.com"}},
                },
            }
        )
        assert config.plugin is not None
        findings = check_remote_modes(
            config.plugin, probe_remote=True, timeout=2.0, client_factory=probe_client_factory(handler)
        )
        assert finding_codes(findings) == ["plugin_remote_unreachable"]
        assert "ConnectError" in findings[0].message

    def test_probe_timeout_is_structured_finding(self, probe_client_factory):
        async def slow_handler(request: httpx.Request) -> httpx.Response:
            import asyncio

            await asyncio.sleep(0.5)
            return httpx.Response(200)

        config = load_category(
            {
                "id": "demo",
                "name": "演示",
                "schedule": "0 9 * * *",
                "sources": [{
                    "name": "api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*]", "url": "$[*]"}},
                }],
                "push": [{"channel": "stdout"}],
                "plugin": {
                    "id": "myia-monitor",
                    "modes": {"remote": {"endpoint": "https://my-monitor.example.com"}},
                },
            }
        )
        assert config.plugin is not None
        findings = check_remote_modes(
            config.plugin, probe_remote=True, timeout=0.01, client_factory=probe_client_factory(slow_handler)
        )
        assert finding_codes(findings) == ["plugin_remote_unreachable"]
        assert "超时" in findings[0].message

    def test_probe_http_401_counts_as_reachable(self, probe_client_factory):
        """4xx = 端点活着(鉴权问题归 token 检查),不算 remote 不可达。"""
        config = load_category(
            {
                "id": "demo",
                "name": "演示",
                "schedule": "0 9 * * *",
                "sources": [{
                    "name": "api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*]", "url": "$[*]"}},
                }],
                "push": [{"channel": "stdout"}],
                "plugin": {
                    "id": "myia-monitor",
                    "modes": {"remote": {"endpoint": "https://my-monitor.example.com"}},
                },
            }
        )
        assert config.plugin is not None
        findings = check_remote_modes(
            config.plugin,
            probe_remote=True,
            timeout=2.0,
            client_factory=probe_client_factory(lambda request: httpx.Response(401)),
        )
        assert findings == []

    def test_probe_is_opt_in_and_run_path_stays_network_free(self, tmp_path: Path, plugin_root: Path, keychain_backend):
        """run 自检默认零网络:probe_remote 缺省 False,注入的传输层一旦收到请求即失败。"""
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        keychain_backend.set_password("myia", "myia/monitor/token", "token-value")
        config = load_category_file(write_category_yaml(tmp_path))
        assert config.plugin is not None

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("缺省自检路径不得发出任何网络请求")

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

        assert check_category_plugin(config.plugin, store, backend=keychain_backend, client_factory=factory) == []


# ---------------------------------------------------------------------------
# 品类顶层 plugin: 节(修 v0.2 评审确认的 monitor.yaml unknown_field 拒载)
# ---------------------------------------------------------------------------


class TestCategoryPluginSection:
    def test_monitor_yaml_remote_optin_section_loads(self):
        """回归钉:monitor.yaml 的 plugin: 节必须过真实加载入口(曾整文件拒载).

        v1.1 迁移后 monitor.yaml 是 remote 可选接入形态(无 local compose,
        requires 清空 —— 内置变更指纹覆盖桌面主场景,铁律不依赖插件)。
        """
        config = load_category_file(REPO_ROOT / "plugins" / "monitor.yaml")
        assert config.plugin is not None
        assert config.plugin.id == "myia-monitor"
        assert config.plugin.requires == []
        assert config.plugin.modes.local is None
        assert config.plugin.modes.remote is not None
        assert config.plugin.modes.remote.token == "keychain:myia/monitor/token"

    def test_plugin_section_unknown_inner_field_is_structured_error(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["plugin"]["mode"] = "remote"  # modes 的拼写错误
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "unknown_field"
        assert detail.path == "$.plugin.mode"

    def test_plugin_section_errors_merge_with_category_errors(self):
        """plugin 节与 12 节的错误一次性合并报告(字段路径各归各位)."""
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["schedule"] = "not-a-cron"
        data["plugin"]["bogus"] = 1
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        paths = {detail.path for detail in excinfo.value.errors}
        assert "$.schedule" in paths and "$.plugin.bogus" in paths

    def test_plugin_section_requires_normalizes_bare_string(self):
        config = load_category(yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML))
        assert config.plugin is not None and config.plugin.requires == ["docker"]

    def test_plugin_section_null_treated_as_absent(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["plugin"] = None
        config = load_category(data)
        assert config.plugin is None

    def test_plugin_section_rejects_env_token(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["plugin"]["modes"]["remote"]["token"] = "env:MYIA_MONITOR_TOKEN"
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        assert excinfo.value.errors[0].error_type == "plugin_token_requires_keychain"

    def test_plugin_section_rejects_plaintext_token(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["plugin"]["modes"]["remote"]["token"] = "hunter2-plaintext"
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        assert excinfo.value.errors[0].error_type == "credential_plaintext"

    def test_plugin_section_requires_at_least_one_mode(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data["plugin"]["modes"] = {}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        assert excinfo.value.errors[0].error_type == "missing_plugin_mode"

    def test_category_without_plugin_section_has_none(self):
        data = yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML)
        assert isinstance(data, dict)
        data.pop("plugin")
        config = load_category(data)
        assert config.plugin is None

    def test_doctor_credential_scan_sees_plugin_token_ref(self):
        """plugin: 节的 token 引用并入 doctor 凭据体检(路径带 $.plugin 前缀).

        用只有 plugin 节带凭据的品类做夹具,路径断言才不被 sources 侧同名
        引用的去重规则遮蔽。
        """
        config = load_category(yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML))
        refs = cli_module._collect_credential_refs(config)
        assert refs == [
            {
                "path": "$.plugin.modes.remote.token",
                "kind": "keychain",
                "name": "myia/monitor/token",
                "ref": "keychain:myia/monitor/token",
            }
        ]

    def test_direct_constructed_config_has_no_plugin(self):
        """绕过 load_category 直接构造时 plugin 为 None(sidecar 只在装载入口挂载)."""
        config = load_category(yaml.safe_load(CATEGORY_WITH_PLUGIN_YAML))
        dumped = config.model_dump()
        assert "plugin" not in dumped, "12 节契约不被 sidecar 污染(SKILL.md 一致性测试依赖)"
        assert config.plugin is not None  # 经 load_category 挂载后可读


# ---------------------------------------------------------------------------
# CLI:myia plugin list / install / remove(--json 单文档,退出码 0/1)
# ---------------------------------------------------------------------------


class TestPluginCli:
    def test_list_missing_dir_exits_zero_with_empty_list(self, tmp_path: Path, capsys):
        """未装任何插件是正常态:空清单、退出码 0,不是错误。"""
        code = main(["plugin", "list", "--dir", str(tmp_path / "nope"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        assert payload["plugins"] == []
        assert payload["summary"]["installed"] == 0

    def test_install_list_remove_roundtrip(self, tmp_path: Path, plugin_root: Path, capsys):
        source = write_plugin_dir(tmp_path)
        assert main(["plugin", "install", str(source), "--dir", str(plugin_root), "--json"]) == EXIT_OK
        capsys.readouterr()
        assert main(["plugin", "list", "--dir", str(plugin_root), "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        (entry,) = payload["plugins"]
        assert entry["id"] == "myia-monitor" and entry["loaded"] is True
        assert entry["compatible_current"] is True
        assert payload["summary"]["usable"] == 1
        assert main(["plugin", "remove", "myia-monitor", "--dir", str(plugin_root), "--json"]) == EXIT_OK
        capsys.readouterr()
        assert main(["plugin", "list", "--dir", str(plugin_root), "--json"]) == EXIT_OK
        assert json.loads(capsys.readouterr().out)["plugins"] == []

    def test_install_broken_manifest_exits_one_structured(self, tmp_path: Path, plugin_root: Path, capsys):
        source = write_plugin_dir(tmp_path, manifest_text="id: myia-broken\nbanana: true\n")
        code = main(["plugin", "install", str(source), "--dir", str(plugin_root), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_CONFIG_ERROR
        assert payload["error"] == "plugin" and payload["code"] == "manifest_invalid"
        banana = next(error for error in payload["errors"] if error["path"] == "$.banana")
        assert banana["error_type"] == "unknown_field"

    def test_install_duplicate_exits_one_without_force(self, tmp_path: Path, plugin_root: Path, capsys):
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        second_source = write_plugin_dir(tmp_path / "second")
        code = main(["plugin", "install", str(second_source), "--dir", str(plugin_root), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_CONFIG_ERROR
        assert payload["code"] == "already_installed"

    def test_remove_missing_exits_one(self, tmp_path: Path, plugin_root: Path, capsys):
        code = main(["plugin", "remove", "myia-ghost", "--dir", str(plugin_root), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_CONFIG_ERROR
        assert payload["code"] == "not_installed"

    def test_list_broken_entry_reports_finding_and_exits_zero(self, tmp_path: Path, plugin_root: Path, capsys):
        broken = plugin_root / "myia-broken"
        broken.mkdir(parents=True)
        (broken / "plugin.yaml").write_text("id: [unclosed\n", encoding="utf-8")
        code = main(["plugin", "list", "--dir", str(plugin_root), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        (entry,) = payload["plugins"]
        assert entry["loaded"] is False
        assert entry["findings"][0]["code"] == "manifest_invalid"
        assert payload["summary"]["errors"] == 1

    def test_list_probe_unreachable_exits_zero_with_finding(
        self, tmp_path: Path, plugin_root: Path, capsys, probe_client_factory, keychain_backend
    ):
        """--probe:端点探测失败只产 findings(信息性命令完成即 0),零真实网络."""
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        probe_client_factory(lambda request: httpx.Response(503))
        code = main(["plugin", "list", "--dir", str(plugin_root), "--json", "--probe"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        (entry,) = payload["plugins"]
        codes = [finding["code"] for finding in entry["findings"]]
        assert "plugin_remote_unreachable" in codes

    def test_human_mode_prints_summary_not_json(self, tmp_path: Path, plugin_root: Path, capsys):
        store = InstalledPluginStore(plugin_root)
        store.install(write_plugin_dir(tmp_path))
        code = main(["plugin", "list", "--dir", str(plugin_root)])
        out = capsys.readouterr().out
        assert code == EXIT_OK
        assert "myia-monitor@1.0.0" in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)

    def test_help_documents_plugin_subcommands(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["plugin", "--help"])
        assert excinfo.value.code == 0
        top_help = capsys.readouterr().out
        for token in ("list", "install", "remove"):
            assert token in top_help
        with pytest.raises(SystemExit):
            main(["plugin", "list", "--help"])
        list_help = capsys.readouterr().out
        assert "--probe" in list_help and "--dir" in list_help
        with pytest.raises(SystemExit):
            main(["plugin", "install", "--help"])
        assert "--force" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 装卸 IO 边界(回归:source==dest --force 曾半卸插件;OSError 曾裸逃)
# ---------------------------------------------------------------------------


class TestPluginStoreIoGuards:
    def test_force_install_from_installed_path_rejected_structured(
        self, tmp_path: Path, plugin_root: Path
    ):
        """`plugin list --json` 的 path 拿来 --force 重装:结构化拒绝,插件完好。"""
        source = write_plugin_dir(tmp_path)
        store = InstalledPluginStore(plugin_root)
        first = store.install(source)
        installed_dir = Path(first["path"])
        assert installed_dir.is_dir()

        with pytest.raises(PluginStoreError) as excinfo:
            store.install(installed_dir, force=True)

        assert excinfo.value.code == "invalid_source"
        assert "安装目标" in str(excinfo.value)
        # 关键:已装插件未被删除(此前被 rmtree 半卸)
        assert installed_dir.is_dir()
        assert (installed_dir / "plugin.yaml").is_file()
        assert [entry.plugin_id for entry in store.entries()] == ["myia-monitor"]

    def test_remove_symlinked_plugin_dir_is_structured_io_error(
        self, tmp_path: Path, plugin_root: Path
    ):
        """安装根内的符号链接目录:rmtree 拒绝 → PluginStoreError(io_error),
        不再裸 OSError 打穿 --json 契约(cli.py 只捕 PluginStoreError)。"""
        source = write_plugin_dir(tmp_path)
        store = InstalledPluginStore(plugin_root)
        store.install(source)
        real = plugin_root / "myia-monitor"
        link = plugin_root / "link-demo"
        link.symlink_to(real)

        with pytest.raises(PluginStoreError) as excinfo:
            store.remove("link-demo")

        assert excinfo.value.code == "io_error"
        assert real.is_dir()  # 真实插件不受影响

    def test_install_unreadable_source_wraps_oserror_as_io_error(
        self, tmp_path: Path, plugin_root: Path
    ):
        """来源目录不可读:copytree 的 OSError 包裹为 io_error(结构化,非裸逃)。"""
        import os
        import platform

        source = write_plugin_dir(tmp_path)
        blocked = source / "secret"
        blocked.mkdir()
        (blocked / "x.txt").write_text("private", encoding="utf-8")
        if platform.system() != "POSIX" or os.geteuid() == 0:  # pragma: no cover
            pytest.skip("需要 POSIX 非 root 才能制造不可读目录")
        blocked.chmod(0o000)
        store = InstalledPluginStore(plugin_root)
        try:
            with pytest.raises(PluginStoreError) as excinfo:
                store.install(source)
            assert excinfo.value.code == "io_error"
            # 半装残留已清(「绝不半装」承诺)
            assert not (plugin_root / "myia-monitor").exists()
        finally:
            blocked.chmod(0o755)  # 还原,tearDown 的 tmp 清理不被权限卡住
