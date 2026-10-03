# 免费层供应商地图:按 MYIA 决策面蒸馏 free-for.dev

## Goal

把 free-for.dev(1600+ 免费层服务清单)按 MYIA 四个决策面(push 通道/抓取引擎/vision+enrich LLM 端点/项目运营)蒸馏成供应商地图:每条带免费层现状、自托管替代、风险标注与核实状态;含趋势与反情报(免费层陷阱案例);供后续连接器选型与用户零成本接入文档取材。

## Requirements

- 蒸馏范围限定 MYIA 四个决策面,以当前代码为锚(push=src/myia/push/ 通道目录架构、engines=src/myia/engines/registry、vision+enrich=OpenAI 兼容端点、运营=开源项目自身),不做全清单搬运
- 每条候选必须带五要素:免费层快照、稳定性信号、自托管/本地替代、MYIA 适配度、核实状态(●=本轮已核 / ○=待核)
- 必须含选型总规则(本地优先、云端连接器须有免费路径、OpenAI 兼容优先)与反情报(免费层被砍/缩/墙的案例、free tier trap)
- 必须显式补位 free-for.dev 的西方中心盲区:中文生态服务(Bark/Server酱/GLM 等)单列并标 ○
- 尊重情报源边界:该仓库无 LICENSE,只记录事实要点与链接,不搬运清单原文

## Acceptance Criteria

- [x] 四个决策面各有候选表,合计 ≥25 条,每条含核实标记
- [x] ● 级(本轮经 zread 读其当前文档核实,快照 2026-10-03)条目 ≥10
- [x] 中文生态缺口有显式补位与 ○ 标记
- [x] 含「写入用户文档前二次核实 ○ 项」的流程要求(防免费层过期误导用户)
- [x] 无清单原文搬运(只有链接与事实要点)

## Notes

- 轻量 research 任务,PRD-only;交付物为同目录 research.md
- 非目标:不改产品代码、不产出用户文档(文档取材是后续任务,见 research.md 末尾出口清单)

## Grill 决议(2026-10-03,五问全按推荐)

- Q1 出口一 → 已立任务 `10-03-zero-cost-setup`(zh 先 en 后;落笔前核 ○ 升 ●)
- Q2 出口二 → 已落 spec `.trellis/spec/domain/connector-selection.md`(免费路径硬规则)
- Q3 出口三 → 入池不立项,排期权留主人(首选 Discord/Slack webhook)
- Q4 ○ 项核实 → 引用时核 + 回写 research.md 升 ●(机制详录 research.md 末节)
- Q5 档案形态 → 活档案;zero-cost-setup 执行完成后本任务 archive
