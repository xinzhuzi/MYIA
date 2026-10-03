# 执行计划:v1.1.2 桌面对齐批次

> 前置:design.md(同目录)已定形;排程门 = v1.1.1 tag 后 start(开工前核对清单
> design §11.3 逐条过,任一不过即顺延)。每步独立 commit;验证命令一律显式退出码,
> 禁管道判活。C8/C9 已划归 10-03-feed-ux(grill Q1),本计划**不含**;C4/C6 登记不
> 实现;可选项 E4/E5 按余量裁,裁则在 census 归档活清单回标理由。
>
> **协议步通则(评审补,适用 D1/D3-D8)**:凡新增/扩展协议方法的步骤,同一 commit
> 更新 `.trellis/spec/desktop/sidecar-protocol.md` 注册表(23→31,新增行带处理器
> 定义行号;store.items/version 扩展改行内注记;错误码表补 `run_not_active`/
> `test_busy`/`item_not_found` 等)——spec 变更纪律第 1 条「注册表随同更新,
> 方法名集合不许漂」;下文各 D 步不再逐条重复。

## 步骤(依赖序)

### D1. C2:壳层 respawn + run.cancel + reprobe 升级(P1,最优先)

- main.rs:抽 `spawn_sidecar`;`Sidecar` 增 `respawn_attempts`;`Terminated` 分支加
  指数退避(1/2/4/8/16s,≤5 次,10s 存活归零)+ emit `sidecar://state`;新命令
  `sidecar_restart`;`#[cfg(test)]` 锁 `backoff_delay(attempt)` 纯函数。
- entry.py:`_RUN_PROCS` 登记/摘除;`_run_worker` 的 Popen 加 `start_new_session=True`
  (onefile 双进程实证见 design §2.2:bootloader+python 孙进程须进程组一锅端);
  `_m_run_cancel` = `os.killpg(os.getpgid(pid), SIGTERM)` → 标 `cancel_requested` →
  `Timer(5, killpg SIGKILL)`(ProcessLookupError 吞)→ 立即返回不阻塞循环;
  `_run_worker` 信号终局(exit_code<0)→ `status="cancelled"`;`_HANDLERS` 注册。
- UI:use-sidecar-status 增 `respawning/dead` 态 + 订阅 `sidecar://state` + reprobe
  升级「探测→拉起→再探测」;top-bar 徽标/按钮文案(本步只动状态区,跑一次在 D2)。
- 测试:protocol 增 `run.cancel` 全往返(启动本地夹具 run → cancel → run.status
  终态 cancelled → `proc.poll() is not None`)+ `run_not_active` 拒绝;vitest:hook
  状态机(dead→拉起调用 invoke("sidecar_restart"))。
  **测试边界如实注记(design §2.2)**:dev 模式测试只见单进程(无 bootloader),
  「孙进程不残留」的最终证据在 D12 冒烟第 2 条(双进程核),此处绿不冒充该验收。
- 验证:`cargo check --manifest-path desktop/src-tauri/Cargo.toml; echo $?` → 0;
  `uv run --no-sync python -m pytest -q tests/test_desktop_sidecar_protocol.py; echo $?` → 0;
  `npm --prefix desktop/ui-src run test; echo $?` → 0。
- 审查门:SIGKILL 孤儿 run 边界已注记(design §2.1);`run.cancel` 不得阻塞 serve
  循环(代码评审确认无 wait 在 handler 内);**杀进程必须 killpg(进程组),裸
  proc.kill 只杀 bootloader 漏孙进程(design §2.2 onefile 实证)**;respawn 与
  手动拉起的双 spawn 竞态有锁内复核。

### D2. C12:顶栏全局「跑一次」(依赖 D1 的 run.cancel)

- 新组件 `components/layout/global-run.tsx`:品类选择(消费 feed-ux C8 提升态;
  未合入则就地提升,注记待 C8 合并时复用)→ `run.start` → running 态 ✕ 调
  `run.cancel`;completed 事件收尾刷新。top-bar 挂载(与 D1 同文件,独立 commit)。
- vitest:发起/忙碌禁用/取消按钮触发 runCancel/run_busy 错误透传。
- 验证:`npm --prefix desktop/ui-src run test; echo $?` → 0;
  `npm --prefix desktop/ui-src run build; echo $?` → 0(tsc 零错)。
- 审查门:与 feed-ux G4(dashboard 品类卡跑一次)不重复——本组件只在顶栏;
  run_busy 文案指向取消按钮。

### D3. C1:游标(分支步,依 design §11.3 第 4 条核对结果)

- 分支 A(feed-ux Python 半边已合入):entry.py/store 增量加 `before_id` 元组比较;
  分支 B(未合入):按 design §3 合流形状一次补齐 `query/before/before_id` 全套
  (list_items + `_m_store_items` 透传),commit message 注明「代 feed-ux implement
  步骤 1 之半,回标 feed-ux」。
- 两分支同做:protocol 测试加「同刻 first_seen 条目数 > 单页 limit」夹具,断言
  before/before_id 翻页推进**直至取尽**(sum(页) == 夹具总数);feed/api.ts 改造
  (cursor → (first_seen, id) 对,判停 hasMore = 返回数 < limit,去重/防御判停保留);
  client.ts storeItems 增可选参。
- vitest:fetchFeedPage 游标透传/空页判停/边界去重。
- 验证:`uv run --no-sync python -m pytest -q tests/test_desktop_sidecar_protocol.py; echo $?` → 0;
  vitest + build → 0。
- 审查门:分支判定依据(核对命令输出)记入任务日志;「取尽」断言必须是对总数,
  不是对「游标变化」。

### D4. C3:runs.list + 仪表盘历史化

- store:`list_runs`;entry.py:`_m_runs_list` + `_run_record_dict` 抽取
  (`_run_worker` 改用);dashboard/api.ts 三并发(doctor + runsList + runStatus),
  runSummary 改吃 runs.list、running 叠加内存活跃。
- 测试:protocol runs.list(种子 db 两条 run → 新→旧/limit/过滤);vitest:
  runSummary 聚合纯函数(runs.list 行 + active 叠加)。
- 验证:pytest 协议文件 + vitest + build,均 → 0。
- 审查门:重启 .app 后仪表盘仍显示历史 run 的验收走 D12 冒烟(杀 sidecar 重拉 +
  重启 .app 两形态);run.status 应答形状未变(向后兼容)。

### D5. B2:feedback 三方法 + 卡片反馈 + 仪表盘反馈卡

- models.py `FEEDBACK_CHANNEL_DESKTOP`;entry.py `_m_feedback_mark/list/stats`
  (直调 myia.feedback 同门,载荷键对齐 CLI);client.ts 三封装。
- UI:`feed-card-feedback.tsx`(👍/👎 → feedback.mark,已标置灰);
  `feedback-stats-card.tsx`(dashboard,feedback.stats 好/坏计数 + Top 负反馈类目)。
- 测试:protocol 三方法往返(mark 经 InMemory backend/种子 db → list 可见同条 →
  stats 计数;item_not_found 拒);vitest:卡片标记调用与置灰、stats 卡空态。
- 验证:pytest 协议文件 + vitest + build → 0。
- 审查门(=验收 B2):往返一致演示——桌面 mark 后
  `uv run --no-sync python -m myia.cli feedback list --db <冒烟 db>` 可见同一条目
  (channel=desktop);此演示入任务日志。

### D6. B4:采集量趋势

- store `daily_item_counts`;entry.py `_m_store_trend`;client.ts storeTrend;
  dashboard `fillDailyCounts/toSparklinePoints` 纯函数 + trend 卡(SVG polyline
  零新依赖);窗口切换 7/14/30 天。
- 测试:protocol store.trend(种子 db 已知逐日计数,含 days 钳制);**vitest 覆盖
  fillDailyCounts 聚合**(补零天/窗口裁剪/空态)——即验收 B4 的落点。
- 验证:pytest 协议文件 + vitest + build → 0。
- 审查门:UTC 逐日口径注记在应答/卡面(不伪称本地时区);无新 npm 依赖。

### D7. C5 + C10:secret.delete 与 app 版本透传

- entry.py `_m_secret_delete`(薄包装 delete_secret);main.rs spawn 注入
  `MYIA_APP_VERSION = package_info().version`;`_m_version` 增 app_version;
  top-bar tooltip 追加 app 版;settings 凭据行删除按钮(confirm → secret.delete →
  刷新 secret.list)。
- 测试:protocol secret.delete(InMemoryKeychainBackend 往返 + 删除后 list 不含);
  version.app_version(monkeypatch env 有/无两态);vitest:凭据行删除交互、tooltip 文案。
- 验证:pytest 协议文件 + vitest + build → 0;`cargo check …; echo $?` → 0。
- 审查门(=验收 C10):重打包后 version 应答 app_version 与 .app 的
  Info.plist/tauri.conf version 一致(D12 冒烟核对)。

### D8. C13:源管理「试抓此源」

- entry.py `_m_sources_test`(围栏 `_fence_yaml_path` + `test_busy` 单飞 + 工作线程
  `_self_command(["test", …, "--json"])` + `test.completed` 事件 + stderr 入环形);
  client.ts sourcesTest;sources-screen 行内「试抓」(spinner → 事件回显提取字段
  摘要或结构化错误)。
- 测试:protocol 用 127.0.0.1 本地 http.server 夹具(同文件既有惯例)真跑通
  exit 0;source 不存在 → CLI config 错透传;test_busy 单飞拒绝;vitest:行内状态机。
- 验证:pytest 协议文件 + vitest + build → 0。
- 审查门:同步实现禁令依据(design §8 的 120s=120s 证据)记入任务日志;子进程
  退出后无残留(popen.poll() 断言)。

### D9. B3 + C11:settings「评分与反馈」分区与 enrich 写回(依赖 yaml.save,已合入)

- settings/api.ts `saveCategoryNode(file, mutator)`(yaml.read → 改节 → yaml.save
  mtime 锁 → doctor 复核);settings-screen 新分区:逐品类 enrich.enabled toggle +
  budget_per_run 只读护栏 + model 可写(C11 半边);push 声明「去配置编辑屏」指引;
  pools 半边维持只展示 + **顺延回标 census**(理由:yaml-editor 待拍板 3 未定)。
- vitest:toggle → saveCategoryNode 调用形状、mtime_conflict 错误透传、doctor 复核回显。
- 验证:`npm --prefix desktop/ui-src run test; echo $?` → 0;build → 0。
- 审查门(=验收 B3):toggle 后重启 .app 状态保留(yaml 复核)走 D12 冒烟;
  「反馈开关 = enrich.enabled」的解释(design §6)在 PR 备注公示,主人有异议则
  回设计不硬上线。

### D10. C7 收口(口径修订:spec 注册表对账,非 client 全量一一对应——design §10)

- **口径前提**:并行 spec 线已钉死「封装面 ≠ 协议面」(.trellis/spec/desktop/
  sidecar-protocol.md 变更纪律第 3 条,client.ts 头注已被其改真)。本步**不做**
  sourcesWrite/yaml 六/image 六的共享封装补齐、**不迁移** sources/api.ts 私有通道
  ——PRD C7 验收「一一对应(含 sources.write)」按评审拍板方案①记录措辞变更:
  「头注如实 + 注册表对账」(D10 审查门记入任务日志 + PR 备注请主人过目)。
- 实作:sources/api.ts:8-13 头注重写(删「将得 method_not_found」,改述屏私有
  封装定位 + 指向 spec 注册表);client.ts 门面此时应已含核心 10 + 本批 8(D1-D8
  随用随加),头注措辞沿用 spec 线版本不覆盖,门面方法计数注记同步 10→18。
- 验证(对账手法 = spec 变更纪律第 2 条):serve 起后发未知方法名,`data.allowed`
  与 spec 注册表 31 行逐一比对,结果记任务日志;`grep -c` client.ts api 键 = 18;
  `grep -rn "method_not_found" desktop/ui-src/src/screens/sources/api.ts` 零失实
  自述残留;vitest + build → 0。
- 审查门:①对账记录(allowed ↔ 注册表 ↔ client 键)零漂;②验收措辞变更已经
  PR 备注公示;③若开工核对(design §11.3 第 6 条)发现 spec 政策改回全量一一
  对应,本步按方案②重写并回改三个屏私有 api.ts(代价已在 design §10 注明)。

### D11. 顺风车 E4/E5(余量步,可裁)

- E4:删 routes/ 五个 C 阶段占位骨架 + App.tsx 头注修正(B3 落地后骨架「反馈开关」
  参考位失效);E5:fetch_base.py:768 过期注释如实化。裁则回标 census 活清单。
- 验证:build → 0;pytest 全量 → 0(E5 触注释行)。

### D12. 协议版本合流 bump + CHANGELOG + 全量回归 + 重打包冒烟

- `grep -n "^PROTOCOL_VERSION" desktop/entry.py`:仍为 1 → bump 2 + CHANGELOG 记
  协议 v2 合流总账(yaml 六 + image 六 + feed-ux store.items 扩展/三方法(若已合入)
  + 本批全量,design §1.3 清单);已为 2 → 只补 CHANGELOG 方法行。
- **spec 注册表终审对账**:发未知方法名拿 `data.allowed`,与
  `.trellis/spec/desktop/sidecar-protocol.md` 注册表(应 31 行)逐一比对零漂
  (各 D 步已随同更新,此处终审;spec 变更纪律第 1/2 条)。
- 全量:`uv run --no-sync python -m pytest -q; echo $?` → 0(对照 §11.3 第 7 条
  基线,新增用例另计);`npm --prefix desktop/ui-src run test; echo $?` → 0;
  `npm --prefix desktop/ui-src run build; echo $?` → 0;
  `cargo check --manifest-path desktop/src-tauri/Cargo.toml; echo $?` → 0。
- 重打包冒烟矩阵(照 brand-trim 口径:tauri build → 备份重装 /Applications → 目视),
  逐条对应 PRD 验收并记录任务日志:
  1. C2:活动监视器杀 sidecar → 壳自动 respawn → 请求恢复(不再
     sidecar_not_running/terminated);连杀 5 次 → dead 态 → 顶栏「拉起」恢复。
     **onefile 双进程注记(design §2.1②):「杀 python 子进程」与「杀 bootloader」
     两形态各演示一次;后者遗留的孤儿 python 应经 stdin EOF 自清(entry.py:1958),
     滞留则如实记录入任务日志,不带病验收。**
  2. C2/C12:跑一次进行中 → 顶栏 ✕ 取消 → run.status 呈 cancelled;**活动监视器
     核 bootloader 与 python 两个 myia 进程均无残留**(onefile 双进程,design §2.2;
     dev 测试只见单进程,此处才是该验收的最终证据)。
  3. C3:重启 .app → 仪表盘历史 run 仍在。
  4. B2:卡片 👍 → CLI `feedback list` 见同条;仪表盘反馈卡 stats 可见。
  5. B3:settings 切换评分开关 → 重启 .app 保留 → doctor/yaml 复核一致。
  6. B4:仪表盘趋势卡渲染且随窗口切换。
  7. C5:删除凭据 → secret.list 不再列出。
  8. C10:version 应答 app_version == .app 版本。
  9. C13:源管理行「试抓」→ 回显结果。
  10. C7:对账记录附卷(D10 产物:`data.allowed` ↔ spec 注册表 31 行 ↔ client
      门面 10+8;验收措辞 = 「头注如实 + 注册表对账」,方案①)。
- 回标:census 归档活清单回写——消号 11 项(C1/C2/C3/C5/C7/C10/C12/C13/B2/B3/B4);
  顺延项 C11 pools 半边(理由:yaml-editor 待拍板 3 未定);**裁项 C4/C6(维持
  登记不实现,理由引 PRD 需求表 prd.md:48-49:C4 环形设计部分刻意、余量再议;
  C6 随 v1.2 市场 UI 拍板)**;E4/E5 做或裁(裁则理由);PRD C7 验收措辞变更记录
  (D10 方案①)。

### D12 执行记录(2026-10-03 v112 首切片:C1/C2/C3/C5/C7/C13;B2/B3/B4/C10/C12 未施工)

- 版本 bump:开工核得 `PROTOCOL_VERSION` 已为 2(messaging 批合流 bump,adee17d),
  按 design §1.3 **不二次 bump**,CHANGELOG 在 v2 台账补记本批方法行(已落)。
- 终审对账(D10):实测发未知方法名,`data.allowed` 与 spec 注册表 27 行
  逐一相等(Python 断言 `allowed == sorted(spec_registry) → True, count=27`;
  门面 `grep -c` client.ts api 键 = 14 = 核心 10 + 本批 4)——零漂。
- 全量回归(2026-10-03 实跑,显式退出码):pytest 全量 `1946 passed, 14 skipped`
  exit 0;协议文件 74 passed exit 0;vitest `13 files / 123 tests` exit 0;
  `tsc -b && vite build` exit 0;`cargo check` exit 0;`cargo test --bin
  myia-desktop` 2 passed exit 0;`npm --prefix desktop run tauri build` exit 0
  (产物世事.app + dmg)。
- **打包面回归治本(2026-10-03 复查发现)**:myia-core.spec 的 SPECPATH 相对化
  (c97c897)会被 build-sidecar.sh 的 CLI 重生成每跑一次冲回本机绝对路径
  (tauri build → beforeBuildCommand 链路,两次落地两次被冲实锤)。已改
  build-sidecar.sh:手维 spec 存在时直接以其为源构建(与 CLI 旗标集等价,
  collect_all×3/add-data/hidden-import 齐核),CLI 重生成仅作首跑 bootstrap;
  复跑 tauri build exit 0 且 spec 保持与 HEAD 一致(git diff --quiet = 0)。
- **冒烟矩阵执行状态(如实)**:
  - **已无头执行(装包产物 myia-core 直驱,不启 .app;脚本与日志见
    /tmp/v112_smoke.py、/tmp/v112_smoke.log,SMOKE_RESULT=PASS)**:
    - 第 2 条(C2 取消)协议+进程级:打包 sidecar serve → run.start(90s 慢源)
      → `run.cancel` 应答 `{cancelled:true}` → completed `exit_code=-15
      status=cancelled` → run 子进程组(pgid 实测)无残留。
    - 第 3 条(C3)协议级:真实 run(success,run_id=1)→ serve 干净退出(EOF=0)
      → **新起 serve 进程**(等价 sidecar 重启)`runs.list` 仍回 `run_id=1
      status=success`——重启后历史可达的协议半边闭环。
    - 进程拓扑实证(探针 /tmp/v112_probe*.log):冻结 serve 派生的 run 子进程
      **单进程**(bootloader 继承 `_MEIPASS2` 不再 fork 孙进程);非冻结父派生
      才呈双进程(bootloader+python 孙,同 pgid)——两种形态 killpg 均一锅端
      (双进程形态探针直证:SIGTERM 组杀后两成员齐消)。
  - **未执行,留主人 GUI 目视**(静默纪律,代理不启 .app/不抢前台):
    第 1 条壳层 respawn 目视(杀 sidecar → 徽标 respawning → 自动恢复;连杀
    5 次 → dead → 顶栏「拉起」;Rust 侧逻辑有 cargo test 退避纯函数 + 无头
    serve 往返证据,壳 GUI 半边未目视);第 3 条 .app 整体重启目视;第
    7(C5)/9(C13)条 UI 点击目视(协议往返已有 pytest/vitest 证据);第
    4/5/6/8 条属未施工项(B2/B3/B4/C10),不在本切片。
- **冒烟中发现的既有边界(如实记录,非本批引入,未在本批修)**:serve EOF
  干净退出时,进行中的 run 子进程会孤儿化(reparent 到 pid 1 继续采集,探针
  /tmp/v112_probe2.log 实证)。与 design §2.1① 记的 SIGKILL 孤儿边相邻但属
  正常退出路径;是否随 serve 退出一并终止 run,语义需主人拍板,本批不动。
- C7 验收措辞变更:按 design §10 方案① 落档至 prd.md 验收行(头注如实 +
  注册表对账;不补 sources.write 共享封装,spec 变更纪律第 3 条政策不推翻)。

## 回滚点

- D1–D11 每步独立 commit,任一步可单 revert(design §11.4:协议方法增量注册、
  数据零迁移、UI 各屏独立);D12 的 bump/CHANGELOG 独立 commit 可单退。
- 冒烟失败定位原则:先 revert 最近一步重跑矩阵,不带着红矩阵继续。

## 风险与守门

- **两批并行碰撞**(feed-ux):top-bar/feed api/dashboard/settings/client 五处行级
  交叠,先合者为准;本批让位原则 = C12 进顶栏、B2 统计进仪表盘(design §11.2)。
- **respawn 竞态**:自动退避与手动拉起必须锁内复核 child 状态,防双 spawn(D1 门)。
- **run.cancel 语义**:只认活跃 run,终态 run 拒绝(`run_not_active`);信号终局才
  标 cancelled,正常退出不被误标;进程组杀(start_new_session + killpg),孙进程
  残留验收以 D12 冒烟双进程核对为准,dev 测试绿不冒充。
- **spec 政策拍板前置**:C7/D10 口径依赖 `.trellis/spec/desktop/sidecar-protocol.md`
  变更纪律现值(design §10/§11.3 第 6 条)——政策未定稿或再变,回设计重开,
  不按过期口径施工。
- **C13 阻塞风险**:试抓一律异步 job;同步实现 = serve 循环卡死,审查门直接拒。
- **B3 解释风险**:「反馈开关 = enrich.enabled + budget 护栏」是 schema 实况下的
  落地解释,PR 公示有异议即回设计;不做无语义假开关。
- **版本 bump 所有权**:只看开工核对时现值,不抢跑不重复 bump(design §1.3)。
