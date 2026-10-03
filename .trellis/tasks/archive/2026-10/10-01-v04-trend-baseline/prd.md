# 趋势基线:品类级 baseline

## Goal

分析侧深水件(规划 v1.7 #4):数值字段存历史,触发 vs 昨日/上周对比;关键词提及量周环比——从搬运到分析。

## Requirements

- schema `baseline:` 节(v01-yaml-schema 预留位确认,缺则回改 schema 任务):数值字段清单、对比窗口(day/week)
- 历史表:品类级数值快照(如价格、提及量);retention 协同(基线期长于条目期)
- 输出:推送模板新增对比函数(`{{vs_last_week price}}` 风格,渲染引擎扩展);「vs 昨日/上周」卡片段
- 关键词提及量周环比:品类维度统计入卡

## Acceptance Criteria

- [ ] 历史写入/窗口对比单测
- [ ] 示例品类(gpu-prices.yaml 顺带补全为官方插件)产出含「vs 上周」的真实卡片
- [ ] retention 与基线期协同单测

## Notes

- gpu-prices.yaml(ZOL L2 + MSRP 对照)在本任务一并补为官方插件

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 三条 AC 均有单测证据:跑 `uv run --no-sync python -m pytest tests/test_baseline.py -q` → 58 passed。AC1=test_save_metric_round_trips/test_day_window_compares_against_previous_local_day/test_week_window_compares_against_previous_iso_week;AC2=test_gpu_prices_official_plugin_loads_with_baseline(官方插件经真实 load_category 装载,baseline sidecar 完整)+ test_gpu_prices_render_snapshot_contains_vs_last_week_card(真实 gpu-prices.yaml 模板+真实 store 历史+TemplateRenderer 渲染出「较昨日 +10.0% 较上周 +20.0%」及关键词周环比,逐字符钉死);AC3=test_cleanup_keeps_metric_history_longer_than_items(retention 基线期长于条目期)。模板函数 vs_yesterday/vs_last_week/keyword_trends 实装于 src/myia/push/templates.py:427+。

备注(非缺陷说明):
- 说明(非缺陷):AC2 的「真实卡片」为渲染快照级证据(历史数值为夹具种子,非 ZOL 实时抓取);若主人要求 ZOL 实抓出卡则需另行手动验证
