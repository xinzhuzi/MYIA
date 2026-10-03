# 用户零成本接入指引:免费 API 端点文档

## Goal

产出 `docs/zh/zero-cost.md` 与 `docs/en/zero-cost.md`(中文先行,英文随后):零成本接入指引 = 本地 mlx-vlm/Ollama(默认)→ OpenRouter `:free` / 智谱 GLM-4V-Flash(云端免费)→ Gemini 轻量批;从 getting-started.md 链入。素材源:`.trellis/tasks/archive/2026-10/10-03-free-tier-supplier-map/research.md`(决策面三)。

## Requirements

- 结构三段:本地默认(mlx-vlm/Ollama,零额度焦虑)→ 云端免费(OpenRouter `:free`、GLM-4V-Flash)→ 轻量批(Gemini 免费层);每段给出 vision 与 enrich 的适用性
- **落笔前核完 research.md 决策面三全部 ○ 项**(GLM-4V-Flash 政策、SiliconFlow 赠额、Groq/Mistral 限速)并回写 research.md 升 ●(引用时核机制)
- 每条免费额度必须带快照日期 +「以官网为准」;营销型额度(会耗尽/会到期)显式标注性质
- 接入步骤以 MYIA 现有配置面为准(OpenAI 兼容 base_url + key 三态入钥匙链,循 domain/security-baseline.md),不虚构配置项
- zh 先 en 后;两版内容对齐,en 可后置单独交付

## Acceptance Criteria

- [ ] `docs/zh/zero-cost.md` 交付,en 版按序(zh 验收通过后再做)
- [ ] `docs/zh|en/getting-started.md` 各加一节/一链指向 zero-cost.md
- [ ] research.md 决策面三 ○ 项全部核实升 ●(快照日期更新)
- [ ] 每条额度含快照日期与免责;营销型额度标明「用尽即止」类性质
- [ ] 无凭据、无私有痕迹(仓库公开红线,循 domain/security-baseline.md)

## Notes

- 轻量任务,PRD-only;不涉及产品代码改动
- 依赖:10-03-free-tier-supplier-map 的 research.md(活档案,引用时核)
- 免费路径与信号分级的选型语境见 `.trellis/spec/domain/connector-selection.md`
