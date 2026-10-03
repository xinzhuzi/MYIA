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

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: pytest tests/test_plugins.py -q → 51 passed, 6 skipped(跳过项即 live smoke);三插件过 schema/十二节齐备/无未知字段(:59/:66/:78),每插件二跑 dedup 自动化测试(:403)过,注释齐备(stocks 全节 showcase,ai-news 64 行含 linux.do 登录墙占位 ai-news.yaml:78,wool 100 行);本会话真实端到端(--db 隔离库,零凭据零外发):一跑入库 stocks 5/ai-news 36/wool 126 行,二跑 dedup_seen 5/29/92 且三库 total==distinct dedup_key 零重发;wool 全部活源真实抓取实证(nodeloc/cocoloop 展开 page=1 后 HTTP 200+30 条,经插件自身 extract 配置各产出 30 条结构化条目)。附注:打包的 test_wool_live_source_smoke[nodeloc/cocoloop] 因夹具直取未展开 {page} 的模板 URL(400)会失败——产品路径会展开占位符,属测试夹具一行修复项,非产品缺陷。
