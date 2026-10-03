# 世事 PyPI 发布 Runbook(主人专用)

> **红线声明**:实发布需要**主人的凭据**(PyPI 账号授权 + GitHub 仓库管理员权限)。
> 本 runbook 把一切准备到「主人一键可发」;准备阶段(AI 任务 10-01)未触发任何
> 发布动作——没有 dispatch 工作流,没有上传,没有在 PyPI 注册任何东西。
> 发布节奏由主人定,以下每一步都由主人亲手执行或在主人授权后执行。

发什么:`shishi` 与 `shishi-classifier` 两个包(前者依赖后者),由
[`.github/workflows/pypi-publish.yml`](../../.github/workflows/pypi-publish.yml)
一键构建 + 校验 + 上传。工作流**只有 `workflow_dispatch` 手动触发**,推送/tag
永远不会误发 PyPI;构建产物在发布前有数据文件硬校验(`keywords.json` /
`prompt.json` 不在 wheel 里就直接失败,到不了 PyPI)。

---

## 第一步:推送代码到 GitHub

workflow_dispatch 的 **Run workflow 按钮只对默认分支(main)上存在的 workflow
文件显示**。所以先确认 `pypi-publish.yml` 已合入 `main`:

```bash
git push origin main        # 或经 PR 合入
# 确认远端文件已到位:
git ls-remote origin main   # 拿到最新 commit 后,在 GitHub 网页核对
# https://github.com/xinzhuzi/shishi/blob/main/.github/workflows/pypi-publish.yml
```

## 第二步:GitHub 仓库设置(一次性)

1. 仓库页 → **Settings → Environments → New environment**,名称填 **`pypi`**
   (必须与工作流里 `environment: pypi` 逐字一致,Trusted Publishing 的 OIDC
   claim 会带这个环境名)。
2. 可选加固:给 `pypi` 环境加 **Required reviewers = 你自己**,这样每次发布
   会多一道人工确认;也可以限制只有 main 分支可部署。
3. 仅 API Token 路径需要:**Settings → Secrets and variables → Actions →
   New repository secret**,名称 `PYPI_API_TOKEN`,值为第三步 B 生成的 token。

## 第三步:PyPI 侧准备(路径 A 与 B 二选一,推荐 A)

### 路径 A:Trusted Publishing(OIDC,推荐:零长期凭据、无 token 可泄漏)

对 `shishi` 和 `shishi-classifier` **各注册一次**,四元组完全相同(同一工作流发
多包是 PyPI 官方支持的用法):

| 表单字段 | 填写值 |
|---|---|
| Owner | `xinzhuzi` |
| Repository | `世事` |
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

### 路径 B:API Token(经典方式)

1. <https://pypi.org/manage/account/token/> → **Add API token**:
   - **新项目首发的鸡生蛋问题**:token scope 下拉里只列已存在的项目,所以首发
     时只能选 **scope: account(所有项目)**;两个包都发上去之后,建议删掉
     account 级 token,换 project-scoped token(`scope: shishi` 各建一把)并更新
     GitHub secret——最小权限。
2. 把 `pypi-` 开头的 token 完整粘贴到第二步的 GitHub secret `PYPI_API_TOKEN`。
3. 第六步 dispatch 时勾选 **use-api-token = true**。
   工作流有守卫:勾了 token 模式但 secret 没配会立刻中文报错失败,不会拿空密码
   去静默回落 OIDC 造成难懂的错误。

> 两路径对照:出问题时 A 的故障面在「PyPI 注册信息 vs OIDC claim 是否一致」,
> B 的故障面在「token 是否有效/过期/权限够」。A 零凭据落盘,公开发布首选;
> B 只在 A 走不通(如 PyPI 侧临时故障)时兜底。

## 第四步:dispatch 发布工作流

1. 仓库页 → **Actions → 左侧 PyPI Publish → Run workflow**。
2. 选项:
   - **package**:
     - 首发或双包同版本发布 → 选 `both`(两包一起,共 4 个产物:wheel+sdist × 2);
     - 只更分类器 → `shishi-classifier`;只更主包 → `shishi`。
   - **use-api-token**:走路径 A 留 `false`;走路径 B 勾 `true`。
3. 点 **Run workflow**,等 build → publish 两个 job 全绿(首发约 2~3 分钟)。
   build job 的 *Verify distributions contain packaged data files* 步骤会打印
   每个 wheel/tar 的文件数与数据文件 OK 清单,失败会列出具体缺哪个文件。

## 第五步:验证发布结果

1. **workflow 日志**:publish job 无红色报错;Upload 行列出 4 个(或选单包时
   2 个)产物 URL。
2. **PyPI 页面**:
   - <https://pypi.org/project/shishi/> 与 <https://pypi.org/project/shishi-classifier/>
     可访问,版本号正确,README 正常渲染(中文简介 + MIT license)。
3. **干净环境安装验证**(模拟真实用户,注意 pip 装的是 PyPI 包,不再走 workspace):

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

4. 有问题回滚:PyPI 不允许覆盖已上传版本,修复后 **bump 版本号再发**
   (两个包的 `pyproject.toml` 中 `version` + 主包依赖里的
   `shishi-classifier>=x,<y` 区间)。

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

## 可选:TestPyPI 演练

首次正式发布前,可在 <https://test.pypi.org> 全流程演练一遍:publisher 四元组
配在 test.pypi.org 侧、token 用 TestPyPI 的,dispatch 跑完用
`pip install --index-url https://test.pypi.org/simple/ shishi-classifier` 验证。
演练产物与正式 PyPI 完全隔离。

## 故障排查

| 症状 | 原因与处置 |
|---|---|
| Actions 列表看不到 PyPI Publish | workflow 文件不在 main(回第一步) |
| publish 报 `environment pypi not found` 或 claim 不匹配 | 第二步环境名与第三步四元组不一致,逐字核对(`xinzhuzi` / `世事` / `pypi-publish.yml` / `pypi`) |
| 报 `Invalid or non-existent authentication information`(OIDC 模式) | PyPI 侧 pending publisher 未注册或四元组填错 |
| token 模式报 403 | token 过期/权限不足/未更新到 GitHub secret;或首发用了 project-scoped token 但项目还不存在(见第三步 B 的鸡生蛋问题) |
| build job Verify 步骤失败 | 产物缺数据文件(词表/prompt 回归被拦截)——修 pyproject 的 artifacts 配置,不要跳过校验 |
| `File already exists` | 版本号已存在,PyPI 禁止覆盖,bump 版本重发 |
