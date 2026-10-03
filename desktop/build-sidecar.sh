#!/usr/bin/env bash
# desktop sidecar 构建脚本(v1.1 产品化:可指定目标平台,产物直落 Tauri externalBin 位):
#   ./build-sidecar.sh                     # 默认目标 = 当前主机三元组
#   ./build-sidecar.sh aarch64-apple-darwin
#   ./build-sidecar.sh x86_64-pc-windows-msvc
#   MYIA_SIDECAR_SKIP=1 ./build-sidecar.sh <target>   # 目标产物已存在时跳过(CI 二段式构建用)
# 流程:
#   1) 桌面构建隔离 venv .venv-build(不动项目 .venv / pyproject / uv.lock)
#   2) pip 安装 pyinstaller + 本项目(依赖从 PyPI 拉,仅进本 venv)
#   3) PyInstaller --onefile 打包 entry.py → dist/myia-core
#   4) 拷贝为 src-tauri/binaries/myia-core-<target>[.exe](tauri.conf externalBin 约定命名,
#      Tauri 按当前 target triple 自动拾取)
# 注意:PyInstaller 不支持交叉编译——目标平台与主机不符时直接报错退出。
set -euo pipefail
SPIKE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SPIKE_DIR/.." && pwd)"
VENV="$SPIKE_DIR/.venv-build"
DIST="$SPIKE_DIR/dist"
BIN_DIR="$SPIKE_DIR/src-tauri/binaries"

# ---- 目标平台解析 ------------------------------------------------------------
# 已知三元组(aarch64-apple-darwin / x86_64-apple-darwin / x86_64-pc-windows-msvc);
# 其余取值不拦(交给使用者自担),但会打印提示。
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  if command -v rustc >/dev/null 2>&1 && rustc -vV 2>/dev/null | grep -q '^host: '; then
    TARGET="$(rustc -vV | sed -n 's/^host: //p')"
  else
    case "$(uname -sm)" in
      "Darwin arm64")  TARGET="aarch64-apple-darwin" ;;
      "Darwin x86_64") TARGET="x86_64-apple-darwin" ;;
      "Linux x86_64")  TARGET="x86_64-unknown-linux-gnu" ;;
      "Linux aarch64") TARGET="aarch64-unknown-linux-gnu" ;;
      MINGW*|MSYS*|CYGWIN*) TARGET="x86_64-pc-windows-msvc" ;;
      *) echo "错误:无法推断主机三元组(uname $(uname -sm) 未识别),请显式传入目标参数。" >&2; exit 1 ;;
    esac
  fi
fi
case "$TARGET" in
  *-apple-darwin*)    TARGET_OS="darwin" ;;
  *-windows-*|MINGW*|MSYS*) TARGET_OS="windows" ;;
  *-linux-*)          TARGET_OS="linux" ;;
  *) echo "提示:目标 $TARGET 不在已知列表(darwin/windows/linux 三元组),按主机平台继续。" >&2
     TARGET_OS="" ;;
esac
case "$(uname -s)" in
  Darwin)  HOST_OS="darwin" ;;
  Linux)   HOST_OS="linux" ;;
  MINGW*|MSYS*|CYGWIN*) HOST_OS="windows" ;;
  *) HOST_OS="unknown" ;;
esac
if [[ -n "$TARGET_OS" && "$TARGET_OS" != "$HOST_OS" ]]; then
  echo "错误:目标平台 $TARGET 与主机平台 $HOST_OS 不符——PyInstaller 不支持交叉编译," >&2
  echo "请在对应平台上运行本脚本(GitHub Actions 由 desktop-release.yml 按平台 matrix 各自构建)。" >&2
  exit 1
fi
EXT=""
[[ "$TARGET_OS" == "windows" ]] && EXT=".exe"
SIDECAR_OUT="$BIN_DIR/myia-core-$TARGET$EXT"

# ---- 幂等跳过(仅显式 MYIA_SIDECAR_SKIP=1 时) --------------------------------
if [[ "${MYIA_SIDECAR_SKIP:-0}" == "1" && -f "$SIDECAR_OUT" ]]; then
  echo "MYIA_SIDECAR_SKIP=1 且产物已存在,跳过 sidecar 构建: $SIDECAR_OUT"
  exit 0
fi

# ---- 隔离 venv + PyInstaller --------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "错误:未找到 uv(脚本经 uv 建隔离 venv,不碰项目 .venv)。请先安装 uv 或配好 PATH。" >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/python" && ! -x "$VENV/Scripts/python.exe" ]]; then
  uv run --no-sync python -m venv "$VENV"
fi
# Windows venv 的解释器在 Scripts/ 而非 bin/(Git Bash 下两处都探测)
PYBIN="$VENV/bin/python"
[[ -x "$PYBIN" ]] || PYBIN="$VENV/Scripts/python.exe"
# 依赖解析必须走 uv 的 workspace 语义:pip 无法解析 myia-classifier(workspace
# 成员,不在 PyPI);借 UV_PROJECT_ENVIRONMENT 把锁定的依赖集(含 workspace
# 成员、可编辑安装的 myia 本体)装进隔离 venv,不动项目 .venv。
# --extra vision:看图双引擎(ocrmac + rapidocr-onnxruntime,task 10-03-image-input
# AC10 装包冒烟;核心依赖不动,extras 走 uv.lock 冻结集)
UV_PROJECT_ENVIRONMENT="$VENV" uv sync --frozen --no-dev --extra vision --project "$ROOT_DIR" --quiet
uv pip install --python "$PYBIN" --quiet "pyinstaller>=6.10"
# --hidden-import myia.secrets:src/myia/schema.py 的 `from myia import secrets`
# 与 stdlib secrets 同名,PyInstaller modulegraph 会解析到 stdlib 而漏收
# myia/secrets.py(2026-10-01 spike 实测),须显式点名。
# --collect-submodules myia:registry/push/classify 按字符串名动态 import 引擎
# 与通道模块,静态分析看不见,须整体收编(spike 实测:漏收时报
# No module named 'myia.engines.static_html',采集全失败退出码 2)。
# --add-data keywords.json:myia_classifier/builtin.py 的 DEFAULT_TABLE_PATH 以
# __file__ 定位 data/keywords.json,onefile 冻结包只收代码不收包内数据文件,
# 缺失即 classify(builtin: true)构造期 config_error「分类关键词表加载失败」
# (2026-10-03 真机冒烟实测)。落位 _MEIPASS/myia_classifier/data/,与冻结后
# __file__ 同基(--add-data 目标分隔符 POSIX ':' / Windows ';')。
# --collect-all ocrmac / rapidocr_onnxruntime:vision 双引擎经 importlib 惰性
# import(同 myia 引擎的动态 import 问题),静态分析看不见;rapidocr 的内置
# onnx 模型是包内数据文件,须连带采集(task 10-03-image-input AC10)。
# --collect-all openai:VisionClient 同为 importlib 惰性 import(10-03 装机
# 冒烟:漏收时运行期 image_provider_error「vision 依赖 openai 未安装」)。
# --collect-all huggingface_hub:vision/models.py 的 snapshot_download/HfApi
# 同为惰性 import(10-03-vision-v2 模型下载;与手维 spec 等价,见下方 spec 策略注)。
DATA_SEP=":"
[[ "$HOST_OS" == "windows" ]] && DATA_SEP=";"
mkdir -p "$DIST" "$BIN_DIR"
# 多会话共享机构建竞态治理(10-03-shared-build-races):PyInstaller 默认缓存
# ~/Library/Application Support/pyinstaller 是全机共享的,--clean 会整删——
# 并行会话同时打包互删缓存实锤三次。导出检出内私有 CONFIG_DIR 隔离。
export PYINSTALLER_CONFIG_DIR="$SPIKE_DIR/.pyinstaller-cache"
PYINST="$VENV/bin/pyinstaller"
[[ -x "$PYINST" ]] || PYINST="$VENV/Scripts/pyinstaller.exe"
# 命名:sidecar 叫 myia-core 而非 myia——主程序 mainBinaryName=MYIA,macOS APFS
# 大小写不敏感,sidecar 若叫 myia 会在 Contents/MacOS/ 与 MYIA 撞名互相覆盖。
#
# spec 策略(2026-10-03,v112 批打包面回归治本):仓库手维 myia-core.spec
# (SPECPATH 相对化,10-03-public-leak-sweep c97c897——公开仓不得带本机绝对
# 路径)存在时**直接以其为源构建**;下列 CLI 重生成只作首跑 bootstrap——
# PyInstaller 以 CLI 旗标生成 spec 时会把 entry.py/add-data 回写成本机绝对
# 路径,tauri build → beforeBuildCommand 每跑一次就把手维 spec 冲回绝对路径
# (dc178e1/c97c897 相对化两次落地两次被冲,实锤)。手维 spec 与下方旗标集
# 等价(collect_submodules myia + myia.secrets + collect_all×3 + keywords.json
# + onefile + name);增删依赖改 spec 本体,勿走重生成路径回退相对化。
SPEC="$SPIKE_DIR/myia-core.spec"
if [[ -f "$SPEC" ]]; then
  "$PYINST" --clean --noconfirm \
    --distpath "$DIST" --workpath "$SPIKE_DIR/build-pyi" "$SPEC"
else
  "$PYINST" --onefile --name myia-core --clean --noconfirm \
    --hidden-import myia.secrets \
    --collect-submodules myia \
    --collect-all ocrmac \
    --collect-all rapidocr_onnxruntime \
    --collect-all openai \
    --collect-all huggingface_hub \
    --add-data "$ROOT_DIR/myia-classifier/myia_classifier/data/keywords.json${DATA_SEP}myia_classifier/data" \
    --distpath "$DIST" --workpath "$SPIKE_DIR/build-pyi" \
    --specpath "$SPIKE_DIR" "$SPIKE_DIR/entry.py"
fi
cp "$DIST/myia-core$EXT" "$SIDECAR_OUT"
echo "sidecar built: $SIDECAR_OUT (target: $TARGET)"
