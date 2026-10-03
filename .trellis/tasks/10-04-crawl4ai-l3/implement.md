# Implement:crawl4ai L3 收实(10-04-crawl4ai-l3)

门禁纪律同全仓(重定向文件+显式 $?;CI 同款命令 `uv run --no-sync python -m pytest -q --tb=short` 等;外来红 test_credhunter/skill_doc/aipocket/shishi 豁免口径)。

## 0. 前置

- [x] 基线核对:rebase 最新 main(registry.py 是并行热点);四道门禁记基线 —— 基线 1822 passed/14 skipped/0 failed(archive/2026-10/10-03-v12-crawl4ai-l3/prd.md:87,CI 同款命令);树已同步并行线扩测(本会话全量 3044 passed,见 §4)
- [x] 拍板①-④回收(默认按推荐:首遇探测+预算3+30s 帽+aihot 目标)—— prd「已拍板决议」节四项全录(grill Round 1,2026-10-04)

## 1. 零结果探测(registry.py + FetchContext)

- [x] FetchContext 增 `l3_probe_budget: int = 3`;pipeline 构造点同步(默认值即可,测试可注)—— fetch_base.py:1465(`DEFAULT_L3_PROBE_BUDGET`);pipeline.py:898 `_context_for_run` 走默认值
- [x] 引擎循环 success 分支改造:零结果首遇探测(触发条件四条)/回滚 fallback/hint 终态;显式 engine 与指纹 skip 零探测 —— registry.py:302-330 首遇武装(四条件:auto+static_html 真零结果+无 hint+预算>0)/:252 探测 30s 短帽注入(`_l3_probe_source`)/:293-298 探测零条回滚空页语义
- [x] 测试六件(见 design 测试节)—— tests/test_crawl4ai.py:748-909:六件+边界二件(explicit_engine/探测异常);定向补跑 7 passed(evidence/smoke/ac1-4-probe-tests.log)
- 验证:定向 pytest tests/test_crawl4ai.py tests/test_registry*(如有);回滚点:独立 commit —— 定向 7 passed;全量 3044 passed/0 failed(ac7-full-pytest.log)
  [^独立 commit 回滚点未启用:并行会话在场,提交由终检落档员按白名单收口]

## 2. 真网冒烟测试扩展

- [x] test_crawl4ai.py real-gate 增 MYIA_SMOKE_TARGET 参数(默认 aihot 文章页)+断言扩展(items/markdown/同域图数)—— tests/test_crawl4ai.py:920-935(`SMOKE_TARGET_DEFAULT` + env 覆写 + items/markdown/同域图断言)
- [x] 真跑一次:`MYIA_SMOKE_REAL=1 MYIA_SMOKE_TARGET=<aihot 文章 url>` → 证据(manifest/输出)存 evidence/ —— 双路成:①pytest -k real 1 passed(items=1/markdown_len=3949/同域图 2,pytest-real-gate.log);②直接引擎级 RESULT=PASS/1.99s/hint 回写 crawl4ai(direct-engine-run.log);证据 8 件 evidence/smoke/(manifest.md)
- 验证:默认路径(不设 env)测试仍跳过=CI 零外网;回滚点:同 commit —— 默认路径在全量跑内(3044 passed 含 skip 19,零外网)

## 3. 文档补全

- [x] docs/zh/schema.md + docs/en/schema.md engine_options 段补 crawl4ai 键(timeout/headless/browser_options/run_options+自管键禁覆盖说明)—— docs/zh/schema.md:100-102、docs/en/schema.md:113-116
- 验证:pytest tests/test_docs.py tests/test_skill_doc.py;回滚点:独立 commit —— 116 passed(ac6-docs-tests.log)

## 4. 回归与收尾

- [x] 四道门禁全绿(数字对基线);spec 登记(python/index 或 engines 相关节:零结果探测语义一行)—— pytest 3044 passed/19 skipped/0 failed/56.22s vs 基线 1822/14/0(增长系并行线扩测,0 failed 无回归);UI 未动 vitest/tsc 免;spec 登记 python/index.md engines 行(本次落档)
- [x] prd AC 1-7 逐条勾;待拍板决议回写 —— AC 1-7 全 passed(逐条证据见 prd 验收节);决议已录 prd「已拍板决议」节

## 显式不做

L4+ 引擎的零结果探测(只探 L3);HTML 特征启发式(拍板①弃);存量源自动重探;代理池行为改动。
