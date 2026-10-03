# 游戏资讯 RSS 源:RSS 提取能力+机核接入

## 背景与来源

主人批 **「要」**(2026-10-03,games 线终局后留下的两个待决口之二)。
探查依据=wrap 三路探查之资讯源结论(档已归档:
`.trellis/tasks/archive/2026-10/10-03-games-wrap/`,证据 `evidence/news-probe-*`):

- **机核 gcores RSS 是最健康通道**:`https://www.gcores.com/rss`——honest UA
  200、robots 允许、中文游戏资讯 20 条/次,字段 title/link/pubDate/description 稳定;
- IGN RSS 为第二候选(英文;必须直用终 URL `https://www.ign.com/rss/articles/feed?tags=games`
  ——`feeds.ign.com` 是 302 而 httpx 缺省不跟随重定向);
- **现有能力接不了 RSS**:static_html 实测 `<link>` void 元素拿不到条目
  url(20/20 缺失)+IGN CDATA 污染,硬接=静默零产出;
- Reddit `.rss` 虽 200 但 robots 全站 Disallow 且官方政策限自动化,无
  CheapShark 式推翻证据,不接(决议维持)。

## Goal

给 MYIA 加 **RSS 提取小能力**(L1/L2 级,非新引擎层级),接入机核游戏资讯,
交付「游戏资讯」情报品类。

## 待决(主人批复即可开工;均带推荐)

1. **依赖**:feedparser(RSS 事实标准库,BSD-2 合规;RSS1/2/Atom/CDATA/
   编码坑全覆盖)vs stdlib `xml.etree` 手写(零新依赖,但 RSS 变体/CDATA/
   编码坑全自己扛)。
   ➡️ **推荐 feedparser**(uv workspace 运行依赖;解析坑是这类库存在的全部理由)。
2. **品类归属**:独立品类 `plugins/news.yaml`(资讯条目无价格字段,games 的
   规则/基线/模板全是价格语义,混进去规则面别扭;独立 12 段各自干净,feed
   品类选择器自然多一项「游戏资讯」)vs 并入 games 第五源(同卡聚合)。
   ➡️ **推荐独立品类**(要合并将来拆装容易,反向难)。
3. **v1 源数**:只机核 vs 机核+IGN 双源。
   ➡️ **推荐 v1 只机核**(中文阅读顺、单源先钉稳;IGN 是加一行的事)。

## Requirements(批复后细化 design)

1. **提取能力**:extend `extract.type` 词表(EXTRACT_TYPES 加 `"rss"`)——
   引擎路径(direct_api 收 XML 还是 static_html 层分发)、字段映射形态
   (title←entry.title / url←entry.link / published←entry.published)设计期定;
   12 段公开契约不动(extract 是子节)。
2. **插件清单**:`plugins/news.yaml`(12 段显式;daily 排程;资讯全 digest
   零路由分层;watchlist 画像;enrich 缺省关)。
3. **测试**:新提取类型单测(fixture 用归档档的 gcores RSS 实录样本);
   `OFFICIAL_PLUGINS` 加名+全套电池(含 golden 收编 news 条目——**改官方
   插件面必同步 golden,三连教训在案**)。
4. **文档**:spec/domain/yaml-schema.md 与 docs 锁定面同步 rss 类型。

## Acceptance Criteria

- [ ] `myia run plugins/news.yaml --dry-run --json` 退出码 0
- [ ] 真跑机核条目 > 0(证据落档)
- [ ] rss 提取类型单测绿(fixture=实录 gcores RSS);OFFICIAL_PLUGINS 全套
      电池绿;golden 同步
- [ ] scoped+全量门禁(外来红口径)零新红;feedparser 依赖进 uv workspace
      锁文件
- [ ] spec/docs 锁定面同步

## 非目标

- Reddit(robots 判例维持);Atom 通用引擎(只做 RSS 条目映射所需最小面);
  并入 games 品类(除非主人推翻待决②)。
