# 游戏情报收尾:telegram 加挂+文案修+三路探查(GOG 限免/资讯/smzdm)定论

## Goal

games 线尾巴清零(主人令 2026-10-03「按照你的建议,将剩下的尾巴检查后都做完」):直接做=telegram 加挂(v1 决议⑤)+测试文件 7 个夹具文案漂移修(v3 low);三路探查定论=GOG 限免通道(可行即接第四源)/游戏资讯源(探后立结论)/smzdm 提取(探后立结论);检查后关闭=CS 扩店(维持 3 店决议③)/Switch(无 API 结论已立)。收尾档记录全部结论。

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

## 执行结论(2026-10-03,六项定论落地)

1. **telegram 加挂(v1 决议⑤,已落地)**:games.yaml push 段新增第二个条目 `channel: telegram / target: env:TELEGRAM_CHAT_ID`,两级路由 when 逐字与 feishu 条目一致(含 GOG 限免双保险析取);模板走**精简纯文本变体**而非复用 feishu markdown 模板——telegram 用户模板契约是渲染文本不带 parse_mode 直发(`src/myia/push/telegram.py` `_compose`),markdown 链接语法会原样露出,故标题后直跟裸 URL,价格/折扣徽标同口径。bot token 走渠道缺省 `env:TELEGRAM_BOT_TOKEN`(DEFAULT_TOKEN_ENV_REF,无需声明)。golden 基件 games 条目 push 子树已同步(基件仍按全量维护);tests/test_plugins.py 补 `test_games_telegram_push_entry_mounted_per_v1_decision5`,凭据引用/模板渲染/路由两态由 OFFICIAL_PLUGINS 电池自动覆盖(86 passed)。

2. **GOG 限免第四源(viable=true,已接入)**:通道 `catalog.gog.com/v1/catalog` 公开 JSON API——gog.com 官网渲染 `/en/games?priceRange=0,0&discounted=true` 的 SSR 内嵌缓存键即此查询(正当性铁证 `evidence/gog-probe-ssr-filtered-excerpt.txt`),纯 curl 默认 UA 匿名 200 无 cookie。落地形态:direct_api 引擎(循 CheapShark 先例),url 锁 `countryCode=US&currency=USD&price=between:0,0&productType=in:game,pack&discounted=eq:true`(参数坑实测:横线形 between-0-0 → 404、冒号形 in:game:pack → 静默清零,勿"美化");列表 `$.products[*]`;字段 title/url(storeLink 绝对链接直出,无 url_template——schema 二选一的另一边)/gog_id/image(coverVertical)/sale_price(finalMoney.amount)/normal_price(baseMoney.amount),美元字符串循 v3 决议④独立命名不进 final_price 人民币分基线。限免判定进「限免」规则与 immediate 路由:条目级双保险=现价 0.00 且原价>0(原价同为 0.00 的永久免费游戏,实测 419 条全如此,被天然滤掉不刷屏);**实现要点:when 表达式加 None 守卫 `(discount_pct or 0)`/`(x or "-1")`——BoolOp 短路只在真值处停,裸 `>=` 对 GOG 条目会在轮到 GOG 析取前以 TypeError 炸整条**(or 假值分支继续求值),守卫后四源形态干净求值且与各源单独评估逐条等价。已知限制(记档):该端点无促销截止字段,限免结束时间拿不到;如需可挂 LootScraper GOG Atom feed(feed.eikowagenknecht.com/lootscraper_gog_game.xml,纯 curl 200,entry 内有 Offer valid to)做补充源,但第三方个人 feed 只当 backstop 不进主源;次级 oracle:`www.gog.com/en/giveaway/claim` 404↔无活动(匿名可判有无,领取得登录);gg.deals(403 Cloudflare)与 menu.gog.com(账户域无匿名路由)按铁律排除,CheapShark 不收 0 元 deal 为 v3 已知事实。robots:catalog.gog.com/robots.txt 404(2026-10-03 实测)= 无限制,`respect_robots: true` 缺省即允许(同 Epic 判例)。零状态四路交叉验证非假阴性:catalog 无 giveaway 形态、giveaway/claim 404、首页无 #giveaway 横幅、LootScraper GOG feed 空而同源 Epic feed 存活。原始证据:`evidence/gog-probe-*`(summary 在 `gog-probe-summary.json`,字段实录在 `gog-probe-catalog-sample.json`/`gog-probe-catalog-freegames-p1.json`)。

3. **游戏资讯源(viable=true 但需新能力,记档关闭,本任务不实现)**:最健康通道是机核 gcores RSS(honest UA 200、robots 允许、中文游戏资讯 20 条/次、title/link/pubDate/description 稳定,`evidence/news-probe-gcores-rss.xml`),IGN RSS 为第二通道(英文,robots 干净;YAML 必须直用终 URL `https://www.ign.com/rss/articles/feed?tags=games`——feeds.ign.com 是 302 而 httpx 缺省不跟随重定向,`evidence/news-probe-ign-rss.xml`)。但 RSS 是 XML:direct_api 不适用,static_html 实测因 `<link>` void 元素语义结构性拿不到条目 url(20/20 缺失)+ IGN CDATA 污染(`evidence/news-probe-summary.txt`),硬接只会静默零产出,故不接。落地需小改:fetch 层加 RSS/XML 解析(feedparser 或 extract.type 扩展,字段映射 item>title/link/pubDate/description),属 L1/L2 级小能力非新引擎层级——若未来立项该能力,gcores 为首选源、IGN 次之。关闭项:小黑盒无公开稳定通道(API 域名已 301 到无关站);r/GamingNews 维持 v1 剔除——`.json` 对 honest UA 403,`.rss` 虽 200 Atom 但 robots.txt 全站 Disallow 且 Reddit Public Content Policy 明文限制自动化访问,不具备 CheapShark 式推翻证据,respect_robots 下不可接。

4. **smzdm 提取(viable=false,不接入)**:三判据过二缺一——免伪装 UA ✓、结构稳定 ✓(11 次抓取类名一致,但仅本会话样本,无跨天验证)、可判定核心字段 ✗:无 discount_pct/original_price 使 games.yaml 全部 classify 规则(限免/大折扣/多店大折扣)永不命中;priceshow 人民币文本("2183.1元")与基线分单位冲突(v3 决议④单位不混入);含史低/免费关键词的卡是 post.smzdm.com 社区视频帖,无价格字段只产 digest 噪音。字段图留档备复用:若未来立『游戏硬件/数码优惠』独立插件,仅第 1 页(约 9 好价/日上限)、item=`div.haojia-card-container`、title/url/@priceshow/@directlink、tags 仅展示、永不并入 games 的 final_price 基线;登录态翻页未测(无凭据)。证据:`evidence/smzdm-probe-*`(summary 在 `smzdm-probe-summary.json`)。

5. **CS 扩店/Switch 检查后关闭**:维持 v3 决议③三店集合(storeID=7,11,15 = GOG/Humble/Fanatical)不变——GOG 零元通道已由本任务第四源直连官方 catalog 收口(比经 CS 转手更上游),付费折扣三店与 Steam featured 零重叠的结构性理由不变,扩店只引入小众 key 店噪音;Switch 维持无 API 结论(官方无公开商店价格 API,爬页面受反爬与 robots 约束,收益不抵成本),不立项。

6. **文案漂移注记(本任务不碰)**:`tests/test_push_schema_targets.py` docstring 的「改动前 7 个已跟踪夹具」文案漂移归 messaging 线在途的承诺边界收窄改造顺带覆盖(commit 2e7e252 已收窄为 push 子树断言);本任务按防踩踏纪律不改动该文件,仅同步 golden 基件 games 条目(push 子树断言下全量维护口径不变)。

## 验收记录(2026-10-03,受主人委托代验)

**结论:accepted**(六项执行结论逐条对上仓库实况,无遗留主人手动项;注记两处见末尾,均不阻塞)。

逐项对照(行号均为本日工作区实况):

1. **telegram 加挂(v1 决议⑤)** ✅:`plugins/games.yaml:268-280` 第二 push 条目(channel telegram / `target: env:TELEGRAM_CHAT_ID`);路由 when 与 feishu 条目逐字一致(`games.yaml:241` vs `:272` 逐字符比对相同,含 GOG 双保险析取);模板为无 markdown 链接的纯文本变体(测试钉 `"](" not in template`,`tests/test_plugins.py:265`)。`test_games_telegram_push_entry_mounted_per_v1_decision5`(:247-265)在电池中且绿。真跑实证:`evidence/live-run-wrap-2026-10-03.json` telegram 通道决策 immediate 2 / digest 39(与 feishu_card 同数)。
2. **GOG 限免第四源** ✅:`games.yaml:142-172`,url 锁 `price=between:0,0&productType=in:game,pack&discounted=eq:true`(横线/冒号坑注释在案);`$.products[*]` 字段 title/url/gog_id/image/sale_price/normal_price,无 url_template(storeLink 直出);`respect_robots: true`(robots 404)。测试 `test_games_gog_source_declares_catalog_query_and_double_insurance`(:191-244)钉住:双保险命中(0.00/19.99)、永久免费(0.00/0.00)不命中不刷 immediate、双通道 giveaway→immediate / permfree→digest、None 守卫形防回退。探查证据 `evidence/gog-probe-summary.json` 四路交叉验证(当日无活动,与 live-run gog-free item_count 0 一致)。
3. **资讯源关闭记档** ✅:`evidence/news-probe-*`(gcores RSS/robots、IGN RSS/robots、reddit json-403/atom/robots、summary.txt、两个提取脚本)齐备;RSS 能力缺口(fetch 层无 XML 解析)记档为未来小能力,gcores 首选/IGN 次之——「探后立结论」达成。
4. **smzdm 关闭记档** ✅:`evidence/smzdm-probe-*`(p1/p2 HTML、headers、pagination、robots、cards、summary.json)齐备;三判据过二缺一(无 discount_pct/original_price 致规则永不命中)+ 字段图留档——「探后立结论」达成。
5. **CS 扩店/Switch 检查后关闭** ✅:PRD 结论五记档维持三店集合与无 API 结论;仓库无相应代码改动需求(核对 games.yaml 无扩店痕迹)。
6. **文案漂移不碰** ✅:`tests/test_push_schema_targets.py` 工作区与 HEAD 零改动(git status 核对);commit `2e7e252`(golden 回归收窄为 push 子树断言)在案。

本次实跑(2026-10-03):

- `uv run --no-sync python -m pytest tests/test_plugins.py -q` → **86 passed, 6 skipped**,exit 0(6 skip = MYIA_SMOKE_REAL 真实源 smoke 默认跳;执行结论一所记 86 passed 复现)。
- `uv run --no-sync python -m pytest tests/test_push_schema_targets.py -q`(golden,push 子树)→ **23 passed**,exit 0;基件 games 条目已含双通道 push 与四源(golden 同步实证)。
- `uv run --no-sync shishi run plugins/games.yaml --dry-run --json` → exit 0,`status: success`,gog-free 在源列表(fetch 4 源 42 条)。

注记(不阻塞):①PRD 的 Requirements/Acceptance Criteria 节为 TBD 占位,实际验收口径=「执行结论」六项;目标节「测试文件 7 个夹具文案漂移修(v3 low)」被结论六以防踩踏纪律合法接管(归 messaging 线),非未完成项。②`evidence/live-run-wrap-2026-10-03.json` status=partial 系该会话未设 `FEISHU_BOT_TOKEN`/`TELEGRAM_BOT_TOKEN`,发送层 env_var_missing——抓取/分类/路由决策层全 ok(failures 空),真实投递实证属 messaging 线 bd6092d 冒烟,非本档代码缺陷。

处置:accepted → 执行 archive(set-branch main)。
