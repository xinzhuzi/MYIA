"""MYIA 桌面 sidecar 入口:双模式。

模式一(直通,默认):: ``entry.py run <yaml> --json …`` 原样转发 :func:`myia.cli.main`,
退出码 0/1/2/3 契约由 CLI 层保证(PyInstaller 打包路径与 v02 spike 完全兼容)。

模式二(RPC 服务):: ``entry.py serve`` 进入行分隔 JSON-RPC 子集(Tauri 壳的常驻
后端,stdin 收请求 / stdout 出应答与事件;stderr 只作调试旁路,不承载协议)。

== 协议(每行一份 JSON 文档,UTF-8,ensure_ascii=False) ==

请求(stdin):: ``{"id": 1, "method": "health", "params": {}}``
- ``id``:int 或字符串,应答原样回显;缺省 = 通知(只执行、不应答)。
- ``params`` 缺省按 ``{}``。

应答(stdout):: ``{"id": 1, "result": {...}}`` 或
``{"id": 1, "error": {"code": "...", "path": "$", "message": "...", "data": {...}}}``
- 错误结构化透传(对齐 spec python/error-handling):``code`` 错误类、``path``
  字段路径、``message`` 中文原因、``data`` 原始细节(CLI 报文整包入 data)。
- 协议级 code:``parse_error`` / ``invalid_request`` / ``invalid_params`` /
  ``method_not_found`` / ``internal_error``;业务级透传底层 code
  (``config`` + errors[] / ``plugins_dir`` / ``store_corrupt`` /
  ``invalid_secret_name`` / ``run_busy`` / ``run_not_found`` …)。

事件(stdout,无 id,以 ``type`` 字段区分)::

    {"type": "log",       "run_id": 3, "stream": "stderr", "line": "…", "ts": "…"}
    {"type": "progress",  "run_id": 3, "phase": "source_done", "source": "…", "items": "2", "ts": "…"}
    {"type": "completed", "run_id": 3, "exit_code": 0, "status": "success", …, "ts": "…"}
    {"type": "image.progress",  "job_id": 1, "stage": "ocr|model", "ts": "…"}
    {"type": "image.completed", "job_id": 1, "ok": true, "result": {"text": "…", "model": "…",
     "channel": "local", "elapsed_ms": 1, "ocr_used": true}, "ts": "…"}

== 方法集(覆盖现有 CLI 能力) ==

================= ============================== ============================
方法              CLI 等价                        结果要点
================= ============================== ============================
version           ``myia --version``             name/version/protocol
health            ``myia list --json``           源健康度 + summary 聚合
plugins.list      ``myia plugin list --json``    已装插件清单 + findings
doctor            ``myia doctor --json``         findings 全量(完成即 0)
run.start         ``myia run <yaml>``            后台子进程,立即返回 run_id
run.status        (sidecar 内注册表)             state/exit_code/status/record
logs.tail         (sidecar 内环形缓冲)            最近日志行(可按 run_id 过滤)
store.items       (SQLiteStore.list_items 直读)  情报流条目(新→旧)
secret.set        ``myia secret set``            只入系统钥匙链,值零回显
secret.list       ``myia secret list``           只有名字,值不可读
sources.write     (品类 YAML 源启停写回)          disable 摘出/enable 移回;
                                                 落盘前过 load_category 同门
yaml.list         (plugins 目录扫描)              品类 YAML 清单(坏文件带
                                                 error 入列,可读可修)
yaml.read         (原文直读)                      UTF-8 原文(newline="" 逐
                                                 字节保真,≤1 MiB)
yaml.validate     (load_category 同门干跑)        findings 分级 error/warning,
                                                 零写入
yaml.template     (协议侧常量)                    最小合法品类模板文本
yaml.save         (校验→查重→.bak→原子写)         mtime 乐观锁;file 不存在 +
                                                 expected_mtime=null = 新建
yaml.delete       (.bak 留底→删主文件→连带暂存)   自建品类生命周期收尾
image.import       (看图图片入库)                  sha256 去重落 <home>/images/;
                                                 10MB 上限;heic 先 sips 转 png
image.ocr          (双引擎 OCR,vision|rapidocr)   逐行 {text, conf} + engine + ms;
                                                 非法 engine → image_engine_unknown
image.analyze      (本地/云端二级看图)             后台线程即返 job_id,结果走
                                                 image.progress/completed 事件
image.status       (sidecar 内注册表)              busy/job_id(UI 重连对账)
image.config.read  (vision.yaml 直读)              脱敏配置(keychain 引用不回明文)
image.config.save  (同门校验→原子写)               失败零写入(image_config_invalid)
================= ============================== ============================

- ``run.start`` params:``yaml``(必填)、``dry``(bool,缺省 false)、``db``、
  ``config``(全局 pools YAML)。同一时刻只允许一个 run(``run_busy`` 结构化拒绝);
  子进程 = 冻结包自启(直通模式)/ dev 下 ``python -m myia.cli``,退出码
  0/1/2/3 由 CLI 原样带回,随 ``completed`` 事件透传(退出码语义保留)。
- ``store.items`` params:``db``、``category``、``since``(ISO 时间)、``limit``。
- ``run.status`` / ``logs.tail`` params:``run_id``(可省)/ ``lines``(tail 上限)。
- ``health`` params:``plugins_dir``、``db``;``plugins.list`` params:``dir``;
  ``doctor`` params:``yamls[]``、``plugins_dir``、``db``、``config``、``probe_timeout``。
- ``sources.write`` params:``file``(必填)、``enable[]`` / ``disable[]``(至少
  其一);应答 ``enabled``/``disabled`` = 写回后的名字全集(往返一致由 UI 侧
  doctor 复核,存储形态见 :func:`_m_sources_write`)。
- ``yaml.*`` 六方法(task 10-03-yaml-editor,契约钉死于任务档 design.md §1,
  Python/TS 两侧注释互指):``yaml.list`` 零参数(目录由 serve 上下文定);
  ``yaml.read {file}``;``yaml.validate {content, file?}``;``yaml.template``
  零参数;``yaml.save {file, content, expected_mtime}``;
  ``yaml.delete {file}``。路径围栏(:func:`_fence_yaml_path`):后缀
  ``.yaml/.yml`` + resolve 后必须位于 plugins 目录内(``os.path.commonpath``,
  消解符号链接与 ``..`` 穿越)+ 新建 stem 过 :data:`CATEGORY_ID_RE` ——
  sidecar 是 UI 直连读写通道,不设围栏 = 桌面端任意文件读写原语。
- ``image.*`` 六方法(task 10-03-image-input,契约钉死于任务档 design.md 协议表,
  TS 侧 ui-src/lib/api/types.ts + screens/image/api.ts 注释互指;能力实现在
  ``myia.vision`` 包):``image.import {kind: path|base64, value}`` → sha256
  前 16 位 id 去重落 ``<home>/images/<id>.<ext>``(10MB 上限;heic 先 sips 转
  png;魔数嗅探定格式,扩展名/mime 不作信任源);``image.ocr {id, engine?}``
  (engine 缺省取 vision.yaml 的 ocr.engine_default);``image.analyze {id,
  mode: read|describe|ask, question?, channel?}`` 后台线程即返 job_id,结果走
  ``image.progress {stage: ocr|model}`` / ``image.completed {ok, result|error}``
  事件(仿 run.start,壳 120s 硬超时免疫;单飞守卫 ``image_busy`` 照 run_busy);
  ``image.status`` 对账;``image.config.read/save`` 读写 ``<home>/vision.yaml``
  (MYIA_HOME 第一个全局配置文件;云端 api_key 只收 ``keychain:`` 引用,
  同门校验失败零写入)。业务错误码统一 ``image_`` 前缀:``image_not_found`` /
  ``image_unsupported`` / ``image_too_large`` / ``image_ocr_failed`` /
  ``image_engine_unknown`` / ``image_unreachable``(附本地服务启动指引)/
  ``image_no_credentials`` / ``image_provider_error`` / ``image_busy`` /
  ``image_config_invalid``。本地通道零出网;结果只随事件呈现,不入库不落日志。

铁律:凭据只进系统钥匙链(``secret.set`` 薄包装 myia.secrets,值不落日志/协议流);
桌面零 Docker;任何插件装不上不拦核心(doctor/list 只产 findings)。

serve 上下文路径解析(v1.1.1 统一,优先级):显式 params > ``MYIA_HOME`` env
(Tauri 壳 spawn 时注入)> 冻结 .app bundle 探测(平台数据根
``~/Library/Application Support/MYIA`` / ``%APPDATA%\\MYIA`` / ``~/.myia``)>
dev 回退 cwd(仓库内运行行为不变)。home 模式下 db/plugins 缺省
``<home>/myia.db``、``<home>/plugins``;serve 启动时首跑种子 —— plugins
目录空则从随包 Resources 拷官方品类 YAML(标志 ``.seeded`` 抑制复种);
health 应答附 ``first_run`` 供 UI 空态引导。

== 退出码 ==

- serve 模式:stdin EOF(壳退出/管道关闭)= 干净退出 0;serve 循环自身致命
  异常 = 1;run 子进程的原样退出码只在 ``completed`` 事件内透传,不改进程码。
- 直通模式:= CLI 契约 0/1/2/3 原样。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import httpx
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

import myia
import yaml
from myia.cli import DEFAULT_DB_PATH, DEFAULT_PLUGINS_DIR, main as cli_main
from myia.plugins.installed import INSTALL_ROOT_ENV, default_install_root
from myia.schema import (
    CATEGORY_ID_RE,
    CategoryConfig,
    CredentialResolveError,
    LoadError,
    # 私有符号受控复用(与 cli.py/manifest.py 复用 _SECRET_REF_RE 同一先例):
    # 重复键检测与凭据引用语法只此一处定义,防两处漂移。
    _SECRET_REF_RE,
    _UniqueKeyLoader,
    load_category,
    load_category_file,
    resolve_credential,
)
from myia.secrets import SecretError, list_secrets, set_secret
from myia.store import SQLiteStore, StoreSchemaError
from myia.vision import (
    CHANNELS,
    OCR_ENGINES,
    OCRError,
    VISION_FILE_NAME,
    VisionClient,
    VisionConfig,
    VisionConfigError,
    VisionResult,
    load_vision_config,
    run_ocr,
    save_vision_config,
)

PROTOCOL_VERSION = 1
#: 日志环形缓冲容量(行);logs.tail 的硬上限。
LOG_RING_CAPACITY = 4000
#: 单次 run 的日志事件与环形上限一致;超限仅丢最旧行。
STATUS_BY_EXIT = {0: "success", 1: "config_error", 2: "failed", 3: "partial"}

#: 应用数据根环境变量名(Tauri 壳 spawn sidecar 时注入,优先级见 _serve_context)。
MYIA_HOME_ENV = "MYIA_HOME"
#: 首跑种子标志文件名(数据根下;存在即永不复种,用户删光插件也不打扰)。
SEED_MARKER = ".seeded"


# ---------------------------------------------------------------------------
# 应用数据根与 serve 上下文(v1.1.1 桌面数据通路统一)
# ---------------------------------------------------------------------------


def myia_home() -> Path:
    """平台应用数据根:darwin ``~/Library/Application Support/MYIA`` /
    win32 ``%APPDATA%\\MYIA`` / 其余 ``~/.myia``(design.md D1)。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MYIA"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "MYIA"
    return Path.home() / ".myia"


def _inside_app_bundle() -> bool:
    """冻结二进制是否位于 .app 内(桌面发行形态;dev/CLI 直跑为 False)。"""
    if not getattr(sys, "frozen", False):
        return False
    return any(parent.suffix == ".app" for parent in Path(sys.executable).resolve().parents)


def _bundle_plugins_dir() -> Path | None:
    """随包官方插件目录(Tauri resources;dev 或无资源时 None)。

    候选按平台资源布局:macOS .app 的 ``Contents/Resources/plugins``、
    Windows/Linux 资源保持相对结构落在 exe 旁(``plugins/``)。
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    candidates = [
        exe.parent.parent / "Resources" / "plugins",
        exe.parent / "plugins",
        exe.parent / "resources" / "plugins",
    ]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.yaml")):
            return candidate
    return None


def _has_category_yamls(root: Path) -> bool:
    """目录内是否已有品类 YAML(平铺 ``*.yaml``/``*.yml``,非递归)。"""
    return root.is_dir() and (any(root.glob("*.yaml")) or any(root.glob("*.yml")))


class ServeContext(NamedTuple):
    """serve 请求的路径上下文:显式 params 永远赢,这里只供缺省。

    ``home=None`` 即 dev 回退(cwd 相对常量,仓库内行为与 v1.1 逐字节一致);
    home 模式下 db/plugins 默认 ``<home>/myia.db``、``<home>/plugins``,市场
    安装根尊重既有 ``MYIA_PLUGIN_DIR`` env,否则同为 ``<home>/plugins``
    (品类 YAML 平铺与市场插件子目录互不干扰:health 扫描非递归)。
    NamedTuple 而非 dataclass:本模块经 importlib 直载(测试),dataclass
    的注解解析依赖 sys.modules 注册,此处没有。
    """

    home: Path | None
    db: str
    plugins_dir: str
    install_root: str

    def first_run(self) -> bool:
        """首跑判定:home 模式且 plugins 目录内零品类 YAML(种子失败/被删光)。"""
        return self.home is not None and not _has_category_yamls(Path(self.plugins_dir))


def _serve_context() -> ServeContext:
    """解析当前 serve 上下文(每请求调用,极廉价;env 可被测试逐例注入)。

    优先级:**显式 params(各方法自行合并)> ``MYIA_HOME`` env > .app bundle
    探测 > dev 回退 cwd**。
    """
    env_home = os.environ.get(MYIA_HOME_ENV)
    if env_home:
        home = Path(env_home).expanduser()
    elif _inside_app_bundle():
        home = myia_home()
    else:
        home = None
    if home is None:
        return ServeContext(
            home=None,
            db=DEFAULT_DB_PATH,
            plugins_dir=DEFAULT_PLUGINS_DIR,
            install_root=str(default_install_root()),
        )
    try:
        home.mkdir(parents=True, exist_ok=True)
        # plugins 目录一并建:全新数据根上 health/doctor 要的是空态 OK,
        # 不是 NotADirectoryError(prd 探查矩阵的「无创建逻辑」根因)
        (home / "plugins").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProtocolError(
            "myia_home_unwritable", f"应用数据根不可创建: {home} ({exc})", path=f"env.{MYIA_HOME_ENV}"
        ) from exc
    install_root = os.environ.get(INSTALL_ROOT_ENV) or str(home / "plugins")
    return ServeContext(
        home=home,
        db=str(home / "myia.db"),
        plugins_dir=str(home / "plugins"),
        install_root=str(Path(install_root).expanduser()),
    )


def _seed_first_run(ctx: ServeContext) -> bool:
    """首跑种子:``<home>/plugins`` 无品类 YAML 且未种过 → 拷随包官方插件。

    幂等由 ``<home>/.seeded`` 标志保证(用户删光插件不复种);拷贝非原子
    可接受 —— 中途失败最坏半份副本且无标志,下次启动整体重拷覆盖。
    仅 home 模式调用;dev 模式零动作。
    """
    assert ctx.home is not None
    if (ctx.home / SEED_MARKER).exists():
        return False
    plugins_dir = Path(ctx.plugins_dir)
    if _has_category_yamls(plugins_dir):
        return False  # 升级安装/用户手动放置过插件 —— 不打扰
    bundle = _bundle_plugins_dir()
    if bundle is None:
        return False
    plugins_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for pattern in ("*.yaml", "*.yml"):
        for source in sorted(bundle.glob(pattern)):
            shutil.copy2(source, plugins_dir / source.name)
            copied.append(source.name)
    (ctx.home / SEED_MARKER).write_text(_now_iso() + "\n", encoding="utf-8")
    _ring_append(
        None, "stderr", f"sidecar: 首跑种子 {len(copied)} 个官方插件 -> {plugins_dir}"
    )
    return bool(copied)


def _startup_seed() -> None:
    """serve 启动时的一次性种子入口;失败只留痕,绝不拦服务起来。"""
    try:
        ctx = _serve_context()
        if ctx.home is not None:
            _seed_first_run(ctx)
    except Exception as exc:  # noqa: BLE001 — 种子是增强,不是依赖
        print(f"sidecar: 首跑种子失败(忽略): {exc}", file=sys.stderr)


class ProtocolError(Exception):
    """协议层结构化错误 → 应答行 ``error`` 对象(code/path/message/data)。"""

    def __init__(self, code: str, message: str, *, path: str = "$", data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.data = data


# ---------------------------------------------------------------------------
# 输出与日志环形缓冲(serve 线程 + run 工作线程共用;单写锁串行化)
# ---------------------------------------------------------------------------

_OUT: io.TextIOBase | None = None
_WRITE_LOCK = threading.Lock()
_RING_LOCK = threading.Lock()
_LOG_RING: deque[dict[str, Any]] = deque(maxlen=LOG_RING_CAPACITY)
_LOG_SEQ = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_line(payload: dict[str, Any]) -> None:
    """协议流单行写入(线程安全;_OUT 是 serve 启动时捕获的真实 stdout)。"""
    assert _OUT is not None
    with _WRITE_LOCK:
        _OUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _OUT.flush()


def _respond(rid: Any, result: Any) -> None:
    _write_line({"id": rid, "result": result})


def _error(rid: Any, code: str, message: str, *, path: str = "$", data: Any = None) -> None:
    error: dict[str, Any] = {"code": code, "path": path, "message": message}
    if data is not None:
        error["data"] = data
    _write_line({"id": rid, "error": error})


def _ring_append(run_id: int | None, stream: str, line: str) -> dict[str, Any]:
    global _LOG_SEQ
    with _RING_LOCK:
        _LOG_SEQ += 1
        entry = {"seq": _LOG_SEQ, "ts": _now_iso(), "run_id": run_id, "stream": stream, "line": line}
        _LOG_RING.append(entry)
        return entry


# ---------------------------------------------------------------------------
# in-process CLI 复用(health / plugins.list / doctor:stdout 单份 JSON 契约)
# ---------------------------------------------------------------------------


def _cli_json(argv: list[str]) -> tuple[int, dict[str, Any] | None]:
    """跑一次 CLI 子命令,捕获其 ``--json`` 单份文档与退出码。

    stderr(WARNING+ 结构化日志)入环形缓冲(run_id=null),供 logs.tail 诊断。
    仅在 serve 循环线程调用;run 工作线程不经此路(无 stdout 重定向竞争)。
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
    except SystemExit as exc:  # 防御:--help/--version 类参数不可达,兜底不穿协议流
        raise ProtocolError("internal_error", f"CLI 异常退出: {exc.code}") from exc
    for line in err.getvalue().splitlines():
        if line.strip():
            _ring_append(None, "stderr", line)
    return code, _last_json(out.getvalue())


def _last_json(text: str) -> dict[str, Any] | None:
    """取 stdout 末份可解析 JSON(CLI --json 契约:恰好一份)。"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _cli_error(code: int, payload: dict[str, Any] | None) -> ProtocolError:
    """CLI ``--json`` 错误报文 → 协议错误对象(code/path/message 透传)。"""
    if payload and "error" in payload:
        if payload["error"] == "config" and payload.get("errors"):
            first = payload["errors"][0]
            return ProtocolError(
                "config", str(first.get("message", "")), path=str(first.get("path", "$")), data=payload
            )
        return ProtocolError(str(payload["error"]), str(payload.get("message", "")), data=payload)
    return ProtocolError("cli_error", f"CLI 退出码 {code} 且无可解析 JSON 输出", data={"exit_code": code})


# ---------------------------------------------------------------------------
# 方法:version / health / plugins.list / doctor
# ---------------------------------------------------------------------------


def _m_version(params: dict[str, Any]) -> dict[str, Any]:
    """``myia --version`` 等价:版本 + 协议版本。"""
    return {"name": "myia", "version": myia.__version__, "protocol": PROTOCOL_VERSION}


def _m_health(params: dict[str, Any]) -> dict[str, Any]:
    """``myia list --json`` 等价:插件清单 + 源健康度 + 计数聚合。

    plugins_dir/db 缺省走 serve 上下文(home 模式 = 数据根;dev = cwd 相对);
    home 模式零品类 YAML 时附 ``first_run=true``(UI 空态引导,不报错)。
    """
    ctx = _serve_context()
    argv = [
        "list",
        "--plugins-dir", str(params.get("plugins_dir") or ctx.plugins_dir),
        "--db", str(params.get("db") or ctx.db),
        "--json",
    ]
    code, payload = _cli_json(argv)
    if code != 0:
        raise _cli_error(code, payload)
    assert payload is not None
    counts = {"ok": 0, "degraded": 0, "dead": 0, "unknown": 0}
    for plugin in payload.get("plugins", []):
        for source in plugin.get("sources", []):
            state = source.get("health", {}).get("state")
            if state in counts:
                counts[state] += 1
    payload["summary"] = {
        "plugins": len(payload.get("plugins", [])),
        "sources": sum(counts.values()),
        **counts,
    }
    # healthy 语义对齐 doctor:dead=error 级;degraded 只算 warning。
    payload["healthy"] = counts["dead"] == 0 and payload.get("store_error") is None
    payload["first_run"] = ctx.first_run()
    payload["exit_code"] = code
    return payload


def _m_plugins_list(params: dict[str, Any]) -> dict[str, Any]:
    """``myia plugin list --json`` 等价:已装市场插件 + findings。

    ``dir`` 缺省走 serve 上下文安装根(home 模式 = ``MYIA_PLUGIN_DIR`` env
    否则 ``<home>/plugins``;市场面首跑合法为空,design.md D7)。
    """
    ctx = _serve_context()
    argv = ["plugin", "list", "--json", "--dir", str(params.get("dir") or ctx.install_root)]
    code, payload = _cli_json(argv)
    if code != 0:
        raise _cli_error(code, payload)
    assert payload is not None
    payload["exit_code"] = code
    return payload


def _m_doctor(params: dict[str, Any]) -> dict[str, Any]:
    """``myia doctor --json`` 等价:结构化诊断(问题全在 findings,完成即 0)。"""
    ctx = _serve_context()
    argv = ["doctor"]
    for yaml_path in params.get("yamls") or []:
        argv.append(str(yaml_path))
    argv += ["--plugins-dir", str(params.get("plugins_dir") or ctx.plugins_dir)]
    argv += ["--db", str(params.get("db") or ctx.db)]
    if params.get("config"):
        argv += ["--config", str(params["config"])]
    if params.get("probe_timeout") is not None:
        argv += ["--probe-timeout", str(params["probe_timeout"])]
    argv.append("--json")
    code, payload = _cli_json(argv)
    if code != 0:
        raise _cli_error(code, payload)
    assert payload is not None
    payload["exit_code"] = code
    return payload


# ---------------------------------------------------------------------------
# 方法:store.items / secret.set / secret.list(凭据只入钥匙链)
# ---------------------------------------------------------------------------


def _item_dict(item: Any) -> dict[str, Any]:
    """ItemRecord → 协议字典(raw/content_hash 不出协议面,情报流无需)。"""
    return {
        "id": item.id,
        "url": item.url,
        "dedup_key": item.dedup_key,
        "title": item.title,
        "source": item.source,
        "content": item.content,
        "tags": item.tags,
        "category": item.category,
        "scores": item.scores,
        "pushed_at": item.pushed_at.isoformat() if item.pushed_at else None,
        "push_slot": item.push_slot,
        "first_seen": item.first_seen.isoformat() if item.first_seen else None,
    }


def _m_store_items(params: dict[str, Any]) -> dict[str, Any]:
    """SQLiteStore.list_items 直读(数据面复用:SQLite 单库,零新后端)。"""
    db = params.get("db") or _serve_context().db
    since_raw = params.get("since")
    since = None
    if since_raw:
        try:
            since = datetime.fromisoformat(str(since_raw))
        except ValueError as exc:
            raise ProtocolError("invalid_params", f"since 不是合法 ISO 时间: {since_raw}", path="params.since") from exc
    limit = params.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ProtocolError("invalid_params", "limit 必须为正整数", path="params.limit")
    try:
        store = SQLiteStore(db)
    except StoreSchemaError as exc:
        raise ProtocolError(exc.code, str(exc), path="params.db", data=exc.details) from exc
    try:
        items = store.list_items(
            category=params.get("category"), since=since, limit=limit
        )
    except ValueError as exc:  # store 层参数校验(空 category 等)
        raise ProtocolError("invalid_params", str(exc), path="params") from exc
    finally:
        store.close()
    return {"db": str(db), "count": len(items), "items": [_item_dict(item) for item in items]}


def _m_secret_set(params: dict[str, Any]) -> dict[str, Any]:
    """薄包装 myia.secrets.set_secret:凭据只入系统钥匙链;值零回显零落日志。"""
    name, value = params.get("name"), params.get("value")
    if not isinstance(name, str) or not name:
        raise ProtocolError("invalid_params", "缺少凭据名 name(myia/<scope>/<name>)", path="params.name")
    if not isinstance(value, str) or not value:
        raise ProtocolError("invalid_params", "缺少凭据值 value", path="params.value")
    try:
        set_secret(name, value)
    except SecretError as exc:
        raise ProtocolError(exc.code, str(exc), path="params.name") from exc
    return {"name": name, "stored": True}


def _m_secret_list(params: dict[str, Any]) -> dict[str, Any]:
    """薄包装 myia.secrets.list_secrets:只有名字,值永不可读。"""
    try:
        names = list_secrets()
    except SecretError as exc:
        raise ProtocolError(exc.code, str(exc)) from exc
    return {"names": names}


# ---------------------------------------------------------------------------
# 方法:sources.write(源启停写回;往返一致 = doctor 复核同名单集合)
# ---------------------------------------------------------------------------


def _stash_path(yaml_path: Path) -> Path:
    """停用源暂存文件:同目录 ``<yaml>.disabled.json``。

    存储形态属 Python 侧自由度(契约只钉名字集合往返):schema 顶层是
    extra=forbid 的 12 节公开契约(SKILL.md 逐字段锁定),停用源不进 YAML
    文档,lossless 暂存在旁边的 JSON 里,随 YAML 目录一起移动。
    """
    return yaml_path.parent / (yaml_path.name + ".disabled.json")


def _load_disabled_stash(yaml_path: Path) -> list[dict[str, Any]]:
    stash_file = _stash_path(yaml_path)
    if not stash_file.exists():
        return []
    try:
        data = json.loads(stash_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(
            "stash_unreadable",
            f"停用源暂存文件不可读: {stash_file} ({exc})",
            path="params.file",
        ) from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ProtocolError(
            "stash_unreadable",
            f"停用源暂存文件损坏(应为对象列表): {stash_file}",
            path="params.file",
        )
    return data


def _source_names(entries: list[Any], *, where: str) -> list[str]:
    """按序取源名;缺 name / 重名都结构化拒绝(写回不可落在含糊名单上)。"""
    names: list[str] = []
    for index, entry in enumerate(entries):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise ProtocolError(
                "invalid_params",
                f"{where}[{index}] 缺少有效 name 字段,无法定位写回目标",
                path="params.file",
            )
        names.append(name)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ProtocolError(
            "duplicate_source", f"{where} 存在重名源: {duplicates}", path="params.file"
        )
    return names


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + ``os.replace`` 原子落盘(读者侧零半写状态)。

    ``newline=""`` 关闭换行翻译:yaml.save 写回的用户原文逐字节保真
    (CRLF 不被翻译成 ``os.linesep``,Windows 上尤其;task 10-03-yaml-editor
    design §2 写侧保真)。sources.write 走同一路径,其内容是生成的纯 ``\\n``
    文本,加 ``newline=""`` 无副作用 —— 统一一条写入路径,不留两套语义。
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _m_sources_write(params: dict[str, Any]) -> dict[str, Any]:
    """源启停写回品类 YAML(协议扩展 `sources.write` 收编,PRD 往返一致)。

    契约(ui-src/screens/sources/api.ts 模块头):``disable`` 把源从
    ``sources:`` 摘出(lossless 暂存到同目录 ``<yaml>.disabled.json``),
    ``enable`` 移回;写回用裸 YAML dict 搬运 —— ``plugin:``/``baseline:``/
    ``aggregate:`` 等 sidecar 节原样保留(pydantic 模型 dump 会丢 PrivateAttr)。
    落盘前过 myia 自家装载器(:func:`load_category`,与 ``myia run`` 同一道
    门)校验,失败即原样零写入 —— 「被 myia run 识别」由装载器同门保证。
    拒绝停用最后一个启用源(schema ``sources`` min_length=1,停满即拒载)。
    """
    yaml_raw = params.get("file")
    if not isinstance(yaml_raw, str) or not yaml_raw:
        raise ProtocolError("invalid_params", "缺少品类 YAML 路径 file", path="params.file")
    enable = params.get("enable")
    disable = params.get("disable")
    for label, value in (("enable", enable), ("disable", disable)):
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ProtocolError(
                "invalid_params", f"{label} 必须为非空字符串数组", path=f"params.{label}"
            )
    enable = list(enable or [])
    disable = list(disable or [])
    if not enable and not disable:
        raise ProtocolError("invalid_params", "enable/disable 至少提供其一", path="params")

    yaml_path = Path(yaml_raw)
    try:
        original = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProtocolError(
            "source_file_unreadable", f"品类 YAML 不可读: {yaml_path} ({exc})", path="params.file"
        ) from exc
    try:
        doc = yaml.safe_load(original)
    except yaml.YAMLError as exc:
        raise ProtocolError(
            "category_invalid", f"品类 YAML 不是合法 YAML: {exc}", path="params.file"
        ) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("sources"), list):
        raise ProtocolError(
            "category_invalid", f"{yaml_path} 不是品类 YAML(缺少 sources 节)", path="params.file"
        )

    sources = list(doc["sources"])
    stash = _load_disabled_stash(yaml_path)
    enabled_names = _source_names(sources, where="sources")
    disabled_names = _source_names(stash, where="disabled_stash")

    unknown = [name for name in disable if name not in enabled_names]
    unknown += [name for name in enable if name not in disabled_names]
    if unknown:
        raise ProtocolError(
            "source_unknown",
            f"名单不在可写范围: {sorted(set(unknown))}"
            f"(现启用={enabled_names},现停用={disabled_names})",
            path="params",
            data={"enabled": enabled_names, "disabled": disabled_names},
        )
    if not [name for name in enabled_names if name not in set(disable)]:
        raise ProtocolError(
            "last_source",
            "拒绝停用最后一个启用源(品类必须保留至少一个源)",
            path="params.disable",
        )

    # dict 保序 + 同名后到覆盖:搬运天然去重(崩溃窗口下的双份残留自愈)。
    keep_enabled = {
        name: source for name, source in zip(enabled_names, sources)
        if name not in set(disable)
    }
    back_enabled = {
        name: source for name, source in zip(disabled_names, stash) if name in set(enable)
    }
    rest_stash = {
        name: source for name, source in zip(disabled_names, stash) if name not in set(enable)
    }
    out_stash = {
        name: source for name, source in zip(enabled_names, sources) if name in set(disable)
    }
    # 保留源维持原序在前,重新启用的源按移入顺序追加在后(dict 保序,同名
    # 后到覆盖天然去重 —— 崩溃窗口下的双份残留自愈)。
    new_sources = list({**keep_enabled, **back_enabled}.values())
    new_stash = list({**rest_stash, **out_stash}.values())
    doc["sources"] = new_sources

    try:  # 往返一致门:与 myia run 同一装载器,失败零写入
        load_category(doc, source=str(yaml_path))
    except LoadError as exc:
        details = [
            {"path": item.path, "code": item.error_type, "message": item.message}
            for item in exc.errors
        ]
        raise ProtocolError(
            "category_invalid",
            f"写回后的源名单未过品类校验: {details}",
            path="params.file",
            data={"errors": details},
        ) from exc

    new_text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    stash_file = _stash_path(yaml_path)
    try:
        # 止血(task 10-03-yaml-editor 决议 2):下面的 safe_dump 重写会抹掉
        # 全部注释,覆盖前先把原文留底 <yaml>.bak(单份滚动);注释保真的根治
        # (文本手术)在 10-03-yaml-toggle-comments,此处只兜底不动主逻辑。
        shutil.copy2(yaml_path, yaml_path.with_suffix(yaml_path.suffix + ".bak"))
        # 先暂存后主文件:中途崩溃的最坏情形是「源同时在两处」(无损、可自愈),
        # 反过来则可能只存在于被覆盖的主文件里。
        if new_stash:
            _atomic_write_text(
                stash_file, json.dumps(new_stash, ensure_ascii=False, indent=2) + "\n"
            )
        elif stash_file.exists():
            stash_file.unlink()
        _atomic_write_text(yaml_path, new_text)
    except OSError as exc:
        raise ProtocolError(
            "source_write_failed", f"写回失败: {exc}", path="params.file"
        ) from exc
    return {
        "file": str(yaml_path),
        "written": True,
        "enabled": [str(source["name"]) for source in new_sources],
        "disabled": [str(source["name"]) for source in new_stash],
    }


# ---------------------------------------------------------------------------
# 方法:yaml.list / read / validate / template / save / delete
# (配置编辑器协议,task 10-03-yaml-editor;契约钉死于任务档 design.md §1,
#  TS 侧 ui-src/screens/yaml-editor/api.ts 注释互指)
# ---------------------------------------------------------------------------

#: 单个品类 YAML 的读写上限(字节):品类 YAML 百行量级,防误开/误写无关大文件。
#: 读堵不写堵等于没堵,yaml.read 与 yaml.save 共用同一常量(design §1)。
YAML_MAX_BYTES = 1024 * 1024

#: 最小合法品类模板(yaml.template 应答原文;UI 只回填 id/name)。
#: 协议侧维护 = schema 的家乡在此,模板随 schema 同仓演进,pytest 锁
#: ``load_category(safe_load(template))`` 必过 —— schema 演进时模板不会静默腐烂。
#: 完整 12 节权威示例见仓库 ``plugins/stocks.yaml``(头注释即指路标)。
_CATEGORY_TEMPLATE = """# 最小品类模板:必填仅 id/name/schedule + sources[](源必填 name/url,
# engine 缺省 auto、extract 缺省 L3 兜底、push 缺省空表)。
# 完整 12 节示例与逐字段注释见 plugins/stocks.yaml(权威示例);
# 凭据只允许 env:VAR / keychain:NAME 引用,明文即拒载(security-baseline)。
id: my-category
name: 我的品类
schedule: "0 9 * * *"
sources:
  - name: example
    url: "https://example.com/"
"""


def _fence_yaml_path(file_raw: Any) -> tuple[Path, ServeContext]:
    """yaml.* 方法的路径围栏:后缀 + resolve 后必须位于 plugins 目录内。

    安全底线(task 10-03-yaml-editor design §1):sidecar 是 UI 直连的读写
    通道,不设围栏 = 桌面端任意文件读写原语。三道门:

    1. 后缀 ∈ {``.yaml``, ``.yml``} → 否则 ``not_yaml_suffix``;
    2. ``Path.resolve()``(消解符号链接与 ``..``)后 ``os.path.commonpath``
       判定必须位于 serve 上下文 plugins 目录之下 → 否则 ``path_outside_root``
       (message 带两个路径,如实展示;不做字符串前缀比对,Windows 分隔符安全);
    3. 新建场景的 stem 检查在 :func:`_m_yaml_save` 内(file 不存在时才适用)。

    Returns:
        (resolve 后的目标路径, serve 上下文)—— 后续读写都落在 resolve 结果上,
        目录内符号链接指向目录内文件属合法形态。
    """
    if not isinstance(file_raw, str) or not file_raw:
        raise ProtocolError("invalid_params", "缺少文件路径 file", path="params.file")
    ctx = _serve_context()
    root = Path(ctx.plugins_dir).resolve()
    path = Path(file_raw).expanduser()
    if path.suffix not in (".yaml", ".yml"):
        raise ProtocolError(
            "not_yaml_suffix", f"文件后缀必须是 .yaml 或 .yml: {file_raw}", path="params.file"
        )
    resolved = path.resolve()
    try:
        inside = os.path.commonpath([str(resolved), str(root)]) == str(root)
    except ValueError:  # 跨盘符等无公共前缀情形,一律按越界处理
        inside = False
    if not inside:
        raise ProtocolError(
            "path_outside_root",
            f"文件路径越出 plugins 目录: {resolved}(根: {root})",
            path="params.file",
            data={"resolved": str(resolved), "root": str(root)},
        )
    return resolved, ctx


def _yaml_files(plugins_dir: Path) -> list[Path]:
    """plugins 目录内的品类 YAML 清单(``*.yaml``/``*.yml``,按文件名排序)。

    非递归 glob:``.disabled.json``/``.bak``/``.seeded`` 天然不命中,
    ``myia-*/plugin.yaml`` 市场子目录不入列(health 扫描同款约定)。
    """
    if not plugins_dir.is_dir():
        raise ProtocolError(
            "source_dir_unreadable", f"plugins 目录不可读: {plugins_dir}", path="params"
        )
    files = {path for pattern in ("*.yaml", "*.yml") for path in plugins_dir.glob(pattern)}
    return sorted(files, key=lambda path: path.name)


def _yaml_file_entry(path: Path) -> dict[str, Any]:
    """yaml.list 单项:解析状态 + 品类元信息(坏文件也入列,parse_ok=false)。"""
    item: dict[str, Any] = {
        "file": str(path),
        "name": path.name,
        "parse_ok": False,
        "category_id": None,
        "category_name": None,
        "sources": None,
        "error": None,
    }
    try:
        config = load_category_file(path)
    except LoadError as exc:
        first = exc.errors[0]
        item["error"] = {"path": first.path, "code": first.error_type, "message": first.message}
        return item
    item["parse_ok"] = True
    item["category_id"] = config.id
    item["category_name"] = config.name
    item["sources"] = len(config.sources)
    return item


def _collect_category_ids(plugins_dir: Path, *, exclude: Path) -> dict[str, Path]:
    """目录扫描取 parse_ok 文件们的品类 id(yaml.save 跨文件查重用)。

    现状 loader 对重复 id 零守卫 —— 两个 ``id: stocks`` 会静默混品类,
    写盘门是唯一能拦的地方;同 id 多文件时取首个(字典序)保持确定性。
    """
    ids: dict[str, Path] = {}
    for path in _yaml_files(plugins_dir):
        if path.resolve() == exclude:
            continue
        try:
            config = load_category_file(path)
        except LoadError:
            continue  # 坏文件本就装不起来,不参与 id 空间
        ids.setdefault(config.id, path)
    return ids


def _iter_keychain_refs(node: Any, path: str = "$"):
    """递归枚举文档中的 ``keychain:`` 引用,产出 ``(字段路径, 凭据名)``。

    ``env:`` 不对照(名单运行时才知,必误报 —— 决议 8);引用语法判定复用
    schema 的 :data:`_SECRET_REF_RE`(同一先例:cli.py/manifest.py 受控复用),
    非 ref 形态的普通字符串直接跳过。
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _iter_keychain_refs(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_keychain_refs(value, f"{path}[{index}]")
    elif isinstance(node, str):
        match = _SECRET_REF_RE.match(node)
        if match is not None and match.group("kc") is not None:
            yield path, match.group("kc")


def _validate_yaml_content(
    content: str, *, source: str | None = None
) -> tuple[list[dict[str, Any]], CategoryConfig | None]:
    """yaml.validate / yaml.save 共享的同一道校验门(防两门漂移)。

    流程:``_UniqueKeyLoader`` 解析(重复键即 ``yaml_parse_error``)→
    :func:`load_category` 同门校验(与 ``myia run`` 一致)→ ``keychain:``
    引用对照已录入凭据名单。**不落盘** —— validate 干跑零写入由调用方保证,
    save 在此之后才碰文件系统。

    Returns:
        (findings, config):findings 每项 ``{path, code, message, level}``,
        ``level`` ∈ error/warning(warning 不拦保存 —— 先写 YAML 后
        ``myia secret set`` 补凭据是合法流,决议 8);config 为装载成功的
        品类模型(用于 save 的跨文件查重与 validate 的 category 摘要)。
    """
    findings: list[dict[str, Any]] = []
    try:
        doc = yaml.load(content, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        findings.append(
            {"path": "$", "code": "yaml_parse_error",
             "message": f"YAML 语法无法解析: {exc}", "level": "error"}
        )
        return findings, None
    config: CategoryConfig | None = None
    try:
        config = load_category(doc if isinstance(doc, Mapping) else doc, source=source)
    except LoadError as exc:
        for detail in exc.errors:
            findings.append(
                {"path": detail.path, "code": detail.error_type,
                 "message": detail.message, "level": "error"}
            )
    if isinstance(doc, Mapping):
        try:
            known = set(list_secrets())
        except SecretError:
            known = None  # 钥匙链不可用:对照降级跳过,不误报 warning
        if known is not None:
            for ref_path, name in _iter_keychain_refs(doc):
                if name not in known:
                    findings.append(
                        {"path": ref_path, "code": "secret_unknown", "level": "warning",
                         "message": f"凭据 {name} 尚未录入钥匙链"
                                    f"(先 myia secret set {name} 或桌面端凭据页录入;"
                                    f"保存不受影响,补录前相关源会采集失败)"}
                    )
    return findings, config


def _m_yaml_list(params: dict[str, Any]) -> dict[str, Any]:
    """plugins 目录的品类 YAML 清单(目录由 serve 上下文定,不开放目录参数)。

    目录扫描而非取自 health:**坏文件也入列**(parse_ok=false + error)——
    编辑器的核心用例之一就是修好 health 加载不了的文件。
    """
    ctx = _serve_context()
    plugins_dir = Path(ctx.plugins_dir)
    return {
        "plugins_dir": str(plugins_dir),
        "files": [_yaml_file_entry(path) for path in _yaml_files(plugins_dir)],
    }


def _m_yaml_read(params: dict[str, Any]) -> dict[str, Any]:
    """品类 YAML 原文直读:不经任何 yaml dump 往返,注释/顺序逐字节原样。

    ``newline=""`` 是硬要求:Python 默认 universal newlines 会把 CRLF 读成
    LF,写回即变字节 —— 「逐字节保真」承诺靠它成立(design §1);失败一律
    结构化(invalid_encoding / file_too_large / file_not_found)。
    """
    resolved, _ctx = _fence_yaml_path(params.get("file"))
    try:
        stat = resolved.stat()
    except FileNotFoundError as exc:
        raise ProtocolError(
            "file_not_found", f"文件不存在: {resolved}", path="params.file"
        ) from exc
    except OSError as exc:
        raise ProtocolError(
            "source_file_unreadable", f"文件不可读: {resolved} ({exc})", path="params.file"
        ) from exc
    if stat.st_size > YAML_MAX_BYTES:
        raise ProtocolError(
            "file_too_large",
            f"文件超过 1 MiB 上限: {stat.st_size} 字节(limit={YAML_MAX_BYTES})",
            path="params.file",
            data={"size": stat.st_size, "limit": YAML_MAX_BYTES},
        )
    try:
        with open(resolved, encoding="utf-8", newline="") as handle:
            content = handle.read()
    except UnicodeDecodeError as exc:
        raise ProtocolError(
            "invalid_encoding",
            f"文件不是有效的 UTF-8 编码: {resolved} ({exc});请转存 UTF-8 后重试",
            path="params.file",
        ) from exc
    except OSError as exc:
        raise ProtocolError(
            "source_file_unreadable", f"文件不可读: {resolved} ({exc})", path="params.file"
        ) from exc
    return {"file": str(resolved), "content": content, "size": stat.st_size, "mtime": stat.st_mtime}


def _m_yaml_validate(params: dict[str, Any]) -> dict[str, Any]:
    """干跑校验:findings 分级返回,**永不抛校验错**(校验结果即正常应答)。

    ``valid`` = 无 error 级 finding(warning 不翻假);``file`` 仅作错误
    上下文,可不带。零写入(design §1:validate 干跑不落盘)。
    """
    content = params.get("content")
    if not isinstance(content, str):
        raise ProtocolError("invalid_params", "缺少字符串字段 content", path="params.content")
    file_raw = params.get("file")
    source = file_raw if isinstance(file_raw, str) and file_raw else None
    findings, config = _validate_yaml_content(content, source=source)
    category = None
    if config is not None:
        category = {"id": config.id, "name": config.name, "sources": len(config.sources)}
    return {
        "valid": not any(finding["level"] == "error" for finding in findings),
        "findings": findings,
        "category": category,
    }


def _m_yaml_template(params: dict[str, Any]) -> dict[str, Any]:
    """最小合法品类模板(id/name 占位,UI 按用户输入回填;决议 6 单模板)。"""
    return {"content": _CATEGORY_TEMPLATE}


def _m_yaml_save(params: dict[str, Any]) -> dict[str, Any]:
    """编辑写回:同门校验 error 级零容忍零写入 → 跨文件 id 查重 → ``.bak`` → 原子落盘。

    流程钉序(design §1):**围栏+stem 正则 → :func:`_validate_yaml_content`
    (error 级零容忍;warning 收集透传)→ 跨文件品类 id 查重 → 旧文件拷
    ``.bak``(新建无此步)→ :func:`_atomic_write_text`**(写用户原文,
    ``newline=""`` 逐字节保真)。

    - 新建语义:file 不存在 + ``expected_mtime=null`` = 创建;file 不存在 +
      非 null mtime = ``file_not_found``(「想改却不存在」与「想建」分开,
      防路径手误建出影子文件)。新建 stem 必须过品类 id 同款正则
      :data:`CATEGORY_ID_RE`,防 ``My Category.yaml`` 脏名进 plugins 目录。
    - 乐观锁:yaml.read 带回 mtime,save 对照;不符即 ``mtime_conflict``
      (与 sources.write 两条写路径靠它互斥,不靠运气)。file 已存在 +
      null mtime = 新建意图撞上已有文件,同样 ``mtime_conflict`` —— 绝不
      无锁覆盖。"""
    file_raw = params.get("file")
    content = params.get("content")
    if not isinstance(file_raw, str) or not file_raw:
        raise ProtocolError("invalid_params", "缺少文件路径 file", path="params.file")
    if not isinstance(content, str):
        raise ProtocolError("invalid_params", "缺少字符串字段 content", path="params.content")
    expected_mtime = params.get("expected_mtime")
    if expected_mtime is not None and (
        isinstance(expected_mtime, bool) or not isinstance(expected_mtime, (int, float))
    ):
        raise ProtocolError(
            "invalid_params", "expected_mtime 必须为数字或 null", path="params.expected_mtime"
        )

    resolved, ctx = _fence_yaml_path(file_raw)
    if len(content.encode("utf-8")) > YAML_MAX_BYTES:
        raise ProtocolError(
            "file_too_large",
            f"内容超过 1 MiB 上限: {len(content.encode('utf-8'))} 字节(limit={YAML_MAX_BYTES})",
            path="params.content",
            data={"size": len(content.encode("utf-8")), "limit": YAML_MAX_BYTES},
        )

    exists = resolved.exists()
    created = not exists
    if created:  # 新建场景:stem 正则 → mtime 语义(「想建」必须显式声明 null)
        if not CATEGORY_ID_RE.match(resolved.stem):
            raise ProtocolError(
                "invalid_file_stem",
                f"新建文件名 {resolved.name} 不合规:stem 必须过品类 id 同款正则"
                f" {CATEGORY_ID_RE.pattern}(小写字母/数字/``-``/``_``,1-64 字符;"
                f"中文名放 name: 字段)",
                path="params.file",
            )
        if expected_mtime is not None:
            raise ProtocolError(
                "file_not_found",
                f"目标文件不存在且携带非空 expected_mtime(非新建意图): {resolved};"
                f"请核对路径,或以 expected_mtime=null 显式新建",
                path="params.file",
            )
    else:
        current_mtime = resolved.stat().st_mtime
        if expected_mtime is None:
            raise ProtocolError(
                "mtime_conflict",
                f"目标文件已存在,新建意图(expected_mtime=null)撞上已有文件: {resolved};"
                f"请先 yaml.read 重读后再保存",
                path="params.expected_mtime",
            )
        if float(expected_mtime) != current_mtime:
            raise ProtocolError(
                "mtime_conflict",
                f"文件已被外部改动(CLI/别的窗口/启停开关),读时 mtime="
                f"{expected_mtime},当前 mtime={current_mtime};请 yaml.read 重读后再保存",
                path="params.expected_mtime",
                data={"expected_mtime": expected_mtime, "current_mtime": current_mtime},
            )

    findings, config = _validate_yaml_content(content, source=str(resolved))
    errors = [finding for finding in findings if finding["level"] == "error"]
    if errors or config is None:
        raise ProtocolError(
            "category_invalid",
            f"内容未过品类校验(error 级 {len(errors)} 处),零写入",
            path="params.content",
            data={"errors": errors},
        )
    conflict = _collect_category_ids(Path(ctx.plugins_dir), exclude=resolved).get(config.id)
    if conflict is not None:
        raise ProtocolError(
            "duplicate_category_id",
            f"品类 id {config.id!r} 已被 {conflict} 使用(重复 id 会静默混品类)",
            path="params.content",
            data={"conflicts": [str(conflict)]},
        )

    backed_up: str | None = None
    try:
        if exists:  # 旧文件留底 .bak(单份滚动);新建无此步
            bak = resolved.with_suffix(resolved.suffix + ".bak")
            shutil.copy2(resolved, bak)
            backed_up = str(bak)
        _atomic_write_text(resolved, content)
    except OSError as exc:
        raise ProtocolError(
            "source_write_failed", f"写回失败: {exc}", path="params.file"
        ) from exc
    return {
        "file": str(resolved),
        "written": True,
        "created": created,
        "backed_up": backed_up,
        "mtime": resolved.stat().st_mtime,
        "warnings": [finding for finding in findings if finding["level"] == "warning"],
    }


def _m_yaml_delete(params: dict[str, Any]) -> dict[str, Any]:
    """删除品类文件:围栏 → 拷 ``.bak`` → 删主文件 → 连带删 ``.disabled.json``。

    钉序的崩溃方向性:先备份后删,中途崩溃最坏 = 文件还在(无损方向)。
    ``.disabled.json`` 暂存归启停管,编辑器不碰 —— 唯一例外就是删除品类后的
    连带清理(暂存无主,留着是脏文件)。全删光 = 合法空态:``.seeded`` 标志
    在即不复种,feed 空态「新建」CTA 引导重建,恰好闭环。
    """
    resolved, _ctx = _fence_yaml_path(params.get("file"))
    if not resolved.exists():
        raise ProtocolError("file_not_found", f"文件不存在: {resolved}", path="params.file")
    bak = resolved.with_suffix(resolved.suffix + ".bak")
    try:
        shutil.copy2(resolved, bak)
        resolved.unlink()
    except OSError as exc:
        raise ProtocolError(
            "source_write_failed", f"删除失败(已先留底 {bak}): {exc}", path="params.file"
        ) from exc
    with contextlib.suppress(OSError):  # 暂存清理是尽力而为:主契约已完成
        _stash_path(resolved).unlink(missing_ok=True)
    return {"file": str(resolved), "deleted": True, "backed_up": str(bak)}


# ---------------------------------------------------------------------------
# 方法:run.start / run.status / logs.tail(run 子进程 + 注册表 + 日志流)
# ---------------------------------------------------------------------------

_RUNS_LOCK = threading.Lock()
_RUNS: dict[int, dict[str, Any]] = {}
_NEXT_RUN_ID = 0
_ACTIVE_RUN_ID: int | None = None

#: pipeline 结构化日志(logging 规范 key=value 形态)→ 进度事件。
#: 日志文案变化时优雅退化(进度事件停发,log 事件照常),不影响正确性。
_PROGRESS_PATTERNS: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (re.compile(r"运行开始 category=(\S+) run_id=(\S+) sources=(\S+) dry_run=(\S+)"), "run_start",
     ("category", "pipeline_run_id", "sources", "dry_run")),
    (re.compile(r"采集完成 source=(\S+) engine=(\S+) items=(\S+)"), "source_done",
     ("source", "engine", "items")),
    (re.compile(r"采集步骤完成 sources=(\S+) items=\S+ source_failures=(\S+)"), "fetch_done",
     ("sources", "source_failures")),
    (re.compile(r"运行结束 category=(\S+) run_id=(\S+) status=(\S+) items=(\S+)"), "run_end",
     ("category", "pipeline_run_id", "run_status", "items")),
)


def _self_command(argv_tail: list[str]) -> tuple[list[str], dict[str, str]]:
    """构造 run 子进程命令:冻结包自启(直通模式)/ dev 下 ``python -m myia.cli``。

    dev 下子进程未必装了 myia(conftest 靠 sys.path 注入 src/),以
    PYTHONPATH 指到 <repo>/src 保证可复现;冻结模式 PyInstaller 包自带全部模块。
    """
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        return [sys.executable, *argv_tail], env
    src = Path(__file__).resolve().parent.parent / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    return [sys.executable, "-m", "myia.cli", *argv_tail], env


def _emit_progress(run_id: int, line: str) -> None:
    for pattern, phase, fields in _PROGRESS_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        event = {"type": "progress", "run_id": run_id, "phase": phase, "ts": _now_iso()}
        event.update(dict(zip(fields, match.groups())))
        _write_line(event)
        return


def _pump_stream(run_id: int, stream_obj: Any, stream_name: str) -> None:
    """逐行转发子进程输出:log 事件 + 环形缓冲;stderr 兼做进度信号源。"""
    for raw in stream_obj:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        _ring_append(run_id, stream_name, line)
        _write_line({"type": "log", "run_id": run_id, "stream": stream_name, "line": line, "ts": _now_iso()})
        if stream_name == "stderr":
            _emit_progress(run_id, line)


def _run_worker(run_id: int, cmd: list[str], env: dict[str, str], *, dry: bool, db: str,
                yaml_path: str, started_at: str, wall_start: float) -> None:
    """后台线程:跑 run 子进程 → 流式 log/progress 事件 → completed 事件。"""
    global _ACTIVE_RUN_ID
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        readers = [
            threading.Thread(target=_pump_stream, args=(run_id, proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=_pump_stream, args=(run_id, proc.stderr, "stderr"), daemon=True),
        ]
        for thread in readers:
            thread.start()
        exit_code = proc.wait()
        for thread in readers:
            thread.join(timeout=10)
        record: dict[str, Any] | None = None
        # 非 dry 且采集语义退出码:runs 表有本 run 的持久化记录(stats/steps)。
        if not dry and exit_code in (0, 2, 3):
            store = None
            try:
                store = SQLiteStore(db)
                latest = store.latest_run()
                if latest is not None:
                    record = {
                        "run_id": latest.id,
                        "category": latest.category,
                        "status": latest.status,
                        "started_at": latest.started_at.isoformat() if latest.started_at else None,
                        "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
                        "stats": latest.stats,
                        "steps": latest.steps,
                        "error": latest.error,
                    }
            except Exception:  # noqa: BLE001 — record 尽力而为,缺位不拦 completed
                record = None
            finally:
                if store is not None:
                    store.close()
        status = STATUS_BY_EXIT.get(exit_code) if exit_code is not None else None
        if record is not None:
            status = record["status"]
        duration_ms = int((datetime.now(timezone.utc).timestamp() - wall_start) * 1000)
        entry = _RUNS[run_id]
        entry.update(state="done", exit_code=exit_code, status=status,
                     finished_at=_now_iso(), duration_ms=duration_ms, record=record)
        _write_line({
            "type": "completed", "run_id": run_id, "exit_code": exit_code, "status": status,
            "dry": dry, "duration_ms": duration_ms, "record": record, "ts": _now_iso(),
        })
    except Exception as exc:  # noqa: BLE001 — 工作线程兜底:错误必须以事件形式可见
        entry = _RUNS[run_id]
        entry.update(state="done", exit_code=None, status=None, finished_at=_now_iso(),
                     error=f"{type(exc).__name__}: {exc}")
        _write_line({"type": "completed", "run_id": run_id, "exit_code": None, "status": None,
                     "dry": dry, "error": f"{type(exc).__name__}: {exc}", "ts": _now_iso()})
    finally:
        with _RUNS_LOCK:
            if _ACTIVE_RUN_ID == run_id:
                _ACTIVE_RUN_ID = None


def _m_run_start(params: dict[str, Any]) -> dict[str, Any]:
    """启动 run 子进程(立即返回);dry/real 与退出码语义由 CLI 层保证。"""
    global _NEXT_RUN_ID, _ACTIVE_RUN_ID
    yaml_path = params.get("yaml")
    if not isinstance(yaml_path, str) or not yaml_path:
        raise ProtocolError("invalid_params", "缺少品类 YAML 路径 yaml", path="params.yaml")
    dry = bool(params.get("dry", False))
    db = str(params.get("db") or _serve_context().db)
    with _RUNS_LOCK:
        if _ACTIVE_RUN_ID is not None:
            raise ProtocolError(
                "run_busy", f"已有 run 在执行 run_id={_ACTIVE_RUN_ID}(桌面单飞;请等待 completed 事件)",
                data={"active_run_id": _ACTIVE_RUN_ID},
            )
        _NEXT_RUN_ID += 1
        run_id = _NEXT_RUN_ID
        _ACTIVE_RUN_ID = run_id
    argv_tail = ["run", yaml_path, "--db", db]
    if dry:
        argv_tail.append("--dry-run")
    if params.get("config"):
        argv_tail += ["--config", str(params["config"])]
    cmd, env = _self_command(argv_tail)
    started_at = _now_iso()
    _RUNS[run_id] = {
        "run_id": run_id, "yaml": yaml_path, "db": db, "dry": dry, "state": "running",
        "exit_code": None, "status": None, "started_at": started_at,
        "finished_at": None, "duration_ms": None, "record": None,
    }
    threading.Thread(
        target=_run_worker,
        args=(run_id, cmd, env), kwargs={"dry": dry, "db": db, "yaml_path": yaml_path,
                                         "started_at": started_at,
                                         "wall_start": datetime.now(timezone.utc).timestamp()},
        daemon=True,
    ).start()
    return {"run_id": run_id, "state": "running", "yaml": yaml_path, "dry": dry, "db": db}


def _m_run_status(params: dict[str, Any]) -> dict[str, Any]:
    """run 注册表查询;run_id 缺省 = 全部(新→旧),未知 id = 结构化 404。"""
    run_id = params.get("run_id")
    with _RUNS_LOCK:
        if run_id is not None:
            entry = _RUNS.get(run_id)
            if entry is None:
                raise ProtocolError("run_not_found", f"无此 run_id: {run_id}", path="params.run_id")
            runs = [dict(entry)]
        else:
            runs = [dict(_RUNS[key]) for key in sorted(_RUNS, reverse=True)]
    return {"runs": runs}


def _m_logs_tail(params: dict[str, Any]) -> dict[str, Any]:
    """环形缓冲尾部;lines 上限 = 缓冲容量,run_id 可选过滤。"""
    lines = params.get("lines", 200)
    if not isinstance(lines, int) or lines < 1:
        raise ProtocolError("invalid_params", "lines 必须为正整数", path="params.lines")
    lines = min(lines, LOG_RING_CAPACITY)
    run_id = params.get("run_id")
    with _RING_LOCK:
        snapshot = list(_LOG_RING)
    if run_id is not None:
        snapshot = [entry for entry in snapshot if entry["run_id"] == run_id]
    return {
        "lines": snapshot[-lines:],
        "total": len(snapshot),
        "truncated": len(snapshot) > lines,
    }


# ---------------------------------------------------------------------------
# 方法:image.import / ocr / analyze / status / config.read / config.save
# (看图协议,task 10-03-image-input;契约钉死于任务档 design.md §sidecar 协议,
#  TS 侧 ui-src/lib/api/types.ts + screens/image/api.ts 注释互指;
#  能力实现在 myia.vision 包:ocr=双引擎 OCR、client=OpenAI 兼容 VL、
#  settings=vision.yaml 同门校验)
# ---------------------------------------------------------------------------

#: 单图入库上限(字节;PRD v1 拍板 10MB,防误投超大图拖垮 OCR/VL)。
IMAGE_MAX_BYTES = 10 * 1024 * 1024
#: 图片 id 形状:sha256 前 16 位十六进制(入库生成;id 白名单 = 路径穿越免疫)。
_IMAGE_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
#: describe 模式固定结构化中文 prompt(local-ocr 技能 2026-10-01 裁定配方)。
_IMAGE_PROMPT_DESCRIBE = (
    "用中文详细解读这张图片,按以下结构输出:1.主体(人/物+姿态神态) "
    "2.构图(视角/主体位置/前中远景) 3.风格与色彩(流派/色调/光影) "
    "4.氛围与意境 5.技术判断(是否疑似 AI 生成+依据)"
)
#: read 模式校对 prompt(local-ocr 09-30 实证配方:只修正确有出入的字,
#: 金额/ID 逐位复核提示由 UI 呈现)。
_IMAGE_PROMPT_PROOFREAD = "对照图片逐行校对此 OCR 初稿,只修正确有出入的字:\n{draft}"
#: 本地服务未起的启动指引(image_unreachable 附带;local-ocr 三步配方摘录)。
_IMAGE_LOCAL_HINT = (
    "本地视觉服务未起?启动:uvx --from mlx-vlm mlx_vlm.server "
    "--model <MYIA_HOME>/models/qwen3-vl-8b-mlx --host 127.0.0.1 --port 8080"
    "(LM Studio 备选 http://127.0.0.1:1234/v1);详见看图设置"
)

_IMAGE_LOCK = threading.Lock()
_IMAGE_ACTIVE_JOB: int | None = None
_IMAGE_NEXT_JOB_ID = 0


class _ProbeAuthError(Exception):
    """探活命中 401/403:端点拒绝鉴权(key 无效/未生效),区别于不可达。"""


def _vision_yaml_path(ctx: ServeContext) -> Path:
    """vision.yaml 路径:home 模式落数据根;dev 回退 cwd 相对(与 db/plugins 同约)。"""
    return ctx.home / VISION_FILE_NAME if ctx.home is not None else Path(VISION_FILE_NAME)


def _images_dir(ctx: ServeContext) -> Path:
    """图片库目录 ``<home>/images``;dev 回退 cwd/images(入库时才创建)。"""
    return ctx.home / "images" if ctx.home is not None else Path("images")


def _load_vision(ctx: ServeContext) -> VisionConfig:
    """装载 vision.yaml(VisionConfigError → image_config_invalid,fail fast)。"""
    try:
        return load_vision_config(_vision_yaml_path(ctx))
    except VisionConfigError as exc:
        raise ProtocolError(
            "image_config_invalid", f"vision 配置拒载: {exc}", path="vision.yaml", data=exc.to_dict()
        ) from exc


def _sniff_image_ext(data: bytes) -> str | None:
    """魔数嗅探图片格式(扩展名/mime 不作信任源;heic 由调用方转 png 后收)。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12] in (b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1")
    ):
        return "heic"
    return None


def _convert_heic_to_png(data: bytes) -> bytes:
    """heic → png(系统 sips;设计拍板:heic 先转 png 后收,库内格式归一)。"""
    handle, heic_name = tempfile.mkstemp(suffix=".heic")
    png_path = Path(heic_name).with_suffix(".png")
    try:
        with os.fdopen(handle, "wb") as tmp_file:
            tmp_file.write(data)
        proc = subprocess.run(
            ["sips", "-s", "format", "png", heic_name, "--out", str(png_path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise ProtocolError(
                "image_unsupported",
                f"heic 转 png 失败(sips 退出码 {proc.returncode}): {proc.stderr.strip()[:200]}",
                path="params.value",
            )
        converted = png_path.read_bytes()
    except OSError as exc:
        raise ProtocolError("image_unsupported", f"heic 转换失败: {exc}", path="params.value") from exc
    finally:
        Path(heic_name).unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
    if _sniff_image_ext(converted) != "png":
        raise ProtocolError("image_unsupported", "heic 转换产物不是有效 png", path="params.value")
    return converted


def _image_file_by_id(id_raw: Any, ctx: ServeContext) -> Path:
    """按 id 定位库内图片;id 先过十六进制白名单(杜绝路径穿越),再查文件。"""
    if not isinstance(id_raw, str) or _IMAGE_ID_RE.fullmatch(id_raw) is None:
        raise ProtocolError("image_not_found", f"无此图片 id: {id_raw!r}", path="params.id")
    images = _images_dir(ctx)
    if images.is_dir():
        for candidate in sorted(images.glob(f"{id_raw}.*")):
            if candidate.is_file():
                return candidate
    raise ProtocolError(
        "image_not_found", f"图片库中无 id={id_raw}({images})", path="params.id"
    )


def _m_image_import(params: dict[str, Any]) -> dict[str, Any]:
    """图片入库:魔数嗅探 → heic 转 png → sha256 前 16 位去重落 images/<id>.<ext>。

    ``kind=path`` 读本地文件(拖拽/选择);``kind=base64`` 收 data URL 或裸
    base64。同一字节流永远同名(去重幂等);v1 不自动清理(PRD 拍板,清理工具 v2)。
    """
    kind = params.get("kind", "path")
    value = params.get("value")
    if not isinstance(value, str) or not value:
        raise ProtocolError("invalid_params", "缺少图片内容 value(路径或 base64)", path="params.value")
    if kind == "path":
        source = Path(value).expanduser()
        try:
            data = source.read_bytes()
        except FileNotFoundError as exc:
            raise ProtocolError("image_not_found", f"图片文件不存在: {source}", path="params.value") from exc
        except OSError as exc:
            raise ProtocolError("image_not_found", f"图片文件不可读: {source} ({exc})", path="params.value") from exc
    elif kind == "base64":
        payload = value
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        payload = "".join(payload.split())
        if len(payload) > IMAGE_MAX_BYTES * 2:  # base64 膨胀 ~4/3,粗拦在解码前
            raise ProtocolError(
                "image_too_large", f"base64 内容超过 10MB 上限(limit={IMAGE_MAX_BYTES})",
                path="params.value", data={"limit": IMAGE_MAX_BYTES},
            )
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProtocolError(
                "image_unsupported", f"base64 内容无法解码为图片: {exc}", path="params.value"
            ) from exc
    else:
        raise ProtocolError("invalid_params", "kind 必须是 path 或 base64", path="params.kind")
    if len(data) > IMAGE_MAX_BYTES:
        raise ProtocolError(
            "image_too_large",
            f"图片超过 10MB 上限: {len(data)} 字节(limit={IMAGE_MAX_BYTES})",
            path="params.value", data={"size": len(data), "limit": IMAGE_MAX_BYTES},
        )
    ext = _sniff_image_ext(data)
    if ext is None:
        raise ProtocolError(
            "image_unsupported", "不支持的图片格式(支持 png/jpg/webp;heic 自动转 png 后收)",
            path="params.value",
        )
    if ext == "heic":
        data = _convert_heic_to_png(data)
        ext = "png"
        if len(data) > IMAGE_MAX_BYTES:  # 转 png 可能膨胀,落库前再拦一道
            raise ProtocolError(
                "image_too_large",
                f"heic 转 png 后超过 10MB 上限: {len(data)} 字节(limit={IMAGE_MAX_BYTES})",
                path="params.value", data={"size": len(data), "limit": IMAGE_MAX_BYTES},
            )
    image_id = hashlib.sha256(data).hexdigest()[:16]
    ctx = _serve_context()
    images = _images_dir(ctx)
    images.mkdir(parents=True, exist_ok=True)
    dest = images / f"{image_id}.{ext}"
    if not dest.exists():  # 去重:同字节流已入库则直接复用
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    return {"id": image_id, "path": str(dest), "bytes": len(data), "ext": ext}


def _m_image_ocr(params: dict[str, Any]) -> dict[str, Any]:
    """一级 OCR(双引擎):逐行 ``{text, conf}`` + engine + 耗时;缺省引擎取配置。"""
    id_raw = params.get("id")
    if not isinstance(id_raw, str) or not id_raw:
        raise ProtocolError("invalid_params", "缺少图片 id(image.import 返回)", path="params.id")
    ctx = _serve_context()
    image_path = _image_file_by_id(id_raw, ctx)
    config = _load_vision(ctx)
    engine = params.get("engine") or config.ocr_engine_default
    if engine not in OCR_ENGINES:
        raise ProtocolError(
            "image_engine_unknown",
            f"未知 OCR 引擎 {engine!r}(可选:{'/'.join(OCR_ENGINES)})",
            path="params.engine",
            data={"engine": engine, "allowed": list(OCR_ENGINES)},
        )
    started = time.monotonic()
    try:
        lines = run_ocr(image_path, engine)
    except OCRError as exc:
        if exc.code == "engine_unknown":  # 引擎名源自配置时的兜底翻译
            raise ProtocolError(
                "image_engine_unknown", str(exc), path="params.engine", data=exc.to_dict()
            ) from exc
        raise ProtocolError("image_ocr_failed", str(exc), path="params", data=exc.to_dict()) from exc
    ms = int((time.monotonic() - started) * 1000)
    return {
        "lines": [{"text": line.text, "conf": round(line.conf, 4)} for line in lines],
        "engine": engine,
        "ms": ms,
    }


def _vision_probe(base_url: str, *, api_key: str | None) -> None:
    """analyze 前轻探活(GET ``{base_url}/models``,3s;design 风险表拍板)。

    失败抛 :class:`OSError`(不可达,worker 译 ``image_unreachable``)或
    :class:`_ProbeAuthError`(401/403,译 ``image_no_credentials``)。
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=3.0) as probe:
            response = probe.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise OSError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code in (401, 403):
        raise _ProbeAuthError(f"HTTP {response.status_code}")
    if response.status_code >= 400:
        raise OSError(f"HTTP {response.status_code}")


def _image_analyze_worker(
    job_id: int,
    *,
    image_path: Path,
    mode: str,
    question: str | None,
    channel: str,
    base_url: str,
    model: str,
    api_key: str | None,
    ocr_engine: str,
) -> None:
    """后台线程:read 先取 OCR 初稿 → 探活 → VL 调用 → image.completed 事件。

    仿 :func:`_run_worker` 的兜底模型:线程内任何失败都以 ``image.completed
    {ok: false, error}`` 事件可见,协议流不裸 traceback,应用不崩。
    """
    global _IMAGE_ACTIVE_JOB

    def _fail(code: str, message: str, data: Any = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        _write_line({"type": "image.completed", "job_id": job_id, "ok": False,
                     "error": error, "ts": _now_iso()})

    started = time.monotonic()
    try:
        ocr_used = False
        if mode == "read":
            _write_line({"type": "image.progress", "job_id": job_id, "stage": "ocr", "ts": _now_iso()})
            try:
                draft_lines = run_ocr(image_path, ocr_engine)
            except OCRError as exc:
                code = "image_engine_unknown" if exc.code == "engine_unknown" else "image_ocr_failed"
                _fail(code, f"read 模式 OCR 初稿失败: {exc}", exc.to_dict())
                return
            prompt = _IMAGE_PROMPT_PROOFREAD.format(
                draft="\n".join(line.text for line in draft_lines)
            )
            ocr_used = True
        elif mode == "describe":
            prompt = _IMAGE_PROMPT_DESCRIBE
        else:  # ask:用户问题直传
            prompt = str(question)
        try:
            _vision_probe(base_url, api_key=api_key)
        except _ProbeAuthError as exc:
            _fail("image_no_credentials",
                  f"视觉端点拒绝鉴权({base_url}): {exc};key 无效或未生效", {"base_url": base_url})
            return
        except OSError as exc:
            hint = _IMAGE_LOCAL_HINT if channel == "local" else "云端端点不可达,请检查网络与 base_url"
            _fail("image_unreachable",
                  f"视觉端点探活失败({base_url}): {exc};{hint}", {"base_url": base_url, "channel": channel})
            return
        _write_line({"type": "image.progress", "job_id": job_id, "stage": "model", "ts": _now_iso()})

        async def _call() -> VisionResult:
            client = VisionClient(base_url, model, api_key=api_key)
            try:
                return await client.analyze(image_path=image_path, prompt=prompt)
            finally:
                await client.aclose()

        result = asyncio.run(_call())
        payload: dict[str, Any] = {
            "text": result.text,
            "model": model,
            "channel": channel,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "ocr_used": ocr_used,
        }
        if ocr_used:
            payload["ocr_engine"] = ocr_engine
        _write_line({"type": "image.completed", "job_id": job_id, "ok": True,
                     "result": payload, "ts": _now_iso()})
    except Exception as exc:  # noqa: BLE001 — 工作线程兜底:错误必须以事件形式可见
        _fail("image_provider_error", f"{type(exc).__name__}: {exc}")
    finally:
        with _IMAGE_LOCK:
            if _IMAGE_ACTIVE_JOB == job_id:
                _IMAGE_ACTIVE_JOB = None


def _m_image_analyze(params: dict[str, Any]) -> dict[str, Any]:
    """二级看图(read/describe/ask):提交即返 job_id,结果走事件流(壳 120s 免疫)。

    同步预检(结构化错误即时应答):id/图片存在、mode/question 形状、通道配置
    可用(本地模型路径已填 / 云端 key 引用可解析);探活与 VL 调用留给后台线程。
    单飞守卫 ``image_busy`` 照抄 ``run_busy``。
    """
    global _IMAGE_NEXT_JOB_ID, _IMAGE_ACTIVE_JOB
    id_raw = params.get("id")
    if not isinstance(id_raw, str) or not id_raw:
        raise ProtocolError("invalid_params", "缺少图片 id(image.import 返回)", path="params.id")
    mode = params.get("mode")
    if mode not in ("read", "describe", "ask"):
        raise ProtocolError("invalid_params", "mode 必须是 read|describe|ask", path="params.mode")
    question = params.get("question")
    if mode == "ask" and (not isinstance(question, str) or not question.strip()):
        raise ProtocolError("invalid_params", "ask 模式必须携带非空 question", path="params.question")
    ctx = _serve_context()
    image_path = _image_file_by_id(id_raw, ctx)
    config = _load_vision(ctx)
    channel = params.get("channel") or config.channel_default
    if channel not in CHANNELS:
        raise ProtocolError("invalid_params", f"channel 必须是 {'/'.join(CHANNELS)}", path="params.channel")
    if channel == "local":
        if not config.local_model:
            raise ProtocolError(
                "image_config_invalid",
                "本地通道未配置模型路径(看图设置 → 本地模型路径;mlx-vlm 场景即模型目录)",
                path="local.model",
            )
        base_url, model, api_key = config.local_base_url, config.local_model, None
    else:
        if not config.cloud_api_key_ref:
            raise ProtocolError(
                "image_no_credentials",
                "云端通道缺 api_key:先在看图设置录入(经 secret.set 入钥匙链 myia/image/api_key)",
                path="cloud.api_key",
            )
        try:
            api_key = resolve_credential(config.cloud_api_key_ref)
        except CredentialResolveError as exc:
            raise ProtocolError(
                "image_no_credentials",
                f"云端 api_key 引用无法解析({config.cloud_api_key_ref}): {exc}",
                path="cloud.api_key",
            ) from exc
        base_url, model = config.cloud_base_url, config.cloud_model
    with _IMAGE_LOCK:
        if _IMAGE_ACTIVE_JOB is not None:
            raise ProtocolError(
                "image_busy",
                f"已有看图任务在执行 job_id={_IMAGE_ACTIVE_JOB}(桌面单飞;请等待 image.completed 事件)",
                data={"active_job_id": _IMAGE_ACTIVE_JOB},
            )
        _IMAGE_NEXT_JOB_ID += 1
        job_id = _IMAGE_NEXT_JOB_ID
        _IMAGE_ACTIVE_JOB = job_id
    threading.Thread(
        target=_image_analyze_worker,
        kwargs=dict(
            job_id=job_id, image_path=image_path, mode=mode,
            question=question if isinstance(question, str) else None,
            channel=channel, base_url=base_url, model=model, api_key=api_key,
            ocr_engine=config.ocr_engine_default,
        ),
        daemon=True,
    ).start()
    return {"job_id": job_id, "state": "running", "mode": mode, "channel": channel}


def _m_image_status(params: dict[str, Any]) -> dict[str, Any]:
    """看图任务对账(UI 重连时):busy + 当前 job_id(空闲时省略 job_id)。"""
    with _IMAGE_LOCK:
        busy = _IMAGE_ACTIVE_JOB is not None
        active = _IMAGE_ACTIVE_JOB
    result: dict[str, Any] = {"busy": busy}
    if busy:
        result["job_id"] = active
    return result


def _m_image_config_read(params: dict[str, Any]) -> dict[str, Any]:
    """vision.yaml 脱敏读取:文件不存在 = 全缺省(合法未配置态)。"""
    ctx = _serve_context()
    path = _vision_yaml_path(ctx)
    config = _load_vision(ctx)
    return {"file": str(path), "exists": path.exists(), "config": config.to_payload()}


def _m_image_config_save(params: dict[str, Any]) -> dict[str, Any]:
    """vision.yaml 保存:同门校验(:class:`VisionConfig` 构造即校验)失败零写入。"""
    payload = params.get("config")
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_params", "缺少对象字段 config", path="params.config")
    try:
        config = VisionConfig.from_payload(payload)
    except VisionConfigError as exc:
        raise ProtocolError(
            "image_config_invalid", f"看图配置未过校验,零写入: {exc}",
            path="params.config", data=exc.to_dict(),
        ) from exc
    path = save_vision_config(_vision_yaml_path(_serve_context()), config)
    return {"ok": True, "file": str(path)}


# ---------------------------------------------------------------------------
# 分发与 serve 循环
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "version": _m_version,
    "health": _m_health,
    "plugins.list": _m_plugins_list,
    "doctor": _m_doctor,
    "run.start": _m_run_start,
    "run.status": _m_run_status,
    "logs.tail": _m_logs_tail,
    "store.items": _m_store_items,
    "secret.set": _m_secret_set,
    "secret.list": _m_secret_list,
    "sources.write": _m_sources_write,
    "yaml.list": _m_yaml_list,
    "yaml.read": _m_yaml_read,
    "yaml.validate": _m_yaml_validate,
    "yaml.template": _m_yaml_template,
    "yaml.save": _m_yaml_save,
    "yaml.delete": _m_yaml_delete,
    "image.import": _m_image_import,
    "image.ocr": _m_image_ocr,
    "image.analyze": _m_image_analyze,
    "image.status": _m_image_status,
    "image.config.read": _m_image_config_read,
    "image.config.save": _m_image_config_save,
}


def _handle_line(line: str) -> None:
    """单请求处理:分发 → 应答;一切错误结构化,协议流不裸 traceback。"""
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        _error(None, "parse_error", f"请求行不是合法 JSON: {exc}")
        return
    if not isinstance(request, dict):
        _error(None, "invalid_request", "请求必须是 JSON 对象")
        return
    rid = request.get("id")
    method = request.get("method")
    if not isinstance(method, str) or not method:
        _error(rid, "invalid_request", "缺少字符串字段 method", path="method")
        return
    handler = _HANDLERS.get(method)
    if handler is None:
        _error(rid, "method_not_found", f"未知方法 {method}", path="method",
               data={"allowed": sorted(_HANDLERS)})
        return
    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        _error(rid, "invalid_params", "params 必须是对象", path="params")
        return
    try:
        result = handler(params)
    except ProtocolError as exc:
        _error(rid, exc.code, exc.message, path=exc.path, data=exc.data)
        return
    except Exception as exc:  # noqa: BLE001 — 兜底:内部错误也必须结构化
        print(f"sidecar: internal_error on {method}: {exc}", file=sys.stderr)
        _error(rid, "internal_error", f"{type(exc).__name__}: {exc}")
        return
    if rid is not None:
        _respond(rid, result)


def serve(stdin: Any | None = None, stdout: Any | None = None) -> int:
    """RPC 服务循环:逐行读请求、写应答/事件;stdin EOF = 干净退出 0。

    Args:
        stdin: 请求流(缺省 sys.stdin;测试注入 StringIO)。
        stdout: 协议流(缺省 sys.stdout;启动即捕获,事件线程直写此对象,
            不经 sys.stdout —— run/CLI 期间的重定向不会污染协议流)。

    Returns:
        退出码:EOF 正常结束 = 0(spec python/error-handling 契约)。
    """
    global _OUT
    _OUT = stdout if stdout is not None else sys.stdout
    _startup_seed()
    source = stdin if stdin is not None else sys.stdin
    while True:
        raw = source.readline()
        if not raw:  # EOF:壳侧关闭管道 = 正常关停
            return 0
        line = raw.strip()
        if line:
            _handle_line(line)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        sys.exit(serve())
    # 直通模式:退出码 0/1/2/3 契约由 myia.cli.main 原样保证(v02 spike 兼容)。
    sys.exit(cli_main())
