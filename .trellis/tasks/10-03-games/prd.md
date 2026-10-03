# 游戏情报品类插件(plugins/games.yaml)

## 背景与来源

主人指示 2026-10-03:**「相关的游戏情报也要做」**。MYIA 现有情报覆盖羊毛/
信用卡/节点/代买/服务器/渠道/token/AI 信息(七大类 + channel,忠实移植
wf_crawl.py,金测集钉住,不扩表)以及 stocks(股票)/gpu-prices(显卡行情)
两个七类外品类插件——游戏情报是同类缺口,走**品类插件**路线,不动 builtin 表。

## Goal

新增官方品类插件 `plugins/games.yaml`:游戏**折扣、限免/喜加一、游戏资讯**
三类情报的采集→分类→去重→推送全链,对齐 stocks/gpu-prices 先例(12 段 schema
显式声明)。

## Requirements

1. **插件清单**:`plugins/games.yaml`,12 段 schema 显式声明;命名直白
   (`id: games`,`name: 游戏情报`),inline 注释写清每段理由(对齐 stocks.yaml
   的「AI 写 YAML 参照」口径)。
2. **来源(设计期定,候选)**——CI 零外网,真实访问核实后才入清单:
   - Epic 每周限免:官方公开 JSON(`store-site-backend-static…/freeGamesPromotions`,
     direct_api L1);
   - Steam 特惠:appdetails `price_overview` / featured(direct_api);
   - Reddit r/GameDeals(`.json` 端点);
   - 中文源(什么值得买游戏频道/小黑盒等,static_html L2,反爬需评估)。
   - 首版建议收 2-3 个可稳定访问的源,选型记录进 design.md。
3. **分类**:`classify.builtin: false`(游戏标题不落七大类,开着只会误杀,
   同 gpu-prices 注释口径)+ 自定义 rules:限免/0 元 → tag `限免`;
   折扣力度阈值(如 `discount_pct >= 50`)→ tag `史低候选`。
4. **去重**:dedup key 禁用裸 `{title}`(schema 永拒);限免类用
   `{url}-{date}` 或促销周期键,普通折扣 `{url}`。
5. **推送**:feishu_card/telegram 模板;路由:限免/0 元 → immediate,
   普通折扣 → digest(既有 route 白名单语法)。
6. **基线(可选,v1 范围决策)**:对齐 gpu-prices 的 baseline 节——
   `fields: [price]` + day/week 窗口 + 史低对照表(类比 msrp)。
   要不要进首版留设计期。
7. **联动**:品类 id `games` 自动出现在情报流品类选择器(feed-ux C8 过滤);
   yaml-editor 多一个真实品类文件可查/可编。

## 非目标

- 不改 myia-classifier 七大类关键词表(忠实移植红线,金测集钉住)。
- 不做游戏内数据(战绩/库存/账号交易),只做"省钱+资讯"情报。
- Windows/移动端适配不在本任务(随整体发布节奏)。

## 开放问题(开工前问主人)

1. 平台范围:Epic/Steam/GOG/Switch/主机电商,各要不要?
2. 只要折扣/限免,还是也要新游/版本资讯?
3. 中英文源偏好?(英文源 API 稳,中文源阅读顺)
4. 史低价格基线进不进首版?

## Acceptance Criteria

- [ ] `myia run plugins/games.yaml --dry-run --json` load-check 通过
- [ ] 进 `tests/test_plugins.py` 参数化基线自动全套(12 段显式/未知字段拒收/
      凭据引用/直连代理/route 两态/白名单语法/模板渲染/建管线/两跑去重)全绿,
      CI 零外网(录制回放 fixture,对齐 `test_plugin_extract_matches_recorded_markup`)
- [ ] 限免条目命中 immediate 路由(测试可见),普通折扣落 digest
- [ ] 来源选型与放弃理由记录在 design.md(复杂度升级则按规矩补 design/implement)
