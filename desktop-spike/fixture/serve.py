"""desktop-spike 夹具静态服务:仅绑定 127.0.0.1,serve 本目录,零外网。

用法:python3 serve.py [port]   (默认 8765)
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
