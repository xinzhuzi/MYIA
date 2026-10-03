# 技术设计:tag 驱动发布(深化版)

## 1. 触发矩阵(改前 → 改后)

| 工作流 | 改前 | 改后 | 变更性质 |
|---|---|---|---|
| `docker-publish.yml` | push(main)+ push tag `v*` + dispatch | **仅 push tag `v*`** | **改** |
| `pypi-publish.yml` | dispatch(默认 test-pypi) | **push tag `v*`**(→正式 pypi)+ dispatch(test-pypi 演练) | **改** |
| `desktop-release.yml` | push tag `v*` + dispatch | 不变 | — |
| `ci.yml` | push main + PR | 不变 | — |

tag 推送后 Actions 页预期出现**三个独立 run**(同一 `github.sha`):Desktop Release(mac 主线 + win 验证)、Docker Publish、PyPI Publish(build → publish → attach-release)。

## 2. docker-publish.yml 改动点(最终形态)

```yaml
on:
  push:
    tags: ["v*"]          # 移除 branches: [main];移除 workflow_dispatch(决议修订)

# metadata step(tags 与 flavor,替换原四条 tags、无 flavor):
#   tags: |
#     type=semver,pattern={{version}}
#   flavor: |
#     latest=auto
```

- 产出:`vX.Y.Z` → 镜像 tag `X.Y.Z` + `latest`;`v1.2.0-rc.1` → 仅 `1.2.0-rc.1`(**不移动 latest**,semver 感知,官方 README type=semver 表实证)。
- 删除项与理由:`type=sha`(每 commit 堆一版的机制,主人指令直接打击对象)、`type=ref,event=tag`(产 `vX.Y.Z` 与 semver 重复)、`type=raw,value=latest,enable={{is_default_branch}}`(依赖 main 推送,触发改后永不生效;latest 改由官方 flavor 承担)。
- `flavor: latest=auto` 实为默认值,显式写出为自文档化(注释注明"默认即 auto,显式声明便于阅读")。
- action 版本不动:metadata-action@v5 / build-push-action@v6 / login@v3 / checkout@v4 / qemu@v3 / buildx@v3 均为现役已验证版本(README 新示例已是 metadata@v6/build-push@v7,但升级 action 大版本不在本任务范围,留待单独任务)。
- 文件头注释改写:补「仅 tag 发布,main 推送不产镜像;预发布 tag 不动 latest」。

## 3. pypi-publish.yml 改动点(最终形态)

### 3.1 事件 × 输入解析全景(push 事件 inputs 全空,这是改动的核心难点)

| 输入 | dispatch(用户选) | push tag(空串)→ 解析为 |
|---|---|---|
| `repository` | test-pypi(默认)/ pypi | **pypi**(tag=正式发布语义) |
| `package` | myia-classifier / myia / both | **both**(双包,`shishi` 依赖 `shishi-classifier`) |
| `use-api-token` | true / false(默认) | **false** → OIDC Trusted Publishing |

三处必改(逐行核对现有代码确认会红):

1. build 步骤 `case "$PACKAGE"`:加 `""` → 与 `both` 同分支(空串=tag 事件=双包)。
2. Verify 脚本 `projects` 字典:加 `"": ["myia_classifier", "myia"]`。
3. Resolve 步骤 `case "$REPOSITORY"`:加 `""` → `pypi`。

`use-api-token` 两处 `if` 无需改:空串在 `if: ${{ inputs.use-api-token }}` 为假(跳过 token 守卫)、`if: ${{ !inputs.use-api-token }}` 为真(走 OIDC),push 事件天然落 OIDC。

### 3.2 版本守卫(新增 step,置于 build 之前)

```bash
# if: startsWith(github.ref, 'refs/tags/v')   ← 仅 tag 事件;dispatch 演练不拦
TAG="${GITHUB_REF_NAME#v}"
# 1) 格式:语义化版本(拦截 vfoo 这类被 v* glob 放进来的非版本 tag)
[[ "$TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || ::error 退出
# 2) 硬校验:tomllib 读根 pyproject 与 myia-classifier/pyproject 的 version
#    两处 != TAG 任一 → ::error 中文报错,列出「tag / 根 / classifier」三者当前值 + 四处事实源路径,exit 1
# 3) 软校验:tauri.conf.json 与 Cargo.toml != TAG → 仅 ::warning(桌面打包从 tag 注入版本,无漂移面)
```

纪律沿袭:TAG 经 env 传入 shell,不内插 `${{ }}`;错误信息中文、指路具体文件。

### 3.3 attach-release job(新增)

```yaml
attach-release:
  needs: publish                    # PyPI 上传成功才附挂
  if: startsWith(github.ref, 'refs/tags/v')
  permissions:
    contents: write                 # 仅此 job 提权;build/publish 保持 contents: read(+id-token)
  steps:
    - uses: actions/download-artifact@v4   # name: dist → dist/
    - uses: softprops/action-gh-release@v2
      with:
        generate_release_notes: true
        files: |
          dist/*.whl
          dist/*.tar.gz
```

选型理由与竞态分析:

- **softprops 而非 gh CLI 等待循环**:本仓库已有**同 tag 双 job 并发调用 softprops 的先例**(desktop-release macos/windows 两 job,v1.1.1 实证无冲突);softprops 对"Release 已存在"按 tag 追加资产,先后顺序双向兼容。
- **时序**:PyPI 全链(build→publish→attach,ubuntu)约 5-8 分钟;桌面 macOS job(Rust+PyInstaller)约 20-40 分钟 → attach 几乎总是先建 Release,桌面后追加;若 `environment: pypi` 配了 required reviewers 把 publish 压到桌面之后,顺序反转同样兼容。真同时创建的窗口极窄且有先例兜底。
- `generate_release_notes: true`:attach 先建时 Release 也有 notes;桌面后到会刷新为全量 notes。桌面全挂的降级场景:Release 存在但缺桌面资产——runbook 故障排查表记档。
- dispatch 演练被 `if` 排除,TestPyPI 产物**不**污染 Release 页。

### 3.4 输入描述与文件头注释

- `repository` 输入 description 补一句:「tag 触发时固定 pypi,本选项仅对手动 dispatch 生效」。
- 文件头「触发方式」段改写:tag=正式正径;dispatch=演练(TestPyPI)与兜底(显式选 pypi,如需重跑历史场景——但 PyPI 禁止同版本覆盖,重发必须 bump)。

## 4. 三通道失败语义

- 独立触发、互不阻塞(与现状一致);失败恢复=Actions 界面 Re-run 对应 run(原生能力,docker 移除 dispatch 后仍可用)。
- 不可逆边界排序:版本守卫(红则零上传)→ PyPI 上传(不可撤)→ Release 附挂(可手工删资产)→ GHCR(可手工删版本)。
- merge 到 main 本身只触发 ci.yml,不触发任何发布 → 工作流改动合入是安全的,首个 tag 才是真验证。

## 5. 版本 bump 纪律(runbook 承载,代码零改动)

主人的发布动作:版本源同 bump(根 pyproject / myia-classifier pyproject / `myia.__version__` / tauri.conf.json / Cargo.toml+lock)→ CHANGELOG → commit → `git tag vX.Y.Z && git push origin vX.Y.Z`。守卫兜住漏 bump(pyproject 硬拦、桌面侧警告)。预期首个 E2E = **v0.0.1**(决议 9:版本序列 2026-10-03 归零,v1.1.1 tag/Release 已删)。

## 6. 备选方案评估记录

| 备选 | 结论 | 理由 |
|---|---|---|
| 合并三工作流为单一 release 工作流(一个 run = 一次发布) | **否决** | desktop-release v1.1.1 已实证绿、不动已验证流水线(最小爆炸半径);三通道独立 Re-run 的恢复粒度更好;形式统一不抵重构风险 |
| docker dispatch 改 build-only(不 push)演练 | 否决 | 多平台 build 无 push 的验证属 ci 范畴;真要 PR 期镜像检查应进 ci.yml 另立任务 |
| attach 用 gh CLI 等待 Release 存在再 `gh release upload` | 否决 | 引入等待循环与超时参数;softprops 双向兼容 + 仓内先例,更简单 |
| PyPI 也去掉 dispatch、纯 tag | 否决 | TestPyPI 演练是既有 runbook 资产(首发前彩排);演练不推 tag,需保留手动通道 |
| rc/预发布 tag 跳过 PyPI 或不触发 | 否决(grill Q2) | 全量=零特判、行为可预测;PyPI prerelease 版本 pip 默认不装,无害 |
| Release 附 SHA256SUMS 校验文件 | 否决(grill Q7) | 远期锦上添花,不为它扩发布流水线面 |
| `environment: pypi` 加 required reviewers | 否决(grill Q6) | tag 已是主人亲手动作,双确认冗余;反悔随时 Settings 可加 |

## 7. 风险与边界

- **GHCR 存量堆积**:本任务只拆增量机制;存量(15+ sha 版本)主人手工清理(AC7)。另:tag-only 模式下重推 `latest` 会把旧 manifest 变 untagged 残留——runbook 版本管理节说明。
- **TestPyPI 演练版本堆积**:演练允许同版本反复传(TestPyPI 宽松),量小可控,runbook 一句注记,不设自动清理。
- **预发布 tag 全链行为(grill Q2 已批全量)**:`v1.2.0-rc.1` 触发三通道全量发布(PyPI 预发布版本、GHCR 预发布镜像 tag 不动 latest、桌面 dmg 照常)——零特判、行为可预测;仓库开始实际用 rc 时再收紧。
- **Trusted Publishing 前置**:正式首发前主人需按 RELEASE.md 注册 pending publisher(双包各一次);environment `pypi` 已存在则不动。
- **并行会话**:实现期只 stage 本任务文件;desktop-release.yml / ci.yml 零改动。

## 8. 回滚

- 工作流改动:revert 提交即恢复。注意恢复 docker 的 main 触发会立刻重新开始每推发版堆积——revert 需主人知情。
- 已发产物不自动回收:PyPI 不可撤;GHCR 版本与 Release 资产按 runbook 手工处理。
