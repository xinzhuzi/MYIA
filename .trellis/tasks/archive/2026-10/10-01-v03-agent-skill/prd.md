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

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC2 有硬证据:跑 `uv run --no-sync python -m pytest tests/test_skill_doc.py -q` → 24 passed(SKILL.md 字段表/枚举表与 schema.py 双向逐项对照、YAML 示例经真实 load_category 装载、与 docs/write-a-plugin.md 互相引用、SKILL.md 引用的 myia 子命令/选项逐一存在于 CLI);skill/SKILL.md(26KB)实有 12 节速查+myia init+写源工作流+自诊断+L1-L6 引擎经验(§1-§7)。

遗留(需主人手动完成):
- AC1「新 agent(无本仓库上下文)按 SKILL.md 写出新品类 YAML 并 run 成功(录屏/日志为证)」仓库内无证据:docs/demo 的 transcript 只是 cat 仓库预置的 demo-news.yaml,docs/demo/video/myia-demo.mp4 为装配版(shot-list.md 中 S2「agent 跑 myia init」、S3「agent 生成 12 节 YAML」均标「☐ 待录」),workspace 日志亦无记录 —— 需主人用新 agent 会话实测一次并留录屏/日志
