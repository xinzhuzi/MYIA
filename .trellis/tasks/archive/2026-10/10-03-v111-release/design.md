# design — v1.1.1 发布执行(版本对齐 + demo 插件 + updater 接线 + tag/CHANGELOG/PyPI/README)

依据:本档 prd.md(权威需求)+ `10-03-grill-v112/prd.md`(R2-1 做全 updater、
R2-2 不公证改右键指引、R2-3 PyPI 先演练、R2-4 AI 出素材主人定稿)
+ `.trellis/spec/python/index.md`「桌面发行数据根」节(发布前必跑真实安装冒烟)。
本文所有行号与命令输出均为 2026-10-03 本会话实测。

---

## 0. 结论速览

| 决策点 | 结论 |
|---|---|
| demo 数据源 | **GitHub Search API**(免凭据 JSON;单请求 30 条;curl 实测 200) |
| demo 插件形状 | `direct_api` + `json_path` 抽取 + `push: stdout` + `classify.builtin: false`,零 `env:`/`keychain:` 引用 |
| updater UI 接线 | settings 屏新增「软件更新」卡片;`ui-src` 补 `@tauri-apps/plugin-updater` + `@tauri-apps/plugin-process`;Rust 侧已注册,**勿动** |
| 版本 bump | 四处 `0.1.0 → 1.1.1`(pyproject / classifier pyproject / Cargo.toml / tauri.conf.json);Cargo.lock 随构建自动跟随 |
| 发布顺序 | 改动合 main → gitleaks 零命中 → 主人三密钥就位 → tag `v1.1.1` → desktop-release → 真机安装冒烟 → test.pypi 演练 → PyPI 正式 → 四帖素材 |

---

## 1. demo 数据源选型(实测证据,2026-10-03)

### 1.1 候选实测

| 候选 | 实测命令 | 结果 | 判定 |
|---|---|---|---|
| **GitHub Search API** | `curl -sS -m 20 'https://api.github.com/search/repositories?q=created:%3E2026-09-26&sort=stars&order=desc&per_page=30' -H 'Accept: application/vnd.github+json'` | **HTTP 200**,2.219s,191861B,`total_count: 3526829`,`items: 30`;字段 `full_name`/`html_url`/`stargazers_count`/`description`/`pushed_at` 全在 | ✅ **选定** |
| hnrss.org/frontpage | `curl -sS -m 20 'https://hnrss.org/frontpage'` | 第一次 `SSL_ERROR_SYSCALL`(HTTP 000,1.858s);重试 HTTP 200 | ✗ 拒连不稳定;且是 RSS/XML 非 JSON,只能走 static_html 解析 |
| HN firebase API | `curl -sS -m 20 'https://hacker-news.firebaseio.com/v0/topstories.json'` | HTTP 200,但只回 ID 数组 `[49937276,…]` | ✗ 取标题需逐条 `v0/item/<id>.json`(N+1 请求),**单请求拿不到 ≥10 条完整记录** |

### 1.2 选定源的工程细节(全部实测)

- **抽取验证(用仓库自身代码)**:`.venv/bin/python` 走 `src/myia/engines/fetch_base.py:1157` 的 `extract_json` 对实测响应跑拟用 fields(`$.items[*].full_name` / `$.items[*].html_url` / `$.items[*].stargazers_count` / `$.items[*].description`)→ **30 行、0 行缺 url**;月窗 URL(`created:>2026-09-01`)另测同样 200 / 30 行 / 0 缺,top3 = `eternity4719/HowToLiveBetter(35405⭐)` 等。
- **robots**:`api.github.com/robots.txt` → 404 JSON(`{"message": "Not Found"}`),无任何 Disallow 规则 → YAML 默认 `respect_robots: true` 不会被拦。
- **UA**:GitHub 仅要求 UA 存在;实测 `curl -A 'python-httpx/0.27'`(= httpx 默认 UA)→ 200。YAML 无需写浏览器 UA,只写 `Accept: application/vnd.github+json`。
- **限额**:免凭据 Search API 限额 10 次/分钟(GitHub 文档口径)。demo 单源单请求/次运行,且 `direct_api.py:89-93` 的变更指纹会跳过未变响应——远低于限额,失败也无凭据可撞(fail-fast 铁律不破)。
- **日期窗口取固定 `created:>2026-09-01`**:schema 的 URL 模板只支持 `{symbol}`/`{page}` 占位(fetch_base `_template_walks` 语义),**不支持当日日期**;固定月窗随时间变宽,榜单长期有效(新星持续涌现),不会 404/空结果。截图翻新时可顺手前移日期(写入 YAML 注释)。
- **同前缀抽取无错位**:四个 field 全部落在同一数组 `$.items[*]` → 走 `fetch_base.py:1173-1187` 的逐元素配对路径(缺字段的元素只丢该字段,不错位)。

---

## 2. `plugins/myia-demo.yaml` 形状(实施时按此落盘)

形状对齐夹具惯例 `desktop/fixture/plugin.yaml:26-27`(`builtin: false` 直通 + stdout 通道零 target)与 README:161-164 的 `demo-min.yaml` 示例:

```yaml
# plugins/myia-demo.yaml — 开箱演示件(v1.1.1 随包第五件,grill Q3)。
# 目的:装机首跑「运行第一个插件」第一次就出真数据,不撞 config_error;
#      同时是 README 五屏截图的数据来源。
# 铁律:零凭据(stdout 通道不需要 target)、零 env:/keychain: 引用(fail-fast 不破)、
#      分类 builtin 关闭直通(夹具惯例)。
id: myia-demo
name: 演示 · GitHub 新星
schedule: "0 9 * * *"          # 仅 --loop 常驻时生效;桌面 run.start 单跑不看它

sources:
  # GitHub Search API:免凭据 JSON、单请求 30 条(2026-10-03 实测 200)。
  # q=created:>2026-09-01 为固定日期:URL 模板只支持 {symbol}/{page},不支持
  # 当日日期;窗口随时间变宽,榜单长期有效,截图翻新时可前移该日期。
  - name: github-new-stars
    engine: direct_api
    url: "https://api.github.com/search/repositories?q=created:%3E2026-09-01&sort=stars&order=desc&per_page=30"
    method: GET
    headers:
      Accept: "application/vnd.github+json"
    extract:
      type: json_path
      fields:                   # 同前缀 $.items[*] → 逐元素配对(fetch_base.py:1173)
        title: "$.items[*].full_name"
        url: "$.items[*].html_url"
        stars: "$.items[*].stargazers_count"
        desc: "$.items[*].description"
    rate_limit:
      qps: 0.5
      jitter: "2s"
      backoff: exponential
      respect_robots: true      # api.github.com/robots.txt 为 404(无规则),默认无害
    proxy: direct
    retry: 3

classify:
  builtin: false                # 演示条目不属于七大论坛类,关掉内置扫描直通

dedup:
  key: "{url}"                  # 同一仓库重跑不重复入库/推送

push:
  - channel: stdout             # 零凭据通道:stdout 不需要 target(desktop/fixture 惯例)

storage:
  retention: 7d
  vacuum: monthly
```

要点:

- `extract.fields` 必含 `url` —— schema.py:545-550 对 `json_path` 强制(去重键依赖 URL)。
- **不写** `watchlist`/`enrich`(直通最简;enrich 默认 pass-through,见 ai-news.yaml 注释);demo 件零 token、零 LLM 依赖。
- **随包**:`desktop/src-tauri/tauri.conf.json` 的 `bundle.resources` 增一行
  `"../../plugins/myia-demo.yaml": "plugins/myia-demo.yaml"`(与四件套同款映射)。
- **种子自动带上**:首跑种子对 `Resources/plugins/*.yaml` 做 glob 拷贝
  (desktop/entry.py:242-243),**无需改任何 Python**;`.seeded` 标志逻辑照旧。

---

## 3. updater UI 接线点(R2-1 批做全)

### 3.1 现状(已就绪,勿动)

| 位置 | 状态 |
|---|---|
| `desktop/src-tauri/src/main.rs:146-150` | shell/updater/process 三插件已 `.plugin(...)` 注册 —— **勿动**(ask 明令) |
| `desktop/src-tauri/capabilities/default.json` | 已授 `updater:default` + `process:allow-restart` |
| `desktop/src-tauri/Cargo.toml` | 已依赖 `tauri-plugin-updater = "2"` / `tauri-plugin-process = "2"` |
| `desktop/src-tauri/tauri.conf.json` → `plugins.updater` | endpoints 指向 Releases `latest.json`,pubkey 为占位符(CI 注入) |
| `.github/workflows/desktop-release.yml` | tag 触发、签名产物、latest.json 生成全就绪 |

### 3.2 缺口(本任务 AI 侧工项)

`desktop/UPDATER.md` 第六节明确:「前端检查更新入口未接线:Rust 侧插件已注册,
但 UI 调用需要 `ui-src` 增加 `@tauri-apps/plugin-updater` 与
`@tauri-apps/plugin-process` npm 依赖及**设置页按钮**」。实测
`desktop/ui-src/package.json` dependencies 两者皆无(只有 `@tauri-apps/api ^2.5.0`)。

### 3.3 接线设计

- **依赖**:`ui-src` 安装 `@tauri-apps/plugin-updater@2` + `@tauri-apps/plugin-process@2`
  (relaunch 需要 process 的 JS 包,与 Rust 侧两插件一一对应)。
- **入口**:settings 屏(`desktop/ui-src/src/screens/settings/settings-screen.tsx`)
  新增「软件更新」Card(现屏已有 LLM/代理池/推送三表单 + doctor 回显,复用
  `Card`/`Button`/`ErrorBox` 组件与状态机惯例):
  1. 「检查更新」→ `check()`(plugin-updater);
  2. 无更新:回显「已是最新(当前 vX.Y.Z)」;有更新:显示 `version` + `notes`,
     出「下载并安装」按钮;
  3. 下载安装 → `downloadAndInstall()` → 成功后 `relaunch()`(plugin-process);
  4. 任一步失败:沿用 `error-box.tsx` 回显(该屏已有 ErrorBox 惯例)。
- **组件测试**:`settings.test.tsx` 既有 `vi.mock("@tauri-apps/api/core")` 模式
  (settings.test.tsx:10-11),新增 `vi.mock("@tauri-apps/plugin-updater")` /
  `vi.mock("@tauri-apps/plugin-process")` 三态用例:无更新 / 有更新→安装→relaunch / 检查失败→ErrorBox。
- **CSP 无需改**:updater 的 HTTP 请求由 Rust 侧发起,不经 webview fetch,
  `tauri.conf.json` 的 `connect-src` 不必加 github.com。
- **真机闭环**(密钥就位后,主人侧配合):按 UPDATER.md 第五节以 `9.9.9` 签名包
  演示「检查→下载→验签→passive 安装→relaunch」;v1.1.1 首发的 latest.json 本身
  即构成后续版本的更新通道。UI 侧本任务只验 mock 测试 + `npm run build` 通过。

---

## 4. 版本号 bump 清单(0.1.0 → 1.1.1)

| # | 文件:位置 | 现值(实测) | 说明 |
|---|---|---|---|
| 1 | `pyproject.toml:7` | `version = "0.1.0"` | `myia --version` 的最终来源:cli.py:247 读 `myia.__version__` ← 包元数据;**bump 后须 `uv sync` 刷新 editable 元数据再验** |
| 2 | `myia-classifier/pyproject.toml:7` | `version = "0.1.0"` | PyPI 第二包 |
| 3 | `desktop/src-tauri/Cargo.toml:3` | `version = "0.1.0"` | rust 包版本 |
| 4 | `desktop/src-tauri/tauri.conf.json` `"version"` | `"0.1.0"` | .app 的 CFBundleShortVersionString 与 dmg 文件名的来源 |
| — | `desktop/src-tauri/Cargo.lock:1979-1980` | `myia-desktop` `0.1.0` | **不改手改**:随 `cargo check`/`tauri build` 自动刷新,以 grep 验证 |
| — | `desktop/ui-src/package.json` `"version"` | `"0.1.0"` | **不在四处清单,不动**:.app 版本以 tauri.conf.json 为准,此值不对外 |

### 4.1 流水线注入顺序(PRD Constraints 要求覆盖)

`desktop-release.yml:88-99`(macOS 主线,Windows job 198-209 同构)在 tag 触发后:

```
tag v1.1.1 → 写 tauri.release.conf.json({version: <tag 版本>,
  bundle: {createUpdaterArtifacts: true}, plugins: {updater: {pubkey: <Secret>}}})
→ npx tauri build --bundles app,dmg --config tauri.release.conf.json(合并覆盖)
→ 生成 latest.json(version = tag 版本,desktop-release.yml:112-129)→ 附 Release
```

由此推出**硬顺序**:四处 bump 必须先合入 main 并推远端,**再**打 `v1.1.1` tag ——
合并片 `{version: <tag>}` 会覆盖基础 conf,若 tauri.conf.json 仍是 0.1.0,虽被覆盖
不直接报错,但本地/CI 普通构建与发布产物版本错位;且 `latest.json` 的 version 与
客户端 .app 内版本(tauri.conf.json)必须同源 1.1.1,否则 updater 比较语义失真。
基础 conf 里 `bundle.createUpdaterArtifacts` 与真实 pubkey 永不落入(UPDATER.md
设计定案:本地 `npx tauri build` 不因缺签名环境失败)。

---

## 5. README / CHANGELOG 改法

### 5.1 `CHANGELOG.md`(新建;keep-a-changelog 风格,英文为主)

三节起步(PRD 口径「从 v1.0 起步」;v0.x 里程碑已在 README roadmap 表,不重复):

- **[1.1.1] — 发布日**:Fixed 段 = 桌面数据通路修复(MYIA_HOME 统一、随包插件、
  首跑种子,commit 45639c3);Added 段 = 开箱 demo 插件(GitHub 新星,零凭据)、
  更新通道(签名校验 + 设置页「检查更新」)、PyPI 双包(`pip install myia` /
  `myia-classifier`)、README Gatekeeper 右键打开指引。
- **[1.1.0] — 桌面优先**:Tauri 2 壳 + PyInstaller sidecar、五屏
  (dashboard/sources/feed/logs/settings)、settings 凭据表单 + doctor 验证。
  **交付列表按实际口径**——卡片反馈按钮/settings 反馈开关/采集量趋势不列
  (排 v1.2,见 B2/B3/B4 如实化)。
- **[1.0.0] — 公开发布**:双语文档、演示物料、GitHub 门面。

### 5.2 `README.md`(行号为当前实测;中英两处同改)

| 位置 | 改法 |
|---|---|
| :16 | 徽章 `status-alpha-orange` → `status-1.1` 稳定口径(蓝/绿) |
| :118 / :378 | 反馈闭环段「卡片内按钮随桌面版交付」→「卡片内按钮随 v1.2 交付」(B2 如实化,中英两处) |
| :235 / :500 | 「仍是 alpha:糙边犹存」/ "Still alpha: rough edges remain" 两段改写:桌面端 v1.1.1 已可日常使用;反馈按钮/采集趋势等排 v1.2;Windows 产物为构建级验证 |
| :244 / :509 | v1.1 行 `🚧 进行中` → `✅ 已交付`,范围列**只留实际交付**(桌面壳+sidecar+进程内插件级),「卡片内反馈按钮」移出;紧随新增 `v1.1.1` 行(数据通路修复 + demo 插件 + 更新通道)✅。注:B3(settings 反馈开关)/B4(采集量趋势)从未在 README 文中宣称(census B3/B4 指向 10-02 桌面 PRD),README 无需为其改文本,补实现进 `10-03-v12-backlog` |
| :132 / :393 | A1 修正:安装口径以 `uv sync` 为主;`pip install -e .` 措辞改为如实(或删);**发布后** A2 兑现为 `pip install myia` / `pip install myia-classifier` 实链 |
| :174 | PyPI 注释改实链(PyPI 正式发布后;发布前先改「见 GitHub Releases 下载」) |
| 新增「下载安装」节 | GitHub Releases dmg 链接 + **右键打开绕 Gatekeeper 三步指引**(R2-2 定案:不做公证;步骤:1)下载 dmg 拖入 Applications;2)首次打开**右键 → 打开 → 再点打开**(或系统设置→隐私与安全性→仍要打开);3)说明未公证原因与安全性来源(开源可审计 + Actions 构建日志) |
| 五屏截图 | demo 插件跑出的真实数据截五屏(dashboard/sources/feed/logs/settings),存 `docs/demo/assets/`(现有目录),中英 README 引用;替换/补充现有 `myia-demo.gif` 演示位 |

### 5.3 PyPI 演练接线(R2-3)

`pypi-publish.yml` 当前只发正式 PyPI(`pypa/gh-action-pypi-publish` 未传
`repository-url`,:187-198);其注释(:24-25)已预留演练口径。**改法**:增加
`workflow_dispatch` 输入 `repository-url`(string,默认空 = `https://upload.pypi.org/legacy/`,
演练时填 `https://test.pypi.org/legacy/`),两个 Publish 步骤透传该输入。
`docs/launch/RELEASE.md` 增补「TestPyPI 演练」节:主人先在 test.pypi.org 注册
publisher(OIDC 四元组同正式,RELEASE.md 第三步路径 A 表格)或用 test 侧 token,
dispatch `package=both` + test repository-url,`pip install -i https://test.pypi.org/simple/ myia==1.1.1` 冒烟,绿后正式 dispatch。

---

## 6. 密钥扫描(v10-release 移交项)

- 本机实测 `which gitleaks` → 未安装。实施时:`brew install gitleaks` →
  `gitleaks detect --source . --redact -v`(扫全量 git 历史,含默认规则集)→
  **零命中**;命令与输出摘要记入任务日志。
- 兜底:brew 不可用则等价工具 `trufflehog git file://.`;两者都不可用 → escalate,
  不得以「看起来没有」替代扫描。

---

## 7. 风险与边界

- **主人侧硬前置只有一个**:R2-1 三密钥(`npx tauri signer generate` + 3 个
  Secrets,UPDATER.md 第二节)。Secrets 未配时 desktop-release.yml 守卫步骤会
  直接中文报错、不出半成品 Release(desktop-release.yml:46-47)——tag 前必须确认。
- **真实安装冒烟是发布验收口径**(spec/python/index.md:41):mock 层绿不算数;
  dmg 装机(cwd=/ 矩阵)首跑点「运行第一个插件」出 GitHub 新星真数据。
- **GitHub Search API 限额**:10 次/分钟(未认证)。单机 demo 无风险;若演示
  密集点击可复现 403——属预期,重试即可(重试间隔 `retry: 3` + 指纹跳过缓解)。
- **工作区已有未提交改动**(实测 `git status`:`dashboard-screen.tsx`、
  `tests/test_desktop_sidecar_protocol.py`、journal、`?? .trellis/tasks/10-03-yaml-editor/`)
  属并行会话在途工作,实施前先 `git diff` 审计,本任务提交**不裹挟**这些改动。
- **禁区遵守**:本规划会话不改产品代码、不 commit、不推送;一切执行入 implement.md。
