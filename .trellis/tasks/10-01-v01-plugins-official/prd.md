# 官方插件:ai-news / wool / stocks

## Goal

按 12 节完整 schema 实例化三个官方插件 YAML——既是开箱即用的卖点,也是 schema 的端到端验收(每个真实源都是一次 schema 语义测试)。

## Requirements

- `plugins/stocks.yaml`:Yahoo chart API(L1,json_path 取数),symbols 列表(NVDA/AAPL/TSLA/0700.HK 等),交易日 cron
- `plugins/ai-news.yaml`:aihot.news(L2/firecrawl)+ 论坛源(linux.do/cocoloop 等,L2 起步,登录墙源留注释占位说明 v0.4 引擎可救)
- `plugins/wool.yaml`:linux.sb / bbs.bt.sb / nodeloc / cocoloop 等公开可抓源(L2);生产 URL 清单参照本地迁移包 `urls-*.txt`(**仅本地参照,YAML 里只放公开 URL**)
- 每插件完整走 schema:pagination / rate_limit / extract / dedup / push.route(至少 immediate+digest 两级)/ storage
- 现有 plugins/*.yaml 是早期占位,本任务按定稿 schema 重写;`monitor.yaml`/`credentials.yaml`/`gpu-prices.yaml` 属于后续版本(plugin 双模式 v0.3、aipocket 集成 v0.3+),本任务不动或仅留占位注释
- 敏感凭据一律 `env:` 引用;示例值用占位符

## Acceptance Criteria

- [ ] 三插件各自真实 run 产出 ≥1 条真实数据入库
- [ ] 每插件二跑:dedup 拦截,无重发
- [ ] 三插件 YAML 通过 schema 校验且无未知字段(互相校对 schema 完备度,发现缺口回改 v01-yaml-schema)
- [ ] 插件内注释解释每节用途(兼作 schema 文档与 AI 生成范例)

## Notes

- 现有 `plugins/ai-news.yaml` / `wool.yaml` / `stocks.yaml` 为占位,以本任务重写版为准
- 发现 schema 语义缺口时:**先回改 schema 任务再继续**,不允许插件里塞 schema 外的私货字段
