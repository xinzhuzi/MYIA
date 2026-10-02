"""官方六件场景件(plugin packages)的封装契约测试(PRD 10-01-v03-plugin-market).

六个 ``plugins/<id>/`` 目录是市场插件包:每包含 ``plugin.yaml``(manifest,
规范见 :mod:`myia.plugins.manifest`)+ README(local/remote 双路径)+
docker-compose.yml(local 模式;myia-credentials 为纯 remote 例外)。三条
被钉住的契约:

1. 六件 manifest 全部过真实校验入口 :func:`load_manifest_file`:id==目录名、
   版本矩阵兼容当前 myia、remote 端点只用 example.com 占位域(公开仓库红线);
2. **remote 模式 mock 往返**:MockTransport 拦截端点探测,零真实网络;
3. **铁律呼应**:损坏/未装的插件包只降级为 warning finding,核心品类加载
   与 Pipeline 构造完全无感(与 tests/test_plugins_system.py 同一铁律,
   这里在真实插件包的尺度上再钉一遍)。

测试纪律:零真实网络、零真实钥匙串(InMemoryKeychainBackend)、每测独立
tmp_path。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import yaml

from myia import __version__ as myia_version
from myia.pipeline import Pipeline
from myia.plugins import VersionRange, check_category_plugin, check_remote_modes
from myia.plugins.installed import InstalledPluginStore
from myia.plugins.manifest import load_manifest_file
from myia.schema import load_category_file
from myia.secrets import InMemoryKeychainBackend
from myia.secrets import reset_backend as reset_keychain_backend
from myia.secrets import set_backend as set_keychain_backend

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"

#: 六件官方场景件(目录名 == manifest id)。
OFFICIAL_PACKAGES = (
    "myia-proxy",
    "myia-osint",
    "myia-douyin",
    "myia-monitor",
    "myia-maxun",
    "myia-credentials",
)


def package_dir(package: str) -> Path:
    return PLUGINS_DIR / package


def load_package(package: str):
    """One package's manifest through the real validation entry point."""
    return load_manifest_file(package_dir(package) / "plugin.yaml")


# ---------------------------------------------------------------------------
# 契约一:六件 manifest 过真实校验 + 包结构完整
# ---------------------------------------------------------------------------


class TestPackageManifests:
    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_manifest_loads_with_id_matching_dir_name(self, package: str):
        manifest = load_package(package)
        assert manifest.id == package, "安装布局契约:目录名必须等于 manifest id"
        assert manifest.name
        assert re.fullmatch(r"\d+\.\d+\.\d+", manifest.version)

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_version_matrix_declares_compatible_myia_range(self, package: str):
        """每件都声明兼容 myia 的版本范围,且当前版本落在其中。"""
        manifest = load_package(package)
        assert VersionRange(manifest.compatible).contains(myia_version)

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_requires_stays_inside_closed_vocabulary(self, package: str):
        manifest = load_package(package)
        assert set(manifest.requires) <= {"docker"}
        assert manifest.provides, "provides 为空 = 品类侧无从引用"

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_declares_at_least_one_mode_with_source(self, package: str):
        manifest = load_package(package)
        assert manifest.modes.local is not None or manifest.modes.remote is not None
        assert manifest.install.source.startswith("https://")

    def test_remote_only_package_ships_no_compose(self):
        """myia-credentials 是纯 remote 例外:无 compose,manifest 也无 local 模式。"""
        manifest = load_package("myia-credentials")
        assert manifest.modes.local is None
        assert manifest.modes.remote is not None
        assert not (package_dir("myia-credentials") / "docker-compose.yml").exists()

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_readme_documents_both_paths(self, package: str):
        readme = (package_dir(package) / "README.md").read_text(encoding="utf-8")
        manifest = load_package(package)
        if manifest.modes.local is not None:
            assert "docker compose" in readme, (
                f"{package}: README 缺 local(docker compose)路径"
            )
        if manifest.modes.remote is not None:
            assert "endpoint" in readme, (
                f"{package}: README 缺 remote(填 endpoint+token)路径"
            )

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_remote_endpoint_is_public_placeholder_and_token_canonical(
        self, package: str
    ):
        """公开仓库红线:endpoint 只用 example.com 占位;token 只走规范名空间。"""
        manifest = load_package(package)
        if manifest.modes.remote is None:
            pytest.skip("该插件未声明 remote 模式")
        hostname = urlparse(manifest.modes.remote.endpoint).hostname or ""
        assert hostname.endswith(".example.com"), (
            f"endpoint 必须用 example.com 占位:{hostname}"
        )
        token = manifest.modes.remote.token
        if token is None:
            return
        assert token.startswith("keychain:myia/")
        name = token.split(":", 1)[1]
        assert name.count("/") == 2, f"token 应为 myia/<scope>/<name>:{name}"
        assert name.split("/")[1] == package.removeprefix("myia-"), (
            "token scope 应与插件名对应"
        )

    @pytest.mark.parametrize(
        "package",
        [
            name
            for name in OFFICIAL_PACKAGES
            if (PLUGINS_DIR / name / "docker-compose.yml").is_file()
        ],
    )
    def test_compose_parses_and_holds_no_plaintext_secrets(self, package: str):
        """compose 可解析、至少一个服务;凭据形键的值必须走 ${VAR} 注入。"""
        compose = yaml.safe_load(
            (package_dir(package) / "docker-compose.yml").read_text(encoding="utf-8")
        )
        assert isinstance(compose, dict) and compose.get("services"), (
            "compose 必须声明 services"
        )
        suspicious = re.compile(
            r"password|secret|token|api[-_]?key|authorization|cookie", re.IGNORECASE
        )
        for service in compose["services"].values():
            assert isinstance(service, dict)
            for key, value in service.items():
                if (
                    suspicious.search(str(key))
                    and isinstance(value, str)
                    and value.strip()
                ):
                    assert "${" in value, (
                        f"{package}: compose 里 {key} 疑似明文凭据(应为 ${{VAR}} 注入)"
                    )


# ---------------------------------------------------------------------------
# 契约二:品类 YAML ↔ 插件包互相咬合(monitor/credentials 两侧)
# ---------------------------------------------------------------------------


class TestCategoryWiring:
    @pytest.mark.parametrize(
        ("category", "package"),
        [("monitor", "myia-monitor"), ("credentials", "myia-credentials")],
    )
    def test_category_plugin_section_matches_package_manifest(
        self, category: str, package: str
    ):
        """品类 plugin: 节与市场包 manifest 同源:id/endpoint/token 一字不差。"""
        config = load_category_file(PLUGINS_DIR / f"{category}.yaml")
        manifest = load_package(package)
        assert config.plugin is not None
        assert config.plugin.id == manifest.id
        assert config.plugin.requires == manifest.requires
        assert (
            config.plugin.modes.remote is not None and manifest.modes.remote is not None
        )
        assert config.plugin.modes.remote.endpoint == manifest.modes.remote.endpoint
        assert config.plugin.modes.remote.token == manifest.modes.remote.token

    def test_credentials_source_header_reuses_package_token_ref(self):
        config = load_category_file(PLUGINS_DIR / "credentials.yaml")
        manifest = load_package("myia-credentials")
        source = next(s for s in config.sources if s.name == "aipocket")
        token = manifest.modes.remote.token
        assert token is not None
        assert source.headers["Authorization"] == f"Bearer {token}"
        assert source.url.startswith(manifest.modes.remote.endpoint)


# ---------------------------------------------------------------------------
# 契约三:remote 模式 mock 往返(MockTransport,零真实网络)
# ---------------------------------------------------------------------------


def _probe_factory(handler: Any) -> Any:
    """探测端 client 工厂:MockTransport 拦截(与基建测试同款形状)."""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return factory


class TestRemoteRoundTrip:
    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_probe_reachable_endpoint_yields_no_findings(self, package: str):
        manifest = load_package(package)
        if manifest.modes.remote is None:
            pytest.skip("该插件未声明 remote 模式")
        findings = check_remote_modes(
            manifest,
            probe_remote=True,
            timeout=2.0,
            client_factory=_probe_factory(lambda request: httpx.Response(200)),
        )
        assert findings == []

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_probe_unreachable_endpoint_degrades_to_warning(self, package: str):
        manifest = load_package(package)
        if manifest.modes.remote is None:
            pytest.skip("该插件未声明 remote 模式")
        findings = check_remote_modes(
            manifest,
            probe_remote=True,
            timeout=2.0,
            client_factory=_probe_factory(lambda request: httpx.Response(500)),
        )
        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "warning", "remote 不可达 = 自动降级,不是 error"
        assert finding.code == "plugin_remote_unreachable"
        assert "HTTP 500" in finding.message

    def test_probe_never_asks_for_token_check_without_backend(
        self, package: str = "myia-monitor"
    ):
        """backend=None 时 token 存在性不可核验也不是问题(不产 finding)。"""
        manifest = load_package(package)
        assert (
            manifest.modes.remote is not None
            and manifest.modes.remote.token is not None
        )
        findings = check_remote_modes(
            manifest,
            backend=None,
            probe_remote=False,
            client_factory=_probe_factory(lambda request: httpx.Response(200)),
        )
        assert findings == []


# ---------------------------------------------------------------------------
# 契约四(铁律,包尺度复钉):装卸全绿;损坏/未装不拦核心
# ---------------------------------------------------------------------------


@pytest.fixture()
def keychain_backend():
    backend = InMemoryKeychainBackend()
    set_keychain_backend(backend)
    yield backend
    reset_keychain_backend()


class TestIronLawOnRealPackages:
    def test_all_six_packages_install_clean_into_store(self, tmp_path: Path):
        store = InstalledPluginStore(tmp_path / "plugins")
        for package in OFFICIAL_PACKAGES:
            store.install(package_dir(package))
        entries = store.entries()
        assert [entry.dir_name for entry in entries] == sorted(OFFICIAL_PACKAGES)
        for entry in entries:
            assert entry.manifest is not None
            assert entry.compatible_current is True
            assert entry.findings == [], f"{entry.dir_name}: 官方包不允许带 findings"

    def test_installed_monitor_with_token_in_keychain_is_finding_free(
        self, tmp_path: Path, keychain_backend
    ):
        store = InstalledPluginStore(tmp_path / "plugins")
        store.install(package_dir("myia-monitor"))
        config = load_category_file(PLUGINS_DIR / "monitor.yaml")
        assert config.plugin is not None
        # token 未写入 → warning,不抛、不拦:
        missing = check_category_plugin(config.plugin, store, backend=keychain_backend)
        assert [finding.code for finding in missing] == ["plugin_token_missing"]
        assert all(finding.severity == "warning" for finding in missing)
        # 写入钥匙链后 → 零 findings(缺省自检零网络):
        keychain_backend.set_password("myia", "myia/monitor/token", "token-value")
        assert (
            check_category_plugin(config.plugin, store, backend=keychain_backend) == []
        )

    def test_corrupt_installed_manifest_only_degrades_to_warning(self, tmp_path: Path):
        """铁律:已装包的 manifest 坏 → warning findings;核心加载与 Pipeline 无感。"""
        store = InstalledPluginStore(tmp_path / "plugins")
        store.install(package_dir("myia-monitor"))
        (tmp_path / "plugins" / "myia-monitor" / "plugin.yaml").write_text(
            "id: [unclosed\n", encoding="utf-8"
        )
        config = load_category_file(PLUGINS_DIR / "monitor.yaml")
        assert config.plugin is not None
        findings = check_category_plugin(config.plugin, store)
        assert [finding.code for finding in findings] == ["manifest_invalid"]
        assert all(finding.severity == "warning" for finding in findings)

    def test_uninstalled_plugin_is_warning_and_core_pipeline_still_builds(
        self, tmp_path: Path
    ):
        store = InstalledPluginStore(tmp_path / "plugins")
        config = load_category_file(PLUGINS_DIR / "monitor.yaml")
        assert config.plugin is not None
        findings = check_category_plugin(config.plugin, store)
        assert [finding.code for finding in findings] == ["plugin_not_installed"]
        assert all(finding.severity == "warning" for finding in findings)
        self._assert_core_unblocked()

    def test_category_without_plugin_section_keeps_core_unblocked(self):
        """「禁用」形态:品类不声明 plugin: 节 → 无插件、核心照常。"""
        config = load_category_file(PLUGINS_DIR / "wool.yaml")
        assert config.plugin is None
        self._assert_core_unblocked()

    @staticmethod
    def _assert_core_unblocked() -> None:
        """核心无感的最小可执行证据:全部顶层品类 YAML 可加载 + Pipeline 可构造。"""
        category_files: list[Path] = sorted(PLUGINS_DIR.glob("*.yaml"))
        assert category_files, "顶层品类示例不应为空"
        for path in category_files:
            config = load_category_file(path)
            assert config.sources, f"{path.name} 必须可加载且有源"
        Pipeline(load_category_file(PLUGINS_DIR / "wool.yaml"))


# ---------------------------------------------------------------------------
# 公开仓库红线:六件包 + community 目录零内网地址/私有系统痕迹
# ---------------------------------------------------------------------------


_PRIVATE_HOST_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
#: 私有系统痕迹(PRD Notes 里的内部环境指称)零入库;127.0.0.1 属显式例外。
#: 字面量拼接书写,避免哨兵自身在仓库内携带该字符串。
_FORBIDDEN_TRACES = ("007" + "idc",)


@pytest.mark.parametrize("package", [*OFFICIAL_PACKAGES, "community"])
def test_package_files_hold_no_private_addresses_or_traces(package: str):
    target = package_dir(package)
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert not _PRIVATE_HOST_RE.search(text), f"{path}: 出现内网地址(公开仓库红线)"
        for trace in _FORBIDDEN_TRACES:
            assert trace not in text, f"{path}: 出现私有系统痕迹 {trace!r}"
        # Bearer 后面只许 keychain 引用或「token」这类说明词,不许裸凭据字面量。
        for match in re.finditer(r"Bearer\s+(\S+)", text):
            word = match.group(1)
            assert word.startswith("keychain:") or word in (
                "token",
                "token)",
                "<token>",
            ), f"{path}: Bearer 后疑似裸凭据字面量 {word!r}"


def test_official_packages_never_reference_their_upstream_by_copying_files():
    """GPL/AGPL 红线的包形状证据:六件包里没有任何上游源码文件(只有声明/文档/compose)."""
    allowed_suffixes = {".yaml", ".yml", ".md"}
    for package in OFFICIAL_PACKAGES:
        for path in package_dir(package).iterdir():
            assert path.suffix in allowed_suffixes, (
                f"{package}/{path.name}: 插件包只许 manifest/文档/compose,上游代码零入库"
            )
