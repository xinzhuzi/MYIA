# v1.1 桌面版与打磨(总览)

## Goal

v1.0(CLI+Docker+Skill 三形态)已发布;v1.1 按 grill Q11 定案交付**桌面正式版**,并完成配套基建与 backlog 清偿。本任务只做排序与总控,不承载实现。

## 子任务与建议顺序

| 序 | 任务 | 优先级 | 说明 |
|---|---|---|---|
| 0 | v11-desktop-app | **P1(头号)** | 桌面正式版:五大界面+updater+打包流水线;地基=desktop/(spike 已验证 Tauri 路线) |
| 1 | v11-plugins-source-arch | P1 | 插件源码化转向(桌面配套基建,osint 样板先行) |
| 2 | v11-skill-install | P2 | Agent Skill 安装通路(可与 0/1 并行) |
| 3 | v11-repo-hygiene | P2 | 图标去重+夹具合一(随手级,任意时点) |
| 4 | v11-low-backlog(6 子) | P2 | 18 条 low 清偿(注意:low-plugins 的 README 修复并入任务 1 执行) |
| ✅ | v11-packages-layout | 已完成(2026-10-02) | packages 套娃拆除 |

## 阶段验收(v1.1 Done 定义)

- [ ] 桌面正式版:dmg 下载即用(零 Docker/Python 要求),五界面可用,updater 生效
- [ ] 插件层:osint 样板进程内可用零 docker;plugins/ 无 docker 内容
- [ ] `myia skill install` 装进至少一个真实 agent 并演示 AI 写插件
- [ ] backlog 18 条清零或显式降级;全量测试绿;CI/镜像绿
- [ ] 需主人手动项(真实凭据类)单独列出

## Notes

- 与规划文档的关系:〇.5(桌面形态/技术选型)、grill Q11(桌面=v1.1+)、v02-desktop-spike(实测数据)是本阶段依据;desktop/ 是打包出处(2026-10-02 主人定)
