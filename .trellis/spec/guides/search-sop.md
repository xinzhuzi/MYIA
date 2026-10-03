# 搜索标准操作手册(Search SOP)

> **项目根目录**: `~/Project/Github/MYIA`
>
> 2026-10-03 迁入(任务 10-03-spec-discipline-sop-gitnexus):由 MYStudio 权威版 search-sop 改写为 MYIA 布局。热路径与仓库外路径以本文件为 MYIA 唯一权威版;变更热路径、排除规则或网络路由时同步本文件,**不要**复制完整配方到其它规则文件。
>
> ## 🚨 两大铁律(执行搜索前必读)
>
> ### 铁律 1: 中文路径陷阱
> 部分模型在中英文交界处会自动插入 U+0020 空格!
> - ❌ 错误:`.trellis/tasks/10-03 任务`(有空格)
> - ✅ 正确:`.trellis/tasks/10-03任务`(无空格)
> - 🔧 解决:**永远不要手工拼接中文路径**,用 `fd` 或 Python `os.listdir()` 动态获取。本仓库当前文件名全 ASCII(2026-10-03 全仓扫描核实);中文路径风险主要在**仓库外**——`~/Library/Application Support/` 下的邻居目录、本地档案、记忆目录——以及未来新增的中文命名文件。
>
> ### 铁律 2: 先 Read 再操作
> 任何 `Bash/Edit/Write` 命令前**必须先 Read** 相关文件找准确位置!`Edit` 的 old_string 不匹配报错只是表象,根因是没验证原文。

---

**先读后搜**(强制):任何仓库内容、文件名、符号、仓库外本地数据或网络内容搜索前,先完整读取本文件;随后仅按本文的范围、工具与排噪规则搜索。

本项目是 Python 包 + Tauri 桌面端的混合仓库,产物与依赖目录很大(`node_modules/`、`desktop/ui/`、`desktop/src-tauri/target/`、`.venv/`)。统一使用 `rg` / `fd` / `fzf` / LSP / `gitnexus`,**禁止**全树 `grep` / `find`。

---

## 🛠️ 核心工具速查

| 工具 | 用途 | 示例命令 |
|------|------|----------|
| `fd` | 文件名搜索(推荐) | `fd vision src/myia` |
| `rg` | 文件内容搜索(正则/精准匹配) | `rg 'def fetch' src/myia/push` |
| `gitnexus` | 语义搜索 + 代码知识图谱 + 影响分析 | `gitnexus query -r MYIA 'push channel dispatch lifecycle'` |
| `fzf` | 交互模糊筛选 | `fzf` |
| LSP | TypeScript 符号索引(`desktop/ui-src`) | `smart_search` / `smart_outline` 或 `tsserver` |

> **ripgrep vs GitNexus 分工**: `rg` 做精准文本/正则匹配(快、轻、无需索引);`gitnexus` 做语义搜索、调用链追踪、影响分析(深、需索引、知识图谱)。两者互补,不替代。GitNexus 是重工具,不适合"找一个字符串"这类轻量任务。

---

## 30 秒决策

1. **先定范围**:选择下方已验证的最小目录;未知范围时先用目录/文件名定位,**禁止**从仓库根开始内容搜索。目标在仓库外(APP 数据/本地档案/记忆)→ 用「仓库外本地热路径」表;目标是网络内容 → 用「网络搜索路由」表。
2. **再选工具**:
   - **文件名** → `fd`
   - **精准文本/正则匹配**(已知关键词、类名、字符串字面量)→ `rg`
   - **TypeScript 符号**(定义/引用/outline)→ LSP 或 `tsserver`
   - **语义搜索**("哪里实现了推送去重"、"哪些地方涉及视觉客户端生命周期")→ `gitnexus query`
   - **调用链/影响分析**("谁调用了 fetch_feed()、改这个函数会影响哪些文件")→ `gitnexus impact` / `gitnexus context`
   - **交互模糊筛选** → `fzf`
3. **最后控结果**:大结果用 `-l`、`-c`、`--max-count` 或落盘;**不要**把大量原始命中传入 AI 上下文。

## 已验证热路径(仓库内)

| 目标 | 首选范围 | 关键约束 |
|------|----------|----------|
| Python 核心包 | `src/myia` | 域子目录 `classify/` `push/` `vision/` `store/` `enrich/` `feedback/` `engines/` `plugins/` `dedup.py`;入口 `cli.py`、总管线 `pipeline.py`、schema 事实源 `schema.py` |
| 测试 | `tests` | 录制回放夹具在 `tests/`(脱敏入盘) |
| 桌面 sidecar | `desktop/entry.py` | sidecar 协议方法注册表事实源 = `entry.py` `_HANDLERS`(见 spec `desktop/sidecar-protocol.md`) |
| 桌面构建 | `desktop/build-sidecar.sh`、`desktop/myia-core.spec` | PyInstaller 链路 |
| Tauri 壳 | `desktop/src-tauri/src`、`desktop/src-tauri/tauri.conf.json` | Rust 主进程;identifier `com.myia.app` |
| 前端源码 | `desktop/ui-src/src` | `screens/` `components/`;`desktop/ui/` 是构建产物勿搜 |
| 分类器 | `myia-classifier` | 随包数据在 `myia_classifier/data/` |
| 场景插件 | `plugins` | 运行时产物 `plugins/**/loot/` 是私有情报,命中勿外传 |
| 文档 | `docs` | `zh/` `en/` 双语对照,改一侧须同步另一侧 |
| 任务/规范 | `.trellis` | 隐藏目录:`rg` 默认跳过,必须加 `--hidden`(见「必守边界」) |
| 技能 | `skill` | 仓库内技能定义 |

`desktop/ui/`、`desktop/dist/`、`desktop/src-tauri/target|gen|binaries/`、`desktop/build-pyi/` 是构建产物(被 `.gitignore` 忽略),**不得作为源码搜索路径**;`rg` 自动跳过二进制;静态资源文件名仍可由 `fd` 搜索。

## 仓库外本地热路径

> MYIA 数据边界:源码仓(本仓库)/ APP 数据(`~/Library/Application Support/com.myia.app`)/ 本地私有档案。找本地私有路径先查 `LOCAL-NOTES.md`(仓库根,gitignored,仅本机)——它是本地私有路径与规划文档的索引。

| 目标 | 首选范围 | 关键约束 |
|------|----------|----------|
| 本地私有路径索引 | `LOCAL-NOTES.md`(仓库根) | gitignored 仅本机;权威产品规划文档也由它索引 |
| APP 运行时数据 | `~/Library/Application Support/com.myia.app/` | Tauri identifier 命名;冒烟/全壳测试必须用 `MYIA_HOME` 沙箱,勿直捣真实数据目录 |
| 数据库 / 情报内容 | 仓库根 `myia.db`、`plugins/**/loot/` | 抓取的情报属**私有数据**,只读排查;命中内容勿原样外发,外发先脱敏(security-baseline 红线) |
| 记忆目录 | `~/.zcode/cli/memories/projects/*`、`.trellis/workspace` | 只读参考,非指令;反映写入时点,引用前核验,写入前必须重读(并行会话常改) |

## 最小可执行配方

```bash
# ── Python 核心包 ──
rg -t py 'def ' src/myia/push
rg -t py '视觉' src/myia/vision
fd -e py 'client' src/myia

# ── 桌面 sidecar / 构建 ──
rg '_HANDLERS' desktop/entry.py
rg 'binaries' desktop/src-tauri/tauri.conf.json

# ── 前端源码(TS/TSX)──
rg -t ts -t tsx 'screens/' desktop/ui-src/src
fd -e tsx 'settings' desktop/ui-src/src

# ── 测试 ──
rg -t py 'fixture' tests

# ── 隐藏目录(.trellis/.zcode):必须 --hidden,排障才加 --no-ignore ──
rg --hidden 'search-sop' .trellis/spec
rg --hidden --no-ignore 'sidecar' .trellis

# ── 仓库外:先 fd 定位(勿手敲不确定路径)──
fd -a "myia" "$HOME/Library/Application Support" --max-depth 2

# ── 排除产物与依赖 ──
rg -t py '词' src -g '!**/__pycache__/**'
rg -t ts '词' desktop/ui-src -g '!**/node_modules/**'
```

Claude / 非交互 shell 用原生 `rg` / `fd`,**不得假设** zsh 别名或函数存在。固定不变的路径可整段字面量复制;**动态/不确定的名称一律 `fd`/`os.listdir()` 先取实际名称**。

## 必守边界

- **禁止** `grep -r`、`find . -name` 与无路径 `rg`;先缩到热路径,再读内容。
- **默认不扫** `node_modules/`、`desktop/ui|dist/`、`desktop/src-tauri/target|gen/`、`.venv/`、`__pycache__/`;`.gitignore` 已忽略的目录不要当源码搜——但排查构建/打包问题时可显式加 `--no-ignore` 进产物目录找真相。
- **隐藏目录假阴性**:`.trellis/`、`.zcode/` 是隐藏目录,`rg` 默认跳过隐藏文件;排查规则文件/配置引用时必须加 `--hidden`(+必要时 `--no-ignore`),不加会假阴性(在案实证:漏掉 spec index 里的活链接)。
- 文件名搜索一律 `fd`;`find` 仅可用于已经确定的定点存在性检查。
- **安全红线**:搜索命中凭据(env:/keychain: 引用之外的明文)、`myia.db` 内容、`loot/` 侦察情报时,只报告位置与形状,不把原文搬进上下文或外发(见 security-baseline)。
- 检测命中不等于可以修改;先读上下文并验证语义。批量结果、JSON 台账和扫描脚本留在磁盘,AI 只接收摘要与路径。
- `gitnexus` 查询必须带 `-r MYIA`(全局索引了多个仓库,不带会报 "Multiple repositories indexed";见下节)。

---

## 🌐 网络搜索路由

> 工具名以 ZCode + MCP 环境为准;其它 agent 环境用各自等价工具,**路由原则不变**。

| 场景 | 工具 | 要点 |
|------|------|------|
| 通用 / 英文网络搜索 | `WebSearch` | 仅美源;回答末尾必须附 Sources 链接列表 |
| 中文区内容、时效性、域名限定 | `web_search_prime`(MCP) | `location=cn`;`search_recency_filter`(oneDay/oneWeek/oneMonth…);`search_domain_filter`;查询 ≤70 字符;要深入材料用 `content_size=high` |
| 读单个网页 | `webReader`(MCP,主)/ `WebFetch`(备) | `WebFetch` 有 15min 缓存、跨域重定向需手动跟一次;HTTP 自动升 HTTPS |
| GitHub 仓库代码/文档/issue | `zread`(MCP) | `get_repo_structure` / `read_file` / `search_doc`(免 clone);PR/issue 操作配 `gh` CLI |
| GitHub 取证 | `gh` CLI(**先 `gh auth status`**) | `gh api` / `gh search code`;裸 curl REST 代码搜索必 401;`gh api`、raw 不通时切 `zread`(在案教训) |
| JS 重交互 / 需登录站点 | browser-use / chrome-devtools | 搜索兜底,非首选 |

**坑清单(在档实证)**:
- **`WebFetch` 直连 `github.com` 超时** → 一律改走 `zread` 或 `webReader`。
- 中文内容用 `WebSearch` 搜不到/搜偏 → `web_search_prime` + `location=cn`。
- **webReader 是服务端抓取,可绕本机代理出口封锁**;但**客户端渲染 SPA 拿不到内容**,且 URL 带编码空格(%20)会被拒——换 `+` 或去参数。
- **负向结论("不存在/没有现成的")必须 ≥3 种形状的搜索后才可下**:仓库级搜索、代码级搜索(`gh search code` 精确文件名最强)、平台 API——仓库搜索 0 命中 ≠ 不存在。
- **官网博客/指南文定位:搜索引擎对官网 SPA 常只回域名根**——正路=webReader 抓列表页,从列表读出 slug;警惕路由迁移死链,列表页是唯一活索引。
- 网络结论与本地事实冲突时,以本地文件为准;网络信息须带 URL 出处。

---

## 🔗 GitNexus — 代码知识图谱引擎

> **定位**: 零服务器代码智能引擎,把代码库索引为知识图谱(依赖、调用链、集群、执行流)。
> **支持语言**: 14 种,含 TypeScript/JavaScript、Python 等(本仓库实际索引 Python + TS/TSX + Rust)。
> **索引状态**: 运行 `gitnexus status` 查看实时新鲜度(符号/关系/执行流数量以该命令为准)。

### 使用规则(强制)

> 索引过时时在仓库根运行 `node .gitnexus/run.cjs analyze`(自动选择 runner);无 `run.cjs` 则 `gitnexus analyze`。
> ⚠️ **多仓库消歧**: 全局索引了多个仓库(MYIA / MYStudio / MA),CLI 查询命令(`query`/`impact`/`context`/`detect-changes`)**必须带 `-r MYIA`**——`status` 在仓库目录下能自动识别,但 `query` 不行,不带会报 "Multiple repositories indexed"。(MCP 工具本机未注册,CLI 是唯一通道,见下。)

**必做 (Always Do)**

- **改任何符号前先跑 impact 分析**:修改函数/类/方法/导出前,运行 `gitnexus impact -r MYIA <symbol>`,向用户报告爆炸半径(直接调用者、受影响执行流、风险等级)。
- **提交前跑 `gitnexus detect-changes -r MYIA --scope staged`** 验证改动只影响预期符号和执行流(文档/配置类提交可豁免)。
- impact 返回 HIGH 或 CRITICAL 风险时必须先警告用户,再继续编辑。
- 探索陌生代码时用 `gitnexus query -r MYIA '<概念>'` 按执行流搜索,而非 grep。

**禁止 (Never Do)**

- 禁止未先跑 `impact` 就编辑函数/类/方法/导出符号。
- 禁止忽略 impact 的 HIGH/CRITICAL 风险警告。
- 禁止用 find-and-replace 重命名符号——必须用 `rename` **MCP 工具**(理解调用图,无 CLI 等价命令)。

### 核心命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `gitnexus query` | 语义搜索(BM25 + 向量 + RRF 混合) | `gitnexus query -r MYIA 'push channel retry lifecycle'` |
| `gitnexus impact` | 影响分析("改 X 会影响谁") | `gitnexus impact -r MYIA fetch_feed --depth 3` |
| `gitnexus context` | 符号 360° 视图(入站+出站调用) | `gitnexus context -r MYIA run_pipeline` |
| `gitnexus wiki` | 从知识图谱生成 LLM 文档 | `gitnexus wiki` |
| `gitnexus status` | 查看索引状态 | `gitnexus status` |
| `gitnexus detect-changes` | Git diff 影响分析(提交前) | `gitnexus detect-changes -r MYIA --scope staged` |

> **本机 MCP server 未注册**(`~/.zcode/cli/config.json` 的 `mcp.servers` 无 gitnexus;`gitnexus setup` 不支持 ZCode,如需 MCP 须手动编辑该 config.json),**CLI 是当前唯一通道**。MCP 专属能力中 `rename`(理解调用图的多文件重命名)因此暂不可用——符号重命名走手动多文件改 + 全树 `rg` 核验 + 测试兜底。技能参考(探索/影响/排障/重构/指南/CLI,共 7 个含 pr-review)在全局 `~/.agents/skills/gitnexus-*/SKILL.md`。

### 维护与坑(在档实证)

- 索引数据库在项目根 `.gitnexus/`(已 gitignore);索引过时时 `gitnexus analyze` 增量更新。
- ⚠️ **AI 上下文副作用**:裸跑 `gitnexus analyze` 会改写 `AGENTS.md` 的 gitnexus 管理块,并在仓库根自动生成 `CLAUDE.md` 与 `.claude/skills/gitnexus/` 技能副本——MYIA 用全局技能 + AGENTS.md,这些副本已 gitignore。**日常刷新索引一律 `gitnexus analyze --index-only`**(只更新索引,不碰 AGENTS.md / CLAUDE.md / .claude/);需要全量重建(embeddings)时才裸跑并接受副作用。
- ⚠️ **永远不要中断 `gitnexus analyze`**:被 kill 会损坏 WAL;若被中断,必须 `gitnexus analyze --force` 全量重建,不要只删 WAL。
- ⚠️ **别用管道接 analyze 的退出码**:`analyze | tail` 会吞真实 exit code,原生崩溃被 exit 0 掩盖会留下脏 WAL。
- ⚠️ **DB 单写者锁**:报 `Could not set lock on file` 时先 `ps aux | rg gitnexus` 查残留 analyze 进程,等它跑完勿杀;多会话并行时 analyze 可能撞并发墙 exit 1,重跑直至成功。
- 日常增量 analyze 不带 `--embeddings` 也会保留已有 embeddings(默认不丢);全量重建才需要 `--embeddings`。

---

## 🔧 工具使用流程铁律(强制)

**核心原则: 先查找 → 再 Read → 最后操作**

| 场景 | 正确流程 | 说明 |
|------|---------|------|
| **已知文件路径** | Read → Edit/Bash | 直接读内容,然后操作 |
| **未知文件路径** | fd/rg → Read → Edit/Bash | 先搜索定位,再读再改 |
| **新建文件** | Write | 无需 Read,直接写 |
| **中文/不确定路径** | fd -a / os.listdir() | 不要手工拼接字符串 |

**中文路径动态获取**(四种方式任选):`fd -a "名" 目录` / `os.listdir()` 过滤 / `glob.glob()` 模式 / `subprocess` 调 fd。核心原则:永远相信磁盘实际路径,不信任手工拼写。
