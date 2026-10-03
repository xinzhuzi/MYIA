# v1.1 打磨:low backlog 清偿

## Goal

三轮深度评审累计 129 条发现,high/medium 全部已修;本任务清偿余下 18 条 low。
素材(逐条 where/what/修法提示):`.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`(2026-10-02 主会话整理自收官报告)。

## Requirements

按模块拆 6 个子任务,18 条全部归档进对应子任务的验收清单:

| 子任务 | 模块 | 条目(素材编号) |
| --- | --- | --- |
| 10-02-v11-low-docs-drift | docs / 文档漂移 | 1-6 |
| 10-02-v11-low-plugins | plugins / 插件包 | 7-8 |
| 10-02-v11-low-engines | engines / 引擎 | 9-10 |
| 10-02-v11-low-feedback | feedback / 反馈闭环 | 11-13 |
| 10-02-v11-low-baseline-aggregate | baseline+aggregate / 基线与事件聚合 | 14-17 |
| 10-02-v11-low-release | release / 发布物料 | 18 |

- 约束:仓库是三轮评审后的完成态(1214 tests 绿);除各子任务 PRD 授权的修复点外不改任何文件;严禁 git 操作、改 pyproject.toml/uv.lock、uv add/pip install。
- 红线:凭据/内网地址/私有系统名零入库;做不了的(如 demo 视频需主人录制)如实记 openIssues,不造假。

## Acceptance Criteria

- [ ] 6 个子任务全部完成并归档,18 条 low 逐条闭环(修掉或在子任务 openIssues 中如实说明原因)
- [ ] `uv run --no-sync python -m pytest` 全量绿(清偿后回归)
- [ ] 涉及文档/注释的修改与代码实际行为一致(逐条对照素材的 where/what)

## Notes

- 每个子任务挂 implement/check 上下文:spec 三件(python/index.md、domain/yaml-schema.md、domain/security-baseline.md)+ 素材文件本身。
- 素材编号(n)在子任务 PRD 中沿用,便于对账。
