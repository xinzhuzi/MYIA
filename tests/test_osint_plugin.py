"""myia-osint 源码型插件样板(PRD 10-02-v11-plugins-source-arch)契约测试.

首个「源码型插件」样板的四组被钉住的契约:

1. **manifest schema 演进**:myia-osint 声明 ``vendor``(上游 submodule
   pin)与 ``adapter``(MYIA 侧适配器入口/方式)且过真实校验入口;旧五包
   两节缺省 None(向后兼容);vendor/adapter 内未知字段、非 https 来源、
   非法 pin、路径越界、mode 白名单外 —— 全部 fail-fast 拒载;
2. **submodule 形状**:vendor/Photon 以 gitlink(160000)入库并等于
   manifest 的 pin —— GPL 上游零代码复制的仓库级证据;
3. **适配器 mock 子进程**:命令形状(uv 隔离环境)、成功装配结构化 JSON、
   非零退出/超时/导出缺失或损坏/目标非法/vendor 缺失/uv 缺失 —— 每种失败
   都是结构化 :class:`OsintAdapterError`(code 词表);
4. **CLI ``myia osint`` + 铁律**:默认目标 example.com;适配器缺失退 1、
   vendor 缺失退 1、采集失败退 2;任何失败形态下核心品类加载与 Pipeline
   构造完全无感(装不上不拦核心)。

测试纪律:子进程一律注入 fake runner(mock),零真实网络、零真实 uv;
每测独立 tmp_path。
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from myia.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_ALL_FAILED,
    EXIT_OK,
    OSINT_FETCH_FAILURE_CODES,
    _import_osint_adapter,
    main,
)
from myia.pipeline import Pipeline
from myia.plugins.manifest import load_manifest, load_manifest_file
from myia.schema import LoadError, load_category_file

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "myia-osint"
PLUGIN_ADAPTER = PLUGIN_DIR / "adapter.py"

#: v1.1 起仍为「无 vendor/adapter 旧包」形态的四件(myia-proxy 已升级为
#: 进程内适配器插件,挪出本清单,见
#: TestManifestSchemaEvolution.test_proxy_package_declares_in_process_adapter_without_vendor)。
LEGACY_PACKAGES = (
    "myia-douyin",
    "myia-monitor",
    "myia-maxun",
    "myia-credentials",
)


def load_adapter() -> Any:
    """按 CLI 同款加载器(compile+exec,零 __pycache__ 副作用)加载真实适配器."""
    return _import_osint_adapter(PLUGINS_DIR)


# ---------------------------------------------------------------------------
# 契约一:manifest schema 演进(vendor / adapter,可选 + fail-fast)
# ---------------------------------------------------------------------------


class TestManifestSchemaEvolution:
    def test_osint_manifest_declares_vendor_and_adapter(self):
        manifest = load_manifest_file(PLUGIN_DIR / "plugin.yaml")
        assert manifest.vendor is not None
        assert manifest.vendor.source == "https://github.com/s0md3v/Photon"
        assert manifest.vendor.path == "vendor/Photon"
        assert manifest.vendor.pin is not None
        assert re.fullmatch(r"[0-9a-f]{40}", manifest.vendor.pin), "pin 必须是完整 40 位 commit SHA"
        assert manifest.vendor.license == "GPL-3.0"
        assert manifest.adapter is not None
        assert manifest.adapter.entry == "adapter.py"
        assert manifest.adapter.mode == "subprocess"

    @pytest.mark.parametrize("package", LEGACY_PACKAGES)
    def test_legacy_packages_default_vendor_adapter_to_none(self, package: str):
        """向后兼容:旧包不声明 vendor/adapter → 缺省 None,照常通过校验。"""
        manifest = load_manifest_file(PLUGINS_DIR / package / "plugin.yaml")
        assert manifest.vendor is None
        assert manifest.adapter is None

    def test_proxy_package_declares_in_process_adapter_without_vendor(self):
        """myia-proxy 与样板同构(声明 adapter)但零 vendored:自实现精简版.

        v1.1 迁移把 myia-proxy 升级为 desktop 分级进程内插件:适配器参照
        proxy_pool 思路自实现(fetch+测活),上游零源码复制、零 submodule。
        """
        manifest = load_manifest_file(PLUGINS_DIR / "myia-proxy" / "plugin.yaml")
        assert manifest.tier == "desktop"
        assert manifest.vendor is None, "myia-proxy 为自实现精简版,不得 vendored 上游"
        assert manifest.adapter is not None
        assert manifest.adapter.entry == "adapter.py"
        assert manifest.adapter.mode == "in_process"

    @staticmethod
    def _manifest_with(**overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": "myia-demo",
            "name": "演示插件",
            "version": "1.0.0",
            "compatible": ">=0.1,<2.0",
            "modes": {"remote": {"endpoint": "https://demo-wrapper.example.com"}},
            "install": {"source": "https://github.com/xinzhuzi/MYIA.git"},
        }
        data.update(overrides)
        return data

    def test_vendor_unknown_field_fails_fast(self):
        data = self._manifest_with(
            vendor={"source": "https://github.com/x/y", "path": "vendor/x", "bogus": 1}
        )
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any("vendor" in detail.path for detail in exc_info.value.errors)

    def test_vendor_source_requires_https(self):
        data = self._manifest_with(
            vendor={"source": "git@github.com:s0md3v/Photon.git", "path": "vendor/Photon"}
        )
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any(detail.error_type == "invalid_vendor_source" for detail in exc_info.value.errors)

    def test_vendor_pin_must_be_hex_sha(self):
        data = self._manifest_with(
            vendor={"source": "https://github.com/x/y", "path": "vendor/x", "pin": "not-a-sha"}
        )
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any(detail.error_type == "invalid_vendor_pin" for detail in exc_info.value.errors)

    @pytest.mark.parametrize("path", ["../Photon", "/etc/Photon", "vendor/../../Photon"])
    def test_vendor_path_rejects_traversal_and_absolute(self, path: str):
        data = self._manifest_with(vendor={"source": "https://github.com/x/y", "path": path})
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any(detail.error_type == "unsafe_relative_path" for detail in exc_info.value.errors)

    def test_adapter_mode_outside_whitelist_fails_fast(self):
        data = self._manifest_with(adapter={"entry": "adapter.py", "mode": "docker"})
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any("adapter.mode" in detail.path for detail in exc_info.value.errors)

    def test_adapter_entry_must_be_python_file(self):
        data = self._manifest_with(adapter={"entry": "adapter.txt"})
        with pytest.raises(LoadError) as exc_info:
            load_manifest(data)
        assert any(detail.error_type == "invalid_adapter_entry" for detail in exc_info.value.errors)


# ---------------------------------------------------------------------------
# 契约二:submodule 形状(gitlink 入库 == manifest pin;GPL 零复制证据)
# ---------------------------------------------------------------------------


class TestSubmoduleShape:
    def test_vendor_is_gitlink_pinned_to_manifest_pin(self):
        """vendor/Photon 必须以 gitlink(160000)入库,SHA 等于 manifest pin。"""
        manifest = load_manifest_file(PLUGIN_DIR / "plugin.yaml")
        assert manifest.vendor is not None and manifest.vendor.pin is not None
        probe = subprocess.run(
            ["git", "ls-files", "--stage", "--", "plugins/myia-osint/vendor/Photon"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            pytest.skip("git 不可用(非 git 检出),gitlink 形状无法核验")
        fields = probe.stdout.split()
        assert fields, "vendor/Photon 必须以 gitlink 入库(零代码复制的仓库级证据)"
        assert fields[0] == "160000"
        assert fields[1] == manifest.vendor.pin

    def test_gitmodules_declares_photon_submodule(self):
        gitmodules = (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
        assert "plugins/myia-osint/vendor/Photon" in gitmodules
        assert "https://github.com/s0md3v/Photon" in gitmodules


# ---------------------------------------------------------------------------
# 契约三:适配器 mock 子进程(命令形状 + 结构化错误词表)
# ---------------------------------------------------------------------------


def make_vendor_plugin(tmp_path: Path) -> Path:
    """构造带 vendor 入口的假插件目录(只造 photon.py 存在性,不装依赖)."""
    plugin = tmp_path / "myia-osint"
    vendor = plugin / "vendor" / "Photon"
    vendor.mkdir(parents=True)
    (vendor / "photon.py").write_text("# upstream stub\n", encoding="utf-8")
    return plugin


def writing_runner(export: Any, *, returncode: int = 0, export_raw: str | None = None):
    """fake subprocess.run:在 -o 指定的目录里落 exported.json 与一个数据集."""

    def runner(command: list[str], **kwargs: Any):
        loot = Path(command[command.index("-o") + 1])
        loot.mkdir(parents=True, exist_ok=True)
        payload = export_raw if export_raw is not None else json.dumps(export)
        (loot / "exported.json").write_text(payload, encoding="utf-8")
        (loot / "endpoints.txt").write_text("https://example.com/\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, stdout="ok", stderr="")

    return runner


class TestPhotonAdapter:
    @pytest.fixture()
    def adapter(self) -> Any:
        return load_adapter()

    def test_build_command_is_isolated_uv_environment(self, adapter):
        command = adapter.build_command("https://example.com", Path("/tmp/loot"))
        assert command[0] == "uv"
        assert "--no-project" in command, "必须切断根项目环境"
        withs = [command[i + 1] for i, part in enumerate(command) if part == "--with"]
        assert withs == ["requests", "urllib3", "tld"], "依赖与上游 requirements.txt 同源"
        assert command[-8:] == [
            "python",
            "photon.py",
            "-u",
            "https://example.com",
            "-o",
            "/tmp/loot",
            "-e",
            "json",
        ]

    def test_run_success_assembles_structured_payload(self, adapter, tmp_path):
        plugin = make_vendor_plugin(tmp_path)
        ticks = iter([0.0, 1.25])
        result = adapter.run(
            "https://example.com/",
            plugin_dir=plugin,
            runner=writing_runner({"endpoints": ["https://example.com/"]}),
            clock=lambda: next(ticks),
        )
        assert result["status"] == "success"
        assert result["plugin"] == "myia-osint"
        assert result["target"] == "https://example.com", "尾部斜杠被规整"
        assert result["results"] == {"endpoints": ["https://example.com/"]}
        assert result["datasets"] == ["endpoints"]
        assert result["duration_seconds"] == 1.25
        assert result["exit_code"] == 0
        assert result["command"][0] == "uv"
        assert result["vendor"]["commit"] is None, "非 git 工作树时溯源缺省 None"

    def test_vendor_commit_reads_git_when_available(self, adapter):
        """真检出的已 init 工作树读到 pin;读不到(未 init)返回 None 而非抛错。"""
        manifest = load_manifest_file(PLUGIN_DIR / "plugin.yaml")
        commit = adapter.vendor_commit()
        assert commit is None or commit == manifest.vendor.pin

    def test_photon_nonzero_exit_maps_to_photon_failed(self, adapter, tmp_path):
        plugin = make_vendor_plugin(tmp_path)

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=plugin, runner=runner)
        error = exc_info.value
        assert error.code == "photon_failed"
        assert error.details["exit_code"] == 1
        assert "boom" in error.details["stderr_tail"]
        assert "photon_failed" in OSINT_FETCH_FAILURE_CODES, "采集失败码必须映射退出码 2"

    def test_timeout_maps_to_photon_timeout(self, adapter, tmp_path):
        plugin = make_vendor_plugin(tmp_path)

        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=5.0)

        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=plugin, runner=runner, timeout=5.0)
        error = exc_info.value
        assert error.code == "photon_timeout"
        assert error.details["timeout_seconds"] == 5.0
        assert "photon_timeout" in OSINT_FETCH_FAILURE_CODES

    def test_missing_export_maps_to_photon_export_missing(self, adapter, tmp_path):
        plugin = make_vendor_plugin(tmp_path)

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=plugin, runner=runner)
        assert exc_info.value.code == "photon_export_missing"

    def test_corrupt_export_maps_to_photon_export_invalid(self, adapter, tmp_path):
        plugin = make_vendor_plugin(tmp_path)
        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run(
                "https://example.com",
                plugin_dir=plugin,
                runner=writing_runner(None, export_raw="{not json"),
            )
        assert exc_info.value.code == "photon_export_invalid"

    @pytest.mark.parametrize("target", ["", "   ", "example.com", "ftp://example.com", "file:///etc"])
    def test_invalid_target_rejected_before_any_process(self, adapter, tmp_path, target):
        plugin = make_vendor_plugin(tmp_path)

        def runner(command, **kwargs):  # pragma: no cover — 不应走到子进程
            raise AssertionError(f"目标非法仍发起子进程:{command}")

        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run(target, plugin_dir=plugin, runner=runner)
        assert exc_info.value.code == "invalid_target"

    def test_vendor_missing_is_structured_not_raised_generic(self, adapter, tmp_path):
        plugin = tmp_path / "myia-osint"
        plugin.mkdir()
        assert adapter.is_available(plugin) is False
        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=plugin)
        error = exc_info.value
        assert error.code == "vendor_missing"
        assert "git submodule update --init" in error.message
        assert error.details["plugin_dir"] == str(plugin)

    def test_uv_missing_is_structured(self, adapter, tmp_path, monkeypatch):
        plugin = make_vendor_plugin(tmp_path)
        monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=plugin, runner=writing_runner({}))
        assert exc_info.value.code == "uv_missing"


# ---------------------------------------------------------------------------
# 契约四:CLI myia osint(默认 example.com;退出码 0/1/2)+ 铁律
# ---------------------------------------------------------------------------


STUB_ADAPTER = textwrap.dedent(
    """
    AVAILABILITY = True
    RAISE = None  # ('photon_failed',) 时模拟采集失败

    class FakeError(Exception):
        def __init__(self, payload):
            super().__init__(payload["message"])
            self.payload = payload

        def to_dict(self):
            return self.payload

    def is_available(plugin_dir=None):
        return AVAILABILITY

    def run(target, timeout=None, **kwargs):
        if not AVAILABILITY:
            raise FakeError({
                "code": "vendor_missing",
                "message": "上游源码未就位(桩)",
                "vendor_path": "vendor/Photon",
            })
        if RAISE == "photon_failed":
            raise FakeError({
                "code": "photon_failed",
                "message": "Photon 子进程非零退出",
                "exit_code": 1,
                "stderr_tail": "boom",
            })
        return {
            "plugin": "myia-osint",
            "target": target,
            "status": "success",
            "results": {"endpoints": [target]},
            "datasets": ["endpoints"],
            "duration_seconds": 0.5,
            "vendor": {"commit": None},
            "command": ["uv"],
            "exit_code": 0,
        }
    """
)


def make_stub_plugin(tmp_path: Path, *, availability: bool = True, raise_code: str | None = None) -> Path:
    plugins = tmp_path / "plugins"
    plugin = plugins / "myia-osint"
    plugin.mkdir(parents=True)
    source = STUB_ADAPTER
    if not availability:
        source = source.replace("AVAILABILITY = True", "AVAILABILITY = False")
    if raise_code:
        source = source.replace('RAISE = None', f'RAISE = {raise_code!r}')
    (plugin / "adapter.py").write_text(source, encoding="utf-8")
    return plugins


class TestCliOsint:
    def test_success_json_contract(self, tmp_path, capsys):
        plugins = make_stub_plugin(tmp_path)
        code = main(["osint", "https://example.com", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugin"] == "myia-osint"
        assert payload["target"] == "https://example.com"
        assert payload["status"] == "success"

    def test_default_target_is_example_com(self, tmp_path, capsys):
        plugins = make_stub_plugin(tmp_path)
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_OK
        assert json.loads(capsys.readouterr().out)["target"] == "https://example.com"

    def test_vendor_missing_exits_1_with_structured_error(self, tmp_path, capsys):
        plugins = make_stub_plugin(tmp_path, availability=False)
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "vendor_missing"
        assert payload["message"]

    def test_photon_failure_exits_2(self, tmp_path, capsys):
        plugins = make_stub_plugin(tmp_path, raise_code="photon_failed")
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_FETCH_ALL_FAILED
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "photon_failed"
        assert payload["exit_code"] == 1

    def test_adapter_file_missing_exits_1(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "osint_adapter_missing"

    def test_real_adapter_without_vendor_exits_1_with_hint(self, tmp_path, capsys):
        """真适配器 + 无 vendor:结构化 vendor_missing(含 submodule 指引),退 1。"""
        plugins = tmp_path / "plugins"
        (plugins / "myia-osint").mkdir(parents=True)
        (plugins / "myia-osint" / "adapter.py").write_text(
            PLUGIN_ADAPTER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "vendor_missing"
        assert "git submodule update --init" in payload["message"]

    def test_broken_adapter_source_exits_1_not_crash(self, tmp_path, capsys):
        plugins = tmp_path / "plugins"
        (plugins / "myia-osint").mkdir(parents=True)
        (plugins / "myia-osint" / "adapter.py").write_text("def run(: broken", encoding="utf-8")
        code = main(["osint", "--plugins-dir", str(plugins), "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "osint_adapter_missing"


class TestIronLawMissingVendorNeverBlocksCore:
    """铁律回归:vendor/适配器任何装不上形态,核心品类流水线完全无感。"""

    @staticmethod
    def _assert_core_unblocked() -> None:
        category_files = sorted(PLUGINS_DIR.glob("*.yaml"))
        assert category_files, "顶层品类示例不应为空"
        for path in category_files:
            config = load_category_file(path)
            assert config.sources, f"{path.name} 必须可加载且有源"
        Pipeline(load_category_file(PLUGINS_DIR / "wool.yaml"))

    def test_real_adapter_vendor_missing_keeps_core_unblocked(self, tmp_path):
        adapter = load_adapter()
        with pytest.raises(adapter.OsintAdapterError) as exc_info:
            adapter.run("https://example.com", plugin_dir=tmp_path)
        assert exc_info.value.code == "vendor_missing"
        self._assert_core_unblocked()

    def test_adapter_not_importable_keeps_core_unblocked(self, tmp_path):
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        assert main(["osint", "--plugins-dir", str(plugins), "--json"]) == EXIT_CONFIG_ERROR
        self._assert_core_unblocked()
