# 游戏情报品类插件(plugins/games.yaml)

## 背景与来源

主人指示 2026-10-03:**「相关的游戏情报也要做」**。MYIA 现有情报覆盖羊毛/
信用卡/节点/代买/服务器/渠道/token/AI 信息(七大类 + channel,忠实移植
wf_crawl.py,金测集钉住,不扩表)以及 stocks(股票)/gpu-prices(显卡行情)
两个七类外品类插件——游戏情报是同类缺口,走**品类插件**路线,不动 builtin 表。

技术设计(选型证据/字段映射/决策 D1-D6/风险矩阵)与执行计划已另档:
`design.md` / `implement.md`,本档只锁需求与验收。

## Goal

新增官方品类插件 `plugins/games.yaml`:游戏**折扣、限免/喜加一**情报的
采集→分类→去重→推送全链,对齐 stocks/gpu-prices 先例(12 段 schema
显式声明),v1 收两个实测稳定的官方 API 源。

## Requirements

1. **插件清单**:`plugins/games.yaml`,12 段 schema 显式声明;命名直白
   (`id: games`,`name: 游戏情报`),inline 注释写清每段理由(对齐 stocks.yaml
   的「AI 写 YAML 参照」口径)。
2. **来源(v1 定稿推荐,实跑证据见 design §1)**:
   - Epic 每周限免官方 JSON(`freeGamesPromotions`,zh-CN)——✅ 实测 200;
   - Steam 每日特惠官方接口(`featuredcategories?cc=cn` specials)——✅ 实测 200;
   - Reddit r/GameDeals `.json` **剔除**(honest UA 返回 HTML,骗头违礼貌采集原则);
   - 中文 L2 源(什么值得买游戏区/小黑盒)未探,留 backlog。
3. **url 构造前置(D1)**:两源响应均无页面 URL(只有 slug/appid),需
   `extract.url_template` 微扩展(可选字段,`{field}` 占位,提取后渲染;
   与 url 字段二选一)——正面收口 stocks 记录过的 schema 缺口;向后兼容,
   旧 yaml 零影响。主人不批动 schema 则退 D2 占位方案(链接体验残缺)。
4. **分类**:`classify.builtin: false` + 自定义规则(两源同名归一化字段,
   规则求值对缺字段整条让路——见 design §1.3):
   - 限免:`final_price == 0 or discount_pct >= 100` → tag `限免`;
   - 大折扣:`discount_pct >= 50` → tag `半价+`。
5. **去重**:dedup key = `{url}`(**稳定键**:槽位抑制语义下每天每槽最多
   重推一次;且 baseline 价格历史按 dedup_key 存,带 `{date}` 会断链)。
   禁裸 `{title}`(schema 永拒)。
6. **推送**:feishu_card,`target: env:FEISHU_CHAT_ID`(凭据引用铁律);
   路由:限免 → immediate,其余 → digest。排程 `0 11 * * *` Asia/Shanghai
   (每日 11:00;调稀为主人旋钮)。
7. **价格基线(D4,进 v1)**:`baseline.enabled: true, fields: [final_price]`
   (两家单位同为分,vs_yesterday 给「今天又降了」);msrp 对照表**不进**
   (游戏 SKU 名对照不现实,史低判定交给折扣阈值+趋势)。
8. **联动**:品类 id `games` 自动出现在情报流品类选择器(feed-ux C8 过滤);
   yaml-editor 多一个真实品类文件可查/可编。

## 非目标

- 不改 myia-classifier 七大类关键词表(忠实移植红线,金测集钉住)。
- 不做游戏内数据(战绩/库存/账号交易),只做"省钱"情报;游戏资讯类
  (新游/版本)v1 不做——源与判重口径都未探,留 backlog。
- Windows/移动端适配不在本任务(随整体发布节奏)。

## 开放问题(开工前主人拍板;均带推荐)

1. 平台范围:**推荐 Epic+Steam 起步**(两官方 API 实测稳定);GOG/Switch/
   主机电商源未探,要则拆后续任务。
2. 资讯要不要:**推荐 v1 只做折扣/限免**(数值自解释、零 token);资讯类
   留 backlog。
3. 中英文源偏好:**推荐英文官方 API 先行**(实测稳定、无反爬);中文源
   探查后按需补。
4. 史低基线:**推荐 price 基线进 v1、msrp 对照不进**(D4)。

## Acceptance Criteria

- [ ] `uv run --no-sync python -m myia.cli run plugins/games.yaml --dry-run --json`
      退出码 0(load-check 过)
- [ ] `tests/test_plugins.py` 的 `OFFICIAL_PLUGINS` 显式加 `"games"`(全套
      电池:12 段显式/未知字段拒收/凭据引用/直连代理/route 两态/白名单语法/
      模板渲染/建管线/二跑去重;**注意是显式元组不是 glob,不加名只有
      「能装载」一项生效**)
- [ ] `_SNIPPETS` 录制样本两条(epic-free/steam-specials,裁剪自实跑响应),
      CI 零外网钉住提取形状
- [ ] 限免条目命中 immediate 路由、普通折扣落 digest(测试可见)
- [ ] url_template 扩展:`tests/test_schema.py` + `tests/test_fetch_base.py`
      新用例绿;`.trellis/spec/domain/yaml-schema.md` 与文档锁定面同步
- [ ] 全量 pytest 零失败(对照开工基线);ruff 对 HEAD 跟踪树零错
