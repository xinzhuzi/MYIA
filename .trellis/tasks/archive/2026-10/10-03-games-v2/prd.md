# 游戏情报 v2:Epic 下周免费预告+gpu-prices 补测试电池+平台扩展探查

## 背景与来源

主人令 2026-10-03 **「深化与补全」**(games v1 终态收口后)。本档收 games
grill 决议留下的三条尾巴:决议②(upcoming 预告=v2 首项)、决议⑧
(gpu-prices 补 OFFICIAL_PLUGINS=独立小待办)、决议①③(GOG/Switch/中文源
先探查)。执行采用轻量直做(主会话,循 yaml-editor「low 收尾主会话直做」
先例),不另起 grill——三项方向均已有决议,实现细节属设计自决。

## Requirements 与落地结果

1. **A. Epic「下周免费」预告(决议②)✅ commit b665236**
   - **字段融合方案**:`upcoming_pct` / `upcoming_start` 与现有字段并列提取
     进**同一条目**——dedup 稳定 `{url}` 不动;预告日条目落 digest
     (`final_price > 0` 天然覆盖),正式限免日**新槽位**照常 immediate,
     决议②担心的「dedup 与切换时点碰撞」由槽位抑制语义自然消解。
   - 规则:`upcoming_pct >= 100` → tag `下周免费`(规则求值白名单含
     float(),TerraScape 实录 upcoming 0% 不命中);预告**不设 immediate 路由**。
   - 模板徽标:`📅{{ upcoming_start[:10] }}起免费`(ISO 切前 10 位,UTC
     15:00 = 北京同日 23:00,日期恰好对)。
   - 测试:fixture 钉 TerraScape 实录 upcoming 0% 形状(expect_second 扩
     upcoming 字段);合成 100% 条目断言 tag 命中+限免规则不命中+路由
     digest;模板值级标记 `📅2026-10-08起免费`。
2. **B. gpu-prices 补进 OFFICIAL_PLUGINS 全套电池(决议⑧)✅ commit
   da390fc + 63bbe82**
   - route 补显式 digest 分支(缺省即 digest,行为不变;官方插件两态齐全
     展示契约);合成 zol 形状两跑计划 + 模板裸渲染样条(vs_*/keyword_trends
     空缺省契约)。
   - **golden 基件随路由显式化再生成**(63bbe82,B 首提交漏项,全量红后补)。
   - 记档:zol 真实页 2026-10-03 实测被反爬检查页拦
     (`service.zol.com.cn/checking` 跳转),无 _SNIPPETS 录制样本(同 v2ex
     parked 先例);gpu-prices 生产源连通性是**独立后续议题**,不在本档。
3. **C. 平台扩展探查(决议①③)✅ 证据落 `evidence/`**
   - **CheapShark 公共 API ✅ 大发现**:`api/1.0/stores` 实测 14 家活跃店
     **含 GOG(7)/Epic Games Store(25)/Humble(11)/Fanatical(15)**;
     deals 形状 title/salePrice/normalPrice/savings(%)/metacriticScore/
     dealID——跳转链接 `https://www.cheapshark.net/redirect?dealID={dealID}`
     恰好 url_template 可构。**v3 扩平台(GOG/多 PC 店折扣)的现成通路**。
     设计要点(留 v3):美元元 vs 人民币分的单位冲突→CS 条目不归一
     `final_price`(价格不进 baseline 或独立字段);`savings` 是字符串,
     规则里 `float(savings) >= 50`(白名单函数可用)。
   - **smzdm ✅ 可达**:游戏频道 914KB HTML 正常返回,未被墙未被拦;深度
     提取(L2 选择器设计)另探,backlog 维持。
   - **Switch/主机**:CheapShark 不含 Nintendo,无稳定公共 API→维持 backlog。

## 验收与门禁(2026-10-03 实跑)

- [x] scoped `tests/test_plugins.py` 79 passed(新+:upcoming 规则/路由/
      徽标断言、gpu-prices 全套电池 11 项、两跑计划、模板样条)
- [x] golden 回归绿(基件同步后再跑 23 passed)
- [x] `myia run plugins/games.yaml --dry-run --json` 退出码 0
- [x] 全量 pytest:本任务范围零失败(工作树另有并行线在途红 7 条:
      docs×3+CLI 版本×2+sidecar/skill 各 1,逐条核对非本任务文件,
      归属 v1.1.1 发布线与 docs 在途会话)

## 遗留(后续排期,不属本档)

- CheapShark 接入 = **10-03-games-v3 候选首项**(单源盖 GOG/Humble/Fanatical)。
- gpu-prices 的 zol 源生产连通性(反爬)——独立探查。
- smzdm 提取设计与判重口径——要中文源再立项。

## 执行记录(2026-10-03 收尾)

- 2026-10-03 收尾:批次工作流最终全量 pytest(CI 同款 `uv run --no-sync python -m pytest -q`,收尾员复跑)= **1822 passed / 14 skipped / 0 failed**(上方门禁节所记 7 条并行在途红已随并行线收口清零);任务维持 review,详细结论见工作流报告。

## 验收记录(2026-10-03,受主人委托代验)

**结论:accepted**(证据全在场,无遗留主人手动项)。

逐项对照(行号均为本日工作区实况):

- **A. Epic 下周免费预告**:字段 `upcoming_pct`/`upcoming_start` 在 `plugins/games.yaml:50-51`;规则「下周免费」`upcoming_pct >= 100` 在 `games.yaml:202-204`;模板徽标 `📅{{ item.upcoming_start[:10] }}起免费` 在 `games.yaml:257`;预告不设 immediate(immediate 路由仅限免析取)。commit `b665236` 在案(`git show` 实证:games.yaml +11、tests +47)。fixture 钉 TerraScape 实录 upcoming 0% 形状(`tests/test_plugins.py:620-644`,expect_second 断 `upcoming_pct: 0`);合成 100% 断言 `test_games_upcoming_free_hits_tag_rule_and_stays_digest`(`test_plugins.py:319-337`:tag 命中+限免不命中+双通道落 digest)。
- **B. gpu-prices 全套电池**:`OFFICIAL_PLUGINS` 五元组含 gpu-prices(`test_plugins.py:48`);显式 digest 路由分支在 `plugins/gpu-prices.yaml:67-73`;golden 基件(`tests/fixtures/push_targets_golden_before.json`)含 gpu-prices 键。commits `da390fc`(电池+route 显式化)与 `63bbe82`(golden 再生成,B 漏项补齐)均在案。
- **C. 平台探查证据**:`evidence/cs-stores.json`(35 店全量,含 storeID 7/11/15/25)、`evidence/smzdm-reachability.json`(http 200 / 932,594 字节,件内自注「PRD 原断言只有行文,本件补上」——诚实补档);Switch 无 API 负结论记档于 PRD 正文。

本次实跑(2026-10-03):

- `uv run --no-sync python -m pytest tests/test_plugins.py -q` → **86 passed, 6 skipped**,exit 0(6 skip = `MYIA_SMOKE_REAL` 真实源 smoke 默认跳,非失败;门禁节所记 79 → 现 86 系 v3/wrap 电池扩容,非本档回归)。
- `uv run --no-sync python -m pytest tests/test_push_schema_targets.py -q`(golden 回归)→ **23 passed**,exit 0。
- `uv run --no-sync shishi run plugins/games.yaml --dry-run --json` → exit 0,`status: success`,fetch 4 源 42 条全 ok(入口名 `shishi` = `myia.cli:main`,pyproject.toml:41)。

观察(不阻塞验收):①dry-run 实测 129 条「自定义规则求值失败→按不命中」WARNING(route→digest 40 / 下周免费 37 / 多店大折扣 22 / 大折扣 30)——系既有 fail-open 语义(`games.yaml:193` 与测试 docstring 明文记载该代价),结论不受影响;后续打磨可循 wrap 的 None 守卫先例给「下周免费」加 `(upcoming_pct or 0) >= 100` 形消音,行为不变。②「全量 1822 passed」为收尾员 2026-10-03 复跑存档,本次代验按 scoped 口径实跑,未复跑全量。

处置:accepted → 执行 archive(set-branch main)。
