# 事件聚合:多源同事件合并

## Goal

分析侧深水件(规划 v1.7 #5):URL 去重拦不住多源同事件——不合并会一事件推五遍。合并为单事件卡,推送带「另见 N 源」。

## Requirements

- 两级判重:标题相似度粗筛(本地、零 token:分词/字符 shingle 相似度)+ 同时间窗内 LLM 判重精筛(并入 enrich 批量与预算护栏)
- 聚合窗口与阈值可配(品类级 `aggregate:` 节,缺则回改 schema 任务)
- 推送:合并单卡,主条目 + 「另见 N 源」列表;digest/immediate 均生效
- 与 dedup(同 URL/组合键)正交:先 dedup 后聚合

## Acceptance Criteria

- [ ] 3 源同事件夹具 → 1 卡(单测)
- [ ] 相似但不同事件不误合并(精确率样例集)
- [ ] LLM 判重走缓存与预算(不重复计费)

## Notes

- prompt 进 enrich prompt 模板体系(可被反馈闭环调整)

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 三条 AC 均有单测证据:跑 `uv run --no-sync python -m pytest tests/test_aggregate.py -q` → 58 passed;并按节点复跑三验收测试全 PASSED(3 passed):AC1=TestPipelineAggregateStage::test_three_sources_same_event_merge_into_one_card(3 源夹具→1 卡,digest 路由亦生效 test_digest_route_renders_merged_card_too,飞书/TG 卡含「另见 N 源」test_feishu_builtin_card_contains_also_seen/test_telegram_builtin_message_contains_also_seen_line);AC2=TestTitleSimilarity::test_precision_sample_set_similar_but_different_events(精确率样例集)+ 管线级 test_similar_but_different_events_not_merged_in_pipeline;AC3=TestEventAggregator::test_cache_hit_never_rebills_same_pair(缓存命中不重复计费)+ test_exhausted_budget_degrades_to_no_merge + test_llm_dedup_reuses_shared_budget_with_scoring(与 enrich 共享预算池);与 dedup 正交由 test_dedup_first_then_aggregate_orthogonal 钉死。
