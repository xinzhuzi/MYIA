# grill 决议 2026-10-03:v1.1.1 之后走向发布(Round 1 全采纳)

> 质询会:主人 × AI(grilling 规程,设计树分轮)。本档为 Round 1 六项决议的权威记录,
> Round 2 开放问题在末节;沿用 v0.1 grill 先例(决议入 trellis,不悬对话)。
> 事实底座(2026-10-03 实查):无 git tag、无 CHANGELOG、README 徽章 alpha、
> 四处版本号(pyproject / myia-classifier / Cargo / tauri.conf)全 0.1.0;
> desktop-release.yml 已就绪(v* tag 触发,macOS dmg 主线 + Windows msi 构建级
> 验证,updater 三密钥走 Secrets);pypi-publish.yml 为手动 dispatch;
> 官方四件套 push 全引用 `env:FEISHU_CHAT_ID`(无凭据首跑必 config_error)。

## Round 1 决议(主人 2026-10-03 批复:全部按推荐)

### Q1 下一刀 = 发布工程先行 ✅

- tag `v1.1.1` 起步,把 v1.1.1 桌面数据通路修复(45639c3)的价值兑现为公开发布。
- Windows 构建与引擎深度(crawl4ai L3 / proxy_pool)**排 v1.2**,收进
  `10-03-v12-backlog` 待办池,开工时拆正式任务。
- 理由:发布是当前最大杠杆——流水线/物料/密钥全就绪或半就绪,缺的只是执行与
  主人侧三个决定(Round 2);后两者不阻塞见人。

### Q2 版本号口径 = 对齐叙事 ✅

- 公开版本 `1.1.1`(semver 1.x):四处版本号同步(pyproject.toml、
  myia-classifier/pyproject.toml、desktop/src-tauri/Cargo.toml、tauri.conf.json),
  git tag `v1.1.1`,dmg 文件名随 tauri 版本自动带 1.1.1。
- README 徽章 `status-alpha` → `1.1`/stable 口径;「仍是 alpha:糙边犹存」段
  同步改写(中英两处)。
- 否决备选 `0.2.0`:与「v1.0 已交付」历史叙事矛盾。1.x 是公开即承诺 API 相对
  稳定——主人接受此承诺(后续 breaking 走 2.0)。

### Q3 开箱即有数据 = 随包第五件 demo 插件 ✅

- 新增 `plugins/myia-demo.yaml`:真实免费源 + `push: channel: stdout`,
  **零凭据零飞书**,随包进 Resources/plugins(首跑种子自然带上)。
- 目的:用户点「运行第一个插件」第一次就出真数据,不撞 config_error;
  同时作为 README 五屏截图的数据来源。
- 约束:demo 件本身不含 env:/keychain: 引用(不破 fail-fast 铁律);源选型
  要稳定免登录(实现时定,候选:公开 RSS/HN 等);分类走 builtin 关闭直通
  (同 fixture 惯例)。
- 实现挂在 `10-03-v111-release`(发布前必须落,否则截图与开箱体验都缺)。

### Q4 挂账任务处置 = 重开 v10-release + 薄层任务注记 ✅

- `10-01-v10-release`:状态 review → **in_progress 重开**;未勾验收项
  (密钥扫描零命中 / CONTRIBUTING+首 issue 流程 / 发帖+首周反馈)移交
  `10-03-v111-release` 执行,v10-release 保留为发布线父档(验收在其 PRD 终勾)。
- `10-01-v02-engine-crawl4ai` / `10-01-v02-proxy-transport`:保持关闭,
  PRD 注记「v0.2 范围已毕,L3 实装/池化是 v1.2 议题(见 10-03-v12-backlog)」,
  消除「review=已完成」的口径失真。

### Q5 test_baseline 预存失败 = 授权当场修 ✅

- `tests/test_baseline.py::test_pipeline_run_feeds_trend_context_into_rendered_card`
  报 `ModuleNotFoundError: No module named 'tests'`(干净 main 复现)。
- Python spec CI 红线(pytest 全绿才可并)→ 单独小 commit 即修,不等排期。
- 执行档:`10-03-test-baseline-import`。

### Q6 决议落档 = 本档 + 子任务 ✅

- 决议记录 = 本任务(grill-v112,轻量 PRD-only);执行项当场建子任务:
  `10-03-v111-release`(复杂任务)、`10-03-test-baseline-import`(轻量)、
  `10-03-v12-backlog`(待办池,轻量)。
- 沿用规矩:待办当轮落 trellis,不悬在对话里。

## Round 2 开放问题(发布线主人侧决策,答后补进本档)

| # | 问题 | 推荐 |
|---|------|------|
| R2-1 | updater 签名三密钥:主人本机 `npx tauri signer generate` + 配 3 个 GitHub Secrets(命令按 desktop/UPDATER.md);还是 v1.1.1 明示不带自动更新。**事实补充(普查 B1)**:密钥之外,UI「检查更新」也未接线(@tauri-apps/plugin-updater 前端未接)——要闭环须连 UI 接线一起列入发布线;只配密钥不接 UI 仍无更新通道 | 生成+配置+UI 接线一并入 v111-release(不配则公开用户拿到坏通道,不如明示无自动更新) |
| R2-2 | Apple 公证(notarization):需 Apple Developer($99/年);不公证则用户首次打开被 Gatekeeper 拦(README 写右键打开指引)。普查 B1 佐证:当前无公证 | 不买则 README 写清绕行指引;买则公证入 CI(后续任务) |
| R2-3 | PyPI:两个包(myia / myia-classifier)先 test.pypi.org 演练再正式?**事实补充(普查 A1/A2)**:README/docs 现有 `pip install -e .` 与 `pip install myia-classifier` 照做即失败(后者 404)——发布双包本身就是这两条文档宣称的兑现,README 修正随发布走 | 先演练(pypi-publish.yml 手动 dispatch,runbook=docs/launch/RELEASE.md) |
| R2-4 | 四平台发帖(docs/launch/ 四帖)节奏与定稿人 | tag+PyPI 后发;AI 出素材(含 demo 数据截图),主人定稿 |

> 普查交叉引用:`.trellis/tasks/10-03-gap-census/prd.md`(另一会话 2026-10-03 落档,
> A/B/C/D/E/F 六组)。与本轮决议重叠项已互相引用;普查其余项修复路由**仍待主人
> 另行拍板**,不在本轮决议范围内。

## Acceptance Criteria(本档=记录型任务)

- [x] Round 1 六项决议全文入档(本文件)
- [x] 三个执行子任务已建并挂父子关系
- [x] v10-release 重开 + 两个薄层任务注记完成
- [ ] Round 2 四问主人批复后补记(答后勾)
