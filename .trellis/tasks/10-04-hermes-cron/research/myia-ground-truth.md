# MYIA 地面真值核实 + APScheduler 探针(10-04-hermes-cron,2026-10-04 实核)

> 两路只读探查 + 两个实跑探针的结论沉淀;行号以 2026-10-04 工作树为准。设计引用本文为事实依据。探针脚本随档:`probe-apscheduler.py`、`probe-dow-normalizer.py`(uv run python 可复跑)。

## A. 管线与推送层(设计直接消费的事实)

1. **RunResult**(pipeline.py:509):`category/category_name/run_id/started_at/dry_run/finished_at/status(RUN_STATUS 枚举 success|partial|failed)/stages[StageReport]/sources[SourceReport]/items(留存条目)/pushes[ChannelPushReport]/resumed_from_run_id/maintenance/feedback_tuning`;辅助 `stats_dict()(:558 落 runs.stats)/to_dict()(:582 --json 全量)/duration_seconds/resolve_status`(CLI 退出码 0/2/3 契约同源)。**cron 摘要直接消费 to_dict()/stats_dict,零新统计**。
2. **run() 不起任何轮询**:telegram getUpdates 只在 `run_forever`(:2344-2352)构建,随常驻退出取消;`run()`(:942-1163)全函数无 getUpdates。**cron job 只调 run() 则与 `--loop` 常驻零 409 冲突**(pipeline.py:2322-2327 的 409 约束只针对多常驻轮询方)。
3. **run 收尾搭车**:非 dry-run 每 run 执行 `feedback_tuning`(开场 :981)+ `maintenance`(retention+cadence 门控 VACUUM,:1149)。cron 多品类高频触发=maintenance 高频执行,**不需要也不应再建独立维护 job**。
4. **digest 池挂 Pipeline 实例**(:852,`_digest_pools[entry_index]`),进程内无 store 回填;run 内 add 后**立即 flush**(:2267-2273),AM/PM 攒卡只在常驻进程成立;flush 全失败留池=同 run 内下次 flush 重试(pipeline.py:2276-2285 明言进程重启即丢)。**「cron 每 fire 新建执行上下文」下,末次 flush 失败的条目无重试路径**——文档注记即可,无需机制(偏离 D6)。
5. **定向投递**(push/delivery.py:308):`send_batch_to_targets(items, *, specs, channel, context, directory, ledger=None, platforms=None)`;SendContext(base.py:113)`slot("am"/"pm")+date 必填,kind Literal["digest","immediate"],target 可空`;SendReport `channel/ok/item_count/error/skipped`;死信判定 `classify_dead_error`(:156)+DeliveryLedger(`<data_root>/delivery_ledger.json`,mark_dead/is_dead/clear)。**channel 须 supports_targeting=True(现仅 feishu_card.py:230、telegram.py:277)**。
6. **目录与解析**:ChannelDirectory(directory.py:151)`(data_root)` 构造即 load()+别名,**无需 refresh 即可解析**;data_root=`Path(db_path).parent`(pipeline.py:857)。resolve_all(targets.py:205)四级:直达钩子→精确 id→精确名→唯一前缀;TargetResolveError 带候选。⚠️ **ChannelDirectory 无锁,跨进程并发 save=last-writer-wins**(原子 rename 保不撕裂,增量可能丢;观测条目可再积累,非致命,注记)。
7. **卡片渲染**:TemplateRenderer.render(templates.py:512,SandboxedEnvironment :503);feishu 内置 `build_markdown_card(markdown, *, title, ...)`(feishu_card.py:176)可直接承载单段 markdown。**无现成运行摘要模板;SendContext.kind 只有两值**,摘要卡要么借 kind 要么受控扩 Literal(grill Q3)。
8. **runs 表可复用**:list_runs(category=..., limit=)(sqlite.py:1315)按品类查历史;cron 摘要快照可选直读 runs.stats。

## B. CLI 与 sidecar(接入范式)

9. **CLI 子命令族范式**(cli.py):嵌套 add_parser(channels :452-485 范式:`channels_sub.add_subparsers(dest="channels_command", required=True)`)+ `main()` handlers dict(:3190-3206);`--json`=stdout 恰一份 JSON,日志 stderr 且收敛 WARNING;`_cmd_run` 退出码 0/1/2/3(success→0/failed→2/partial→3,:161-165);`_resolve_pools(config_path, as_json)`(:1295)。**cron 族插 `_add_run_parser` 后 + handlers dict 一行**。
10. **sidecar serve**(entry.py:3657):单线程逐行 JSON-RPC,handler 同步执行=协议队头阻塞(spec 明文,**ticker 绝不能在 serve 线程跑**);worker 线程发事件用 `_write_line`(`_WRITE_LOCK` 线程安全);`_HANDLERS`(:3571)43 方法字面量注册;新方法姿势=`_m_xxx(params)->dict`+注册一行+isinstance 校验+ProtocolError。
11. **schedule.preview(:2072)**:cron.* 族最近亲;`build_cron_trigger` 局部 import;推进用 `cursor = fire + timedelta(microseconds=1)` 严格递增。
12. **协议四件套**:PROTOCOL_VERSION=6(:336,一任务批一 bump);同步面=entry.py/_HANDLERS+test_desktop_sidecar_protocol.py(含**对账计数** `len(allowed)==43` :2184-2202,加方法必改)+spec desktop/sidecar-protocol.md+CHANGELOG;前端镜像 client.ts/types.ts 可选。⚠️ **autouse 夹具 `_reset_sidecar_state`(test:89-120)逐例重置模块级全局+cron ticker 句柄若引入必须加进去,否则测试间线程泄漏**。
13. **数据根**:`_serve_context()`(:418)优先级=显式 params>MYIA_HOME env>bundle 探测>dev cwd;home 模式 `<home>/myia.db`+`<home>/plugins`;db 传递=各 handler 临时 `SQLiteStore(db)` 开闭。
14. **sidecar 从不构造 Pipeline**(零命中):桌面 run 一律子进程 `_self_command(["run", yaml, "--db", db])`(:2278-2289,Popen(start_new_session=True) :2341,killpg 宽限 Timer :2432)。异步先例=handler 内 `asyncio.run(单协程)`(:1041/:3559)。**cron 执行体照此走子进程=零新范式**(偏离 D11)。
15. **桌面 run 全局单飞**:`_RUNS_LOCK+_ACTIVE_RUN_ID`(:2448-2456)查-占原子,占用报 run_busy。**cron fire 与用户手点 run.start 必争此锁**——冲突策略=设计必须定义(grill Q2,推荐=cron 侧跳过本 fire+结构化事件)。
16. **测试范式**:无 freezegun/pytest-asyncio;异步=同步体内 `asyncio.run`(conftest `run()` 助手);控时=显式 `datetime(..., tzinfo=ZoneInfo(...))`+FakeClock(conftest.py:39)注入 `clock/sleep`;run_forever 测试=CronTrigger(second="*/1")+max_fires=2 真等约 2s(test_pipeline.py:709);FakeChannel 范式=类属性 name+`async send(items, context)`+calls 记录(test_push.py:74);品类 fixture=tmp_path 写 YAML 常量;`--json` 侧 stdout_stream=sys.stderr 注入(pipeline 构造)。

## C. APScheduler 探针(实跑,3.11.3,2026-10-04)

17. **⚠️ 数字周几错位(决定性)**:`CronTrigger.from_crontab` 的 dow 数字按 **0=周一**(Python 系)解析,**不是 POSIX cron 的 0=周日**——`0 9 * * 1`(cron 语义周一)会射到**周二**;`sun/mon/tue` 名字不受影响。`7` 直接 ValueError(上限 6)。**Hermes 移植的自然语言前端产出 cron 编号(monday→"1"),必须过归一化层再进 CronTrigger**。
18. **dow 归一化器已实证**(probe-dow-normalizer.py):POSIX 语义(0/7=周日、列表、区间、步进、**环绕区间 5-1**、名字)展开为周名集合再喂 from_crontab;九个用例全过,端到端 `*/2` 从周一午 → 周二(cron 正确;不归一化会错成周三)。
19. **6 段 crontab 被拒**(from_crontab 只要 5 段;Hermes croniter 允许 5-6 段)→ 移植层限 **恰好 5 段**,6 段报结构化错(偏离 D1 附注)。
20. **DST 回拨安全**:America/New_York 2026-11-01 重复小时实验——fold=1(第二次 01:45)起算正确返回次日 01:30,**无 Hermes #11723 类过去时刻回环**;名字月/周(JAN/MON)表达式可用。
21. **重锚语义可用**:`get_next_fire_time(previous_fire_time=last_run_at, now)` 严格晚于 max(now, prev);恰在射点上的 base 返回**下一个**射点(严格后继)——compute_next_run(schedule, last_run_at) 契约直接满足。
22. ZoneInfo 直传可用(build_cron_trigger pipeline.py:676 现役同款);APScheduler 钉 `>=3.10,<4`(pyproject),实测 3.11.3。

## D. 汇总 ⚠️(设计必须回应)

| # | 事实 | 设计回应 |
|---|------|----------|
| W1 | APScheduler 数字周几=周一系 | schedule.py 加 normalize_dow(实证器随档) |
| W2 | sidecar 不构造 Pipeline,run 走子进程 | 执行体=子进程 `shishi run --json`(D11) |
| W3 | 桌面 run 全局单飞锁 | cron fire 冲突=跳过+事件(grill Q2) |
| W4 | SendContext.kind 仅 digest/immediate | 摘要卡 kind 受控扩值或借用(grill Q3) |
| W5 | digest 留池进程内 | 跨 fire 不攒=文档注记(D6),不建机制 |
| W6 | 目录跨进程 last-writer-wins | 注记+观测条目可再积累,不加锁 |
| W7 | 多品类同 db 并发 run 未验证 | 同 db job 串行化(grill Q2 附带) |
| W8 | maintenance 随 run 搭车 | 不建独立维护 job,频次照抄 |
