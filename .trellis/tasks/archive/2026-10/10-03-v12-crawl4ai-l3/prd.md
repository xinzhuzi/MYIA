# crawl4ai L3 实装:真引擎进降级链

> 父档:`10-03-v12-backlog`(Requirements 第 2 项);v0.2 档
> `10-01-v02-engine-crawl4ai`(已归档)的 grill Q4 注记把「L3 真引擎实装」划入本档。

## 现状(2026-10-03 落档核实,与池档描述的差异)

池档写「`src/myia/engines/crawl4ai.py` 为薄层,L3 降级档实质缺位」——**经通读现码,
该评估已过时**:commit 8e8d2ab 起引擎即为实装态,以下能力全部在位:

- 惰性 import(`crawl4ai.py:71-96` `load_crawl4ai`),未安装 → 结构化
  `dependency_missing` 含 `pip install shishi[crawl4ai]`(`crawl4ai.py:57` INSTALL_COMMAND)
- BrowserConfig/CrawlerRunConfig 基础配置(`crawl4ai.py:135-153`):
  headless、page_timeout、cache_mode=BYPASS、pool 代理透传、headers 透传
- 无 extract 自动结构化兜底 `{url,title,content}`(`crawl4ai.py:227-233`,yaml-schema 规则 7)
- 页面预算 `asyncio.wait_for` 护栏,超时结构化为 `timeout`(`crawl4ai.py:194-209`)
- 降级链序 L1→L2→crawl4ai→firecrawl→scrapling→stealth_browser→llm_browser
  (`registry.py:67-75`),与 yaml-schema 规则 5 一致;测试
  `test_registry.py:88-108`、`test_crawl4ai.py:395-484` 已覆盖链序与
  「L2 失败→crawl4ai 成功不再落 firecrawl」「dependency_missing 继续降级」
- extras 已声明:`pyproject.toml:28` `crawl4ai = ["crawl4ai"]`(池档口径「已有」属实)
- 测试为脚本化假模块回放(sys.modules 注入,先例 `test_scrapling.py`)+
  httpx.MockTransport,零真实网络零浏览器,即池档「录制回放(CI 零外网)」的落地形态

**本档真正要补的缺口(两项)**:

1. **配置化深度不足**:引擎自管键只有 `headless`/`timeout`,crawl4ai 的
   BrowserConfig/CrawlerRunConfig 其余配置面(user_agent、viewport、text_mode、
   word_count_threshold、wait_for、css_selector…)不可达。
2. **smoke 门禁不一致**:crawl4ai 真实源 smoke 用 `MYIA_CRAWL4AI_SMOKE`
   (`test_crawl4ai.py:494`),而仓库既有约定是 `MYIA_SMOKE_REAL`
   (`test_scrapling.py:785`、`test_direct_api.py`、`test_plugins.py`)。

## Requirements

1. **配置化补全**:`engine_options.crawl4ai.browser_options`(dict,透传合并进
   BrowserConfig)与 `engine_options.crawl4ai.run_options`(dict,透传合并进
   CrawlerRunConfig),打开 crawl4ai 完整配置面;引擎语义自管键
   (browser: headless/proxy/headers;run: cache_mode/page_timeout)禁止透传覆盖
   (双来源 = 配置冲突,fail-fast 结构化拒绝);dict 形状错、透传键被
   crawl4ai 配置类拒绝(TypeError)均结构化报错(invalid_browser_options /
   invalid_run_options),fail-fast 于配置、先于依赖加载(与 timeout/headless 同序)。
2. **smoke 门禁对齐**:crawl4ai 真实源 smoke opt-in 变量统一为
   `MYIA_SMOKE_REAL`(对齐 test_scrapling.py),默认跳过,CI 不依赖,本次不实跑。
3. **降级链确认**:链序已正确(registry.py:67-75),不改码;以现测试运行复核。
4. 测试全部零外网(假模块脚本化回放 + MockTransport);文档字符串/模块契约同步。

## Acceptance Criteria

- [x] extras 可选依赖已有 `shishi[crawl4ai]`(pyproject.toml:28,本档只核不改)
- [x] L2 失败自动落 L3:`test_auto_chain_l2_failure_degrades_to_crawl4ai_not_firecrawl`
- [x] 录制回放测试 CI 零外网:tests/test_crawl4ai.py 全绿(假模块回放,零 socket)
- [x] 不装依赖时报结构化错误,信息含 `pip install shishi[crawl4ai]`(既有 + 新增用例)
- [x] browser_options/run_options 透传、自管键冲突拒绝、未知键结构化报错(新增用例)
- [x] smoke 门禁 MYIA_SMOKE_REAL(改 skipif,默认 skip,本次不实跑)

## 执行记录(2026-10-03)

**改动文件**:

- `src/myia/engines/crawl4ai.py`:新增 `_RESERVED_BROWSER_KEYS`/`_RESERVED_RUN_KEYS`
  与 `_passthrough_options()`(dict 形状 + 自管键冲突校验,fail-fast 于依赖加载);
  `_fetch_impl` 里 browser_options/run_options 合并进 BrowserConfig/CrawlerRunConfig
  构造,构造期 TypeError → 结构化 `invalid_browser_options`/`invalid_run_options`
  (此前裸 TypeError 会逃到 registry 被归 unknown);就绪日志加透传键名清单;
  模块契约 docstring 同步。
- `tests/test_crawl4ai.py`:假 BrowserConfig/CrawlerRunConfig 改为严格键集
  (与真库 dataclass 同语义:未知参数构造期 TypeError);新增 5 个透传用例
  (透传生效+自管键不受影响 / 非映射 / 自管键冲突 / browser 未知键 / run 未知键);
  真实源 smoke 门禁 `MYIA_CRAWL4AI_SMOKE` → `MYIA_SMOKE_REAL`(对齐
  test_scrapling.py / test_direct_api.py / test_plugins.py 统一 opt-in 变量);
  模块 docstring 覆盖清单同步。
- 降级链序(registry.py:67-75)读现码确认正确,未改码;既有链序测试覆盖
  (test_registry.py:88-108、test_crawl4ai.py 链序三连、test_llm_browser.py:874-879)。

**验证(本机实跑)**:

- `uv run --no-sync python -m pytest tests/test_crawl4ai.py tests/test_registry.py -q`
  → **44 passed, 1 skipped**(skip 即 MYIA_SMOKE_REAL 真实网络 smoke,符合预期;
  首跑曾 1 failed:`headers` 冲突用例的 Cookie 明文值被 schema 装载期拒——
  恰是安全基线生效,改为 `env:` 引用形态后全绿)
- `uv run --no-sync python -m pytest tests/test_scrapling.py tests/test_llm_browser.py
  tests/test_stealth.py tests/test_firecrawl.py tests/test_fetch_base.py
  tests/test_static_html.py tests/test_direct_api.py -q` → **199 passed, 5 skipped**
  (skip 均为各引擎 opt-in 真实 smoke),跨引擎零回归。
- 全量 1771 基线由脚本门禁统一跑,本任务未跑全量(公共约定 4)。
- 2026-10-03 收尾:批次工作流最终全量 pytest(CI 同款 `uv run --no-sync python -m pytest -q`,收尾员复跑)= **1822 passed / 14 skipped / 0 failed**(上条「未跑全量」由本次收尾补跑);任务维持 review,详细结论见工作流报告。

**诚实边界**:

- TapNow 真实 JS 渲染冒烟(验收 v0.2 遗留 AC1)依赖本机安装 `shishi[crawl4ai]` +
  crawl4ai-setup + 真实网络,本次未实跑,仍需主人手动:设 `MYIA_SMOKE_REAL=1` 后
  `uv run --no-sync python -m pytest tests/test_crawl4ai.py -k smoke -q`。
- 「L3 降级档实质缺位」的池档评估与 8e8d2ab 后实况不符:引擎主链路(惰性
  import/结构化错误/自动结构化/预算护栏/链序)在v0.2 档已落地,本档补的是
  配置面深度与门禁一致性,PRD 现状节有逐条证据。

## Notes

- 池档「薄层/实质缺位」评估与 commit 8e8d2ab 后的实况不符,本档以现码为准;
  v0.2 档验收记录(39 passed 1 skipped)与本档核读一致。
- 引擎自管键清单是有意收窄的:proxy/headers/cache/timeout 的语义
  (凭据解析、脱敏日志、缓存旁路、预算护栏)由引擎单一来源负责,不开放透传。
