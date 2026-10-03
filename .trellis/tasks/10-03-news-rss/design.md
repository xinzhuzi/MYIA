# Design — 游戏资讯 RSS 源(10-03-news-rss)

> 决议(grill 式批复 2026-10-03 主人「按推荐」):①解析库=**feedparser**
> (BSD-2);②品类=**独立 `plugins/news.yaml`**;③v1 源=**只机核 gcores**。
> 探查证据(实测)在 `archive/2026-10/10-03-games-wrap/evidence/news-probe-*`
> (gcores-rss.xml/ign-rss.xml/reddit-atom.xml/robots 三份/summary.txt)。

## 1. 能力面:extract.type 加 `"rss"`

- **schema**(`src/myia/schema.py`):`EXTRACT_TYPES`(schema.py:149)与
  `ExtractType` Literal 加 `"rss"`;`_check_shape` 的 url 必填校验对 rss 同样
  适用(rss 的条目 url=feedparser `entry.link`,仍是去重键根基)。
- **字段映射形态**:`extract.fields` 的**值=feedparser entry 属性名白名单**
  (`title`/`link`/`published`/`updated`/`summary`/`author`),键=归一字段名。
  例:`fields: {title: title, url: link, published: published}`。白名单外
  属性名**拒载**(拼错即拦,同 url_template 交叉校验精神);条目缺属性→
  该字段省略(逐条目,同 json_path 语义)。
- **引擎**:`static_html`(非 direct_api——XML 不是 JSON):引擎
  `SUPPORTED_EXTRACT_TYPES`(static_html.py:49)加 `"rss"`;`fetch_base`
  新增 `extract_rss(text, extract)`——`feedparser.parse(text)` → entries →
  字段映射。引擎收文本的现成通路:`decode_response` → 提取函数
  (static_html.py:93-99 同构)。

## 2. 依赖

根 `pyproject.toml` 运行依赖加 `feedparser>=6`(BSD-2 合规;纯 Python +
sgmllib3k,零重依赖);`uv lock` 更新锁文件并提交。

## 3. plugins/news.yaml 骨架(12 段显式)

```yaml
id: news
name: 游戏资讯
schedule: "0 11 * * *"          # 与 games 同时段(资讯日更足够)
timezone: Asia/Shanghai
sources:
  - name: gcores-rss            # 机核;honest UA 200/robots 允许(探查实证)
    engine: static_html         # 引擎收文本;提取走 type: rss
    url: "https://www.gcores.com/rss"
    extract:
      type: rss
      fields: { title: title, url: link, published: published }
watchlist: { keywords: [新游, 版本, 更新, 发售], mute: [广告, 招聘] }
classify: { builtin: false, rules: [] }   # 资讯标题不落七大类;无价格规则面
dedup: { key: "{url}" }
enrich: { enabled: false }
push:                            # 双通道(同 games wrap 先例)
  - feishu_card + telegram 条目,全 digest(资讯无 immediate 分层)
storage: { retention: 90d, vacuum: monthly }
```

模板:标题+链接+日期(**不进 summary/CDATA**——R3 减坑,后续要摘要再说)。

## 4. 测试与 golden

- `extract_rss` 单测:fixture=**archive 档 `news-probe-gcores-rss.xml` 裁剪**
  (含中文标题/CDATA summary 形状);断言逐条目映射、缺属性省略、白名单外
  属性名拒载。
- `OFFICIAL_PLUGINS` 加 `"news"`:全套电池生效;`_SNIPPETS` 加
  `(news, gcores-rss)`(实录裁剪);两跑 dedup 计划(gcores 形状 XML 第二跑
  换条目);模板渲染样条+marker。
- **golden 收编 news 条目(整块首增,同 games v3 先例)**——改官方插件面必
  同步 golden,三连教训在案。

## 5. spec/文档同步

`.trellis/spec/domain/yaml-schema.md` + `docs/zh|en/schema.md`:
extract.type 词表加 rss + 字段映射语义与白名单(逐字段一行)。

## 6. 风险

- **R1 gcores RSS 结构变/停服**:fixture 钉形状报红兜底;IGN 是加一行
  (探查已留终 URL 与 302 注意点)。
- **R2 feedparser 依赖面**:纯 Python 轻依赖;锁文件钉版本。
- **R3 CDATA(summary 含 HTML)**:v1 模板不显示 summary,字段白名单里
  保留但 news.yaml 不映射——坑留给未来要摘要时。
- **R4 双引擎边界**:`type: rss` 只挂 static_html 引擎;direct_api 保持
  JSON-only(词表校验按引擎拦截,direct_api 配 rss 拒载)。

## 7. 非目标

Atom 通用解析(feedparser 本身支持,但词表/测试只钉 RSS 条目映射所需
最小面);IGN 第二源(v2 加一行);enrich LLM 摘要;并入 games 品类
(决议②已否)。
