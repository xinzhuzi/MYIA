# 编排:Pipeline DAG + APScheduler + asyncio

## Goal

编排层(核心):fetch → classify → dedup → analyze → push 的进程内轻量编排,APScheduler 定时,asyncio 并发。决策:不引入 n8n/Kestra 等编排平台(规划定案),自研 ~200 行。

## Requirements

- Pipeline 步骤模型:线性主链 + 少量分支;每步骤可配超时与重试;步骤产物(条目列表)向下游传递
- `analyze` 步骤 v0.1 为直通桩(LLM 精评 v0.2 接入),但接口位留好
- 调度:APScheduler 进程内 cron(`schedule` + `timezone`);前台 `run --once` 与常驻 `run --loop` 两种模式
- 并发:asyncio 任务组,多源并发采集;同域限速由 fetch_base 合并(不在此层重复实现)
- 失败隔离:单源失败(采集/解析)不影响品类其余源与后续步骤;失败结构化记录到 runs 表
- 运行可观测:每次 run 的步骤级耗时/条目数/跳过原因(变更指纹 skip、dedup skip)输出结构化日志

## Acceptance Criteria

- [ ] 单测:步骤顺序与产物传递、单源失败隔离、超时与重试、cron+timezone 解析
- [ ] demo 插件 `--once` 端到端跑通;多源(≥4)并发采集耗时明显低于串行
- [ ] 常驻模式:到点自动触发(可用短周期 cron 验证)

## Notes

- 填充 `src/myia/pipeline.py`(41 行壳);APScheduler 是 v0.1 允许的新增依赖(纯 Python、pip 秒装)
- 服务端常驻(v0.2 docker compose)复用同一编排层,不另写调度

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: pytest tests/test_pipeline.py -q → 29 passed;步骤顺序与产物传递(:162)、单源失败隔离(:224)、超时隔离(:307)、重试+退避(:341)、cron+timezone(:686)、常驻短周期触发(:709)、多源并发 barrier 证并发(:412)全过;端到端实证:myia run plugins/myia-demo.yaml --dry-run --json exit 0,真实 GitHub API 3.1s 抓 30 条,--dry-run 用内存库无副作用(pipeline.py:840)。
