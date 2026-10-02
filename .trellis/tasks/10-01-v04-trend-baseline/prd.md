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
