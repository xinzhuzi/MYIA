# Implement:v1.1.2 桌面补全批次收口(2026-10-04)

## 0. 路线(接手收口,非全新实现)

盘点结论(prd.md 验收线逐项核对后的归属):

- **已提交半边**(不属本会话改动,只核验):
  - 协议方法 run.cancel/runs.list/secret.delete/sources.test、store.items
    游标、feedback.*/store.trend —— dc1cf86 及此前切片;
  - 壳层 respawn + sidecar_restart(main.rs +162)+ use-sidecar-status
    reprobe 升级 —— dc1cf86;
  - CHANGELOG Unreleased 本批全部条目 + v2 统一 bump 记账。
- **在途滞留半边(本批 UI 新文件,未提交、load-bearing)**:
  - `desktop/ui-src/src/components/layout/global-run.tsx` + `.test.tsx`(C12,
    C2 取消按钮落点)
  - `desktop/ui-src/src/screens/dashboard/trend-card.tsx` + `.test.tsx`、
    `feedback-stats-card.tsx`(B4/B2;B4 历史窗口依赖 C3 runs.list)
  - `desktop/ui-src/src/screens/feed/feed-card-feedback.tsx` + `.test.tsx`(B2)
  - `desktop/ui-src/src/lib/api/client.test.ts`(C7 门面对账测试)
  - 以上被已跟踪页面(top-bar/dashboard-screen/feed-screen)import,缺任一
    即编译红——属本批收口范围,保持原样(不重写),由门禁证实其绿。

## 1. 收口会话执行清单(2026-10-04;证据 evidence/gates.md)

1. [x] 盘点:git status/diff 全量核对,归属裁定入 design.md §1。
2. [x] 补档:design.md(as-built 设计捕获)+ 本 implement.md。
3. [x] 无头门禁(全显式退出码,详见 evidence/gates.md):
   - `uv run --no-sync python -m pytest -q` → 3037 passed / 19 skipped,exit 0;
   - `uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q`
     → 105 passed,exit 0(含 C1 :2035 / C2 :1939+:1977 / C3 :1989 /
     C5 :2016 / C13 :2123+:2144+:2160);
   - `npm --prefix desktop/ui-src run test` → 262 passed / 20 files,exit 0
     (含在途 8 文件的 29 条新用例);
   - `npm --prefix desktop/ui-src run build`(tsc -b && vite build)→ exit 0;
   - `cargo check` → exit 0;`cargo test`(src-tauri)→ 2 passed(backoff
     序列锁),exit 0。
4. [x] C7 对账实测:spec 注册表 43 行 ↔ `_HANDLERS` 43 零漂;client 门面
   22 键 = 头注分项(核心 10 + v112 批 8 + feed-ux 3 + fe-small-batch 1)。
5. [x] 协议版本裁定核验:本批新增并入 v2 ledger(CHANGELOG 明示),现值 6
   归 fe-small-batch 在途;本收口零新增方法,不 bump 不改 CHANGELOG。
6. [ ] GUI 冒烟两演示(C2 杀 sidecar 救回 / C3 重启 .app 历史可达):
   静默纪律留主人,runbook = evidence/gui-smoke-runbook.md。
7. [ ] 重打包:按 parity/feed-ux 两档注记,entry.py 归静后统一
   `npm --prefix desktop run tauri build` 冒烟;本会话不执行(理由见
   design.md §8)。

## 2. 不做与理由

- 不二次 bump PROTOCOL_VERSION、不改 CHANGELOG(一次 bump 合流政策;
  本批条目已在册)。
- 不 git commit/push(主线统一落)。
- 不重写任何在途实现;未发现归属本批的红项或缺口(门禁全绿)。
- census 活清单回标:parity 档验收线含回标项,本 stub 从未入清单
  (superseded 档),无需回标动作。
