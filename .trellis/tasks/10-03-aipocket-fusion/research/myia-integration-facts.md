# MYIA 集成点地面真值(credhunter 接入用)

> 2026-10-03 只读核实(Explore 代理),file:line 相对本仓库当前工作区。
> 用途:design.md 契约面的实证依据;实现时以本档为准,不重新猜测。

## 1. CLI 子命令注册模式(以 proxy 为样板)

- 注册入口:`build_parser()` 逐个 `_add_xxx_parser(sub)`;proxy 挂载点 `src/myia/cli.py:265`。
- parser 定义:cli.py:2735-2766;handler 分发字典 `handlers`:cli.py:2793-2806(`"proxy": _cmd_proxy`)。
- 失败降级:适配器加载抛 `OSError/ImportError/SyntaxError` → `_emit_generic_error("proxy_adapter_missing")` 退 1(cli.py:2710-2718);`adapter.run()` 异常按失败码 frozenset(cli.py:182,proxy=`{"fetch_failed","no_alive_proxy"}`)分退 2/1(cli.py:2721-2727)。
- **动态加载器 `_import_plugin_adapter`**:cli.py:2611-2628。读 `<plugins_dir>/<plugin_id>/adapter.py`,`compile()+exec()` 进全新 `types.ModuleType`(刻意不走 importlib,避免插件目录产生 `__pycache__`);核心 wheel 零静态耦合。
- 新子命令接入三件套:cli.py:265 附近挂 parser;cli.py:2804 附近加 handlers 映射;cli.py:175-182 附近加 PLUGIN_ID 与失败码常量。

## 2. 进程内插件产出进管线(最关键结论)

**现状:proxy/osint 适配器产出零进管线。**

- 品类 YAML `sources[].engine` 封闭词表 = `EngineName` Literal 8 值(`src/myia/schema.py:175-177`:auto/direct_api/static_html/crawl4ai/firecrawl/scrapling/stealth_browser/llm_browser)。
- fetch 分发唯一接缝:`src/myia/engines/registry.py:84-92` `ENGINE_REGISTRY`(name→惰性类工厂);auto 降级链 registry.py:67-75;`fetch_source` 只经此注册表(registry.py:153-232)。**无「进程内插件引擎」机制。**
- `shishi osint/proxy --json` 均纯 CLI stdout,不构造 Store、不落库(cli.py:2652-2683、2700-2732)。
- 全链(classify→dedup→store→push)唯一入口 = Pipeline:`_cmd_run` 构造(cli.py:721-738)→ `pipeline.run()`(src/myia/pipeline.py:936)→ fetch 逐源 `fetch_source`(pipeline.py:1523)→ 阶段表 pipeline.py:1065-1069 → dedup `store.save_item`(pipeline.py:1709;Store 接口 src/myia/store/base.py:40)。

**通路 A(采纳,findings 走全链)**:注册新引擎 `engine: credhunter` —— 改 schema.py:175-177 `EngineName` + registry.py:84-92 `ENGINE_REGISTRY` + 新增 `src/myia/engines/credhunter.py`(引擎内复用 cli.py:2611-2628 的 compile+exec 加载器调 `plugins/myia-credhunter/adapter.py`);产出 items 后下游 classify/dedup/store/push **零改动自动复用**。代价:`EngineName` 是 12 节公开契约,受 docs zh/en + test_docs/test_skill_doc 三锁与 golden 约束;引擎缺失/适配器缺失须结构化降级(照 registry.py:179-212 EngineFailure 模式)。

通路 B(不采纳,与 PRD「进管线」冲突):纯 CLI 子命令止步 stdout JSON;入链只能配 direct_api HTTP 端点 = 现存 myia-credentials remote 模式。

**credcheck 例外**:验证/余额是「读库→探测→回填」的后处理,不是 fetch;走独立 CLI 子命令(proxy 样板),不入 ENGINE_REGISTRY。

## 3. schema 插件节与扩展点

- `CategoryPluginConfig`(schema.py:1412-1439):仅 `id/requires/modes`;id 校验 `_PLUGIN_ID_RE`(schema.py:236);`requires` 封闭词表 `REQUIRES_TOKENS=("docker",)`(schema.py:173)。
- `modes` 仅 `local`(compose/install 至少其一)/`remote`(endpoint 必须 http(s);**token 只许 keychain:,env: 也拒**,schema.py:1377-1393)。
- 加「native 进程内 mode」需动:PluginModesConfig(schema.py:1399-1400,StrictModel 未知键即拒)+ REQUIRES_TOKENS(schema.py:173)+ manifest tier 词表(src/myia/plugins/manifest.py:225 `_check_tier`、PluginManifest manifest.py:171-252)。
- **规避方案(采纳)**:credhunter 走「品类 YAML source 的 `engine: credhunter`」表达,plugin: 节维持现状(remote 模式给 myia-credentials 旧源),**不加新 mode**——契约面最小改动。

## 4. golden 与官方插件同步清单(改声明面必做)

1. `tests/test_plugins.py:48` `OFFICIAL_PLUGINS` 品类 YAML 电池(现 = stocks/ai-news/wool/games/gpu-prices)。
2. `tests/test_plugin_packages.py:51-58` `OFFICIAL_PACKAGES`(六件套含 myia-credentials)+ 61-68 `EXPECTED_TIERS`。
3. push golden:`tests/fixtures/push_targets_golden_before.json`(test_push_schema_targets.py:18 消费,235-255 对 golden 列名 YAML 做 push 子树 additive 等价断言 258-279;credentials.yaml 在列,**无再生成脚本,手工维护**)。
4. `TestCategoryWiring`(test_plugin_packages.py:227-254)钉死品类 plugin 节 ↔ manifest id/requires/endpoint/token 一字不差。
5. 桌面首跑种子钉 4 YAML(tests/test_desktop_sidecar_protocol.py:727-738;拷贝逻辑 desktop/entry.py:289-315)。**exposure.yaml 若要进桌面默认集需同步此断言**。

## 5. keychain/env 与限速

- 引用解析:`parse_secret_value`(schema.py:368-404,支持 `Bearer env:VAR`)→ 运行期 `resolve_credential`(schema.py:447-489)→ `src/myia/secrets.py` `get_secret`(:250)/`resolve_keychain_ref`(:338)。插件适配器运行在 CLI 进程内,**可直接 `from myia.secrets import get_secret`**(注意:现有适配器先例只 import httpx,import myia.* 无先例,评审时需说明理由=读 keychain/限速器属宿主能力)。
- 限速:`RateLimiter`(src/myia/engines/fetch_base.py:852 起,async:最小间隔+jitter,`tighten()` 取最严)+ `HostLimiterRegistry`(fetch_base.py:917-943 同 host 合并);**仅 async,无同步限速器**(myia-proxy 适配器是同步串行 pacing)。credhunter 引擎跑在 async fetch 上下文 → 可直接复用 RateLimiter;CLI 型 credcheck 自带串行+间隔即可。

## 6. 未核实

- push golden 再生成脚本(疑不存在,手工维护口径)。
- 适配器 import myia.* 的政策边界(无先例,需评审口径)。
- desktop Resources 种子清单维护位置(桌面产物禁扫)。
