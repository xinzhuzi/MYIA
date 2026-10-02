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
