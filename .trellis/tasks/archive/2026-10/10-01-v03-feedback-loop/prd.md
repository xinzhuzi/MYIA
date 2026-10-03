# 反馈闭环:有价值/没价值 → 回写 → 调参

## Goal

AI-NATIVE 最便宜的闭环(规划 v1.7 定案,README 卖点):推送卡片的反馈按钮回写 SQLite,周期性调整 enrich prompt 与路由阈值。

## Requirements

- **分形态接收(grill Q7 定案,消解桌面收不到公网回调的矛盾)**:
  - 桌面形态:TG `getUpdates` 轮询接收 callback(无需公网)+ `myia feedback mark <条目> <good|bad>` CLI 手动标记
  - 服务端/compose 形态:飞书卡片回调端点(默认关闭;开启强制 token 鉴权 + 仅内网,与 ingest API 同策略)
- `feedback` 表:条目、判定、时间、渠道;与 items 关联
- 周期任务(随调度):统计负反馈 Top 类目/词 → 调整 watchlist.mute 词表权重与 enrich prompt 要点(简单统计起步,不引入学习库);调整历史可追溯
- `myia feedback list/stats/mark` CLI

## Acceptance Criteria

- [ ] TG callback(mock getUpdates)→ 入库 → stats 单测
- [ ] CLI mark → 入库单测
- [ ] 演示:一轮负反馈后同类条目下轮被降权(demo 级即可)
- [ ] 飞书回调端点默认关闭,开启时鉴权校验单测(仅服务端形态交付)

## Notes

- 桌面 UI 里的反馈按钮属桌面正式版(排期见 v1.0 发布形态决策);本任务交付 CLI / TG / 服务端三路

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 四条 AC 均有单测证据:跑 `uv run --no-sync python -m pytest tests/test_feedback.py -q` → 76 passed。AC1=test_mock_getupdates_ingest_stats_end_to_end(TG mock getUpdates→入库→stats);AC2=test_cli_mark_by_id_saves_feedback/test_cli_mark_by_dedup_key_url;AC3=test_one_round_negative_feedback_downweights_next_round(一轮负反馈同类降权 demo);AC4=test_default_config_is_disabled/test_disabled_handler_answers_404+token 401 系列(飞书端点默认关、开启强制鉴权)。`uv run --no-sync myia feedback --help` 确认 mark/list/stats 三子命令;src/myia/feedback/ 为三分形态接收实现。
