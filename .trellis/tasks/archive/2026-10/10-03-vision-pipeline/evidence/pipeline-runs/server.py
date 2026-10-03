"""E2E fixture HTTP server:静态文件 + mock enrich LLM(POST /v1/chat/completions)。

- 双栈绑定(bases:空 = 全接口),静态目录 ./static;
- 每个请求记一行到 /tmp/myia-e2e/access.log(GET/POST + 路径);
- mock LLM:请求体全文追加 /tmp/myia-e2e/llm_requests.jsonl;从 user 消息里
  解析待评条目 url,返回可解析的评分 JSON;usage.total_tokens=1234;
  /tmp/myia-e2e/llm_sleep 存在且为数字时,每个请求先 sleep 该秒数(AC7 中断用)。
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path("/tmp/myia-e2e")
STATIC = ROOT / "static"
ACCESS = ROOT / "access.log"
LLM_REQ = ROOT / "llm_requests.jsonl"
LLM_SLEEP = ROOT / "llm_sleep"

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html",
        ".json": "application/json", ".txt": "text/plain"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log(self, note=""):
        line = f"{time.time():.3f} {self.command} {self.path} {note}".strip()
        with ACCESS.open("a") as f:
            f.write(line + "\n")

    def do_GET(self):
        self._log()
        path = unquote(urlparse(self.path).path)
        if path.startswith("/static/"):
            path = path[len("/static"):]
        if path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self._send(200, body, "text/plain")
            return
        file = (STATIC / path.lstrip("/")).resolve()
        if not str(file).startswith(str(STATIC.resolve())) or not file.is_file():
            self._send(404, b"not found", "text/plain")
            return
        data = file.read_bytes()
        self._send(200, data, MIME.get(file.suffix, "application/octet-stream"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if urlparse(self.path).path != "/v1/chat/completions":
            self._log(f"404 POST:{self.path}")
            self._send(404, b"not found", "text/plain")
            return
        self._log(f"llm bytes={length}")
        try:
            body = json.loads(raw)
        except Exception:
            self._send(400, b"bad json", "text/plain")
            return
        with LLM_REQ.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "body": body}, ensure_ascii=False) + "\n")
        if LLM_SLEEP.exists():
            try:
                secs = float(LLM_SLEEP.read_text().strip() or 0)
            except ValueError:
                secs = 0
            if secs > 0:
                time.sleep(secs)
        content = ""
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                content = msg.get("content", "")
        urls = re.findall(r'"url":\s*"([^"]+)"', content)
        scores = json.dumps(
            [{"url": u, "value": 8, "relevance": 7, "credibility": 6,
              "reason": "E2E mock 固定分"} for u in urls],
            ensure_ascii=False)
        resp = {"id": "chatcmpl-e2e", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": scores}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 100,
                          "total_tokens": 1234}}
        self._send(200, json.dumps(resp, ensure_ascii=False).encode(), "application/json")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):  # 静默 stderr
        pass


if __name__ == "__main__":
    for f in (ACCESS, LLM_REQ):
        f.unlink(missing_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("fixture server on http://127.0.0.1:8765", flush=True)
    server.serve_forever()
