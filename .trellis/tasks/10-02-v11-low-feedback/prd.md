# v1.1 feedback:反馈闭环加固(3 条 low)

## Goal

清偿 low backlog(feedback 模块,素材编号 11-13,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),加固反馈闭环的健壮性与测试覆盖。

## Acceptance Criteria

- [ ] **11.** where: `src/myia/push/feishu_callback.py:295` — what: 非 ASCII token 触发未处理 TypeError(hmac.compare_digest 仅收 bytes/ASCII str)。修法:比较前先 encode 成 bytes,失败时回结构化 401 而非 500/裸异常;补非 ASCII token 的测试。
- [ ] **12.** where: `src/myia/pipeline.py:2002` — what: 多品类常驻进程各起 getUpdates 轮询同一 bot token → Telegram 409 竞争。修法:至少文档警示该约束;理想为跨进程互斥/单例说明。
- [ ] **13.** where: `src/myia/pipeline.py:2016` — what: `_feedback_poll_loop` 常驻轮询循环零测试(offset 书签/store 复用/异常隔离是唯一常驻接线)。修法:补测试覆盖 offset 推进、store 复用、单轮异常不终止循环。

## Notes

- 条目 11/13 动代码与测试;条目 12 最低要求是文档,若实现互斥属加分项。
- 回归:`uv run --no-sync python -m pytest` 全量绿(含新增测试)。
