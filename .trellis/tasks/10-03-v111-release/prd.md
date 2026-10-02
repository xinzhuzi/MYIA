# v1.1.1 发布执行:版本对齐 + demo 插件 + tag/CHANGELOG/PyPI/README

## Goal

把 v1.1.1 桌面数据通路修复(45639c3)的价值兑现为公开发布:版本号对齐 1.1.1、
随包 demo 插件(开箱即有数据)、CHANGELOG、推 `v1.1.1` tag 触发 desktop-release
流水线产出 GitHub Release、PyPI 双包、README 升格出见人状态。

来源:grill 决议 2026-10-03(父档 `10-03-grill-v112`,Q1/Q2/Q3;v10-release
未勾验收项移交本档执行)。

## Requirements

1. **demo 插件**(grill Q3,发布前置):`plugins/myia-demo.yaml` —— 真实免费稳定源
   + `push: channel: stdout`,零凭据零 env 引用(fail-fast 铁律不破),builtin
   分类关闭直通(fixture 惯例);tauri.conf resources 与首跑种子自然随包。
   验收口径:装机首跑点「运行第一个插件」出真数据,不撞 config_error。
2. **版本号对齐 1.1.1**(grill Q2):pyproject.toml、myia-classifier/pyproject.toml、
   desktop/src-tauri/Cargo.toml(+Cargo.lock 随之)、desktop/src-tauri/tauri.conf.json。
   `myia --version` 与 .app 版本一致。
3. **CHANGELOG.md**:从 v1.0 起步覆盖至 1.1.1(keep-a-changelog 风格,英文为主);
   1.1.1 条目必须含桌面数据通路修复(MYIA_HOME 统一/随包插件/首跑种子)。
4. **README 升格**:徽章 alpha → 1.1;「仍是 alpha:糙边犹存」中英两段改写;
   安装节指向 GitHub Releases dmg 下载;五屏真实数据截图(demo 插件跑出)。
   Gatekeeper 指引按 R2-2 批复结果写(公证 or 右键打开指引)。
5. **tag → Release**:推 `v1.1.1` 触发 desktop-release.yml(dmg + latest.json 附
   GitHub Release)。**前置=R2-1 批复的三个 updater Secrets 已配**,缺失时流水线
   守卫会中文报错拦住(设计如此,不绕)。
6. **PyPI 双包**(顺序按 R2-3 批复执行,缺省=先 test.pypi.org 演练再正式):
   myia + myia-classifier,手动 dispatch pypi-publish.yml,runbook=
   docs/launch/RELEASE.md。
7. **密钥扫描零命中**(v10-release 移交):tag 前全仓扫描(gitleaks 或等价),
   报告与命令入任务日志。
8. **发帖素材**(v10-release 移交):docs/launch/ 四帖按 demo 真实数据更新截图;
   AI 出素材、主人定稿(R2-4),发帖链接回填 `10-01-v10-release` 验收。

## Constraints

- 主人侧依赖不阻塞 AI 先行项:demo 插件/版本号/CHANGELOG/README/密钥扫描/
  帖子素材(第 1/2/3/4/7/8 项)先行;tag(5)等 R2-1,PyPI(6)等 R2-3,
  README 公证文案等 R2-2 —— 三问见父档 Round 2 表。
- 不做(排 v1.2):Windows 产物化、crawl4ai L3、proxy_pool(见 10-03-v12-backlog)。
- 复杂任务:start 前补 design.md(源选型/流水线注入顺序)+ implement.md。

## 普查交叉引用(10-03-gap-census,发布线内顺带修)

- **B5**:本地领先 origin/main 2 提交(933c7da/45639c3)未推 —— tag 前先推。
- **A1/A2**:README 与 docs 的 `pip install -e .` / `pip install myia-classifier`
  照做即失败 —— README 升格时一并修正安装宣称(PyPI 发布本身即兑现 A2)。
- **B1 附带**:updater UI「检查更新」未接线 —— 按 R2-1 批复决定是否随本档接
  (批「配密钥」则必须接,否则断链;批「明示无更新」则 README 写明)。
- **C10 附带**:侧栏硬编码「v1.1 骨架」文案 —— 版本对齐时顺手改为读真实版本。

## Acceptance Criteria

- [ ] 四处版本号 = 1.1.1;`myia --version` 与 .app/CFBundle 版本一致
- [ ] myia-demo.yaml 随包:重装机首跑「运行第一个插件」出真数据(冒烟记录入任务)
- [ ] CHANGELOG.md 就位,覆盖 v1.0→1.1.1(含数据通路修复条目)
- [ ] README 徽章/状态段升格;五屏真实数据截图入 docs
- [ ] 密钥扫描零命中(命令+报告入日志)
- [ ] `v1.1.1` tag 推送,desktop-release.yml 绿,GitHub Release 带 dmg+latest.json
- [ ] PyPI 双包按批复顺序完成(演练或正式),链接入日志
- [ ] 四帖素材(含截图)交主人定稿;发帖后链接回填 v10-release
