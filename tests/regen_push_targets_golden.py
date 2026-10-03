"""push targets 黄金基线的显式再生成入口(10-03-golden-frozen-snapshots)。

黄金回归(`tests/test_push_schema_targets.py::TestGoldenRegression`)只对
``tests/fixtures/push_targets_golden/`` 下的**冻结副本**断言,与活体
``plugins/*.yaml`` 彻底解耦——并行会话合法演进自己品类的 push 配置不再
误伤该测试,也因此**不再需要**随手"golden 同步"。

本脚本是唯一的基线刷新通道,两档用法(都显式,绝不静默跟随活库):

    # ① 同源自检:仅从冻结副本重算黄金 JSON(不触碰冻结副本)。
    #    连跑两次输出必须逐字节一致;与仓库内 JSON 一致即"同源可复现"。
    uv run --no-sync python tests/regen_push_targets_golden.py

    # ② 显式刷新基线:先从活库逐份重新冻结副本,再重算黄金 JSON。
    #    仅当 schema 语义演进导致冻结版与活版漂移过大、经评审决定重立
    #    基线时使用(PRD 升级路径);新品类入黄金集 = 手改 MANIFEST 再跑。
    uv run --no-sync python tests/regen_push_targets_golden.py --refreeze

MANIFEST 即黄金集清单:键为仓库根相对路径,冻结副本镜像同一路径落位
(``GOLDEN_DIR / key``),故 plugins/stocks.yaml 与 tests/fixtures/stocks.yaml
两名冲突天然消解。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from myia.schema import load_category_file  # noqa: E402

GOLDEN_DIR = REPO / "tests" / "fixtures" / "push_targets_golden"
GOLDEN_JSON = REPO / "tests" / "fixtures" / "push_targets_golden_before.json"

# 黄金集:仓库根相对路径(与黄金 JSON 的键一一对应)。收编新品类或
# 移除旧品类在此显式增删,不允许脚本自动扫描活库(那正是被治的病)。
MANIFEST: tuple[str, ...] = (
    "plugins/ai-news.yaml",
    "plugins/credentials.yaml",
    "plugins/games.yaml",
    "plugins/gpu-prices.yaml",
    "plugins/monitor.yaml",
    "plugins/news.yaml",
    "plugins/stocks.yaml",
    "plugins/wool.yaml",
    "tests/fixtures/stocks.yaml",
)


def _strip_additive_targets(dump: dict) -> None:
    """剥除 targets 新增键——与测试侧 ``_strip_additive_targets`` 同一语义。

    冻结副本均为 targets 落地前的旧 YAML,targets 只能是空表;若某副本
    出现非空 targets,说明冻结来源混入了新写法,生成期即报错拒收。
    """
    for push in dump.get("push", []):
        assert push.pop("targets") == [], "冻结副本含非空 targets,拒绝生成"
        for rule in push.get("route", []):
            assert rule.pop("targets") == [], "冻结副本 route 含非空 targets,拒绝生成"


def refreeze() -> None:
    """从活库逐份复制冻结副本(显式基线刷新,不走 = 冻结版永不漂移)。"""
    for key in MANIFEST:
        src = REPO / key
        if not src.is_file():
            raise SystemExit(f"MANIFEST 指向的活库文件不存在: {key}")
        dst = GOLDEN_DIR / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"frozen  {key} -> {dst.relative_to(REPO)}")


def regenerate() -> None:
    """从冻结副本重算黄金 JSON(同源复现;逐字节格式:indent=1/sorted/non-ascii)。"""
    data: dict[str, dict] = {}
    for key in sorted(MANIFEST):
        cfg = load_category_file(GOLDEN_DIR / key)
        dump = cfg.model_dump(mode="json")
        _strip_additive_targets(dump)
        data[key] = dump
    GOLDEN_JSON.write_text(
        json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"regen   {GOLDEN_JSON.relative_to(REPO)}({len(data)} 份 fixture)")


def main(argv: list[str]) -> int:
    if "--refreeze" in argv[1:]:
        refreeze()
    elif not GOLDEN_DIR.is_dir():
        raise SystemExit("冻结目录不存在:先跑 --refreeze 建立基线")
    regenerate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
