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

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: uv run --no-sync python -m pytest tests/test_push_channels.py -q → 36 passed;分段与超限=test_split_message_*(4096 上限)+ test_telegram_send_auto_splits_message_over_4096_chars;webhook payload 结构=test_webhook_send_posts_payload_with_render_data_and_item_metadata,超时与重试=test_webhook_send_timeout_exception_retries_then_raises_http_error + test_webhook_send_retries_transient_503_then_succeeds + test_webhook_send_exhausts_retries_on_permanent_500;通道无关打通=test_route_digest_and_slot_suppression_flow_through_new_channels(route 分级/digest/AM-PM 防重发)。

遗留(需主人手动完成):
- AC『测试 bot 真实收消息一次(手动验证记录)』未做:任务目录与 workspace 无验证记录,需主人持真实 TG bot 凭据收消息验证一次,凭据不进仓库。
