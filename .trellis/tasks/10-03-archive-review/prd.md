# 归档会话:43 个 review 任务批量核对归档

## Goal

普查 E6 流程债:review 滞留任务批量核对归档,**独立会话执行、排 v1.1.1
tag 后**(grill 2026-10-03 Q5/Q7;避免与在途会话缠绕)。

## Requirements

- 逐个核对 review 状态任务:验收全勾/交付有 commit = 归档
  (`task.py archive`,机制已被 49e223b 示范);验收未闭环 = 按普查矩阵
  回炉到对应批次任务(如 B2/B3/B4 → v1.2 补实现,v12-backlog 引用)。
- **保留不归档**:活跃 planning 档(v111-release、docs-truth、v112-desktop-batch、
  ci-gates、本档、grill-v112、v12-backlog、gap-census 未归档前)与
  in_progress 的 10-01-v10-release(发布线父档,终勾后归)。
- v01–v11 历史树(v01-skeleton 等 5 个 planning 态伞档):确认是记录型
  伞档后一并归档(它们的决议已沉淀在 research/ 与 memory)。

## Acceptance Criteria

- [ ] .trellis/tasks/ 根下只剩活跃档,其余全部入 archive/(清单记入日志)
- [ ] 每个归档决定带一行依据(验收勾选/commit 证据)
- [ ] 回炉项在目标批次任务里有反向引用
