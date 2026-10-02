<div align="center">

<img src="desktop/branding/myia-icon-1024.png" width="160" alt="MYIA — 眼睛与雷达虹膜" />

# MYIA

**AI 原生情报中枢 · AI-native intelligence hub**

**说需求,AI 做其余。** · Say what you want — AI does the rest.

<p>
<a href="https://github.com/xinzhuzi/MYIA/actions/workflows/ci.yml"><img src="https://github.com/xinzhuzi/MYIA/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
<img src="https://img.shields.io/badge/tests-1300%2B%20passing-2EA44F" alt="tests: 1300+ passing" />
<img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="python 3.11+" />
<img src="https://img.shields.io/badge/license-MIT-3DA639" alt="MIT license" />
<img src="https://img.shields.io/badge/status-alpha-orange" alt="status: alpha" />
</p>
<p>
<img src="https://img.shields.io/badge/fetch%20engines-6%20(L1%E2%80%93L6)-22D3EE" alt="6 fetch engines, L1–L6" />
<img src="https://img.shields.io/badge/push-Feishu%20%C2%B7%20Telegram%20%C2%B7%20webhook%20%C2%B7%20stdout-8B5CF6" alt="push: Feishu · Telegram · webhook · stdout" />
<img src="https://img.shields.io/badge/weight-SQLite%20single%20file%20%C2%B7%20no%20daemon-64748B" alt="SQLite single file, no daemon" />
</p>

<img src="docs/demo/assets/myia-demo.gif" width="800" alt="MYIA demo:一个 YAML → 情报推送(myia run → 收到推送卡片)" />

<sub>MYIA 三分钟 —— 一个 YAML → 情报推送。录制脚本与分镜:<a href="docs/demo/"><code>docs/demo/</code></a></sub>

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
一个爬虫、一个订阅源、一个 diff 监视器。MYIA 给的是全链路,配置驱动,并且天生
就是给 AI agent 开的。

任何情报品类 = **一份 YAML 文件**。MYIA 负责抓取(六级引擎自动降级)、分类去重、
打分(先关键词粗筛 —— 零 token;可选 LLM 精评),把重要的推到你的即时通讯。
YAML 本身由你的编码 agent 照规范现场生成:读 schema、写配置、`myia test` 试抓
验证、凭 `myia doctor` 输出自行修复失效源。

**人做决策,AI 做其余。**

## 市面空白:为什么是 MYIA

| 现有工具 | 给你什么 | 缺什么 |
|---|---|---|
| Crawlab / Kestra | 爬虫/工作流编排 | 只是载具 —— 没有分类、去重、推送 |
| changedetection.io | 网站变更监控 | 只盯 diff —— 没有采集管线、分析与打分 |
| RSSHub | 把站点转成 RSS | 只做源转换 —— 没有过滤、打分与投递 |
| 单一用途盯盘工具(凭证/羊毛各一款) | 各覆盖一个细分品类 | 品类写死;换目标 = 换工具 |
| **MYIA** | **品类无关的采集→分类→分析→推送全链,配置驱动** | — |

## 核心亮点

<table>
<tr>
<td width="50%" valign="top">

🧠 **是情报,不只是爬虫**
七大类关键词分类器(零 token,以独立包
[`myia-classifier`](myia-classifier/) 发行)+ 可选 LLM 精评(价值/相关性/
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
对推送标记有价值 / 无价值(CLI 现已可用;Telegram/飞书回调接收已就绪,
卡片内按钮随桌面版交付)—— 负反馈持续回写,调优盯盘权重与阈值。

</td>
</tr>
</table>

开箱即用:SQLite 单文件存储(无 Redis、无 Postgres、无常驻守护),保留期
自动清理 + 定期 VACUUM,进程内调度。

## 快速开始

```bash
git clone https://github.com/xinzhuzi/MYIA
cd MYIA
uv sync                     # 或 pip install -e .
uv run myia --version       # myia 0.1.0
```

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

uv run myia test plugins/demo-min.yaml --json            # 试抓:不入库、不推送
uv run myia run plugins/demo-min.yaml --dry-run --json   # 全链演练,不推送
uv run myia run plugins/demo-min.yaml                    # 正式跑;--loop 常驻调度
```

完整走读:[docs/zh/getting-started.md](docs/zh/getting-started.md)。

> PyPI 包(`myia`、`myia-classifier`)将走手动发布流程;在那之前请如上从源码安装。

## AI 原生闭环

把闭环交给任何编码 agent(Claude Code、Cursor……)。
[Agent Skill](skill/SKILL.md) 是自包含速查表,一条命令安装
(`myia skill install --agent claude`;也支持 `cursor` / `zcode`,
`--path` 自定义目录,`--link` 以链接代替复制):

```
myia skill install --agent claude         # 一次性:速查表 → ~/.claude/skills/myia/
myia init --json                          # 拿结构化信息清单
( agent 现场写 <id>.yaml )                # 对照 12 节 schema
myia test plugins/<id>.yaml --json        # 试抓,核对字段与去重键
myia run plugins/<id>.yaml --dry-run      # 演练
myia run plugins/<id>.yaml --loop         # 常驻调度
myia doctor --json                        # 拿 findings;agent 自修后复查
```

## 架构

```
用户层          myia CLI · Agent Skill · 桌面应用(Tauri,v1.1)· Web UI(规划中)
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
分析            内置七类关键词分类器(myia-classifier)
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
仍是 alpha:糙边犹存。

| 里程碑 | 范围 | 状态 |
|---|---|---|
| v0.1 骨架 | 核心流水线、direct_api/static/firecrawl 引擎、12 节 schema、变更指纹、分类器、飞书路由 | ✅ 已交付 |
| v0.2 可用 | SQLite 存储 + 注册表 + 保留期、完整 CLI(init/test/list/doctor)、crawl4ai L3、LLM 精评 + 预算护栏、docker compose、Telegram、钥匙链凭据 | ✅ 已交付 |
| v0.3 生态 | Agent Skill、插件市场(本地/远端双模)、Scrapling L4、反馈闭环(CLI + 回调接收) | ✅ 已交付 |
| v0.4 深水区 | stealth_browser L5、llm_browser L6、趋势基线、事件聚合 | ✅ 已交付 |
| v1.0 发布 | 双语文档、演示物料、GitHub 门面、公开交付 | ✅ 已交付 |
| v1.1 桌面优先 | Tauri 桌面壳(Python 核心以 sidecar 嵌入)、进程内插件级、卡片内反馈按钮 | 🚧 进行中 |
| Web UI | 同一核心上的浏览器前端 | 📋 规划中 |

## 伦理与边界

MYIA 默认讲礼貌:尊重 robots.txt、限速采集、凭据永不落明文。要求真人验证
(验证码、手机号)的源会以结构化错误失败 —— MYIA 不做绕过。完整表述见
[中文 FAQ](docs/zh/faq.md) · [English FAQ](docs/en/faq.md)。

## 社区

- Bug 与需求:[issue 模板](.github/ISSUE_TEMPLATE/)
- 参与贡献:[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略与凭据处理设计:[SECURITY.md](SECURITY.md)

## 致谢

MYIA 自有代码全部原创(MIT),但站在巨人的肩膀上 —— 以依赖、插件后端与设计
参考的形式接入:

- [crawl4ai](https://github.com/unclecode/crawl4ai) —— L3 采集引擎(可选依赖)
- [Scrapling](https://github.com/D4Vinci/Scrapling) —— L4 自适应反爬引擎(可选依赖)
- [Firecrawl](https://github.com/firecrawl/firecrawl) —— L3 云端/自建渲染后端(可选依赖,以 API 调用)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) —— L6 LLM 浏览器兜底(可选依赖)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) —— 源管理与 diff 交互参考,`myia-monitor` 插件后端
- [RSSHub](https://github.com/DIYgod/RSSHub) —— 「一切皆源」的哲学参考
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) —— `myia-proxy` 插件后端
- [Photon](https://github.com/s0md3v/Photon) —— `myia-osint` 插件后端(以 git 子模块引入)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— `myia-douyin` 插件后端
- [Maxun](https://github.com/getmaxun/maxun) —— `myia-maxun` 插件后端
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
you one slice: a crawler, a feed, a diff watcher. MYIA is the whole chain,
config-driven, and built to be driven by your AI agent.

Describe any category as **one YAML file**. MYIA fetches it (six engines on
an auto-degrading ladder), classifies and dedups it, scores it (keywords
first — zero tokens; optional LLM for precision), and pushes what matters to
your messaging apps. The YAML itself is written by your coding agent: it
reads the schema, generates the config, trial-fetches with `myia test`, and
repairs broken sources on its own from `myia doctor` output.

**Humans decide; AI does the rest.**

### Why MYIA

| Existing tools | What they give you | What they miss |
|---|---|---|
| Crawlab / Kestra | crawler & workflow orchestration | a vehicle only — no classification, dedup or push |
| changedetection.io | website change monitoring | watches diffs — no collection pipeline, no analysis, no LLM scoring |
| RSSHub | turns sites into RSS feeds | source conversion only — no filtering, scoring or delivery |
| Single-purpose watchers (credential / deal trackers) | one niche category each | category-locked; every new target means new tooling |
| **MYIA** | **category-agnostic fetch → classify → analyze → push, all config-driven** | — |

### Highlights

<table>
<tr>
<td width="50%" valign="top">

🧠 **Intelligence, not just crawling**
A seven-category keyword classifier (zero tokens, shipped as the standalone
[`myia-classifier`](myia-classifier/) package) plus optional LLM enrichment
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
Mark pushed items valuable / not valuable (CLI today; Telegram/Feishu
callback receivers ship now, in-card buttons land with the desktop app) —
negative feedback retunes watchlist weights and thresholds over time.

</td>
</tr>
</table>

Batteries included: SQLite single-file storage (no Redis, no Postgres, no
daemon), retention + auto-VACUUM, in-process scheduling.

### Quickstart

```bash
git clone https://github.com/xinzhuzi/MYIA
cd MYIA
uv sync                     # or: pip install -e .
uv run myia --version       # myia 0.1.0
```

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

uv run myia test plugins/demo-min.yaml --json            # trial fetch: no DB, no push
uv run myia run plugins/demo-min.yaml --dry-run --json   # full rehearsal, no push
uv run myia run plugins/demo-min.yaml                    # real run; add --loop for scheduling
```

Full walk-through: [docs/en/getting-started.md](docs/en/getting-started.md).

> PyPI packages (`myia`, `myia-classifier`) will publish via a manual release
> workflow; until then, install from source as above.

### The AI-native loop

Hand the loop to any coding agent (Claude Code, Cursor, …). The
[Agent Skill](skill/SKILL.md) is a self-contained cheat sheet, installed with
one command (`myia skill install --agent claude`; also `cursor` / `zcode`,
`--path` for a custom dir, `--link` to symlink instead of copy):

```
myia skill install --agent claude         # one-time: skill sheet → ~/.claude/skills/myia/
myia init --json                          # structured checklist of what to collect
( agent writes <id>.yaml )                # against the 12-section schema
myia test plugins/<id>.yaml --json        # trial fetch, inspect fields + dedup keys
myia run plugins/<id>.yaml --dry-run      # rehearsal
myia run plugins/<id>.yaml --loop         # scheduled operation
myia doctor --json                        # findings; the agent repairs and re-checks
```

### Architecture

```
User layer      myia CLI · Agent Skill · desktop app (Tauri, v1.1) · Web UI (planned)
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
Analysis        builtin 7-category keyword classifier (myia-classifier)
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
them touch the real network. Still alpha: rough edges remain.

| Milestone | Scope | Status |
|---|---|---|
| v0.1 skeleton | Core pipeline, direct_api/static/firecrawl engines, 12-section schema, change fingerprint, classifier, Feishu routing | ✅ shipped |
| v0.2 usable | SQLite store + registry + retention, full CLI (init/test/list/doctor), crawl4ai L3, LLM enrich + budget guardrails, docker compose, Telegram, keychain secrets | ✅ shipped |
| v0.3 ecosystem | Agent Skill, plugin market (local/remote dual-mode), Scrapling L4, feedback loop (CLI + callback receivers) | ✅ shipped |
| v0.4 deep water | stealth_browser L5, llm_browser L6, trend baselines, event aggregation | ✅ shipped |
| v1.0 launch | Bilingual docs, demo assets, GitHub facade, public delivery | ✅ shipped |
| v1.1 desktop-first | Tauri desktop shell (Python core as sidecar), in-process plugin tier, in-card feedback buttons | 🚧 in progress |
| Web UI | browser front-end on the same core | 📋 planned |

### Ethics & boundaries

MYIA is polite by default: robots.txt respected, rate-limited fetching,
credentials never in plaintext. Sources that demand human verification
(CAPTCHA, phone numbers) fail with a structured error — MYIA does not attempt
to bypass them. Full statement in the
[FAQ](docs/en/faq.md) · [中文 FAQ](docs/zh/faq.md).

### Community

- Bug reports & feature requests: [issue templates](.github/ISSUE_TEMPLATE/)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy & credential-handling design: [SECURITY.md](SECURITY.md)

### Acknowledgments

MYIA's own code is original (MIT), but it stands on giants — consumed as
dependencies, plugin backends and design references:

- [crawl4ai](https://github.com/unclecode/crawl4ai) — L3 fetch engine (optional dependency)
- [Scrapling](https://github.com/D4Vinci/Scrapling) — L4 adaptive anti-bot engine (optional dependency)
- [Firecrawl](https://github.com/firecrawl/firecrawl) — L3 cloud/self-hosted rendering backend (optional dependency, called as an API)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) — L6 LLM-browser fallback (optional dependency)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) — source-management & diff UX reference; `myia-monitor` plugin backend
- [RSSHub](https://github.com/DIYgod/RSSHub) — the "everything is a feed" philosophy
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — `myia-proxy` plugin backend
- [Photon](https://github.com/s0md3v/Photon) — `myia-osint` plugin backend (vendored via git submodule)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) — `myia-douyin` plugin backend
- [Maxun](https://github.com/getmaxun/maxun) — `myia-maxun` plugin backend
- [Tauri](https://github.com/tauri-apps/tauri) — desktop shell (Python core embedded as a sidecar)

No upstream source is copied into this repository except clearly-marked git
submodules; dependency policy in [CONTRIBUTING.md](CONTRIBUTING.md).

### License

[MIT](LICENSE) © 2026 xinzhuzi
