# Design:Hermes cron → shishi/cron 移植(深化版 2026-10-04)

> 蓝本:`~/.hermes/hermes-agent/cron/`(NousResearch/Hermes-Agent,MIT)。纪律:逐文件对照重写,docstring 标注上游归属;本文对照表是权威映射,偏离只出现在「偏离表」且逐条给理由。事实依据:[research/hermes-cron-map.md](research/hermes-cron-map.md)(上游行号地图)、[research/myia-ground-truth.md](research/myia-ground-truth.md)(MYIA 实核+探针,下文引用作 A/B/C/W 编号)。

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
cron/scheduler.py    (4403 行)            shishi/cron/runner.py             执行体(spawn `shishi run --json` 子进程,D11)
cron/occurrences.py                       shishi/cron/occurrences.py        到期身份去重 + pending_slot
cron/executions.py                        shishi/cron/executions.py         执行账本(照抄,裁 metrics、增摘要列)
cron/scheduler_thread.py                  shishi/cron/ticker.py             SupervisedTickerThread(照抄)
cron/constants.py                         shishi/cron/constants.py          FIRE_CLAIM_TTL=300s / SKEW=60s / HEADROOM=3
hermes_cli/subcommands/cron.py            shishi/cli.py cron 子命令族        list/create/edit/pause/resume/run/remove/status/runs/serve/tick
gateway._HANDLERS 等价物                   desktop/entry.py _HANDLERS        cron.* 方法族(spec/desktop/sidecar-protocol.md)
deliver → gateway 平台适配器               shishi/push/(W1 消息平台层)      directory+targets+delivery 复用
```

不搬(见 §6 偏离表):agent 运行时族、detached worker、code-skew yield、worktree 维护、bot_chat_delivery/delivery_queue、quota/unreachable/incidents、suggestion/blueprint catalog、multiplex profile。

**数据根**:cron 目录 = `<data_root>/cron/`,data_root 与目录/账本同款 = `Path(db_path).parent`(pipeline.py:857 先例);CLI 形态即 `--db` 缺省 `myia.db` 所在目录(cwd),桌面 home 模式 = `<home>/`(B13)。

## 2. 数据模型

### 2.1 job 记录(jobs.json 数组元素;对照 Hermes create_job jobs.py:1812)

```jsonc
{
  "id": "a1b2c3d4e5f6",            // uuid4().hex[:12],照抄
  "name": "早晚情报流",              // 缺省取 category 文件名截 50
  "category": "plugins/news.yaml",  // ★载荷:品类 YAML 路径(替代 Hermes prompt 族,D2)
  "dry_run": false,                 // 可选;仅显式设置才持久化(Hermes 可选键风格)
  "db_path": null,                  // 可选:覆写 myia.db;缺省数据根推导
  "config_path": null,              // 可选:pools YAML(--config 同款,cli.py:1295 _resolve_pools)
  "run_timeout": null,              // 可选秒数,缺省 3600;墙钟超时 killpg(D12)
  "schedule": {"kind": "cron", "expr": "0 9 * * *", "display": "every day at 9am"},
  "schedule_display": "every day at 9am",
  "repeat": {"times": null, "completed": 0},   // times null = forever;once 自动 times=1
  "enabled": true, "state": "scheduled",       // state ∈ scheduled|paused(终态即退役删除)
  "paused_at": null, "paused_reason": null,
  "created_at": "...", "next_run_at": "...", "last_run_at": null,
  "last_status": null,              // ok|failed|delivery_failed|skipped_busy(W3,grill Q2)
  "last_error": null, "last_delivery_error": null,
  "failure_streak": 0,
  "deliver": "local",               // local | platform:ref;缺省 local(D4)
  "failure_deliver": null,          // 同 spec 语法;运行失败时投递失败摘要
  "origin": {"source": "cli"},      // cli|desktop;不参与投递(D4)
  "timezone": null                  // 可选 IANA;解析链 job > 品类 YAML schedule.timezone > 本地
}
```

调度器运行态字段随记录存(Hermes 同款):`fire_claim`、`pending_slot`。

### 2.2 schedule 模型(parse_schedule 产物;照抄 jobs.py:784)

`{"kind":"interval","minutes":30,"display":"every 30m"}` / `{"kind":"cron","expr":"0 9 * * *","display":...}` / `{"kind":"once","run_at":"ISO","display":"once at ..."}`。

- **自然语言前端照抄**:`every monday 9am`、`weekdays at 9am`、`30m/2h/1d`、`noon/midnight`、12/24 时制、逗号周几;错误信息形态照抄(Hermes 的五形态用法清单文案)。
- **cron 时刻计算 = APScheduler CronTrigger + dow 归一化层**(D1,探针实证 C17-C21):
  - `parse_schedule` 的 cron 分支**限恰好 5 段**(6 段结构化拒绝——Hermes 允许 croniter 5-6 段,D1 附注);dow 字段过 `normalize_dow`:POSIX 语义(0/7=周日、列表/区间/步进/**环绕区间 5-1**/名字)展开为周名集合(实证器 `research/probe-dow-normalizer.py`,九用例+端到端全过)。
  - `compute_next_run(kind=cron)` = `CronTrigger.from_crontab(normalized_expr, ZoneInfo(tz)).get_next_fire_time(last_run_at or None, now)`:重锚严格后继、DST 回拨无回环、恰在射点返回下一射点(全部探针实证 C20-C21)。
  - interval 用 UTC 加法、once 朴素时间戳锚配置时区(#51021 修复照抄)、once 恢复窗 `_recoverable_oneshot_run_at` 照抄。

### 2.3 executions.db(照抄 executions.py DDL,裁 source=external 维度)

```sql
CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
  source TEXT NOT NULL,             -- 'tick' | 'manual'
  status TEXT NOT NULL,             -- claimed|running|completed|failed|unknown
  scheduled_instant TEXT,           -- 到期身份(occurrence 去重键)
  pid INTEGER, process_start_time REAL,   -- 属主指纹(照抄)
  claimed_at TEXT, started_at TEXT, finished_at TEXT,
  error TEXT, run_summary_json TEXT  -- ★MYIA 增量:RunResult.stats_dict 快照(D10)
);
```

状态机与恢复照抄:属主指纹不匹配≠死亡;`unknown` 只在属主证实死亡后;终态不可变;`MAX_TERMINAL_EXECUTIONS=1000` 裁剪。

## 3. tick 时序与执行体

### 3.1 tick(照抄 scheduler_tick.py,逐行对照)

```
tick():
  1 拿 tick 文件锁(cron/tick.lock,fcntl LOCK_EX|LOCK_NB;抢不到→静默 return 0)
  2 estop 检查(cron/paused.marker 全局急停;`shishi cron pause --all` 写,grill Q5)
  3 reap 死属主(executions 属主指纹核实→终态化 unknown)
  4 due = get_due_jobs()            # 含 backlog 坍缩:>1 周期积压只发一发并 fast-forward
  5 sweep_stale_inflight(due)       # 清理超龄 fire_claim
  6 空转:更新心跳标记,return 0     # 成功/错误标记文件族照抄(cron.status 消费)
  7 advance_next_runs(due ids)      # ★先推进(at-most-once);recurring 盖 pending_slot 戳
  8 派发:同 db 的 job 串行、不同 db 可并行(W7;实现=按 db_path 分组,组内顺序 await,
    组间 ThreadPoolExecutor(max_workers=min(4,cpu)))
      per job: 宿主侧互斥检查(见 3.3)→ fire claim(TTL=300s 写 jobs.json)
              → executions claimed → runner.execute(job) → completed/failed
              → mark_job_run(re-arm+repeat 计数+终态退役)
  9 释放锁
```

### 3.2 执行体 runner(D11:子进程模型,对齐 sidecar run 现范式 B14)

```
execute(job) -> (status, summary_dict):
  1 构造命令:[sys.executable -m shishi? 不——CLI 入口] `_self_command(["run", category,
     "--db", db, "--json"] + (["--dry-run"] if dry_run) + (["--config", cfg] if cfg))`
     (sidecar entry.py:2278 _self_command 先例;CLI serve 形态=等价自 invoke)
  2 Popen(start_new_session=True),异步泵 stdout(JSON 文档)与 stderr(日志留档 output/<job_id>/);
     超时 run_timeout(墙钟,缺省 3600s)→ killpg(SIGTERM→宽限 SIGKILL,Timer 范式 entry.py:2432)
  3 解析 stdout RunResult JSON(to_dict 形态,A1)→ summary = 精简(status/duration/sources 统计/
     items_retained/push 桶计数/item_failures 数)→ run_summary_json 入 executions
  4 退出码语义复用 CLI 契约:0=ok,2=failed,3=partial→按 job 语义映射 ok|failed(3 记 ok+附注
    partial 标志,与 run.start 的 exit 语义一致);超时/无法解析=failed(结构化 error)
  5 摘要投递(deliver≠local):宿主进程内 asyncio.run(send_batch_to_targets([伪条目], specs=[deliver],
     channel, SendContext(...), directory, ledger))(asyncio.run 先例 B14;目录/delivery 层在宿主可用 A5-A6)
  6 输出落 cron/output/<job_id>/<ts>.md(运行文档+摘要,照抄 output 目录习惯)
```

**为何子进程而非进程内构造 Pipeline**(D11):①sidecar 从不构造 Pipeline 是现状铁律(B14「需设计背书」),子进程=零新范式;②runs 表/维护/退出码语义天然获得;③执行体崩溃隔离(管线 segfault 不伤宿主);④冷启成本毫秒级。代价=每 fire 进程冷启+JSON 解析,可接受。

### 3.3 宿主互斥(W3/W7)

- **桌面 sidecar**:cron fire 前查 `_RUNS_LOCK/_ACTIVE_RUN_ID`(entry.py:2448 单飞锁):占用→**跳过本 fire**(advance 已消耗,不排队不回滚——与 at-most-once 一致),`last_status="skipped_busy"`+`cron.skipped` 事件(_write_line 线程安全 B10);用户手点优先于 cron。
- **CLI serve**:无桌面锁,同 db job 由 3.1 步骤 8 分组串行兜底;不同 db 并行。
- **多宿主**(serve+sidecar 并存):tick 文件锁单飞 + fire claim 认领,双保险照抄(常量 300s/60s/3)。
- telegram 409:cron 执行体只跑 run() 不起轮询(A2),与 `--loop` 常驻零冲突;文档注记「同 token 至多一个 --loop」照旧。

## 4. 宿主与 API 面

### 4.1 CLI:`shishi cron` 子命令族(照抄 hermes_cli/subcommands/cron.py 参数面)

| 子命令 | 参数 | 说明 |
|--------|------|------|
| `list` | `--db`(缺省 myia.db,定数据根)、`--json`、`--all`(含 paused/disabled) | 表格:name/id/schedule_display/next_run_at/last_status/deliver |
| `create` | `schedule`(位置)、`--category`(必填)、`--name/--deliver/--failure-deliver/--repeat/--db/--config/--timezone/--run-timeout/--dry-run/--paused/--paused-reason` | 建 job,回显 id+next_run_at |
| `edit` | `job_id` + `--schedule/--name/--category/--deliver/--failure-deliver/--repeat/--timezone/...` | 部分更新,schedule 变更重算 next_run_at |
| `pause` / `resume` | `job_id`(resume `--at` 可选 ISO 重挂)| 照抄;另 `pause --all`/`resume --all`=estop 标记(grill Q5) |
| `run` | `job_id` | 下次 tick 立即跑(manual source) |
| `remove` | `job_id` | 删记录(output 目录保留) |
| `status` | — | ticker 活性(心跳龄/最后错误/下次到期) |
| `runs` | `[job_id] --limit`(缺省 20,1-500) | executions 账本尾查 |
| `serve` | `--db/--interval`(缺省 60s) | 阻塞常驻,SupervisedTickerThread |
| `tick` | `--db` | 手动单次扫描(调试/外接 cron;抢不到锁静默 0) |

注册:仿 channels 族(cli.py:452-485)插 `_add_run_parser` 后 + handlers dict 加 `"cron": _cmd_cron`(B9)。

### 4.2 sidecar cron.* 方法族(协议四件套,B12)

`cron.list/create/edit/pause/resume/run/remove/status/runs`(薄封装 API 层,参数同 CLI;create 参数收 `schedule/category/name/deliver/failure_deliver/repeat/timezone/run_timeout/dry_run/paused`)。事件:`cron.skipped`(W3)、`cron.completed`(fire 完成,带 job_id/status/摘要计数——桌面 UI 后续消费)。协议版本 **v7**(一任务批一 bump);`_HANDLERS` 43→52;对账计数测试同步;**ticker 线程句柄入 `_reset_sidecar_state` autouse 夹具**(B12 ⚠️,防测试线程泄漏)。ticker 启动=serve() 就绪后 daemon Thread(数据根从 `_serve_context` 取),绝不占 serve 线程(B10 队头阻塞铁律)。

## 5. 摘要卡(W4,grill Q3)

- 内容(spec 固定):`job 名 / 品类 / 状态(ok|partial|failed)/ 时长 / 源统计(n ok, m failed)/ 条目留存 / push 桶(immediate/digest/archive)/ 失败摘要行(截断)`——数据全部来自 RunResult.to_dict()(A1),零新统计。
- 渲染:新模板 `cron_summary`(TemplateRenderer 渲染 markdown)+ feishu 侧 `build_markdown_card` 直载(A7);telegram 侧 plain text。
- SendContext:`slot/date` 按 fire 时刻本地;**kind=受控扩 `"cron_summary"`**(base.py Literal+校验+card_title 一支,影响面三处,推荐)——grill Q3 备选=借 kind="digest" 零改动但语义撒谎。
- 失败摘要(failure_deliver):同模板换失败数据(error/尾部日志摘要截断)。

## 6. 偏离表(Hermes → MYIA,逐条理由;其余全照抄)

| # | 偏离 | 理由 |
|---|------|------|
| D1 | croniter → CronTrigger+normalize_dow 层;限 5 段 | 核心 6 依赖红线;⚠️ 探针实证数字周几 0=周一(C17)必须归一化(实证器随档);6 段/7 拒收结构化报错;DST/重锚/严格后继探针全绿(C20-C21) |
| D2 | prompt/skills/model/provider/toolsets/interpreter/workdir/monitor_* 字段族不搬;载荷=category | MYIA job=品类管线,无 agent 运行时;lifecycle_guard 随之不需要 |
| D3 | detached external worker 不搬 | 子进程执行体(D11)已覆盖隔离诉求;跨网关重启存活是 LLM 长任务诉求,管线 job 墙钟分钟级 |
| D4 | deliver 缺省 local;origin 降级创建来源记录 | MYIA 无入站,origin 无从回投 |
| D5 | bot_chat_delivery/delivery_queue 不搬 | MYIA 无常驻网关;投递在宿主进程直发(push 层自带死信+自愈),不走断交队列 |
| D6 | 每 fire 新执行上下文;digest 留池跨 fire 不攒 | 探查实证池挂 Pipeline 实例且 run 内 add 即 flush(A4);单 run 末次 flush 失败条目无重试=文档注记,不建机制 |
| D7 | quota_hold/unreachable_retry/incidents 不搬 | LLM 配额故障面不存在;failure_streak+failure_deliver 覆盖告警 |
| D8 | code-skew/worktree/MCP 孤儿/multiplex 不搬 | 绑定 Hermes 专属物,MYIA 均无 |
| D9 | `hermes cron`(gateway 内嵌)→ `cron serve` 独立子命令 + sidecar 双宿主 | MYIA 无 gateway;多宿主互斥本就是 Hermes 多进程设计,原样成立 |
| D10 | executions 增 run_summary_json 列 | `cron runs` 直出摘要;上游从 Hermes 会话 DB 取,MYIA 无会话存储 |
| D11 | 执行体=spawn `shishi run --json` 子进程(Hermes=进程内 agent) | sidecar 不构造 Pipeline 是现状范式(B14);runs 表/维护/退出码天然;崩溃隔离;killpg+墙钟超时复用 run.start 范式 |
| D12 | HERMES_CRON_TIMEOUT 不活跃超时 → run_timeout 墙钟超时 | 子进程内活性宿主不可见;墙钟+killpg 可实现等价的资源护栏 |
| D13 | 同 db job 派发串行化(W7) | Pipeline 并发写同 db 未验证;Hermes 并行池保留,按 db 分组排队 | 

## 7. 兼容、回滚与风险

- 零迁移:不动 SQLiteStore schema、不动现有 CLI 行为、`run --loop` 原样;新目录首跑自建;回滚=删 `src/shishi/cron/`+CLI 子命令+sidecar 注册,数据目录无害残留。
- 风险:①normalize_dow 边角(环绕/步进/大小写)——实证器九用例+单测扩全;②子进程 stdout 混行(管线日志走 stderr、JSON 独占 stdout,`--json` 契约保证 B9);③宿主事件线程安全——`_write_line` 锁内,先例 B10;④目录跨进程 last-writer-wins(W6)——注记不加锁,观测条目可再积累;⑤sidecar 与 serve 同时投递摘要的重复面——deliver 发送幂等性=平台侧非幂等,靠 fire claim 唯一认领保证单发(照抄 Hermes 语义)。

## 8. Grill 待决问题(已带推荐,批复后回写)

- **Q1 执行体形态**:子进程 `shishi run --json`(D11,推荐)vs sidecar 进程内构造 Pipeline(新范式,探查明示需背书)。
- **Q2 cron × 桌面单飞冲突**:占用即跳过本 fire+`skipped_busy` 状态+`cron.skipped` 事件(推荐,与 at-most-once 一致)vs 排队等 run 完(Hermes 无排队语义)。
- **Q3 摘要卡 kind**:受控扩 `SendContext.kind="cron_summary"`(推荐,三处小改)vs 借用 digest(零改但语义失真)。
- **Q4 estop 全局急停**:`pause --all`/`resume --all` 落 marker 文件,tick 检查照抄 Hermes estop(推荐保留)vs v1 砍掉逐 job pause 够用。
- **Q5 repeat×长任务**:interval 5m 而管线跑 8m→完成后重锚 last_run_at+5m(推荐,compute_next_run 锚语义自然给出)vs 错过后立即补发。
