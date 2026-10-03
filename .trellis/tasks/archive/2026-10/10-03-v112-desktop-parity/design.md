# 技术设计:v1.1.2 桌面对齐批次(B2–B4 + C 组协议/UX 缺口)

> 事实底座(2026-10-03 本会话实查;首稿 HEAD=`dee8e24`,评审修订时实况 HEAD=`b4e2787`
> ——**多会话并行,行号以 grep 判活为准,下引行号为首稿实读值、漂移不纠值只纠事实**):
> - `desktop/entry.py`(首稿 1934 行,现势 PROTOCOL_VERSION :183 = 1、_HANDLERS
>   :1875-1899):`PROTOCOL_VERSION = 1`;`_HANDLERS` 现有
>   23 方法——**yaml-editor 的 yaml.* 六方法与 image-input 的 image.* 六方法已在
>   `fbba437` 合入 main**(git log -S 实证),但协议版本仍未 bump(「合流时统一 +1」
>   的欠账,本批收口,见 §1.2)。无 runs.list / run.cancel / secret.delete /
>   feedback.* / sources.test / store.trend。首稿后 `2b54865`/`b4e2787` 又改过
>   entry.py(image.status 增 last 对账、image.import stat 预检)——与本批方法面
>   零重叠,行号整体后移。
> - `desktop/src-tauri/src/main.rs`(174 行):`CommandEvent::Terminated` 只置
>   `child=None` 并向 pending 回 `sidecar_terminated`(main.rs:109-118),**零 respawn**;
>   请求超时 120s(main.rs:22);spawn 逻辑内联在 setup(main.rs:158-162);
>   `MYIA_HOME` 注入已有,`MYIA_APP_VERSION` 无。tauri.conf.json `version: "1.1.1"`。
> - **协议文档已立档(评审修订时实况)**:并行会话落了未提交的
>   `.trellis/spec/desktop/sidecar-protocol.md`——23 方法注册表(单一事实源 =
>   `_HANDLERS`,method_not_found 的 `data.allowed` 机器对账)+ 错误码表 +
>   变更纪律三条:①新增方法注册表随同更新;②对账手法;③**封装面 ≠ 协议面:
>   client.ts api 门面只盖核心 10 方法,sources.write/yaml.*/image.* 屏私有封装**。
>   本批的 C7/D10 口径与 spec 注册表更新义务据此重定(§10、§11.1)。
> - 工作区在途改动**以开工时 `git status` 实况为准,不点名具体线**(首稿时是
>   push/messaging 线且已于 `2b54865` 提交;评审修订时是 image 屏测试 + client.ts
>   头注改写 + 新 spec 目录 + feishu 线——多会话滚动,点名必过期)。原 PRD
>   「yaml-editor 会话未提交改动(dashboard 文案 + 协议测试增补)」描述亦已过期
>   (该部分随 `fbba437` 落盘)。开工前核对清单见 §11.3。
> - store:`list_items(category/since/limit)`(sqlite.py:661,无 before/query);
>   `list_feedback(verdict/channel/since/limit)`(sqlite.py:991)已存在;
>   runs 节有 `start_run/finish_run/get_run/previous_run/latest_run`(sqlite.py:1207-1334),
>   **无 list_runs**;`daily 聚合`无现成方法。
> - feedback:`record_feedback(store, verdict, channel, item)` channel 为自由字符串
>   无词表校验(feedback/record.py:83-133)——`channel="desktop"` 可直接落库,
>   CLI `myia feedback list` 不带过滤即可见(往返一致验收成立);常数家在
>   store/models.py:28-30(`FEEDBACK_CHANNEL_CLI/TELEGRAM/FEISHU`)。
> - secrets:`delete_secret` 已存在(secrets.py:281);CLI `myia secret delete` 已有
>   (cli.py:375 区)。
> - 试抓:CLI `myia test <yaml> --source <name> --json` 已有(cli.py:1232 `_cmd_test`),
>   **每源超时缺省 120s**(cli.py:179 `DEFAULT_TEST_TIMEOUT_SECONDS = 120.0`)——恰等于
>   壳层单请求 120s 硬超时,同步实现必撞墙,C13 只能做异步 job(§8)。
> - UI:client.ts `api` 门面 **10 方法**(实数,client.ts:113-134 键列),头注已被
>   并行 spec 线改写为「只封装核心 10 方法,非协议全量」并与 spec 注册表互指
>   (git diff 实读);types.ts `SidecarProtocol` 类型映射盖 16(核心 + image.*,
>   spec 变更纪律第 2 条注记)。**仍失实的是 sources/api.ts 头注**:「将得
>   method_not_found」(sources/api.ts:8-13,方法早已收编)——C7 剩余半边即此(§10);
>   feed/api.ts 游标 = since 复用 + 客户端去重 + added==0 判停(feed/api.ts:7-11 自注);
>   dashboard 近期 run 卡只吃 `run.status` 内存注册表(dashboard/api.ts:31-33);
>   settings 三表单 + doctor 回显,enrich.model 明注「不伪造保存成功」
>   (settings/api.ts:16-18);ui-src 全局 grep feedback 零命中(本会话复跑确认)。
> - schema:`EnrichConfig.enabled: bool = False` + `budget_per_run`(schema.py:754-770)
>   ——品类 YAML 里唯一带预算护栏的开关节;**品类 schema 无任何 feedback 节**
>   (grep 零命中),反馈闭环的 tuning 无 YAML 落点(自动调参,runs 旁路)。
> - feed-ux 设计已定形(feed-ux/design.md §1):store.items 增 `query`+`before`(严格
>   小于),其 implement.md 步骤 1 认领 Python 半边与协议测试,末节明言「v112-batch
>   侧不再重复改 store.items——两批开工顺序若反转,以先合者为准」。

## 1. 协议扩展总表(三线合流;方法名/参数/返回/错误形状)

### 1.1 本批新增/扩展(v112 线;错误形状一律 `{code, path, message, data?}`,与现有 ProtocolError 同构)

| 方法 | 参数 | 返回 | 错误(新 code) | 缺口 |
|---|---|---|---|---|
| `run.cancel` | `{run_id?: int}`(缺省=当前活跃 run) | `{run_id, cancelled: true, state}` | `run_not_found`(未知 id)/ `run_not_active`(已终态,data 带 state) | C2/C12 |
| `runs.list` | `{category?: string, limit?: int=50}`(limit 钳制 [1,200]) | `{runs: [RunRecord 字典]}` 新→旧,直读 SQLite | `store_corrupt` 透传 / `invalid_params` | C3 |
| `secret.delete` | `{name: string}` | `{name, deleted: true}` | `invalid_secret_name` / secrets 层 code 透传 | C5 |
| `feedback.mark` | `{item: string\|int, verdict: "good"\|"bad", db?}` | `{feedback_id, item_id, dedup_key, verdict, channel:"desktop"}` | `item_not_found` / `feedback`(校验) | B2 |
| `feedback.list` | `{verdict?, channel?, limit?=50, db?}` | `{count, items: [FeedbackRow]}`(键同 cli.py `_feedback_row_dict`:id/item_id/dedup_key/verdict/channel/title/category/created_at) | `invalid_params` / `store_corrupt` | B2 |
| `feedback.stats` | `{window_days?=14, top?=5, db?}` | `{window_days, stats, active_tuning, tuning_history}`(键同 CLI stats 载荷,cli.py:2085-2110) | `feedback` / `store_corrupt` | B2 |
| `sources.test` | `{file: string, source?: string, timeout?: float≤120, config?}` | `{job_id, state:"running", source?}`;结果走事件 `test.completed {job_id, ok, exit_code, result?\|error?, ts}`;子进程 stderr 进环形缓冲(run_id=null,logs.tail 可见) | `invalid_params` / `source_file_unreadable`(装不上品类)/ `test_busy`(单飞) | C13 |
| `store.trend` | `{days?: int=14, category?, db?}`(days 钳制 [1,90]) | `{days: [{date: "YYYY-MM-DD", count: int}]}` 旧→新,UTC 逐日 | `invalid_params` / `store_corrupt` | B4 |
| `version`(扩展) | 不变 | 增 `app_version: string\|null`(env `MYIA_APP_VERSION` 缺省 null,dev/CLI 场景如实) | 不变 | C10 |
| `store.items`(扩展,本批半边=见 §3 分工) | 增 `before_id?: int`(与 `before` 组成复合游标) | 不变 | `invalid_params` | C1 |

事件面新增:`test.completed`(C13)与壳层 `sidecar://state`(C2,壳 emit 非 sidecar 协议,见 §2)。

**注册表同步义务(spec 变更纪律第 1 条)**:上表每行落地的方法,同 commit 更新
`.trellis/spec/desktop/sidecar-protocol.md` 注册表(23→31)+ store.items/version
行注记 + 错误码表新 code;D12 以未知方法名的 `data.allowed` 对账(纪律第 2 条)。

### 1.2 合流参考行(非本批实现,同表对齐防止三线漂移)

| 线 | 方法/参数 | 状态 |
|---|---|---|
| yaml-editor | `yaml.list/read/validate/template/save/delete` | **已合入 HEAD**(`fbba437`;entry.py:801-1210)——B3/C11 的前置已满足 |
| feed-ux G1/G3 | `store.items` 增 `query`(三列 LIKE NOCASE)+ `before`(first_seen 严格小于);`feed.export {format,path,category?,query?}`;`push.test {channel}`;`schedule.preview {file,count?}` | 在途(planning,排 tag 后并行);其 implement 步骤 1 认领 Python 半边 + 协议测试 |
| 本批 × feed-ux 合流点 | C1 游标形状 = feed-ux 钉死的 `before` **再加本批补充的 `before_id`**(§3:严格 `before` 单键无法满足「翻页直至取尽」验收,同刻超限条目会被跳过;复合游标向后兼容——只传 before = feed-ux 原形状) | 本设计定形,feed-ux 侧无需改设计,UI 可选多传一参 |

### 1.3 PROTOCOL_VERSION 合流 bump 政策(全批统一 +1 一次,不各加各的)

- 现值 1(HEAD);yaml.*/image.* 六方法各自落库时均未 bump(按「谁后合谁 bump」欠着)。
- 本批**收尾步**(implement D12)执行:`PROTOCOL_VERSION == 1` → bump 至 2,CHANGELOG
  记一次协议 v2 的**合流方法总账**:yaml.* 六(yaml-editor)+ image.* 六(image-input)+
  store.items query/before/before_id + feed.export/push.test/schedule.preview(feed-ux,
  若已合入)+ 本批 §1.1 全部方法。若开工时已被别的线 bump 至 2(核对清单 §11.3 第 5 条),
  本批**不二次 bump**,只在 CHANGELOG 补记本批方法行。
- 判活命令:`grep -n "^PROTOCOL_VERSION" desktop/entry.py`。

## 2. C2 respawn 设计(壳层指数退避 + reprobe 升级 + run.cancel 联动)

### 2.1 壳层状态机(main.rs)

- 抽 `fn spawn_sidecar(app: &AppHandle) -> Result<CommandChild, Box<dyn Error>>`:把
  setup 里的 `sidecar("myia").args(["serve"])` + `MYIA_HOME` 注入(main.rs:158-162)提为
  独立函数,setup / 自动 respawn / 手动拉起三处共用;spawn 后照旧 `pump_task`。
- `Sidecar` 增 `respawn_attempts: Mutex<u32>`;常量 `RESPAWN_MAX_ATTEMPTS = 5`、
  `RESPAWN_BASE_DELAY = 1s`(退避序列 1/2/4/8/16s)、`RESPAWN_STABLE_AFTER = 10s`。
- `Terminated` 处理(main.rs:109-118 改造):置 child=None + drain pending(现状保留)
  → `attempts += 1` → 若 `attempts <= MAX`:emit `sidecar://state {state:"respawning",
  attempt}` 并起异步任务 `sleep(backoff_delay(attempts))` → 锁内复核 child 仍 None
  (防与手动拉起双 spawn)→ `spawn_sidecar` → 成功后起稳定计时任务(sleep 10s 后进程
  仍存活则 `attempts=0` 归零;又死则自然走下一轮更长退避)。若 `attempts > MAX`:
  emit `sidecar://state {state:"dead", attempt}` 停止自动,转手动。
- 新壳命令 `#[tauri::command] sidecar_restart`:锁内 child 为 None(或已 dead)时
  `spawn_sidecar` + `attempts=0` + emit `state:"online"`;进程健在时直接返回
  `{restarted: false}`(幂等,不杀活进程)。注册进 `generate_handler!`。
- **reprobe 升级为「探测+拉起」**(use-sidecar-status.ts):reprobe = `api.version()`
  探测(沿用)→ offline 时 `invoke("sidecar_restart")` → 再探测一次;并订阅壳事件
  `sidecar://state`(listen 经 core:default,无新权限)把 `respawning/dead` 如实入
  状态机——dead 态顶栏按钮文案改「拉起 sidecar」。`connecting/online/offline` 语义
  不变,新增 `respawning`/`dead` 两态(hook 的 SidecarStatus 联合类型扩两值)。
- 已知边界(如实注记,不扩范围):①sidecar 被 SIGKILL 时其 run 子进程成孤儿继续写库
  (SQLite 并发安全,不损数据);respawn 后 `_RUNS` 内存注册表为空,UI 依赖
  runs.list(C3)补历史,进行中 run 的 completed 事件不再到来(UI 状态机自有超时兜底)。
  ②**sidecar 自身也是 onefile 双进程**(Tauri CommandChild = bootloader,另有同名
  python 子进程,§2.2 同证):杀 python 子进程 → bootloader 随之退出 → Terminated →
  respawn,干净;杀 bootloader → python 子孤儿,靠 stdin EOF 自清(serve 循环
  EOF = 退出 0,entry.py:1958;前提 = 壳在 Terminated 置 `child=None` 时丢弃
  CommandChild 关闭管道——现行 main.rs 的 `*guard = None` 即触发 drop)。若孤儿
  滞留属 respawn 机制缺陷,D12 冒烟如实记录入任务日志,不带病验收。

### 2.2 run.cancel(entry.py;进程组杀——onefile 双进程实证)

- **为什么必须进程组杀**:myia.spec 为 onefile(`a.binaries` + `runtime_tmpdir=None`
  + 无 COLLECT,desktop/myia.spec:26,35 实读)——冻结模式 `_self_command` 返回
  `[sys.executable, ...]`(entry.py:1246),run「子进程」实为 **bootloader + 真实
  python 采集孙进程两个进程**。`proc.terminate()`/`proc.kill()` 只能命中 bootloader:
  SIGTERM 会被 bootloader 转发孙进程(可靠),但 SIGKILL 兜底只杀 bootloader,
  孙进程成孤儿继续采集——验收「run 子进程不残留」(prd.md:59)在兜底路径必翻车。
- 新增 `_RUN_PROCS: dict[int, subprocess.Popen]`(`_RUNS` 条目不存 proc,防
  run.status 把 Popen 对象带进应答);`_run_worker` 的 `Popen` 加
  **`start_new_session=True`**(run 自成进程组;dev 单进程形态行为不变)，
  起后登记、`finally` 摘除。
- `_m_run_cancel`:锁内定位活跃 run(state!="running" → `run_not_active`,带 state);
  **`os.killpg(os.getpgid(proc.pid), SIGTERM)`**(组内 bootloader+孙进程一锅端,
  bootloader 转发路径不受影响)→ 条目标 `cancel_requested=True` →
  `threading.Timer(5, os.killpg(pgid, SIGKILL))` 兜底(进程不存在时
  `ProcessLookupError` 吞掉)→ 立即返回,不阻塞 serve 循环。
- `_run_worker` 收尾:`exit_code = proc.wait()` 后若 `cancel_requested and exit_code
  is not None and exit_code < 0`(信号终局)→ `status = "cancelled"`(可辨认终态,
  STATUS_BY_EXIT 不动);completed 事件照常带 `status:"cancelled"`。
- **测试边界如实注记**:D1 协议测试在 dev 模式跑(`python -m myia.cli`,无
  bootloader,单进程),`proc.poll() is not None` 断言只见单进程——**孙进程不残留
  的最终证据在 D12 冒烟**(活动监视器核 bootloader 与 python 两个 myia 进程均无
  残留),dev 测试绿不等于该验收过。
- C12 联动:全局「跑一次」放**顶栏**(不放仪表盘——feed-ux G4 已认领 dashboard
  CategoryCard 的逐品类「跑一次」,两批避撞,见 §11.2):顶栏右侧
  `[▶ 跑一次(品类)] [● 采集中 ✕]` 组件;品类来源 = feed-ux C8 提升的顶栏品类选择态
  (未合入则本批就地提升该状态到 AppLayout,C8 合入时复用);running 态的 ✕ 调
  `run.cancel`。新组件独立文件 `components/layout/global-run.tsx`,不搅 feed-ux 的
  C8 Select 接线区。

## 3. C1 游标设计(与 feed-ux G1/G3 合参;含分工)

- **形状**(合流定形):`before`(ISO-8601,first_seen 严格小于,feed-ux 原钉)+
  `before_id`(int,可选;`(first_seen < before) OR (first_seen = before AND id <
  before_id)` 元组比较)。只传 before = feed-ux 原语义不变;两参同传 = 同刻条目超
  单页 limit 也能**推进直至取尽**(PRD 验收 C1 的硬要求——严格 before 单键会把同刻
  超限的更旧条目整批跳过,过不了「取尽」验收;升级点在本批,feed-ux 零改动)。
- **分工**(以 feed-ux implement.md 末节共识为准):Python 半边
  (list_items 增参 + `_m_store_items` 透传 + query/before 协议测试)归 feed-ux;
  本批补:(a) `before_id` 参数与元组 SQL(若本批开工时 feed-ux 已合入,则在其上
  增量加 before_id;未合入则本批按合流形状一次性补齐 query/before/before_id 全套并
  回标 feed-ux——核对清单 §11.3 第 4 条定分支);(b) 同刻夹具协议级测试
  「> limit 同刻条目,翻页推进直至取尽」进 tests/test_desktop_sidecar_protocol.py;
  (c) feed/api.ts 翻页改造(§3.1)。
- **§3.1 feed/api.ts**:`FeedPageRequest` 增 `cursorId: number | null`;
  `fetchFeedPage` 改传 `before/before_id`(替换 since 复用);`nextCursor` 取本页最旧
  条目的 `(first_seen, id)` 对;判停 `hasMore = items.length >= pageSize` 不变
  (即「返回数 < limit」),`appendFeedPage` 客户端去重与 added==0 防御判停保留。
  feed-ux 的 `query` 透传在同一函数上追加,两批改动行相邻、rebase 成本可控(§11.2)。

## 4. C3 runs.list(历史 run 桌面可达)

- store(sqlite.py runs 节内):`list_runs(*, category=None, limit=50)` →
  `SELECT * FROM runs [WHERE category=?] ORDER BY id DESC LIMIT ?`,复用
  `_row_to_run`;limit 负数 ValueError(照 list_feedback 口径)。
- entry.py:`_m_runs_list` 直读表,应答 `runs[]` 每项 = `_run_record_dict(record)` ——
  新抽小助手(键:run_id/category/status/started_at/finished_at/stats/steps/error),
  `_run_worker` 现拼的 record 字典(entry.py:1299-1308)改走同一助手,防两处漂移。
- 仪表盘重构线:dashboard/api.ts `loadDashboardData` 并发 `doctor + runsList({limit:20})
  + runStatus()`;`runSummary` 改吃 runs.list(重启 .app 后历史仍在),`running` 计数
  叠加 run.status 的内存活跃条目;`RecentRunRow` 数据形状对齐 RunRecord 字典。
  内存注册表(`run.status`)降级为「活跃 run + 当次会话快速查询」。

## 5. B2 feedback.mark/list/stats(桌面反馈入口)

- 协议三方法见 §1.1。实现门:**同门直调**(与 CLI 同一函数),不经 `_cli_json`
  子进程包装——`record_feedback`/`resolve_item_ref`/`FeedbackTuner`/`TuningPolicy`/
  `load_active_tuning` 全部自 `myia.feedback` 直入(channel 自由串,已核);
  `feedback.list` 直调 `store.list_feedback`(PRD「读侧直读 sqlite」);stats 载荷键
  逐一对齐 CLI(cli.py:2101-2110),CLI/桌面同形。
- channel:store/models.py 增 `FEEDBACK_CHANNEL_DESKTOP = "desktop"`(与三个既有
  常数同家);CLI `myia feedback list` 无过滤可见 desktop 行 → 往返一致验收成立。
- UI:feed 卡片 👍/👎 = 新独立组件 `screens/feed/feed-card-feedback.tsx`(调
  `feedback.mark`,item 传 dedup_key;重复标记幂等——record_feedback 同 (item,verdict)
  重复入库为多行?否——幂等键是 (channel, external_id),手动标记无 external_id,
  故 UI 侧已标即置灰防重);**反馈统计入口放仪表盘**新卡(好/坏计数 + 负反馈 Top
  类目摘要,数据 `feedback.stats`):feed 屏是 feed-ux 重改动区(G1/G2/G3),统计卡
  放 dashboard 把 B2 的 UI 足迹压到「feed 卡片一个小组件 + dashboard 一张卡」,
  且满足验收「反馈 stats 桌面可见」。新组件 `screens/dashboard/feedback-stats-card.tsx`。

## 6. B3/C11:反馈开关分区与 settings 写回(前置已满足 + 顺延条件)

- **前置事实**:yaml.save 六方法已在 HEAD(`fbba437`),PRD 所述「以彼档 yaml.save
  合入为前置」**在代码层已满足**;开工前只需核对 yaml-editor 任务已收口或其剩余
  范围不再碰 entry.py/_HANDLERS(§11.3 第 2 条)。若彼档终审又改协议形状,以彼档
  design.md §1 为准回来同步本设计。
- **「反馈开关」的落点拍板**(schema 实况逼出的解释,需在审查门公示):品类 schema
  无 feedback 节、tuning 无 YAML 落点(§0);唯一真实存在、带预算护栏、可经
  yaml.save 写品类节的开关 = **`enrich.enabled` + `enrich.budget_per_run`**
  (schema.py:754-770,v11 settings 承诺「反馈开关」的引擎侧对应物 = LLM 精评/
  评分回路的开关 + 预算护栏)。B3 分区 = settings 新增「评分与反馈」:逐品类
  (doctor 回显的 enrichSections 现值)toggle `enrich.enabled` + 只读展示
  `budget_per_run`(护栏明示);不做假开关。
- **写回通路(C11 与 B3 合一)**:切换/保存 = `yaml.read {file}` → 改
  `enrich.enabled`(或 `enrich.model`,C11 半边)→ `yaml.save {file, content,
  expected_mtime}`(注释保真靠原文读写)→ `doctor({yamls:[file]})` 复核回显。
  settings/api.ts 增 `saveCategoryNode(file, mutator)` 小助手(读→改→写→复核四步)。
- **顺延与划出(如实)**:pools 全局配置写回 = yaml-editor 待拍板 3 未定 → **顺延**
  并回标 census(界面维持现「只展示 + 指引」);push 通道声明写回 = 品类 YAML push
  节的列表编辑,配置编辑屏(第六屏)已提供全文件编辑通道,settings 内不重复造
  表单——只加「去配置编辑屏改 push 声明」指引链接;LLM key/base_url 仍走钥匙链
  (铁律不动)。

## 7. B4 趋势数据聚合口径(SQLite 逐日计数)

- 协议侧聚合(PRD「自 SQLite 聚合」):store 增 `daily_item_counts(*, days=14,
  category=None)` → `SELECT substr(first_seen,1,10) AS day, COUNT(*) FROM items
  WHERE first_seen IS NOT NULL [AND category=?] AND first_seen >= ? GROUP BY day
  ORDER BY day ASC`(入参 since = now-days 的 UTC ISO;**口径 = UTC 逐日**,如实
  注记,不做时区换算)。`store.trend` 薄包装(§1.1)。
- UI:dashboard 新卡「采集量趋势」= sparkline(手写 SVG polyline,零新依赖);
  纯函数 `fillDailyCounts(rows, days)`(补零天 + 窗口对齐)+ `toSparklinePoints`
  放 `screens/dashboard/api.ts`,**vitest 覆盖该聚合纯函数**(补零/边界/空态)——
  即验收「vitest 覆盖其数据聚合」的落点。
- 「历史窗口依赖 C3」:趋势卡头部提供窗口切换(7/14/30 天),超窗口的历史 run
  语境经 runs.list(C3)可查;趋势本体只依赖 items 表。

## 8. C13 试抓(异步 job;同步不可行的证据)

- 每源试抓缺省 120s(cli.py:179)= 壳层单请求硬超时(main.rs:22),同步必撞
  sidecar_timeout;且 serve 循环单线程,同步会把整个桌面后端卡死至抓完。故照
  `image.analyze` 先例(entry.py:1730-1797)做第三例异步 job:`_TEST_LOCK` +
  `_TEST_ACTIVE_JOB` 单飞(`test_busy`)→ 工作线程 `_self_command(["test", file,
  "--source", s, "--json", ...])` 子进程 → stderr 逐行入环形缓冲(run_id=null)→
  `proc.wait()` 后取 stdout 末份 JSON(`_last_json`)→ emit `test.completed
  {job_id, ok, exit_code, result|error}`。
- `file` 过 `_fence_yaml_path` 围栏(与 yaml.* 同一道门,试抓不得成为任意文件读
  原语);`source` 缺省=全部源(传 CLI 原语义),UI 只用单源形态。
- UI:sources 表格行增「试抓」动作(行内 spinner → completed 事件回显
  提取字段/指纹摘要或结构化错误);结果只驻屏内状态,v1 不做跨屏留存(如实注记)。

## 9. C5 secret.delete / C10 app 版本透传

- **C5**:`_m_secret_delete` 薄包装 `myia.secrets.delete_secret`(与
  `_m_secret_set` 同款错误透传);settings 凭据清单行加删除按钮(confirm 文案:
  名字 + 「删除后引用该凭据的源将采集失败」;凭据只有名字无值,无回显问题);
  删除后刷新 `secret.list` 回显(验收:不再列出)。
- **C10**:main.rs spawn 时注入 `MYIA_APP_VERSION =
  app.package_info().version.to_string()`(单一事实源 = tauri.conf.json 的
  `version`,不另维护常量,与 .app 版本天然一致);entry.py `_m_version` 增
  `app_version`(env 缺省 null);top-bar 在线态 tooltip 追加
  `· app v{app_version}`(排障三件套:sidecar 版/协议版/app 版)。

## 10. C7 收口(口径修订:与 spec 注册表对账,替代「client 全量一一对应」)

> 修订缘由:本设计首稿落盘 4 分钟后,并行会话落了
> `.trellis/spec/desktop/sidecar-protocol.md`,其变更纪律第 3 条钉死相反政策
> (「封装面 ≠ 协议面:client.ts api 门面只盖核心 10 方法;sources.write/yaml.*/
> image.* 屏私有封装」),并已改写 client.ts 头注为「只封装核心 10 方法,非协议
> 全量」(git diff 实读)。PRD C7 验收原措辞「client.ts 方法集与 _HANDLERS 一一
> 对应(含 sources.write)」(prd.md:65)与该 spec 互斥。拍板取**方案①(推荐,
> 改动小、不撞在途线)**:共享门面 = 核心 10 + 本批新方法,屏私有封装保留,验收
> 措辞在 D10 审查门记录变更为「头注如实 + 注册表对账」。若主人拍板②(坚持全量
> 一一对应),须先推翻 spec 第 3 条并回改三个屏私有 api.ts(撞在途 yaml-editor/
> image 线)——不推荐;②入选时本节回设计重开。

- **封装落点**:本批新增 8 方法封装一律入共享 client.ts(`runCancel/runsList/
  secretDelete/feedbackMark/feedbackList/feedbackStats/sourcesTest/storeTrend`),
  `storeItems` 可选参与 `version.app_version` 类型扩展进 types.ts `SidecarProtocol`;
  spec 第 3 条的屏私名单(sources.write/yaml.*/image.*)**不扩、不迁移**——新方法
  全入共享门面,使「spec 注册表新增行 ↔ 门面新增行」同源对账,封装政策不长出
  第二套例外。
- **头注如实化(只剩一处)**:sources/api.ts:8-13 删「将得 method_not_found」段,
  改述为「sources.write 已收编,本模块屏私有封装(spec 变更纪律第 3 条),契约见
  spec 注册表」;其私有 invoke 通道**保留不迁移**。client.ts 头注已被 spec 线改真,
  本批只随新方法自然扩门面,不覆盖其措辞(避免与并行线行级撞车)。
- **核对命令(验收门,D10 执行)**:发未知方法名拿 `data.allowed`(spec 变更纪律
  第 2 条对账手法)与 spec 注册表行集比对(23+8=31);`grep` client.ts api 键数
  = 10+8;结果记任务日志。

## 11. 文件级改动地图 / 避撞顺序 / 开工前核对 / 回滚点

### 11.1 改动地图(文件 → 函数/区段)

| 文件 | 改动 |
|---|---|
| desktop/src-tauri/src/main.rs | 抽 `spawn_sidecar`;`Sidecar` 增 respawn 字段;`Terminated` 分支改造(退避+状态事件);新命令 `sidecar_restart`;setup 注入 `MYIA_APP_VERSION`;`#[cfg(test)]` 退避纯函数测试 |
| desktop/entry.py | 模块头方法表/参数段更新;`_m_version` 增 app_version;`_RUN_PROCS` + `_run_worker`(Popen `start_new_session=True`)登记/摘除/cancelled 终态;`_m_run_cancel`(killpg SIGTERM→Timer(5) killpg SIGKILL,§2.2);`_m_runs_list` + `_run_record_dict`;`_m_secret_delete`;`_m_feedback_mark/list/stats`(+ import myia.feedback、FEEDBACK_CHANNEL_DESKTOP);`_m_sources_test` + `_TEST_*` 单飞/worker;`_m_store_trend`;store.items `before_id` 透传(§3 分支);`_HANDLERS` 注册;D12 bump `PROTOCOL_VERSION` |
| src/myia/store/sqlite.py | `list_runs`(runs 节);`daily_item_counts`(items 节);(条件)list_items `query/before/before_id` |
| src/myia/store/models.py | `FEEDBACK_CHANNEL_DESKTOP = "desktop"` |
| desktop/ui-src/src/lib/api/types.ts + client.ts | §10 修订口径:本批 8 新方法封装入共享门面 + storeItems 可选参/VersionResult.app_version 类型扩展;spec 屏私名单(sources.write/yaml.*/image.*)不动 |
| desktop/ui-src/src/hooks/use-sidecar-status.ts | 订阅 `sidecar://state`;状态机增 respawning/dead;reprobe → 探测+拉起 |
| desktop/ui-src/src/components/layout/top-bar.tsx | 状态徽标新态/「拉起」按钮/tooltip 增 app 版本;挂 GlobalRun(一次 commit 收口本批全部 top-bar 改动) |
| desktop/ui-src/src/components/layout/global-run.tsx(新) | C12 跑一次 + 取消 |
| desktop/ui-src/src/screens/feed/api.ts | `fetchFeedPage` before/before_id 游标 + cursorId(§3.1) |
| desktop/ui-src/src/screens/feed/feed-card-feedback.tsx(新)+ feed-screen.tsx | B2 卡片 👍/👎(feed-screen 仅挂载点) |
| desktop/ui-src/src/screens/dashboard/api.ts + dashboard-screen.tsx | C3 runs.list 重构线;B4 `fillDailyCounts/toSparklinePoints`;两新卡组件文件(trend-card / feedback-stats-card) |
| desktop/ui-src/src/screens/settings/settings-screen.tsx + settings/api.ts | B3「评分与反馈」分区;C11 model/enabled 写回助手 `saveCategoryNode`;C5 凭据行删除;push 声明指引链接 |
| desktop/ui-src/src/screens/sources/sources-screen.tsx + sources/api.ts | C13 行内试抓(sourcesTest 走共享门面);sources/api.ts 头注如实化(私有通道保留不迁移,§10) |
| .trellis/spec/desktop/sidecar-protocol.md | **协议注册表随同更新(spec 变更纪律第 1 条)**:23→31(新增 8 行,分组注 v1.1.2 桌面对齐)+ store.items/version 行参数注记 + 错误码表补 `run_not_active`/`test_busy`/`item_not_found` 等;每个新增方法的 D 步同 commit 更新,D12 用 `data.allowed` 对账(纪律第 2 条)——首稿漏此落点,评审补 |
| tests/test_desktop_sidecar_protocol.py | C1 同刻取尽夹具;run.cancel 全往返;runs.list;secret.delete;feedback 三方法;sources.test(mock 子进程或 127.0.0.1 本地服务夹具);store.trend;version.app_version |
| CHANGELOG.md | D12 协议 v2 合流总账 |
| (顺风车)desktop/ui-src/src/routes/*.tsx + App.tsx;src/myia/engines/fetch_base.py:768 | E4 骨架死代码清理;E5 过期注释 |

### 11.2 与在途会话的避撞顺序

1. **排程门**:v1.1.1 tag 后才 start(PRD 排程前置;本档仅备档)。
2. **yaml-editor(应已合入)**:协议六方法已在 HEAD;其 UI 屏(screens/yaml-editor/)
   本批零触碰;若彼档仍 in_progress 且还有 entry.py 协议面改动 → 等其收口再开工
   (§11.3 第 2 条)。
3. **feed-ux(并行,碰撞面最大)**:共享文件 = top-bar.tsx(其 C8 品类 Select 接线
   vs 本批 C2 徽标/C10 tooltip/C12 跑一次)、feed/api.ts(其 query vs 本批游标)、
   client.ts(各自增封装,加法不冲突)、dashboard-screen.tsx(其 G4 品类卡「跑一次」
   vs 本批 C3/B4/B2 三卡)、settings-screen.tsx(其 G5 推送测试按钮 vs 本批 B3/C5/C11
   分区)。姿态:本批 C12 全局跑一次**放顶栏不放仪表盘**、B2 统计**放仪表盘不放
   feed 屏**,把同文件交叠压到行级;先合者为准、后合者 rebase;两批协议形状以本
   设计 §1/§3 合流表为准(feed-ux 无需改其设计)。
4. **工作区在途线(不点名,以开工时实况为准)**:首稿时的 push/messaging 线已提交
   (`2b54865`);评审时的 image 屏测试/client.ts 头注/spec 目录等属 spec 立档线
   ——后者与本批强相关(spec 注册表 = 本批协议文档,§10/§11.1 新增更新义务),
   开工前须其合入且政策定稿(§11.3 第 6 条);其余线以 `git status` 干净为准。
5. **v111-release**:若 README 改口/版本对齐仍有尾巴,本批不动 README(明确不做)。

### 11.3 开工前核对清单(start 前逐条打勾,任一不过即顺延并升级)

1. `git tag --list 'v1.1.1*'` 非空(排程门);tauri.conf.json version ≥ 1.1.1。
2. yaml-editor:`grep -c '"yaml.save"' desktop/entry.py` ≥1(现 HEAD 已满足)+
   其 task.json 已 completed/或 notes 载明协议面冻结。
3. `git status` 干净;在途改动(无论哪条线)先请其落定或 rebase——不点名具体线,
   以 `git status` 实况为准(§0:多会话滚动,点名必过期)。
4. feed-ux 合流分支判定:`grep -n "before" src/myia/store/sqlite.py | head` ——
   已有 → 本批 C1 走「增量加 before_id + UI + 测试」;没有 → 本批按 §3 合流形状
   一次性补齐并回标 feed-ux(其 implement 步骤 1 作废半边)。
5. `grep -n "^PROTOCOL_VERSION" desktop/entry.py` 记现值 → 定 D12 bump 所有权(§1.3)。
6. **spec 封装政策现值核对**:读 `.trellis/spec/desktop/sidecar-protocol.md` 变更纪律
   现文 + `git log --oneline -- .trellis/spec/desktop/sidecar-protocol.md`——确认
   其已按评审拍板合入(方案①:注册表对账口径);若政策仍是未定稿/又变,§10 与
   D10 回设计重开,不得按过期口径开工。
7. 基线实测并记录:`uv run --no-sync python -m pytest -q` 与
   `npm --prefix desktop/ui-src run test`、`run build` 的通过数(PRD:以开工时基线
   为准,勿死守 1397/40)。

### 11.4 回滚点

- 每步独立 commit(implement.md 步序即回滚序):协议新方法均为 `_HANDLERS` 增量
  注册,revert 即摘除;壳层 respawn 与 `sidecar_restart` 是 Rust 独立 commit,cargo
  revert 即回现状(顶栏 hook 退回纯探测);feed/api.ts 游标改造单 commit,退回即
  恢复 since 语义;settings 分区/凭据删除各自独立;D12 版本 bump 独立 commit 可单退。
- 数据零迁移:本批无 schema 变更(runs/items/feedback 表现势直读),全部回滚零残留。
