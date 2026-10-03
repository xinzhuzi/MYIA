# Design:v1.1.2 桌面补全批次(C2/C3/C7 + C1/C5/C13;2026-10-04 收口)

> **路线声明(2026-10-04 实现会话)**:本档为「接手收口」补档。工作区盘点
> (git status/diff)证实本任务范围的实现已作为在途产物存在——协议与壳层
> 半边已随 10-03-v112-desktop-parity 第一/二切片提交(feed-ux 收编切片 +
> dc1cf86),UI 半边 8 个新文件(global-run/trend-card/feedback-stats-card/
> feed-card-feedback/client.test)**未提交滞留工作树**且被已跟踪页面 import
> (load-bearing)。本会话不重写,按验收线核对门禁与缺口;本文档为 as-built
> 设计捕获。superseded 档案关系见 prd.md 头注(替代档 = 10-03-v112-desktop-parity,
> 其 design.md/implement.md 为第一手设计源,本档不复制其全文,只钉本批
> 六项的实现口径与核对结论)。

## 1. 范围与划界(as-run)

| 项 | 内容 | 归属裁定 |
|---|---|---|
| C2 | sidecar 崩溃救回(respawn + reprobe 升级 + run 取消) | 本批,已落地 |
| C1 | store.items 复合游标(before/before_id) | 本批(prd 划界注记:C8/C9 归 feed-ux,C1 留本批;feed-ux 已开工且其 G1/G3 Python 半边被本批形状取代——CHANGELOG Unreleased「cursor & search」条目) |
| C3 | runs.list 直读 SQLite | 本批,已落地 |
| C7 | 封装修正 | 本批,按 parity PRD 方案①(措辞变更 2026-10-03 D10 拍板):头注释如实 + spec 注册表对账,不补 sources.write 共享封装 |
| C5 | secret.delete 入口 | 余量项,已落地 |
| C13 | 试抓此源(sources.test 异步 job) | 余量项,已落地 |

C4/C6/C11 维持登记/顺延(parity PRD「明确不做」节),不在本收口范围。

## 2. C2 壳层 respawn(as-built;desktop/src-tauri/src/main.rs)

- 生命周期事件 `sidecar://state`:`{state: respawning|online|dead, attempt?, respawned?}`,
  壳自发,非 sidecar 协议面(main.rs:21-23)。
- 自动 respawn:`Terminated` → 置空 child → drain pending(每请求回
  `sidecar_terminated`)→ `schedule_respawn`(main.rs:170-219):
  指数退避 1/2/4/8/16s(`backoff_delay`,`2u32.saturating_pow` 防溢出,
  单测锁定序列),上限 5 次转 `dead` 态(手动拉起);spawn 失败同序列自驱。
- 双 spawn 防护:锁内复核 child 仍空才 spawn(手动拉起抢先复活则自动任务
  退出,main.rs:193-196);稳定存活 10s 后 `*guard == attempt` 复核归零
  (防两代 respawn 任务交错重置,main.rs:207-215)。
- 手动拉起:`sidecar_restart` 命令(幂等:进程健在回 `restarted:false`,
  绝不杀活进程,main.rs:223-236);前端 reprobe 升级为「探测 → 失败时
  sidecar_restart → 再探测」(use-sidecar-status.ts:56-69)。
- run 取消(协议半边):`run.cancel` killpg(SIGTERM→5s→SIGKILL 兜底),
  信号终局 status=`cancelled` 可辨认;run 子进程注册表与 `_RUNS` 分家
  (entry.py:2256-2259 注记)。

## 3. C1 复合游标(as-built;entry.py store.items)

- `before`(ISO 时间,first_seen 严格小于)+ `before_id`(正整数,须与
  before 同传)组成 `(first_seen, id)` 复合游标——同刻条目超单页 limit 时
  元组比较可推进(entry.py:750-773);feed/api.ts 判停改「返回数 < limit」,
  旧 since 复用 + 客户端去重 + added==0 仅留防御兜底。
- 协议级测试:`test_store_items_same_timestamp_pagination_to_exhaustion`
  (tests/test_desktop_sidecar_protocol.py:2035)。

## 4. C3 runs.list(as-built;entry.py + SQLiteStore.list_runs)

- `runs.list` 直读 runs 表(SQLiteStore.list_runs,新→旧,limit 钳制
  [1,200]),sidecar 重启后历史可达;内存 `_RUNS` 仅保留「进行中」语义。
- 行形状 = `_run_record_to_dict`(entry.py:2316 注:与 completed.record
  同一形状防两处漂移)。
- 测试:`test_runs_list_reads_table_newest_first`(:1989)。

## 5. C7 封装修正(方案①,as-built)

- client.ts 头注释如实:核心 10 + v112 批 8(runCancel/runsList/
  secretDelete/sourcesTest/feedbackMark/feedbackList/feedbackStats/
  storeTrend)+ feed-ux 3 + fe-small-batch 1 = 22 键,明示「非协议全量」,
  屏私有封装名单(sources.write/yaml.*/image.*/channels.*/push.write)。
- sources/api.ts 头注释:删「将得 method_not_found」失实句,改「已收编
  进 _HANDLERS」+ spec 变更纪律第 3 条注记。
- 对账机制(本收口会话实测 2026-10-04):spec 注册表 43 行 ↔ `_HANDLERS`
  43 方法零漂(含在途 feed.enrich 行,树内自洽);client 门面 22 键与头注
  分项计数一致。注册表纪律测试
  `test_method_registry_allowed_matches_handlers` 常驻协议套件。

## 6. C5 / C13(as-built)

- C5 `secret.delete`:经 myia.secrets 同门;二次删除 `secret_not_found`;
  settings 凭据行删除动作带 confirm,凭据只名无值不涉回显。
  测试 `test_secret_delete_roundtrip`(:2016)。
- C13 `sources.test`:试抓此源,异步 job(单飞 `test_busy`;每源 CLI 120s
  超时 = 壳层单请求硬超时,同步实现构造性禁用);结果走 `test.completed`
  事件(ok:false 透传子进程级错误)。测试 :2123/:2144/:2160 三条。

## 7. 协议版本与 CHANGELOG(裁定)

- 本批协议新增(run.cancel/runs.list/secret.delete/sources.test + C1 游标/
  query)并入 **v2 统一 bump 记账**(CHANGELOG Unreleased「protocol version
  bumped to 2」与 v112 批条目明示 absorbed;second-slice 四方法 riding the
  existing ledger)——一次 bump 合流政策,本收口不二次 +1。
- 工作树现值 PROTOCOL_VERSION=6 为 fe-small-batch 在途 bump(v6),归彼线;
  本批零新增协议方法,不动版本号。
- CHANGELOG 已含本批全部条目(协议方法/游标/respawn/second-slice UI),
  2026-10-04 收口会话实读核对。

## 8. 验证策略(收口会话执行;证据见 evidence/gates.md)

- 无头门禁:全量 pytest、协议套件 pytest、vitest 全量、`tsc -b && vite build`、
  `cargo check` + `cargo test`(backoff 单测)——全部显式退出码。
- GUI 冒烟(C2 杀进程救回演示、C3 重启 .app 历史可达演示):静默纪律禁止
  本会话执行抢前台/GUI 动作,落 runbook 留主人(evidence/gui-smoke-runbook.md)。
- 重打包(tauri build):parity 与 feed-ux 两档注记均已裁定「entry.py 归静
  后统一重打包冒烟」;当前 entry.py 带并行批次在途改动(+186 行),本会话
  不重打包,不抢统一冒烟批次。
