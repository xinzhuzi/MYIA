# Implement — 游戏情报品类插件(10-03-games)

> 前置:design.md 的 D1-D6 已定;执行顺序即依赖顺序(1→2 是 schema/引擎地基,
> 3 挂在其上,4-5 钉质量,6 验真)。每步有独立验证命令与回滚点;
> **全程不碰 desktop/(无 UI 改动),vitest/cargo 门禁不涉**。

## 0. 基线(开工先跑,红则停)

```bash
uv run --no-sync python -m pytest -q --tb=short > /tmp/games-baseline.log 2>&1; echo "exit=$?"
```

- 零失败才继续(浮动基线口径:以开工时点零失败为准,总数会被并行任务移动)。
- 注意 conftest 规约:测试文件一律 `from conftest import …`,勿 `from tests.conftest import …`。

## 1. schema:url_template 微扩展(D1)

- [ ] `src/myia/schema.py` `ExtractConfig`:增可选 `url_template: str | None`
      (min_length=1);`_check_shape` 的 `missing_url_field` 校验放宽为
      「fields 含 url **或** url_template 非空,二者必居其一;都缺才报错」;
      `type != "list"` 时 url_template 同样拒(与 item 语义对齐——单页源
      url 即请求 URL,不需要模板)。
- [ ] 模板占位语法沿用 dedup.key 的 `{field}` 纯占位(`_PLACEHOLDER_RE`
      同款校验:至少一个占位符,未知占位渲染时按缺字段处理)。
- [ ] 单测进 `tests/test_schema.py`:url_template 合法/无占位符拒/与 url
      二选一矩阵(只有 url ✓、只有 template ✓、都缺 ✗、都有→url 胜出还是
      拒?**定死:都有=url 字段胜出,template 静默不用**,少一个报错分支)。
- 验证:`uv run --no-sync python -m pytest tests/test_schema.py -q` → 绿。
- 回滚点:独立 commit;纯增量字段,旧 yaml 零影响。

## 2. 引擎:提取出口渲染 url_template

- [ ] `src/myia/engines/fetch_base.py`:`extract_json` / `extract_html` 出口
      (或两出口共用的一个小函数)——extract 配置带 url_template 时,逐条
      用条目字段渲染 `{field}` 占位(**复用 dedup 的迷你模板渲染器,不新写**,
      若其签名不适配则提最小公共函数),渲染结果覆盖/填入 `url` 键;
      占位字段缺失→url 置空串(条目保留,链接差;design R1)。
- [ ] 单测:字段齐→URL 拼对;字段缺→空串不抛;int 字段(steam_id)渲染成
      "1593500" 字符串。
- 验证:`uv run --no-sync python -m pytest tests/test_fetch_base.py -q` → 绿。
- 回滚点:独立 commit。

## 3. 插件清单 plugins/games.yaml(design §3 骨架落全)

- [ ] 12 段显式 + baseline 节 + inline 注释(stocks「AI 写 YAML 参照」口径,
      每段一行为什么);字段映射照 design §1.1/§1.2;rate_limit qps 0.5 +
      jitter 2s + retry 3(同 stocks 礼貌档);proxy: direct。
- [ ] load-check(不通网):
      `uv run --no-sync python -m myia.cli run plugins/games.yaml --dry-run --json`
      → 退出码 0。
- 回滚点:单文件,独立 commit。

## 4. 测试基线接入(D6)

- [ ] `tests/test_plugins.py`:`OFFICIAL_PLUGINS` 加 `"games"`
      (全套电池自动生效:12 段显式/未知字段拒收/凭据引用/直连代理/route
      两态/白名单语法/模板渲染/建管线/二跑去重)。
- [ ] `_SNIPPETS` 加 `(games, "epic-free")` 与 `(games, "steam-specials")`
      两条:JSON 从 `evidence/epic-free.json` / `evidence/steam-featured.json`
      实跑响应裁剪(每源 1-2 元素,含限免形状与大折扣形状;若响应结构已
      变,按 design §1 字段表手工合成,断言不依赖网络)。
- [ ] games 专属断言(对齐 `test_stocks_uses_direct_api_with_json_path_extract`
      先例):双源 direct_api+json_path+url_template;合成限免条目
      (final_price=0)命中限免规则与 immediate 路由。
- 验证:`uv run --no-sync python -m pytest tests/test_plugins.py -q` → 绿。
- 回滚点:测试独立 commit。

## 5. 文档与 spec 同步(R6:防「文档照做即失败」)

- [ ] `.trellis/spec/domain/yaml-schema.md`:extract 节补 url_template
      逐字段条目(语义/二选一/占位语法/缺占位行为)。
- [ ] grep 全仓 `extract.fields` / 12 段字段锁定面(SKILL.md、docs/)逐处
      补一行;`tests/test_docs.py` 跑绿(若其钉 docs 一致性)。
- 验证:`uv run --no-sync python -m pytest tests/test_docs.py -q` → 绿。
- 回滚点:文档独立 commit。

## 6. 门禁与真跑(收尾,全量)

- [ ] 全量 pytest:`uv run --no-sync python -m pytest -q --tb=short > /tmp/games-final.log 2>&1; echo "exit=$?"` → 零失败(对照步骤 0 基线,新增用例全数计入)。
- [ ] 真跑一次(非 dry,出网):`uv run --no-sync python -m myia.cli run plugins/games.yaml --json`
      → 条目数 >0(Epic 12 元素含非限免,Steam specials ~10;当前若无
      限免属正常,immediate 路由 0 条不算失败,digest 卡可见)。
      注意 R5:失败先怀疑主人网络代理,别急着改代码。
- [ ] ruff:`uvx ruff@0.16.10 check .` → 对 HEAD 跟踪树零错(ci-gates 口径)。
- [ ] `python3 .trellis/scripts/task.py validate 10-03-games` → 绿。

## 守门与禁忌

- **不 push**(tag/推送=主人门禁);提交按归属白名单逐文件 add,
  不 `git add -A`(并行会话在场,在途改动严禁卷入)。
- 不动 stocks/gpu-prices 现有行为(stocks 换装 url_template、gpu-prices
  入测试元组=独立后续,design §5 已记)。
- 排期旋钮(cron 调稀、telegram 通道加挂)留主人;实现期按 design D5 缺省。
