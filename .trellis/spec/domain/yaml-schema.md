# 品类 YAML schema 硬规则

> schema 完备度 = 产品完备度 =「AI 写 YAML」卖点成立的前提。

## 十二节(权威示例:规划第三节 stocks.yaml,本地)

id/name、schedule+timezone、sources[](engine/url/method+post_body/headers/pagination/extract(+url_template)/rate_limit/proxy/retry/源参数)、watchlist、classify.rules、dedup.key、enrich、push[](channel/target/route/template)、storage

## 铁律

1. **每节必须有明确语义与缺省值**——没有缺省值的节,对 AI 生成就是不可用节
2. **未知字段 fail-fast**:加载期报错(字段路径+原因),不许静默忽略
3. **凭据禁明文**:只许 `env:VAR` / `keychain:name` 引用;疑似凭据键(Cookie/Authorization/Token)无前缀 → LoadError
4. **永不标题指纹**:去重键只用 URL 或组合键模板(dedup.key)
5. `engine: auto` 降级链顺序固定:L1 direct_api → L2 static_html → L3 crawl4ai → firecrawl(替代后端) → L4 scrapling → L5 stealth_browser → L6 llm_browser;成功选择回写 SQLite engine_hints,**不回写用户 YAML**
6. 推送语义分层:route 管「推不推」(score 分级),AM/PM 槽位管「发没发过」(防重发)——两层正交
7. 无 extract 时 L3 自动结构化兜底;scroll 翻页仅 L4+ 支持
8. **extract 条目 URL 二选一**(10-03-games D1):`list`/`json_path` 的
   `fields` 含 `url` **或** 配 `extract.url_template`——`{field}` 纯占位
   (至少一个,`_PLACEHOLDER_RE` 同款校验),**占位符必须在 `fields` 字段名
   内(装载期交叉校验,拼错即拒)**,提取出口逐条渲染并填入 `url`;运行期
   单条目占位缺「值」→ url 置空串,该条目在管线 fetch 阶段按 `invalid_item`
   记失败后丢弃(**不带坏链接入库**);都有 = `url` 字段胜出、模板静默不用;
   `item` 单页源禁配(条目 url 即请求 URL)
9. **rss 提取**(10-03-news-rss):`extract.type: rss` 只挂 `static_html`
   文本通路(feedparser 条目映射;CSS 硬接 RSS 因 `<link>` void 元素拿不到
   条目 url = 静默零产出);`direct_api` 保持 JSON-only(引擎层拒,auto 链
   正确降级)。`fields` **值 = feedparser entry 属性白名单**
   {title,link,published,updated,summary,author}(键=归一字段名,如
   `url: link`),**拼错装载即拒**(`invalid_rss_field`);**必含 `url`**
   (映射 `link`,去重键根基);`item` 选择器与 `url_template` 均与 rss
   互斥;条目缺属性逐条省略(同 json_path 语义);summary 是 CDATA HTML,
   模板直出刷屏——官方插件模板只用标题+链接+日期(官方示例
   `plugins/news.yaml`,fixture `tests/fixtures/news-gcores-rss.xml`)

## 变更纪律

- 改 schema 必须同步:schema.py、SKILL.md、docs、plugins 示例——四处一处都不能漂
