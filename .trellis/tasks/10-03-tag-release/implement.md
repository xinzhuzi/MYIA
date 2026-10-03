# 执行计划:tag 驱动发布(深化版)

前置门槛:主人过目三件套 → `task.py start`(planning → in_progress)后方可动手。
grill 记录:2026-10-03 八问全按推荐回写毕(决议表见 prd.md「Grill 决议」节;Q8 另立任务 10-03-docker-build-check)。
基线:main 脏 73 文件(并行会话在途)——**每次提交只 stage 本清单碰过的文件**,不 stash 不卷入他人改动。
改动面(全量):`docker-publish.yml`、`pypi-publish.yml`、`docs/launch/RELEASE.md`、`CHANGELOG.md`、(核对不改:`docs/launch/jike-draft.md`)+ 本任务档。desktop-release.yml / ci.yml 零改动。

## 顺序清单

### Step 1:docker-publish.yml(触发 + tag 策略)

- [x] `on:` 块改为仅 `push: tags: ["v*"]`;**删除** `workflow_dispatch:` 与 `branches: [main]`。
- [x] metadata-action step:`tags` 四条 → 一条 `type=semver,pattern={{version}}`;新增 `flavor: latest=auto`(显式声明+注释"默认即 auto;预发布 tag 不移动 latest")。
- [x] 文件头注释(现 1-3 行)改写:仅 tag 发布、main 推送不产镜像、预发布 tag 语义。
- [x] 自查:全文 rg 确认无 `is_default_branch` / `type=sha` / `event=tag` / `workflow_dispatch` 残留。

### Step 2:pypi-publish.yml(tag 触发 + 空输入解析 + 守卫 + 附挂)

- [x] `on:` 增加 `push: tags: ["v*"]`(dispatch 块原样)。
- [x] build 步骤 case:增加 `""` 分支,行为= `both`(双包;注释:空串=tag 事件)。
- [x] Verify 脚本 `projects` 字典:增加 `"": ["myia_classifier", "myia"]`。
- [x] Resolve 步骤 case:增加 `""` → `pypi`(注释:tag=正式,dispatch 才可选演练)。
- [x] 新增「守卫:tag 与包版本一致性」step(setup-python 之后、Build 之前):
  - `if: startsWith(github.ref, 'refs/tags/v')`;TAG 经 env(`GITHUB_REF_NAME`)取,不内插。
  - 硬拦:tag 格式 `^v\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$`;tomllib 读两个 pyproject `version` != `TAG#v` 任一 → `::error` 中文列三值+事实源路径,exit 1。
  - 软警:tauri.conf.json / Cargo.toml 不符 → 仅 `::warning`(桌面版本从 tag 注入,无漂移面)。
- [x] 新增 `attach-release` job:`needs: publish`、`if: startsWith(github.ref, 'refs/tags/v')`、`permissions: contents: write`;download-artifact@v4(name: dist)→ softprops/action-gh-release@v2(`generate_release_notes: true`,`files: dist/*.whl + dist/*.tar.gz`)。
- [x] `repository` 输入 description 补「tag 触发固定 pypi,此选项仅 dispatch 生效」;文件头「触发方式」段改写(tag=正径 / dispatch=演练与兜底)。

### Step 3:结构与静态验证

- [x] actionlint 获取(本机未装,二选一):
      a. `brew install actionlint`(装持久工具);b. 官方脚本装到 `/tmp` 不动系统:`bash <(curl -s https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) /tmp` 后 `/tmp/actionlint-*/actionlint`。
- [x] `actionlint .github/workflows/docker-publish.yml .github/workflows/pypi-publish.yml`(0 error;`shellcheck` 相关告警如有,只修自己引入的行)。
- [x] YAML 解析兜底:`python3 -c "import yaml; [yaml.safe_load(open(f)) for f in [...]]"`(venv 内 PyYAML 在)。
- [x] `git diff --stat` 范围核对:仅预期文件;desktop-release.yml / ci.yml 不在列。
- [x] 触发矩阵人工复核表:PRD「事件×行为全景」逐格对钩。

### Step 4:文档同步

RELEASE.md 逐节改写映射(现有 211 行 → tag runbook):

| 现节 | 处置 |
|---|---|
| 头部红线声明 + 「发什么」段 | 改写:发布动作=推 tag(仍主人亲手;无 tag 零发布物) |
| 第一步 推送代码到 main | 改为「第一步:版本 bump」——五处版本源同 bump(根 pyproject+依赖窗 / classifier pyproject / `myia.__version__` / tauri.conf.json / Cargo.toml+lock,决议 9)+ CHANGELOG + commit 合入 main |
| 第二步 GitHub 仓库设置 | 基本保留(environment `pypi`);required reviewers 决议**不加**(grill Q6:tag 即门禁),保留原文「可选加固」措辞并注一句当前决议 |
| 第三步 PyPI 侧准备(双路径) | 保留(四元组不变;workflow filename 仍 pypi-publish.yml) |
| 第四步 dispatch 发布 | **重写为「推 tag 发布」**:`git tag vX.Y.Z && git push origin vX.Y.Z` → 三工作流自动;预期产物矩阵(PRD 表);失败 Re-run |
| 第五步 验证发布结果 | 扩为三通道核对:Release 资产 8 件清单、GHCR `X.Y.Z`+`latest`、PyPI 双包页;干净环境安装验证命令保留 |
| TestPyPI 演练节 | 保留(dispatch 路径不变);补一句:演练不推 tag、不受守卫拦截、与 tag 正径互不影响 |
| 故障排查表 | 增四行:守卫红=版本没 bump 齐;attach 后 Release 缺桌面资产=桌面 job 挂;GHCR latest 未移动=预发布 tag 预期行为;**旧版桌面端「检查更新」404=v1.1.1 Release 已随版本归零删除(决议 9),0.0.1 发布即恢复,且 updater 不自动降级——1.1.1 机器重装 0.0.1 dmg** |
| (新增)GHCR 版本管理节 | 存量清理入口(Packages→shishi→versions,主人操作)+ **清理时点:首个 tag E2E 全绿后全清**(`1.1.1`/`v1.1.1`/`sha-*` 一个不留,决议 9 修订 grill Q4)+ untagged manifest 说明、TestPyPI 演练残留注记 |

CHANGELOG.md `[Unreleased]` 落条目(草稿,随实现微调):

```markdown
### Changed

- **Tag-driven releases** (10-03-tag-release): publishing is now triggered
  exclusively by pushing a `v*` tag. The Docker workflow no longer publishes on
  every push to main — GHCR receives only `X.Y.Z` + `latest` per release (and
  pre-release tags do not move `latest`); PyPI publishing rides the same tag
  with a version-consistency guard (tag must match both `pyproject.toml`
  versions) and wheels/sdists are attached to the GitHub Release. TestPyPI
  rehearsal remains available via manual dispatch.
```

- [x] jike-draft.md 核对:裸引用 `ghcr.io/xinzhuzi/shishi`(=latest)改后语义=最新发布版,对读者更准 → **不改**,结论记于本清单。

### Step 5:提交与收口

- [x] `git add` 仅本任务文件(两 yml、RELEASE.md、CHANGELOG.md、本任务档)。
- [x] 纯 CI 配置+文档类提交:GitNexus detect-changes 豁免(AGENTS.md 既定);提交信息:`ci(release): 发布改 tag 驱动——Docker 收 tag-only+semver/latest 收敛 / PyPI 接 tag 触发+空输入解析+版本守卫+wheels 附 Release / RELEASE.md 改写 tag runbook(10-03-tag-release)`。
- [x] trellis-check(2.2)全量一轮;3.3 spec 更新评估(`.github/workflows` 无对应 spec 文件 → 记 no-op 理由)。
- [x] 报主人 E2E 清单(v0.0.1 实例,决议 9 归零后):推 `v0.0.1` 后逐项核对——
  1. Actions 三 run 全绿(Desktop Release / Docker Publish / PyPI Publish);
  2. Release `v0.0.1` 资产 8 件:`shishi_0.0.1_aarch64.dmg`、`shishi.app.tar.gz`(+`.sig`)、`latest.json`、`shishi-0.0.1-*.whl/.tar.gz`、`shishi_classifier-0.0.1-*.whl/.tar.gz`;
  3. GHCR 镜像 tag 恰两个:`0.0.1`、`latest`(无 `v0.0.1`、无 `sha-*`);
  4. pypi.org 双包页版本 0.0.1;`pip index versions shishi` 出 0.0.1;
  5. `docker pull ghcr.io/xinzhuzi/shishi:0.0.1` 可拉。
  另:E2E 全绿后主人做 GHCR 存量**全清**(决议 9 修订 grill Q4:`1.1.1`/`v1.1.1`/`sha-*` 全删,一个不留;Packages → shishi → versions);PyPI pending publisher 注册是 E2E 前置,实现收口时一并提醒;jike-draft 等 launch 文案 1.1.x 口径发布前重写。

## 验证命令速查

```bash
# actionlint(方式 b 示例)
bash <(curl -s https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) /tmp
/tmp/actionlint-*/actionlint .github/workflows/docker-publish.yml .github/workflows/pypi-publish.yml
# YAML 解析兜底
uv run python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/docker-publish.yml','.github/workflows/pypi-publish.yml']]; print('yaml ok')"
# 范围核对
git diff --stat
rg -n 'is_default_branch|type=sha|event=tag|workflow_dispatch' .github/workflows/docker-publish.yml   # 期望 0 命中(docker 文件)
```

## 回滚点

- Step 1 / Step 2 / Step 4 相互独立,任一步翻车 `git checkout -- <file>` 单文件回退。
- 合入后发现语义错误:revert 单 commit(注意 revert docker 的 main 触发会恢复每推发版堆积,主人知情)。
- 真发布出问题:PyPI 不可撤;bump 新版本重发;GHCR / Release 资产按 runbook 手工处理,代码不承担。

## 复核门(review gates)

- 主人过目三件套 → start(Phase 1.4)。
- 实现完 trellis-check → 主人推真实 tag(v1.1.2)做 E2E → 按上方清单核对 → finish。

## 完成注记(2026-10-03 收口)

- trellis-check:由 dwfrun-cc671057 独立质检员承担(两轮,AC 全核对+diff 范围复查),结构门禁由脚本实跑(actionlint 3 文件 0 error、rg 违禁残留 0)。
- spec 更新评估:no-op——`.github/workflows/` 无对应 spec 文件,search-sop/engineering-discipline 等既有 spec 不受本次改动影响。
- CHANGELOG.md 带他线在途 hunks(质检实证单 hunk 混 v112-parity/feed-ux 等条目),按纪律不入本任务提交笔,条目随在途文件走;条目本身已落盘(质检核对过)。
- 工作流工程教训:质检 pass 判定未豁免 owner=none 的提交期处置类发现,整流停在提交步——后续写质检指令应注明此类「记录不阻断」,或脚本仅对 owner∈{workflows,docs,ci} 阻断(已记 journal)。
