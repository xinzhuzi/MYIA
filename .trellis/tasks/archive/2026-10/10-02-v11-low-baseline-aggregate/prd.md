# v1.1 baseline+aggregate:基线与事件聚合修正(4 条 low)

## Goal

清偿 low backlog(baseline+aggregate 模块,素材编号 14-17,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),披露隐性契约、修正截断预算与文档时序、清理死代码。

## Acceptance Criteria

- [ ] **14.** where: `src/myia/push/templates.py:462` — what: vs_msrp 钉死读 `price` 字段名,其他数值字段的品类恒空串。修法:至少 docstring + 中双语文档披露该契约(只认 price 字段)。
- [ ] **15.** where: `src/myia/push/telegram.py:163` — what: 「另见 N 源」截断路径行长可超自声明的 1024 上限 6 字符(预算未计后缀)。修法:预算计入后缀长度;补边界测试(满长正文 + 后缀 ≤1024)。
- [ ] **16.** where: `src/myia/enrich/aggregate.py:417` + `src/myia/enrich/__init__.py:149` + `src/myia/enrich/errors.py:3` — what: EventAggregator/LLMEnricher 的 Raises 文档声称缺依赖是构造期失败,实际懒加载、首调用才触发。修法:同家族 docstring 一并改为首调用触发。
- [ ] **17.** where: `src/myia/enrich/aggregate.py:140` — what: `AggregateOutcome.to_dict` 是死代码且 docstring 虚构消费方(run stats/doctor 均不消费)。修法:删除;若保留则接到真实消费方并修 docstring。

## Notes

- 条目 15/17 动代码,条目 14/16 动文档;条目 17 删代码前先全仓 grep 确认零调用。
- 回归:`uv run --no-sync python -m pytest` 全量绿(含新增边界测试)。

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 条目 14:templates.py:455-458 docstring 披露 vs_msrp 钉死 price 字段 + docs/zh/schema.md:265-266、docs/en/schema.md:287-289 双语披露;条目 15:telegram.py:165-177 截断预算计入「…等 N 源」完整后缀(:173 `MAX_ITEM_LINE_LENGTH - len(suffix)`),边界测试 tests/test_push_channels.py:559 test_item_also_line_truncation_reserves_full_suffix_width(该文件 36 tests 我实跑绿);条目 16:aggregate.py:411-415、enrich/__init__.py:165-171、errors.py:8-10 三处 Raises 均改「缺 openai 包在首次 LLM 调用时浮现,非构造期」;条目 17:AggregateOutcome.to_dict 已删(grep 'def to_dict' 零命中,aggregate.py:110-112 docstring 记录删除理由「曾虚构消费方且全仓零调用,v1.1 已删除」);test_aggregate+test_enrich 我实跑 108 passed。
