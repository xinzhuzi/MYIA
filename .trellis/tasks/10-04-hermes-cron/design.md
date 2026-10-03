# Design:Hermes cron → shishi/cron 移植

> 蓝本:`~/.hermes/hermes-agent/cron/`(NousResearch/Hermes-Agent,MIT)。纪律:逐文件对照重写,docstring 标注上游归属;本文对照表是权威映射,偏离只出现在「偏离表」且逐条给理由。

## 1. 架构总览

```
Hermes                                    MYIA(目标)
─────────────────────────                 ─────────────────────────
~/.hermes/cron/jobs.json                  <data_root>/cron/jobs.json        job 注册表(JSON,原子写+建议锁)
~/.hermes/cron/executions.db              <data_root>/cron/executions.db    执行账本(cron 专属 SQLite)
~/.hermes/cron/output/<job_id>/           <data_root>/cron/output/<job_id>/ 运行输出/摘要落盘
gateway 守护进程 60s tick                  shishi cron serve(监督守护线程)/ 桌面 sidecar ticker
cron/jobs.py        (3480 行)             shishi/cron/jobs.py               存储锁+CRUD+生命周期+到期扫描+心跳标记
cron/scheduler_tick.py                    shishi/cron/tick.py               tick 入场与派发(文件锁单飞)
cron/scheduler.py    (4403 行)            shishi/cron/runner.py             执行体(情报流 job:Pipeline.run)
cron/occurrences.py                       shishi/cron/occurrences.py        到期身份去重 + pending_slot
cron/executions.py                        shishi/cron/executions.py         执行账本(照抄,裁掉 metrics 上报)
cron/scheduler_thread.py                  shishi/cron/ticker.py             SupervisedTickerThread(照抄)
cron/constants.py                         shishi/cron/constants.py          FIRE_CLAIM_TTL=300s / SKEW=60s / HEADROOM=3
hermes_cli/subcommands/cron.py            shishi/cli.py cron 子命令族        list/create/edit/pause/resume/run/remove/status/runs/serve/tick
gateway._HANDLERS 等价物                   desktop/entry.py _HANDLERS        cron.* 方法族(协议见 spec/desktop/sidecar-protocol.md)
deliver → gateway 平台适配器               shishi/push/(W1 消息平台层)      directory+targets+delivery 复用
```

不搬(见 §6 偏离表):agent 运行时族(prompt/skills/model/toolsets/secret scope/MCP)、detached worker、code-skew yield、worktree 维护、bot_chat_delivery、quota/unreachable/incidents、suggestion/blueprint catalog、multiplex profile。

## 2. 数据模型

### 2.1 job 记录(jobs.json 数组元素;对照 Hermes `create_job` jobs.py:1812)

```jsonc
{
  "id": "a1b2c3d4e5f6",            // uuid4().hex[:12],照抄
  "name": "早晚情报流",              // 缺省取 category 文件名截 50
  "category": "plugins/news.yaml",  // ★载荷:品类 YAML 路径(绝对或相对数据根;替代 Hermes prompt 族)
  "dry_run": false,                 // 可选;仅显式设置才持久化(Hermes 可选键风格)
  "db_path": null,                  // 可选:覆写 myia.db 位置;缺省随数据根
  "config_path": null,              // 可选:pools YAML(--config 同款)
  "schedule": {"kind": "cron", "expr": "0 9 * * *", "display": "every day at 9am"},
  "schedule_display": "every day at 9am",
  "repeat": {"times": null, "completed": 0},   // times null = forever;once 自动 times=1
  "enabled": true, "state": "scheduled",       // state ∈ scheduled|paused(终态即退役删除,Hermes 同款)
  "paused_at": null, "paused_reason": null,
  "created_at": "...", "next_run_at": "...", "last_run_at": null,
  "last_status": null, "last_error": null, "last_delivery_error": null,
  "failure_streak": 0,
  "deliver": "local",               // local | platform:ref(feishu:群名);缺省 local(Hermes 缺省 origin 的 MYIA 化)
  "failure_deliver": null,          // 同 spec 语法;运行失败时投递失败摘要
  "origin": {"source": "cli"},      // 创建来源记录(cli|desktop);不参与投递(偏离表 D5)
  "timezone": null                  // 可选 IANA 名;缺省取品类 YAML schedule.timezone,再缺省本地
}
```

调度器专有运行态字段(随 job 记录存,同 Hermes):`fire_claim`、`pending_slot`、`monitor_state`(不搬)。

### 2.2 schedule dict(parse_schedule 产物;照抄 jobs.py:784)

`{"kind":"interval","minutes":30,"display":"every 30m"}` / `{"kind":"cron","expr":"0 9 * * *","display":...}` / `{"kind":"once","run_at":"ISO","display":"once at ..."}`。自然语言→cron 前端(`every monday 9am`、`weekdays at 9am`、`noon/midnight`、12/24 时制、逗号周几列表)照抄;**cron 时刻计算用 APScheduler `CronTrigger`**(偏离表 D1):`compute_next_run(kind=cron)` = `CronTrigger.from_crontab(expr, tz).get_next_fire_time(None, base)`;DST 回拨双候选取严格未来——CronTrigger 原生保证(其内部即 fold-aware 求解),单测钉死秋令重播小时用例。

### 2.3 executions.db(照抄 executions.py DDL,裁掉 source=external 维度的一半)

```sql
CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
  source TEXT NOT NULL,             -- 'tick' | 'manual'
  status TEXT NOT NULL,             -- claimed|running|completed|failed|unknown
  scheduled_instant TEXT,           -- 到期身份(occurrence 去重键)
  pid INTEGER, process_start_time REAL,   -- 属主指纹(照抄)
  claimed_at TEXT, started_at TEXT, finished_at TEXT,
  error TEXT, run_summary_json TEXT  -- ★MYIA 增量:RunResult 摘要快照(runs 查询用)
);
```

状态机与恢复照抄:属主指纹不匹配≠死亡证明;`unknown` 只在属主证实死亡后;终态不可变;`MAX_TERMINAL_EXECUTIONS=1000` 裁剪。

## 3. tick 时序(照抄 scheduler_tick.py,逐行对照)

```
tick():
  1 拿 tick 文件锁(cron/tick.lock,fcntl LOCK_EX|LOCK_NB;抢不到→静默 return 0)
  2 estop 检查(照抄:全局暂停标记 cron/paused.marker,`shishi cron pause --all` 写)
  3 reap 死属主(executions 属主指纹核实→终态化 unknown)
  4 due = get_due_jobs()            # 含 backlog 坍缩:>1 周期积压只发一发并 fast-forward
  5 sweep_stale_inflight(due)       # 清理超龄 fire_claim
  6 空转:更新心跳标记,return 0     # 心跳=成功时间戳;失败=错误标记(照抄 marker 文件族)
  7 advance_next_runs(due ids)      # ★先推进(at-most-once);recurring 盖 pending_slot 戳
  8 并行池 ThreadPoolExecutor(max_workers 默认 min(4, cpu)):
      per job: fire claim(TTL=300s,写 jobs.json)→ executions claimed → runner 执行
              → completed/failed → mark_job_run(re-arm+repeat 计数+终态退役)
  9 释放锁
```

**执行体 runner(替代 Hermes `_run_one_job_body` 的 agent 机器)**:

```
run_job(job):
  1 线程内新事件循环:asyncio.run(_execute(job))
  2 _execute: load_category_file(category)(schema 校验错→failed 结构化报错)
     → Pipeline(config, db_path=..., proxy_pools=_resolve_pools(config_path))
     → result = await pipeline.run(dry_run=...)      # 内建 push 阶段照常(情报本体推送)
  3 摘要 = render_run_summary(result, job)           # 状态/各阶段统计/保留条目/per-channel 推送结果
  4 deliver ≠ local → 解析 spec(directory+targets)→ send_batch_to_targets(复用死信账本)
  5 失败:failure_deliver 在场 → 投递失败摘要;last_status 三态 ok|failed|delivery_failed(照抄语义)
  6 输出落 cron/output/<job_id>/<ts>.md(运行文档,照抄 output 目录习惯)
```

Pipeline 每次执行新建实例:digest 失败留池是实例内存态(run_forever 同款约束),跨 tick 不保留——中心调度下 digest 重试语义=下次 tick 自然重推,行为注记进文档(偏离表 D6)。

## 4. 宿主与互斥

- **`shishi cron serve`**:阻塞主线程,ticker=SupervisedTickerThread(60s 间隔;respawn + restart 计数,照抄 scheduler_thread.py)+ 每循环 `restart_if_dead` 外层监督。
- **桌面 sidecar**:entry.py serve() 启动时起同款 ticker(数据根解析沿用 `_serve_context`);`_HANDLERS` 加 `cron.list/create/edit/pause/resume/run/remove/status/runs`(薄封装同一 API 层,协议测试同步;方法形态对齐 spec/desktop/sidecar-protocol.md)。
- **互斥**:tick.lock 单飞 + fire claim 认领=双保险(照抄常量 TTL 300s/skew 60s);`shishi cron tick` 手动单次扫描供外接 cron/调试。
- **与 run --loop 并存**:同一 telegram token 单轮询方约束照旧——telegram_feedback 轮询只在 Pipeline 实例内随 run 存在,cron job 间隔执行=短命轮询,与常驻 --loop 并存时仍可能互踢;文档注记「同 token 品类二选一形态」,不新增互斥机器(Hermes 无此概念,现状文档已有)。

## 5. 目录/时区/常量

```
src/shishi/cron/
  __init__.py        # 公共 API 再导出(对照 cron/__init__.py)
  constants.py       # FIRE_CLAIM_TTL_SECONDS=300 / FIRE_CLAIM_SKEW_SECONDS=60 / CLAIM_TTL_INACTIVITY_HEADROOM=3
  schedule.py        # parse_schedule + compute_next_run + natural-language 前端(上游 jobs.py 642-1238 段)
  store.py           # jobs.json 装载/原子写/建议锁/记录规整(上游 jobs.py 存储段 115-631 + 1355-1673)
  jobs.py            # 生命周期 CRUD + due 扫描 + advance/mark + 心跳标记(上游 jobs.py 其余)
  executions.py      # 执行账本(上游 executions.py)
  occurrences.py     # 到期身份去重 + pending_slot(上游 occurrences.py)
  tick.py            # tick 入场与派发(上游 scheduler_tick.py)
  runner.py          # 执行体:Pipeline job + 摘要 + deliver(替代上游 scheduler.py 主体)
  ticker.py          # SupervisedTickerThread(上游 scheduler_thread.py)
  summary.py         # RunResult→人读摘要渲染(MYIA 新;上游 _summarize_cron_failure_for_delivery 的对标)
```

时区解析链:job.timezone > 品类 YAML schedule.timezone > 本地(对照 Hermes get_timezone 链;once 朴素时间戳锚配置时区,防「存成 UTC 校验成 IST 错位」在案缺陷 #51021,照抄其修复)。

## 6. 偏离表(Hermes → MYIA,逐条理由;其余全照抄)

| # | 偏离 | 理由 |
|---|------|------|
| D1 | croniter → APScheduler CronTrigger | 核心 6 依赖红线(python spec);CronTrigger 已在核心依赖内且 DST 安全,行为单测钉死等价 |
| D2 | prompt/skills/model/provider/toolsets/interpreter/workdir/monitor_* 字段族不搬;载荷=category | MYIA job=品类管线,无 agent 运行时;lifecycle_guard(防重启循环)随之不需要 |
| D3 | detached external worker 不搬 | 服务对象是分钟级 LLM 长任务跨网关重启存活;管线 job 进程内并行池+账本恢复已覆盖;模块边界已留 |
| D4 | deliver 缺省 local(Hermes 缺省 origin 回投) | MYIA 无入站,origin 无从回投;origin 降级为创建来源记录 |
| D5 | bot_chat_delivery/delivery_queue(经常驻网关适配器出站+断递交队列)不搬 | MYIA 无常驻网关;投递在 runner 进程内直发(push 层自带死信+重试语义),不走队列 |
| D6 | Pipeline 每次 tick 新建实例(digest 池不跨 tick) | 中心调度多 job 并存下常驻实例=资源驻留;Hermes 每次运行也是全新会话;语义差异(留池重试)文档注记 |
| D7 | quota_hold/unreachable_retry/incidents 不搬 | LLM 配额/模型不可达故障面在 MYIA 是引擎层事务(fetch_base 已有降级重试);job 级用 failure_streak+failure_deliver 覆盖告警 |
| D8 | code-skew yield/worktree 维护/MCP 孤儿清扫/multiplex profile 不搬 | 分别绑定 Hermes 自更新网关、agent worktree、MCP 子进程、多 profile——MYIA 均无此物 |
| D9 | `hermes cron`(gateway 内嵌)→ `shishi cron serve` 独立子命令 + sidecar 双宿主 | MYIA 无 gateway 守护进程;双宿主互斥本就是 Hermes 多进程设计(tick 锁+fire claim),原样成立 |
| D10 | executions 增 run_summary_json 列 | `cron runs` 直接出摘要,免二次查 myia.db;上游从 session DB 取,Hermes 专属会话存储 MYIA 没有 |

## 7. 兼容与回滚

- 零迁移:不动 SQLiteStore schema、不动现有 CLI 行为、`run --loop` 原样;新目录 `<data_root>/cron/` 首次运行自建。
- 回滚=删除 `src/shishi/cron/` + CLI 子命令 + sidecar 方法注册;数据目录(jobs.json/executions.db/output)无害残留。
- 风险点:①APScheduler CronTrigger 与 croniter 语义差(周几编号/步进语法)——单测对照 5 段 cron 全形态;②线程内嵌事件循环与 sidecar 主循环互扰——runner 全隔离新循环,单测钉;③deliver 解析失败不得吞掉运行成功——三态 last_status 单测钉。
