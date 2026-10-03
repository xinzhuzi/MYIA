# v112-desktop-batch 收口门禁证据(2026-10-04;实现会话实跑)

环境:仓库根 `/Users/zhengbingjin/Project/Github/MYIA`,HEAD=c6263d1,工作树
含并行批次在途改动(fe-small-batch entry.py+186、vision server.py、多屏 UI)。

## 门禁(显式退出码)

```
$ uv run --no-sync python -m pytest -q
3037 passed, 19 skipped in 60.11s (0:01:00)
PYTEST_FULL_EXIT:0

$ uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q
105 passed in 5.07s
PYTEST_PROTOCOL_EXIT:0
  # 本批六项在列(行号 = tests/test_desktop_sidecar_protocol.py):
  #   C1  test_store_items_same_timestamp_pagination_to_exhaustion :2035
  #   C2  test_run_cancel_full_roundtrip :1939 / test_run_cancel_refusals :1977
  #   C3  test_runs_list_reads_table_newest_first :1989
  #   C5  test_secret_delete_roundtrip :2016
  #   C13 test_sources_test_async_job_roundtrip :2123
  #       test_sources_test_unknown_source_completes_with_error :2144
  #       test_sources_test_single_flight_and_refusals :2160

$ npm --prefix desktop/ui-src run test
Test Files  20 passed (20)
     Tests  262 passed (262)
VITEST_EXIT:0
  # 含本批滞留在途 8 文件用例:global-run 6 / feed-card-feedback 6 /
  # trend-card 11 / client.test 6;feed-screen 49 条含「分页带 before/before_id
  # 复合游标」「游标停滞判停」两显名用例(C1 UI 半边)

$ npm --prefix desktop/ui-src run build        # = tsc -b && vite build
✓ built in 1.59s
UI_BUILD_EXIT:0

$ cd desktop/src-tauri && cargo check
Finished `dev` profile ... in 1.20s
CARGO_CHECK_EXIT:0

$ cd desktop/src-tauri && cargo test
running 2 tests
test tests::backoff_delay_doubles_per_attempt ... ok
test tests::backoff_delay_saturates_without_panic ... ok
test result: ok. 2 passed
CARGO_TEST_EXIT:0
```

## C7 对账(实测 2026-10-04,python3 正则逐行核)

```
spec 注册表(.trellis/spec/desktop/sidecar-protocol.md 编号行)= 43
entry.py _HANDLERS = 43
spec not in handlers: []
handlers not in spec: []          # 零漂(含 fe-small-batch 在途 feed.enrich 行)
client.ts api 门面键 = 22 = 核心10 + v112批8(runCancel/runsList/secretDelete/
  sourcesTest/feedbackMark/feedbackList/feedbackStats/storeTrend)
  + feed-ux3 + fe-small-batch1,与 client.ts:136-146 头注分项一致
sources/api.ts 头注:「已收编进 entry.py _HANDLERS」+ 封装面≠协议面注记,
  原「将得 method_not_found」失实句已不在(实读 sources/api.ts:1-20)
```

## 协议版本与 CHANGELOG 核验(实读)

- `PROTOCOL_VERSION` 工作树现值 = 6(v6 注释 = fe-small-batch G8 在途 bump);
  已提交值 = 5(vision-v2)。
- 本批协议新增(run.cancel/runs.list/secret.delete/sources.test、store.items
  before/before_id+query)并入 **v2 统一 bump**:CHANGELOG Unreleased
  「Sidecar protocol version bumped to 2」与「v1.1.2 desktop parity batch —
  protocol methods」「cursor & search」两明示 absorbed/landed 条目;
  second-slice 四方法条目明示 riding the existing ledger。
- 结论:一次 bump 合流政策已满足;本收口零新增协议方法 → 不 bump、
  CHANGELOG 无需增行。

## 在途产物归属(git 实查 2026-10-04)

- 本批 UI 新文件(load-bearing,被已跟踪页面 import,全部纳入收口验证):
  global-run.tsx/.test.tsx、trend-card.tsx/.test.tsx、feedback-stats-card.tsx、
  feed-card-feedback.tsx/.test.tsx、client.test.ts(8 个 untracked)。
- 壳层 respawn/sidecar_restart(main.rs)与协议方法半边:已提交(dc1cf86
  及此前切片),工作树 clean(main.rs 无未提交 diff)。
- 其余在途改动(fe-small-batch entry.py、vision server.py、多屏 UI 修改)
  归并行批次,本会话未触碰;全量门禁(上)在合并树上跑绿,证共存无冲突。

## 未跑项(如实)

- `npm --prefix desktop run tauri build` 重打包:未跑——parity 与 feed-ux
  两档注记均裁定「entry.py 归静后统一重打包冒烟」,当前 entry.py 带并行
  批次在途 +186 行,不抢统一冒烟批次。
- C2/C3 GUI 演示:静默纪律禁止抢前台/GUI 动作,runbook 留主人
  (gui-smoke-runbook.md)。
