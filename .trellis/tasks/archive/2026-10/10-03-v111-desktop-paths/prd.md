# v1.1.1 桌面数据通路:应用数据根统一 + 插件随包 + 首跑种子(P0)

## 报障(2026-10-03 主人,已装 /Applications/MYIA.app 实测)

> 仪表盘数据不可用(sidecar 错误码 plugins_dir);情报流不可用(internal_error);请求失败:插件目录不存在: plugins

## 探查结论(2026-10-03 对已装二进制全方法探测,证据在案)

### 逐方法矩阵(cwd=/ 模拟 Finder 启动)

| 方法 | 结果 | 根因 |
|---|---|---|
| version | OK | — |
| health | **ERROR plugins_dir(相对 "plugins")** | cwd 依赖 |
| plugins.list | OK 但空,dir=~/.myia/plugins(**该目录不存在**) | 家目录策略,无种子 |
| store.items | **ERROR internal_error: unable to open database file** | db="myia.db" 相对 cwd(/ 下不可建) |
| logs.tail / secret.list / run.status | OK | 内存态/钥匙链 |
| doctor | **ERROR plugins_dir** | 同 health |
| run.start | invalid_params(需 yaml 路径) | UI 侧传参形态待对齐(修路径后复核) |
| sources.write | 校验正常 | 路径同上会踩同一坑 |

### 三个根因

1. **三套路径策略并存**:health/doctor 用相对 `plugins`;store.items 用相对 `myia.db`;plugins.list 用 `~/.myia/plugins`——同一 serve 二进制三种解析,互不一致
2. **cwd 依赖**:从仓库目录跑同一二进制 health 即 OK(plugins_dir=plugins 落在仓库)——桌面上下文 cwd=/ 全线断
3. **空壳发行**:.app Resources 只有 icon+夹具 plugin.yaml,**无随包官方插件、无首跑种子**;~/.myia 不存在也无创建逻辑 → 用户拿到的是无插件无库无数据的壳

### 附带教训(写入验收防复发)

v1.1 的 UI 测试全部 mock sidecar,未做真实 .app 启动冒烟 → 五屏"可用"只在 mock 层成立。本任务验收必须含**真实安装冒烟**。

## 修复设计(design 可细化)

1. **统一应用数据根 myia_home()**:macOS `~/Library/Application Support/MYIA` / Windows `%APPDATA%\\MYIA` / Linux `~/.myia`(现有 plugins.list 的 ~/.myia 引用收编至此);db/plugins 默认一律 <home>/myia.db、<home>/plugins
2. **serve 上下文解析**(优先级):显式 params > `MYIA_HOME` env(**Rust main.rs spawn sidecar 时注入**,一处) > bundle 探测(exe 位于 .app → 平台数据根) > 开发回退 cwd(仓库内运行行为不变)
3. **插件随包 + 首跑种子**:build 侧把官方插件(ai-news/wool/stocks/gpu-prices)收进 Resources/plugins/;serve 首跑发现 <home>/plugins 为空 → 从 bundle 拷贝可写副本(记录 seeded 标志)
4. **空态语义**:health 在零插件时返回 healthy+first_run 标志而非报错;UI 各屏空态引导(跑第一个插件)而非错误
5. run.start 的参数形态与 UI 对齐复核(sources 屏写回同)

## Acceptance Criteria

- [x] 从 `/` cwd 直接跑已装二进制:health/plugins.list/store.items/doctor 全 OK,无一屏报错
- [x] db 与 plugins 落在平台数据根(实查文件);首跑后 **health.plugins ≥4 官方插件 且 plugins.list 无错误返回**(原「plugins.list ≥4」口径修正:官方插件是品类 YAML 形态,经 health/doctor 可见;plugins.list 是市场面,首跑空是合法态 —— design.md D7)
- [x] 仓库内开发行为不回退(cwd 回退语义有测试:`test_serve_context_dev_fallback_unchanged`)
- [x] **真实安装冒烟**:重装 .app → 全方法矩阵 cwd=/ 全 OK;真首跑(Rust 注入 MYIA_HOME)自动建根+种子;截图/输出留档 `evidence/`(像素级核对留主人;五屏数据通路经程序级矩阵与 40 vitest 用例锁定)
- [x] 三种上下文解析优先级有单测;pytest+vitest 绿(`tests/test_baseline.py::test_pipeline_run_feeds_trend_context_into_rendered_card` 预存失败,干净 main 同样失败——`ModuleNotFoundError: No module named 'tests'`,与本任务无关,待另修)
- [x] 重打包装机验证(dmg 21.92 MiB,已装 /Applications)
- [x] run.start 参数形态与 UI 对齐复核:yaml = health 报告的品类 YAML 绝对路径原样回传(与 sources.write 同口径);feed 空态 CTA「运行第一个插件」走此路(dry 闭环探针验证)

### 冒烟实况(2026-10-03,装机后)

- cwd=/ 无 MYIA_HOME(bundle 探测路):version/health/plugins.list/store.items/logs.tail/secret.list/run.status/doctor 全 OK;health plugins=4 healthy=true
- 真首跑(清根后 `open -a MYIA`,Rust 注入路):serve 进程 env 带 MYIA_HOME;数据根自动建 + 四件套种子 + `.seeded`
- run.start dry 闭环:run_id=1 → completed(exit=1 config_error)。~~归因初记:四件套 push 全引用 `env:FEISHU_CHAT_ID`,无凭据首跑=引导态~~ **勘误(2026-10-03 冒烟复测)**:真因为冻结包缺 `myia_classifier/data/keywords.json`(PyInstaller datas 未收录),classify(builtin: true)构造期即报「分类关键词表加载失败」退出,run 未达 push 段;修复(`build-sidecar.sh --add-data` + 重打 sidecar)后同矩阵复测:dry run exit=0 success,无凭据 real run 为 exit=3 partial(push `ok:false` 凭据引导态,采集与分类全部正常)
- 旧 ~/.myia 未迁移(桌面从未提供市场 UI 入口,无既有用户数据,design.md D7)

## Notes

- P0 发布阻断级(已装用户核心屏全断);涉及 desktop/entry.py、src-tauri/main.rs、build-sidecar.sh/tauri.conf resources、ui-src 空态
- 探查证据:2026-10-03 serve 全方法矩阵(本 PRD 表格即摘要)

## 执行记录(2026-10-03,受主人委托)

- P0 已修:应用数据根统一 + 插件随包 + 首跑种子的修复已交付(修复提交见 task.json notes 记 45639c3,验收全勾,状态对齐仓惯例 review=待归档)。
- 真机冒烟结果见工作流报告;PRD 内已载 2026-10-03 冒烟复测勘误(冻结包缺 myia_classifier/data/keywords.json 已修,重打 sidecar 后 dry run exit=0 success,无凭据 real run exit=3 partial 凭据引导态)。

## 验收记录(2026-10-03,受主人委托代验)

结论:**accepted**(7/7 全有证据;其中矩阵/单测/装机产物为代验本轮第一手实跑,重打包与真首跑为记录佐证+产物核验)。

1. **cwd=/ 已装二进制矩阵 ✅(第一手)**:本轮实跑 `cd / && printf …各方法… | /Applications/MYIA.app/Contents/MacOS/myia-core serve` → version OK / health OK(plugins=5 healthy=True first_run=False)/ plugins.list OK(installed=0,市场面空=design D7 合法态)/ store.items OK(items=2,db 可开)/ doctor OK / logs.tail OK,均无 error;run.status 返回 run_not_found 属语义错误(新数据根无 run 记录)非路径故障。MATRIX_EXIT=0。
2. **平台数据根实查 ✅(第一手)**:`~/Library/Application Support/MYIA/` 实存 myia.db(151,552 字节)/ plugins/(ai-news、gpu-prices、myia-demo、stocks、wool 共 5 份品类 YAML)/ `.seeded`(10-03 10:11);health.plugins=5 ≥4;plugins.list 无错误返回。
3. **开发回退不回退 ✅(第一手)**:`uv run --no-sync python -m pytest tests/test_desktop_sidecar_protocol.py -q -k "serve_context or seed or first_run or follow_home or three_platforms"` → **10 passed**(含 test_serve_context_dev_fallback_unchanged)。注:该测试文件属并行会话在途禁区,本轮仅只读执行未改一字。
4. **真实安装冒烟留档 ✅**:`evidence/2026-10-03-first-run-dashboard.png`(399,429 字节,07:33)在档;全方法矩阵由代验本轮对现装 .app 第一手复跑通过(见第 1 条);像素级目视核对按 PRD 原口径留主人(非阻断,程序级矩阵+vitest 已锁定)。
5. **三上下文优先级单测齐 ✅(第一手)**:env 优先级 test_serve_context_myia_home_env(tests/test_desktop_sidecar_protocol.py:640)、bundle 探测 test_bundle_detection_dot_app(:673,-k bundle → 2 passed)、dev 回退 test_serve_context_dev_fallback_unchanged(:660)全过;vitest 本轮实跑 89/89 全绿(TEST_EXIT=0)。PRD 所记 test_baseline 预存失败与本任务无关、已另立 10-03-test-baseline-import 档,不在本验口径。
6. **重打包装机 ✅(产物核验)**:现装 /Applications/MYIA.app(10-03 10:35-10:36 构建,MYIA 壳 + myia-core sidecar + Resources/plugins 随包)矩阵实跑全 OK(第 1 条);dmg 产物在 desktop/src-tauri/target/release/bundle/dmg/MYIA_1.1.1_aarch64.dmg。**注记**:该 dmg 现为 10-03 10:51 由 v1.1.2 并行批次重建的版本(123,200,429 字节 ≈117.5 MiB),PRD 所记 21.92 MiB 为验收时点构建,已被后续并行重建取代——装机可用性结论不受影响(第一手复验的就是更新后的装机)。
7. **run.start 参数形态对齐 ✅(两端实读+单测)**:sidecar 端 entry.py:1657 `params.get("yaml")` 非字符串即 invalid_params;UI 端 feed-screen.tsx:237,250 空态 CTA「运行第一个插件」→ `api.runStart({ yaml: plugin.file })`(health 品类 YAML 绝对路径原样回传,与 sources.write 同口径);test_run_start_default_db_follows_home 在第 3 条 10 passed 内。
- 修复提交在史:`git log -1 45639c3` → "fix(desktop): unify app data root — MYIA_HOME injection, bundled plugins, first-run seed";Rust 注入在案 desktop/src-tauri/src/main.rs:169,207。

处置:accepted → `task.py archive --no-commit`(禁 git,提交留主人)。
