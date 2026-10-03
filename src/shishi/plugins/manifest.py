"""插件市场 manifest(plugin.yaml)规范:pydantic 模型 + fail-fast 加载校验.

一个市场插件 = 一个目录,根下放 ``plugin.yaml``(README、compose 等随目录
分发;实现与重依赖全部留在插件侧,核心仓库只装市场目录与加载器)。manifest
字段(PRD 10-01-v03-plugin-market):

- ``id`` / ``name`` / ``version`` — 插件标识(安装目录名与其一致)、显示名与
  插件自身语义化版本;
- ``compatible`` — 兼容的 myia 核心版本范围(版本矩阵,语法见
  :mod:`shishi.plugins.versioning`);
- ``tier`` — v1.1 插件分级(:data:`TIER_TOKENS`,缺省 ``desktop``):
  ``desktop`` 桌面默认集(源码/进程内能力,零 docker);``remote`` 桌面可选
  (经已部署服务接入,零 docker,不进默认集);``server-only`` 桌面默认集
  移出(仅服务端可选部署,如重 Web 服务/不可进程化上游);
- ``requires`` — 宿主能力要求(封闭词表 :data:`shishi.schema.REQUIRES_TOKENS`,
  当前只有 ``docker``;``requires: docker`` 与 ``[docker]`` 两种写法都收);
- ``provides`` — 提供的能力名(小写标识符,品类侧与目录索引引用它);
- ``modes`` — v1.7 双模式:``local``(本机 Docker compose)/ ``remote``
  (endpoint + keychain token 引用)。模型直接复用品类顶层 plugin 节的
  :class:`shishi.schema.PluginModesConfig` —— 凭据规则(endpoint http(s)、
  token 只走 ``keychain:myia/<scope>/<name>``)一处定义零漂移;
- ``install`` — 插件来源(``source``:git/https URL 或本地路径,供人与 agent
  追溯;实际装卸走 ``myia plugin install <目录>``)。

v1.1 源码型插件(PRD 10-02-v11-plugins-source-arch,样板 myia-osint)新增
两个**可选**节——旧包(纯 manifest/文档/compose)不声明即缺省 ``None``,
向后兼容;新包声明后 fail-fast 校验:

- ``vendor`` — 上游源码的 vendor 声明:``source``(上游公开 git 仓库
  https URL;GPL/AGPL 上游只以 submodule 引用、零代码复制)、``path``
  (相对插件目录的 submodule 路径,如 ``vendor/Photon``)、``pin``
  (钉住的 commit SHA,7-64 位十六进制)、``license``(上游许可证标识,
  如 ``GPL-3.0``);
- ``adapter`` — MYIA 侧适配器声明:``entry``(相对插件目录的适配器入口
  .py 文件)、``mode``(``subprocess`` 子进程调用 | ``in_process`` 进程内
  import,缺省 ``subprocess``)。适配器装不上/上游 vendor 缺失 → 结构化
  降级,绝不拦核心流水线(铁律)。

错误契约与品类 YAML 一致::class:`~shishi.schema.LoadError` 携带结构化明细
(字段路径 + 错误类 + 中文原因),``myia plugin install --json`` 与修复 agent
直接消费;未知字段 fail-fast,不做静默忽略。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shishi.plugins.versioning import VersionRange, VersionSpecError
from shishi.schema import (
    CATEGORY_ID_RE,
    # 私有符号受控复用(与 cli.py 复用 _SECRET_REF_RE 同一先例):防两处漂移。
    _PLUGIN_ID_RE,
    LoadError,
    LoadErrorDetail,
    PluginModesConfig,
    SchemaValueError,
    ShortStr,
    _pydantic_error_detail,
    normalize_plugin_requires,
    read_yaml_document,
)

__all__ = [
    "MANIFEST_FILENAME",
    "ManifestAdapterConfig",
    "ManifestInstallConfig",
    "ManifestVendorConfig",
    "PluginManifest",
    "TIER_TOKENS",
    "find_manifest_file",
    "load_manifest",
    "load_manifest_file",
]

#: manifest 的规范文件名(plugin.yml 为兼容别名)。
MANIFEST_FILENAME = "plugin.yaml"
_MANIFEST_FILENAMES = (MANIFEST_FILENAME, "plugin.yml")

#: v1.1 插件分级词表(PRD 10-02-v11-plugins-source-arch):桌面优先 ——
#: desktop 桌面默认集(源码/进程内,零 docker)/ remote 桌面可选(已部署
#: 服务接入)/ server-only 桌面默认集移出(仅服务端可选部署)。
TIER_TOKENS = ("desktop", "remote", "server-only")

#: 插件自身版本:语义化版本(主.次.修,允许 pre-release/build 后缀)。
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")

#: vendor.pin:git commit SHA(sha1 40 位 / sha256 仓库 64 位;允许缩写 ≥7 位)。
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


class _StrictManifestModel(BaseModel):
    """manifest 子模型基类:未知字段 fail-fast(与品类 schema 同一纪律)。"""

    model_config = ConfigDict(extra="forbid")


class ManifestInstallConfig(_StrictManifestModel):
    """插件的获取来源说明:``source`` 指向插件仓库/发行位置。"""

    source: str = Field(min_length=1, max_length=512)


def _safe_relative_path(value: str, *, field_name: str) -> str:
    """插件目录内的相对路径守卫:拒绝绝对路径与 ``..`` 越界(公开仓库红线)."""
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SchemaValueError(
            "unsafe_relative_path",
            f"{field_name} 必须是插件目录内的相对路径(禁止绝对路径与 .. 越界),当前为 {value!r}",
        )
    return value


class ManifestVendorConfig(_StrictManifestModel):
    """上游源码的 vendor 声明(git submodule pin 优先,GPL 上游零代码复制)."""

    source: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=256)
    pin: str | None = Field(default=None, min_length=7, max_length=64)
    license: str | None = Field(default=None, min_length=2, max_length=64)

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise SchemaValueError(
                "invalid_vendor_source",
                f"vendor.source 必须是上游公开仓库的 https URL(零代码复制,只作 submodule 引用),当前为 {value!r}",
            )
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _safe_relative_path(value, field_name="vendor.path")

    @field_validator("pin")
    @classmethod
    def _check_pin(cls, value: str | None) -> str | None:
        if value is not None and not _COMMIT_SHA_RE.match(value):
            raise SchemaValueError(
                "invalid_vendor_pin",
                f"vendor.pin 应为 git commit SHA(7-64 位十六进制),当前为 {value!r}",
            )
        return value


class ManifestAdapterConfig(_StrictManifestModel):
    """MYIA 侧适配器声明:进程内 import 或子进程调用上游(v1.1 源码型插件)."""

    entry: str = Field(min_length=1, max_length=256)
    mode: Literal["subprocess", "in_process"] = "subprocess"

    @field_validator("entry")
    @classmethod
    def _check_entry(cls, value: str) -> str:
        value = _safe_relative_path(value, field_name="adapter.entry")
        if not value.endswith(".py"):
            raise SchemaValueError(
                "invalid_adapter_entry",
                f"adapter.entry 应为插件目录内的 .py 适配器入口文件,当前为 {value!r}",
            )
        return value


class PluginManifest(_StrictManifestModel):
    """一个市场插件的 plugin.yaml(规范全字段,见模块 docstring)。

    未知字段 fail-fast(``extra="forbid"``)。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: ShortStr
    version: str
    compatible: str
    # v1.1 分级(缺省 desktop = 桌面默认集,向后兼容旧包不声明):
    tier: str = "desktop"
    requires: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    modes: PluginModesConfig
    install: ManifestInstallConfig
    # v1.1 源码型插件扩展(可选;旧包缺省 None,向后兼容):
    vendor: ManifestVendorConfig | None = None
    adapter: ManifestAdapterConfig | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.match(value):
            raise SchemaValueError(
                "invalid_plugin_id",
                f"插件 id 只允许小写字母/数字/连字符/下划线且字母数字开头(2-64 字符,惯例 myia-<名称>),"
                f"当前为 {value!r}",
            )
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not _SEMVER_RE.match(value):
            raise SchemaValueError(
                "invalid_semver",
                f"插件 version 应为语义化版本(主.次.修,如 1.0.0),当前为 {value!r}",
            )
        return value

    @field_validator("compatible")
    @classmethod
    def _check_compatible(cls, value: str) -> str:
        try:
            VersionRange(value)
        except VersionSpecError as exc:
            raise SchemaValueError(exc.code, str(exc)) from exc
        return value

    @field_validator("tier")
    @classmethod
    def _check_tier(cls, value: str) -> str:
        if value not in TIER_TOKENS:
            raise SchemaValueError(
                "invalid_tier",
                f"tier 取值 {value!r} 不在允许范围 {list(TIER_TOKENS)} 内"
                "(desktop=桌面默认集 / remote=桌面可选 / server-only=桌面默认集移出)",
            )
        return value

    @field_validator("requires", mode="before")
    @classmethod
    def _normalize_requires(cls, value: Any) -> list[str]:
        return normalize_plugin_requires(value)

    @field_validator("provides")
    @classmethod
    def _check_provides(cls, value: list[str]) -> list[str]:
        """能力名走品类 id 同一套标识符规则(CATEGORY_ID_RE 复用,防两处漂移)。"""
        for token in value:
            if not CATEGORY_ID_RE.match(token):
                raise SchemaValueError(
                    "invalid_provides_token",
                    f"provides 取值 {token!r} 应为小写字母/数字/连字符/下划线且字母数字开头(1-64 字符)",
                )
        if len(set(value)) != len(value):
            raise SchemaValueError("duplicate_provides", f"provides 存在重复项: {value}")
        return value


def load_manifest(data: Mapping[str, Any], *, source: str | None = None) -> PluginManifest:
    """Validate a parsed plugin.yaml mapping into :class:`PluginManifest`.

    Raises:
        LoadError: 不是映射,或任一字段校验失败(一次性收集全部错误)。
    """
    if not isinstance(data, Mapping):
        raise LoadError(
            [
                LoadErrorDetail(
                    "$",
                    "invalid_root",
                    f"插件 manifest 必须是键值映射,当前为 {type(data).__name__}",
                )
            ],
            source=source,
        )
    try:
        return PluginManifest.model_validate(dict(data))
    except ValidationError as exc:
        raise LoadError([_pydantic_error_detail(err) for err in exc.errors()], source=source) from exc


def find_manifest_file(plugin_dir: str | Path) -> Path | None:
    """Return the manifest file inside ``plugin_dir`` (plugin.yaml 优先),else None."""
    root = Path(plugin_dir)
    for name in _MANIFEST_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def load_manifest_file(path: str | Path) -> PluginManifest:
    """Read, parse and validate one plugin.yaml file.

    Raises:
        LoadError: 文件缺失/不可读/非 UTF-8/YAML 语法坏(含重复键)/为空/
            字段校验失败。
    """
    file_path = Path(path)
    data = read_yaml_document(file_path)
    if data is None:
        raise LoadError(
            [LoadErrorDetail("$", "invalid_root", f"插件 manifest 文件为空({file_path})")],
            source=str(file_path),
        )
    return load_manifest(data, source=str(file_path))
