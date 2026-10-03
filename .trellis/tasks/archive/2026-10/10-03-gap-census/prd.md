# PRD:项目缺口全量普查(缺陷矩阵与修复排期)

## 背景

主人 2026-10-03 指示:全仓深度排查"还有什么没补全",缺陷找全之前不动手修,先把本档写好。
本任务**只落档不改产品代码**;修复按第 6 节路由另行开任务/回炉。

## 排查方法与基线(2026-10-03 07:2x–07:4x)

- 三路只读探查代理:①代码占位符/未实现痕迹 ②文档宣称 vs 实际漂移 ③桌面端完备性(协议面/五屏/打包链/版本)。
- 实跑基线:
  - pytest(CI 同款 `uv run --no-sync python -m pytest -q`):**1397 passed / 14 skipped,exit 0**(含并行会话未提交的 +2 个新测试)。
  - vitest(desktop/ui-src `npm run test`):**40/40 全绿**。
  - git:HEAD=`45639c3`;**领先 origin/main 2 个提交未推送**(`933c7da`、`45639c3`);**零 tag、零 release**。
- 代码卫生结论:全仓 TODO/FIXME 字面**零**;17 处 pytest skip 全为真实网络/环境门控,无掩盖失败;静默吞错仅 1 处且属控制流用法。未实现项全部以结构化、带版本排期的方式显式声明(见 F 节)。

**并行会话提示**:v111-desktop-paths 修复由另一会话施工中(主修复已提交 `45639c3`,工作区残留其 tests 增补未提交)。本普查不评审其代码对错,其范围(P0 三屏路径)以该任务为准,不重复立项。

## 1. A 组 · 照做即失败(P1,文档/模板错误)

| # | 位置 | 问题 | 证据 |
|---|------|------|------|
| A1 | README.md:132,393;docs/zh/getting-started.md:14;docs/en/getting-started.md:17;docs/launch/linuxdo.md:76 | `pip install -e .` 必失败:根包依赖 `myia-classifier>=0.1,<0.2` 不在 PyPI(404),仅 uv workspace 可解析;docs 后三处连"从源码装"的缓冲说明都没有 | pyproject.toml:23,43-45;pypi.org 404 实测 |
| A2 | myia-classifier/README.md:18-20 | `pip install myia-classifier` 宣称,包从未发布(404),无任何缓冲说明 | pypi 404;发布配置本身存在且一致(pypi-publish.yml、RELEASE.md) |
| A3 | docker/env.example:13-14 | 模板给 `TG_BOT_TOKEN`/`TG_CHAT_ID`,代码硬性解析 `env:TELEGRAM_BOT_TOKEN`/`env:TELEGRAM_CHAT_ID`;照填启用 telegram 通道必 `env_var_missing` 退 1 | src/myia/push/telegram.py:59-61 |
| A4 | docker/env.example:21 | LLM 段给 `OPENAI_API_KEY=`,文档教 `MYIA_LLM_BASE_URL`/`MYIA_LLM_KEY`;文件内注释自认"键名对齐后调整" | docs/zh/getting-started.md:44-46;skill/SKILL.md:182-183 |

## 2. B 组 · 宣称 vs 实际(P1,交付完整性)

| # | 位置 | 问题 | 证据 |
|---|------|------|------|
| B1 | README.md:239-244;SECURITY.md:95-98;.github/workflows/desktop-release.yml:31 | 零 tag、零 release,但路线图 v0.1–v1.0 全勾"✅ 已交付"、v1.0"公开交付";SECURITY 版本表引用不存在的 "latest release/older tags";desktop-release 的 `v*` 触发器从未触发。附带:UPDATER.md §六 自认——updater secrets 未配则 tag 流水线守卫直接红、UI"检查更新"未接线(未装 plugin-updater)、Windows 仅构建级验证、无 Apple 公证 | `git tag -l` 空;desktop/UPDATER.md:104-110 |
| B2 | README.md:244(v1.1 交付宣称) | "卡片内反馈按钮"列为 v1.1 交付;桌面端零反馈入口(CLI feedback mark/list/stats 齐全,桌面无方法无 UI);情报流只有本地已读/星标 | cli.py:425-449;ui-src 全局 grep 零命中 |
| B3 | desktop/ui-src/src/screens/settings/settings-screen.tsx(480 行) | settings 屏缺 PRD 承诺的"反馈开关"分区(骨架 routes/settings.tsx:26-27 列了,真实实现只有 LLM/代理池/推送三表单) | 对照 .trellis/tasks/10-02-v11-desktop-app/prd.md |
| B4 | desktop/ui-src/src/screens/dashboard/dashboard-screen.tsx:229-260 | 仪表盘缺 PRD 承诺的"采集量趋势"(只有品类卡/健康度/近期成功率,grep trend/趋势 零命中) | 同上 PRD |
| B5 | git | 本地领先 origin/main 2 提交未推送(933c7da、45639c3) | `git rev-list origin/main...HEAD` = 0/2 |

## 3. C 组 · 桌面通路协议/UX 缺口(P2)

| # | 位置 | 缺什么 | 备注 |
|---|------|--------|------|
| C1 | feed/api.ts:6-12(自注) | `store.items` 无 before/offset:同刻条目超单页 limit 时翻页游标卡死、追加 0 条即判停 | 协议缺口,代码已注明 |
| C2 | main.rs:109-118;use-sidecar-status.ts:33-54 | run 无取消通道;sidecar 崩溃(Terminated)后不重启,此后所有请求永得 `sidecar_not_running`,顶栏"重新探测"按钮救不回,必须重启 .app | 与 reprobe 按钮承诺直接矛盾 |
| C3 | entry.py:874-885;sqlite.py:1207-1334 | `run.status` 只读内存 `_RUNS`,重启即空;store 有 runs 表但无 list_runs/sidecar 无 runs.list,历史 run 成功率桌面不可达 | |
| C4 | entry.py:104-105,279-281 | 日志环形缓冲纯内存 4000 行,重启即空,"回看历史日志"实际不可用 | 部分刻意(环形缓冲设计) |
| C5 | cli.py:375 vs entry.py `_HANDLERS` | `secret.delete` 桌面无入口,误存凭据无法从 UI 清除 | |
| C6 | client.ts:117-118 | `pluginsList` 封装全仓零调用 = 无插件管理/市场屏(CLI plugin install/remove 齐全) | 或属 v1.2 市场 UI,无文档化计划 |
| C7 | client.ts:111-135;sources/api.ts:8-13 | 后端已有 `sources.write`(entry.py:585)但共享封装缺,client.ts:108 "与 _HANDLERS 一一对应"注释失实;sources/api.ts 头注释仍写"必得 method_not_found"误导 | 文档腐化 + 封装补齐,小改 |
| C8 | top-bar.tsx:25-33 | 全局品类选择器硬编码空壳(只有"全部品类",C 阶段注释仍在,选中不过滤) | |
| C9 | feed-screen.tsx:75-78 | 情报流卡片无"打开原文"外链(item.url 只进 title 提示) | 轻 |
| C10 | entry.py:371-373;sidebar.tsx:52 | 协议无 app/bundle 版本字段(UI 只能拿 Python 包版本顶替);~~侧栏硬编码"v1.1 骨架"文案与 0.1.0 版本体系脱节且"骨架"措辞失实~~(文案半边已由 10-03-ui-hints-trim 清除,2026-10-03;版本字段缺口仍在) | 版本四处目前一致但靠人肉 |
| C11 | settings/api.ts:16-22(自注) | enrich.model / pools 结构 / push 通道声明不可写回(无 yaml.write 泛化方法),界面如实标注不伪造保存 | 协议缺口,代码已注明 |
| C12 | feed-screen.tsx:250(唯一) | runStart 全 UI 仅 feed 空态 CTA 一处;仪表盘/日志/源管理无"跑一次";run_busy 后无取消 | 与 C2 同源 |
| C13 | sources-screen.tsx:59-84 | 源管理只有启停;无"试抓此源"(CLI `myia test --source`)、无增删改(add-source 属 v0.2 stub,刻意延后但查/改无替代说明) | 部分刻意 |

## 4. D/E 组 · 工程门禁与卫生(P2/P3)

| # | 位置 | 问题 | 级 |
|---|------|------|----|
| D1 | .github/workflows/ci.yml | Rust(src-tauri)push/PR 零编译检查,仅 tag 发版才首次编译——PR 弄坏 Rust 代码 CI 依旧全绿 | P2 |
| D2 | ci.yml;pyproject.toml | 无任何 lint/类型门禁(ruff/mypy 步骤与配置均无) | P2 |
| D3 | tests/(无 __init__.py) | 裸 `pytest` 本地跑必假红:`tests/test_baseline.py:789 from tests.conftest import` 依赖 cwd 进 sys.path,只有 `python -m pytest` 可跑;**v111 implement.md 步骤 1 写的恰是失败跑法** `uv run pytest` | P2 |
| D4 | desktop/myia.spec:9 | PyInstaller 过期生成物入库且含机器绝对路径 `~/...`(build-sidecar.sh:99-104 每次重新生成,入库副本纯噪声) | P2 |
| D5 | desktop/src-tauri/tauri.conf.json:28 | externalBin 的 binaries/ 被 gitignore 且未配 beforeDevCommand:fresh clone 不先跑 build-sidecar.sh 则 `tauri dev` 直接挂 | P2 |
| D6 | desktop-release.yml:96-99 | tag 版本只合并进 tauri.release.conf.json,package.json/pyproject 不随 tag 提升——首个 tag 后桌面版本与包版本必然漂移 | P2(设计使然,留意) |
| E1 | CONTRIBUTING.md:89 | "时间类逻辑用 freezegun" 约定 vs 实际:freezegun 非依赖、测试刻意自建 FakeClock(tests 自注 "no freezegun needed") | P3 |
| E2 | README.md:19 vs CONTRIBUTING.md:26 | 引擎计数措辞打架:"fetch engines 6 (L1–L6)" vs "7-rung fetch chain"(实际 7 模块横跨 6 级,两种说法各自成立但互斥) | P3 |
| E3 | docs/demo/README.md:69-70 | "根 README 第 11 行、width=820" 实际 README.md:24、width=800 | P3 |
| E4 | desktop/ui-src/src/routes/*.tsx(5 文件) | C 阶段占位骨架死代码,零引用(App.tsx:16-17 自述留档备查) | P3(可清) |
| E5 | src/myia/engines/fetch_base.py:768 | 过期注释"留待 v02-cli-full 接线",实际 cli.py:1079/1782 已接 | P3 |
| E6 | .trellis/tasks/ | **49 个任务滞留 review 状态从未归档**(v01–v11 全树 0/N done),任务面板失去"什么真没做完"的分辨力——本次普查的动机之一 | P3(流程债,建议批量 finish-work) |
| E7 | 仓库根 | 本地垃圾:myia.db、osint_stderr.log、.coverage(gitignore 已覆盖,未被跟踪,顺手清盘即可) | P3 |

## 5. F 组 · 已知刻意不做(登记防误报,不立项)

- `add-source`/`dashboard` CLI 子命令 stub,**排期 v0.2**,结构化拒绝(cli.py:201-204,2381-2391)
- `residential:` 住宅代理轮换,**排期 v0.3**,结构化报错(fetch_base.py:131-134 等)
- digest 池持久化回填无排期,注释坦承"当前没有任何实现,勿宣称 otherwise"(pipeline.py:31,212)——重启丢池内条目,已知数据丢失窗口
- linux.do / v2ex 源整块停用(登录墙/Cloudflare,待 stealth_browser;wool.yaml:143-191、ai-news.yaml:78-91)
- crawl4ai L3 现为 ~10KB 薄适配层,深度实装属下一波(主人待办已列);proxy_pool 对接与 residential(v0.3)同批
- Windows 交叉构建无 .exe/.msi 产物(desktop-release.yml 未按平台 matrix;UPDATER.md 已文档化)——发布工程下一波
- credentials.yaml / monitor.yaml 不随包(v111 design D5:依赖市场插件/凭据,首跑种子会造成 doctor 大面积 error)
- updater pubkey 占位 / Developer ID 签名 / 公证 / Windows latest.json 手工补(desktop/UPDATER.md 全文档化,待主人配 secrets)
- SECURITY 拒载明文凭据、`InMemoryKeychainBackend` 测试注入、typing.Protocol `...` 体、endpoint=example.com 红线占位——均为刻意设计

## 6. 修复路由建议(执行时另行开任务/回炉,本任务不动代码)

1. **A 组 + B5 → 轻任务 docs-truth**:一次 PR 修正 README/子包 README/docs 四处 pip 宣称、env.example 键名(A3/A4)+ push 两提交。
2. **B2/B3/B4 → 回炉 10-02-v11-desktop-app**(其 PRD 验收未闭环,任务却在 review 状态——顺带暴露 E6 流程债)。
3. **C 组 → 新建 v1.1.2 桌面补全批次**(或并入上述回炉);优先 C2(sidecar 崩溃救不回,最伤用户)、C1(翻页卡死)、C7(顺手)。
4. **D1–D3 → 轻任务 ci-gates**:PR 加 cargo check + ruff(可选 mypy)+ 统一 pytest 跑法(加 tests/__init__.py 或文档钉死 `python -m pytest`)。
5. **D4/E4/E5/E7 → 顺手卫生**(可搭任意上述 PR 顺风车)。
6. **E6 → 专项归档会话**:批量核对 49 个 review 任务,真做完的归档、没做完的按本矩阵回炉。
7. **B1(发布物)→ 已由 grill Round 1 定向**(10-03-grill-v112):tag v1.1.1、四处版本 0.1.0→1.1.1、v10-release 重开为 10-03-v111-release 执行;README 路线图措辞随发布任务一并改口。

## 7. 验收标准(本普查任务)

- [x] 三路探查完成,每条缺陷带 file:line 证据(本文件即证据矩阵)
- [x] 实跑基线记录(pytest 1397/14skip、vitest 40/40、git 状态)
- [x] 刻意不做清单成文,防止后续误报
- [x] 主人过目分组与路由,拍板后按路由开修复任务(2026-10-03 grill Round 3 拍板,见文末注记;活清单收口后归档)
  - 2026-10-03 主人批准路由,已建 3 个修复任务:`10-03-docs-truth`、`10-03-v112-desktop-parity`、`10-03-ci-gates`(活清单收口,本档归档)
- 本任务全程不改产品代码、不提交(并行会话在途,避免缠绕)

## 8. 核实通过面(不列条目的部分)

以下经代理逐项核对**无漂移**,不在上表重复:README 全部命令/路径/徽章/相对链接("tests 1300+ passing" 实测成立)、docs zh+en 四页与 write-a-plugin(测试锁定)、skill/SKILL.md 与 cli.py 逐项一致、docker/README vs compose/Dockerfile、SECURITY 凭据契约、desktop/UPDATER.md 与发版流水线互洽、RELEASE.md runbook 与 pypi-publish.yml 逐字一致、六处版本号 0.1.0 一致、tauri resources 引用路径全部存在、capabilities 权限最小自洽、UI 无 skip/xfail、Rust 侧无 todo!/unimplemented!。

> **2026-10-03 拍板注记(grill Round 3,主人全按推荐;决议全文=10-03-grill-v112)**:
> 第 6 节路由**全部批准**,排序修正:立即=推提交(B5)+ docs 小修
> (`10-03-docs-truth`:A3/A4/A1-docs 部分+task.py 防护搭车);v111-release 并行
> (A1/A2/B1 的 README 部分);tag 后=`10-03-ci-gates`(D1/D2)、
> `10-03-v112-desktop-batch`(C 组,C2 最优先)、`10-03-archive-review`(E6)。
> **路由②被覆盖**:B2/B3/B4 改为「v1.1.1 改宣称(README 如实化,随 v111-release),
> 补实现排 v1.2(10-03-v12-backlog)」,不回炉 10-02-v11-desktop-app。
> 本档转**活清单**,状态推进标记:
> - D3 ✅ 已修(718d56c,from conftest import;两跑法 1397 绿)
> - B1 ✅ 已定向(tag/版本/updater/UI 接线全量入 10-03-v111-release;R2 批做全)
> - C10 ✅ 主体已修(并行会话 02a0dce 剥开发期文案;版本一致性核对随 v111-release)
> - B5 ✅ 获批即推(grill Q5)
> - 2026-10-03 路由落档:已建 3 个修复任务——`10-03-docs-truth`(路由①:A 组 docs 侧 + A3/A4 + Q8/E7 搭车)、
>   `10-03-v112-desktop-parity`(路由③:C 组全量 + B2/B3/B4 补实现;吸收 stub 10-03-v112-desktop-batch
>   [task.json 标注 superseded] 与 10-03-v12-backlog 第 4 项;v111-release 的 B2/B3/B4 改口指向已回标至该档)、
>   `10-03-ci-gates`(路由④:D1/D2 核心,D4/D5 顺风车)。活清单收口,本档归档。
> - 其余条目:A 组 README 半边 / A2 / B1 → 10-03-v111-release;E4/E5 随 v112 批次做或裁;E6 → 归档会话。
> - 2026-10-03 ci-gates 收口回标(终态,证据全文见
>   `.trellis/tasks/archive/2026-10/10-03-ci-gates/prd.md` 收口补记):
>   **D1 ✅** ci.yml rust-check job 落库,CI 全近期 run 绿;/tmp 干净克隆
>   占位→exit 0、注入语法错→101、撤错→0 三态实证;
>   **D2 ✅** ci.yml ruff job + 根 ruff.toml 最小集 E9/F63/F7/F82 锁
>   0.16.10,本地全树 0 错(pyproject 方案因当时在途碰撞改独立 ruff.toml,
>   缘由在文件头);
>   **D4 ✅ 演化关闭** myia.spec 未删——845f64a 更名 myia-core.spec 并
>   相对路径化,由噪声变可复跑模板;
>   **D5 ✅** UPDATER.md:20-23 文档提示路线,未加 beforeDevCommand。
