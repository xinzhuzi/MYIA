"""desktop/entry.py sidecar 协议单测(mock stdin/stdout,全方法往返)。

覆盖:10 个方法的请求→应答往返、流式事件(log/progress/completed 以 type
区分)、错误结构化透传(code/path/message)、退出码语义(serve EOF=0;run
子进程 0/1/2 原样透传;直通模式 0/1)。零外网:成功 run 走 127.0.0.1 本地
http.server(安全底线明示例外);凭据方法 monkeypatch,不触碰真实钥匙链。
"""

from __future__ import annotations

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
