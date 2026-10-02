# Agent Skill 发行:SKILL.md

## Goal

第一发行形态(规划〇定案):把「AI 读规范 → 写 YAML → 自诊断」做成可安装的 Agent Skill,装进 Claude Code / Cursor 等。

## Requirements

- `skill/SKILL.md`:插件规范速查(12 节 schema 语义+缺省值)、`myia init` 结构化输出说明、写源工作流(需求→YAML→myia test→run)、自诊断流程(doctor JSON→修复动作)、真实源 URL 情报(L1/L2/L3 选择经验)
- 与 docs/write-a-plugin.md 单一事实源:文档站与 SKILL.md 同源生成或互相引用,不许两份漂移
- 安装方式:复制进平台的 skill 目录(SKILL.md 通用标准,验收目标 Claude Code / Cursor);**不专门适配任何单一 agent 框架**(含 Hermes/NousResearch——主人 2026-10-01 定调:不确定有帮助就不管),若对方兼容 SKILL.md 属顺带收益
- 示例演示对话/脚本进 docs

## Acceptance Criteria

- [ ] 新 agent(无本仓库上下文)按 SKILL.md 写出新品类 YAML 并 run 成功(录屏/日志为证)
- [ ] SKILL.md 中每个 schema 字段与代码 schema.py 一致(一致性测试或生成式校验)

## Notes

- 填充现有 `skill/SKILL.md`(骨架已有)
