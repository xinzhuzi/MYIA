"""触屏交互存证(seed):沙箱 MYIA_HOME 造可交互数据(条目 + 历史 run)。

用法(仓库根):uv run python .trellis/tasks/10-03-fe-small-batch/evidence/touch-harness/seed.py <sandbox_home>

- items:6 条(news ×4 / stocks ×2),与 tests/test_desktop_sidecar_protocol.py
  `_seed_items_for_export` 同门(store.save_item + ItemRecord)。
- runs:3 条历史 run(news success / stocks partial / news failed),走
  SQLiteStore.start_run + finish_run 正门 API,供日志屏瀑布/过滤/重跑(rerun
  回放 yaml + dry,零外网)。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from myia.store.models import ItemRecord
from myia.store.sqlite import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parents[5]
DEMO_YAML = REPO_ROOT / "plugins" / "myia-demo.yaml"


def main() -> int:
    home = Path(sys.argv[1]).resolve()
    home.mkdir(parents=True, exist_ok=True)
    db = home / "myia.db"
    store = SQLiteStore(str(db))

    base = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
    items: list[tuple[str, str, str]] = [
        ("GitHub 新星甲:myia 桌面端对齐", "news", "github"),
        ("RSS 乙:Rust 周报摘录", "news", "blog"),
        ("HackerNews 丙:本地模型推理加速", "news", "hackernews"),
        ("RSS 丁:开源情报工具链动态", "news", "blog"),
        ("股票戊:半导体板块异动", "stocks", "api"),
        ("API 己:指数成分调整公告", "stocks", "api"),
    ]
    for index, (title, category, source) in enumerate(items):
        store.save_item(
            ItemRecord(
                url=f"https://example.com/touch-{index}",
                dedup_key=f"touch-{index}",
                title=title,
                content=f"{title} 正文内容,供触屏交互存证。",
                source=source,
                category=category,
                first_seen=base + timedelta(minutes=index * 7),
            )
        )

    # 历史 run ×3(status 语义 = runs 表注释:running|success|partial|failed)
    started = base - timedelta(hours=3)
    r1 = store.start_run("news")
    store.finish_run(r1, status="success", stats={"fetched": 4, "new": 4})
    r2 = store.start_run("stocks")
    store.finish_run(r2, status="partial", stats={"fetched": 2, "new": 1})
    r3 = store.start_run("news")
    store.finish_run(
        r3,
        status="failed",
        stats=None,
        error="推送通道未配置:push telegram 凭据缺失(keychain myia/push/telegram/token)",
    )
    # 触屏重跑(rerunRun)回放 RunEntry.yaml + dry:指向仓内 demo yaml(零凭据)。
    # runs 表无 yaml 列;UI 的 RunEntry.yaml 来自 run.status 应答拼装(sidecar
    # `_run_entry_dict` 依 yaml 路径存在性回填)——dry 重跑由 UI 侧传 yaml,
    # 这里仅保证 db 侧 run 行可查。yaml 存在性核对:
    assert DEMO_YAML.is_file(), f"demo yaml 缺失: {DEMO_YAML}"
    store.close()

    # 官方插件拷贝:dev 态 sidecar `_bundle_plugins_dir()` 恒 None(entry.py:378,
    # 仅 frozen 包有种),沙箱自拷最小三件(demo 零凭据 + news/stocks 与造数
    # 品类对齐),供 health/plugins.list/跑一次/沉淀面板消费。
    import shutil

    plugins_dir = home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    for name in ("myia-demo.yaml", "news.yaml", "stocks.yaml"):
        shutil.copy2(REPO_ROOT / "plugins" / name, plugins_dir / name)

    print(f"seeded: db={db} items={len(items)} runs=3(news success/stocks partial/news failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
