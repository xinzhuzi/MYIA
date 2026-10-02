# 推送:飞书卡片 + 阈值分级路由

## Goal

push 层:模板渲染 + 阈值分级路由(打完分之后怎么办)+ 飞书卡片通道;另带 stdout 调试通道。

## Requirements

- `push.route[]` 阈值分级路由(v1.7 定案,生产「只推白给型」语义沉淀):
  - `when: "score >= 8"` → `mode: immediate`(立即推)
  - `when: "score >= 5"` → `mode: digest`(进每日摘要,按 AM/PM 槽位发)
  - `when: "score < 5"` → `mode: archive`(只入库不推)
  - when 表达式与 classify 同一套安全求值器;无 route 时缺省 immediate(兼容简单用法)
- **v0.1 无 score 时的缺省分级(grill Q1 定案)**:按大类映射缺省 route——羊毛/节点/代买 → immediate;AI资讯/服务器/token/信用卡 → digest;品类 YAML 可覆盖此映射;v0.2 LLM score 回填后 score 路由优先于大类映射
- digest 发送:按 schedule 槽位(AM/PM)聚合当日 digest 池,槽位触发时统一发一张摘要卡;与 AM/PM 防重发共享注册表
- 模板渲染:**Jinja2**(grill Q2 定案,进核心依赖)+ SandboxedEnvironment 沙箱;规划示例模板的 Handlebars 语法(`{{#each}}`)由本任务改写为 Jinja2 语法并同步 PRD/文档示例
- `feishu_card` 通道:飞书 open 平台卡片消息;凭据 `env:FEISHU_*`;卡片样式对齐生产 card.json 结构(迁移包 wf-crawl/card.json,仅本地参考)
- `stdout` 通道:结构化输出(调试与 --dry-run、CI 用)
- 无 score 且分类未命中任何大类:保守缺省 digest(不打扰)

## Acceptance Criteria

- [ ] route 三分支单测 + 大类缺省映射单测 + 未命中保守缺省单测
- [ ] 模板渲染快照测试覆盖规划 stocks.yaml 示例模板
- [ ] 飞书沙箱/测试群真实收卡一次(手动验证记录到任务日志)
- [ ] digest 聚合:同槽位多条目合并一张卡

## Notes

- 填充 `src/myia/push/feishu_card.py`(13 行壳)、`push/__init__.py` 路由逻辑
- TG / webhook / 邮件通道是后续版本;通道接口按可扩展设计
