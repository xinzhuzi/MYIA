# tag 驱动发布:推 tag 才打包(桌面/Docker/PyPI 统一走 GitHub Release)

## Goal

主人指令(2026-10-03 原话):"你的打包逻辑好像是错误的……以我让你设置 tag 方式去打包,而不是现在很多个版本,要在 GitHub 上面利用相关的功能进行打包,这样才表示发布"。

**目标模型:发布的唯一语义 = 主人推 `v*` tag;一个 tag = 一次完整发布;main 推送零发布物。**

## 背景:现状取证矩阵(2026-10-03 实测)

| 通道 | 工作流 | 现状触发 | 实际行为(证据) | 判定 |
|---|---|---|---|---|
| 桌面 | `desktop-release.yml` | push tag `v*` + dispatch | v1.1.1 一次成功,Release 资产齐全(dmg / app.tar.gz+sig / latest.json) | ✓ 已符合,不动 |
| Docker | `docker-publish.yml` | **push main + push tag `v*`** + dispatch | main 每推必发 GHCR:当日 15+ 次成功 run;tag 策略 raw-latest(is_default_branch)+ ref(tag)+ semver + sha,每次运行堆一个 sha 版本 | ✗ **"很多个版本"直接来源** |
| PyPI | `pypi-publish.yml` | 仅 dispatch(从未运行) | PyPI/TestPyPI 上 `shishi` / `shishi-classifier` 均不存在(404 实测);手动按钮不构成"发布"语义 | ✗ 不符合 tag=发布 |
| CI | `ci.yml` | push main + PR | 纯测试/编译门禁,不产发布物 | ✓ 不动 |

版本事实源现状(决议 9 归零后全对齐 `0.0.1`,2026-10-03):根 `pyproject.toml`(含依赖窗)、`myia-classifier/pyproject.toml`、`src/myia/__init__.py`、`desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/Cargo.toml`(+lock)。tag 与 GitHub Release 均已清空(原 v1.1.1 已删)——版本堆积发生在 GHCR 包页,不在 Release 页。下一个真实 tag = **v0.0.1**(原 v1.1.2 预期随版本归零作废)。

## 决议(含深化轮修订记录)

1. **触发收敛**:所有发布类工作流只由 `v*` tag 触发;main 分支推送只跑 CI。
2. **Docker 收敛**:`docker-publish.yml` 改为 **纯 tag 触发**。
   - ⚠️ 修订(深化轮):初版保留 `workflow_dispatch` 演练,现**改判移除**——dispatch 若推镜像=再造版本堆积(违背主人指令本意),若不推=半功能;失败恢复用 Actions 界面对既有 tag run 的 **Re-run**(原生能力,无需 dispatch)。
3. **GHCR tag 收敛**:tag 发布只打 `X.Y.Z` + `latest`。
   - ⚠️ 修订(深化轮):`latest` 改用 metadata-action **官方 `flavor: latest=auto` 默认语义**(初版自拼 `enable=${{ startsWith(github.ref,'refs/tags/v') }}` 表达式弃用)——官方文档明示 auto 模式对 `type=semver` 自动产 latest,且 **semver 感知预发布**:`v1.2.0-rc.1` 只产 `1.2.0-rc.1`,不移动 `latest`(README type=semver 表实证)。
   - 移除 `type=sha`(每 commit 堆一版机制)与 `type=ref,event=tag`(`v` 前缀重复)与 `type=raw,value=latest,enable={{is_default_branch}}`(依赖 main 推送,改后永不生效)。
4. **PyPI 接入 tag**:`pypi-publish.yml` 增加 tag 触发;tag 事件 = 正式 PyPI + 双包同发 + OIDC;TestPyPI 演练保留手动 dispatch(默认 test-pypi)。
   - ⚠️ 修订(深化轮):初版只处理了 `repository` 空输入,**漏了 `package` 空输入**——push 事件下 `inputs.*` 全为空串,现有 build 步骤 case 会以「未知 package 输入」直接红、verify 脚本 projects 查表得 None 退出。tag 事件须解析为 `both`(双包,`shishi` 依赖 `shishi-classifier`)。
5. **版本一致性守卫**:tag 与两个 pyproject version 不符即红,先于一切上传;桌面侧(tauri.conf.json / Cargo.toml)做 **软校验**(仅 `::warning`,不拦——桌面打包版本本就从 tag 注入,无漂移面,警告只为维护四件套纪律可见)。
6. **发布中心 = GitHub Release 页**:PyPI 产物(wheel/sdist)附到同 tag 的 Release 页。
7. **文档同步**:RELEASE.md 改写为 tag runbook;CHANGELOG 落笔;jike-draft 核对(初判不改:其 `ghcr.io/xinzhuzi/shishi` 裸引用=latest,改后 latest 语义=最新发布版,对读者反而更准)。
8. **不自动清理**:GHCR 已堆积的历史版本不自动删(破坏性,主人门禁),runbook 给手工清理入口(含 untagged manifest 说明)。
9. **版本序列归零(2026-10-03 主人令,grill 后追加)**:版本从 **0.0.1** 重新开始——`v1.1.1` tag 与 GitHub Release 已删(远端+本地,实测 ls-remote 零 tag、Release 列表空);五处版本源同日落零(根 pyproject+依赖窗 `>=0.0.1,<0.1`、classifier pyproject、`myia.__version__`、tauri.conf.json、Cargo.toml+lock)。**首个 E2E 由 v1.1.2 改为 v0.0.1**,上文 v1.1.2/v1.1.2-E2E 相关表述(含 grill Q4「保 1.1.1」)以此为准作废;GHCR 清理随之改口径=全清(1.1.1/v1.1.1/sha-* 一并删,主人手工);jike-draft 等 launch 文案的 1.1.x 口径待 0.0.1 发布前重写。桌面副作用注记:已装 1.1.1 的机器 updater 会 404 且不会自动降到 0.0.1,需重装 0.0.1 dmg。

## Grill 决议(2026-10-03,八问全按推荐;决议已并入上文各节)

| # | 问题 | 决议 |
|---|---|---|
| Q1 | docker 移除 `workflow_dispatch` 改判 | **批准**(失败恢复=Actions 原生 Re-run) |
| Q2 | 预发布 tag(`v1.2.0-rc.1`)全链行为 | **全量三通道**:PyPI 出 prerelease 版本(pip 默认不装,无害)、GHCR 出预发布 tag 不动 `latest`、桌面照常;零特判可预测,开始用 rc 时再收紧 |
| Q3 | `latest` 去留与版本 tag 形态 | **保留 `latest`**(=最新正式发布)+ 版本 tag `1.1.2` 无前缀形态;jike 文案不用改 |
| Q4 | GHCR 存量清理时机 | **首个 tag(v1.1.2)E2E 全绿后**,主人手工清一次:保 `1.1.1` 与 `latest` 相关 manifest,删其余 `sha-*`;只进 runbook 不写代码 |
| Q5 | 版本锁步范围 | **双 pyproject 硬拦 + 桌面两件软警告(现案)**;classifier 独立发版属远期,届时改守卫+引入新 tag 形态 |
| Q6 | `environment: pypi` 加 required reviewers | **不加**:tag=主人门禁已足;反悔随时 Settings 可加,不现在锁死 |
| Q7 | wheels 附 Release + checksums | **附 Release 批准**;SHA256SUMS 不做(远期锦上添花) |
| Q8 | PR 期镜像构建检查 | **另立任务** `10-03-docker-build-check`(已当场建档);不入本任务 |

Frontier 复核:八问彼此独立、答复未引出新分叉,grill 收口。

## Requirements(事件×行为全景)

| 事件 | ci.yml | desktop-release | docker-publish | pypi-publish |
|---|---|---|---|---|
| push main / PR | 测试+编译门禁 | — | **—(改后)** | — |
| push tag `v*` | —(tag 不跑 CI) | dmg/msi+updater → Release(不变) | `X.Y.Z`+`latest` → GHCR | 双包 → PyPI(OIDC)+ 守卫 + wheels 附 Release |
| 手动 dispatch | — | 演练(既有) | **—(已移除)** | TestPyPI 演练(默认)/ 显式 pypi 兜底 |

一个 `vX.Y.Z` 应产出(预期产物清单,E2E 核对基准):

| 通道 | 产物 |
|---|---|
| GitHub Release | dmg、`shishi.app.tar.gz`(+sig)、`latest.json`(桌面,已有)+ `shishi-X.Y.Z-*.whl`、`shishi-X.Y.Z.tar.gz`、`shishi_classifier-X.Y.Z-*.whl`、`shishi_classifier-X.Y.Z.tar.gz`(附挂,新增) |
| GHCR | 镜像 tag `X.Y.Z`、`latest` 两个(此前还产 `vX.Y.Z`、`sha-xxxx`) |
| PyPI | `shishi` 与 `shishi-classifier` 各一个 `X.Y.Z` |

## Acceptance Criteria

- [ ] **AC1** `docker-publish.yml` 触发改为仅 push tag `v*`,**无 dispatch**;push main 不再发镜像。
- [ ] **AC2** GHCR tag 策略:`type=semver,pattern={{version}}` + 官方 `flavor: latest=auto`;移除 sha / ref-tag / raw-latest 三条;tag 发布只产 `X.Y.Z`+`latest` 两镜像 tag;**预发布 tag(rc/beta)不移动 `latest`**。
- [ ] **AC3** `pypi-publish.yml` 增加 push tag `v*` 触发,tag 事件三处空输入全解析正确:`repository`→`pypi`、`package`→`both`、`use-api-token`→false(OIDC);dispatch 演练路径原样(默认 test-pypi)。
- [ ] **AC4** 版本守卫(tag 事件专属,先于 build):tag 格式校验(`v` + 语义化版本)+ `tag#v` == 根 pyproject version == myia-classifier pyproject version,不符中文 `::error` 列四处事实源当前值;tauri.conf.json / Cargo.toml 不符仅 `::warning`。
- [ ] **AC5** 新增 `attach-release` job(仅 tag 事件、`needs: publish`、job 级 `contents: write`):PyPI 产物附到 `github.ref_name` 的 Release(已存在则追加,与桌面资产同页)。
- [ ] **AC6** `docs/launch/RELEASE.md` 改写为 tag 驱动 runbook(逐节改写映射见 implement.md Step 4):版本四件套同 bump → CHANGELOG → commit → 推 tag → 三通道预期产物核对 → GHCR 版本管理节(存量清理入口+untagged manifest 说明);`CHANGELOG.md` `[Unreleased]` 落本次变更;`jike-draft.md` 核对并记结论。
- [ ] **AC7** runbook 含 GHCR 历史版本手工清理入口(Packages → shishi → versions),注明属主人操作;本任务不执行任何删除。清理时点=首个 tag E2E 全绿后**全清**(决议 9 修订 grill Q4:`1.1.1`/`v1.1.1`/`sha-*` 一并删)。
- [ ] **AC8** 结构验证:改动 yml 过 actionlint(获取方式见 implement.md Step 3,本机未装有 brew 可装)+ YAML 解析兜底 + `git diff --stat` 范围核对(只含预期文件);真 E2E(推下一个真实 tag,预期 v1.1.2)留主人,runbook 写明核对清单。

## 不做的事

- 不动 `desktop-release.yml` 与 `ci.yml`(前者已符合 tag 模型且 v1.1.1 实证,后者是测试门禁非发布)。
- 不给 docker-publish 保留任何手动推镜像通道(决议 2 修订+grill Q1 批;PR 期镜像构建检查已批另立任务 `10-03-docker-build-check`,grill Q8)。
- 不生成 SHA256SUMS / checksums 附 Release(grill Q7:远期锦上添花,不扩本任务面)。
- 不做 npm / brew / snap 等其它发布通道。
- 不自动删除 GHCR 历史版本(含 untagged manifest)、不回收已发布的 PyPI 版本与 GitHub Release(不可逆,主人门禁)。
- 不 bump 版本号本身(下一个版本的 bump 属主人的下一次发布动作,预期 v1.1.2)。
- 不合并三工作流为单一 release 工作流(备选已评估并否决,理由见 design.md §6)。

## 已核实事实附录(取证路径与出处)

| 事实 | 出处 |
|---|---|
| GHCR 版本堆积、main 每推必发 | `gh run list --workflow=docker-publish.yml`(2026-10-03 当日 15+ 次 push main 全 success) |
| PyPI 双包不存在 | pypi.org / test.pypi.org JSON API 双查 404 |
| v1.1.1 Release 资产 4 件 | `gh release view v1.1.1 --json assets` |
| 四件套版本对齐 1.1.1 | 四文件 `version` 字段逐一核读 |
| metadata-action `latest=auto` 默认、对 semver 自动产 latest、**预发布不移动 latest** | docker/metadata-action 官方 README(raw.githubusercontent.com;zread 未索引该仓库,按搜索 SOP 降级 raw 通道) |
| 本仓库并发 softprops 挂同一 Release 已有先例 | desktop-release.yml macos/windows 两 job 同 tag 并行调用,v1.1.1 实证无冲突 |
| push 事件 `inputs.*` 为空串 | GitHub Actions inputs 语义(仅 workflow_dispatch/workflow_call 填充);与现工作流 case 语句逐行比对确认会红 |
| actionlint 本机未装、brew 在 | `command -v` 实测 |
| 下一个 tag 预期 v1.1.2 | `docs/launch/jike-draft.md` 口径已按 1.1.2 定稿 |

## Notes

- "tag/PyPI=主人门禁"(既有纪律)在本模型下强化:无 tag 不出任何发布物,tag 由主人推;`environment: pypi` **不加 required reviewers**(grill Q6:tag 即门禁,双确认冗余,反悔随时可加)。
- 三工作流对同一 tag 独立触发、互不阻塞,与现状一致;单通道失败不自动回滚其它通道,runbook 提供逐通道核对清单。
- 并行会话在场:实现期只 stage 本任务碰过的文件,不卷入他人在途改动(脏 73 文件基线)。
