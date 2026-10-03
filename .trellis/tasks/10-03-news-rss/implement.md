# Implement — 游戏资讯 RSS 源(10-03-news-rss)

> 决议已定(design.md 头);执行顺序即依赖顺序。**不碰 desktop/ 与并行线
> 在途文件;开工前核 git status 目标面干净**。

## 0. 基线(开工先跑)

```bash
uv run --no-sync python -m pytest -q --tb=short > /tmp/news-baseline.log 2>&1; echo "exit=$?"
```

外来红口径(基线红仅拦本任务范围文件,终跑不得新增;FAILED 与收集 ERROR
都解析)。conftest 规约:测试一律 `from conftest import`。

## 1. 依赖:feedparser

- [ ] 根 `pyproject.toml` 运行依赖加 `feedparser>=6`;`uv lock` 更新锁文件
      (锁文件随提交);import 冒烟 `uv run --no-sync python -c "import feedparser"`。
- 回滚点:独立 commit(仅 pyproject+lock)。

## 2. schema:extract.type 词表 + 白名单校验

- [ ] `EXTRACT_TYPES`/`ExtractType` 加 `"rss"`(schema.py:149/166);
- [ ] rss 专属校验:fields 值 ∈ entry 属性白名单
      {title,link,published,updated,summary,author},违者 fail-fast
      (中文错误信息,path 指到 fields.<键>);url 必填沿同规;`type: rss`
      与 `item` 选择器互斥沿用现有 `_check_shape` 结构。
- [ ] 单测进 `tests/test_schema.py`:rss 合法/白名单外拒/缺 url 拒。
- 验证:`uv run --no-sync python -m pytest tests/test_schema.py -q`。

## 3. 引擎与提取:extract_rss

- [ ] `src/myia/engines/fetch_base.py` 新增 `extract_rss(text, extract)`:
      feedparser.parse → entries → 白名单字段映射,缺属性省略;bozo 容错
      (feedparser 的 malformed 标志只记 WARNING 不拒,条目为空返回空列表)。
- [ ] `src/myia/engines/static_html.py` `SUPPORTED_EXTRACT_TYPES` 加
      `"rss"` 并在提取出口分流到 extract_rss;**direct_api 保持 JSON-only**
      (其 SUPPORTED 不加,配置 rss+direct_api 在引擎层拒,design R4)。
- [ ] 单测进 `tests/test_fetch_base.py`:fixture 用
      `archive/2026-10/10-03-games-wrap/evidence/news-probe-gcores-rss.xml`
      裁剪(2-3 条,含中文标题与 CDATA summary);断言映射/缺属性省略/
      bozo 容错。
- 验证:`uv run --no-sync python -m pytest tests/test_fetch_base.py -q`。

## 4. 品类与测试基线接入

- [ ] `plugins/news.yaml`(design §3 骨架,12 段显式+双通道,inline 注释);
      `myia run plugins/news.yaml --dry-run --json` 退出码 0。
- [ ] `tests/test_plugins.py`:`OFFICIAL_PLUGINS` 加 `"news"`;
      `_SNIPPETS` 加 `(news, gcores-rss)`(实录裁剪);两跑计划加 news
      (gcores 形状 XML,第二跑换条目);模板样条+marker;**golden 收编
      news 条目**(整块首增,同 games v3 再生成先例,复刻 63bbe82 手法)。
- 验证:`uv run --no-sync python -m pytest tests/test_plugins.py tests/test_push_schema_targets.py -q`。

## 5. spec/文档同步

- [ ] `.trellis/spec/domain/yaml-schema.md` 与 `docs/zh|en/schema.md`:
      extract.type 词表加 rss,逐字段行(映射语义/白名单/引擎边界)。
- 验证:`uv run --no-sync python -m pytest tests/test_docs.py -q`(若该文件
  在途被并行线占用,记录跳过理由,不碰)。

## 6. 门禁与真跑(收尾)

- [ ] 全量 pytest(外来红口径)零新红;ruff 本任务 py 零错
      (`uvx ruff@0.16.10 check <本任务 py 文件>`,勿喂 yaml)。
- [ ] 真跑机核条目 > 0(`--db /tmp` 沙箱),run 报告存本档 `evidence/`。
- [ ] `python3 .trellis/scripts/task.py validate 10-03-news-rss` 绿。
- [ ] 白名单提交(归属分笔):①能力笔(pyproject/lock+schema+fetch_base/
      static_html+三测试件);②品类笔(news.yaml+test_plugins+golden);
      ③docs 笔(spec+docs+任务目录)。不 push。

## 守门

- 不动 games.yaml/gpu-prices 等既有官方插件声明面(news 是纯新增);
  不碰 `tests/test_push_schema_targets.py` 源码(golden 数据件除外——
  若该测试文件在途,改用「golden 只增键不动测试」路径并记录)。
- 真跑失败先怀疑网络/代理(design 探查已证通道健康),勿急改代码。
