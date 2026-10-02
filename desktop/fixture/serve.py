"""本地夹具静态服务(唯一假源目录):仅绑定 127.0.0.1,serve 本目录,零外网。

本目录是桌面端与 docs/demo 共用的本地假源(2026-10-02 夹具合一):
- page.html    — 桌面 sidecar 往返夹具(desktop/fixture/plugin.yaml 抓取)
- index.html   — docs/demo「3 分钟上手」演示源(demo-news.yaml 抓取 `/`)

用法:python3 serve.py [port]   (默认 8765)
(等价一行:python3 -m http.server 8765 --bind 127.0.0.1 --directory desktop/fixture)
"""

from __future__ import annotations

import functools
import http.server
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIR)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"serving {DIR} on http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
