# v1.1 release:发布物料收尾(1 条 low)

## Goal

清偿 low backlog(release 模块,素材编号 18,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),确认贡献文档与 CI 工作流措辞一致。

## Acceptance Criteria

- [ ] **18.** where: `CONTRIBUTING.md:95` — what: 「Run the suite exactly like CI does」措辞;ci.yml 已于 2026-10-02 改用 uv。修法:对照 `.github/workflows/ci.yml` 当前实际命令,确认 CONTRIBUTING 的测试命令与之一致;不一致则改 CONTRIBUTING,一致则记录确认即可关闭。

## Notes

- 纯确认型条目:先读 ci.yml 实际步骤,再核对 CONTRIBUTING:95 的命令;改动仅限文档。
- 回归:不需要跑全量测试(文档改动),但如顺手可跑 `uv run --no-sync python -m pytest` 确认零影响。
