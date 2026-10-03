"""detail E2E fixture HTTP server(10-03-detail-images AC1/2/3)。

复用 10-03-vision-pipeline 的 server 形态(ThreadingHTTPServer @127.0.0.1:8765,
静态目录 ./static,逐请求记 /tmp/myia-di-e2e/access.log)。本任务无 enrich LLM,
砍 POST mock。页面:
  /index.html   列表页:4 张 card(标题带 MYIA),零 <img>(条目无图 → 触发追抓)
  /detail-a.html 有字真图(同域绝对路径)+ 噪声渐变(相对路径)+ 跨域/data: 干扰项
  /detail-c.html 零 <img>(SPA 空页对照)
  /detail-d.html 仅跨域 <img>(同域过滤对照)
  /detail-b.html 不存在 → 404(失败页对照)
条目 e 指向 127.0.0.1:9999(端口关闭 → connection refused → failed:network)。
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import time

ROOT = Path("/tmp/myia-di-e2e")
STATIC = ROOT / "static"
ACCESS = ROOT / "access.log"

MIME = {".png": "image/png", ".html": "text/html", ".txt": "text/plain"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log(self, note=""):
        line = f"{time.time():.3f} {self.command} {self.path} {note}".strip()
        with ACCESS.open("a") as f:
            f.write(line + "\n")

    def do_GET(self):
        self._log()
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html", "/feed"):
            path = "/index.html"
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
    ACCESS.unlink(missing_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("detail fixture server on http://127.0.0.1:8765", flush=True)
    server.serve_forever()
