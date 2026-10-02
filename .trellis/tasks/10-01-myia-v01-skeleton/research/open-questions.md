# Open Questions 决议记录(2026-10-01)

v0.1 父任务 PRD 提出的 4 个 Open Questions,主人答复如下:

## 已解决

| # | 问题 | 答复 |
|---|------|------|
| 1 | 「X聚合」(L1 引擎示例)指哪个聚合 API 服务? | 就是 X 平台本体:`https://x.com/`。规划能力矩阵里它作为 L1「公开 JSON API」示例;但实战对标一节明确 X→L6 或 cookie 池——L1 直连仅适用于 X 的公开/轻量端点,不作为 v0.1 验收源(见 v01-engine-l1-l2 PRD Notes) |
| 2 | TapNow(L2 实例)是哪个站? | `https://app.tapnow.ai/`。规划 L2「纯 JS 渲染壳页」的实战例子;定为 v02-engine-crawl4ai 的 JS 渲染验证源(见该 PRD 验收标准) |

## 待定

(无——2026-10-01 grill 第一轮后 4 个 OQ 全部关闭;OQ4 决议:Tauri spike 保持 v0.2。grill 十项决议见 [grill-round1-decisions.md](./grill-round1-decisions.md))

## 追加决议(同日)

| # | 问题 | 答复 |
|---|------|------|
| 3 | Hermes 的 skill 安装规范在哪? | Hermes 是开源 AI agent 框架(主人描述;推断为 NousResearch/hermes-agent,主打从历史轨迹提炼可复用 skill,中文社区有「橘皮书」alchaincyf/hermes-agent-orange-book)。主人定调:**不知道对本项目有没有帮助,没帮助就不在意**。决议:MYIA 的 SKILL.md 按通用文件标准发行,不专门适配任何单一 agent 框架(含 Hermes);能装进 Claude Code/Cursor 即为 v0.3 验收,其他 agent 兼容属顺带收益。v03-agent-skill 与 v0.3 父任务 PRD 已同步改 |

## 备注

- 本记录与各 PRD 同步更新;规划原文(`LOCAL-NOTES.md 索引的规划文档(本地)`,仅本地)未改动,如需把答案回写规划 v1.8,由主人决定。
