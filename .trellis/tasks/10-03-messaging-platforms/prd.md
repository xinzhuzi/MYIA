# 消息平台:平台总览与接入态(UI 对齐 Hermes 消息平台页)

## 需求源

主人 2026-10-03 看 Hermes 消息平台截图(本会话经 local-ocr 还原)后质询「相关功能,一个都不落吗?」——逐项矩阵核对后确认四个出站兼容缺口,本任务补齐。蓝本:`~/.hermes/hermes-agent/apps/desktop/src/app/messaging/index.tsx`(1136 行)。

## 截图对照矩阵(证据:OCR 还原 + Hermes 源码)

| Hermes 功能 | 本任务 |
|---|---|
| 平台总览网格(约 20+ 平台卡片,含未连接的) | R1 |
| 筛选 tabs:全部/已连接/未启用 | R2 |
| 平台状态徽标(已禁用/需要设置/已连接) | R3 |
| 分平台凭据指南(快速设置/@BotFather 流程/设置指南链接) | R4 |
| 扫码配对、允许的用户 ID、Webhook secret、网关/RAM 状态栏、会话列表 | **不做**(定向出站决议,入站/双向域) |

## Requirements

1. **R1 平台总览网格**:消息屏上区升级为平台卡片网格——已实装平台(feishu/telegram)显示真实状态与目录条数;W2/W3 未实装平台(微信/企微/钉钉/ntfy/slack/discord/whatsapp/signal/line/matrix/mattermost/google_chat/teams/email/sms/irc/simplex/bluebubbles/qqbot/yuanbao/a2a/photon/homeassistant)显示「即将支持」灰卡(数据源:父任务 PRD 的全量清单,硬编码进前端常量即可,不做后端注册表)。
2. **R2 筛选 tabs**:全部 / 已连接 / 未启用 三档;「已连接」= 已实装且凭据可解析且最近一次目录刷新成功;「未启用」= 已实装但凭据缺失,或未实装平台。
3. **R3 平台状态语义**(不新增后端概念,纯前端派生):已连接(绿)/ 需要设置(凭据缺失,黄)/ 即将支持(灰,W2/W3)。状态来源:凭据探测(现有 secret.list 端点)+ 目录 last refresh 结果(channels.list 返回值)。
4. **R4 分平台凭据指南**:点开平台卡片显示出站凭据的获取步骤——feishu:如何拿 tenant_access_token(手工换取流程,指向飞书开放平台文档);telegram:@BotFather /newbot 拿令牌 + 从 @userinfobot 拿 chat_id(与截图同款流程,只取出站所需);内容写在 ui-src 本地常量(中文,直白),不引外链依赖。
5. 复用现有端点(channels.list/secret.list),新增派生聚合可在前端完成;确需新端点(如 channels.platforms 汇总)才动 desktop/entry.py,协议测试同范式。

## Acceptance Criteria

- [ ] 平台网格渲染:已实装 2 平台带真实状态;未实装平台灰卡「即将支持」;组件测试覆盖三态。
- [ ] 三档筛选正确分组(已连接/需要设置/即将支持)。
- [ ] feishu/telegram 凭据指南内容正确(与实际凭据 key 对应:FEISHU_BOT_TOKEN/TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)。
- [ ] vitest + 协议测试全绿;旧消息屏功能(目录/别名/规则挑对象)零回归。
- [ ] 入站项零出现(不渲染扫码/用户 ID/webhook secret 任何元素)。

## 非目标

- 未实装平台的任何后端实装(仍按 W2/W3 波次)
- 入站/双向功能(决议未翻案)
