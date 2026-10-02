# v1.1 plugins:插件包修正(2 条 low)

## Goal

清偿 low backlog(plugins 模块,素材编号 7-8,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),修正插件包内误导性注释与照抄即错的命令。

## Acceptance Criteria

- [ ] **7.** where: `plugins/monitor.yaml:30` — what: 源 URL 注释宣称「resolved from plugin.modes.remote」,无此机制(URL 实为两处手工维护),误导生成配置的 agent。修法:注释如实描述两处手工维护的事实。
- [ ] **8.** where: `plugins/myia-douyin/README.md:20`、`plugins/myia-monitor/README.md:18`、`plugins/myia-osint/README.md:21`、`plugins/myia-proxy/README.md:18` — what: local 模式命令缺 `cd plugins/<id>`,在仓库根照抄执行 compose 报 no configuration file(myia-maxun 已有 cd)。修法:四处统一补 `cd plugins/<id>`,与 myia-maxun 对齐。

## Notes

- 条目 8 执行前先核对四个 README 的实际行号(素材为建档时行号,可能有偏移)。
- 回归:`uv run --no-sync python -m pytest` 全量绿(改动均为文档,应零影响)。

> **2026-10-02 依赖批注**:README 的 cd 修复项将在 v11-plugins-source-arch 重写 README 时一并完成(该任务为父方向);本任务执行时先核对哪些已随之消失。
