# Design — 游戏情报品类插件(plugins/games.yaml)

> 事实基线:2026-10-03 本机实跑探源 + 源码核订(schema.py / fetch_base.py /
> custom.py / templates.py / test_plugins.py,行号为当日 HEAD)。所有引述
> 均可复核;探源原始响应存本任务 `evidence/epic-free.json`、
> `evidence/steam-featured.json`(implement 阶段的 fixture 从这两个文件裁剪)。

## 1. 来源选型(实跑证据矩阵)

| 候选 | 实跑结果 | v1 决定 |
| --- | --- | --- |
| Epic `freeGamesPromotions` 官方 JSON | ✅ 200(zh-CN 标题直出,12 elements);robots.txt 404=无限制 | **入选** |
| Steam `featuredcategories?cc=cn` specials | ✅ 200(specials.items 10 条,`discount_percent` 直出);robots.txt 不禁 `/api/` | **入选** |
| Reddit r/GameDeals `.json` | ❌ honest UA 返回 HTML(theme-beta 页)非 JSON;骗浏览器头属反检测范畴,违礼貌采集原则 | 剔除,记 backlog |
| 中文 L2(什么值得买游戏区/小黑盒) | 未探(反爬未知);两稳定 API 已满足 PRD「2-3 源」 | backlog |

两个入选源均为 `direct_api`(L1)+ `json_path` 提取,与 stocks 同型;
访问姿态:honest MYIA UA(stocks 的 Yahoo 教训——别伪装浏览器头),
`respect_robots: true` 保持缺省(两家 robots 实测均不拦 API 路径)。

### 1.1 Epic 字段映射(前缀 `$.data.Catalog.searchStore.elements[*]`)

| 归一字段 | json_path | 备注 |
| --- | --- | --- |
| title | `.title` | zh-CN |
| url_slug | `.urlSlug` | 构 URL 用;部分元素为 None(风险 R1) |
| final_price | `.price.totalPrice.discountPrice` | **单位=分**(decimals=2);限免=0 |
| original_price | `.price.totalPrice.originalPrice` | 分 |
| discount_pct | `.promotions.promotionalOffers[0].promotionalOffers[0].discountSetting.discountPercentage` | 仅当前促销存在;缺=无促销(逐元素提取缺字段省略,不错位) |
| price_text | `.price.totalPrice.fmtPrice.discountPrice` | "¥60.00" 直出,免模板换算 |

结构证据:price=`{"totalPrice":{"discountPrice":6000,"originalPrice":6000,"discount":0,"currencyCode":"CNY",…fmtPrice…}}`;promotions 分当前(`promotionalOffers`)与将来(`upcomingPromotionalOffers`,Ghostrunner 2 样本 20% 2026-12 起)。**upcoming 不进 v1**(记 PRD 开放问题④增强项)。

### 1.2 Steam featured specials 字段映射(前缀 `$.specials.items[*]`)

| 归一字段 | json_path | 备注 |
| --- | --- | --- |
| title | `.name` | |
| steam_id | `.id` | int;构 URL 用 |
| final_price | `.final_price` | **单位=分**(样本 1360=¥13.60,discount 90%) |
| original_price | `.original_price` | 分 |
| discount_pct | `.discount_percent` | int 直出(样本 90) |
| expire | `.discount_expiration` | unix 秒;模板显示用 |

### 1.3 为什么必须同名归一化字段(规则求值事实)

`myia_classifier/custom.py` 规则求值:**缺字段读 None;含 None 的运算 =
整条规则跳过(WARNING + 不命中)**。规则是品类级(不分源),所以两源
必须产出同名 `final_price`/`discount_pct`——规则在缺字段的条目上自然让路,
在有字段的条目上正常命中。

## 2. D 决策

### D1 url 构造:extract.url_template 微扩展(推荐,本任务内做)

两个入选源的响应里**都没有可点的页面 URL**(Epic 元素 `url` 恒 None,只有
`urlSlug`/`productSlug`;Steam 只有数字 `id`)。这正是 stocks.yaml 注释里
记录过的 open issue:「json_path cannot express "item URL = f(field)"」。
现状只有两条路:

- **D1(推荐)**:`ExtractConfig` 增可选 `url_template: str`(`{field}` 占位,
  与 dedup.key 同款迷你模板语义),提取出口统一渲染覆盖 url;
  `missing_url_field` 校验放宽为「url 字段或 url_template 二选一」。
  变更面:schema.py(~15 行)+ fetch_base 提取出口(~15 行)+ 单测;
  12 段公开契约不动(extract 是子节)。正面收口 stocks 记录的缺口,
  stocks 自己换装不在本任务(向后兼容,url 字段路径行为不变)。
  Epic:`https://store.epicgames.com/zh-CN/p/{url_slug}`;
  Steam:`https://store.steampowered.com/app/{steam_id}`(int 渲染成 str)。
- **D2(备选,主人不批动 schema 时)**:url=slug/appid 占位(stocks 先例),
  推送卡与 feed「打开原文」链接失效,G2 shell:allow-open(https scope)
  会拒开——缺口注记,链接体验残缺。

### D2 dedup.key = `{url}`(稳定键,不能带 {date})

- 抑制语义是**槽位窗口作用域**(日期+AM/PM;dedup.py:8)——`{url}` 键 =
  同一条目每天每槽最多重推一次,天然"日报"节奏,长促销次日可再浮现;
  stocks 注释「bare {symbol} would silence the plugin」指的是同槽内二跑,
  跨槽/跨日会重开。
- **baseline 的 metric key = dedup_key 优先,退回 url**(templates.py:127)。
  若 key 带 `{date}` 每天换键,价格历史永远只有一行,vs_yesterday 失效。
  → 稳定 `{url}` 是价格基线成立的硬前提。
- 连锁:`{url}` 依赖 D1(占位 slug/appid 也能当稳定键,D2 下 dedup 仍成立,
  只是链接烂)。

### D3 规则与路由(零 token 双漏斗)

- classify:`builtin: false`(游戏标题不落七大类,同 gpu-prices 口径)+ rules:
  - 限免:`final_price == 0 or discount_pct >= 100` → tag `限免`
  - 大折扣:`discount_pct >= 50` → tag `半价+`
- route(首匹配,未命中走缺省 digest——gpu-prices 先例):
  - `final_price == 0 or discount_pct >= 100` → immediate(喜加一即推)
  - 其余 → digest(每日 11:00 汇总卡)
- 规则文法核订:白名单 AST 支持比较/and/or/算术,函数 abs/min/max/round/
  len/int/float/str(custom.py:60-69);route.when 复用同一 Rule 引擎。

### D4 baseline:进 v1(fields=[final_price],msrp 不进)

- `enabled: true, fields: [final_price], windows: [day, week]`(缺省即此)。
  vs_yesterday(final_price) 给出「这游戏今天又降了」;两家单位同为分,
  字段可直接比。
- msrp 对照表不进:游戏 SKU 名对照表维护不现实(键=商品名子串),
  「史低」判定交给 `discount_pct` 阈值 + 价格趋势,足够。

### D5 排程与推送

- `schedule: "0 11 * * *"` + `timezone: Asia/Shanghai`:每日 11:00(AM 槽)。
  Epic 喜加一周四刷新、Steam 特惠每日轮换,每日一跑全覆盖;嫌吵调稀
  `0 11 * * 4,6` 是主人旋钮,不动结构。
- push:`feishu_card` + `target: env:FEISHU_CHAT_ID`(与 gpu-prices/stocks
  同款凭据引用;bot token 走渠道缺省 env:FEISHU_BOT_TOKEN)。
- enrich:`enabled: false`(零 token;游戏情报数值自解释,不需要 LLM 精评)。
- watchlist:keywords `[喜加一, 限免, 免费, 史低, 折扣, 白嫖]`,
  mute `[抽奖, 求购, 二手, 代练]`。**核订事实:enrich 关闭时 watchlist
  无降权作用**(pipeline.py:1667 只在 enrich 阶段消费),仅做提及量统计,
  不会误杀游戏名标题。

### D6 测试接入(修正 PRD 初稿口径)

- `OFFICIAL_PLUGINS = ("stocks", "ai-news", "wool")` 是**显式元组**
  (test_plugins.py:32)——全套电池(12 段显式/未知字段拒收/凭据引用/
  route 两态/白名单语法/模板渲染/建管线/二跑去重)要**手动加 "games"**;
  自动进门的只有 glob 装载测试(test_plugins.py:466)。
  (顺带观察:gpu-prices 至今不在元组里——不属本任务,记 gap 待主人定。)
- `_SNIPPETS` 内联录制样本(test_plugins.py:291)加两条:
  `(games, epic)` / `(games, steam-specials)`,JSON 从本机实跑响应裁剪
  (每源留 1-2 个元素,含一个限免形状、一个大折扣形状),断言首条
  url/title——CI 零外网铁律由此钉住。
- games 专属断言(对齐 test_stocks_uses_direct_api 先例):
  双源 direct_api+json_path;限免规则对合成限免条目命中 immediate。

## 3. 插件清单骨架(games.yaml 关键节)

```yaml
id: games
name: 游戏情报
schedule: "0 11 * * *"
timezone: Asia/Shanghai
sources:
  - name: epic-free                      # direct_api / json_path / url_template
    engine: direct_api
    url: "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN"
    extract: { type: json_path, url_template: "https://store.epicgames.com/zh-CN/p/{url_slug}", fields: {…1.1 表…} }
  - name: steam-specials
    engine: direct_api
    url: "https://store.steampowered.com/api/featuredcategories?cc=cn"
    extract: { type: json_path, url_template: "https://store.steampowered.com/app/{steam_id}", fields: {…1.2 表…} }
watchlist: { keywords: [喜加一, 限免, 免费, 史低, 折扣, 白嫖], mute: [抽奖, 求购, 二手, 代练] }
classify: { builtin: false, rules: [限免/半价+ 两条] }
dedup: { key: "{url}" }
enrich: { enabled: false }
baseline: { enabled: true, fields: [final_price] }
push:
  - channel: feishu_card
    target: env:FEISHU_CHAT_ID
    route: [限免 immediate,缺省 digest]
    template: |   # 标题/价格(优先 price_text,退 final_price/100)/折扣%/vs_yesterday(final_price)
storage: { retention: 90d, vacuum: monthly }
```

(12 段显式+inline 注释对齐 stocks「AI 写 YAML 参照」口径,成品以
implement 阶段为准。)

## 4. 风险与缓解

- **R1 Epic urlSlug 为 None 的元素**:url 渲染为空 → 条目入库但链接差。
  缓解:v1 接受(缺注);后继可加 productSlug 兜底字段(需字符串清理,
  json_path 无字符串操作,得靠 url_template 多占位或 python 侧,不进本任务)。
- **R2 Steam 特惠轮换下架**:条目自然消失,retention 90d 清尾,无需特判。
- **R3 API 变更**:fixture 钉形状,变了测试即红,维护面小;两家 API 均
  多年稳定(Epic 免费游戏页官方数据通道、Steam featured 官方接口)。
- **R4 长促销每日 immediate 重推一条**(槽位抑制语义+每日一跑):digest
  吸收大半;限免周促连推 7 天属可接受提醒;嫌吵调 cron(见 D5)。
- **R5 主人网络代理抽风史**(GitHub 取证通道教训):两 API 本机直连均
  实证 200;CI 零外网不受影响;真跑失败先怀疑代理再怀疑源。
- **R6 url_template 是 schema 扩展**:向后兼容(url 字段路径行为不变,
  新字段可选);spec/文档锁定面同步见 implement 步骤 5,防「文档照做即失败」。

## 5. 明确不做(本轮)

- Reddit 源(证据:HTML 拒答;骗 UA 违原则)——backlog。
- 中文 L2 游戏源(反爬未探)——backlog,拆任务时先全量探查。
- Epic「下周免费」upcoming 提前预告——v2 增强(PRD 开放问题④)。
- stocks 换装 url_template、gpu-prices 入 OFFICIAL_PLUGINS——独立小事,
  不混本任务提交。
