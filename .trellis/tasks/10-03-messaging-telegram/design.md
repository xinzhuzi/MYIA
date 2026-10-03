# Design:messaging-telegram — 被动目录 + 定向发送

上游蓝本:`~/.hermes/hermes-agent/plugins/platforms/telegram/`(事实轮核:无目录/联系人 API,条目唯一来源是入站回填——MYIA 的等价物是 feedback 轮询)。前置:messaging-core 契约。

## D1:直达解析

`telegram:<纯数字>`(私聊/群/频道 chat_id)与 `telegram:@username`(公开用户名/频道)不经目录,挂 core `parse_direct_ref` 钩子(Hermes telegram_ids 同款形态)。注意:@username 仅对公开 username 有效,私聊必须数字 id——报错文案要讲清这一点。

## D2:被动积累 = poller 挂 sink,不改轮询骨架

`TelegramFeedbackPoller`(push/telegram_feedback.py)增可选构造参数 `on_chat: Callable[[ChannelEntry], None] | None = None`;轮询循环里每条 update 的 effective chat 归一为 `ChannelEntry` 后回调:

- group/supergroup:`chat.title` 为名,type=group
- private:`chat.first_name [+ last_name]` 为名,type=dm;有 username 时记进 name 备注字段(ChannelEntry.name 保留 first_name,username 进不了现有字段就拼进 name——不扩 schema,实现时取简)

回调由 poller 实例化处(desktop serve 路径)注入 `ChannelDirectory.merge("telegram", ...)`;CLI/server 形态不跑轮询,目录只靠手工别名 + 直达 id(合法态,不报错)。poller 现有解析/去重/错误路径零改动——sink 是旁路观察者,sink 抛错只记日志不中断轮询。

## D3:定向发送

`supports_targeting = True`;`send()` 内 `context.target.chat_id` 优先、退回 legacy `_resolve_chat_id()`;4096 分段(split_message @ telegram.py:70-95)对每目标各自生效,逻辑零改动。

## D4:错误语义

投递 403(chat 不存在/bot 被踢)按 core Q3 分类标 dead;429/超时为瞬态不标。目录积累无错误面(sink 旁路)。

## 兼容与回滚

- 不配 targets 零变化;poller 不注入 sink 时行为与现网逐字节一致。
- 回滚 = revert telegram.py / telegram_feedback.py 增量。
