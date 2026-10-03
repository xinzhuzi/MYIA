# Hermes cron 定时任务移植:定时底座 + 情报流定时跑一遍发消息

## Goal

主人 2026-10-04 指令:按照 Hermes 的定时任务去做功能,融合到本项目中,就是情报流,整体跑一遍做成定时任务功能,主要负责跑一遍、发送消息;先把这个定时任务的底座功能完善,再做上层业务;都要狠狠地照抄 Hermes。

蓝本 = Hermes 源码 cron/ 子系统(本地 `~/.hermes/hermes-agent`,NousResearch/Hermes-Agent,MIT;消息平台 W1 已按同款纪律移植过其 gateway)。交付分两段:

- **Stage A 底座**:通用定时任务子系统——job 存储、schedule 解析、tick 调度、执行账本、生命周期 API、CLI、宿主进程。
- **Stage B 业务**:情报流 job——job 载荷 = 品类 YAML,跑一遍 = `Pipeline.run()`(抓取→分类→去重→分析→内建推送),跑完把**运行结果摘要**按 job 的 `deliver` 目标定向推送(复用消息平台层 `feishu:群名` 等 spec)。

## Requirements

### F1 定时任务底座(照抄 Hermes cron/ 架构与语义)

- **F1.1 job 生命周期 API**:`create_job / get_job / list_jobs / update_job / pause_job / resume_job / trigger_job / rearm_oneshot / remove_job`,语义照抄 Hermes(含 repeat 次数与终态退役、paused 可带 reason、trigger 下次 tick 立即跑)。
- **F1.2 schedule 模型**:`"30m"` / `"every 2h"`(interval)、`"every monday 9am"` / `"weekdays at 9am"` / `"0 9 * * *"`(cron)、`"in 30m"` / ISO 时间戳(once);自然语言→cron 的转换、时长解析(`30m/2h/1d`、`noon/midnight`、12/24 时制)、错误信息形态照抄 `parse_schedule`。`compute_next_run` 锚定 last_run_at(重启不重锚)、interval 跨时区加 UTC、cron 按配置时区墙钟匹配、DST 回拨小时严格递增防循环。
- **F1.3 job 存储**:数据根 `<home>/cron/jobs.json`(tmp+rename 原子写、跨进程建议锁、磁盘意外多出的 job 记录合并不覆盖、读取前记录规整修复);执行输出落 `<home>/cron/output/<job_id>/`。**不进 myia.db、不动 SQLiteStore SCHEMA_VERSION**(Hermes 原味形态:jobs.json + cron 专属 executions.db)。
- **F1.4 tick 调度器**:tick 文件锁全进程单飞;到期扫描(超过一个周期的积压**坍缩只补一发**);**先推进 next_run_at 再派发**(at-most-once);并行池派发(max_workers 可配);单 job 失败不拖垮 tick;`mark_job_run` 完成时 re-arm + repeat 计数 + 到限退役;pending_slot(推进→认领窗口的崩溃凭证,属主死则恢复一次)。
- **F1.5 执行账本**:`<home>/cron/executions.db`(cron 专属 SQLite):execution 状态机(claimed→running→completed/failed/unknown)、属主存活判定(pid+进程起始时间指纹)、中断恢复(重启后 interrupted 标记)、终态不可变、终态行数裁剪。
- **F1.6 可观测:****ticker 心跳标记文件**(成功/错误时间戳),`cron status` 据此报告调度器活性;job 级 `last_status / last_error / last_delivery_error / failure_streak`。
- **F1.7 失败语义**:failure_streak 计数;运行成功但投递失败记 `last_status="delivery_failed"` 且不动 streak(Hermes 原味);`failure_deliver` 独立失败告警目标。

### F2 宿主进程(谁在跑 tick)

- **F2.1 CLI 常驻**:`shishi cron serve`——headless/服务器形态,ticker 走监督守护线程(Hermes `SupervisedTickerThread`:线程崩了自动 respawn、restart 计数入日志),60s tick。
- **F2.2 桌面 sidecar**:desktop/entry.py 起 ticker(同款监督线程)+ `_HANDLERS` 注册 `cron.*` 方法(list/create/edit/pause/resume/run/remove/status/runs,serve 由桌面进程天然承担);与 CLI serve 并存时靠 tick 文件锁 + fire claim 互斥不双发——多宿主共存是 Hermes 的原生设计,照抄。
- **F2.3 手动 tick**:`shishi cron tick`(单次扫描,调试/外接 cron 用)。

### F3 情报流 job(上层业务)

- **F3.1 job 载荷**:job 字段 `category`(品类 YAML 路径)+ 可选 `dry_run`;执行体 = 构造 `Pipeline` → `run()`。管线内建 push 阶段照常发生(情报本体推送);`--db`/代理池沿用 CLI run 的解析规则(`--config` pools)。
- **F3.2 运行结果摘要投递**:job 的 `deliver` = `"local"`(写 cron output 目录,默认)/ 平台 spec(`"feishu:群名"`、`"telegram:12345"`,经 `push/directory`+`push/targets` 解析、`push/delivery` 派发,复用死信语义);摘要内容 = 运行状态 + 各阶段统计(RunResult 形状)+ 保留条目数 + 推送结果;`failure_deliver` 同 spec 语法,运行失败时投递失败摘要。
- **F3.3 示范真跑**:用官方品类 YAML(如 plugins/news.yaml)建一个定时 job 真跑 ≥2 个 tick 周期,验证跑一遍+摘要消息端到端到达(真发冒烟沿用 W1 先例:定向卡入群)。

### F4 CLI 面(照抄 Hermes `hermes cron` 子命令族)

`shishi cron list / create / edit / pause / resume / run / remove / status / runs`(+ `serve` / `tick`);create 参数:schedule(自然语言/cron)、`--category`、`--name`、`--deliver`、`--failure-deliver`、`--repeat`、`--paused`/`--paused-reason`;输出对 AI/人类双友好。

## 非目标(明确不做)

- **不做** LLM/agent prompt 型 job(Hermes 的 prompt/skills/model/provider/toolsets 整族字段不搬)——MYIA 的 job 载荷就是品类 YAML 管线。
- **不做** detached external worker(`-m cron.scheduler` 子进程托管,服务 LLM 长任务跨网关重启存活)——v1 执行体是分钟级管线,进程内并行池 + 崩溃恢复已覆盖;架构位留好,单独立档再上。
- **不做** origin 入站回投(job 从哪个聊天创建就回哪)——MYIA 无入站;`origin` 字段保留为创建来源记录(如 `cli`/`desktop`),不参与投递解析。
- **不做** 桌面定时任务 UI 屏(sidecar 方法齐即可;UI 另立档)。
- **不做** quota hold / unreachable retry 阶梯 / incidents 告警事件簿(Hermes 面向 LLM 配额与模型不可达的机制,管线 job 无此故障面;failure_streak + failure_deliver 已覆盖告警需求)。
- **不碰** 现有 `shishi run --loop`(每品类单进程常驻保留原样;中心调度与其并存,同一 telegram bot token 单轮询方约束照旧,文档注记)。

## Constraints

- **蓝本移植纪律**(W1 定案沿用):逐文件对照 Hermes 源码结构重写为 MYIA 风格,模块 docstring 标注上游文件路径与 MIT 归属;不整块拷贝原文、不引 git 子模块;上游对照表登记在本档 design.md。
- **依赖红线**:核心 6 依赖不动。Hermes 用 croniter,MYIA 用已有 APScheduler `CronTrigger` 承担 cron 时刻计算(行为等价:DST 安全、墙钟锚定);自然语言解析层照抄。croniter 不进依赖。
- 全异步执行体跑在线程池的独立事件循环里,不阻塞 tick;类型注解全覆盖;数据根解析沿用 `MYIA_HOME` 体系(cron 目录挂数据根,CLI dev 模式=cwd)。
- 时间一律 aware ISO;时区取品类 YAML `schedule.timezone` 同源配置(job 可覆写,缺省本地)。

## Acceptance Criteria

- [ ] AC1 底座 API:`create/get/list/update/pause/resume/trigger/rearm_oneshot/remove` 全量单测覆盖,含 repeat 退役、paused reason、schedule 变更重算 next_run_at、无效 schedule 报错文案。
- [ ] AC2 schedule 解析:五种形态(interval/自然语言周几/cron/一次性 in-NaN/ISO)解析与 compute_next_run 全测,DST 回拨小时不产生过去时刻、interval 加 UTC、重启不重锚。
- [ ] AC3 at-most-once:tick 先推进后派发;模拟「推进后进程崩溃」→ pending_slot 恢复恰一次;两个并发 tick(双线程抢文件锁)只派发一份。
- [ ] AC4 执行账本:claimed→running→completed/failed 状态机;杀死执行进程后重启恢复为 interrupted,不重发已终态 execution;终态行数裁剪生效。
- [ ] AC5 多宿主互斥:`cron serve` 与桌面 sidecar ticker 同时开,同一 job 同一时刻只跑一份(tick 锁 + fire claim 双保险),人工 `cron tick` 抢不到锁时安静返回 0。
- [ ] AC6 情报流 job 端到端:`shishi cron create "every 5m" --category plugins/news.yaml --deliver <stdout/测试通道>` 真跑:管线执行、RunResult 摘要生成、deliver 定向到达;运行失败路径 failure_deliver 收到失败摘要;`last_status="delivery_failed"` 语义(成功+投递失败)有测。
- [ ] AC7 CLI 全子命令 + sidecar cron.* 方法行为一致(同一 API 层),桌面 `_HANDLERS` 注册表与协议测试同步。
- [ ] AC8 心跳/状态:`cron status` 报告 ticker 活性(心跳龄)、下次到期时刻;心跳标记文件在 tick 成功/失败时更新。
- [ ] AC9 蓝本对照:每个移植模块 docstring 标注上游文件;design.md 对照表完整;偏离表逐条有理由。
- [ ] AC10 回归:全量 pytest 零新红;`run --loop` 原行为不动;不新增核心依赖(pyproject dependencies 不变)。
- [ ] AC11 文档:docs/zh+en 新增定时任务文档(建 job/自然语言 schedule 语法/deliver spec/serve 形态),双语同步。
