# 引擎 llm_browser:L6 skyvern 兜底

## Goal

降级链末端(规划定案「烧 token,只做兜底」):skyvern 自然语言指挥浏览器,多步交互/复杂表单死源的最后手段。

## Requirements

- `llm_browser.py`:对接 skyvern(自部署/云,endpoint+key 走凭据引用);自然语言任务描述由源级配置或 AI 生成
- **硬护栏**(比 L3 更严):每 run 引擎调用次数上限、单源白名单(engine: llm_browser 显式指定或 auto 末位)、token 预算计入 enrich 同一套护栏体系
- 失败即终止降级链并结构化上报(链尾无「下一层」)

## Acceptance Criteria

- [ ] 多步交互源 demo 一次(真实或录制)
- [ ] 次数上限熔断、预算护栏单测
- [ ] auto 链完整顺序终测(L1→L6,注入假引擎)

## Notes

- 填充 `src/myia/engines/llm_browser.py` 壳(17 行)

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC2/AC3 有硬证据:跑 `uv run --no-sync python -m pytest tests/test_llm_browser.py -q` → 47 passed。AC2=test_whitelist_refuses_sources_not_explicit_or_auto(engine_not_whitelisted 零请求)、call_budget_exhausted 熔断系列(:691/:708/:743)、budget_exhausted(与 enrich 共用 BudgetTracker,:746-761)、test_default_guard_values_are_stricter_than_l5;AC3=test_auto_chain_full_seven_layers(L1→L6 全序)+ test_auto_chain_first_six_fail_llm_browser_rescues(假引擎)+ test_llm_browser_failure_terminates_chain_structured(链尾失败即终止、无下一层)。

遗留(需主人手动完成):
- AC1「多步交互源 demo 一次(真实或录制)」仓库内无记录:docs 与 workspace 无 skyvern 实跑/录制痕迹 —— 需主人以真实 skyvern 自部署/云 endpoint+key 做 demo 并留证(真实凭据类)
