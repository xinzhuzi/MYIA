# Hermes cron 蓝本对照表(10-04-hermes-cron)

> 上游前缀 `H = ~/.hermes/hermes-agent`。2026-10-04 实读建档;行号为当日 HEAD,引用前先核漂移。设计偏离见 design.md §6,本文只给地图。

## 模块映射与关键行号

| MYIA 目标 | 上游文件 | 关键内容(行号) |
|-----------|----------|------------------|
| cron/constants.py | H:cron/constants.py(17 行全文) | FIRE_CLAIM_TTL_SECONDS=300 / FIRE_CLAIM_SKEW_SECONDS=60 / CLAIM_TTL_INACTIVITY_HEADROOM=3 |
| cron/schedule.py | H:cron/jobs.py | normalize_repeat_value:642 / parse_duration:674 / _WEEKDAY_TO_CRON_DOW:690 / _parse_clock_time:706 / _natural_every_to_cron:731 / _cron_schedule:767 / _interval_schedule:780 / **parse_schedule:784** / 时间工具 _ensure_aware 等:860-938 / **compute_next_run:1175**(DST 双 fold 严格未来)/ _classify_dispatch_lateness:953 / _compute_grace_seconds:938 |
| cron/store.py | H:cron/jobs.py | 跨平台建议锁注释头:18 / _CronStorePaths:115 / store 路径解析:165-207 / flock 原语:251-286 / _jobs_lock:286 / 记录规整 _normalize_job_record:504 / _secure_dir/_ensure_cron_dir:576-631 / **_parse_jobs_file:1355 / load_jobs:1367** / 意外磁盘合并:1499-1541 / **_stage_jobs_payload:1548 / _save_jobs_unlocked:1567 / save_jobs:1614** |
| cron/jobs.py | H:cron/jobs.py | **create_job:1812**(job 记录全字段+缺省规则)/ resolve_job_ref:1961(AmbiguousJobReference)/ update_job:2108 / _apply_schedule_update:2077 / pause:2150 / resume:2163 / trigger:2203 / _claim_is_live:2245 / rearm_oneshot:2264 / remove_job:2299 / **mark_job_run:2454**(三态 status+quota/unreachable 钩子——钩子不搬 D7)/ **advance_next_runs:2685** / **get_due_jobs:2875**(+_DueScan 2891 / _normalize_due_scan_records:2929 / 积压坍缩 docstring)/ 心跳标记族:1239-1341(ticker_heartbeat/error/catch_up) |
| cron/executions.py | H:cron/executions.py(520 行) | _connect(_home/cron/executions.db):41 / DDL _initialize_schema:53 / _transaction:98 / 属主指纹 _process_start_time:122 / _owner_is_live:130 / create_execution:186 / mark_execution_running:285 / finish_execution:285 / **recover_interrupted_executions:322** / terminalize_dead_owner:389 / list_executions:444 / latest_executions:500 / MAX_TERMINAL_EXECUTIONS=1000 |
| cron/occurrences.py | H:cron/occurrences.py(102 行全文) | scheduled_instant:20 / **completed_occurrence:31**(到期身份去重,查账本 completed 行)/ **pending_slot 三函数:75-102**(advance→claim 崩溃窗口;属主死/租约过→恢复恰一次) |
| cron/tick.py | H:cron/scheduler_tick.py(121 行全文) | tick:7 全时序:tick 锁→estop→reap→due→sweep→advance→并行池→释放;单 job 失败不拖垮;空转心跳 |
| cron/ticker.py | H:cron/scheduler_thread.py(64 行全文) | SupervisedTickerThread:18(_crashed 标志/restart_if_dead/计数日志) |
| cron/runner.py | H:cron/scheduler.py | _run_one_job_body:3284(执行主干,agent 机器换 Pipeline)/ _submit_with_guard:4239 / run_job:2546(run 审计)/ _FireOwnership:2981(fire claim 心跳保活)/ 输出目录 _job_output_dir:423 |
| cron/summary.py | H:cron/scheduler.py | _summarize_cron_failure_for_delivery:289(失败摘要文案风格)/ _compose_run_delivery:2928(成功摘要组装思路) |
| CLI cron 族 | H:hermes_cli/subcommands/cron.py | 子命令面:list/create/edit/pause/resume(+--at)/run/remove/status/runs(--limit);create 参数:schedule/--name/--deliver/--failure-deliver/--repeat/--paused/--paused-reason |

## 关键语义备忘(照抄判据)

1. **at-most-once**:tick 在锁内**先** advance_next_runs 再派发(scheduler_tick.py:60-64 注释);mark_job_run 完成时覆写 next_run_at。
2. **积压坍缩**:recurring 超 1 周期未跑→fast-forward 但**仍发一发**(get_due_jobs docstring #33315 防永续推迟)。
3. **repeat**:once 自动 times=1;completed>=times → 终态退役移出 jobs.json;update 改 schedule 时重推导 repeat(_rederive_repeat_for_schedule_change:2045)。
4. **三态 last_status**:成功=ok;运行失败=failed;运行成功投递失败=delivery_failed(failure_streak 不动)(mark_job_run docstring)。
5. **fire claim**:TTL 300s;skew 60s 内的早到认领仍有效;heartbeat 保活(_run_with_fire_claim_heartbeat:2706);认领属主变更时丢弃过期完成回执(mark_job_run expected_fire_owner 校验)。
6. **once 时区**:朴素 ISO 锚**配置**时区非服务器本地(#51021 在案缺陷修复照抄)。
7. **jobs.json 锁**:写者持锁期间全量重写;另一进程磁盘新增的未知 job 记录合并不覆盖(_merge_unexpected_disk_jobs)。
8. **tick 单飞**:文件锁抢不到**静默 return 0**(手动 tick 与 daemon 并存安全)。

## 上游不搬清单(理由见 design.md 偏离表 D2-D8)

scheduler.py 的 agent 运行时族(_CronJobConfig/_resolve_job_runtime/watchdog/MCP/prefill/preflight)、scheduler_prompt.py、scheduler_script.py、scheduler_provider.py(Chronos)、scheduler_detached_worker.py、scheduler_worker_*.py、delivery_queue.py、bot_chat_delivery.py、quota_hold.py、unreachable_retry.py、incidents.py、lifecycle_guard.py、monitor.py、notepad.py、suggestions.py、suggestion_catalog.py、blueprint_catalog.py、scheduler_ownership.py(fire fence 精髓并入 jobs.py)、scheduler_failure_copy.py、scheduler_diagnostics.py、agent/periodic_scheduler.py、agent/monitoring/cron_health.py、job_definition.py(profile 分发导入)。
