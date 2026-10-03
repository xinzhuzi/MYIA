"""已安装插件仓:扫描 / 装卸 / 品类侧启动自检(加载失败→结构化 finding,绝不拦核心).

安装布局:每个插件是安装根目录下的一个子目录(目录名 == manifest id),根下
``plugin.yaml`` 即 manifest(:mod:`shishi.plugins.manifest`)。默认安装根
``~/.myia/plugins``,环境变量 ``MYIA_PLUGIN_DIR`` 可覆盖(桌面端与 CLI 共用
同一解析,:func:`default_install_root`)。

铁律(security-baseline spec):**任何 plugin 装不上 / 配置坏 / remote 不可达,
核心流水线必须照常跑通。** 因此:

- 所有扫描路径(:meth:`InstalledPluginStore.entries` / ``locate``)返回
  「条目 + 结构化 findings」,坏目录/坏 manifest 照常成条目,绝不抛异常;
- :func:`check_category_plugin` 产出的 finding 一律 warning 级(自动降级
  语义,logging spec:WARNING = 自动恢复的异常),调用方落日志/诊断,
  不参与退出码;函数本身吞掉一切意外转为 finding;
- remote 端点探测(:func:`check_remote_modes` 的 ``probe_remote=True``)是
  显式 opt-in 的诊断动作 —— run 启动自检默认零网络,不拖慢核心路径。

装卸(:meth:`InstalledPluginStore.install` / ``remove``)是唯一 fail-fast 的
操作面:manifest 坏 / 版本矩阵不兼容 / 已装 / 未装都结构化拒绝
(:class:`PluginStoreError`,CLI 退出码 1),绝不半装半卸。凭据红线:安装即
原样拷贝目录,compose 文件零明文凭据;remote token 只以 keychain 引用存在。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

import httpx

import shishi
from shishi.plugins.manifest import (
    MANIFEST_FILENAME,
    PluginManifest,
    find_manifest_file,
    load_manifest_file,
)
from shishi.plugins.versioning import VersionRange
from shishi.schema import (
    _PLUGIN_ID_RE,
    CategoryPluginConfig,
    SchemaValueError,
    parse_secret_value,
)
from shishi.secrets import SECRET_SERVICE, KeychainBackend

__all__ = [
    "DEFAULT_INSTALL_ROOT",
    "INSTALL_ROOT_ENV",
    "InstalledEntry",
    "InstalledPluginStore",
    "PluginFinding",
    "PluginStoreError",
    "check_category_plugin",
    "check_remote_modes",
    "default_install_root",
]

logger = logging.getLogger(__name__)

#: 默认安装根(桌面用户的每用户目录)。
DEFAULT_INSTALL_ROOT = Path.home() / ".myia" / "plugins"
#: 安装根的环境变量覆盖名。
INSTALL_ROOT_ENV = "MYIA_PLUGIN_DIR"
#: remote 端点探测的默认超时秒数(显式 opt-in 的诊断动作)。
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


def default_install_root() -> Path:
    """解析安装根:环境变量 ``MYIA_PLUGIN_DIR`` 优先,否则 ``~/.myia/plugins``。"""
    override = os.environ.get(INSTALL_ROOT_ENV)
    return Path(override).expanduser() if override else DEFAULT_INSTALL_ROOT


def myia_version() -> str:
    """当前 myia 核心版本(版本矩阵比对的被检方;独立函数便于测试替换)。"""
    return shishi.__version__


# ---------------------------------------------------------------------------
# Structured findings / errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginFinding:
    """一条结构化插件诊断(doctor findings 同款形状:severity/scope/code/message)。

    ``detail`` 可选承载机器可读明细(如 manifest 校验错误列表),供 agent
    自修消费;``message`` 是中文人读原因。
    """

    severity: str
    scope: str
    code: str
    message: str
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "scope": self.scope,
            "code": self.code,
            "message": self.message,
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class PluginStoreError(RuntimeError):
    """插件仓操作失败(install/remove 的结构化错误,CLI 退出码 1 语义)。

    Attributes:
        code: ``invalid_source`` | ``manifest_invalid`` | ``incompatible_version``
            | ``already_installed`` | ``not_installed`` | ``invalid_plugin_id``
            | ``io_error``(装卸阶段的文件系统失败统一包裹,绝不裸逃)。
        errors: manifest 校验失败的结构化明细(:meth:`LoadError.to_dict` 形状)。
    """

    def __init__(self, code: str, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.errors: list[dict[str, Any]] = list(errors or [])


# ---------------------------------------------------------------------------
# Installed store
# ---------------------------------------------------------------------------


@dataclass
class InstalledEntry:
    """安装根下一个子目录的扫描结果(manifest 坏也照常成条目,findings 带原因)。"""

    path: Path
    dir_name: str
    manifest: PluginManifest | None = None
    findings: list[PluginFinding] = field(default_factory=list)

    @property
    def plugin_id(self) -> str | None:
        return self.manifest.id if self.manifest is not None else None

    @property
    def compatible_current(self) -> bool | None:
        """当前 myia 是否落在兼容范围;manifest 缺失时 None(无从判断)。"""
        if self.manifest is None:
            return None
        return VersionRange(self.manifest.compatible).contains(myia_version())

    def to_dict(self) -> dict[str, Any]:
        manifest = self.manifest
        return {
            "id": self.plugin_id,
            "dir_name": self.dir_name,
            "loaded": manifest is not None,
            "path": str(self.path),
            "name": manifest.name if manifest else None,
            "version": manifest.version if manifest else None,
            "compatible": manifest.compatible if manifest else None,
            "compatible_current": self.compatible_current,
            # v1.1 分级(desktop/remote/server-only;manifest 缺失时无从判断)。
            "tier": manifest.tier if manifest else None,
            "requires": list(manifest.requires) if manifest else [],
            "provides": list(manifest.provides) if manifest else [],
            "modes": manifest.modes.model_dump() if manifest else None,
            "install_source": manifest.install.source if manifest else None,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class InstalledPluginStore:
    """安装根目录的扫描与装卸;扫描路径零异常上抛,装卸路径 fail-fast。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root).expanduser() if root is not None else default_install_root()

    @property
    def root(self) -> Path:
        return self._root

    # -- 扫描(绝不抛) -----------------------------------------------------

    def entries(self) -> list[InstalledEntry]:
        """扫描安装根(目录名序)。根不存在 = 空清单(未装任何插件是正常态)。"""
        if not self._root.is_dir():
            return []
        found: list[InstalledEntry] = []
        for child in sorted(self._root.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or not child.is_dir():
                continue  # 隐藏目录/散落文件(如 .DS_Store)不当成插件尝试
            found.append(self._scan_entry(child))
        return found

    def _scan_entry(self, path: Path) -> InstalledEntry:
        entry = InstalledEntry(path=path, dir_name=path.name)
        manifest_file = find_manifest_file(path)
        if manifest_file is None:
            entry.findings.append(
                PluginFinding(
                    "warning",
                    f"plugin:{path.name}",
                    "manifest_missing",
                    f"安装目录 {path} 下没有 {MANIFEST_FILENAME},不是完整的插件安装;"
                    "可删除该目录或补齐 manifest",
                )
            )
            return entry
        try:
            entry.manifest = load_manifest_file(manifest_file)
        except Exception as exc:  # noqa: BLE001 - LoadError 与意外 IO 一律降级为 finding
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            detail = exc.to_dict() if hasattr(exc, "to_dict") else None
            entry.findings.append(
                PluginFinding(
                    "error",
                    f"plugin:{path.name}",
                    "manifest_invalid",
                    f"插件 manifest 校验失败({manifest_file}): {first}",
                    detail=detail,
                )
            )
            return entry
        assert entry.manifest is not None
        if entry.manifest.id != path.name:
            entry.findings.append(
                PluginFinding(
                    "warning",
                    f"plugin:{path.name}",
                    "id_mismatch",
                    f"目录名 {path.name!r} 与 manifest id {entry.manifest.id!r} 不一致(按 manifest id 为准)",
                )
            )
        if entry.compatible_current is False:
            entry.findings.append(
                PluginFinding(
                    "warning",
                    f"plugin:{entry.manifest.id}",
                    "incompatible_version",
                    f"插件要求 myia {entry.manifest.compatible},当前 myia {myia_version()};"
                    "该插件将被跳过,核心流水线不受影响",
                )
            )
        return entry

    def locate(self, plugin_id: str) -> InstalledEntry | None:
        """按 id 找已装条目;manifest 坏的目录按目录名兜底可定位(装卸/诊断需要)。"""
        for entry in self.entries():
            if entry.plugin_id == plugin_id or (entry.manifest is None and entry.dir_name == plugin_id):
                return entry
        return None

    # -- 装卸(fail-fast,结构化拒绝) ---------------------------------------

    def install(
        self,
        source: str | Path,
        *,
        force: bool = False,
        current_version: str | None = None,
    ) -> dict[str, Any]:
        """把一个插件目录安装进仓:manifest 校验 → 版本矩阵检查 → 整目录拷贝。

        Args:
            source: 插件目录(含 ``plugin.yaml``)或 ``plugin.yaml`` 文件路径。
            force: 目标已存在时覆盖;版本矩阵不兼容时强制安装(warning 语义)。
            current_version: 版本矩阵比对用的当前 myia 版本;None = 实际版本。

        Returns:
            安装结果 dict(id/name/version/compatible/compatible_current/
            forced/source/path),``myia plugin install --json`` 直出。

        Raises:
            PluginStoreError: 结构化失败 —— 来源不存在/没有 manifest/manifest
                校验失败/已装未 force/版本不兼容未 force。绝不半装。
        """
        plugin_root = self._resolve_source(source)
        manifest_file = find_manifest_file(plugin_root)
        if manifest_file is None:
            raise PluginStoreError(
                "invalid_source", f"来源 {plugin_root} 下没有 {MANIFEST_FILENAME},不是可安装的插件目录"
            )
        try:
            manifest = load_manifest_file(manifest_file)
        except Exception as exc:  # noqa: BLE001 - LoadError 与意外 IO 统一走结构化拒绝
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            detail = exc.to_dict() if hasattr(exc, "to_dict") else None
            raise PluginStoreError(
                "manifest_invalid",
                f"插件 manifest 校验失败({manifest_file}): {first}",
                errors=detail.get("errors") if isinstance(detail, dict) else None,
            ) from exc
        current = current_version if current_version is not None else myia_version()
        range_ok = VersionRange(manifest.compatible).contains(current)
        # manifest id 已过 _PLUGIN_ID_RE(仅小写字母/数字/_-),无路径穿越可能。
        dest = self._root / manifest.id
        # source==dest(或 source 在 dest 内):覆盖安装会先 rmtree(dest),
        # 把「源」一并删掉 —— 插件被半卸且 copytree 报裸 FileNotFoundError。
        # `plugin list --json` 输出的正是已装 path,agent 拿它做「重装」必踩,
        # 故在此结构化拒绝(error-handling spec:错误必须结构化)。
        source_resolved = Path(plugin_root).resolve()
        dest_resolved = dest.resolve()
        if source_resolved == dest_resolved or source_resolved.is_relative_to(dest_resolved):
            raise PluginStoreError(
                "invalid_source",
                f"安装来源 {plugin_root} 就是(或位于)安装目标 {dest} 内:"
                "覆盖安装会先删除目标目录,连源一起删掉。请从插件原始目录安装,"
                "或用 --dir 指定其他安装根",
            )
        if dest.exists() and not force:
            raise PluginStoreError(
                "already_installed", f"插件 {manifest.id} 已安装于 {dest};覆盖安装请加 --force"
            )
        if not range_ok and not force:
            raise PluginStoreError(
                "incompatible_version",
                f"插件 {manifest.id} 要求 myia {manifest.compatible},当前 myia {current};"
                "请升级 myia 或选择兼容版本,确认风险后可 --force 强制安装",
            )
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            if dest.exists():  # force 覆盖:先清后拷,避免陈旧文件残留
                shutil.rmtree(dest)
            shutil.copytree(plugin_root, dest)
        except OSError as exc:
            # IO 失败结构化(权限/磁盘满/跨设备等),绝不裸 traceback 打穿
            # --json 契约;copytree 中途失败可能留半装目录 —— 尽力清残,
            # 兑现模块承诺「绝不半装半卸」。
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            raise PluginStoreError(
                "io_error", f"插件安装 IO 失败({plugin_root} → {dest}): {exc}"
            ) from exc
        logger.info("插件已安装 id=%s version=%s path=%s", manifest.id, manifest.version, dest)
        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "compatible": manifest.compatible,
            "compatible_current": range_ok,
            "forced": bool(force),
            "source": str(plugin_root),
            "path": str(dest),
        }

    def remove(self, plugin_id: str) -> dict[str, Any]:
        """按 id 移除已安装插件;不存在 → 结构化错误(manifest 坏也能按目录名删)。

        Raises:
            PluginStoreError: ``invalid_plugin_id``(防路径穿越)或
                ``not_installed``。
        """
        if not _safe_plugin_id(plugin_id):
            raise PluginStoreError(
                "invalid_plugin_id", f"插件 id 非法: {plugin_id!r}(应为 manifest 里的 id)"
            )
        target = self._root / plugin_id
        if not target.is_dir():
            raise PluginStoreError(
                "not_installed", f"插件 {plugin_id} 未安装于 {self._root},无法移除"
            )
        try:
            shutil.rmtree(target)  # 目标是指向别处的符号链接时 rmtree 拒绝 → 结构化
        except OSError as exc:
            raise PluginStoreError(
                "io_error", f"插件移除 IO 失败({target}): {exc}"
            ) from exc
        logger.info("插件已移除 id=%s path=%s", plugin_id, target)
        return {"id": plugin_id, "path": str(target), "removed": True}

    def _resolve_source(self, source: str | Path) -> Path:
        """install 来源归一:plugin.yaml 文件 → 其目录;缺失/异物 → 结构化错误。"""
        path = Path(source).expanduser()
        if path.is_file():
            if path.name in (MANIFEST_FILENAME, "plugin.yml"):
                return path.parent
            raise PluginStoreError(
                "invalid_source", f"安装来源 {path} 不是 {MANIFEST_FILENAME};请传插件目录或其 plugin.yaml"
            )
        if path.is_dir():
            return path
        raise PluginStoreError("invalid_source", f"安装来源不存在: {path}")


def _safe_plugin_id(plugin_id: str) -> bool:
    """id 只允许小写字母/数字/连字符/下划线(2-64 字符),杜绝路径穿越。"""
    return isinstance(plugin_id, str) and bool(_PLUGIN_ID_RE.match(plugin_id))


# ---------------------------------------------------------------------------
# 启动自检(品类 plugin: 节 → warning findings,绝不拦核心)
# ---------------------------------------------------------------------------


def check_category_plugin(
    section: CategoryPluginConfig,
    store: InstalledPluginStore,
    *,
    backend: KeychainBackend | None = None,
    probe_remote: bool = False,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    current_version: str | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> list[PluginFinding]:
    """品类 ``plugin:`` 节的启动自检:只产 warning 级 finding,永不抛异常。

    检查项(全部可降级,逐项独立):

    1. 插件是否已装(未装 → ``plugin_not_installed``,品类按无插件继续跑);
    2. 已装目录的 manifest 是否可读(坏 → 原样带上 store 的 findings);
    3. 版本矩阵是否兼容当前 myia(不兼容 → ``incompatible_version``,插件跳过);
    4. remote token 在钥匙链是否存在(``backend=None`` 时跳过 —— 无法核验不是
       问题);
    5. ``probe_remote=True`` 时 remote 端点可达性(显式 opt-in,run 路径默认
       零网络)。
    """
    scope = f"plugin:{section.id}"
    try:
        entry = store.locate(section.id)
    except Exception as exc:  # noqa: BLE001 - 自检任何意外都降级为 finding
        return [
            PluginFinding(
                "warning",
                scope,
                "plugin_selfcheck_failed",
                f"插件自检失败(已跳过,核心流水线不受影响): {exc}",
            )
        ]
    if entry is None:
        return [
            PluginFinding(
                "warning",
                scope,
                "plugin_not_installed",
                f"插件 {section.id} 未安装(安装根 {store.root});本品类按无插件继续跑,"
                "其 local/remote 服务能力不可用。安装:myia plugin install <插件目录>",
            )
        ]
    # 已装目录自身的 findings(manifest 坏/版本不兼容)全部降为 warning 透出:
    # 插件坏是「自动降级」不是「核心病」,绝不以 error 级拦 run。
    findings = [dataclass_replace(finding, severity="warning") for finding in entry.findings]
    if entry.manifest is None:
        return findings
    current = current_version if current_version is not None else myia_version()
    if not VersionRange(entry.manifest.compatible).contains(current):
        findings.append(
            PluginFinding(
                "warning",
                scope,
                "incompatible_version",
                f"已安装插件要求 myia {entry.manifest.compatible},当前 myia {current};"
                "已跳过该插件,核心流水线不受影响",
            )
        )
    findings.extend(
        check_remote_modes(
            section,
            backend=backend,
            probe_remote=probe_remote,
            timeout=timeout,
            client_factory=client_factory,
        )
    )
    return findings


def check_remote_modes(
    subject: CategoryPluginConfig | PluginManifest,
    *,
    backend: KeychainBackend | None = None,
    probe_remote: bool = False,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> list[PluginFinding]:
    """对一个声明了 remote 模式的对象(品类 plugin 节或 manifest)做远端侧检查。

    ``subject`` 按 duck-typing 取 ``id`` 与 ``modes.remote``(品类节与 manifest
    的 modes 模型同源,形状一致)。token 存在性核验只在 ``backend`` 可用时做;
    端点探测只在 ``probe_remote=True`` 时做(4xx 算可达 —— 端点活着,只是鉴权
    问题,归 token 检查管)。
    """
    remote = subject.modes.remote
    if remote is None:
        return []
    scope = f"plugin:{subject.id}"
    findings: list[PluginFinding] = []
    if remote.token is not None and backend is not None:
        ref = None
        try:
            ref = parse_secret_value(remote.token, label="modes.remote.token", allow_scheme=False)
        except SchemaValueError:
            ref = None  # schema 层已拒;防御直达构造的模型
        if ref is not None and ref.kind == "keychain":
            try:
                exists: bool | None = backend.get_password(SECRET_SERVICE, ref.name) is not None
            except Exception as exc:  # noqa: BLE001 - 钥匙链异常 = 无法核验,不是插件问题
                logger.debug("钥匙链存在性核验失败 name=%s: %s", ref.name, exc)
                exists = None
            if exists is False:
                findings.append(
                    PluginFinding(
                        "warning",
                        scope,
                        "plugin_token_missing",
                        f"remote token 引用的钥匙链凭据 {ref.name} 不存在;"
                        f"请执行 myia secret set {ref.name} 写入,否则 remote 模式不可用",
                    )
                )
    if probe_remote:
        message = asyncio.run(_probe_endpoint(remote.endpoint, timeout=timeout, client_factory=client_factory))
        if message is not None:
            findings.append(
                PluginFinding(
                    "warning",
                    scope,
                    "plugin_remote_unreachable",
                    f"remote 端点 {remote.endpoint} 不可达:{message};核心流水线不受影响",
                )
            )
    return findings


async def _probe_endpoint(
    endpoint: str,
    *,
    timeout: float,
    client_factory: Callable[..., httpx.AsyncClient],
) -> str | None:
    """探测 remote 端点;返回 None=可达,否则返回中文原因(不抛)。"""
    client = client_factory(timeout=timeout)
    try:
        try:
            response = await asyncio.wait_for(client.get(endpoint), timeout=timeout)
        except TimeoutError:  # py3.11+: asyncio.TimeoutError 即内建 TimeoutError
            return f"超时(>{timeout:.0f}s)"
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        if response.status_code >= 500:
            return f"HTTP {response.status_code}"
        return None
    finally:
        await client.aclose()
