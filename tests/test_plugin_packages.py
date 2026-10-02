"""官方六件场景件(plugin packages)的封装契约测试(PRD 10-01-v03-plugin-market
及其 v1.1 架构转向,PRD 10-02-v11-plugins-source-arch).

六个 ``plugins/<id>/`` 目录是市场插件包:每包含 ``plugin.yaml``(manifest,
规范见 :mod:`myia.plugins.manifest`)+ README + 桌面路径声明。三条被钉住的
契约:

1. 六件 manifest 全部过真实校验入口 :func:`load_manifest_file`:id==目录名、
   版本矩阵兼容当前 myia、**tier 分级**(desktop/remote/server-only)与
   定级一致、remote 端点只用 example.com 占位域(公开仓库红线);
2. **remote 模式 mock 往返**:MockTransport 拦截端点探测,零真实网络;
3. **铁律呼应**:损坏/未装的插件包只降级为 warning finding,核心品类加载
   与 Pipeline 构造完全无感(与 tests/test_plugins_system.py 同一铁律,
   这里在真实插件包的尺度上再钉一遍)。

v1.1 架构转向(桌面优先,零 docker):官方包 manifest 不再声明 local
compose 模式,``plugins/`` 目录零 compose 文件;场景件的本地部署文件统一
收在 ``docker/plugins/<id>/compose.yml``(服务端可选路径,凭据红线照钉)。

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
#: 场景件的服务端可选部署(v1.1 起迁出插件目录):docker/plugins/<id>/compose.yml。
DOCKER_PLUGINS_DIR = REPO_ROOT / "docker" / "plugins"

#: 六件官方场景件(目录名 == manifest id)。
OFFICIAL_PACKAGES = (
    "myia-proxy",
    "myia-osint",
    "myia-douyin",
    "myia-monitor",
    "myia-maxun",
    "myia-credentials",
)

#: v1.1 定级建议(PRD 10-02-v11-plugins-source-arch 复核表)钉死的期望分级。
EXPECTED_TIERS = {
    "myia-proxy": "desktop",
    "myia-osint": "desktop",
    "myia-monitor": "remote",
    "myia-credentials": "remote",
    "myia-douyin": "server-only",
    "myia-maxun": "server-only",
}


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
        """v1.1 起所有官方包都不再携带本地部署文件(不只 credentials)。"""
        manifest = load_package("myia-credentials")
        assert manifest.modes.local is None
        assert manifest.modes.remote is not None
        assert not (package_dir("myia-credentials") / "docker-compose.yml").exists()

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_v11_tier_matches_prd_table(self, package: str):
        """v1.1 分级与 PRD 定级表一字不差(myia plugin list 据此展示)。"""
        manifest = load_package(package)
        assert manifest.tier == EXPECTED_TIERS[package]
        assert manifest.tier in ("desktop", "remote", "server-only")

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_no_manifest_declares_local_compose_anymore(self, package: str):
        """桌面优先(v1.1):manifest 不再声明 local compose 模式。"""
        manifest = load_package(package)
        assert manifest.modes.local is None, (
            f"{package}: v1.1 起插件目录不携带本地部署 compose,manifest 不得声明 modes.local"
        )

    def test_plugins_dir_holds_no_compose_files(self):
        """铁验收:plugins/ 目录 grep 不到 docker-compose,也没有任何 compose 文件."""
        compose_files = [
            path
            for path in PLUGINS_DIR.rglob("*")
            if path.is_file()
            and path.suffix in (".yml", ".yaml")
            and "docker-compose" in path.name
            and "vendor" not in path.relative_to(PLUGINS_DIR).parts
        ]
        assert compose_files == [], f"plugins/ 里发现 compose 文件: {compose_files}"
        for path in PLUGINS_DIR.rglob("*.md"):
            if "vendor" in path.relative_to(PLUGINS_DIR).parts:
                continue
            assert "docker-compose" not in path.read_text(encoding="utf-8"), (
                f"{path}: 文档提及 docker-compose(部署文件应指向 docker/plugins/)"
            )

    def test_docker_plugins_dir_holds_exactly_the_five_composes(self):
        """迁出的部署文件落在 docker/plugins/<id>/compose.yml,五件不多不少。"""
        expected = {"myia-proxy", "myia-osint", "myia-monitor", "myia-douyin", "myia-maxun"}
        found = {path.parent.name for path in DOCKER_PLUGINS_DIR.glob("*/compose.yml")}
        assert found == expected

    @pytest.mark.parametrize("package", OFFICIAL_PACKAGES)
    def test_readme_documents_desktop_first(self, package: str):
        """README 桌面路径优先(源码/进程内或 remote);server-only 说明移出原因."""
        readme = (package_dir(package) / "README.md").read_text(encoding="utf-8")
        manifest = load_package(package)
        assert "endpoint" in readme, f"{package}: README 缺 remote(填 endpoint+token)路径"
        if manifest.tier == "desktop":
            assert "桌面路径" in readme, f"{package}: desktop 分级 README 缺桌面路径说明"
        if manifest.tier == "server-only":
            assert "server-only" in readme and "移出" in readme, (
                f"{package}: server-only 分级 README 须说明桌面默认集移出原因"
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


def _docker_compose_fixtures() -> list[Path]:
    if not DOCKER_PLUGINS_DIR.is_dir():
        return []
    return sorted(DOCKER_PLUGINS_DIR.glob("*/compose.yml"))


@pytest.mark.parametrize("compose_path", _docker_compose_fixtures())
def test_plugin_compose_parses_and_holds_no_plaintext_secrets(compose_path: Path):
    """服务端 compose(已迁 docker/plugins/)可解析、凭据形键必须 ${VAR} 注入."""
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
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
                    f"{compose_path}: {key} 疑似明文凭据(应为 ${{VAR}} 注入)"
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
        if "vendor" in path.relative_to(target).parts:
            # vendor/ 是上游工作树(submodule pin 635c25a,已全树扫描无内网
            # 地址/裸凭据并记录在案);其内容属上游仓库,不由本仓库红线扫描
            # 负责 —— 本仓库零复制的证据由 gitlink 形状测试负责。
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


#: v1.1 源码型/进程内插件(PRD 10-02-v11-plugins-source-arch):插件目录除
#: manifest/文档外,还允许 MYIA 侧适配器 adapter.py;myia-osint 另有上游
#: submodule 指针目录 vendor/(gitlink,上游代码零入库、零复制);
#: myia-proxy 的适配器是自实现精简版(参照 proxy_pool 思路),零 vendored。
SOURCE_TYPE_PACKAGES = {
    "myia-osint": {"adapter.py", "vendor"},
    "myia-proxy": {"adapter.py"},
}


def test_official_packages_never_reference_their_upstream_by_copying_files():
    """GPL/AGPL 红线的包形状证据:六件包里没有任何上游源码文件被复制入库.

    v1.1 起源码型插件(myia-osint)以 gitlink(submodule)指向上游 ——
    vendor/ 是 submodule 指针而非上游文件;适配器 adapter.py 是 MYIA 侧
    代码。gitlink 形状(160000 == manifest pin)钉在
    tests/test_osint_plugin.py::TestSubmoduleShape。
    """
    allowed_suffixes = {".yaml", ".yml", ".md"}
    for package in OFFICIAL_PACKAGES:
        source_type_extra = SOURCE_TYPE_PACKAGES.get(package, set())
        for path in package_dir(package).iterdir():
            if path.name in source_type_extra:
                continue
            assert path.suffix in allowed_suffixes, (
                f"{package}/{path.name}: 插件包只许 manifest/文档/compose,上游代码零入库"
            )


def test_source_type_vendor_directory_holds_only_the_submodule():
    """源码型样板的 vendor/ 里除 submodule 外零散落文件(无上游复制)."""
    vendor = package_dir("myia-osint") / "vendor"
    assert vendor.is_dir(), "源码型样板应声明 vendor/"
    stray = [
        path.name
        for path in vendor.iterdir()
        if path.name != "Photon" and not path.name.startswith(".")
    ]
    assert stray == [], f"vendor/ 只许放 submodule 目录,发现散落文件:{stray}"
