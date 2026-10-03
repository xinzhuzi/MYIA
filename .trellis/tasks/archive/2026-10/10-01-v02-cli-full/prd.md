# CLI 完整:init / test / list / doctor

## Goal

CLI 是 AI 的手柄:四条子命令全部输出结构化结果,agent 拿到即可行动(AI-NATIVE)。

## Requirements

- `myia init`:向导输出**结构化提示(JSON)**供 agent 消费——生成品类 YAML 需要的信息清单,而非人机问答(规划定案)
- `myia test <yaml> [--source name]`:单源试抓,打印提取字段与指纹结果,不推送不入库
- `myia list`:插件清单+各源健康度(最近 run 成败/降级引擎/指纹跳过率)
- `myia doctor`:结构化诊断——源状态机(ok/degraded/dead)、engine_hints、凭据配置检查、调度下次触发时间;JSON 输出,agent 据此自修
- **源健康度判据**(list/doctor 共用;2026-10-01 主人补,防 09-28 式静默消失——ZOL gpu-prices 采 0 条却报 success 的事故不得复发):
  - `ok` = 本轮有产出,或 skip 原因=指纹未变(0 条合理)
  - `degraded` = 指纹未跳过却产出 0 条(抓取/提取疑似坏:页面结构变化、反爬升级);或条目数 < 近 5 次基线的 50%(基线取 store 历史;样本不足 5 次时只看 0 条判据)
  - `dead` = 连续 3 次采集失败
  - 判据看**源级条目数与 skip 原因**,不看 run 级成败——run 级 success 不得掩盖单源静默 0 条;若 store 尚未持久化源级条目数/skip 原因,先在 store 补齐(与 storage-hardening 同批,走其迁移机制)
- 全部命令支持 `--json`

## Acceptance Criteria

- [ ] 四命令 + `--json` 快照测试
- [ ] doctor 能诊断三类预置故障样例:明文凭据 / 源连续失败 / keychain 引用不存在
- [ ] 预置样例:「指纹未跳过但产出 0 条」的源,run 状态为 success,`myia list` / `myia doctor --json` 仍显示 degraded(ZOL gpu-prices 2026-10-01 静默 0 条事故的对位用例)
- [ ] `myia init` 输出被解析为 JSON 成功(契约测试)

## Notes

- 填充 `src/myia/cli.py`;v0.1 留位的子命令在本任务实现

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: uv run --no-sync python -m pytest tests/test_cli_full.py -q → 42 passed;实跑 `uv run --no-sync myia --version`(myia 1.1.1)与 `uv run --no-sync myia init --json`(stdout 被 python json.load 解析成功,command=init)。四命令 --json 单文档契约=TestJsonContract.test_all_four_commands_emit_single_json_document;doctor 三类故障=test_detects_plaintext_credential/test_detects_dead_source/test_detects_missing_keychain_ref;ZOL 静默 0 条对位=list 侧 test_silent_zero_with_success_run_is_degraded + doctor 侧 test_detects_silent_zero_despite_success_run(均断言 degraded);init JSON 契约=test_init_json_contract_parses。
