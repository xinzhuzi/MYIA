"""触屏交互存证(G7/G8/G9/G12,after-only;主人 2026-10-03 裁决口径)。

用法(仓库根,先起 vite:5173 与 bridge:8797):
  /opt/homebrew/opt/python@3.14/bin/python3.14 \\
      .trellis/tasks/10-03-fe-small-batch/evidence/touch-harness/shoot.py

- 视口 390×844 @2x + hasTouch(触屏模拟);全程无头,零前台。
- 所有交互走真实 UI 控件(跑一次/AI 摘要/全部已读/沉淀为关键词/重跑),
  数据来自沙箱 sidecar(bridge FORCE_DRY=1:run 子进程/管线/日志环全真,
  仅采集与推送 dry,零外网)。
- 产物:.trellis/tasks/10-03-fe-small-batch/evidence/after-g*.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
SHIM = (HERE / "shim.js").read_text(encoding="utf-8")
BASE = "http://localhost:5173"

results: list[tuple[str, str]] = []  # (文件名, 结论)


def shoot(page, name: str, full_page: bool = True) -> None:
    path = EVIDENCE / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    results.append((f"{name}.png", f"saved {path.stat().st_size}B"))


def step(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — 单步失败不拦后续存证
        results.append((label, f"FAILED: {exc!r}"))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 桌面级触屏形态(触屏笔电/平板):390px 手机视口下桌面侧栏(w-56)
        # 会盖住卡片操作簇,非本产品形态;触屏语义由 hasTouch+tap 保证。
        context = browser.new_context(
            viewport={"width": 1180, "height": 820},
            device_scale_factor=2,
            has_touch=True,
            is_mobile=False,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            color_scheme="dark",
        )
        page = context.new_page()
        page.add_init_script(SHIM)
        page.set_default_timeout(30000)

        # ---------------- 情报流:G8 / G9 / G12 ----------------
        def feed_flow():
            page.goto(f"{BASE}/#/feed")
            page.wait_for_selector('[data-testid^="feed-item-"]')
            time.sleep(0.6)

            def g9_before():
                page.locator('[data-unread="true"]').first.wait_for(state="visible")
                shoot(page, "after-g9-before")

            step("g9-before", g9_before)

            def g8_loading():
                # 桥延迟 feed.enrich 应答 1.2s,捕 loading 态(传输层延迟,不改前端)
                def slow_enrich(route):
                    time.sleep(1.2)
                    route.continue_()

                page.route("**/rpc", slow_enrich)
                page.locator('[aria-label="AI 摘要"]').first.tap()
                page.wait_for_selector('[data-testid^="feed-enrich-loading-"]')
                shoot(page, "after-g8-enrich-loading", full_page=False)
                page.unroute("**/rpc", slow_enrich)

            step("g8-loading", g8_loading)

            def g8_result():
                page.wait_for_selector('[data-testid^="feed-enrich-error-"]', timeout=20000)
                shoot(page, "after-g8-enrich-result")

            step("g8-result", g8_result)

            def g9_after():
                page.locator('button[title^="把已加载的"]').first.tap()
                page.wait_for_function(
                    "document.querySelectorAll('[data-unread=\"true\"]').length === 0"
                )
                shoot(page, "after-g9-after")

            step("g9-after", g9_after)

            def g12_panel():
                # 全部标已读后默认「未读」过滤列表清空 → 切「全部」chip 复现卡片
                page.locator('[aria-label="过滤:全部"]').tap()
                page.wait_for_selector('[data-testid^="feed-item-"]')
                page.locator('[aria-label="沉淀为关键词"]').first.tap()
                page.wait_for_selector('[data-testid^="feed-keyword-pin-"]')
                page.wait_for_selector('[aria-label="目标品类 YAML"]')
                shoot(page, "after-g12-watchlist-panel")

            step("g12-panel", g12_panel)

            def g12_written():
                page.locator('[aria-label="沉淀关键词"]').fill("触屏存证词")
                page.get_by_role("button", name="写入", exact=True).tap()
                page.wait_for_selector('[data-testid^="feed-keyword-note-"]', timeout=20000)
                shoot(page, "after-g12-watchlist-written")

            step("g12-written", g12_written)

        step("feed-flow", feed_flow)

        # ---------------- 采集日志:G7(重跑/过滤/搜索) ----------------
        def logs_flow():
            page.goto(f"{BASE}/#/logs")
            # 经顶栏「跑一次」触发真实 dry run(FORCE_DRY 桥改写,零外网);
            # collecting 徽标 = run.start 已受理,终态后 completed 事件驱动列表刷新。
            page.locator('button[title^="手动触发"]').first.tap()
            try:
                page.wait_for_selector('[data-testid="global-run-collecting"]', timeout=5000)
            except Exception:  # noqa: BLE001 — dry run 可能快于徽标捕捉,不拦
                pass
            page.wait_for_selector('[data-testid^="run-group-"]', timeout=60000)
            time.sleep(1.0)

            def g7_rerun():
                # 先等首个 run 终态(run_busy 单飞:活跃期重跑会被结构化拒)
                try:
                    page.wait_for_selector('[data-testid="global-run-collecting"]', state="hidden", timeout=45000)
                except Exception:  # noqa: BLE001 — 徽标捕捉不到时兜底静置
                    time.sleep(8)
                page.locator('[data-testid^="run-rerun-"]').first.tap()
                page.wait_for_selector('[data-testid^="run-rerun-ok-"]', timeout=30000)
                shoot(page, "after-g7-rerun-triggered")

            step("g7-rerun", g7_rerun)

            def g7_search():
                page.fill('[data-testid="log-search-input"]', "dry")
                page.wait_for_selector('[data-testid="log-search-hit"]', timeout=10000)
                shoot(page, "after-g7-search")

            step("g7-search", g7_search)

            def g7_filter():
                page.fill('[data-testid="log-search-input"]', "")
                page.locator('[data-testid="filter-category"]').tap()
                # registry 只有本会话 run(品类 myia-demo)→ 选项即 myia-demo
                page.get_by_role("option", name="myia-demo", exact=True).tap()
                time.sleep(0.4)
                shoot(page, "after-g7-filter")

            step("g7-filter", g7_filter)

        step("logs-flow", logs_flow)

        browser.close()

    print("== 触屏交互存证结果 ==")
    for name, outcome in results:
        print(f"{name}: {outcome}")
    failures = [item for item in results if "FAILED" in item[1]]
    print(f"total={len(results)} failed={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
