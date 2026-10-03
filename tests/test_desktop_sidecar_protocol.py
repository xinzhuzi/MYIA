"""desktop/entry.py sidecar 协议单测(mock stdin/stdout,全方法往返)。

覆盖:10 个方法的请求→应答往返、流式事件(log/progress/completed 以 type
区分)、错误结构化透传(code/path/message)、退出码语义(serve EOF=0;run
子进程 0/1/2 原样透传;直通模式 0/1)。零外网:成功 run 走 127.0.0.1 本地
http.server(安全底线明示例外);凭据方法 monkeypatch,不触碰真实钥匙链。
"""

from __future__ import annotations

import base64
import http.server
import importlib.util
import io
import json
import socketserver
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from myia.secrets import InMemoryKeychainBackend
from myia.store import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_PATH = REPO_ROOT / "desktop" / "entry.py"

_spec = importlib.util.spec_from_file_location("desktop_entry", ENTRY_PATH)
entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry)


VALID_YAML = """
id: proto-demo
name: 协议夹具品类
schedule: "0 9 * * *"
sources:
  - name: local-api
    engine: direct_api
    url: "http://127.0.0.1:{port}/list"
    rate_limit:
      qps: 1000.0
      respect_robots: false
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.data[*].title"
        url: "$.data[*].url"
classify:
  builtin: false   # 夹具条目不属于七大类,直通(同 desktop/fixture 惯例)
push:
  - channel: stdout
"""

BAD_CRON_YAML = """
id: proto-bad
name: 非法 cron
schedule: "not-a-cron"
sources:
  - name: api
    engine: direct_api
    url: "http://127.0.0.1:9/x"
    rate_limit:
      qps: 1000.0
      respect_robots: false
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.a"
        url: "$.b"
"""

API_JSON = {"data": [{"title": "协议条目一", "url": "https://example.com/p1"},
                     {"title": "协议条目二", "url": "https://example.com/p2"}]}


# ---------------------------------------------------------------------------
# 夹具与助手
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sidecar_state(monkeypatch):
    """每个测试独立的 sidecar 内存态(run 注册表/日志环/钥匙链后端)。"""
    monkeypatch.setattr(entry, "_RUNS", {})
    monkeypatch.setattr(entry, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(entry, "_NEXT_RUN_ID", 0)
    monkeypatch.setattr(entry, "_LOG_RING", deque(maxlen=entry.LOG_RING_CAPACITY))
    monkeypatch.setattr(entry, "_LOG_SEQ", 0)
    # 看图任务态(image.analyze 单飞注册表,10-03-image-input)
    monkeypatch.setattr(entry, "_IMAGE_ACTIVE_JOB", None)
    monkeypatch.setattr(entry, "_IMAGE_NEXT_JOB_ID", 0)
    # v1.1.1 上下文隔离:ambient MYIA_HOME 不得影响任何用例(dev 模式是默认前提)
    monkeypatch.delenv("MYIA_HOME", raising=False)
    monkeypatch.delenv("MYIA_PLUGIN_DIR", raising=False)
    backend = InMemoryKeychainBackend()
    monkeypatch.setattr(entry, "set_secret", _capture_secret(backend))
    monkeypatch.setattr(entry, "list_secrets", lambda: sorted(name for _, name in backend._items))
    yield


class _SecretCapture:
    def __init__(self, backend: InMemoryKeychainBackend) -> None:
        self.backend = backend
        self.calls: list[tuple[str, str]] = []


def _capture_secret(backend: InMemoryKeychainBackend):
    capture = _SecretCapture(backend)

    def fake_set_secret(name: str, value: str) -> None:
        capture.calls.append((name, value))
        capture.backend.set_password(entry.SECRET_SERVICE if hasattr(entry, "SECRET_SERVICE") else "myia",
                                     name, value)

    fake_set_secret.capture = capture  # type: ignore[attr-defined]
    return fake_set_secret


def rpc(*requests: dict, raw_lines: list[str] | None = None) -> tuple[int, list[dict], list[dict]]:
    """整轮 RPC:写请求 → serve → 按有无 id 拆应答/事件。"""
    lines = [json.dumps(req, ensure_ascii=False) for req in requests]
    lines += raw_lines or []
    stdin = io.StringIO("".join(line + "\n" for line in lines))
    out = io.StringIO()
    code = entry.serve(stdin=stdin, stdout=out)
    responses: list[dict] = []
    events: list[dict] = []
    for line in out.getvalue().splitlines():
        obj = json.loads(line)
        (responses if "id" in obj else events).append(obj)
    return code, responses, events


def write_yaml(tmp_path: Path, text: str, name: str = "demo.yaml") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    """任意路径回固定 JSON 直 API 夹具(仅 127.0.0.1,零外网)。"""

    def do_GET(self):  # noqa: N802
        body = json.dumps(API_JSON, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def local_api():
    """临时本地直 API 服务(线程内,ephemeral 端口,测毕关闭)。"""
    with socketserver.TCPServer(("127.0.0.1", 0), _ApiHandler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield port
        srv.shutdown()


def split_stream(out: io.StringIO) -> tuple[list[dict], list[dict]]:
    """协议流拆分:有 id = 应答,有 type = 事件(与 spec 的单写锁同步快照)。"""
    with entry._WRITE_LOCK:
        text = out.getvalue()
    responses: list[dict] = []
    events: list[dict] = []
    for line in text.splitlines():
        obj = json.loads(line)
        (responses if "id" in obj else events).append(obj)
    return responses, events


def wait_completed(out: io.StringIO, run_id: int, timeout: float = 60.0) -> dict:
    """等 completed 事件(工作线程异步写 stdout;EOF 后仍在写)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for obj in split_stream(out)[1]:
            if obj.get("type") == "completed" and obj.get("run_id") == run_id:
                return obj
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内完成")


# ---------------------------------------------------------------------------
# 基础往返:version / health / plugins.list / doctor
# ---------------------------------------------------------------------------


def test_version_roundtrip():
    """version:与 myia.__version__ 一致,携带协议版本。"""
    import myia

    code, responses, events = rpc({"id": 1, "method": "version", "params": {}})
    assert code == 0
    assert events == []
    assert responses == [{"id": 1, "result": {"name": "myia", "version": myia.__version__,
                                              "protocol": entry.PROTOCOL_VERSION}}]


def test_health_roundtrip_with_summary(tmp_path):
    """health:CLI list 同源数据 + 状态计数聚合 + healthy。"""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "demo.yaml").write_text(
        VALID_YAML.replace("{port}", "9"), encoding="utf-8"
    )
    db = tmp_path / "myia.db"
    code, responses, _ = rpc({"id": 7, "method": "health",
                              "params": {"plugins_dir": str(plugins_dir), "db": str(db)}})
    assert code == 0
    result = responses[0]["result"]
    assert "error" not in responses[0]
    assert result["exit_code"] == 0
    assert result["healthy"] is True
    assert result["summary"]["plugins"] == 1
    assert result["summary"]["unknown"] == 1  # 无运行记录 → unknown(非 dead)
    plugin = result["plugins"][0]
    assert plugin["loaded"] is True
    assert plugin["sources"][0]["health"]["state"] == "unknown"


def test_health_missing_dir_structured_error(tmp_path):
    """health:目录不存在 → CLI 错误结构化透传(code=path/message 落位)。"""
    code, responses, _ = rpc({"id": 1, "method": "health",
                              "params": {"plugins_dir": str(tmp_path / "nope")}})
    assert code == 0  # serve 进程不因单请求错误退出
    error = responses[0]["error"]
    assert error["code"] == "plugins_dir"
    assert "message" in error and error["path"] == "$"
    assert error["data"]["error"] == "plugins_dir"


def test_plugins_list_roundtrip(tmp_path):
    """plugins.list:缺失安装根 = 空清单正常态(exit_code 0,与 CLI 语义一致)。"""
    code, responses, _ = rpc({"id": 2, "method": "plugins.list",
                              "params": {"dir": str(tmp_path / "no-root")}})
    result = responses[0]["result"]
    assert result["exit_code"] == 0
    assert result["plugins"] == []
    assert result["summary"]["installed"] == 0


def test_doctor_roundtrip(tmp_path):
    """doctor:完成即 0,findings 全量随行,healthy 视角与 CLI 一致。"""
    yaml_path = write_yaml(tmp_path, VALID_YAML.replace("{port}", "9"))
    db = tmp_path / "myia.db"
    code, responses, _ = rpc({"id": 3, "method": "doctor",
                              "params": {"yamls": [yaml_path], "db": str(db)}})
    result = responses[0]["result"]
    assert result["exit_code"] == 0
    assert result["healthy"] is True
    assert result["plugins"][0]["id"] == "proto-demo"
    assert isinstance(result["findings"], list)


# ---------------------------------------------------------------------------
# run.start / run.status / logs.tail(子进程 + 流式事件 + 退出码透传)
# ---------------------------------------------------------------------------


def test_run_start_success_full_roundtrip(tmp_path, local_api):
    """成功 run:立即返回 running → log/progress 流 → completed(0/success)
    → run.status done → logs.tail → store.items(数据面贯通)。"""
    yaml_path = write_yaml(tmp_path, VALID_YAML.replace("{port}", str(local_api)))
    db = tmp_path / "myia.db"
    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "run.start",
                                    "params": {"yaml": yaml_path, "db": str(db)}}) + "\n")
    assert entry.serve(stdin=stdin, stdout=out) == 0  # EOF 即回,不等 run
    responses, _ = split_stream(out)
    started = responses[0]["result"]
    run_id = started["run_id"]
    assert started["state"] == "running" and started["dry"] is False

    completed = wait_completed(out, run_id)
    assert completed["exit_code"] == 0
    assert completed["status"] == "success"
    assert completed["record"] is not None
    assert completed["record"]["status"] == "success"

    _, events = split_stream(out)
    types = {event["type"] for event in events}
    assert {"log", "progress", "completed"} <= types  # 三类事件以 type 区分
    phases = [event["phase"] for event in events if event["type"] == "progress"]
    assert "run_start" in phases and "run_end" in phases
    assert any(event["type"] == "progress" and event.get("phase") == "source_done"
               and event.get("items") == "2" for event in events)

    code, status_resp, _ = rpc({"id": 2, "method": "run.status", "params": {"run_id": run_id}})
    entry_record = status_resp[0]["result"]["runs"][0]
    assert entry_record["state"] == "done"
    assert entry_record["exit_code"] == 0 and entry_record["status"] == "success"

    code, logs_resp, _ = rpc({"id": 3, "method": "logs.tail",
                              "params": {"run_id": run_id, "lines": 200}})
    log_lines = logs_resp[0]["result"]["lines"]
    assert log_lines and any("运行结束" in entry["line"] for entry in log_lines)

    code, items_resp, _ = rpc({"id": 4, "method": "store.items", "params": {"db": str(db)}})
    items = items_resp[0]["result"]
    assert items["count"] == 2
    assert {item["title"] for item in items["items"]} == {"协议条目一", "协议条目二"}


def test_run_start_dry_leaves_store_untouched(tmp_path, local_api):
    """dry run:全链执行、completed 0/success,但 store 零持久化副作用。"""
    yaml_path = write_yaml(tmp_path, VALID_YAML.replace("{port}", str(local_api)), "dry.yaml")
    db = tmp_path / "dry.db"
    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "run.start",
                                    "params": {"yaml": yaml_path, "dry": True, "db": str(db)}}) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    run_id = json.loads(out.getvalue().splitlines()[0])["result"]["run_id"]
    completed = wait_completed(out, run_id)
    assert completed["exit_code"] == 0 and completed["status"] == "success"
    assert completed["dry"] is True
    assert completed["record"] is None  # dry 走内存存储,无 runs 记录
    code, items_resp, _ = rpc({"id": 2, "method": "store.items", "params": {"db": str(db)}})
    assert items_resp[0]["result"]["count"] == 0


def test_run_start_config_error_exit_code_one(tmp_path):
    """坏 YAML:completed 透传 CLI 退出码 1(config_error),无 store 记录。"""
    yaml_path = write_yaml(tmp_path, BAD_CRON_YAML, "bad.yaml")
    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "run.start",
                                    "params": {"yaml": yaml_path, "db": str(tmp_path / "x.db")}}) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    run_id = json.loads(out.getvalue().splitlines()[0])["result"]["run_id"]
    completed = wait_completed(out, run_id)
    assert completed["exit_code"] == 1
    assert completed["status"] == "config_error"
    assert completed["record"] is None


def test_run_busy_single_flight():
    """已有 run 在执行:第二个 run.start 结构化拒绝(run_busy)。"""
    entry._RUNS[99] = {"run_id": 99, "state": "running"}
    entry._ACTIVE_RUN_ID = 99
    try:
        code, responses, _ = rpc({"id": 1, "method": "run.start",
                                  "params": {"yaml": "whatever.yaml"}})
        error = responses[0]["error"]
        assert error["code"] == "run_busy"
        assert error["data"]["active_run_id"] == 99
    finally:
        entry._ACTIVE_RUN_ID = None


def test_run_status_unknown_id():
    """未知 run_id:结构化 404(run_not_found)。"""
    code, responses, _ = rpc({"id": 5, "method": "run.status", "params": {"run_id": 424242}})
    assert responses[0]["error"]["code"] == "run_not_found"
    assert responses[0]["error"]["path"] == "params.run_id"


# ---------------------------------------------------------------------------
# store.items / secret.set / secret.list
# ---------------------------------------------------------------------------


def test_store_items_seeded_db_with_filters(tmp_path):
    """store.items:直读 SQLiteStore,支持 category 过滤与 limit(新→旧)。"""
    db = tmp_path / "seed.db"
    store = SQLiteStore(str(db))
    from datetime import datetime, timezone

    from myia.store.models import ItemRecord
    for index in range(3):
        store.save_item(ItemRecord(
            url=f"https://example.com/{index}", dedup_key=f"k{index}", title=f"条目{index}",
            category="proto-demo" if index < 2 else "other",
            first_seen=datetime(2026, 10, 1, tzinfo=timezone.utc),
        ))
    store.close()
    code, responses, _ = rpc({"id": 1, "method": "store.items", "params": {"db": str(db)}})
    result = responses[0]["result"]
    assert result["count"] == 3 and result["items"][0]["title"] == "条目2"  # 新→旧
    code, responses, _ = rpc({"id": 2, "method": "store.items",
                              "params": {"db": str(db), "category": "proto-demo", "limit": 1}})
    result = responses[0]["result"]
    assert result["count"] == 1 and result["items"][0]["category"] == "proto-demo"


def test_store_items_corrupt_db_structured_error(tmp_path):
    """库损坏:结构化透传 store_corrupt(码/路径/消息)。"""
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not sqlite" * 10)
    code, responses, _ = rpc({"id": 1, "method": "store.items", "params": {"db": str(db)}})
    error = responses[0]["error"]
    assert error["code"] == "store_corrupt"
    assert error["path"] == "params.db"


def test_secret_set_roundtrip_value_never_echoed():
    """secret.set:写入走 myia.secrets(钥匙链),应答零回显值。"""
    fake = entry.set_secret
    code, responses, events = rpc({"id": 9, "method": "secret.set",
                                   "params": {"name": "myia/proto/token", "value": "super-secret-value"}})
    assert code == 0
    assert responses == [{"id": 9, "result": {"name": "myia/proto/token", "stored": True}}]
    assert fake.capture.calls == [("myia/proto/token", "super-secret-value")]
    assert "super-secret-value" not in json.dumps(responses)  # 值零回显
    code, responses, _ = rpc({"id": 10, "method": "secret.list"})
    assert responses[0]["result"]["names"] == ["myia/proto/token"]  # 只有名字


def test_secret_set_missing_params():
    """secret.set 缺参:invalid_params 带字段路径。"""
    code, responses, _ = rpc({"id": 1, "method": "secret.set", "params": {"name": "myia/a/b"}})
    assert responses[0]["error"]["code"] == "invalid_params"
    assert responses[0]["error"]["path"] == "params.value"


# ---------------------------------------------------------------------------
# 协议层:错误结构化 / 通知 / EOF 退出码 / 直通模式
# ---------------------------------------------------------------------------


def test_protocol_errors_are_structured():
    """parse_error(id=null)/ invalid_request / invalid_params / method_not_found。"""
    code, responses, _ = rpc(raw_lines=["{not json"])
    assert responses[0] == {"id": None, "error": {"code": "parse_error", "path": "$",
                                                  "message": responses[0]["error"]["message"]}}
    code, responses, _ = rpc({"id": 1, "params": {}})  # 缺 method
    assert responses[0]["error"]["code"] == "invalid_request"
    code, responses, _ = rpc({"id": 2, "method": "version", "params": [1]})
    assert responses[0]["error"]["code"] == "invalid_params"
    assert responses[0]["error"]["path"] == "params"
    code, responses, _ = rpc({"id": 3, "method": "does.not.exist"})
    assert responses[0]["error"]["code"] == "method_not_found"
    assert "version" in responses[0]["error"]["data"]["allowed"]


def test_notification_yields_no_response():
    """无 id = 通知:执行但不应答(零输出行)。"""
    code, responses, events = rpc({"method": "version"})
    assert code == 0
    assert responses == [] and events == []


def test_serve_exit_code_zero_on_eof():
    """stdin EOF = 干净退出 0(壳关闭管道即正常关停)。"""
    code, _, _ = rpc()
    assert code == 0


def test_oneshot_passthrough_preserves_cli_contract(tmp_path):
    """直通模式:退出码契约原样(--version=0;run 坏 YAML=1)。"""
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "src"), "HOME": str(tmp_path)}
    version = subprocess.run(
        [sys.executable, str(ENTRY_PATH), "--version"], capture_output=True, text=True, env=env,
        check=False,
    )
    assert version.returncode == 0
    assert version.stdout.startswith("myia ")
    bad = write_yaml(tmp_path, BAD_CRON_YAML, "passthrough-bad.yaml")
    run = subprocess.run(
        [sys.executable, str(ENTRY_PATH), "run", bad, "--db", str(tmp_path / "p.db")],
        capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 1  # 配置错误 = 1(spec python/error-handling)


# ---------------------------------------------------------------------------
# sources.write:源启停写回(往返一致 = load_category_file 同门复核)
# ---------------------------------------------------------------------------

SOURCES_WRITE_YAML = """
id: sources-demo
name: 源启停夹具
schedule: "0 9 * * *"
sources:
  - name: keep-me
    engine: static_html
    url: "http://127.0.0.1:9/pages"
    rate_limit:
      respect_robots: false
    extract:
      type: list
      item: "li"
      fields:
        title: "h3"
        url: "a@href"
  - name: drop-me
    engine: static_html
    url: "http://127.0.0.1:9/other"
    rate_limit:
      respect_robots: false
    extract:
      type: list
      item: "li"
      fields:
        title: "h3"
        url: "a@href"
classify:
  builtin: false
dedup:
  key: "{url}"
push:
  - channel: stdout
plugin:
  id: myia-demo
  modes:
    local:
      compose: docker-compose.yml
"""


def test_sources_write_disable_enable_roundtrip(tmp_path):
    """disable 摘出(enable 移回):myia 装载器同门复核 + sidecar 节保留 +
    暂存文件 lossless 往返 —— PRD「写回品类 YAML 并被 myia run 识别」。"""
    from myia.schema import load_category_file

    yaml_path = Path(write_yaml(tmp_path, SOURCES_WRITE_YAML, "sources-demo.yaml"))

    code, responses, _ = rpc(
        {"id": 1, "method": "sources.write",
         "params": {"file": str(yaml_path), "disable": ["drop-me"]}},
    )
    assert code == 0
    assert responses[0]["result"] == {
        "file": str(yaml_path),
        "written": True,
        "enabled": ["keep-me"],
        "disabled": ["drop-me"],
    }
    # myia run 同门:写回文件可装载,名单只剩 keep-me;plugin: 节原样保留
    config = load_category_file(yaml_path)
    assert [source.name for source in config.sources] == ["keep-me"]
    assert config.plugin is not None and config.plugin.id == "myia-demo"
    raw_text = yaml_path.read_text(encoding="utf-8")
    assert "plugin:" in raw_text and "drop-me" not in raw_text
    # lossless 暂存:被摘出的源完整落在 <yaml>.disabled.json
    stash = json.loads((tmp_path / "sources-demo.yaml.disabled.json").read_text("utf-8"))
    assert [item["name"] for item in stash] == ["drop-me"]
    assert stash[0]["url"] == "http://127.0.0.1:9/other"

    # enable 移回:名单复原,暂存文件清空删除
    code, responses, _ = rpc(
        {"id": 2, "method": "sources.write",
         "params": {"file": str(yaml_path), "enable": ["drop-me"]}},
    )
    assert code == 0
    assert responses[0]["result"]["enabled"] == ["keep-me", "drop-me"]
    assert responses[0]["result"]["disabled"] == []
    config = load_category_file(yaml_path)
    assert [source.name for source in config.sources] == ["keep-me", "drop-me"]
    assert not (tmp_path / "sources-demo.yaml.disabled.json").exists()


def test_sources_write_structured_refusals(tmp_path):
    """结构化拒绝:未知源名 / 停用最后一个启用源,失败零写入。"""
    yaml_path = Path(write_yaml(tmp_path, SOURCES_WRITE_YAML, "refusal.yaml"))
    before = yaml_path.read_text(encoding="utf-8")

    code, responses, _ = rpc(
        {"id": 1, "method": "sources.write",
         "params": {"file": str(yaml_path), "disable": ["nope"]}},
    )
    assert code == 0  # 协议流不因业务错误中断;错误在应答对象里
    error = responses[0]["error"]
    assert error["code"] == "source_unknown"
    assert error["data"]["enabled"] == ["keep-me", "drop-me"]

    code, responses, _ = rpc(
        {"id": 2, "method": "sources.write",
         "params": {"file": str(yaml_path).replace("refusal.yaml", "missing.yaml"),
                    "disable": ["keep-me"]}},
    )
    error = responses[0]["error"]
    assert error["code"] == "source_file_unreadable"
    assert error["path"] == "params.file"

    # 逐个停到只剩一个,再停即拒(schema sources min_length=1 的可装载底线)
    code, responses, _ = rpc(
        {"id": 3, "method": "sources.write",
         "params": {"file": str(yaml_path), "disable": ["drop-me"]}},
    )
    assert responses[0]["result"]["written"] is True
    code, responses, _ = rpc(
        {"id": 4, "method": "sources.write",
         "params": {"file": str(yaml_path), "disable": ["keep-me"]}},
    )
    assert responses[0]["error"]["code"] == "last_source"
    # 拒绝 = 零写入(文件仍是「只剩 keep-me」的成功态,不是半态)
    from myia.schema import load_category_file

    assert [source.name for source in load_category_file(yaml_path).sources] == ["keep-me"]
    assert yaml_path.read_text(encoding="utf-8") != before


# ---------------------------------------------------------------------------
# v1.1.1 应用数据根:上下文解析优先级 / .app bundle 探测 / 首跑种子 / first_run
# (task 10-03-v111-desktop-paths,design.md D1-D3/D8)
# ---------------------------------------------------------------------------

OFFICIAL_TEMPLATE = """
id: {pid}
name: 官方夹具 {pid}
schedule: "0 9 * * *"
sources:
  - name: local-api
    engine: direct_api
    url: "http://127.0.0.1:9/x"
    rate_limit:
      qps: 1000.0
      respect_robots: false
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.a"
        url: "$.b"
classify:
  builtin: false
"""


def _fake_bundle(tmp_path: Path, *plugin_ids: str) -> Path:
    bundle = tmp_path / "bundle" / "plugins"
    bundle.mkdir(parents=True, exist_ok=True)
    for pid in plugin_ids:
        (bundle / f"{pid}.yaml").write_text(OFFICIAL_TEMPLATE.format(pid=pid), encoding="utf-8")
    return bundle


def test_serve_context_myia_home_env(tmp_path, monkeypatch):
    """MYIA_HOME env → home 模式:db/plugins 默认全落数据根(并即时建目录)。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MYIA_HOME", str(home))
    ctx = entry._serve_context()
    assert ctx.home == home
    assert ctx.db == str(home / "myia.db")
    assert ctx.plugins_dir == str(home / "plugins")
    assert ctx.install_root == str(home / "plugins")
    assert home.is_dir()


def test_serve_context_plugin_dir_env_respected(tmp_path, monkeypatch):
    """既有 MYIA_PLUGIN_DIR 约定不被夺权:安装根显式 env 优先于 <home>/plugins。"""
    market = tmp_path / "market"
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MYIA_PLUGIN_DIR", str(market))
    assert entry._serve_context().install_root == str(market)


def test_serve_context_dev_fallback_unchanged(monkeypatch):
    """dev 回退:三项默认与 v1.1 CLI 常量逐字节一致(仓库内行为不回退)。"""
    from myia.cli import DEFAULT_DB_PATH, DEFAULT_PLUGINS_DIR
    from myia.plugins.installed import default_install_root

    monkeypatch.delattr(sys, "frozen", raising=False)
    ctx = entry._serve_context()
    assert ctx.home is None
    assert ctx.db == DEFAULT_DB_PATH
    assert ctx.plugins_dir == DEFAULT_PLUGINS_DIR
    assert ctx.install_root == str(default_install_root())


def test_bundle_detection_dot_app(tmp_path, monkeypatch):
    """冻结 exe 位于 .app 内 → 平台数据根(bundle 探测,Rust 注入丢失时的兜底)。"""
    exe = tmp_path / "MYIA.app" / "Contents" / "MacOS" / "myia"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    platform_root = tmp_path / "platform-root"
    monkeypatch.setattr(entry, "myia_home", lambda: platform_root)
    assert entry._inside_app_bundle() is True
    assert entry._serve_context().home == platform_root


def test_bundle_detection_requires_dot_app(tmp_path, monkeypatch):
    """冻结但不在 .app 内(裸 CLI 分发形态)→ 仍 dev 回退,不偷偷进家目录。"""
    exe = tmp_path / "bin" / "myia"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert entry._inside_app_bundle() is False
    assert entry._serve_context().home is None


def test_seed_copies_official_plugins_and_marks(tmp_path, monkeypatch):
    """首跑种子:空 plugins → 拷官方四件套 + 写 .seeded;标志抑制复种。"""
    home = tmp_path / "home"
    bundle = _fake_bundle(tmp_path, "ai-news", "wool", "stocks", "gpu-prices")
    monkeypatch.setattr(entry, "_bundle_plugins_dir", lambda: bundle)
    ctx = entry.ServeContext(
        home=home, db=str(home / "myia.db"),
        plugins_dir=str(home / "plugins"), install_root=str(home / "plugins"),
    )
    assert entry._seed_first_run(ctx) is True
    seeded = sorted(path.name for path in (home / "plugins").glob("*.yaml"))
    assert seeded == ["ai-news.yaml", "gpu-prices.yaml", "stocks.yaml", "wool.yaml"]
    assert (home / entry.SEED_MARKER).exists()

    # 幂等:用户删光一个插件后重启,标志在 → 不复种(尊重用户删除)
    (home / "plugins" / "wool.yaml").unlink()
    assert entry._seed_first_run(ctx) is False
    assert not (home / "plugins" / "wool.yaml").exists()


def test_seed_skips_when_user_has_plugins(tmp_path, monkeypatch):
    """升级安装/手动放置过插件(plugins 非空)→ 零打扰:不种、不写标志。"""
    home = tmp_path / "home"
    bundle = _fake_bundle(tmp_path, "ai-news")
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "mine.yaml").write_text(OFFICIAL_TEMPLATE.format(pid="mine"), encoding="utf-8")
    monkeypatch.setattr(entry, "_bundle_plugins_dir", lambda: bundle)
    ctx = entry.ServeContext(
        home=home, db=str(home / "myia.db"),
        plugins_dir=str(plugins), install_root=str(plugins),
    )
    assert entry._seed_first_run(ctx) is False
    assert not (home / entry.SEED_MARKER).exists()
    assert sorted(path.name for path in plugins.glob("*.yaml")) == ["mine.yaml"]


def test_serve_startup_seeds_in_home_mode(tmp_path, monkeypatch):
    """serve 启动即种子(home 模式);dev 模式连 bundle 探测都不碰。"""
    home = tmp_path / "home"
    bundle = _fake_bundle(tmp_path, "ai-news")
    monkeypatch.setenv("MYIA_HOME", str(home))
    monkeypatch.setattr(entry, "_bundle_plugins_dir", lambda: bundle)
    code, responses, _ = rpc({"id": 1, "method": "version", "params": {}})
    assert code == 0 and responses[0]["result"]["version"]
    assert (home / "plugins" / "ai-news.yaml").exists()

    monkeypatch.delenv("MYIA_HOME")
    monkeypatch.setattr(
        entry, "_bundle_plugins_dir",
        lambda: (_ for _ in ()).throw(AssertionError("dev 模式不得触发 bundle 探测")),
    )
    code, responses, _ = rpc({"id": 2, "method": "version", "params": {}})
    assert code == 0 and responses[0]["result"]["version"]


def test_health_first_run_flag_and_home_defaults(tmp_path, monkeypatch):
    """health:home 模式 db/plugins 落数据根;零 yaml → first_run=true,种上即 false。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MYIA_HOME", str(home))
    code, responses, _ = rpc({"id": 1, "method": "health", "params": {}})
    result = responses[0]["result"]
    assert code == 0
    assert result["plugins_dir"] == str(home / "plugins")
    assert result["db"] == str(home / "myia.db")
    assert result["first_run"] is True
    assert result["healthy"] is True  # 空态是合法态,不是错误

    (home / "plugins").mkdir(parents=True, exist_ok=True)
    (home / "plugins" / "ai-news.yaml").write_text(
        OFFICIAL_TEMPLATE.format(pid="ai-news"), encoding="utf-8"
    )
    code, responses, _ = rpc({"id": 2, "method": "health", "params": {}})
    result = responses[0]["result"]
    assert result["first_run"] is False
    assert [plugin["id"] for plugin in result["plugins"]] == ["ai-news"]


def test_health_explicit_params_win_over_env(tmp_path, monkeypatch):
    """优先级之首:显式 params 永远赢过 MYIA_HOME(env 只供缺省)。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    plugins_dir = tmp_path / "elsewhere"
    plugins_dir.mkdir()
    code, responses, _ = rpc(
        {"id": 1, "method": "health", "params": {"plugins_dir": str(plugins_dir)}},
    )
    assert code == 0
    assert responses[0]["result"]["plugins_dir"] == str(plugins_dir)


def test_store_items_and_run_default_db_follow_home(tmp_path, monkeypatch):
    """store.items / run.start 的 db 缺省同收口:一条数据通路一个库。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MYIA_HOME", str(home))
    code, responses, _ = rpc({"id": 1, "method": "store.items", "params": {"limit": 5}})
    assert code == 0
    assert responses[0]["result"]["db"] == str(home / "myia.db")
    assert responses[0]["result"]["items"] == []


def test_myia_home_three_platforms(tmp_path, monkeypatch):
    """myia_home 三平台规则(design.md D1):darwin=~/Library/Application
    Support/MYIA、win32=%APPDATA%\\MYIA、linux=~/.myia(本机外分支注入
    sys.platform 验证;Path.home 按 tarHeel 平台语义 monkeypatch)。"""
    # darwin(HOME 重定向,避免触碰真实用户目录)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert entry.myia_home() == tmp_path / "Library" / "Application Support" / "MYIA"
    # win32(APPDATA 优先,缺失回退 ~/AppData/Roaming)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert entry.myia_home() == tmp_path / "Roaming" / "MYIA"
    monkeypatch.delenv("APPDATA")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # Windows 语义:家目录即 %USERPROFILE%
    assert entry.myia_home() == tmp_path / "AppData" / "Roaming" / "MYIA"
    # linux/其余(POSIX 家目录策略,收编旧 ~/.myia 约定)
    monkeypatch.setattr(sys, "platform", "linux")
    assert entry.myia_home() == tmp_path / ".myia"


def test_run_start_default_db_follows_home(tmp_path, monkeypatch):
    """run.start 的 db 缺省收口到数据根(工作线程 monkeypatch 成 no-op,
    只验参数装配形态,不真跑子进程;参数形态=UI types.ts RunStartParams)。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MYIA_HOME", str(home))
    yaml_path = write_yaml(tmp_path, VALID_YAML.replace("{port}", "9"))
    monkeypatch.setattr(entry, "_run_worker", lambda *args, **kwargs: None)
    code, responses, _ = rpc({"id": 1, "method": "run.start", "params": {"yaml": yaml_path}})
    assert code == 0
    started = responses[0]["result"]
    assert started["db"] == str(home / "myia.db")
    assert started["state"] == "running" and started["dry"] is False
    assert entry._ACTIVE_RUN_ID == 1
    entry._ACTIVE_RUN_ID = None  # no-op worker 不会走 finally 清理,此处手工复位


# ---------------------------------------------------------------------------
# yaml.*:配置编辑器协议(task 10-03-yaml-editor;契约钉死于任务档 design.md §1,
# 坏文件入列可修、围栏四违例、save 零写入守门、乐观锁、启停止血、两写路径互斥)
# ---------------------------------------------------------------------------

EDITOR_YAML = """
# 头部注释:编辑器往返保真夹具(逐字节原样,注释不丢)
id: editor-demo
name: 编辑器夹具
schedule: "0 9 * * *"
sources:
  - name: local-api
    engine: direct_api
    url: "http://127.0.0.1:9/x"
    rate_limit:
      qps: 1000.0
      respect_robots: false
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.a"
        url: "$.b"
classify:
  builtin: false
push:
  - channel: stdout
"""

#: 启停止血/写路径互斥夹具:两源 + 顶部注释(sources.write 的 safe_dump 会抹注释)。
COMMENTED_TOGGLE_YAML = """
# 顶部注释:启停止血夹具(safe_dump 重写会抹掉,.bak 留底可找回)
id: toggle-demo
name: 启停止血
schedule: "0 9 * * *"
sources:
  - name: keep-me
    engine: static_html
    url: "http://127.0.0.1:9/pages"
    rate_limit:
      respect_robots: false
    extract:
      type: list
      item: "li"
      fields:
        title: "h3"
        url: "a@href"
  - name: drop-me
    engine: static_html
    url: "http://127.0.0.1:9/other"
    rate_limit:
      respect_robots: false
    extract:
      type: list
      item: "li"
      fields:
        title: "h3"
        url: "a@href"
"""

#: 凭据分级夹具:env: 引用只验格式(不对照名单),keychain: 未录入出 warning。
SECRET_REF_YAML = """
id: secret-demo
name: 凭据夹具
schedule: "0 9 * * *"
sources:
  - name: api
    engine: direct_api
    url: "http://127.0.0.1:9/x"
    headers:
      Authorization: "env:NOT_SET_ANYWHERE"
    rate_limit:
      qps: 1000.0
      respect_robots: false
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.a"
        url: "$.b"
push:
  - channel: webhook
    target: "keychain:myia/push/demo"
"""


def _editor_plugins(tmp_path: Path, monkeypatch, *files: tuple[str, str]) -> Path:
    """home 模式下的可控 plugins 目录:yaml.list 零参数,目录由 serve 上下文定。"""
    plugins = tmp_path / "home" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    for name, text in files:
        (plugins / name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    return plugins


def test_yaml_fence_four_violations_and_stem(tmp_path, monkeypatch):
    """路径围栏:../ 穿越 / 目录外绝对路径 / 非 yaml 后缀 / 符号链接逃逸 →
    结构化拒绝;新建 stem 违例(大写+空格)在 save 路径同样被拦。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    outside = tmp_path / "outside.yaml"
    outside.write_text(EDITOR_YAML, encoding="utf-8")
    link = plugins / "link.yaml"
    link.symlink_to(outside)
    cases = [
        (str(plugins / ".." / ".." / "evil.yaml"), "path_outside_root"),  # ../ 穿越
        (str(outside), "path_outside_root"),  # 目录外绝对路径
        (str(plugins / "notes.txt"), "not_yaml_suffix"),  # 非 yaml 后缀
        (str(link), "path_outside_root"),  # 符号链接逃逸(resolve 消解后越界)
    ]
    for index, (file, code) in enumerate(cases, start=1):
        _, responses, _ = rpc({"id": index, "method": "yaml.read", "params": {"file": file}})
        assert responses[0]["error"]["code"] == code
    # 新建 stem 违例:围栏第三道门(file 不存在时 stem 过品类 id 同款正则)
    bad_stem = plugins / "My Category.yaml"
    _, responses, _ = rpc({"id": 9, "method": "yaml.save",
                           "params": {"file": str(bad_stem), "content": EDITOR_YAML,
                                      "expected_mtime": None}})
    assert responses[0]["error"]["code"] == "invalid_file_stem"
    assert not bad_stem.exists()  # 拒绝 = 零写入


def test_yaml_list_broken_file_readable_and_fixable(tmp_path, monkeypatch):
    """坏 YAML 也入列(parse_ok=false + error)且可读可修 —— 编辑器核心用例:
    修好 health 加载不了的文件;修好 save 后 list 恢复 parse_ok。"""
    plugins = _editor_plugins(
        tmp_path, monkeypatch,
        ("demo.yaml", EDITOR_YAML),
        ("broken.yaml", EDITOR_YAML.replace("editor-demo", "broken-demo")
                                   .replace('schedule: "0 9 * * *"', 'schedule: "not-a-cron"')),
    )
    code, responses, _ = rpc({"id": 1, "method": "yaml.list"})
    result = responses[0]["result"]
    assert code == 0
    assert result["plugins_dir"] == str(plugins)
    assert [item["name"] for item in result["files"]] == ["broken.yaml", "demo.yaml"]  # 按文件名排序
    good = result["files"][1]
    assert good["parse_ok"] is True and good["category_id"] == "editor-demo"
    assert good["category_name"] == "编辑器夹具" and good["sources"] == 1
    assert good["error"] is None
    bad = result["files"][0]
    assert bad["parse_ok"] is False and bad["category_id"] is None and bad["sources"] is None
    assert bad["error"]["code"] == "invalid_cron" and bad["error"]["path"] == "$.schedule"

    # 坏文件可读:原文字节直读,注释原样
    _, responses, _ = rpc({"id": 2, "method": "yaml.read",
                           "params": {"file": str(plugins / "broken.yaml")}})
    read = responses[0]["result"]
    assert "头部注释" in read["content"]
    assert read["size"] == len(read["content"].encode("utf-8"))
    # 可修:带读回的 mtime 保存修好的内容 → list 恢复 parse_ok
    fixed = read["content"].replace("not-a-cron", "0 9 * * *")
    _, responses, _ = rpc({"id": 3, "method": "yaml.save",
                           "params": {"file": str(plugins / "broken.yaml"),
                                      "content": fixed, "expected_mtime": read["mtime"]}})
    assert responses[0]["result"]["created"] is False
    _, responses, _ = rpc({"id": 4, "method": "yaml.list"})
    entry = [item for item in responses[0]["result"]["files"]
             if item["name"] == "broken.yaml"][0]
    assert entry["parse_ok"] is True and entry["category_id"] == "broken-demo"


def test_yaml_validate_dry_run_zero_write(tmp_path, monkeypatch):
    """validate 干跑:findings 结构化(未知字段 error 级 + 字段路径),正例带
    category 摘要;干跑后文件内容与 mtime 逐项不变(零写入)。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    before = path.read_text(encoding="utf-8")
    mtime_before = path.stat().st_mtime
    _, responses, _ = rpc({"id": 1, "method": "yaml.validate",
                           "params": {"content": EDITOR_YAML + "bogus_field: 1\n",
                                      "file": str(path)}})
    result = responses[0]["result"]
    assert result["valid"] is False and result["category"] is None
    finding = result["findings"][0]
    assert finding == {"path": "$.bogus_field", "code": "unknown_field",
                       "message": finding["message"], "level": "error"}
    assert path.read_text(encoding="utf-8") == before  # 零写入
    assert path.stat().st_mtime == mtime_before  # 连 mtime 都不动
    # 正例:不带 file 也可干跑,category 摘要随行
    _, responses, _ = rpc({"id": 2, "method": "yaml.validate", "params": {"content": EDITOR_YAML}})
    result = responses[0]["result"]
    assert result["valid"] is True and result["findings"] == []
    assert result["category"] == {"id": "editor-demo", "name": "编辑器夹具", "sources": 1}


def test_yaml_save_invalid_zero_write_bak_untouched(tmp_path, monkeypatch):
    """save 校验失败:语法错/停到 0 源(schema 同门)→ category_invalid 结构化
    明细;目标文件零变更,既有 .bak 不动(拒绝路径不碰备份)。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    bak = plugins / "demo.yaml.bak"
    bak.write_text("SENTINEL-BAK", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    _, responses, _ = rpc({"id": 1, "method": "yaml.save",
                           "params": {"file": str(path), "content": "id: broken\nsources: [",
                                      "expected_mtime": path.stat().st_mtime}})
    error = responses[0]["error"]
    assert error["code"] == "category_invalid" and error["path"] == "params.content"
    assert error["data"]["errors"][0]["code"] == "yaml_parse_error"  # 明细同构透传
    # 停到 0 源:schema sources min_length=1 的编辑保存同门生效
    no_sources = """
id: editor-demo
name: 编辑器夹具
schedule: "0 9 * * *"
sources: []
"""
    _, responses, _ = rpc({"id": 2, "method": "yaml.save",
                           "params": {"file": str(path), "content": no_sources,
                                      "expected_mtime": path.stat().st_mtime}})
    error = responses[0]["error"]
    assert error["code"] == "category_invalid"
    assert error["data"]["errors"][0]["code"] == "too_short"
    assert path.read_text(encoding="utf-8") == before  # 零写入
    assert bak.read_text(encoding="utf-8") == "SENTINEL-BAK"  # .bak 不动


def test_yaml_save_new_file_roundtrip_and_not_found_fork(tmp_path, monkeypatch):
    """新建往返:null mtime + 不存在 = 创建(created=true、无 .bak)→ doctor/
    list 识别新品类,既有文件 .bak 不误伤;非 null mtime + 不存在 = file_not_found。"""
    from myia.schema import load_category_file

    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    new_path = plugins / "fresh-pick.yaml"
    content = EDITOR_YAML.replace("editor-demo", "fresh-pick")
    code, responses, _ = rpc({"id": 1, "method": "yaml.save",
                              "params": {"file": str(new_path), "content": content,
                                         "expected_mtime": None}})
    result = responses[0]["result"]
    assert code == 0
    assert result["file"] == str(new_path)
    assert result["written"] is True and result["created"] is True
    assert result["backed_up"] is None and result["warnings"] == []
    assert isinstance(result["mtime"], float) and result["mtime"] > 0
    # myia run 同门:新文件可装载,id 即用户命名
    assert load_category_file(new_path).id == "fresh-pick"
    # doctor 识别(保存闭环的证据面)
    _, responses, _ = rpc({"id": 2, "method": "doctor", "params": {"yamls": [str(new_path)]}})
    assert responses[0]["result"]["plugins"][0]["id"] == "fresh-pick"
    # list 识别;既有 demo.yaml 不被误伤(无 .bak)
    _, responses, _ = rpc({"id": 3, "method": "yaml.list"})
    names = [item["name"] for item in responses[0]["result"]["files"]]
    assert names == ["demo.yaml", "fresh-pick.yaml"]
    assert not (plugins / "demo.yaml.bak").exists()
    # file_not_found 分叉:「想改却不存在」≠「想建」,防路径手误建出影子文件
    ghost = plugins / "ghost.yaml"
    _, responses, _ = rpc({"id": 4, "method": "yaml.save",
                           "params": {"file": str(ghost), "content": content,
                                      "expected_mtime": 1.0}})
    assert responses[0]["error"]["code"] == "file_not_found"
    assert not ghost.exists()


def test_yaml_save_overwrite_backs_up_previous_content(tmp_path, monkeypatch):
    """save 成功覆盖已有文件:.bak = 保存前旧原文(含注释,逐字节),应答
    backed_up 指向它;单份滚动 —— 二次保存后 .bak 前移为上一次内容
    (design §7 测试清单「save 的 .bak 内容 = 旧原文」的锁定项)。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    bak = plugins / "demo.yaml.bak"
    assert not bak.exists()  # 起点:无备份(只有真覆盖才产生)
    edited = EDITOR_YAML.replace('schedule: "0 9 * * *"', 'schedule: "*/15 * * * *"')
    code, responses, _ = rpc({"id": 1, "method": "yaml.save",
                              "params": {"file": str(path), "content": edited,
                                         "expected_mtime": path.stat().st_mtime}})
    result = responses[0]["result"]
    assert code == 0
    assert result["created"] is False
    assert result["backed_up"] == str(bak)
    assert path.read_text(encoding="utf-8") == edited  # 新内容落盘
    assert bak.read_text(encoding="utf-8") == EDITOR_YAML  # .bak = 旧原文(头注释在内)
    # 单份滚动:再保存一次,.bak 换成上一次内容(永远只有最近一份留底)
    twice = edited.replace("name: 编辑器夹具", "name: 编辑器夹具二")
    _, responses, _ = rpc({"id": 2, "method": "yaml.save",
                           "params": {"file": str(path), "content": twice,
                                      "expected_mtime": result["mtime"]}})
    assert responses[0]["result"]["created"] is False
    assert path.read_text(encoding="utf-8") == twice
    assert bak.read_text(encoding="utf-8") == edited  # .bak 前移为上一次内容


def test_yaml_save_duplicate_category_id_rejected(tmp_path, monkeypatch):
    """跨文件品类 id 查重:新建内容撞既有 id → duplicate_category_id(data 带
    冲突文件),零写入(静默混品类地雷在写盘门收口)。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    dup_path = plugins / "shadow.yaml"
    _, responses, _ = rpc({"id": 1, "method": "yaml.save",
                           "params": {"file": str(dup_path), "content": EDITOR_YAML,
                                      "expected_mtime": None}})
    error = responses[0]["error"]
    assert error["code"] == "duplicate_category_id"
    assert [Path(item).name for item in error["data"]["conflicts"]] == ["demo.yaml"]
    assert not dup_path.exists()
    # 改 id 后同一保存即放行(id 空间归位)
    _, responses, _ = rpc({"id": 2, "method": "yaml.save",
                           "params": {"file": str(dup_path),
                                      "content": EDITOR_YAML.replace("editor-demo", "shadow"),
                                      "expected_mtime": None}})
    assert responses[0]["result"]["created"] is True


def test_yaml_delete_roundtrip_cleans_stash(tmp_path, monkeypatch):
    """delete 往返:.bak 留底删除前原文 → 主文件删 → 连带 .disabled.json 清;
    .bak 不入列;再删 = file_not_found。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("doomed.yaml", EDITOR_YAML))
    path = plugins / "doomed.yaml"
    stash = plugins / "doomed.yaml.disabled.json"
    stash.write_text(json.dumps([{"name": "stashed", "url": "http://127.0.0.1:9/y"}],
                                ensure_ascii=False), encoding="utf-8")
    code, responses, _ = rpc({"id": 1, "method": "yaml.delete", "params": {"file": str(path)}})
    result = responses[0]["result"]
    assert code == 0
    assert result == {"file": str(path), "deleted": True,
                      "backed_up": str(plugins / "doomed.yaml.bak")}
    assert Path(result["backed_up"]).read_text(encoding="utf-8") == EDITOR_YAML  # .bak = 删除前原文
    assert not path.exists() and not stash.exists()  # 主文件 + 连带暂存清理
    # 合法空态:.bak 不入列(编辑对象只剩备份痕迹)
    _, responses, _ = rpc({"id": 2, "method": "yaml.list"})
    assert responses[0]["result"]["files"] == []
    _, responses, _ = rpc({"id": 3, "method": "yaml.delete", "params": {"file": str(path)}})
    assert responses[0]["error"]["code"] == "file_not_found"


def test_yaml_template_passes_load_category():
    """模板必过 load_category(schema 演进防腐锁);头注释指向 stocks.yaml。"""
    import yaml as yaml_module

    from myia.schema import load_category

    code, responses, _ = rpc({"id": 1, "method": "yaml.template"})
    content = responses[0]["result"]["content"]
    assert code == 0 and "stocks.yaml" in content
    config = load_category(yaml_module.safe_load(content))
    assert config.id == "my-category" and config.name == "我的品类"
    assert len(config.sources) == 1 and config.sources[0].name == "example"


def test_yaml_secret_warning_level_and_save_not_blocked(tmp_path, monkeypatch):
    """findings 分级:secret_unknown 是 warning(valid 不翻假、保存放行且应答
    带回);env: 引用只验格式不做存在性对照;补录凭据后 warning 消失。"""
    plugins = _editor_plugins(tmp_path, monkeypatch)
    _, responses, _ = rpc({"id": 1, "method": "yaml.validate", "params": {"content": SECRET_REF_YAML}})
    result = responses[0]["result"]
    assert result["valid"] is True  # warning 不翻假
    assert result["category"] == {"id": "secret-demo", "name": "凭据夹具", "sources": 1}
    assert result["findings"] == [{
        "path": "$.push[0].target", "code": "secret_unknown", "level": "warning",
        "message": result["findings"][0]["message"],
    }]
    assert "myia/push/demo" in result["findings"][0]["message"]  # message 含凭据名
    assert "myia secret set" in result["findings"][0]["message"]  # 与录入命令
    assert "NOT_SET_ANYWHERE" not in json.dumps(result)  # env: 不对照名单(决议 8)
    # warning 不拦保存,且应答原样带回(UI 展示)
    _, responses, _ = rpc({"id": 2, "method": "yaml.save",
                           "params": {"file": str(plugins / "secret-demo.yaml"),
                                      "content": SECRET_REF_YAML, "expected_mtime": None}})
    saved = responses[0]["result"]
    assert saved["written"] is True
    assert [finding["code"] for finding in saved["warnings"]] == ["secret_unknown"]
    # 补录凭据(先写 YAML 后补凭据是合法流)→ validate 干净
    rpc({"id": 3, "method": "secret.set",
         "params": {"name": "myia/push/demo", "value": "hook-token"}})
    _, responses, _ = rpc({"id": 4, "method": "yaml.validate", "params": {"content": SECRET_REF_YAML}})
    assert responses[0]["result"]["findings"] == []


def test_yaml_save_mtime_conflict(tmp_path, monkeypatch):
    """mtime 乐观锁:读后文件被外部(CLI/别的窗口)改过 → mtime_conflict 且
    不覆盖外部改动;重读携带新 mtime 后保存成功(UI 恢复路径)。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    _, responses, _ = rpc({"id": 1, "method": "yaml.read", "params": {"file": str(path)}})
    stale = responses[0]["result"]["mtime"]
    external = EDITOR_YAML.replace("0 9 * * *", "0 10 * * *")
    path.write_text(external, encoding="utf-8")  # 读后外部改动
    _, responses, _ = rpc({"id": 2, "method": "yaml.save",
                           "params": {"file": str(path), "content": EDITOR_YAML,
                                      "expected_mtime": stale}})
    error = responses[0]["error"]
    assert error["code"] == "mtime_conflict" and error["path"] == "params.expected_mtime"
    assert error["data"]["expected_mtime"] == stale
    assert error["data"]["current_mtime"] != stale
    assert path.read_text(encoding="utf-8") == external  # 不覆盖外部改动
    _, responses, _ = rpc({"id": 3, "method": "yaml.read", "params": {"file": str(path)}})
    _, responses, _ = rpc({"id": 4, "method": "yaml.save",
                           "params": {"file": str(path), "content": EDITOR_YAML,
                                      "expected_mtime": responses[0]["result"]["mtime"]}})
    assert responses[0]["result"]["written"] is True


def test_yaml_read_limits_and_encoding(tmp_path, monkeypatch):
    """read 错误面补全:>1 MiB → file_too_large;非 UTF-8 → invalid_encoding;
    不存在 → file_not_found。"""
    plugins = _editor_plugins(tmp_path, monkeypatch)
    big = plugins / "big.yaml"
    big.write_text("x" * (entry.YAML_MAX_BYTES + 1), encoding="utf-8")
    _, responses, _ = rpc({"id": 1, "method": "yaml.read", "params": {"file": str(big)}})
    error = responses[0]["error"]
    assert error["code"] == "file_too_large"
    assert error["data"] == {"size": entry.YAML_MAX_BYTES + 1, "limit": entry.YAML_MAX_BYTES}
    garbled = plugins / "gbk.yaml"
    garbled.write_bytes(b"\xd6\xd0\xce\xc4 not-utf8")  # GBK 字节,非 UTF-8
    _, responses, _ = rpc({"id": 2, "method": "yaml.read", "params": {"file": str(garbled)}})
    assert responses[0]["error"]["code"] == "invalid_encoding"
    _, responses, _ = rpc({"id": 3, "method": "yaml.read",
                           "params": {"file": str(plugins / "ghost.yaml")}})
    assert responses[0]["error"]["code"] == "file_not_found"


def test_sources_write_backup_stops_comment_loss(tmp_path):
    """启停止血(决议 2):点一次启停,.bak 保有操作前的带注释原文(主文件仍
    被 safe_dump 重写抹注释 —— 已知缺陷,根治在 10-03-yaml-toggle-comments)。"""
    path = Path(write_yaml(tmp_path, COMMENTED_TOGGLE_YAML, "toggle.yaml"))
    original = path.read_text(encoding="utf-8")
    code, responses, _ = rpc({"id": 1, "method": "sources.write",
                              "params": {"file": str(path), "disable": ["drop-me"]}})
    assert code == 0 and responses[0]["result"]["written"] is True
    bak = tmp_path / "toggle.yaml.bak"
    assert bak.read_text(encoding="utf-8") == original  # .bak = 带注释原文
    assert "顶部注释" not in path.read_text(encoding="utf-8")  # 主文件被重写(止血不根治)


def test_sources_write_then_yaml_save_mutex_by_mtime(tmp_path, monkeypatch):
    """两写路径互斥:编辑器读到的 mtime 被 sources.write 顶掉 → yaml.save 撞
    mtime_conflict(靠乐观锁互斥,不靠运气);重读后保存恢复。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("toggle.yaml", COMMENTED_TOGGLE_YAML))
    path = plugins / "toggle.yaml"
    _, responses, _ = rpc({"id": 1, "method": "yaml.read", "params": {"file": str(path)}})
    stale = responses[0]["result"]["mtime"]
    _, responses, _ = rpc({"id": 2, "method": "sources.write",
                           "params": {"file": str(path), "disable": ["drop-me"]}})
    assert responses[0]["result"]["written"] is True
    _, responses, _ = rpc({"id": 3, "method": "yaml.save",
                           "params": {"file": str(path), "content": COMMENTED_TOGGLE_YAML,
                                      "expected_mtime": stale}})
    assert responses[0]["error"]["code"] == "mtime_conflict"
    # 重读 → 携带启停后的新 mtime → 编辑保存放行(注释随原文写回)
    _, responses, _ = rpc({"id": 4, "method": "yaml.read", "params": {"file": str(path)}})
    fresh = responses[0]["result"]["mtime"]
    _, responses, _ = rpc({"id": 5, "method": "yaml.save",
                           "params": {"file": str(path), "content": COMMENTED_TOGGLE_YAML,
                                      "expected_mtime": fresh}})
    assert responses[0]["result"]["written"] is True
    assert "顶部注释" in path.read_text(encoding="utf-8")  # 编辑链路注释保真


# ---------------------------------------------------------------------------
# image.*:看图六方法往返 + 错误矩阵(10-03-image-input;引擎/VL/探活/sips
# 全 monkeypatch,零外网零真实钥匙链;MYIA_HOME 指向 tmp 数据根)
# ---------------------------------------------------------------------------

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"protocol-image-fixture"
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"heic-body"
GIF_BYTES = b"GIF89a" + b"not-supported"

#: 完整合法看图配置(local_model 已填 = 本地通道可提交;云端 key 引用在用例里按需注入)。
VISION_CONFIG_OK = {
    "channel_default": "local",
    "local": {"base_url": "http://127.0.0.1:8080/v1", "model": "/models/qwen3-vl-8b-mlx"},
    "cloud": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6v",
              "api_key": None},
    "ocr": {"enabled": True, "engine_default": "vision"},
}

from typing import Any  # noqa: E402

from myia.vision import OCRError, OcrLine, VisionResult  # noqa: E402


def wait_image_completed(out: io.StringIO, job_id: int, timeout: float = 30.0) -> dict:
    """等 image.completed 事件(后台线程异步写;EOF 后仍在写)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for obj in split_stream(out)[1]:
            if obj.get("type") == "image.completed" and obj.get("job_id") == job_id:
                return obj
        time.sleep(0.05)
    raise AssertionError(f"看图任务 {job_id} 未在 {timeout}s 内完成")


def _import_image(monkeypatch, tmp_path: Path, data: bytes = PNG_BYTES, name: str = "shot.png") -> dict:
    """入库一张图(MYIA_HOME=tmp/home),返回 image.import 应答 result。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    source = tmp_path / name
    source.write_bytes(data)
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "path", "value": str(source)}})
    assert code == 0
    assert "error" not in responses[0], responses[0]
    return responses[0]["result"]


def _save_vision_config(monkeypatch, config: dict) -> dict:
    code, responses, _ = rpc({"id": 90, "method": "image.config.save",
                              "params": {"config": config}})
    assert "error" not in responses[0], responses[0]
    return responses[0]["result"]


def _fake_run_ocr(monkeypatch, lines=None, raises=None):
    """替换 entry.run_ocr:记录调用;可脚本化返回行或抛结构化 OCRError。"""
    calls: list[dict] = []

    def fake_run_ocr(image_path, engine):
        calls.append({"image_path": str(image_path), "engine": engine})
        if raises is not None:
            raise raises
        return [OcrLine(text=text, conf=conf) for text, conf in (lines or [("OCR行一", 0.98)])]

    monkeypatch.setattr(entry, "run_ocr", fake_run_ocr)
    return calls


def _fake_vision_client(monkeypatch, reply="模型解读结果", error=None):
    """替换 entry.VisionClient:记录构造参数与 prompt;可脚本化 analyze 抛错。"""
    created: list[Any] = []

    class FakeVisionClient:
        def __init__(self, base_url: str, model: str, *, api_key=None, timeout_seconds=60.0,
                     max_output_tokens=2048) -> None:
            self.kwargs = {"base_url": base_url, "model": model, "api_key": api_key}
            self.calls: list[dict] = []
            created.append(self)

        async def analyze(self, *, image_path, prompt):
            self.calls.append({"image_path": str(image_path), "prompt": prompt})
            if error is not None:
                raise error
            return VisionResult(text=reply, total_tokens=17)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(entry, "VisionClient", FakeVisionClient)
    return created


def _fake_probe(monkeypatch, fail=None):
    """替换 entry._vision_probe:记录探活目标;可脚本化抛 OSError/鉴权错。"""
    calls: list[dict] = []

    def fake_probe(base_url, *, api_key=None):
        calls.append({"base_url": base_url, "api_key": api_key})
        if fail is not None:
            raise fail

    monkeypatch.setattr(entry, "_vision_probe", fake_probe)
    return calls


def test_image_import_path_roundtrip_and_dedupe(tmp_path, monkeypatch):
    """import(path):落 <home>/images/<sha256前16>.png;同字节流重复入库幂等。"""
    imported = _import_image(monkeypatch, tmp_path)
    import hashlib

    digest = hashlib.sha256(PNG_BYTES).hexdigest()[:16]
    assert imported == {"id": digest, "path": str(tmp_path / "home" / "images" / f"{digest}.png"),
                        "bytes": len(PNG_BYTES), "ext": "png"}
    assert (tmp_path / "home" / "images" / f"{digest}.png").read_bytes() == PNG_BYTES
    again = _import_image(monkeypatch, tmp_path)  # 去重:同 id 同路径
    assert again["id"] == digest


def test_image_import_base64_with_data_url_prefix(tmp_path, monkeypatch):
    """import(base64):data URL 前缀剥离、魔数定格式(webp 直收)。"""
    webp = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"body"
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    encoded = "data:image/webp;base64," + base64.b64encode(webp).decode("ascii")
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "base64", "value": encoded}})
    result = responses[0]["result"]
    assert result["ext"] == "webp" and result["bytes"] == len(webp)


def test_image_import_too_large_structured(tmp_path, monkeypatch):
    """超 10MB:结构化 image_too_large(带 size/limit),零落盘。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    big = tmp_path / "big.png"
    big.write_bytes(PNG_BYTES + b"\x00" * (10 * 1024 * 1024 + 1))
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "path", "value": str(big)}})
    error = responses[0]["error"]
    assert error["code"] == "image_too_large" and error["path"] == "params.value"
    assert error["data"]["limit"] == 10 * 1024 * 1024
    assert not (tmp_path / "home" / "images").exists() or not any(
        (tmp_path / "home" / "images").iterdir()
    )


def test_image_import_unsupported_magic(tmp_path, monkeypatch):
    """魔数不支持(GIF 冒充 .png):image_unsupported;base64 坏内容同码。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    gif = tmp_path / "fake.png"
    gif.write_bytes(GIF_BYTES)
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "path", "value": str(gif)}})
    assert responses[0]["error"]["code"] == "image_unsupported"
    code, responses, _ = rpc({"id": 2, "method": "image.import",
                              "params": {"kind": "base64", "value": "%%%not-base64%%%"}})
    assert responses[0]["error"]["code"] == "image_unsupported"


def test_image_import_heic_converted_to_png(tmp_path, monkeypatch):
    """heic:先经 sips 转 png 再收(monkeypatch 转换函数,CI 无 sips 也能测)。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    source = tmp_path / "photo.heic"
    source.write_bytes(HEIC_BYTES)
    monkeypatch.setattr(entry, "_convert_heic_to_png", lambda data: b"\x89PNG\r\n\x1a\nconverted")
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "path", "value": str(source)}})
    result = responses[0]["result"]
    assert result["ext"] == "png" and result["bytes"] == len(b"\x89PNG\r\n\x1a\nconverted")


def test_image_import_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    code, responses, _ = rpc({"id": 1, "method": "image.import",
                              "params": {"kind": "path", "value": str(tmp_path / "nope.png")}})
    assert responses[0]["error"]["code"] == "image_not_found"


def test_image_ocr_roundtrip_default_engine_from_config(tmp_path, monkeypatch):
    """ocr:engine 缺省取 vision.yaml 的 ocr.engine_default;逐行 {text, conf}+ms。"""
    imported = _import_image(monkeypatch, tmp_path)
    config = dict(VISION_CONFIG_OK)
    config["ocr"] = {"enabled": True, "engine_default": "rapidocr"}
    _save_vision_config(monkeypatch, config)
    calls = _fake_run_ocr(monkeypatch, lines=[("羊毛行", 0.95), ("低置信行", 0.42)])
    code, responses, _ = rpc({"id": 2, "method": "image.ocr", "params": {"id": imported["id"]}})
    result = responses[0]["result"]
    assert result["engine"] == "rapidocr"  # 缺省引擎来自配置而非硬编码
    assert result["lines"] == [{"text": "羊毛行", "conf": 0.95}, {"text": "低置信行", "conf": 0.42}]
    assert isinstance(result["ms"], int) and result["ms"] >= 0
    assert calls and calls[0]["engine"] == "rapidocr"


def test_image_ocr_engine_param_passthrough(tmp_path, monkeypatch):
    """ocr:显式 engine 直传引擎层(vision/rapidocr 同门)。"""
    imported = _import_image(monkeypatch, tmp_path)
    calls = _fake_run_ocr(monkeypatch)
    code, responses, _ = rpc({"id": 2, "method": "image.ocr",
                              "params": {"id": imported["id"], "engine": "rapidocr"}})
    assert responses[0]["result"]["engine"] == "rapidocr"
    assert calls[0]["engine"] == "rapidocr" and calls[0]["image_path"] == imported["path"]


def test_image_ocr_engine_unknown_structured(tmp_path, monkeypatch):
    """错误矩阵:非法 engine → image_engine_unknown(带 allowed 名单)。"""
    imported = _import_image(monkeypatch, tmp_path)
    _fake_run_ocr(monkeypatch)  # 引擎层不被触达
    code, responses, _ = rpc({"id": 2, "method": "image.ocr",
                              "params": {"id": imported["id"], "engine": "tesseract"}})
    error = responses[0]["error"]
    assert error["code"] == "image_engine_unknown" and error["path"] == "params.engine"
    assert error["data"]["allowed"] == ["vision", "rapidocr"]


def test_image_ocr_unknown_id_and_dependency_missing(tmp_path, monkeypatch):
    """错误矩阵:未知 id → image_not_found;引擎依赖缺失 → image_ocr_failed(带安装命令)。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    code, responses, _ = rpc({"id": 1, "method": "image.ocr", "params": {"id": "0123456789abcdef"}})
    assert responses[0]["error"]["code"] == "image_not_found"
    # 路径穿越免疫:id 必须是 16 位十六进制
    code, responses, _ = rpc({"id": 2, "method": "image.ocr", "params": {"id": "../../etc/passwd"}})
    assert responses[0]["error"]["code"] == "image_not_found"

    imported = _import_image(monkeypatch, tmp_path)
    _fake_run_ocr(
        monkeypatch,
        raises=OCRError("dependency_missing", "vision 引擎依赖 ocrmac 未安装:请先执行 pip install 'myia[vision]'",
                        details={"package": "ocrmac"}),
    )
    code, responses, _ = rpc({"id": 3, "method": "image.ocr", "params": {"id": imported["id"]}})
    error = responses[0]["error"]
    assert error["code"] == "image_ocr_failed"
    assert "myia[vision]" in error["message"]


def test_image_ocr_invalid_config_file_structured(tmp_path, monkeypatch):
    """vision.yaml 手改坏(engine_default 越界):image_config_invalid,fail fast。"""
    imported = _import_image(monkeypatch, tmp_path)
    home = tmp_path / "home"
    (home / "vision.yaml").write_text("ocr:\n  engine_default: tesseract\n", encoding="utf-8")
    _fake_run_ocr(monkeypatch)
    code, responses, _ = rpc({"id": 2, "method": "image.ocr", "params": {"id": imported["id"]}})
    assert responses[0]["error"]["code"] == "image_config_invalid"
    assert responses[0]["error"]["path"] == "vision.yaml"


def test_image_analyze_read_mode_full_event_stream(tmp_path, monkeypatch):
    """analyze(read):job_id 即返 → image.progress(ocr→model)→ image.completed;
    OCR 初稿嵌校对 prompt;result 带 text/model/channel/elapsed_ms/ocr_used。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    _fake_run_ocr(monkeypatch, lines=[("初稿行A", 0.98)])
    clients = _fake_vision_client(monkeypatch, reply="校对后的文字")
    _fake_probe(monkeypatch)

    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                    "params": {"id": imported["id"], "mode": "read"}},
                                   ensure_ascii=False) + "\n")
    assert entry.serve(stdin=stdin, stdout=out) == 0
    responses, _ = split_stream(out)
    started = responses[0]["result"]
    job_id = started["job_id"]
    assert started["state"] == "running" and started["channel"] == "local"

    completed = wait_image_completed(out, job_id)
    assert completed["ok"] is True
    result = completed["result"]
    assert result["text"] == "校对后的文字"
    assert result["model"] == "/models/qwen3-vl-8b-mlx"
    assert result["channel"] == "local"
    assert result["ocr_used"] is True and result["ocr_engine"] == "vision"
    assert isinstance(result["elapsed_ms"], int)

    _, events = split_stream(out)
    stages = [e["stage"] for e in events if e["type"] == "image.progress"]
    assert stages == ["ocr", "model"]
    # read 模式:OCR 初稿嵌校对 prompt(local-ocr 实证配方)
    assert "初稿行A" in clients[0].calls[0]["prompt"]
    assert "校对" in clients[0].calls[0]["prompt"]
    # 完成后单飞解锁
    code, status_resp, _ = rpc({"id": 2, "method": "image.status", "params": {}})
    assert status_resp[0]["result"] == {"busy": False}


def test_image_analyze_describe_and_ask_prompts(tmp_path, monkeypatch):
    """describe 固定结构化 prompt 且 ocr_used=False;ask 直传用户问题。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    clients = _fake_vision_client(monkeypatch)
    _fake_probe(monkeypatch)
    _fake_run_ocr(monkeypatch)

    for params, needle, ocr_used in (
        ({"mode": "describe"}, "主体", False),
        ({"mode": "ask", "question": "这串报错码是什么意思?"}, "报错码", False),
    ):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                        "params": {"id": imported["id"], **params}},
                                       ensure_ascii=False) + "\n")
        entry.serve(stdin=stdin, stdout=out)
        job_id = split_stream(out)[0][0]["result"]["job_id"]  # 应答与事件行序不保证,按 id 拆
        completed = wait_image_completed(out, job_id)
        assert completed["ok"] is True
        assert completed["result"]["ocr_used"] is ocr_used
        assert needle in clients[-1].calls[0]["prompt"]


def test_image_analyze_busy_single_flight(tmp_path, monkeypatch):
    """单飞守卫:已有看图任务 → 第二次提交 image_busy(带 active_job_id)。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    entry._IMAGE_ACTIVE_JOB = 7
    try:
        code, responses, _ = rpc({"id": 1, "method": "image.analyze",
                                  "params": {"id": imported["id"], "mode": "describe"}})
        error = responses[0]["error"]
        assert error["code"] == "image_busy"
        assert error["data"]["active_job_id"] == 7
    finally:
        entry._IMAGE_ACTIVE_JOB = None


def test_image_analyze_local_model_missing(tmp_path, monkeypatch):
    """本地通道未配模型路径:image_config_invalid(同步拒绝,零事件)。"""
    imported = _import_image(monkeypatch, tmp_path)
    config = dict(VISION_CONFIG_OK)
    config["local"] = {"base_url": "http://127.0.0.1:8080/v1", "model": ""}
    _save_vision_config(monkeypatch, config)
    code, responses, _ = rpc({"id": 1, "method": "image.analyze",
                              "params": {"id": imported["id"], "mode": "describe"}})
    assert responses[0]["error"]["code"] == "image_config_invalid"
    assert responses[0]["error"]["path"] == "local.model"


def test_image_analyze_cloud_no_credentials(tmp_path, monkeypatch):
    """云端无 key 引用:同步 image_no_credentials,绝不假装成功(AC3)。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    code, responses, _ = rpc({"id": 1, "method": "image.analyze",
                              "params": {"id": imported["id"], "mode": "describe", "channel": "cloud"}})
    error = responses[0]["error"]
    assert error["code"] == "image_no_credentials" and error["path"] == "cloud.api_key"
    assert "myia/image/api_key" in error["message"]


def test_image_analyze_cloud_with_key_uses_bearer(tmp_path, monkeypatch):
    """云端有 key 引用:提交时解析钥匙链引用,VisionClient 拿到已解析 key。"""
    imported = _import_image(monkeypatch, tmp_path)
    config = dict(VISION_CONFIG_OK)
    config["cloud"] = {"base_url": "https://open.bigmodel.cn/api/paas/v4",
                       "model": "glm-4.6v", "api_key": "keychain:myia/image/api_key"}
    _save_vision_config(monkeypatch, config)
    monkeypatch.setattr(entry, "resolve_credential",
                        lambda ref: "test-cloud-key")  # 不触碰真实钥匙链
    clients = _fake_vision_client(monkeypatch)
    probes = _fake_probe(monkeypatch)

    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                    "params": {"id": imported["id"], "mode": "describe",
                                               "channel": "cloud"}}, ensure_ascii=False) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    job_id = split_stream(out)[0][0]["result"]["job_id"]  # 应答与事件行序不保证,按 id 拆
    completed = wait_image_completed(out, job_id)
    assert completed["ok"] is True and completed["result"]["channel"] == "cloud"
    assert completed["result"]["model"] == "glm-4.6v"
    assert clients[0].kwargs["api_key"] == "test-cloud-key"  # 云端带 Bearer
    assert probes[0]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_image_analyze_unreachable_event_with_hint(tmp_path, monkeypatch):
    """探活失败:image.completed(ok=false, image_unreachable),本地场景附启动指引(AC4)。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    _fake_vision_client(monkeypatch)
    _fake_probe(monkeypatch, fail=OSError("ConnectionRefusedError: [Errno 61]"))

    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                    "params": {"id": imported["id"], "mode": "describe"}},
                                   ensure_ascii=False) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    job_id = split_stream(out)[0][0]["result"]["job_id"]  # 应答与事件行序不保证,按 id 拆
    completed = wait_image_completed(out, job_id)
    assert completed["ok"] is False
    error = completed["error"]
    assert error["code"] == "image_unreachable"
    assert "mlx_vlm" in error["message"]  # 启动指引文案
    assert error["data"]["base_url"] == "http://127.0.0.1:8080/v1"
    code, status_resp, _ = rpc({"id": 2, "method": "image.status", "params": {}})
    assert status_resp[0]["result"]["busy"] is False  # 失败同样解锁


def test_image_analyze_provider_error_event(tmp_path, monkeypatch):
    """VL 调用抛错:image.completed(ok=false, image_provider_error),应用不崩。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    _fake_vision_client(monkeypatch, error=RuntimeError("模拟端点故障"))
    _fake_probe(monkeypatch)

    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                    "params": {"id": imported["id"], "mode": "describe"}},
                                   ensure_ascii=False) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    job_id = split_stream(out)[0][0]["result"]["job_id"]  # 应答与事件行序不保证,按 id 拆
    completed = wait_image_completed(out, job_id)
    assert completed["ok"] is False
    assert completed["error"]["code"] == "image_provider_error"
    assert "模拟端点故障" in completed["error"]["message"]


def test_image_analyze_param_validation(tmp_path, monkeypatch):
    """错误矩阵:未知 id / 非法 mode / ask 缺 question → 结构化拒绝。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    code, responses, _ = rpc({"id": 1, "method": "image.analyze",
                              "params": {"id": "0123456789abcdef", "mode": "describe"}})
    assert responses[0]["error"]["code"] == "image_not_found"

    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    code, responses, _ = rpc({"id": 2, "method": "image.analyze",
                              "params": {"id": imported["id"], "mode": "summarize"}})
    assert responses[0]["error"]["code"] == "invalid_params"
    assert responses[0]["error"]["path"] == "params.mode"
    code, responses, _ = rpc({"id": 3, "method": "image.analyze",
                              "params": {"id": imported["id"], "mode": "ask"}})
    assert responses[0]["error"]["code"] == "invalid_params"
    assert responses[0]["error"]["path"] == "params.question"


def test_image_status_busy_reports_job(tmp_path):
    """status:空闲 {busy: false};执行中附 job_id(serve 退出后注册表仍在)。"""
    code, responses, _ = rpc({"id": 1, "method": "image.status", "params": {}})
    assert responses[0]["result"] == {"busy": False}
    entry._IMAGE_ACTIVE_JOB = 3
    try:
        code, responses, _ = rpc({"id": 2, "method": "image.status", "params": {}})
        assert responses[0]["result"] == {"busy": True, "job_id": 3}
    finally:
        entry._IMAGE_ACTIVE_JOB = None


def test_image_status_last_completed_reconciliation(tmp_path, monkeypatch):
    """status 对账:瞬时失败任务的 completed 可能在 webview 订阅建立前写出而被
    丢 —— 带 job_id 查询附 ``last``(与事件流同一载荷);不带 job_id 保持旧形状。"""
    imported = _import_image(monkeypatch, tmp_path)
    _save_vision_config(monkeypatch, dict(VISION_CONFIG_OK))
    _fake_vision_client(monkeypatch)
    _fake_probe(monkeypatch, fail=OSError("ConnectionRefusedError: [Errno 61]"))

    out = io.StringIO()
    stdin = io.StringIO(json.dumps({"id": 1, "method": "image.analyze",
                                    "params": {"id": imported["id"], "mode": "describe"}},
                                   ensure_ascii=False) + "\n")
    entry.serve(stdin=stdin, stdout=out)
    job_id = split_stream(out)[0][0]["result"]["job_id"]  # 应答与事件行序不保证,按 id 拆
    completed = wait_image_completed(out, job_id)
    assert completed["ok"] is False

    code, responses, _ = rpc({"id": 2, "method": "image.status", "params": {}})
    assert responses[0]["result"] == {"busy": False}  # 旧形状:不带 job_id 不附 last
    code, responses, _ = rpc({"id": 3, "method": "image.status", "params": {"job_id": job_id}})
    result = responses[0]["result"]
    assert result["busy"] is False
    assert result["last"] == completed  # 注册表留存 = 事件流写出的同一份终态载荷
    assert result["last"]["error"]["code"] == "image_unreachable"


def test_image_config_read_defaults_without_file(tmp_path, monkeypatch):
    """config.read:文件不存在 = 全缺省(exists=false,合法未配置态)。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    code, responses, _ = rpc({"id": 1, "method": "image.config.read", "params": {}})
    result = responses[0]["result"]
    assert result["exists"] is False
    assert result["file"] == str(tmp_path / "home" / "vision.yaml")
    assert result["config"]["channel_default"] == "local"
    assert result["config"]["cloud"]["model"] == "glm-4.6v"
    assert result["config"]["cloud"]["api_key"] is None
    assert result["config"]["ocr"]["engine_default"] == "vision"


def test_image_config_save_and_read_roundtrip(tmp_path, monkeypatch):
    """config.save→read 往返:引用原样落盘,keychain 引用不回明文(值无从谈起)。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    config = dict(VISION_CONFIG_OK)
    config["cloud"] = {"base_url": "https://open.bigmodel.cn/api/paas/v4",
                       "model": "glm-4.6v", "api_key": "keychain:myia/image/api_key"}
    config["ocr"] = {"enabled": True, "engine_default": "rapidocr"}
    code, responses, _ = rpc({"id": 1, "method": "image.config.save",
                              "params": {"config": config}})
    assert responses[0]["result"]["ok"] is True
    text = (tmp_path / "home" / "vision.yaml").read_text(encoding="utf-8")
    assert "keychain:myia/image/api_key" in text

    code, responses, _ = rpc({"id": 2, "method": "image.config.read", "params": {}})
    result = responses[0]["result"]
    assert result["exists"] is True
    assert result["config"]["ocr"]["engine_default"] == "rapidocr"
    assert result["config"]["cloud"]["api_key"] == "keychain:myia/image/api_key"


def test_image_config_save_plaintext_rejected_zero_write(tmp_path, monkeypatch):
    """config.save:明文 key / 非法引擎 → image_config_invalid 且零写入。"""
    monkeypatch.setenv("MYIA_HOME", str(tmp_path / "home"))
    bad = dict(VISION_CONFIG_OK)
    bad["cloud"] = {**bad["cloud"], "api_key": "sk-plaintext"}
    code, responses, _ = rpc({"id": 1, "method": "image.config.save",
                              "params": {"config": bad}})
    error = responses[0]["error"]
    assert error["code"] == "image_config_invalid"
    assert error["data"]["error_type"] == "credential_plaintext"
    assert not (tmp_path / "home" / "vision.yaml").exists()  # 拒载 = 零写入

    bad_engine = dict(VISION_CONFIG_OK)
    bad_engine["ocr"] = {"enabled": True, "engine_default": "paddle"}
    code, responses, _ = rpc({"id": 2, "method": "image.config.save",
                              "params": {"config": bad_engine}})
    assert responses[0]["error"]["code"] == "image_config_invalid"
    assert responses[0]["error"]["data"]["error_type"] == "invalid_engine"
