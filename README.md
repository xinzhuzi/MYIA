<div align="center">

<img src="desktop/branding/shishi-icon-1024.png" width="160" alt="世事 — 眼睛与雷达虹膜" />

# 世事

**AI 原生情报中枢 · AI-native intelligence hub**

**说需求,AI 做其余。** · Say what you want — AI does the rest.

<p>
<a href="https://github.com/xinzhuzi/shishi/actions/workflows/ci.yml"><img src="https://github.com/xinzhuzi/shishi/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
<img src="https://img.shields.io/badge/tests-1300%2B%20passing-2EA44F" alt="tests: 1300+ passing" />
<img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="python 3.11+" />
<img src="https://img.shields.io/badge/license-MIT-3DA639" alt="MIT license" />
<img src="https://img.shields.io/badge/status-0.0.1%20first%20release-2EA44F" alt="status: v0.0.1 first release" />
</p>
<p>
<img src="https://img.shields.io/badge/fetch%20engines-6%20(L1%E2%80%93L6)-22D3EE" alt="6 fetch engines, L1–L6" />
<img src="https://img.shields.io/badge/push-Feishu%20%C2%B7%20Telegram%20%C2%B7%20webhook%20%C2%B7%20stdout-8B5CF6" alt="push: Feishu · Telegram · webhook · stdout" />
<img src="https://img.shields.io/badge/weight-SQLite%20single%20file%20%C2%B7%20no%20daemon-64748B" alt="SQLite single file, no daemon" />
</p>

<img src="docs/demo/assets/shishi-demo.gif" width="800" alt="世事 demo:一个 YAML → 情报推送(shishi run → 收到推送卡片)" />

<sub>世事 三分钟 —— 一个 YAML → 情报推送。录制脚本与分镜:<a href="docs/demo/"><code>docs/demo/</code></a></sub>

<br/>
<br/>

<code>fetch</code> ➜ <code>classify</code> ➜ <code>dedup</code> ➜ <code>analyze</code> ➜ <code>enrich</code> ➜ <code>push</code>

<br/>
<br/>

**<a href="#中文">中文</a>** · **<a href="#english">English</a>**

</div>

---

<a id="中文"></a>

## 理念

所有情报需求 —— AI 资讯、股票异动、补货监控、显卡行情、羊毛优惠 —— 形状都一样:
**盯住一些源,留下重要的,忽略其余的,该叫你的时候叫你。** 现有工具各给一片:
一个爬虫、一个订阅源、一个 diff 监视器。世事 给的是全链路,配置驱动,并且天生
就是给 AI agent 开的。

任何情报品类 = **一份 YAML 文件**。世事 负责抓取(六级引擎自动降级)、分类去重、
打分(先关键词粗筛 —— 零 token;可选 LLM 精评),把重要的推到你的即时通讯。
YAML 本身由你的编码 agent 照规范现场生成:读 schema、写配置、`shishi test` 试抓
验证、凭 `shishi doctor` 输出自行修复失效源。

**人做决策,AI 做其余。**

## 市面空白:为什么是 世事

| 现有工具 | 给你什么 | 缺什么 |
|---|---|---|
| Crawlab / Kestra | 爬虫/工作流编排 | 只是载具 —— 没有分类、去重、推送 |
| changedetection.io | 网站变更监控 | 只盯 diff —— 没有采集管线、分析与打分 |
| RSSHub | 把站点转成 RSS | 只做源转换 —— 没有过滤、打分与投递 |
| 单一用途盯盘工具(凭证/羊毛各一款) | 各覆盖一个细分品类 | 品类写死;换目标 = 换工具 |
| **世事** | **品类无关的采集→分类→分析→推送全链,配置驱动** | — |

## 核心亮点

<table>
<tr>
<td width="50%" valign="top">

🧠 **是情报,不只是爬虫**
七大类关键词分类器(零 token,以独立包
[`shishi-classifier`](shishi-classifier/) 发行)+ 可选 LLM 精评(价值/相关性/
可信度 0–10)。阈值分级路由:score ≥ 8 立即推,≥ 5 进早晚摘要,其余归档。

</td>
<td width="50%" valign="top">

🪜 **六级采集降级梯**
`direct_api` → `static_html` → `crawl4ai` / `firecrawl` → `Scrapling` →
隐身浏览器 → LLM 浏览器。源永不写死引擎:某一级失灵,下一级顶上,胜出引擎
按源记忆(存 SQLite —— 永不回写你的 YAML)。

</td>
</tr>
<tr>
<td width="50%" valign="top">

📬 **推送尊重你的注意力**
URL 键去重注册表 + 早/晚摘要槽位 —— 生产验证过的语义;同一条情报你永远不会
收到第二遍。通道:飞书卡片、Telegram、webhook、stdout。

</td>
<td width="50%" valign="top">

🔐 **凭据永不落明文**
凭据永不进 YAML —— 只允许 `env:VAR` / `keychain:myia/<scope>/<name>` 引用;
配置文件里出现明文凭据,加载即拒。落 macOS Keychain / Windows DPAPI。

</td>
</tr>
<tr>
<td width="50%" valign="top">

🤖 **天生 Agent 友好**
全量文档化的 12 节 YAML schema、处处有缺省值、每条命令都有 `--json`
(stdout 恒为恰好一份 JSON 文档)、结构化诊断 —— 每个接口都为 agent 可驱动
而设计,不只是给人用的。

</td>
<td width="50%" valign="top">

🔁 **反馈闭环自我调优**
对推送标记有价值 / 无价值(CLI 现已可用;桌面卡片内按钮已随桌面对齐批次
落地 main、随下个发布版交付;Telegram/飞书回调接收已就绪)—— 负反馈持续
回写,调优盯盘权重与阈值。

</td>
</tr>
</table>

开箱即用:SQLite 单文件存储(无 Redis、无 Postgres、无常驻守护),保留期
自动清理 + 定期 VACUUM,进程内调度。

## 快速开始

```bash
git clone https://github.com/xinzhuzi/shishi
cd shishi
uv sync                     # uv workspace(主口径):一并装好 shishi 与 shishi-classifier
uv run shishi --version       # shishi 0.0.1
```

> 本仓是 uv workspace(`shishi-classifier` 是 workspace 成员):裸 `pip install -e .`
> 装不齐依赖,源码安装请用 `uv sync`;`pip install shishi` 待 PyPI 上架后可用。

重型采集引擎是可选 extras;缺引擎时沿梯子优雅降级,给出结构化
`dependency_missing` 错误而不是崩溃:

```bash
uv sync --extra crawl4ai    # L3 JS 渲染引擎
uv sync --extra llm         # LLM 精评 / 事件聚合
```

跑第一个品类 —— 零凭据的完整配置就一个小文件:

```bash
cat > plugins/demo-min.yaml <<'YAML'
id: demo-min
name: Minimal demo
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
classify:
  builtin: false                 # demo items match no category; disable the filter
push:
  - channel: stdout              # zero-credential local verification
YAML

uv run shishi test plugins/demo-min.yaml --json            # 试抓:不入库、不推送
uv run shishi run plugins/demo-min.yaml --dry-run --json   # 全链演练,不推送
uv run shishi run plugins/demo-min.yaml                    # 正式跑;--loop 常驻调度
```

完整走读:[docs/zh/getting-started.md](docs/zh/getting-started.md)。

> PyPI 包(`shishi`、`shishi-classifier`)将走手动发布流程;在那之前请如上从源码安装,
> 桌面用户可直接用下方安装包。

## 下载安装(桌面应用)

macOS(Apple Silicon)安装包随 GitHub Releases 发布:

1. 从 [Releases](https://github.com/xinzhuzi/shishi/releases) 下载
   `shishi_<版本>_aarch64.dmg`(版本号随发布更替,以 Releases 页面实际资产为准),
   把 世事 拖入「应用程序」;
2. 首次打开:**在「应用程序」里右键 世事 →「打开」→ 再点「打开」**
   (或双击被拦后到 系统设置 → 隐私与安全性 → 点「仍要打开」);
3. 安装包未做 Apple 公证(公证需付费开发者账号)—— 代码完全开源可审计,
   每个安装包由 GitHub Actions 公开构建、日志可溯;右键打开一次即完成
   Gatekeeper 放行,之后正常双击启动。

装机首跑自动种子官方插件(含零凭据演示件 `myia-demo`:GitHub 新星榜),
第一次点「运行第一个插件」就出真数据;设置页「检查更新」走签名更新通道。

桌面五屏(截图为 demo 插件真实抓取数据):

<p align="center">
<img src="docs/screenshots/dashboard.png" width="400" alt="仪表盘:插件运行状态与最近采集概览" />
<img src="docs/screenshots/feed.png" width="400" alt="信息流:去重后的情报条目" />
</p>
<p align="center">
<img src="docs/screenshots/sources.png" width="260" alt="源管理:插件与源配置" />
<img src="docs/screenshots/logs.png" width="260" alt="日志:运行日志回溯" />
<img src="docs/screenshots/settings.png" width="260" alt="设置:凭据入钥匙链 + 软件更新" />
</p>

## AI 原生闭环

把闭环交给任何编码 agent(Claude Code、Cursor……)。
[Agent Skill](skill/SKILL.md) 是自包含速查表,一条命令安装
(`shishi skill install --agent claude`;也支持 `cursor` / `zcode`,
`--path` 自定义目录,`--link` 以链接代替复制):

```
shishi skill install --agent claude         # 一次性:速查表 → ~/.claude/skills/shishi/
shishi init --json                          # 拿结构化信息清单
( agent 现场写 <id>.yaml )                # 对照 12 节 schema
shishi test plugins/<id>.yaml --json        # 试抓,核对字段与去重键
shishi run plugins/<id>.yaml --dry-run      # 演练
shishi run plugins/<id>.yaml --loop         # 常驻调度
shishi doctor --json                        # 拿 findings;agent 自修后复查
```

## 架构

```
用户层          shishi CLI · Agent Skill · 桌面应用(Tauri,v1.1)· Web UI(规划中)
                     │
编排层          流水线:fetch → classify → dedup → analyze → enrich → push
                (进程内 APScheduler + asyncio;无外部编排器、无守护进程)
                     │
插件层          一个品类一份 YAML · 6 个官方品类 · 市场插件
                desktop 级 = 进程内 adapter(零 docker)· remote/server 级走端点
                (代理池 · 变更监控 · OSINT · 抖音 · maxun · …)
                     │
采集引擎        L1 direct_api → L2 static_html → L3 crawl4ai ⇄ firecrawl
                → L4 scrapling → L5 stealth_browser → L6 llm_browser
                (自动降级链;胜出引擎按源持久化)
                     │
分析            内置七类关键词分类器(shishi-classifier)
                + 可选 LLM 精评(价值/相关性/可信度,0–10)
                     │
存储            SQLite 单文件 · 保留期 + VACUUM · 变更基线
                     │
推送            飞书卡片 · Telegram · webhook · stdout,阈值分级路由
                (立即 / 早晚摘要 / 归档)+ 反馈闭环
```

## 文档

双语文档随仓库发行,由测试(`tests/test_docs.py`)锁住与代码一致 —— 每个
示例 YAML 都过真实 schema 入口加载,zh/en 两棵树结构上不许漂移:

| | English | 中文 |
|---|---|---|
| Getting started | [docs/en/getting-started.md](docs/en/getting-started.md) | [docs/zh/getting-started.md](docs/zh/getting-started.md) |
| Write a plugin | [docs/en/write-a-plugin.md](docs/en/write-a-plugin.md) | [docs/zh/write-a-plugin.md](docs/zh/write-a-plugin.md) |
| Schema reference | [docs/en/schema.md](docs/en/schema.md) | [docs/zh/schema.md](docs/zh/schema.md) |
| FAQ(伦理与边界) | [docs/en/faq.md](docs/en/faq.md) | [docs/zh/faq.md](docs/zh/faq.md) |

面向 agent 的浓缩参考:[`skill/SKILL.md`](skill/SKILL.md)。

## 路线图

核心流水线已实现且有测试覆盖 —— 1300+ 测试跑在 CI 里,无一条碰真实网络。
桌面端已可日常使用(数据通路统一、官方插件随包、开箱 demo、签名更新通道,
v0.0.1 起随 Releases 交付);桌面卡片内反馈按钮、设置反馈开关、采集量趋势
已随桌面对齐批次落地 main、随下个发布版交付(反馈闭环 CLI 现已可用);
Windows 构建这版未通过,Release 暂无 Windows 安装包(后续批次计划补上)。

| 里程碑 | 范围 | 状态 |
|---|---|---|
| v0.1 骨架 | 核心流水线、direct_api/static/firecrawl 引擎、12 节 schema、变更指纹、分类器、飞书路由 | ✅ 已交付 |
| v0.2 可用 | SQLite 存储 + 注册表 + 保留期、完整 CLI(init/test/list/doctor)、crawl4ai L3、LLM 精评 + 预算护栏、docker compose、Telegram、钥匙链凭据 | ✅ 已交付 |
| v0.3 生态 | Agent Skill、插件市场(本地/远端双模)、Scrapling L4、反馈闭环(CLI + 回调接收) | ✅ 已交付 |
| v0.4 深水区 | stealth_browser L5、llm_browser L6、趋势基线、事件聚合 | ✅ 已交付 |
| v1.0 发布 | 双语文档、演示物料、GitHub 门面、公开交付 | ✅ 已交付 |
| v1.1 桌面优先 | Tauri 桌面壳(Python 核心以 sidecar 嵌入)、五屏 UI、进程内插件级;卡片内反馈按钮/settings 反馈开关/采集量趋势已随桌面对齐批次落地、随下个发布版交付(反馈 CLI 已可用) | ✅ 已交付 |
| v1.1.1 通路修复 | 桌面数据通路统一(MYIA_HOME/官方插件随包/首跑种子)、开箱 demo 插件、签名更新通道(检查更新 + 自动安装) | ✅ 已交付 |
| Web UI | 同一核心上的浏览器前端 | 📋 规划中 |

## 伦理与边界

世事 默认讲礼貌:尊重 robots.txt、限速采集、凭据永不落明文。要求真人验证
(验证码、手机号)的源会以结构化错误失败 —— 世事 不做绕过。完整表述见
[中文 FAQ](docs/zh/faq.md) · [English FAQ](docs/en/faq.md)。

## 社区

- Bug 与需求:[issue 模板](.github/ISSUE_TEMPLATE/)
- 参与贡献:[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略与凭据处理设计:[SECURITY.md](SECURITY.md)

## 致谢

世事 自有代码全部原创(MIT),但站在巨人的肩膀上 —— 以依赖、插件后端与设计
参考的形式接入:

- [crawl4ai](https://github.com/unclecode/crawl4ai) —— L3 采集引擎(可选依赖)
- [Scrapling](https://github.com/D4Vinci/Scrapling) —— L4 自适应反爬引擎(可选依赖)
- [Firecrawl](https://github.com/firecrawl/firecrawl) —— L3 云端/自建渲染后端(可选依赖,以 API 调用)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) —— L6 LLM 浏览器兜底(可选依赖)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) —— 源管理与 diff 交互参考,`shishi-monitor` 插件后端
- [RSSHub](https://github.com/DIYgod/RSSHub) —— 「一切皆源」的哲学参考
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) —— `shishi-proxy` 插件后端
- [Photon](https://github.com/s0md3v/Photon) —— `shishi-osint` 插件后端(以 git 子模块引入)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— `shishi-douyin` 插件后端
- [Maxun](https://github.com/getmaxun/maxun) —— `shishi-maxun` 插件后端
- [Tauri](https://github.com/tauri-apps/tauri) —— 桌面壳(Python 核心以 sidecar 嵌入)

除明确标注的 git 子模块外,不复制任何上游源码进本仓库;依赖接入策略见
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE) © 2026 xinzhuzi

---

<a id="english"></a>

<div align="center">

## English

**AI-native intelligence hub — say what you want, AI does the rest.**

</div>

### The idea

Every intelligence need — AI news, stock moves, restocks, GPU prices,
freebies and deals — has the same shape: **watch some sources, keep what
matters, ignore the rest, tell me when it counts.** Existing tools each give
you one slice: a crawler, a feed, a diff watcher. 世事 is the whole chain,
config-driven, and built to be driven by your AI agent.

Describe any category as **one YAML file**. 世事 fetches it (six engines on
an auto-degrading ladder), classifies and dedups it, scores it (keywords
first — zero tokens; optional LLM for precision), and pushes what matters to
your messaging apps. The YAML itself is written by your coding agent: it
reads the schema, generates the config, trial-fetches with `shishi test`, and
repairs broken sources on its own from `shishi doctor` output.

Weixin outbound is a bridge via a local Hermes-Agent install — without one,
the weixin channel is unavailable (MYIA itself holds zero WeChat credentials).

**Humans decide; AI does the rest.**

### Why 世事

| Existing tools | What they give you | What they miss |
|---|---|---|
| Crawlab / Kestra | crawler & workflow orchestration | a vehicle only — no classification, dedup or push |
| changedetection.io | website change monitoring | watches diffs — no collection pipeline, no analysis, no LLM scoring |
| RSSHub | turns sites into RSS feeds | source conversion only — no filtering, scoring or delivery |
| Single-purpose watchers (credential / deal trackers) | one niche category each | category-locked; every new target means new tooling |
| **世事** | **category-agnostic fetch → classify → analyze → push, all config-driven** | — |

### Highlights

<table>
<tr>
<td width="50%" valign="top">

🧠 **Intelligence, not just crawling**
A seven-category keyword classifier (zero tokens, shipped as the standalone
[`shishi-classifier`](shishi-classifier/) package) plus optional LLM enrichment
scoring value / relevance / credibility 0–10. Thresholds route the result:
score ≥ 8 pushes immediately, ≥ 5 waits for the AM/PM digest, the rest is
archived.

</td>
<td width="50%" valign="top">

🪜 **A six-engine fetch ladder**
`direct_api` → `static_html` → `crawl4ai` / `firecrawl` → `Scrapling` →
stealth browser → LLM browser. Sources never hard-code an engine: when one
rung fails, the next takes over, and the winning engine is remembered per
source (in SQLite — never written back into your YAML).

</td>
</tr>
<tr>
<td width="50%" valign="top">

📬 **Push that respects your attention**
URL-key dedup registry + AM/PM digest slots — production-proven semantics;
you never receive the same item twice. Feishu card, Telegram, webhook and
stdout channels.

</td>
<td width="50%" valign="top">

🔐 **Secrets stay secret**
Credentials never live in YAML — only `env:VAR` / `keychain:myia/<scope>/<name>`
references. A plaintext credential in a config file is rejected at load time.
macOS Keychain / Windows DPAPI backed.

</td>
</tr>
<tr>
<td width="50%" valign="top">

🤖 **Agent-native by design**
A fully documented 12-section YAML schema with defaults everywhere,
`--json` on every command (stdout is always exactly one JSON document),
structured diagnostics. Every interface is built so an agent can drive it —
not just a human.

</td>
<td width="50%" valign="top">

🔁 **A feedback loop that tunes itself**
Mark pushed items valuable / not valuable (CLI today; in-card desktop
buttons have landed on main in the desktop-parity batch and ship with the
next release; Telegram/Feishu callback receivers are ready) —
negative feedback retunes watchlist weights and thresholds over time.

</td>
</tr>
</table>

Batteries included: SQLite single-file storage (no Redis, no Postgres, no
daemon), retention + auto-VACUUM, in-process scheduling.

### Quickstart

```bash
git clone https://github.com/xinzhuzi/shishi
cd shishi
uv sync                     # uv workspace (primary): installs shishi + shishi-classifier
uv run shishi --version       # shishi 0.0.1
```

> This repo is a uv workspace (`shishi-classifier` is a workspace member): a bare
> `pip install -e .` won't pull it in — install from source with `uv sync`;
> `pip install shishi` becomes available once the package lands on PyPI.

Heavy fetch engines are optional extras; a missing engine degrades gracefully
down the ladder with a structured `dependency_missing` error instead of
crashing:

```bash
uv sync --extra crawl4ai    # L3 JS-rendered page engine
uv sync --extra llm         # LLM enrich scoring / event aggregation
```

Run your first category — a complete, zero-credential config in one small
file:

```bash
cat > plugins/demo-min.yaml <<'YAML'
id: demo-min
name: Minimal demo
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
classify:
  builtin: false                 # demo items match no category; disable the filter
push:
  - channel: stdout              # zero-credential local verification
YAML

uv run shishi test plugins/demo-min.yaml --json            # trial fetch: no DB, no push
uv run shishi run plugins/demo-min.yaml --dry-run --json   # full rehearsal, no push
uv run shishi run plugins/demo-min.yaml                    # real run; add --loop for scheduling
```

Full walk-through: [docs/en/getting-started.md](docs/en/getting-started.md).

> PyPI packages (`shishi`, `shishi-classifier`) will publish via a manual release
> workflow; until then, install from source as above — or grab the desktop
> installer below.

### Download & install (desktop app)

The macOS (Apple Silicon) installer ships via GitHub Releases:

1. Download `shishi_<version>_aarch64.dmg` from
   [Releases](https://github.com/xinzhuzi/shishi/releases) (the version token
   rotates per release — the Releases page is authoritative) and drag 世事 into
   Applications;
2. On first launch: **right-click 世事 in Applications → Open → Open**
   (or, after a blocked double-click: System Settings → Privacy & Security →
   Open Anyway);
3. The package is not Apple-notarized (notarization requires a paid developer
   account) — the code is fully open-source and auditable, and every installer
   is built in public by GitHub Actions with traceable logs. One right-click
   open clears Gatekeeper; subsequent launches open normally.

A fresh install auto-seeds the official plugins (including the
zero-credential demo `myia-demo`: GitHub's new-star board), so the first click
of "run your first plugin" shows real data; the settings screen offers
"Check for updates" over a signed update channel.

The five desktop screens (fed by real demo-plugin data):

<p align="center">
<img src="docs/screenshots/dashboard.png" width="400" alt="Dashboard: plugin run status and recent collection overview" />
<img src="docs/screenshots/feed.png" width="400" alt="Feed: deduplicated intelligence items" />
</p>
<p align="center">
<img src="docs/screenshots/sources.png" width="260" alt="Sources: plugin and source configuration" />
<img src="docs/screenshots/logs.png" width="260" alt="Logs: run log tail" />
<img src="docs/screenshots/settings.png" width="260" alt="Settings: credentials into the keychain + software update" />
</p>

### The AI-native loop

Hand the loop to any coding agent (Claude Code, Cursor, …). The
[Agent Skill](skill/SKILL.md) is a self-contained cheat sheet, installed with
one command (`shishi skill install --agent claude`; also `cursor` / `zcode`,
`--path` for a custom dir, `--link` to symlink instead of copy):

```
shishi skill install --agent claude         # one-time: skill sheet → ~/.claude/skills/shishi/
shishi init --json                          # structured checklist of what to collect
( agent writes <id>.yaml )                # against the 12-section schema
shishi test plugins/<id>.yaml --json        # trial fetch, inspect fields + dedup keys
shishi run plugins/<id>.yaml --dry-run      # rehearsal
shishi run plugins/<id>.yaml --loop         # scheduled operation
shishi doctor --json                        # findings; the agent repairs and re-checks
```

### Architecture

```
User layer      shishi CLI · Agent Skill · desktop app (Tauri, v1.1) · Web UI (planned)
                     │
Orchestration   Pipeline: fetch → classify → dedup → analyze → enrich → push
                (in-process APScheduler + asyncio; no external orchestrator, no daemon)
                     │
Plugin layer     One YAML per category · 6 official categories · market plugins
                desktop tier = in-process adapter (zero docker) · remote/server tier
                (proxy pool · changedetection · OSINT · douyin · maxun · …)
                     │
Fetch engines   L1 direct_api → L2 static_html → L3 crawl4ai ⇄ firecrawl
                → L4 scrapling → L5 stealth_browser → L6 llm_browser
                (auto degrade chain; winning engine persisted per source)
                     │
Analysis        builtin 7-category keyword classifier (shishi-classifier)
                + optional LLM enrich (value / relevance / credibility, 0–10)
                     │
Storage         SQLite single file · retention + VACUUM · change baselines
                     │
Push            Feishu card · Telegram · webhook · stdout, threshold-routed
                (immediate / digest AM-PM / archive) + feedback loop
```

### Documentation

Bilingual docs ship in-repo, kept consistent with the code by tests
(`tests/test_docs.py`) — every example YAML loads through the real schema
entry point, and zh/en trees cannot drift apart:

| | English | 中文 |
|---|---|---|
| Getting started | [docs/en/getting-started.md](docs/en/getting-started.md) | [docs/zh/getting-started.md](docs/zh/getting-started.md) |
| Write a plugin | [docs/en/write-a-plugin.md](docs/en/write-a-plugin.md) | [docs/zh/write-a-plugin.md](docs/zh/write-a-plugin.md) |
| Schema reference | [docs/en/schema.md](docs/en/schema.md) | [docs/zh/schema.md](docs/zh/schema.md) |
| FAQ (ethics & boundaries) | [docs/en/faq.md](docs/en/faq.md) | [docs/zh/faq.md](docs/zh/faq.md) |

Agent-facing condensed reference: [`skill/SKILL.md`](skill/SKILL.md).

### Roadmap

The core pipeline is implemented and tested — 1300+ tests run in CI, none of
them touch the real network. The desktop app is ready for daily use
(unified data paths, bundled official plugins, an out-of-the-box demo, a
signed update channel — shipping in Releases since v0.0.1); in-card
feedback buttons, the settings feedback toggle and collection trends have
landed on main in the desktop-parity batch and ship with the next release
(the feedback loop already works via CLI); the Windows build did not ship
in this release (no Windows installer in Releases yet; planned for a
follow-up batch).

| Milestone | Scope | Status |
|---|---|---|
| v0.1 skeleton | Core pipeline, direct_api/static/firecrawl engines, 12-section schema, change fingerprint, classifier, Feishu routing | ✅ shipped |
| v0.2 usable | SQLite store + registry + retention, full CLI (init/test/list/doctor), crawl4ai L3, LLM enrich + budget guardrails, docker compose, Telegram, keychain secrets | ✅ shipped |
| v0.3 ecosystem | Agent Skill, plugin market (local/remote dual-mode), Scrapling L4, feedback loop (CLI + callback receivers) | ✅ shipped |
| v0.4 deep water | stealth_browser L5, llm_browser L6, trend baselines, event aggregation | ✅ shipped |
| v1.0 launch | Bilingual docs, demo assets, GitHub facade, public delivery | ✅ shipped |
| v1.1 desktop-first | Tauri desktop shell (Python core as sidecar), five-screen UI, in-process plugin tier; in-card feedback buttons / settings feedback toggle / collection trends have landed in the desktop-parity batch and ship with the next release (feedback works via CLI today) | ✅ shipped |
| v1.1.1 data paths | Desktop data-path unification (MYIA_HOME / bundled official plugins / first-run seed), out-of-the-box demo plugin, signed update channel (check + install) | ✅ shipped |
| Web UI | browser front-end on the same core | 📋 planned |

### Ethics & boundaries

世事 is polite by default: robots.txt respected, rate-limited fetching,
credentials never in plaintext. Sources that demand human verification
(CAPTCHA, phone numbers) fail with a structured error — 世事 does not attempt
to bypass them. Full statement in the
[FAQ](docs/en/faq.md) · [中文 FAQ](docs/zh/faq.md).

### Community

- Bug reports & feature requests: [issue templates](.github/ISSUE_TEMPLATE/)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy & credential-handling design: [SECURITY.md](SECURITY.md)

### Acknowledgments

世事's own code is original (MIT), but it stands on giants — consumed as
dependencies, plugin backends and design references:

- [crawl4ai](https://github.com/unclecode/crawl4ai) — L3 fetch engine (optional dependency)
- [Scrapling](https://github.com/D4Vinci/Scrapling) — L4 adaptive anti-bot engine (optional dependency)
- [Firecrawl](https://github.com/firecrawl/firecrawl) — L3 cloud/self-hosted rendering backend (optional dependency, called as an API)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) — L6 LLM-browser fallback (optional dependency)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) — source-management & diff UX reference; `shishi-monitor` plugin backend
- [RSSHub](https://github.com/DIYgod/RSSHub) — the "everything is a feed" philosophy
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — `shishi-proxy` plugin backend
- [Photon](https://github.com/s0md3v/Photon) — `shishi-osint` plugin backend (vendored via git submodule)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) — `shishi-douyin` plugin backend
- [Maxun](https://github.com/getmaxun/maxun) — `shishi-maxun` plugin backend
- [Tauri](https://github.com/tauri-apps/tauri) — desktop shell (Python core embedded as a sidecar)

No upstream source is copied into this repository except clearly-marked git
submodules; dependency policy in [CONTRIBUTING.md](CONTRIBUTING.md).

### License

[MIT](LICENSE) © 2026 xinzhuzi
