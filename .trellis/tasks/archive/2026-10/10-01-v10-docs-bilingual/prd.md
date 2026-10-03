# 双语文档:zh/en

## Goal

开源项目门面(规划第四节 docs/):3 分钟上手、插件开发指南、schema 参考,双语文档站。

## Requirements

- `docs/zh/` + `docs/en/` 内容对齐:getting-started、write-a-plugin(与 SKILL.md 同源)、schema 逐节参考、FAQ(反爬边界/伦理:robots 尊重、真人验证不碰)
- 文档站生成选型(mkdocs-material 或纯 markdown)design 定;README 双语入口
- 文档即 AI 输入:结构清晰、示例完整可复制运行

## Acceptance Criteria

- [ ] 双语全部页面与代码现状一致(示例经 CI 或手动验证)
- [ ] 新手 30 分钟跑通任一品类(真人验证记录)

## Notes

- 填充现有 docs/zh/getting-started.md、docs/en/getting-started.md、docs/write-a-plugin.md 骨架

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: 实跑 uv run --no-sync python -m pytest tests/test_skill_doc.py -q → 24 passed;uv run --no-sync myia --version → myia 1.1.1;docs/zh/getting-started.md:17 现写 '# 输出 myia x.y.z(x.y.z 为实际安装版本)'、docs/en/getting-started.md:21 'prints myia x.y.z (the installed version)',占位符消除版本漂移;grep '0\.1\.0' 全 docs/README/skill 零命中(AC1 修复已验证)。

遗留(需主人手动完成):
- AC2 新手 30 分钟跑通任一品类的真人验证记录仍缺(.trellis/tasks/10-01-v10-docs-bilingual/prd.md:16 仍未勾选)——需主人实测并把记录写入任务日志(真人测试类,需主人)
