# Implement:10-04-hermes-cron

> 查改分家:实现前重读 research/hermes-cron-map.md 对照表;每个模块开工前先读对应上游文件再动笔(先 Read 再操作)。上游路径前缀 `H = ~/.hermes/hermes-agent`。

## Stage 0:基线

- [ ] 0.1 `uv run pytest -q` 记录基线(当前已知预存红以 stash 验证 HEAD 树归因);`git status` 快照留档
- [ ] 0.2 确认 `APScheduler>=3.10,<4` 在核心依赖(pyproject)且 CronTrigger 支持 5 段 crontab + tz(`uv run python -c` 探针)

## Stage A:定时底座(自底向上,每步独立可测)

- [ ] A1 `shishi/cron/constants.py` + `schedule.py`
  - 照抄 H:cron/constants.py 全文;jobs.py 642-1238 段(normalize_repeat/parse_duration/_parse_clock_time/_natural_every_to_cron/parse_schedule/compute_next_run 前端)
  - compute_next_run 的 cron 分支改 CronTrigger(偏离 D1);once 朴素时间戳锚配置时区;interval UTC 加法照抄;DST 回拨严格未来单测
  - 测试 `tests/test_cron_schedule.py`:五形态解析+错误文案+next 计算+DST+重锚
- [ ] A2 `shishi/cron/store.py`
  - 照抄 H jobs.py 存储段:跨进程建议锁(fcntl/msvcrt 分平台)、tmp+rename 原子写、_normalize_job_record 规整、意外磁盘 job 合并(_merge_unexpected_disk_jobs)
  - 数据根:`<data_root>/cron/`(_serve_context 同源解析,CLI=cwd 相对 myia 习惯对齐 cli.py DEFAULT_DB_PATH 体系)
  - 测试 `tests/test_cron_store.py`:并发写、崩溃残留合并、损坏 JSON 修复路径
- [ ] A3 `shishi/cron/executions.py`
  - 照抄 H cron/executions.py:DDL/状态机/属主指纹/中断恢复/终态裁剪;裁 metrics 上报、增 run_summary_json(D10)
  - 测试 `tests/test_cron_executions.py`:状态机全转移+死属主判定+恢复+不可变性
- [ ] A4 `shishi/cron/jobs.py`(生命周期)
  - 照抄 H jobs.py:create/get/list/update/pause/resume/trigger/rearm_oneshot/remove + _apply_schedule_update + mark_job_run + get_due_jobs(backlog 坍缩)+ advance_next_runs + 心跳标记族
  - job 字段按 design §2.1(D2/D4 改造);mark_job_run 三态 status 语义照抄
  - 测试 `tests/test_cron_jobs.py`:CRUD+repeat 退役+paused+schedule 变更重算+due 扫描坍缩+心跳
- [ ] A5 `shishi/cron/occurrences.py`
  - 照抄 H cron/occurrences.py:scheduled_instant 去重 + pending_slot 三函数
  - 测试 `tests/test_cron_occurrences.py`(并入 A3/A4 亦可,但用例独立成文件)
- [ ] A6 `shishi/cron/tick.py` + `ticker.py`
  - 照抄 H scheduler_tick.py 全时序(design §3)+ scheduler_thread.py SupervisedTickerThread 全文
  - 测试 `tests/test_cron_tick.py`:文件锁单飞、先推进后派发(at-most-once)、并行池、单 job 失败不拖垮、ticker respawn
- [ ] **门禁 G1(底座成形)**:`uv run pytest -q tests/test_cron_*.py` 全绿 + 全量回归零新红;小步提交

## Stage B:宿主 + 业务

- [ ] B1 `shishi/cron/summary.py` + `runner.py`
  - summary:RunResult→人读摘要(状态/阶段统计/保留条目/per-channel 推送)
  - runner:线程内新事件循环→load_category_file→Pipeline.run→摘要→deliver 解析(push/targets)→send_batch_to_targets→三态 last_status→输出落 output/<job_id>/(design §3 执行体)
  - 测试 `tests/test_cron_runner.py`:成功/失败/delivery_failed 三态、deliver local/平台 spec、failure_deliver、线程循环隔离
- [ ] B2 CLI:`shishi cron` 子命令族(list/create/edit/pause/resume/run/remove/status/runs/serve/tick)
  - 对照 H hermes_cli/subcommands/cron.py 参数面,载荷参数换 --category/--dry-run/--db/--config/--deliver/--failure-deliver;输出 AI/人类双友好
  - 测试:tests/test_cli.py 增 cron 用例(离线:mock runner;真进程冒烟留 AC6)
- [ ] B3 sidecar:desktop/entry.py 起 ticker(数据根就绪后)+ `_HANDLERS` 注册 cron.* 方法族
  - 协议测试同步 tests/test_desktop_sidecar_protocol.py;spec/desktop/sidecar-protocol.md 增方法表
  - 注意:serve() 单线程循环,ticker 独立 daemon 线程,互不阻塞
- [ ] **门禁 G2(端到端)**:AC6 真跑——`shishi cron create "every 2m" --category plugins/news.yaml --deliver <测试通道>` 起 serve ≥2 周期:管线执行、摘要到达、心跳/status 可查、remove 清场
- [ ] B4 文档:docs/zh/cron.md + docs/en/cron.md(建 job/自然语言语法/deliver spec/serve 形态/--loop 并存注记);README 功能清单补行

## 收尾

- [ ] C1 全量 `uv run pytest -q` 零新红;`uv run pytest -q tests/test_cron_*.py` 绿;tsc/npm 不涉
- [ ] C2 `gitnexus detect-changes -r shishi --scope staged` 核验改动面(python spec 红线:未动核心依赖、未动 SQLiteStore)
- [ ] C3 spec 更新:python/index.md 增 cron/ 小节(存储布局/偏离表指针/蓝本归属);desktop/sidecar-protocol.md 增 cron.* 表
- [ ] C4 蓝本对照表终核:每模块 docstring 上游标注齐全(research/hermes-cron-map.md 勾销)
- [ ] C5 提交分批:底座(A)/业务+CLI(B1-B2)/sidecar+文档(B3-B4)三批,逐批 detect-changes

## 回滚点

- G1 前:删除 src/shishi/cron/ 即净回滚(无数据残留风险)
- G2 后:回滚同上;<data_root>/cron/ 数据目录无害,可留可删

## 验证命令速查

```bash
uv run pytest -q tests/test_cron_schedule.py tests/test_cron_store.py tests/test_cron_executions.py tests/test_cron_jobs.py tests/test_cron_tick.py tests/test_cron_runner.py
uv run shishi cron list
uv run shishi cron create "every 2m" --category plugins/news.yaml --deliver local
uv run shishi cron serve   # 另终端;Ctrl-C 停
uv run shishi cron status && uv run shishi cron runs
gitnexus detect-changes -r shishi --scope staged
```
