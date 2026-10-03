# Design:crawl4ai L3 收实(10-04-crawl4ai-l3)

事实基础 = prd.md 背景/探查节。核心机制事实(本会话二轮实读):`fetch_source` 的引擎循环里,**任一引擎成功(哪怕 0 条)即 `set_engine_hint` 并 return**(registry.py:228-239);指纹 skip 走 `skipped/skip_reason` 通道与 0 条区分;引擎经 `resolve_engine` 惰性直取(registry.py:93-128),`FetchContext` 是 per-run 共享体(store/limiter/clock/sleep/proxy_pools/keychain,fetch_base.py:1429-1443)。

## 零结果 L3 探测:每源终身一次(拍板①的精化形态)

**触发条件(全部满足才探测)**:
1. 本轮走的是 auto 链(显式 `engine:` 配置的源**尊重用户选择,永不探测**)
2. 命中的引擎是 `static_html` 且 items==0 且 `skipped is False`(真零结果;指纹 skip 直接 return 不探测)
3. `context.store.get_engine_hint(source_key) is None`(**首遇源**——已有 hint 的源(含已被锁 L2 的存量源)零探测零打扰)
4. `crawl4ai` 在链上且本轮尚未尝试过;`FetchContext` 的探测预算 `l3_probe_budget`(初值 3,拍板②)>0

**循环改造点(registry.py 引擎循环 success 分支)**:
```
成功分支(items/skipped 赋值后):
  if items 非空 or skipped: set_hint(引擎); return            # 现行为
  if 触发条件全满足:
      l3_probe_budget -= 1; 记 zero_fallback = 浅拷贝当前 outcome
      **不设 hint,不 return,continue 走链上的 crawl4ai**      # 探测=正常走一级引擎
      (L3 页预算:探测路径给 30s 独立短帽,拍板③——经 engine_options.timeout
       覆写实现的 run 级预算注入,失败/超时按引擎失败记录)
  else: set_hint(引擎); return                                 # 维持现语义
后续:
  L3 探测 >0 条 → set_hint(crawl4ai); return(自然路径,现行为)
  L3 探测 0 条/异常 → **回滚 zero_fallback**(items=[]/engine=static_html/
      skip_reason=None),set_hint(static_html),failures 里 L3 记录保留
      (观测性:日志一行「L3 探测零结果,回退 L2 空页语义」)
```

**为何终身一次是对的**:真空的 JS 壳页每 run 重探 = 每 run 一次浏览器开销,不可接受;首遇一次探测后锁 static,与今天的行为完全一致(一次性成本换「新 JS 壳源首跑就进 L3」)。存量已锁源不受影响;要手动重探 = 显式 `engine: crawl4ai`(现成出口)。

**不变量**:探测失败绝不放大为源失败(回滚 fallback);代理短路逻辑(proxy_* break)不受探测影响;robots 由 L3 引擎自查(现行为)。

## 真网冒烟:扩既有 real-gate 测试(不另立脚本)

`tests/test_crawl4ai.py:727-736` 的 `MYIA_SMOKE_REAL` 门默认跳过——扩展为双 env 参数:`MYIA_SMOKE_REAL=1`(开关,现有)+ `MYIA_SMOKE_TARGET=<url>`(默认 aihot 文章页 URL,拍板④)。断言:exit 0、items>0、content/markdown 非空;若 `myia.vision.collect` 可导入则附打同域图收集数(看图线 JS 页路径联动验证)。**CI 零外网不变**(默认跳过);真跑证据(manifest/transcript)另存任务 evidence/。

## 文档补全

`docs/zh/schema.md:97-99` engine_options 命名空间段补 crawl4ai 键(对照引擎自管面:crawl4ai.py:96/104/152-174):`timeout`(缺省 60s)、`headless`(缺省 true)、`browser_options`/`run_options` 透传(自管键 headless/proxy/proxy_config/headers 与 cache_mode/page_timeout 禁覆盖);`docs/en/schema.md` 同步。`test_docs.py` 只断言特定主题(凭据/链接),新增示例不触雷,跑一遍确认;`test_skill_doc` 枚举面不涉。

## 测试策略

- registry 层(fixture 假 crawl4ai 回放,沿用 install_fake_crawl4ai):
  - 首遇 JS 壳:L2 零条 → 探测 → L3 出条 → hint=crawl4ai;第二跑 hint-first 直达 L3
  - 真空页:L3 零条 → 回滚空页语义,hint=static,run 健康
  - 指纹 skip:零探测(断言不构造 crawl4ai 引擎)
  - 显式 engine: static_html:零探测
  - 预算耗尽:第 4 个首遇零结果源不探测,按空页收
  - L3 探测异常(dependency_missing 也算):回滚源不失败
- docs:zh/en 双件改后 `test_docs`/`test_skill_doc` 绿
- 真网:`MYIA_SMOKE_REAL=1 MYIA_SMOKE_TARGET=<aihot 文章>` 手跑,证据入档

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 探测拖慢首跑(浏览器冷启 ~数秒/源) | 预算 3/run + 终身一次 + 30s 短帽 |
| L3 对非 JS 空页(真没内容)白探一次 | 一次性成本,回滚零误报;日志可观测 |
| 存量源已锁 L2 无法享受 | 设计如此(免重复开销);文档写明手动出口 engine: crawl4ai |
| 并行会话热点(registry/pipeline) | 开工 rebase;改动集中单分支函数 |

## 回滚

改动集中 registry.py 引擎循环 + FetchContext 一字段 + 文档;revert 即回今天行为(探测分支纯增量)。
