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

## 验收记录(2026-10-03,受主人委托代验)

**verdict: rejected(一处应修「照做即失败」缺陷,修复后复审即可归档)** —— 通过面:docs/zh|en/zero-cost.md 双语交付,三段结构/快照日期(2026-10-03)/以官网为准免责/营销型额度性质标注齐备;getting-started.md 双语链入(zh:56,130;en:65-66,154);research.md 决策面三引用项全部升 ●(SiliconFlow 赠额核实为「公开渠道已无明文」、诚实保留 ○ 并在文档显式排除,处置正确);`tests/test_docs.py` 80 passed(代验实跑);无凭据、无私有痕迹。**缺陷(where+证据):docs/zh/zero-cost.md:68,77 与 docs/en/zero-cost.md:85,96 教 `keychain:shishi/image/api_key` + `shishi secret set shishi/image/api_key`,而 src/myia/secrets.py:69 `_SECRET_NAME_RE=^myia/…` 强制 canonical 命名空间且 set/get 双侧校验(secrets.py:203-218,238),代码无任何 `shishi/` 别名(src/myia+desktop/entry.py grep 零命中)——照做即失败(录入被拒、yaml 引用解析失败,云端看图通道接不通);桌面端真名为 `myia/image/api_key`(src/myia/vision/settings.py:49)。溯源:845f64a 原版正确写 `myia/image/api_key`,改名波 cb87302 全局替换误伤,c1fa1ee 只复原 schema.md 漏了本档两页。修复=4 行把钥匙串名改回 `myia/image/api_key`(CLI 名 `shishi` 保留不动)。

## Notes

- 轻量任务,PRD-only;不涉及产品代码改动
- 依赖:10-03-free-tier-supplier-map 的 research.md(活档案,引用时核)
- 免费路径与信号分级的选型语境见 `.trellis/spec/domain/connector-selection.md`

> 2026-10-03 复审:驳回项已修(4 处钥匙串名改回 myia/ 命名空间,文档口径与 secrets 校验器一致)
