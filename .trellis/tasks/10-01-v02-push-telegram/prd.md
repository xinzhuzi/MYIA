# 推送:Telegram + webhook 通道

## Goal

第二、三推送通道(grill Q5 定案:webhook 并入本任务):TG 是开源海外用户刚需;webhook 是 plugin 生态(myia-monitor 等)与 n8n 集成的地基。email 砍掉(社区贡献)。

## Requirements

- `channel: telegram`:Bot API sendMessage(凭据 `env:`/`keychain:`);模板复用 push 层 Jinja2 渲染
- `channel: webhook`:POST JSON 到用户端点(payload = 渲染数据 + 条目元数据);超时/重试可配;端点 URL 走凭据引用
- 与 route 分级、digest 聚合、AM/PM 防重发完全打通(通道无关)
- TG 消息长度超限自动分段(4096 限制)

## Acceptance Criteria

- [ ] 测试 bot 真实收消息一次(手动验证记录,凭据不进仓库)
- [ ] 分段与超限单测
- [ ] webhook 单测:mock HTTP server 校验 payload 结构、超时与重试路径

## Notes

- 填充 `src/myia/push/telegram.py` 壳(8 行)与 `webhook.py` 壳(8 行)
