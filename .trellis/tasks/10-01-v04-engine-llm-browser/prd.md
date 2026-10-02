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
