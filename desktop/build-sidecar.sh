#!/bin/zsh
# desktop sidecar 构建脚本(可重放,v1.1 桌面打包从此处出):
# 1) spike 隔离 venv(不动项目 .venv / pyproject / uv.lock)
# 2) pip 安装 pyinstaller + 本项目(依赖从 PyPI 拉,仅进本 venv)
# 3) PyInstaller --onefile 打包 entry.py → dist/myia
set -euo pipefail
SPIKE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SPIKE_DIR/.venv-build"

if [[ ! -x "$VENV/bin/python" ]]; then
  uv run --no-sync python -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
# 可编辑安装:仓库 .gitignore 的 `secrets.*` 误伤 src/myia/secrets.py(未被 git
# 跟踪),hatchling 打 wheel 会把该模块排除,PyInstaller 随之漏包导致
# `from myia import secrets` ImportError;编辑态直指 src/ 目录绕开该问题
# (仓库级修复——gitignore 规则加 src/myia/secrets.py 例外——不在本 spike 边界)。
"$VENV/bin/pip" install --quiet "pyinstaller>=6.10" -e "$SPIKE_DIR/.."
# --hidden-import myia.secrets:src/myia/schema.py 的 `from myia import secrets`
# 与 stdlib secrets 同名,PyInstaller modulegraph 会解析到 stdlib 而漏收
# myia/secrets.py(2026-10-01 spike 实测),须显式点名。
# --collect-submodules myia:registry/push/classify 按字符串名动态 import 引擎
# 与通道模块,静态分析看不见,须整体收编(spike 实测:漏收时报
# No module named 'myia.engines.static_html',采集全失败退出码 2)。
"$VENV/bin/pyinstaller" --onefile --name myia --clean --noconfirm \
  --hidden-import myia.secrets \
  --collect-submodules myia \
  --distpath "$SPIKE_DIR/dist" --workpath "$SPIKE_DIR/build-pyi" \
  --specpath "$SPIKE_DIR" "$SPIKE_DIR/entry.py"
echo "sidecar built: $SPIKE_DIR/dist/myia"
