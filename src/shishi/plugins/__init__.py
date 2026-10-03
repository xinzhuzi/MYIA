"""MYIA 插件市场基建:plugin.yaml 规范、版本矩阵、装卸与降级加载.

公开接口(PRD 10-01-v03-plugin-market 基建部分):

- :class:`~shishi.plugins.manifest.PluginManifest` — 市场 manifest(plugin.yaml)
  的规范模型;:func:`~shishi.plugins.manifest.load_manifest_file` fail-fast 加载;
- :mod:`~shishi.plugins.versioning` — ``compatible`` 版本矩阵(兼容 myia 版本
  范围)的极简 spec 与 :class:`~shishi.plugins.versioning.VersionRange`;
- :class:`~shishi.plugins.installed.InstalledPluginStore` — 已安装插件仓的扫描
  与装卸(list/install/remove 的底座);
- :func:`~shishi.plugins.installed.check_category_plugin` — 品类 ``plugin:`` 节
  的启动自检:只产 warning 级 :class:`~shishi.plugins.installed.PluginFinding`,
  任何插件装不上/配置坏/remote 不可达都不拦核心流水线(security-baseline 铁律);
- :func:`~shishi.plugins.installed.check_remote_modes` — remote 侧检查
  (token 钥匙链存在性 + opt-in 端点探测),品类节与 manifest 两用。

分层:cli → plugins → schema → secrets,单向依赖;双模式的字段模型
(local compose / remote endpoint+keychain token)定义在
:mod:`shishi.schema`(品类 plugin 节与 manifest 共用,规则零漂移)。
"""

from __future__ import annotations

from shishi.plugins.installed import (
    DEFAULT_INSTALL_ROOT,
    INSTALL_ROOT_ENV,
    InstalledEntry,
    InstalledPluginStore,
    PluginFinding,
    PluginStoreError,
    check_category_plugin,
    check_remote_modes,
    default_install_root,
)
from shishi.plugins.manifest import (
    MANIFEST_FILENAME,
    ManifestAdapterConfig,
    ManifestInstallConfig,
    ManifestVendorConfig,
    PluginManifest,
    TIER_TOKENS,
    find_manifest_file,
    load_manifest,
    load_manifest_file,
)
from shishi.plugins.versioning import VersionRange, VersionSpecError, compare_versions, parse_version

__all__ = [
    "DEFAULT_INSTALL_ROOT",
    "INSTALL_ROOT_ENV",
    "MANIFEST_FILENAME",
    "InstalledEntry",
    "InstalledPluginStore",
    "ManifestAdapterConfig",
    "ManifestInstallConfig",
    "ManifestVendorConfig",
    "PluginFinding",
    "PluginManifest",
    "PluginStoreError",
    "TIER_TOKENS",
    "VersionRange",
    "VersionSpecError",
    "check_category_plugin",
    "check_remote_modes",
    "compare_versions",
    "default_install_root",
    "find_manifest_file",
    "load_manifest",
    "load_manifest_file",
    "parse_version",
]
