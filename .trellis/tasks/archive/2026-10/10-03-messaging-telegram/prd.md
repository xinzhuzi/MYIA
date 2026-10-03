# 消息平台:Telegram 适配器(被动目录+定向发送)

## Goal

Telegram 平台接入 messaging-core 引擎。蓝本:`~/.hermes/hermes-agent/plugins/platforms/telegram/`。

**前置**:messaging-core 已收口(`SendContext.target`、`PLATFORMS`、目录契约就位);本任务在 core 之后 start。

## 现状底子

- `TelegramChannel` 已有 Bot API sendMessage(4096 自动分段);`TelegramFeedbackPoller` 已在桌面形态轮询 `getUpdates`——被动目录的现成数据源。

## Requirements

1. **被动目录积累**:feedback 轮询看到的每个 `chat`(id/type/title 或 username/first_name)记入 `ChannelDirectory`(platform="telegram");Telegram Bot API 无「列出会话」能力,这是 Hermes 同款约束,不是缺陷。
2. **直达与手动补录**:显式形态 `telegram:12345` / `telegram:@username` 不经目录直达(Hermes telegram 解析器同款);别名文件手工登记 chat_id(Hermes 同语义)补友好名,`targets: ["telegram:12345"]` 裸 id 也始终可用。
3. **定向发送**:`SendContext.target.chat_id` 优先;4096 分段逻辑复用。
4. **注册**:`PLATFORMS["telegram"] = TelegramChannel`;`supports_targeting` 翻 True。

## Acceptance Criteria

- [ ] 单测(httpx mock):getUpdates → 目录 merge(title/username 映射、私聊与群各自 type)、定向发送目标覆盖、分段不回归。
- [ ] 真机冒烟:给机器人发一条消息 → 目录出现该会话;按 chat_id 定向推送收到。
- [ ] 旧配置行为不变(回归测试)。

## 非目标

- 主动发现(Telegram API 不存在该能力)
- inline keyboard 反馈按对象归属(父任务已划出站+对象级反馈之外)
