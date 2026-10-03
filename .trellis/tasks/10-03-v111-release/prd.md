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
   安装节指向 GitHub Releases dmg 下载 + **右键打开绕 Gatekeeper 指引**
   (R2-2 定案:不做 Apple 公证);五屏真实数据截图(demo 插件跑出);
   **B2/B3/B4 宣称如实化**(grill Q6):v1.1 交付列表中桌面未实现的三项
   (卡片反馈按钮/settings 反馈开关/采集量趋势)改为如实口径,补实现已排
   v1.2(10-03-v12-backlog)。
   > **回标注记(2026-10-03 路由落档)**:B2/B3/B4 补实现已从 v1.2 提前为
   > **v1.1.2 桌面对齐批次**(`10-03-v112-desktop-parity`,吸收 10-03-v12-backlog
   > 第 4 项)。README 改口文案请写「补实现已排 v1.1.2 桌面对齐批次」或去版本号化
   > 「排下一批次」,勿照「排 v1.2」旧口径写——否则 v1.1.1 发布时即成过期事实。
5. **tag → Release**:推 `v1.1.1` 触发 desktop-release.yml(dmg + latest.json 附
   GitHub Release)。**前置=R2-1 已批做全**:主人生成 updater 三密钥并配
   三个 Secrets;**AI 侧工项=UI「检查更新」接线**(@tauri-apps/plugin-updater
   前端,普查 B1 断链点),与密钥一起构成完整更新通道。
6. **PyPI 双包**(R2-3 定案:先 test.pypi.org 演练再正式):
   myia + myia-classifier,手动 dispatch pypi-publish.yml,runbook=
   docs/launch/RELEASE.md。
7. **密钥扫描零命中**(v10-release 移交):tag 前全仓扫描(gitleaks 或等价),
   报告与命令入任务日志。
8. **发帖素材**(v10-release 移交):docs/launch/ 四帖按 demo 真实数据更新截图;
   AI 出素材、主人定稿(R2-4),发帖链接回填 `10-01-v10-release` 验收。

## Constraints

- 主人侧依赖只剩一个:**R2-1 三密钥生成+Secrets 配置**(tag 的硬前置);
  其余 R2 决策已定案(不公证/先演练/发帖节奏),AI 侧工项全部可先行。
- 不做(排 v1.2):Windows 产物化、crawl4ai L3、proxy_pool(见 10-03-v12-backlog);
  B2/B3/B4 补实现原排 v1.2,**已于 2026-10-03 提前移交 v1.1.2 桌面对齐批次
  (10-03-v112-desktop-parity)**,本档仍只做 README 宣称如实化。
- 复杂任务:start 前补 design.md(demo 源选型/updater 接线/流水线注入顺序)
  + implement.md。

## 普查交叉引用(10-03-gap-census,发布线内顺带修)

- **B5**:本地领先 origin/main 的提交批后即推(grill Q5)。
- **A1/A2**:README 的 `pip install -e .` / `pip install myia-classifier`
  宣称随 README 升格一并修正(PyPI 发布本身即兑现 A2);docs 侧三处由
  `10-03-docs-truth` 修(防双头改)。
- **B1 附带**:updater UI「检查更新」接线入本档 scope(R2-1 批做全)。
- **C10**:主体已由并行会话 02a0dce 修复(剥侧栏「v1.1 骨架」等开发期文案);
  版本展示与四处版本号对齐的一致性核对仍随本档第 2 项走。

## Acceptance Criteria

- [ ] 四处版本号 = 1.1.1;`myia --version` 与 .app/CFBundle 版本一致
- [ ] myia-demo.yaml 随包:重装机首跑「运行第一个插件」出真数据(冒烟记录入任务)
- [ ] CHANGELOG.md 就位,覆盖 v1.0→1.1.1(含数据通路修复条目)
- [ ] README 徽章/状态段升格;五屏真实数据截图入 docs;Gatekeeper 右键指引;
      B2/B3/B4 交付宣称如实化
- [ ] updater 通道闭环:UI「检查更新」接线 + 密钥签名校验通过(主人三密钥就位后)
- [ ] 密钥扫描零命中(命令+报告入日志)
- [ ] `v1.1.1` tag 推送,desktop-release.yml 绿,GitHub Release 带 dmg+latest.json
- [ ] PyPI 双包先 test.pypi 演练再正式,链接入日志
- [ ] 四帖素材(含截图)交主人定稿;发帖后链接回填 v10-release
