# 实证2:小模型真下载全链(10-03-vision-v2 proofs)
# 产品路径:spawn .venv entry.py serve(MYIA_HOME=真实桌面根)→ 行 JSON-RPC
#   image.models.download(repo=mlx-community/Qwen2-VL-2B-Instruct-4bit, name=test-vl-2b)
#   吃 progress/completed 事件 → image.models.list(非 incomplete)→ activate
#   → 停 8080(记录)→ image.server.ensure 起新模型 → 真图 chat/completions 出 caption
#   → 收尾 activate 回 qwen3-vl-8b-mlx → 停 → ensure 恢复 → status/health 确认。
import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path("/Users/zhengbingjin/Project/Github/MYIA")
HOME_ROOT = Path("/Users/zhengbingjin/Library/Application Support/MYIA")
PROOFS = Path(__file__).parent
LOG_PATH = PROOFS / "model-download-chain.log"
REAL_IMG = PROOFS / "real-test.png"
# 首选 Qwen2-VL-2B-Instruct-4bit(1.26GB)实测本机链路全程 ~360KB/s(直连/Clash
# 代理/hf-mirror 三路同帽,并行分片不聚合,见 attempt1/attempt2 日志),60 分钟超
# ask 的 20 分钟帽 → 按 ask「或更小」换 SmolVLM-256M-Instruct-4bit(150.4MB,
# model_type=idefics3,mlx_vlm 0.7.4 内置 idefics3 模块,HF API 实测总量)。
REPO_ID = "mlx-community/SmolVLM-256M-Instruct-4bit"
LOCAL_NAME = "test-vl-256m"
RESTORE_NAME = "qwen3-vl-8b-mlx"
BASE = "http://127.0.0.1:8080"

_lock = threading.Lock()
_events: list[dict] = []


def log(line: str) -> None:
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class Serve:
    def __init__(self) -> None:
        env = dict(os.environ)
        env["MYIA_HOME"] = str(HOME_ROOT)
        self.proc = subprocess.Popen(
            [str(REPO / ".venv/bin/python"), str(REPO / "desktop/entry.py"), "serve"],
            cwd=str(REPO),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        self._next_id = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        progress_count = 0
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log(f"[serve-stdout-nonjson] {line[:200]}")
                continue
            # 事件即达即记:progress 事件(产品 0.5s 节流)首条+每 25 条采样,
            # 其余事件全录 —— ask 要求 progress/completed 事件都吃进证据。
            etype = msg.get("type")
            if isinstance(etype, str) and etype.startswith("image."):
                if etype == "image.models.progress":
                    progress_count += 1
                    if progress_count <= 2 or progress_count % 25 == 0:
                        log(f"[event] {etype} #{progress_count} done={msg.get('done_bytes')} total={msg.get('total_bytes')}")
                else:
                    log(f"[event] {json.dumps(msg, ensure_ascii=False)[:400]}")
            with _lock:
                _events.append(msg)

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            line = raw.rstrip()
            if line:
                log(f"[serve-stderr] {line}")

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            with _lock:
                events = list(_events)
            for msg in events[seen:]:
                seen += 1
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(f"{method} error: {json.dumps(msg['error'], ensure_ascii=False)}")
                    return msg.get("result") or {}
            time.sleep(0.05)
        raise TimeoutError(f"{method} 应答超时 {timeout}s")

    def wait_event(self, want_type: str, job_id: int | None, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        seen = 0
        last_progress_logged = 0.0
        count = 0
        while time.monotonic() < deadline:
            with _lock:
                events = list(_events)
            for msg in events[seen:]:
                seen += 1
                if msg.get("type") != want_type:
                    continue
                if job_id is not None and msg.get("job_id") != job_id:
                    continue
                if want_type.endswith("progress"):
                    count += 1
                    now = time.monotonic()
                    if count == 1 or count % 40 == 0 or now - last_progress_logged > 30:
                        log(f"[event] {want_type} #{count} done={msg.get('done_bytes')} total={msg.get('total_bytes')}")
                        last_progress_logged = now
                    continue
                log(f"[event] {json.dumps(msg, ensure_ascii=False)[:400]}")
                return msg
            time.sleep(0.2)
        raise TimeoutError(f"等 {want_type}(job={job_id})超时 {timeout}s")


def listener_pid() -> int | None:
    out = subprocess.run(
        ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True,
    ).stdout.strip()
    pids = [int(p) for p in out.splitlines() if p.strip().isdigit()]
    return pids[0] if pids else None


def listener_cmd(pid: int) -> str:
    out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return out.strip()


def stop_8080(stage: str) -> None:
    pid = listener_pid()
    if pid is None:
        log(f"[{stage}] 8080 无监听(已停)")
        return
    cmd = listener_cmd(pid)
    log(f"[{stage}] 停 8080: PID={pid} cmd={cmd}")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if listener_pid() is None:
            log(f"[{stage}] 8080 已释放( PID {pid} 退出)")
            return
        time.sleep(0.5)
    log(f"[{stage}] SIGTERM 20s 未退,SIGKILL 兜底 PID={pid}")
    os.kill(pid, signal.SIGKILL)
    time.sleep(2)


def http_json(url: str, payload: dict, timeout: float = 180.0) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def caption(image: Path, prompt: str, model: str) -> str:
    data_url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    status, body = http_json(f"{BASE}/v1/chat/completions", {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 120,
        "temperature": 0.1,
    })
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    log(f"[caption] http={status} model={body.get('model')}")
    log(f"[caption] text={text!r}")
    return text


def main() -> int:
    started = time.monotonic()
    for f in (LOG_PATH,):
        f.unlink(missing_ok=True)
    serve = Serve()
    restored = False
    try:
        # ---- 基线 ----
        v = serve.request("version", timeout=30)
        log(f"[base] version={v.get('version')} protocol={v.get('protocol')}")
        st0 = serve.request("image.server.status", timeout=30)
        log(f"[base] image.server.status={json.dumps(st0, ensure_ascii=False)}")
        assert st0.get("healthy") is True, "基线 8080 不健康,按 ask 前提应健康"
        ml0 = serve.request("image.models.list", timeout=30)
        log(f"[base] models={[ (m['name'], m['active'], m['incomplete']) for m in ml0['models'] ]}")

        # ---- 下载(product 路径,吃事件)----
        t = time.monotonic()
        r = serve.request("image.models.download", {"repo": REPO_ID, "name": LOCAL_NAME}, timeout=30)
        job = r["job_id"]
        log(f"[download] 提交 job_id={job} repo={REPO_ID} name={LOCAL_NAME}")
        done = serve.wait_event("image.models.completed", job, timeout=900)
        log(f"[download] completed ok={done.get('ok')} error={done.get('error')} 耗时={time.monotonic()-t:.1f}s")
        if not done.get("ok"):
            log("RESULT=FAILED(download 未完成,incomplete 目录保留作证据)")
            return 1

        # ---- 清单确认完整 ----
        ml1 = serve.request("image.models.list", timeout=30)
        entry = next((m for m in ml1["models"] if m["name"] == LOCAL_NAME), None)
        log(f"[list] {LOCAL_NAME}={json.dumps(entry, ensure_ascii=False)}")
        assert entry and entry["incomplete"] is False, "下载后仍 incomplete"

        # ---- 激活 → 停旧 → ensure 新 ----
        r = serve.request("image.models.activate", {"name": LOCAL_NAME}, timeout=30)
        log(f"[activate] {LOCAL_NAME} -> {json.dumps(r, ensure_ascii=False)}")
        stop_8080("switch")
        r = serve.request("image.server.ensure", timeout=30)
        log(f"[ensure] 应答={json.dumps(r, ensure_ascii=False)}")
        job = r.get("job_id")
        ev = serve.wait_event("image.server.completed", job, timeout=200)
        log(f"[ensure] completed ok={ev.get('ok')} status={json.dumps(ev.get('status'), ensure_ascii=False)}")
        if not ev.get("ok"):
            log(f"RESULT=FAILED(ensure 失败 error={ev.get('error')})")
            return 1
        st1 = serve.request("image.server.status", timeout=30)
        log(f"[ensure] status={json.dumps(st1, ensure_ascii=False)}")
        assert st1.get("healthy") is True

        # ---- 真图 caption ----
        # mlx_vlm 0.7.4 VLMRequest.model 必填(schemas.py:691)——产品 client 同款
        # 传配置模型路径(src/myia/vision/client.py:144);缺它即 422(attempt3 实证)。
        text = caption(
            REAL_IMG, "Describe this screenshot in one short sentence.",
            model=st1["model"],
        )
        assert isinstance(text, str) and len(text.strip()) > 4, "caption 空或过短"

        log(f"CHAIN_OK 总耗时={time.monotonic()-started:.1f}s")
    except Exception as exc:  # noqa: BLE001 — 全链任何失败都要走恢复收尾
        log(f"[exception] {type(exc).__name__}: {exc}")
        failure = True
    else:
        failure = False
    finally:
        # ---- 收尾:activate 回 8B → 停 → ensure → 确认 ----
        try:
            r = serve.request("image.models.activate", {"name": RESTORE_NAME}, timeout=30)
            log(f"[restore] activate {RESTORE_NAME} -> {json.dumps(r, ensure_ascii=False)}")
            stop_8080("restore")
            r = serve.request("image.server.ensure", timeout=30)
            log(f"[restore] ensure 应答={json.dumps(r, ensure_ascii=False)}")
            job = r.get("job_id")
            if job is not None:
                ev = serve.wait_event("image.server.completed", job, timeout=300)
                log(f"[restore] completed ok={ev.get('ok')} status={json.dumps(ev.get('status'), ensure_ascii=False)}")
            st = serve.request("image.server.status", timeout=30)
            log(f"[restore] status={json.dumps(st, ensure_ascii=False)}")
            with urllib.request.urlopen(f"{BASE}/health", timeout=10) as resp:
                health = json.loads(resp.read())
            log(f"[restore] /health loaded_model={health.get('loaded_model')}")
            restored = (
                st.get("healthy") is True
                and RESTORE_NAME in str(health.get("loaded_model"))
            )
            log(f"[restore] RESTORED={restored}")
            # 现场复原:经产品方法删两个测试模型目录(incomplete 残件 + 完整测试模型),
            # 让真实 models/ 回到基线(仅 qwen3-vl-8b-mlx);证据在日志,不在目录。
            if restored:
                for leftover in (LOCAL_NAME, "test-vl-2b"):
                    try:
                        r = serve.request("image.models.delete", {"name": leftover}, timeout=30)
                        log(f"[cleanup] delete {leftover} -> {json.dumps(r, ensure_ascii=False)}")
                    except Exception as exc:  # noqa: BLE001 — 清理尽力而为
                        log(f"[cleanup] delete {leftover} 失败: {exc}")
                ml_end = serve.request("image.models.list", timeout=30)
                log(f"[cleanup] 末态 models={[ (m['name'], m['active']) for m in ml_end['models'] ]}")
        except Exception as exc:  # noqa: BLE001
            log(f"[restore-exception] {type(exc).__name__}: {exc}")
        finally:
            serve.proc.terminate()
    log(f"DONE failure={failure} restored={restored} 总耗时={time.monotonic()-started:.1f}s")
    return 0 if (not failure and restored) else 1


if __name__ == "__main__":
    sys.exit(main())
