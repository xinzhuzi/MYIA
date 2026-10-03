"""视觉模型仓管:mlx-community 系 MLX 权重的 清单 / 下载 / 删除 / 激活。

(task 10-03-vision-v2;PRD R1「应用内下载视觉模型」的 Python 能力面——
协议方法在 desktop/entry.py ``image.models.*``,本模块是纯能力实现。)

存储形态:每个模型 = ``<models_root>/<name>/`` 一个目录(``$MYIA_HOME/models/``
平铺;models_root 由调用方解析,home 模式 = 数据根、dev 回退 cwd 相对,
与 vision.yaml 同目录)。下载走 ``huggingface_hub.snapshot_download`` +
``local_dir=`` —— mlx-community 系发布的就是 MLX 量化权重,直下免 convert;
``local_dir`` 形态天然**断点续传**(未完文件落 ``.cache/huggingface/`` 的
``.incomplete``,重复调用自动接续),删目录即弃。

下载前**磁盘预检**:``HfApi.model_info(files_metadata=True)`` 取仓库总量,
与 ``shutil.disk_usage`` 可用空间对照,不足即结构化 ``disk_insufficient``
(绝不落到下载一半才 ENOSPC)。

依赖红线:``huggingface_hub`` 惰性 import(:func:`_import_hub`,extras
``myia[vision]`` 已含)—— 本模块顶层 import 零重依赖,与 ocr/client 的
懒加载契约同门;未装 extras 时结构化 ``hf_unavailable`` 附安装命令。

进度事件:``download_model(on_progress=...)`` 回调 ``(done_bytes,
total_bytes | None)``(逐文件 tqdm 聚合为累计字节,节流防刷屏;
total 来自预检的仓库总量,预检失败时 None = 只报进度不报总量)。
协议侧(desktop/entry.py)把它翻译成 ``image.models.progress`` 事件,
整活跑 sidecar 后台线程(仿 sources.test 先例),绝不阻塞 serve 循环。

错误族(:class:`VisionModelError`,code + message + details,镜像
:class:`myia.vision.settings.VisionConfigError`):``invalid_repo``
(非 ``mlx-community/<name>`` 形态)/ ``invalid_name`` / ``hf_unavailable``
/ ``repo_unreachable`` / ``disk_insufficient`` / ``download_failed`` /
``model_not_found`` / ``model_active_refused``(激活模型拒删)/
``model_exists``(同名完整模型拒重下,提示先删)/ ``model_incomplete``
(半成品目录拒激活)。
"""

from __future__ import annotations

import importlib
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from myia.vision.settings import VisionConfig, load_vision_config, save_vision_config

__all__ = [
    "INSTALL_COMMAND",
    "MLX_COMMUNITY_PREFIX",
    "VisionModelError",
    "activate_model",
    "delete_model",
    "download_model",
    "list_models",
]

#: 模型下载安装命令(未装 huggingface_hub 时的结构化提示)。
INSTALL_COMMAND = "pip install 'myia[vision]'  # 或 uv add 'myia[vision]'"

#: v2 只收 mlx-community 系 MLX 格式权重(直下免 convert;其余命名空间的
#: 仓库多为原始 HF 权重,对 mlx_vlm.server 不可用,结构化拒)。
MLX_COMMUNITY_PREFIX = "mlx-community/"

#: 合法 repo_id:``mlx-community/<name>``(单斜杠,org 固定)。
_REPO_RE = re.compile(r"^mlx-community/[A-Za-z0-9][A-Za-z0-9._-]*$")

#: 合法本地名(目录名安全:字母数字开头,仅 ``.``/``_``/``-``,禁路径分隔)。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: 进度回调节流间隔(秒):逐 chunk 上报会刷爆协议流,只在间隔到点或终态时发。
_PROGRESS_MIN_INTERVAL = 0.5

ProgressCallback = Callable[[int, "int | None"], None]


class VisionModelError(ValueError):
    """模型仓管结构化错误(code + message + details;协议层翻译为应答 error)。

    Attributes:
        code: 见模块头错误族表。
        details: 结构化上下文(磁盘预检带 required/free,激活带 model 路径等)。
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form(协议错误 data / 诊断消费)。"""
        return {"error_type": self.code, "message": str(self), **self.details}


# ---------------------------------------------------------------------------
# 惰性依赖
# ---------------------------------------------------------------------------


def _import_hub() -> Any:
    """惰性加载 ``huggingface_hub``(extras myia[vision];缺装结构化报错)。"""
    try:
        return importlib.import_module("huggingface_hub")
    except ImportError as exc:
        raise VisionModelError(
            "hf_unavailable",
            f"模型下载依赖 huggingface_hub 未安装:请先执行 {INSTALL_COMMAND}",
            details={"package": "huggingface_hub", "install": INSTALL_COMMAND},
        ) from exc


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------


def _dir_bytes(path: Path) -> int:
    """目录递归字节数(跳过隐藏 ``.cache`` 等 —— 未完下载不算已装体量)。"""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            for entry in current.iterdir():
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    stack.append(entry)
                else:
                    total += entry.stat().st_size
        except OSError:
            continue  # 竞态(并发删除):尽力而为
    return total


def _resolved(path: Path | str) -> str:
    """resolve 成可比对的绝对路径串(不存在也能 resolve,防软链误判)。"""
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path)


def _is_complete_model_dir(path: Path) -> bool:
    """完整模型目录 = 顶层含 ``config.json`` 且至少一个 ``*.safetensors``。

    半成品(下载中断残留 / 手工乱拷)天然不满足 —— 清单标 incomplete、
    activate 拒、download 走续传放行,三处共用同一判定。OSError 一律按
    不完整处理(读不了 = 不能信)。
    """
    try:
        if not (path / "config.json").is_file():
            return False
        return any(path.glob("*.safetensors"))
    except OSError:
        return False


def list_models(models_root: Path | str, *, active_path: str | None = None) -> list[dict[str, Any]]:
    """扫描 ``models_root`` 下的已装模型 → ``[{name, path, bytes, active, incomplete}]``。

    模型 = 一级子目录(文件与隐藏目录不算);``bytes`` 为递归字节数(跳过
    隐藏 ``.cache``);``active`` = 目录与 ``active_path``(vision.yaml
    ``local.model``,调用方 lenient 解析)resolve 后全等;``incomplete`` =
    缺 ``config.json`` 或 ``*.safetensors`` 的半成品(10-03-vision-v2 复查:
    半成品目录不得冒充完整模型,UI 标「未完成(可续传)」)。空根 = 合法空表。
    """
    root = Path(models_root)
    if not root.is_dir():
        return []
    active_resolved = _resolved(active_path) if active_path else ""
    models: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        models.append(
            {
                "name": entry.name,
                "path": str(entry.resolve()),
                "bytes": _dir_bytes(entry),
                "active": bool(active_resolved) and _resolved(entry) == active_resolved,
                "incomplete": not _is_complete_model_dir(entry),
            }
        )
    return models


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


class _ProgressTracker:
    """多文件下载进度聚合器:逐文件当前字节 → 累计 ``(done, total)`` 回调。

    huggingface_hub 新版 snapshot 并发下载(``max_workers``>1),bar 状态
    读写必须加锁;``_emit`` 按 :data:`_PROGRESS_MIN_INTERVAL` 节流,终态
    (:meth:`finish`)强制发最后一次。
    """

    def __init__(self, on_progress: ProgressCallback | None, total_bytes: int | None) -> None:
        self._on_progress = on_progress
        self._total = total_bytes
        self._lock = threading.Lock()
        self._bars: dict[int, int] = {}
        self._closed_base = 0
        self._last_emit = 0.0
        self._last_done = 0

    def _bar_update(self, key: int, done: int) -> None:
        with self._lock:
            self._bars[key] = done
            self._emit_locked(force=False)

    def _bar_close(self, key: int, done: int) -> None:
        with self._lock:
            self._bars.pop(key, None)
            self._closed_base += done
            self._emit_locked(force=False)

    def _emit_locked(self, *, force: bool) -> None:
        """发一次进度(调用方持锁;节流 + 尾事件不吞)。"""
        if self._on_progress is None:
            return
        done = self._closed_base + sum(self._bars.values())
        now = time.monotonic()
        if not force and now - self._last_emit < _PROGRESS_MIN_INTERVAL:
            return
        self._last_emit = now
        self._last_done = done
        self._on_progress(done, self._total)

    def finish(self) -> None:
        """终态强制上报(节流可能吞掉最后一次 chunk 更新)。"""
        with self._lock:
            self._emit_locked(force=True)

    def tqdm_class(self) -> Any:
        """huggingface_hub ``tqdm_class`` 适配器(tqdm 缺装时 None,进度降级)。"""
        try:
            from tqdm import tqdm
        except ImportError:
            return None
        tracker = self

        class _HubProgress(tqdm):  # noqa: D106 — hf 逐文件进度 bar 的桩
            def update(self, n: int = 1) -> Any:  # noqa: ANN401 — tqdm 基类签名
                result = super().update(n)
                tracker._bar_update(id(self), self.n)
                return result

            def close(self) -> None:
                # tqdm 会 close 两次(显式 + __del__);disable 门保证单文件字节
                # 只入累计基座一次,否则 __del__ 二次入账把进度翻倍(测试实证)。
                if not self.disable:
                    tracker._bar_close(id(self), self.n)
                super().close()

        return _HubProgress


def _nearest_free_bytes(path: Path) -> int:
    """目标目录(可不存在)所在卷的可用字节 —— 就近取最深存在的祖先探测。"""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            probe = Path(".")
            break
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _validate_repo(repo_id: str) -> None:
    if not isinstance(repo_id, str) or not _REPO_RE.match(repo_id):
        raise VisionModelError(
            "invalid_repo",
            f"repo 必须是 {MLX_COMMUNITY_PREFIX}<name> 形态(mlx-community 系 MLX "
            f"格式权重直下免 convert),当前为 {repo_id!r}",
            details={"repo": repo_id},
        )


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name:
        raise VisionModelError(
            "invalid_name",
            f"模型名不合法(字母数字开头,仅 . _ -,禁路径分隔): {name!r}",
            details={"name": name},
        )


def download_model(
    repo_id: str,
    models_root: Path | str,
    *,
    name: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """下载一个 mlx-community MLX 模型到 ``<models_root>/<name>/``,返回目录。

    流程:repo/name 校验 → 目标目录守卫(同名完整模型 ``model_exists`` 拒;
    半成品/空目录放行 = 续传语义)→ ``HfApi.model_info(files_metadata=True)``
    取仓库总量(磁盘预检数据源)→ ``shutil.disk_usage`` 可用空间对照(不足
    结构化 ``disk_insufficient``,绝不下载一半才 ENOSPC)→ ``snapshot_download``
    (``local_dir=`` 形态天然断点续传,重复调用接续 ``.incomplete``)→
    终态进度强制上报。

    同名守卫(10-03-vision-v2 复查):``dest`` 非空时 —— 完整模型(判定同
    :func:`_is_complete_model_dir`)= 结构化 ``model_exists``(换 repo 重下
    同名不做静默混装,提示先删);半成品 = 放行续传。空目录 = 全新下载。

    Args:
        repo_id: ``mlx-community/<name>``。
        models_root: 模型根目录(自动创建)。
        name: 本地目录名;缺省 = repo 名段。
        on_progress: ``(done_bytes, total_bytes | None)`` 回调(节流 ~0.5s;
            total 来自预检,预检失败时 None)。

    Raises:
        VisionModelError: 见模块头错误族表。
    """
    _validate_repo(repo_id)
    if name is not None:
        _validate_name(name)
    local_name = name or repo_id.split("/", 1)[1]
    _validate_name(local_name)
    root = Path(models_root)
    dest = root / local_name
    if dest.exists() and not dest.is_dir():
        raise VisionModelError(
            "invalid_name",
            f"目标路径已存在同名非目录文件: {dest}",
            details={"path": str(dest)},
        )
    if dest.is_dir() and any(dest.iterdir()):
        if _is_complete_model_dir(dest):
            raise VisionModelError(
                "model_exists",
                f"目标目录已存在同名完整模型: {dest}(要换仓库重下请先删除,"
                f"或换一个本地名)",
                details={"repo": repo_id, "name": local_name, "path": str(dest)},
            )
        # 半成品(incomplete)→ 续传语义:snapshot_download 接续 .incomplete
    hub = _import_hub()
    total: int | None = None
    try:
        info = hub.HfApi().model_info(repo_id=repo_id, files_metadata=True)
        siblings = getattr(info, "siblings", None) or []
        total = sum(int(getattr(item, "size") or 0) for item in siblings if getattr(item, "size"))
    except Exception as exc:  # noqa: BLE001 - 网络层错误原样结构化,不裸穿
        raise VisionModelError(
            "repo_unreachable",
            f"仓库元信息不可达({repo_id}): {type(exc).__name__}: {exc}",
            details={"repo": repo_id},
        ) from exc
    free = _nearest_free_bytes(root)
    if total and free < total:
        raise VisionModelError(
            "disk_insufficient",
            f"磁盘空间不足:需要 ~{total} 字节,可用 {free} 字节({root})",
            details={"repo": repo_id, "required": total, "free": free},
        )
    root.mkdir(parents=True, exist_ok=True)
    tracker = _ProgressTracker(on_progress, total)
    kwargs: dict[str, Any] = {"repo_id": repo_id, "local_dir": str(dest)}
    tqdm_class = tracker.tqdm_class()
    if tqdm_class is not None:
        kwargs["tqdm_class"] = tqdm_class
    try:
        hub.snapshot_download(**kwargs)
    except VisionModelError:
        raise
    except Exception as exc:  # noqa: BLE001 - 下载中断(可续传)结构化上抛
        raise VisionModelError(
            "download_failed",
            f"模型下载失败(已下载部分保留,可重试续传): {type(exc).__name__}: {exc}",
            details={"repo": repo_id, "path": str(dest)},
        ) from exc
    finally:
        tracker.finish()
    return dest


# ---------------------------------------------------------------------------
# 删除 / 激活
# ---------------------------------------------------------------------------


def delete_model(
    name: str, models_root: Path | str, *, active_path: str | None = None
) -> Path:
    """删除 ``<models_root>/<name>/`` 整目录(含未完缓存);激活模型拒删。

    激活判定 = 与 ``active_path``(vision.yaml ``local.model``)resolve 全等
    —— 在用权重被删会让本地 server/vl 突然失效,必须在 UI 层先切换。
    """
    _validate_name(name)
    root = Path(models_root)
    dest = root / name
    if active_path and _resolved(dest) == _resolved(active_path):
        raise VisionModelError(
            "model_active_refused",
            f"模型 {name} 正被 vision.yaml local.model 使用,拒绝删除(先切换激活模型)",
            details={"name": name, "path": str(dest)},
        )
    if not dest.is_dir():
        raise VisionModelError(
            "model_not_found", f"模型不存在: {dest}", details={"name": name, "path": str(dest)}
        )
    shutil.rmtree(dest)
    return dest


def activate_model(
    name: str, models_root: Path | str, vision_yaml_path: Path | str
) -> VisionConfig:
    """把 vision.yaml ``local.model`` 指向 ``<models_root>/<name>/``。

    半成品拒激活(``model_incomplete``,判定同 :func:`_is_complete_model_dir`)
    —— 把 local.model 指向缺权重的目录,ensure/spawn 只会得到 server_died,
    必须在激活口拦下(续传补全 = 再次 download 同名)。

    同门纪律:先 :func:`load_vision_config` 装载现值(拒载即上抛,不半载),
    :func:`dataclasses.replace` 换 ``local_model`` 后经
    :func:`save_vision_config` 原子落盘 —— 构造即校验的门风由 VisionConfig
    自带,这里零再判。
    """
    _validate_name(name)
    dest = Path(models_root) / name
    if not dest.is_dir():
        raise VisionModelError(
            "model_not_found", f"模型不存在: {dest}", details={"name": name, "path": str(dest)}
        )
    if not _is_complete_model_dir(dest):
        raise VisionModelError(
            "model_incomplete",
            f"模型 {name} 未下载完整(缺 config.json 或 *.safetensors),不能激活;"
            f"再次下载同名可断点续传补全",
            details={"name": name, "path": str(dest)},
        )
    config = load_vision_config(vision_yaml_path)  # VisionConfigError 原样上抛(拒载零写入)
    updated = replace(config, local_model=str(dest.resolve()))
    save_vision_config(vision_yaml_path, updated)
    return updated
