# 世事发布 Runbook(tag 驱动,主人专用)

> **红线声明**:发布 = **主人亲手推一个 `v*` tag**,再无别的发布动作。无 tag
> 零发布物——main 推送只跑 CI 门禁(ci.yml),不产镜像、不上 PyPI、不出安装包;
> 发布节奏由主人定。实发布依赖主人的凭据与一次性配置:PyPI 侧 Trusted
> Publishing 注册(第三步)、GitHub 仓库 `pypi` environment(第二步)、updater
> secrets(配置见 [desktop/UPDATER.md](../../desktop/UPDATER.md))。

发什么(一个 `v*` tag = 一次完整发布,三通道同时开动,产物汇总到同 tag 的
GitHub Release 页):

| 通道 | 工作流 | 产物 |
|---|---|---|
| 桌面 | [desktop-release.yml](../../.github/workflows/desktop-release.yml) | dmg + 更新包(`shishi.app.tar.gz`+sig)+ `latest.json` → Release |
| Docker | [docker-publish.yml](../../.github/workflows/docker-publish.yml) | 镜像 tag `X.Y.Z` + `latest` → GHCR(`ghcr.io/xinzhuzi/shishi`) |
| PyPI | [pypi-publish.yml](../../.github/workflows/pypi-publish.yml) | `shishi` + `shishi-classifier` 双包 → pypi.org(OIDC),wheel/sdist 附挂 Release |

PyPI 通道在构建前有两道硬闸:tag 与版本源一致性守卫(第一步的 bump 没做齐
直接红,零产物离库)+ 数据文件校验(`keywords.json` / `prompt.json` 不在
wheel 里就直接失败,到不了 PyPI)。TestPyPI 演练走手动 dispatch,与 tag 正径
互不影响(见下文「TestPyPI 演练」节)。

---

## 第一步:版本 bump(五源同 bump + CHANGELOG + 合入 main)

版本事实源共五处,发布版本一律**五源同 bump**。其中双 pyproject 由 PyPI 工作
流守卫硬拦(不符即红);桌面两件是软警告——桌面打包版本本就从 tag 注入,无
漂移面,同 bump 只为版本纪律可见:

| # | 文件 | 改什么 |
|---|---|---|
| 1 | `pyproject.toml`(根) | `version` + 依赖窗 `shishi-classifier>=X.Y.Z,<下一档` |
| 2 | `myia-classifier/pyproject.toml` | `version` |
| 3 | `src/myia/__init__.py` | `__version__` |
| 4 | `desktop/src-tauri/tauri.conf.json` | `version` |
| 5 | `desktop/src-tauri/Cargo.toml`(+ `Cargo.lock`) | `version`(lock 随下一次桌面构建同步) |

随后:

1. `CHANGELOG.md`:把 `[Unreleased]` 定版为 `[X.Y.Z] — 日期`,顶部新开空
   `[Unreleased]`。
2. commit 合入 main(`git push origin main` 或经 PR)。**tag 必须打在包含本次
   bump 的 commit 上**——tag 触发的工作流按 tag 快照 checkout,守卫校验的也是
   tag 快照里的 pyproject。

> **v0.0.1 实例(当前)**:五源已于 2026-10-03 随版本序列归零统一落在 `0.0.1`
> (根 pyproject 依赖窗 `>=0.0.1,<0.1`),git tag 与 GitHub Release 均已清空
> ——首个发布 tag 即 `v0.0.1`,本步无需再动版本号,直接进第四步。

## 第二步:GitHub 仓库设置(一次性)

1. 仓库页 → **Settings → Environments → New environment**,名称填 **`pypi`**
   (必须与工作流里 `environment: pypi` 逐字一致,Trusted Publishing 的 OIDC
   claim 会带这个环境名)。
2. 可选加固:给 `pypi` 环境加 **Required reviewers = 你自己**,这样每次发布
   会多一道人工确认;也可以限制只有 main 分支可部署。
   当前决议(2026-10-03 grill Q6)**不加**:推 tag 本身就是主人门禁,双确认
   冗余;反悔随时回此页补加,不锁死。
3. 仅 API Token 路径需要(现仅服务 dispatch 演练/兜底):**Settings →
   Secrets and variables → Actions → New repository secret**,名称
   `PYPI_API_TOKEN`,值为第三步 B 生成的 token。tag 触发的正式发布固定走
   OIDC,不受此开关影响。

## 第三步:PyPI 侧准备(路径 A 与 B 二选一,推荐 A)

### 路径 A:Trusted Publishing(OIDC,推荐:零长期凭据、无 token 可泄漏)

对 `shishi` 和 `shishi-classifier` **各注册一次**,四元组完全相同(同一工作流发
多包是 PyPI 官方支持的用法;tag 正式发布与 dispatch 演练都走这条):

| 表单字段 | 填写值 |
|---|---|
| Owner | `xinzhuzi` |
| Repository | `shishi` |
| Workflow filename | `pypi-publish.yml` |
| Environment | `pypi` |
| Destination(版本/tag 限制) | 留空即可 |

注册入口分两种情况:

- **项目还不在 PyPI 上(首发场景)**:登录 <https://pypi.org> → 右上角头像 →
  **Account settings → Publishing**(直达 <https://pypi.org/manage/publishing/>)→
  **Add a new pending publisher**,填上面四元组 + PyPI project name(`shishi` 一次,
  `shishi-classifier` 一次)。pending publisher 在工作流首次成功上传时自动转正并
  创建项目。
- **项目已存在**:打开项目页 → **Manage(设置)→ Publishing → Add a new
  trusted publisher**,填同样四元组。

### 路径 B:API Token(经典方式;现仅服务手动 dispatch 的演练/兜底)

tag 触发的正式发布固定走 OIDC(push 事件下 `use-api-token` 为空串,自动落
OIDC 路径);token 只在手动 dispatch 时可用:

1. <https://pypi.org/manage/account/token/> → **Add API token**:
   - **新项目首发的鸡生蛋问题**:token scope 下拉里只列已存在的项目,所以首发
     时只能选 **scope: account(所有项目)**;两个包都发上去之后,建议删掉
     account 级 token,换 project-scoped token(`scope: shishi` 各建一把)并更新
     GitHub secret——最小权限。
2. 把 `pypi-` 开头的 token 完整粘贴到第二步的 GitHub secret `PYPI_API_TOKEN`。
3. dispatch 时勾选 **use-api-token = true**(tag 事件下该输入为空串,永远走
   OIDC,token 不参与 tag 发布)。
   工作流有守卫:勾了 token 模式但 secret 没配会立刻中文报错失败,不会拿空密码
   去静默回落 OIDC 造成难懂的错误。

> 两路径对照:出问题时 A 的故障面在「PyPI 注册信息 vs OIDC claim 是否一致」,
> B 的故障面在「token 是否有效/过期/权限够」。A 零凭据落盘,公开发布首选;
> B 只在 A 走不通(如 PyPI 侧临时故障)时兜底。

## 第四步:推 tag 发布

```bash
git tag vX.Y.Z          # 例:git tag v0.0.1(打在第一步的 bump commit 上)
git push origin vX.Y.Z  # 推出即发布:三工作流同时开动
```

tag 推出后 Actions 自动起三个 run(互相独立,单通道失败不影响其它通道):

| 工作流 | 做什么 |
|---|---|
| **Desktop Release** | macOS dmg + 更新包 + `latest.json` 附到同 tag Release;Windows msi 构建级验证(允许失败,不阻塞) |
| **Docker Publish** | 双平台镜像(amd64/arm64)推 GHCR,镜像 tag 恰 `X.Y.Z` + `latest` 两个;预发布 tag(如 `v1.2.0-rc.1`)只出 `1.2.0-rc.1`,**不移动 `latest`** |
| **PyPI Publish** | 版本守卫(仅 tag 事件)→ 双包构建 + 数据文件校验 → OIDC 上传 pypi.org → wheel/sdist 附挂同 tag Release(attach-release job) |

- 首发(PyPI 上项目还不存在)前,确认第三步 A 的 **pending publisher 已注册**
  ——否则上传步报 `Invalid or non-existent authentication information`。
- 失败恢复用 Actions 原生 **Re-run**(run 页右上角;Docker 工作流已无手动
  dispatch,PyPI 的 dispatch 仅演练/兜底通道,见下文)。注意:PyPI 的 publish
  job 已成功后重跑整个工作流会在上传步报 `File already exists`(PyPI 禁止
  覆盖)——Re-run 只对失败的 run 有意义,已绿的通道不必重跑。

预期产物矩阵(一个 `vX.Y.Z` 应产出,E2E 核对基准):

| 通道 | 产物 |
|---|---|
| GitHub Release | dmg、`shishi.app.tar.gz`(+sig)、`latest.json`(桌面)+ `shishi-X.Y.Z-*.whl`、`shishi-X.Y.Z.tar.gz`、`shishi_classifier-X.Y.Z-*.whl`、`shishi_classifier-X.Y.Z.tar.gz`(PyPI 附挂);Windows msi(+sig)构建成功时另附 |
| GHCR | 镜像 tag `X.Y.Z`、`latest` 两个(不再产 `vX.Y.Z`、`sha-*`) |
| PyPI | `shishi` 与 `shishi-classifier` 各一个 `X.Y.Z` |

## 第五步:验证发布结果(三通道核对)

以 `v0.0.1` 为例逐项核对:

1. **Actions 三个 run 全绿**:Desktop Release / Docker Publish / PyPI Publish
   (Desktop 的 Windows job 标黄 = 允许失败,不阻塞)。
2. **Release 资产 8 件**:<https://github.com/xinzhuzi/shishi/releases/tag/v0.0.1>
   - 桌面 4 件:`shishi_0.0.1_aarch64.dmg`、`shishi.app.tar.gz`、
     `shishi.app.tar.gz.sig`、`latest.json`;
   - PyPI 附挂 4 件:`shishi-0.0.1-*.whl`、`shishi-0.0.1.tar.gz`、
     `shishi_classifier-0.0.1-*.whl`、`shishi_classifier-0.0.1.tar.gz`;
   - Windows msi 成功时另有 `*.msi`(+`.msi.sig`),不计入 8 件核对。
3. **GHCR 镜像 tag 恰两个**:`0.0.1` 与 `latest`(无 `v0.0.1`、无 `sha-*`);
   `docker pull ghcr.io/xinzhuzi/shishi:0.0.1` 可拉。
4. **PyPI 页面**:<https://pypi.org/project/shishi/> 与
   <https://pypi.org/project/shishi-classifier/> 可访问、版本号 `0.0.1`、README
   正常渲染(中文简介 + MIT license);`pip index versions shishi` 列出
   `0.0.1`。
5. **干净环境安装验证**(模拟真实用户,注意 pip 装的是 PyPI 包,不再走 workspace):

```bash
uv venv /tmp/verify-shishi && source /tmp/verify-shishi/bin/activate
# 先验独立分类器(零依赖,应秒装):
pip install shishi-classifier
python -c "from myia_classifier import classify_title, ALL_CATEGORIES; \
           print(classify_title('OpenAI 发布新模型').category, ALL_CATEGORIES)"
# 预期输出:ai-news(加 7 个类目列表)

# 再验主包(会自动拉 shishi-classifier 依赖):
pip install shishi
shishi --version          # 预期输出:shishi <刚发布的版本号>(与上一步 PyPI 页面所示一致)
deactivate
```

6. 有问题回滚:PyPI 不允许覆盖已上传版本,修复后回第一步 **bump 版本号再发**
   (五源 + 主包依赖窗 `shishi-classifier>=x,<y` 区间)。

## 第六步:社区发帖(发布确认后)

文案弹药库在 [`docs/launch/`](./README.md),先过一遍其中「发布前统一检查」
(零凭据 / 状态如实 / 采集伦理 / demo 已脱敏):

| 平台 | 文案文件 | 语言 |
|---|---|---|
| Reddit r/selfhosted | [reddit-r-selfhosted.md](reddit-r-selfhosted.md) | en |
| V2EX(分享创造) | [v2ex.md](v2ex.md) | zh |
| 即刻 | [jike.md](jike.md) | zh |
| LinuxDo | [linuxdo.md](linuxdo.md) | zh |

PyPI 已发布后,各文案里的安装命令从「源码安装」切换为 `pip install shishi`
(发帖前 `docs/launch/README.md` 检查表有对应项)。发出后按 README 的
「首周反馈汇总」表记录链接与反馈。

## TestPyPI 演练

首次正式发布前,先在 <https://test.pypi.org> 全流程彩排一遍。工作流已内置
`repository` 开关:dispatch 选 `test-pypi`(**默认**)即上传到
`https://test.pypi.org/legacy/`,选 `pypi` 才走正式
`https://upload.pypi.org/legacy/`。演练产物与正式 PyPI 完全隔离。
**演练走手动 dispatch,不推 tag**:tag 版本守卫只在 tag 事件运行,演练不受
守卫拦截;演练通道与 tag 正式发布互不影响(正式发布只认 tag)。

### 准备(test.pypi.org 侧,一次性)

test.pypi.org 与 pypi.org 的账号、publisher、token **互不相通**,两边各配各的:

- **路径 A(Trusted Publishing,推荐)**:登录 <https://test.pypi.org/manage/publishing/>
  → **Add a new pending publisher**,四元组与第三步 A 逐字相同
  (`xinzhuzi` / `shishi` / `pypi-publish.yml` / `pypi`),PyPI project name 各填
  `shishi` 与 `shishi-classifier` 一次。GitHub 侧无需新增任何东西
  (`environment: pypi` 沿用第二步已建的环境)。
- **路径 B(API Token)**:登录 <https://test.pypi.org/manage/account/token/> →
  **Add API token**,把 `pypi-` 开头的 token 粘贴到仓库 secret
  `PYPI_API_TOKEN`(与正式发布同一个 secret 名)。两点注意:
  1. token 必须在 **test.pypi.org** 生成——pypi.org 的 token 在 TestPyPI 无效
     (反之亦然),工作流守卫会对 test-pypi 给出相应提示;
  2. **正式发布前记得换回 pypi.org 的 token**(或正式发布直接走路径 A,免来回换)。
     test.pypi 上项目还不存在时 scope 只能选 account,同第三步 B 的鸡生蛋说明。

### 演练命令

`gh` 已登录、`pypi-publish.yml` 已合入 main 时:

```bash
gh workflow run pypi-publish.yml -f repository=test-pypi
# 或显式给全参数(演练建议 both,把双包链路一次跑通;OIDC 路径):
gh workflow run pypi-publish.yml --ref main \
  -f repository=test-pypi -f package=both -f use-api-token=false
# 看进度:
gh run watch "$(gh run list --workflow=pypi-publish.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

### 演练后校验

1. **Actions 日志**:该次 run 的 publish job 全绿,日志里
   *Resolve upload repository* 步骤应打印 `上传目标:test-pypi → https://test.pypi.org/legacy/`。
2. **test.pypi 项目页**:<https://test.pypi.org/project/shishi-classifier/> 与
   <https://test.pypi.org/project/shishi/> 可访问、版本号正确(pending publisher
   首次上传成功后自动建项目,刚注册完看不到项目页是正常的)。
3. **pip index 查版本**(不动本地环境):

   ```bash
   pip index versions shishi-classifier --index-url https://test.pypi.org/simple/
   # 预期列出刚演练上传的版本号
   ```

4. **安装验证**(可选,模拟真实用户):

   ```bash
   uv venv /tmp/verify-testpypi && source /tmp/verify-testpypi/bin/activate
   pip install --index-url https://test.pypi.org/simple/ shishi-classifier
   # 验主包时其余依赖不在 TestPyPI 上,需挂正式源兜底:
   pip install --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ shishi
   deactivate
   ```

演练确认 build → 校验 → 上传全链路无误后,正式发布回第四步推 tag:tag 事件
固定走正式 PyPI + OIDC(`repository` 选项只对 dispatch 生效,正式发布无需选)。

## GHCR 版本管理

Docker 工作流历史上 main 每推必发,在 GHCR 堆积了 `1.1.1` / `v1.1.1` /
`sha-*` 等旧序列版本(2026-10-03 tag 驱动改造后不再新增)。清理是破坏性操作,
属**主人手工操作**,仓库工作流不自动删任何版本:

- **入口**:GitHub 仓库页右侧 **Packages → shishi → Package versions**,逐条
  Delete。
- **清理时点**:首个 tag(`v0.0.1`)按第五步核对全绿后**全清**存量——
  `1.1.1` / `v1.1.1` / `sha-*` 一个不留(2026-10-03 决议 9 修订 grill Q4,随
  版本序列归零一并作废);清完 versions 页只应剩 `0.0.1` 与 `latest`。
- **untagged manifest**:versions 列表里不带版本号、标 *untagged* 的条目是
  历史 tag 被覆盖(如 `latest` 滚动)后失去标签的镜像 manifest,不占 tag 名、
  不影响 `docker pull`,只占存储;随存量一并删除即可。
- **TestPyPI 演练残留**:演练只上传 TestPyPI,不产任何 GHCR 镜像(Docker
  工作流已无 dispatch),GHCR 清理无需考虑演练残留;TestPyPI 侧产物如需处理
  走 test.pypi.org 项目页,同样不自动删。

## 故障排查

| 症状 | 原因与处置 |
|---|---|
| Actions 列表看不到 PyPI Publish | workflow 文件不在 main(回第一步合入) |
| publish 报 `environment pypi not found` 或 claim 不匹配 | 第二步环境名与第三步四元组不一致,逐字核对(`xinzhuzi` / `shishi` / `pypi-publish.yml` / `pypi`) |
| 报 `Invalid or non-existent authentication information`(OIDC 模式) | PyPI 侧 pending publisher 未注册或四元组填错 |
| token 模式报 403 | token 过期/权限不足/未更新到 GitHub secret;或首发用了 project-scoped token 但项目还不存在(见第三步 B 的鸡生蛋问题) |
| build job Verify 步骤失败 | 产物缺数据文件(词表/prompt 回归被拦截)——修 pyproject 的 artifacts 配置,不要跳过校验 |
| `File already exists` | 版本号已存在,PyPI 禁止覆盖,回第一步 bump 版本重发 |
| PyPI run 守卫步骤红「版本不一致」 | 第一步的版本没 bump 齐(tag ≠ 双 pyproject 之一);守卫红时零产物离库,删 tag 重来安全:`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`,五源补齐后重打重推 |
| Release 里有 PyPI 的 wheel/sdist,但缺桌面资产(dmg / `latest.json`) | Desktop Release run 挂了——桌面上传与 PyPI 附挂是两个独立 run,各自往同一 Release 追加;查桌面 run 失败原因,修后对它 Re-run |
| GHCR 只出了版本 tag,`latest` 没动 | 预发布 tag 的预期行为(`latest=auto` 对 rc/beta 不滚动 `latest`);发正式版即滚动 |
| 旧版(1.1.1)桌面端「检查更新」404 | v1.1.1 Release 已随版本序列归零删除(决议 9),updater 端点 `releases/latest/download/latest.json` 在无任何 Release 时必 404;`v0.0.1` 发布后即恢复。注意 updater **只升不降**——已装 1.1.1 的机器不会自动降到 0.0.1,需手动重装 `shishi_0.0.1_aarch64.dmg` |
