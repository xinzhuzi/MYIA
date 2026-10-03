"""myia-osint 适配器:子进程调用上游 Photon(vendor/Photon)——MYIA 侧代码.

本文件由 MYIA 仓库创作与维护,**不是**上游代码;上游 Photon(GPL-3.0)
只以 git submodule 形式钉在 ``vendor/Photon``(见 plugin.yaml ``vendor:``
节),零复制、零修改、零分发。适配器职责:

1. 定位 vendor(缺 submodule → 结构化 :data:`VENDOR_MISSING`,绝不猜路径);
2. 以 **隔离临时环境** 子进程运行上游 CLI——
   ``uv run --no-project --with <deps> python photon.py …``(依赖按需装进
   uv 缓存的临时环境,绝不进根依赖;依赖清单与上游 requirements.txt 同源:
   requests / urllib3 / tld);
3. 读取 Photon ``-e json`` 导出的 ``exported.json``,连同命令、退出码、
   vendor pin 等装配成**结构化 JSON 结果**(AI 消费路径,``myia osint --json``)。

错误契约(.trellis/spec/python/error-handling):所有失败都抛
:class:`OsintAdapterError`(code + message + 结构化 details,``to_dict()``
直接进 CLI 的 JSON 输出);禁止只有一句 str 的异常。code 词表:

- ``vendor_missing``   vendor/Photon/photon.py 不存在(未 init submodule)
- ``uv_missing``       宿主无 uv(隔离运行时不可用)
- ``invalid_target``   目标不是 http(s) URL(用法/配置错误)
- ``photon_failed``    Photon 子进程非零退出(采集失败)
- ``photon_timeout``   子进程超过 wall-clock 预算(采集失败)
- ``photon_export_missing`` / ``photon_export_invalid``
                       退出 0 但 JSON 导出缺失/不可解析(采集失败)

失败码 → CLI 退出码的映射归 CLI 所有(``shishi.cli.OSINT_FETCH_FAILURE_CODES``,
spec python/error-handling 的退出码契约);适配器只负责如实上报 code。

铁律(security-baseline):适配器任何失败只影响 ``myia osint`` 自身,
核心品类流水线照常跑通(测试钉在 tests/test_osint_plugin.py)。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "OsintAdapterError",
    "build_command",
    "is_available",
    "normalize_target",
    "plugin_dir",
    "run",
    "vendor_commit",
    "vendor_root",
]

#: 适配器所在插件目录(本文件按样板布局固定在 plugins/myia-osint/ 下)。
PLUGIN_DIR = Path(__file__).resolve().parent

#: 上游 submodule 相对插件目录路径(与 plugin.yaml ``vendor.path`` 同源)。
VENDOR_RELATIVE_PATH = "vendor/Photon"

#: 上游 CLI 入口(相对 vendor 根)。
PHOTON_ENTRY = "photon.py"

#: Photon 的 JSON 导出文件名(上游 plugins/exporter.py 的固定落点)。
PHOTON_EXPORT_FILE = "exported.json"

#: 隔离运行时按需注入的依赖(与上游 requirements.txt 同源,绝不进根依赖)。
PHOTON_DEPENDENCIES = ("requests", "urllib3", "tld")

#: 子进程 wall-clock 预算缺省值(整个侦察过程,非单请求超时)。
DEFAULT_TIMEOUT_SECONDS = 600.0

#: 结构化错误里 stderr/stdout 摘录的上限(保持 JSON 输出有界)。
_PROCESS_TAIL_CHARS = 2000


class OsintAdapterError(Exception):
    """结构化适配器错误:code + message + details,``to_dict()`` 进 JSON 输出."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """结构化形态:{"code", "message", **details}(供 CLI --json 与 agent 自修)."""
        return {"code": self.code, "message": self.message, **self.details}


def plugin_dir() -> Path:
    """本插件目录(适配器所在处)."""
    return PLUGIN_DIR


def vendor_root(plugin_dir: str | Path | None = None) -> Path:
    """上游 vendor 根目录(可注入 plugin_dir 供测试/安装根场景)."""
    root = Path(plugin_dir) if plugin_dir is not None else PLUGIN_DIR
    return root / VENDOR_RELATIVE_PATH


def is_available(plugin_dir: str | Path | None = None) -> bool:
    """上游源码是否就位(submodule 已 init 且入口文件存在)."""
    return (vendor_root(plugin_dir) / PHOTON_ENTRY).is_file()


def vendor_commit(
    plugin_dir: str | Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str | None:
    """读 vendor 工作树钉住的 commit(``git rev-parse HEAD``);取不到返回 None.

    纯溯源信息:失败(无 git/未 init/非仓库)一律 None,绝不抛 ——
    commit 读不到不代表侦察不能跑。
    """
    do_run = runner if runner is not None else subprocess.run
    vendor = vendor_root(plugin_dir)
    if not vendor.is_dir():
        return None
    # vendor 自身必须是仓库根(submodule checkout);空目录时 `git -C` 会向上
    # 爬到外层 MYIA 仓库、把宿主 HEAD 误当 Photon pin(CI 无 submodule 时实测),
    # 故先验 .git 存在(子仓的工作树形态:目录或 .git 文件皆可)。
    if not (vendor / ".git").exists():
        return None
    try:
        completed = do_run(
            ["git", "-C", str(vendor), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    commit = (completed.stdout or "").strip()
    return commit or None


def normalize_target(target: str) -> str:
    """校验并规整侦察目标:必须是 http(s) URL(防误用与 argv 注入面)."""
    value = (target or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OsintAdapterError(
            "invalid_target",
            f"侦察目标必须是 http(s) URL,当前为 {value!r}(示例:https://example.com)",
            target=target,
        )
    return value.rstrip("/")


def build_command(
    target: str,
    output_dir: str | Path,
    *,
    extra_args: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """装配隔离子进程命令:uv 临时环境按需装依赖,再跑上游 CLI.

    ``--no-project`` 切断根项目环境;``--with`` 逐个注入上游依赖(uv 缓存
    的临时环境,用完即弃);``-e json`` 让上游落 ``exported.json``。
    """
    command = ["uv", "run", "--no-project"]
    for dependency in PHOTON_DEPENDENCIES:
        command += ["--with", dependency]
    command += [
        "python",
        PHOTON_ENTRY,
        "-u",
        target,
        "-o",
        str(output_dir),
        "-e",
        "json",
        *extra_args,
    ]
    return command


def _tail(text: str | None) -> str:
    """进程输出摘录(尾部),保持结构化错误有界."""
    value = (text or "").strip()
    return value[-_PROCESS_TAIL_CHARS:]


def run(
    target: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    extra_args: tuple[str, ...] | list[str] = (),
    plugin_dir: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """对 ``target`` 跑一次上游 Photon 侦察,返回结构化 JSON 结果.

    Args:
        target: http(s) 目标 URL(合法公开目标,样板默认 https://example.com)。
        timeout: 子进程 wall-clock 预算秒数,超时抛 ``photon_timeout``。
        extra_args: 透传给上游 CLI 的附加参数(如 ``("--delay", "1")``)。
        plugin_dir: 插件目录覆盖(测试/自定义安装根)。
        runner: subprocess.run 注入口(测试 mock 用)。
        clock: 单调时钟注入口(测试 mock 用)。

    Returns:
        结构化结果 dict:plugin/target/status/vendor/command/exit_code/
        duration_seconds/results(上游 JSON 导出)/datasets(.txt 数据集名)。

    Raises:
        OsintAdapterError: 全部失败形态都是结构化错误(见模块 docstring 词表)。
    """
    do_run = runner if runner is not None else subprocess.run
    root = Path(plugin_dir) if plugin_dir is not None else PLUGIN_DIR
    clean_target = normalize_target(target)
    vendor = vendor_root(root)
    if not (vendor / PHOTON_ENTRY).is_file():
        raise OsintAdapterError(
            "vendor_missing",
            (
                f"上游源码未就位:{vendor / PHOTON_ENTRY} 不存在。"
                f"先初始化 submodule(在插件目录 {root} 下执行):"
                f"git submodule update --init {VENDOR_RELATIVE_PATH}"
            ),
            plugin_dir=str(root),
            vendor_path=str(vendor),
        )
    if shutil.which("uv") is None:
        raise OsintAdapterError(
            "uv_missing",
            "隔离运行时不可用: PATH 上找不到 uv(https://docs.astral.sh/uv/)。"
            "适配器以 uv 临时环境运行上游 CLI,依赖不进根依赖。",
            plugin_dir=str(root),
        )
    started = clock()
    with tempfile.TemporaryDirectory(prefix="myia-osint-") as scratch:
        loot_dir = Path(scratch) / "loot"
        command = build_command(clean_target, loot_dir, extra_args=extra_args)
        try:
            completed = do_run(
                command,
                cwd=str(vendor),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OsintAdapterError(
                "photon_timeout",
                f"Photon 子进程超过 {timeout:g}s 预算被终止(目标 {clean_target})",
                target=clean_target,
                timeout_seconds=timeout,
                stderr_tail=_tail(exc.stderr if isinstance(exc.stderr, str) else None),
            ) from exc
        duration = round(clock() - started, 3)
        if completed.returncode != 0:
            raise OsintAdapterError(
                "photon_failed",
                f"Photon 子进程非零退出(code={completed.returncode};目标 {clean_target})",
                target=clean_target,
                exit_code=completed.returncode,
                stderr_tail=_tail(completed.stderr),
                stdout_tail=_tail(completed.stdout),
            )
        export_file = loot_dir / PHOTON_EXPORT_FILE
        if not export_file.is_file():
            raise OsintAdapterError(
                "photon_export_missing",
                f"Photon 退出 0 但未产出 {PHOTON_EXPORT_FILE}(目标 {clean_target})",
                target=clean_target,
                exit_code=completed.returncode,
                stdout_tail=_tail(completed.stdout),
            )
        try:
            results = json.loads(export_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OsintAdapterError(
                "photon_export_invalid",
                f"Photon 的 {PHOTON_EXPORT_FILE} 不可解析:{exc}",
                target=clean_target,
            ) from exc
        datasets = sorted(path.stem for path in loot_dir.glob("*.txt"))
    return {
        "plugin": "myia-osint",
        "target": clean_target,
        "status": "success",
        "vendor": {
            "path": str(vendor),
            "commit": vendor_commit(root),
        },
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "results": results,
        "datasets": datasets,
    }
