# v0.3 生态:Skill 发行 + 插件双模式 + Scrapling + 反馈闭环

## Goal

生态起步:Agent Skill 第一发行形态落地(SKILL.md),插件市场目录与双模式消解 Docker 矛盾,L4 引擎补全降级链,AI-NATIVE 最便宜的反馈闭环上线。

## 前置

v0.2 完成。

## 子任务地图

| # | 子任务 | 边界 |
|---|---|---|
| 1 | v03-agent-skill | SKILL.md 发行:LLM 拿到即会用 |
| 2 | v03-plugin-market | plugin local/remote 双模式 + 市场目录 |
| 3 | v03-engine-scrapling | L4 自适应反爬 |
| 4 | v03-feedback-loop | 有价值/没价值 → 回写 → 调参 |

## 阶段验收标准

- [ ] 新 agent 无上下文、仅凭 SKILL.md 写出一个新品类 YAML 并 run 成功(验收即演示)
- [ ] 无 Docker 的桌面用户可用 remote 模式插件;拔掉任意 plugin 核心流水线照常(铁律)
- [ ] nodeseek 一类基础盾源经 L4 跑通;auto 链四层
- [ ] 反馈按钮 → SQLite 回写 → enrich prompt/词表调整的链路可演示

## Open Questions

无。原「Hermes 安装规范」已决议(2026-10-01 主人定调):Hermes 是开源 AI agent 框架(推断为 NousResearch/hermes-agent),对本项目无硬依赖价值——SKILL.md 按通用标准发行,不专门适配,详见 v0.1 父任务 research/open-questions.md。
