# Implement:10-04-hermes-cron(深化版 2026-10-04)

> 查改分家:实现前重读 research/hermes-cron-map.md(上游地图)+ research/myia-ground-truth.md(MYIA 实核+探针,下文 A/B/C/W 编号同该档);每个模块开工前先读对应上游文件再动笔。上游前缀 `H = ~/.hermes/hermes-agent`。

## Stage 0:基线

- [ ] 0.1 `uv run pytest -q` 记录基线(预存红以 stash 验证 HEAD 树归因);`git status` 快照留档
- [ ] 0.2 核对探针仍绿:`uv run python .trellis/tasks/10-04-hermes-cron/research/probe-dow-normalizer.py`(APScheduler 3.11.3 实证环境)

## Stage A:定时底座(自底向上,每步独立可测)

- [ ] A1 `shishi/cron/constants.py` + `schedule.py`
  - constants 照抄 H:cron/constants.py 全文(TTL=300/SKEW=60/HEADROOM=3)
  - schedule 照抄 H jobs.py 642-1238 段:normalize_repeat_value / parse_duration / _WEEKDAY_TO_CRON_DOW / _parse_clock_time / _natural_every_to_cron / parse_schedule / compute_next_run 前端 / once 恢复窗 / 时间工具族
  - **normalize_dow 落产**(D1):把 probe-dow-normalizer.py 的实证器转正为库函数+九用例入测试(0/7=周日、列表、区间、步进、环绕 5-1、名字、大小写);cron 分支限 5 段、归一化后 `CronTrigger.from_crontab(expr, ZoneInfo(tz))`,`compute_next_run(cron)` = `get_next_fire_time(last_run_at or None, now)`
  - once 朴素时间戳锚配置时区;interval UTC 加法;错误文案照抄五形态清单
  - 测试 `tests/test_cron_schedule.py`:五形态解析+错误文案+next 计算(重锚/严格后继/恰在射点)+DST 回拨(New_York 2026-11-01 折叠小时,探针场景固化为测试)+dow 归一化九用例+端到端射日(`*/2` 周一午→周二)
- [ ] A2 `shishi/cron/store.py`
  - 照抄 H jobs.py 存储段:跨进程建议锁(fcntl/msvcrt)、tmp+rename 原子写、_normalize_job_record 规整、意外磁盘 job 合并(_merge_unexpected_disk_jobs)、_stage_jobs_payload
  - 数据根:`Path(db_path).parent / "cron"`(与目录/账本同款,ground-truth A6);目录自建
  - 测试 `tests/test_cron_store.py`:并发写、崩溃残留合并、损坏 JSON 修复路径
- [ ] A3 `shishi/cron/executions.py`
  - 照抄 H cron/executions.py:DDL(design §2.3,含 run_summary_json)/状态机/属主指纹(pid+start_time)/中断恢复/终态裁剪 1000;裁 metrics 上报
  - 测试 `tests/test_cron_executions.py`:全转移+死属主判定(指纹不匹配≠死亡)+恢复+终态不可变
- [ ] A4 `shishi/cron/jobs.py`(生命周期)
  - 照抄 H jobs.py:create/get/list/update/pause/resume/trigger/rearm_oneshot/remove + _apply_schedule_update + mark_job_run + get_due_jobs(backlog 坍缩)+ advance_next_runs + 心跳标记族;estop marker 检查口(grill Q4)
  - job 字段按 design §2.1(D2/D4/D11 改造);mark_job_run 四态 last_status(ok/failed/delivery_failed/skipped_busy)
  - 测试 `tests/test_cron_jobs.py`:CRUD+repeat 退役+paused reason+schedule 变更重算+due 扫描坍缩+心跳
- [ ] A5 `shishi/cron/occurrences.py`
  - 照抄 H cron/occurrences.py:scheduled_instant 去重 + pending_slot 三函数
  - 测试 `tests/test_cron_occurrences.py`
- [ ] A6 `shishi/cron/tick.py` + `ticker.py`
  - tick 照抄 H scheduler_tick.py 全时序(design §3.1);**派发分组=同 db 串行、不同 db 并行**(D13);ticker 照抄 H scheduler_thread.py SupervisedTickerThread 全文
  - 测试 `tests/test_cron_tick.py`:文件锁单飞、先推进后派发、并行池、单 job 失败不拖垮、ticker respawn
- [ ] **门禁 G1(底座成形)**:`uv run pytest -q tests/test_cron_schedule.py tests/test_cron_store.py tests/test_cron_executions.py tests/test_cron_jobs.py tests/test_cron_occurrences.py tests/test_cron_tick.py` 全绿 + 全量回归零新红;小步提交

## Stage B:执行体 + 宿主 + 业务

- [ ] B1 `shishi/cron/summary.py` + `runner.py`(D11/D12)
  - runner:`_self_command(["run", category, "--db", db, "--json", ...])` 子进程(参考 entry.py:2278/2341/2432 范式:Popen(start_new_session=True)、stdout JSON 泵、stderr 落 output/<job_id>/、墙钟 run_timeout 缺省 3600s→killpg SIGTERM→宽限 Timer→SIGKILL);退出码映射 0=ok/2=failed/3=partial 附注;RunResult.to_dict() 解析→summary 精简
  - summary:RunResult→摘要 dict+markdown 模板(状态/时长/源统计/留存/push 桶/失败行截断,design §5);SendContext kind 按 grill Q3 决议落
  - deliver≠local:宿主内 `asyncio.run(send_batch_to_targets(...))`(asyncio.run 先例 entry.py:3559;ChannelDirectory+DeliveryLedger 从数据根装载,A6);失败摘要走 failure_deliver
  - 测试 `tests/test_cron_runner.py`:spawn/JSON 解析/退出码映射/超时 killpg/deliver local+平台 spec(mock 子进程)/failure_deliver/delivery_failed 三态(用 RecordingChannel 范式 test_push.py:74)
- [ ] B2 CLI:`shishi cron` 子命令族(design §4.1 十一个子命令)
  - 注册仿 channels 族(cli.py:452-485)插 `_add_run_parser` 后 + handlers dict;`--json` 契约(stdout 恰一份 JSON)
  - 测试:tests/test_cli.py 增 cron 用例(离线 mock runner;serve/tick 冒烟走 AC6)
- [ ] B3 sidecar:desktop/entry.py
  - serve() 就绪后起 cron ticker(daemon Thread,数据根取 `_serve_context`;**不占 serve 线程**,B10 队头阻塞铁律);**cron fire 前查 `_RUNS_LOCK/_ACTIVE_RUN_ID`,占用=跳过+`skipped_busy`+`cron.skipped` 事件**(grill Q2 决议落;事件走 `_write_line`)
  - `_HANDLERS` 注册 cron.* 九方法(43→52)+ PROTOCOL_VERSION **v7**;**协议四件套同步**:entry.py/tests 对账计数+用例(spec 范本 :2287)/spec desktop/sidecar-protocol.md/CHANGELOG;**ticker 句柄入 `_reset_sidecar_state` autouse 夹具**(B12 ⚠️ 线程泄漏)
  - 测试:test_desktop_sidecar_protocol.py 增族用例+ticker 生命周期用例
- [ ] **门禁 G2(端到端)**:AC6 真跑——`shishi cron create "every 2m" --category plugins/news.yaml --deliver <测试通道>` 起 serve ≥2 周期:子进程 run 执行、runs 表有记录、摘要到达、心跳/status 可查、runs 命令出摘要、remove 清场
- [ ] B4 文档:docs/zh/cron.md + docs/en/cron.md(建 job/自然语言语法+5 段 cron POSIX dow 语义/deliver spec/serve 形态/与 `--loop` 并存注记/digest 留池行为注记);README 功能清单补行

## 收尾

- [ ] C1 全量 `uv run pytest -q` 零新红;桌面协议测试全绿;`uv run shishi cron --help` 面=design §4.1
- [ ] C2 `gitnexus detect-changes -r shishi --scope staged` 核验改动面(红线:核心依赖零新增、SQLiteStore 零改动、`run --loop` 零改动)
- [ ] C3 spec 更新:python/index.md 增 cron/ 小节(存储布局/蓝本归属/偏离表指针);desktop/sidecar-protocol.md 增 cron.* 表+事件两枚
- [ ] C4 蓝本对照终核:每模块 docstring 上游标注(research/hermes-cron-map.md 勾销)
- [ ] C5 提交分批:底座(A)/执行体+CLI(B1-B2)/sidecar+文档(B3-B4)三批,逐批 detect-changes;并行会话在场用 pathspec 限定提交

## 回滚点

- G1 前:删 src/shishi/cron/ 即净回滚(无数据残留风险)
- G2 后:回滚同上;`<data_root>/cron/` 数据目录无害,可留可删

## 验证命令速查

```bash
uv run pytest -q tests/test_cron_schedule.py tests/test_cron_store.py tests/test_cron_executions.py tests/test_cron_jobs.py tests/test_cron_occurrences.py tests/test_cron_tick.py tests/test_cron_runner.py
uv run shishi cron list
uv run shishi cron create "every 2m" --category plugins/news.yaml --deliver local
uv run shishi cron serve   # 另终端;Ctrl-C 停
uv run shishi cron status && uv run shishi cron runs
uv run python .trellis/tasks/10-04-hermes-cron/research/probe-dow-normalizer.py
gitnexus detect-changes -r shishi --scope staged
```
