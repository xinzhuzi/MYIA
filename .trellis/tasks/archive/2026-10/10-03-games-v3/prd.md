# 游戏情报 v3:CheapShark 多店折扣源(GOG/Humble/Fanatical)

## 背景与来源

v2 档遗留节的 v3 候选首项(探查大发现:CheapShark 一个稳定公共 API 盖
14 活跃店)。主人令 2026-10-03「Grill 针对trellis文档进行补全」→
**grill Round 1 七问全按推荐批复(「按推荐」)**。探查证据在本档
`evidence/`(stores 清单/多店 deals/0 元空集)。

## Goal

games 品类加第三源 `cheapshark`:GOG/Humble/Fanatical 三家店的**付费
折扣**情报并入「游戏情报」卡;限免仍归 Epic 官方源(CS 实测无 0 元 deal,
它不是限免通道)。

## Grill 决议(Round 1,2026-10-03,全按推荐)

1. **robots 有据推翻已批**:CS robots 明文 `Disallow: /api/1.0/`,循
   stocks/Yahoo 判例——结构化公共数据 API、官方文档公开邀请、qps 0.5
   礼貌档低频(每日 1 请求);源上 `respect_robots: false` + YAML 注释
   写明 deliberate override 与回退条件(复用为页面爬取前必须翻回)。
2. **并入 games 品类做第三源**(独立品类否决:订阅意图是「游戏省钱」,
   拆卡碎;后要拆=YAML 拆文件,低成本迁移)。
3. **店集合 `storeID=7,11,15`**(GOG/Humble/Fanatical;与 Steam featured
   源零重叠,排除小众 key 店噪音——Aldorlea 类 96% off 刷屏)。
4. **字段单位与路由**:美元独立命名 `sale_price`/`savings_pct`(字符串,
   规则 `float(savings_pct) >= 50` 用白名单函数),**不归一 `final_price`、
   不进 baseline**(人民币分基线不容美元元);**路由零改动**——CS 全落
   digest(immediate 仍只属限免;digest 无 items 排序,顺序=源到达序,CS 条目
   落日报尾部)。
5. **URL 用 `.com` 域**:`https://www.cheapshark.com/redirect?dealID={deal_id}`
   (实测 200;`.net` 域本机两次 SSL 拒连,网络路径问题记档不纠缠)。
6. **zol 连通性与 smzdm 不搭车**:zol 属 gpu-prices 线独立议题;smzdm
   要中文源时再立项(先探提取再谈判重)。
7. **开工时机**:等主人开工令(决议⑨模式——shishi 改名/v1.1.1 发布线/
   大工作流在途,`test_plugins.py` 与 golden 基件是共享热点,今天已两次
   见证碰撞)。

## Requirements(实现要点)

1. `plugins/games.yaml` 增 `cheapshark` 源(direct_api+json_path):
   - url:`https://www.cheapshark.com/api/1.0/deals?storeID=7,11,15&sortBy=Savings&pageSize=20`
   - **响应是顶层数组**,字段前缀 `$[*]`(与 Epic/Steam 嵌套前缀不同,
     extract_json 同前缀逐元素提取同样适用);
   - 字段映射:`title`←`.title`、`deal_id`←`.dealID`(已 URL-encoded,
     直接拼链接)、`sale_price`←`.salePrice`(美元元)、`savings_pct`←
     `.savings`(字符串%)、`normal_price`←`.normalPrice`、`metacritic`←
     `.metacriticScore`(显示参考);
   - `url_template: "https://www.cheapshark.com/redirect?dealID={deal_id}"`。
2. classify 加一条:`float(savings_pct) >= 50` → tag `多店半价+`
   (缺字段让路语义对 Epic/Steam 条目天然无感);**路由不加 immediate**。
3. 模板:CS 条目显示 `$ {{ sale_price }}(原价 {{ normal_price }})`
   与 `-{{ savings_pct|round|int }}%`;与现有字段形态并存(Epic
   price_text / Steam final_price÷100 退路不变)。
4. watchlist 维持现集(零降权事实不变);排程随品类每日 11:00 同跑。
5. golden 基件**不涉**(不改任何现有插件声明面——games.yaml 新增源不改
   装载形状?**注意**:games.yaml 在 golden 基件清单里,新增 source 会改
   `sources` 列表长度 → **必须同步 golden**(v2 教训:改官方插件声明面
   必同步基件),已列入验收)。

## 非目标

- CS 全 14 店、限免通道(它没有)、独立品类、Steam 店(重复)。
- zol / smzdm(决议⑥)。
- baseline 扩展到美元价格(币种基线混存是坑,永不混)。

## Acceptance Criteria

- [ ] `myia run plugins/games.yaml --dry-run --json` 退出码 0
- [ ] `_SNIPPETS` 加 `(games, cheapshark)`(裁自 `evidence/cs-multi.json`,
      含 96%+ 命中形状与一条非命中形状);第二形状断言错位防护
- [ ] golden 基件随 games.yaml 源数变化同步再生成并绿
- [ ] 合成 CS 条目断言:`float(savings_pct) >= 50` 规则命中、路由落
      digest(不 immediate)
- [ ] scoped `tests/test_plugins.py` 绿;全量本任务范围零失败
      (外来红口径同 v2:逐条归属)
- [ ] 真跑三店条目 > 0(evidence 落档)

## 验收记录(2026-10-03,受主人委托代验)

**结论:accepted**(六条 AC 全部对上仓库实况;复选框维持原状,证据如下)。

Acceptance Criteria 逐条(行号/命令均为本日实况):

1. **dry-run 退出码 0** ✅:`uv run --no-sync shishi run plugins/games.yaml --dry-run --json` → exit 0,`status: success`(shishi = myia.cli:main,pyproject.toml:41);cheapshark 源当日 fetch ok。
2. **`_SNIPPETS` 加 `(games, cheapshark)`** ✅:`tests/test_plugins.py:682-713`,两元素裁自 `evidence/cs-multi.json`——Unclaimed World 的 dealID `hV1uGbDuy%2F…%3D` 与 evidence 逐字节一致(python 比对实证)、savings `97.057092` 即 96%+ 命中形;非命中形 savings `"45.0"` 系唯一人为调整(注释明示三店 top-60 无 sub-50 实录可裁,循 gpu-prices zol 合成先例)。第二形状错位防护:`expect_second`(:708-712)+ 断言环(:790-799)。
3. **golden 同步再生成并绿** ✅:基件 `tests/fixtures/push_targets_golden_before.json` games 源数=4(cheapshark 在列:storeID=7,11,15 / `$[*]` 前缀 / `.com` redirect 模板 / respect_robots false);同步落 commit `f768e4e`(feat(games): CheapShark 多店折扣第三源……+golden 同步);`uv run --no-sync python -m pytest tests/test_push_schema_targets.py -q` → **23 passed**(本次实跑)。
4. **合成 CS 条目断言** ✅:`test_games_cheapshark_deal_hits_rule_and_stays_digest`(`test_plugins.py:340-374`):`float(savings_pct) >= 50` 命中含 `>=50` 边界(50.0 命中/49.999 不命中)、Epic/Steam 缺字段让路、双通道 resolve_route 落 digest 不 immediate;源形状由 `test_games_cheapshark_source_declares_three_stores_top_array_prefix`(:268-288)钉住($[*] 前缀、robots override 非缺省漂移)。
5. **scoped 绿 / 全量本任务范围零失败** ✅:`uv run --no-sync python -m pytest tests/test_plugins.py -q` → **86 passed, 6 skipped**(6 skip = MYIA_SMOKE_REAL 真实源 smoke 默认跳),exit 0(本次实跑);全量口径=批次收尾存档 1822 passed/14 skipped/0 failed(时点在 f768e4e 之后,见 games-v2 执行记录),本次代验按 scoped 实跑、未复跑全量。
6. **真跑三店条目 > 0** ✅:`evidence/live-run-v3-2026-10-03.json`(dry_run: false,cheapshark item_count 20,fetch/classify/dedup/analyze/push 全 ok、failures 空)+ `evidence/cs-three-stores.json`(20 条)。当日店分布 storeID ∈ {11,15}(Humble/Fanatical),GOG 店(7)当日无 top-Savings 折扣条目——如实注记,不影响「条目>0」成立。

决议符合性抽查:respect_robots: false 附 deliberate override 注释+回退条件(`plugins/games.yaml:121-125`);美元字段 sale_price/savings_pct 不进 final_price/baseline(测试 `test_plugins.py:188` 钉住 `baseline.fields == ["final_price"]`);路由零改动(immediate 仅限免析取)。

处置:accepted → 执行 archive(set-branch main)。
