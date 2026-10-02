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
