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

铁律:凭据只进系统钥匙链(``secret.set`` 薄包装 myia.secrets,值不落日志/协议流);
桌面零 Docker;任何插件装不上不拦核心(doctor/list 只产 findings)。

== 退出码 ==

- serve 模式:stdin EOF(壳退出/管道关闭)= 干净退出 0;serve 循环自身致命
  异常 = 1;run 子进程的原样退出码只在 ``completed`` 事件内透传,不改进程码。
- 直通模式:= CLI 契约 0/1/2/3 原样。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import myia
import yaml
from myia.cli import DEFAULT_DB_PATH, DEFAULT_PLUGINS_DIR, main as cli_main
from myia.schema import LoadError, load_category
from myia.secrets import SecretError, list_secrets, set_secret
from myia.store import SQLiteStore, StoreSchemaError

PROTOCOL_VERSION = 1
#: 日志环形缓冲容量(行);logs.tail 的硬上限。
LOG_RING_CAPACITY = 4000
#: 单次 run 的日志事件与环形上限一致;超限仅丢最旧行。
STATUS_BY_EXIT = {0: "success", 1: "config_error", 2: "failed", 3: "partial"}


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
    """``myia list --json`` 等价:插件清单 + 源健康度 + 计数聚合。"""
    argv = [
        "list",
        "--plugins-dir", str(params.get("plugins_dir") or DEFAULT_PLUGINS_DIR),
        "--db", str(params.get("db") or DEFAULT_DB_PATH),
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
    payload["exit_code"] = code
    return payload


def _m_plugins_list(params: dict[str, Any]) -> dict[str, Any]:
    """``myia plugin list --json`` 等价:已装市场插件 + findings。"""
    argv = ["plugin", "list", "--json"]
    if params.get("dir"):
        argv += ["--dir", str(params["dir"])]
    code, payload = _cli_json(argv)
    if code != 0:
        raise _cli_error(code, payload)
    assert payload is not None
    payload["exit_code"] = code
    return payload


def _m_doctor(params: dict[str, Any]) -> dict[str, Any]:
    """``myia doctor --json`` 等价:结构化诊断(问题全在 findings,完成即 0)。"""
    argv = ["doctor"]
    for yaml_path in params.get("yamls") or []:
        argv.append(str(yaml_path))
    if params.get("plugins_dir"):
        argv += ["--plugins-dir", str(params["plugins_dir"])]
    argv += ["--db", str(params.get("db") or DEFAULT_DB_PATH)]
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
    db = params.get("db") or DEFAULT_DB_PATH
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
    """同目录临时文件 + ``os.replace`` 原子落盘(读者侧零半写状态)。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
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
    db = str(params.get("db") or DEFAULT_DB_PATH)
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
