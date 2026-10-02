"""PyInstaller 入口:转发到 myia.cli.main,退出码契约(0/1/2/3)原样透传。

不用 console_scripts 包装,直接 import 调用,避免 entry-point 元数据依赖。
"""

from __future__ import annotations

import sys

from myia.cli import main

if __name__ == "__main__":
    sys.exit(main())
