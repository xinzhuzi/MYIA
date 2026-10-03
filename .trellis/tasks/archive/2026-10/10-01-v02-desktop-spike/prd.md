# 桌面原型:Tauri + PyInstaller sidecar(产出=决策,非产品)

## Goal

〇.5 定调验证:v0.1 骨架按 Tauri 路线搭的假设成立吗?用最小原型实测,产出「继续 Tauri / 切 Flet」的书面结论。

## Requirements

- Tauri 2 最小壳:一个窗口 + 一段 Rust 胶水,调起 sidecar 执行 `myia run <yaml> --json`,结果展示
- Python 核心 PyInstaller 打包单文件二进制,作为 Tauri sidecar 嵌入
- 实测并记录:二进制体积(目标:整体安装包 <30MB 级)、冷启动耗时、Rust 胶水行数(预期 ≤十几行)、Windows/Mac 双端打包工作量
- 结论写入本任务 `research/`:满足预期→Tauri 继续;Rust 胶水成本超预期→切 Flet(不影响核心,规划定案的退路)

## Acceptance Criteria

- [ ] macOS `.app`/dmg 打包成功、安装启动、sidecar 调用往返
- [ ] 实测数据(体积/耗时/胶水行数)记录在 research/
- [ ] 书面决策:继续 Tauri 或切 Flet,给出依据

## Notes

- 本任务是 spike:**禁止在此阶段铺开写桌面产品代码**
- 参照:tauri-apps/tauri 官方 sidecar 示例(规划〇.5 模仿表)
- 本 spike 是 **v0.x 阶段桌面唯一交付**(grill Q11 定案):桌面/Web UI 正式版排 v1.1+,其任务树以本任务 research/ 的实测结论为输入

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 本任务定义『产出=决策,非产品』,三条 AC 的验收物在 .trellis/tasks/10-01-v02-desktop-spike/research/spike-report.md:实测数据齐全(.app 25MiB/dmg 20MiB、壳冷启动 144-242ms、sidecar 往返 305-324ms、main.rs 72 行、退出码 0/1/2/3 冻结二进制实测)=AC2;第一节书面决策『继续 Tauri』附依据与切换条件判定=AC3;第六节逐条自评含 .app/dmg 打包、dmg 挂载、安装启动、sidecar 往返=AC1(原始 desktop-spike/ 目录已被 desktop/ 正式化取代,build-sidecar.sh 与 fixture/ 延续存在);报告所述仓库级 gitignore 缺陷已核实修复(.gitignore:12 !src/myia/secrets.py,git check-ignore 确认不再忽略)。
