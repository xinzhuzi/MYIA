# PRD:crawl4ai L3 收实——L2 零结果降级探测 + 真网 JS 源冒烟 + 文档补全

## 背景

主人 2026-10-03 选定「crawl4ai L3 实装」开工(做软件方向,v1.2 池)。**建档探查反转(2026-10-04 本会话实读)**:该池档旧口径「L3 薄层实质缺位」已过时——并行线归档任务 `archive/2026-10/10-03-v12-crawl4ai-l3` 已完成实装并验收(commit 8e8d2ab/60022ff/dee06cd;真库接入 `crawl4ai.py:114-139/211-298`、惰性 `shishi[crawl4ai]` extras、CI 零外网假模块回放测试 955 行 34 例、基线 1822 全绿;本机 .venv 已装 crawl4ai 0.9.4+playwright 1.63)。上游核实:unclecode/crawl4ai 84.7k★ Apache-2.0(2026-10-03 gh api),markdown 图片输出标准 `![alt](绝对URL)`(vendored html2text `ignore_images=False`,urljoin 绝对化)与看图线 `_MARKDOWN_IMAGE_RE`(collect.py:171)兼容。

**真剩余面(本任务)= 三件**:

### 1. L2 零结果不降级 L3(产品缺陷,核心)

- 现状:auto 链 L2 static_html 提取 0 条视为「成功+健康空页」,并**回写 hint 锁死 L2**(registry.py:228-229;static_html.py:113-115;pipeline.py:1556-1563)——JS 渲染空壳页(正需要 L3 的页面)在 auto 模式下**永远到不了 L3**;只有显式 `engine: crawl4ai` 或 L2 真报错才触发 L3
- 目标:零结果触发一次**有界 L3 探测**——L3 也零则维持空页语义(不误报),L3 出条则采用并 hint 改 L3;防浏览器开销滥用(探测只对零结果页,且受页预算/单 run 探测上限约束)
- 指纹 skip(变更指纹未变)不触发探测——那是真·无更新,不是 JS 壳

### 2. 真网 JS 源冒烟(从未实跑的诚实边界)

- 归档档 prd.md:91-93 明言真实 JS 源 smoke 需 `MYIA_SMOKE_REAL=1` 手动、从未跑过;本任务补:挑 1-2 个真实 JS 渲染页(候选:aihot.news 文章页——本会话已证其为 JS-SPA、curl 静态拿不到图)真跑 L3 全链,断言条目/markdown/同域图收集;顺带验证看图线 JS 页路径(L3 源 → markdown 图 → OCR)
- 证据进 evidence/,失败如实(网络/反爬),不造假

### 3. 文档补全

- `docs/zh/schema.md`(及 en 对应件)engine_options 节补 crawl4ai 键(timeout/headless/browser_options/run_options 透传,自管键清单)——现只写了 firecrawl/scrapling/stealth_browser(schema.md:97-99)

## Requirements

1. 零结果 L3 探测:降级链语义改动集中在 registry/pipeline,守三条底线——指纹 skip 不探测、探测有单 run 上限(拍板①)、L3 探测失败/零条回退原空页语义零误报;测试覆盖:JS 壳页经探测被 L3 接住、真空页探测后仍空、指纹 skip 不探测、探测预算耗尽、hint 回写正确
2. 真网冒烟:`MYIA_SMOKE_REAL=1` 跑通至少一个真实 JS 页(条目+markdown 断言;有图则图收集断言);8080/vision 链路顺带验证(可选)
3. schema 文档:zh/en 双语补齐 crawl4ai 键,示例对齐现码(engine_options 声明面)

## Acceptance Criteria

> 冒烟判定 2026-10-04(证据 8 件 evidence/smoke/,清单 manifest.md):**AC 1-7 全 passed**。

1. fixture:JS 壳页(假 crawl4ai 回放)在 auto 模式下 L2 零条 → L3 探测 → 条目落地且 hint=L3;第二跑直接走 L3
   ✅ passed:`test_auto_zero_result_probes_l3_and_catches_js_shell`(tests/test_crawl4ai.py:748,含 30s 短帽注入与预算消耗断言)+ `test_second_run_hint_first_goes_straight_to_l3`(:771,二跑直达 L3 零重复页面请求);定向补跑 7 passed(ac1-4-probe-tests.log)
2. fixture:真空页 L3 探测零条 → 结果仍为空页语义,run 健康,无重复浏览器开销(hint 不变)
   ✅ passed:`test_true_empty_page_probe_rolls_back_to_l2_semantics`(:797;items=[]/engine=static_html/hint 锁回 static,含二跑指纹 skip 零浏览器构造断言);在上述 7 passed 内
3. 指纹 skip 页零探测(断言不构造浏览器)
   ✅ passed:`test_fingerprint_skip_never_probes`(:827;`fake.core.calls == []` 且 `browser_configs == []`);在上述 7 passed 内
4. 单 run 探测上限生效(超限源零结果按空页收,不阻塞)
   ✅ passed:`test_probe_budget_exhausted_fourth_source_not_probed`(:864;budget 3→0,第四源零探测按空页收);另 `test_l3_probe_exception_rolls_back_source_not_failed`(:891,探测异常回滚源不失败);在上述 7 passed 内
5. 真网:≥1 个真实 JS 页 L3 全链真跑出条目(证据在档;网络失败如实标注并留重试口径)
   ✅ passed:双路真跑均成——①`MYIA_SMOKE_REAL=1 pytest -k real` → 1 passed(items=1/markdown_len=3949/same_domain_images=2,浏览器 FETCH 0.78s;pytest-real-gate.log);②直接引擎级 `registry.fetch_source`(显式 engine=crawl4ai)→ RESULT=PASS(items=1/markdown 3949/同域图 2/hint 回写 crawl4ai/1.99s;direct-engine-run.log)。证据 8 件 evidence/smoke/(manifest.md)。重试口径未触发(首跳即成,无网络失败):备选 cocoloop 帖子页与 `MYIA_SMOKE_TARGET` 覆写口径已录 manifest.md §3 与 tests/test_crawl4ai.py:920 注释
6. docs/zh+en schema.md crawl4ai 键齐,`test_docs` 类文档一致性测试(若有)绿
   ✅ passed:docs/zh/schema.md:100-102、docs/en/schema.md:113-116 已含 `engine_options.crawl4ai`(timeout/headless/browser_options/run_options+自管键禁覆盖);实跑 `pytest tests/test_docs.py tests/test_skill_doc.py` → 116 passed(ac6-docs-tests.log)
7. 基线不回归:pytest/vitest(不动 UI 则免)/tsc 以开工基线为准;CI 同款命令
   ✅ passed:基线 1822 passed/14 skipped/0 failed(archive/2026-10/10-03-v12-crawl4ai-l3/prd.md:87);本会话 CI 同款 `uv run --no-sync python -m pytest -q --tb=short` → 3044 passed/19 skipped/0 failed/56.22s exit 0(ac7-full-pytest.log)——0 failed 无回归,总数增长系并行线(仓更名 shishi 等)扩测非缩绿;UI 未动,vitest/tsc 按本条口径免

## 已拍板决议(grill Round 1,2026-10-04,主人 /workflow 即执行令——四项按推荐)

**复查 4 low 终局注记(2026-10-04)**:#1 首遇判定用 run 初 hint 快照(hint 中途被清的源错过唯一探测机会,与 design 字面分歧;语义边角留档不改)/#2 外层取消时预算已扣回滚不执行(默认配置不触发,留档)/#3 文档手动出口缺失 **已修**(3332fc2:zh/en schema engine 行补探测行为+`engine: crawl4ai` 出口)/#4 evidence 缺位系复查时序(复查先于冒烟),终态 evidence/smoke/ 8 件在档。

1. **探测策略 = 首遇探测(每源终身一次)**:仅 auto 链 + static_html 真零结果(非指纹 skip)+ 无既有 hint + 预算>0 四条件齐触发;探测零/异常回滚空页语义并锁回 static(与今天行为一致,一次性成本);显式 `engine:` 配置永不探测(尊重用户选择)。精化理由与不变量见 design.md
2. **单 run 探测预算 = 3 源**(`FetchContext.l3_probe_budget` 初值 3)
3. **探测页预算 = 30s 短帽**(经 engine_options.timeout 注入实现)
4. **冒烟对象 = aihot 文章页一个**(现成 JS-SPA 实证,不加第二源)
5. **存量注记**:真实库 engine_hints 实测仅 1 条(direct_api),批量重探出口不做;手动重探出口 = 源配置写 `engine: crawl4ai`

> **2026-10-04 更正**:前条「superseded 重复档」注记**作废**——本档活跃,并行线已续交付 L3 零结果首遇探测(e811162/3332fc2);归档撤回,状态由线主自管。
