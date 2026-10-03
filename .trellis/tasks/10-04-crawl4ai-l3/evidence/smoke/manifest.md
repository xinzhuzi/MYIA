# 真网冒烟证据清单(10-04-crawl4ai-l3,AC5 主证;附 AC1-4/6/7 判定材料)

- 日期:2026-10-04;执行者:真网冒烟员(工作流子代理);机器:macOS darwin 25.4.0 arm64
- 环境:uv venv,`crawl4ai 0.9.4` + `playwright 1.63`(浏览器二进制 chromium_headless_shell-1243,本会话补装,安装前引擎结构化报缺、报文含 crawl4ai-setup 提示——见 pytest-real-gate.log 首跑段)
- 冒烟目标:`https://aihot.news/items/bzodztryi4kvwm4kz9mrwb6nn`(= tests/test_crawl4ai.py:922 `SMOKE_TARGET_DEFAULT`)。选证:该 id 仍在根页 https://aihot.news/ 当日卡片清单中(curl 实取根页 grep `/items/` 首条即它;curl 直访 items 页 403 系 UA 反爬,引擎基座 UA 可过)。真实库 items 表无 aihot.news 文章 URL(仅 1 条 github.com/KKKKhazix/AIHOT),故按任务口径「测试默认值可用则直接」采用默认值。

## 1. real-gate pytest 真跑(AC5 主入口)

- 命令:`MYIA_SMOKE_REAL=1 uv run --no-sync python -m pytest tests/test_crawl4ai.py -k real -q -s`
- 结果:**1 passed, 1 skipped, 39 deselected in 1.88s**(exit 0;wall 2.4s)
- 冒烟行:`[smoke] target=https://aihot.news/items/bzodztryi4kvwm4kz9mrwb6nn items=1 markdown_len=3949 same_domain_images=2`
- 断言(实跑生效):`items` 非空 ✓、markdown 载荷非空 ✓;`shishi.vision.collect.markdown_image_urls` 可导入(vision extras 在场)→ 附打同域图数 2
- crawl4ai 侧计时:FETCH 0.78s / SCRAPE 0.01s / COMPLETE 0.80s(真实浏览器渲染)
- 日志:`pytest-real-gate.log`(补装后通过跑原始输出;首跑环境失败摘录另见 `playwright-setup-note.md`——首跑输出被复跑 tee 同路径覆盖,注记系当场捕获的错误段重建,已如实标注)

## 2. 直接引擎级真跑(不经 pytest;AC5 交叉证 + 看图线联动证)

- 脚本:`direct_engine_smoke.py`——`registry.fetch_source`(src/shishi/engines/registry.py:214)以显式 `engine: crawl4ai` 源视图真抓,真 httpx client + 一次性 SQLiteStore(hint 回写可见)
- 命令:`uv run --no-sync python .trellis/tasks/10-04-crawl4ai-l3/evidence/smoke/direct_engine_smoke.py`
- 结果:**RESULT=PASS(exit 0)**
  - `engine=crawl4ai items=1 skipped=False failures=[]`
  - `markdown_len=3949`,title=`GPT-6.1 Sol (Max) 进入 Agent Arena 第 5 名,以更低成本逼近前列模型 · AIHOT`
  - `same_domain_images=2`(两条均为 aihot.news 同域 `api/img-proxy?u=…pbs.twimg.com…` 形态——markdown 图 URL 同域过滤按页域名收代理图)
  - `hint_after=crawl4ai`(成功引擎选择回写 store)
  - 耗时 1.99s(浏览器 FETCH 0.74s)
- 日志:`direct-engine-run.log`
- 看图线联动结论:L3 无 extract 自动结构化 markdown 含 `![alt](绝对URL)` 图,`vision.collect.markdown_image_urls`(src/shishi/vision/collect.py:218)从该 markdown 收出 2 条同域图——「L3 源 → markdown 图 → 图处理线」JS 页路径打通。

## 3. 备选口径(未触发)

cocoloop 帖子页备选未使用:两路真跑(①/②)首跳即成,无反爬失败/超时,不构成任务定义的「网络失败→换备选重试」触发条件。

## 4. AC1-4/6/7 判定材料(前面阶段产物 + 本会话补跑)

- **AC1-4(fixture 探测六件)**:tests/test_crawl4ai.py:748-909——`test_auto_zero_result_probes_l3_and_catches_js_shell`(AC1,含二跑 `test_second_run_hint_first_goes_straight_to_l3`)、`test_true_empty_page_probe_rolls_back_to_l2_semantics`(AC2,hint 不变+第二跑零浏览器开销)、`test_fingerprint_skip_never_probes`(AC3,断言不构造浏览器)、`test_probe_budget_exhausted_fourth_source_not_probed`(AC4)、`test_l3_probe_exception_rolls_back_source_not_failed`/`test_explicit_engine_config_never_probes`(边界)。本会话补跑:`-k "probes_l3 or hint_first or rolls_back or never_probes or budget_exhausted or probe_exception"` → **7 passed**(ac1-4-probe-tests.log)。
- **AC6(文档)**:docs/zh/schema.md:100-102、docs/en/schema.md:113-116 已含 `engine_options.crawl4ai`(timeout/headless/browser_options/run_options + 自管键禁覆盖)。本会话补跑 `uv run --no-sync python -m pytest tests/test_docs.py tests/test_skill_doc.py -q` → **116 passed**(ac6-docs-tests.log)。
- **AC7(基线)**:开工基线 **1822 passed / 14 skipped / 0 failed**(CI 同款 `uv run --no-sync python -m pytest -q`,录于 archive/2026-10/10-03-v12-crawl4ai-l3/prd.md:87)。本会话复跑同款命令(`-q --tb=short`):**3044 passed, 19 skipped, 0 failed, 56.22s**(exit 0)——0 failed 无回归;总数较基线增长系并行线(仓更名 shishi 等)扩测所致,非本任务缩绿。日志:ac7-full-pytest.log。
