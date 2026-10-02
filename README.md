# MYIA

> **AI-native intelligence hub — say what you want, AI does the rest.**(说需求,AI 做其余。)

[![CI](https://github.com/xinzhuzi/MYIA/actions/workflows/ci.yml/badge.svg)](https://github.com/xinzhuzi/MYIA/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![status](https://img.shields.io/badge/status-alpha-orange)

<p align="center">
  <img src="docs/demo/assets/myia-demo.gif"
       alt="MYIA:一个 YAML → 情报推送(终端 myia run → 收到推送卡片)"
       width="820">
</p>

<p align="center">MYIA in 3 minutes: one YAML → intelligence push. Recording
scripts &amp; material: <a href="docs/demo/"><code>docs/demo/</code></a> ·
video links (Bilibili / YouTube) to be added on publication.</p>

**MYIA is a config-driven intelligence pipeline**: describe what you want to
watch — AI news, stock moves, freebies and deals, GPU prices, anything — as
**one YAML file**. MYIA fetches it, classifies and dedups it, scores it
(keywords first, optional LLM for precision), and pushes what matters to your
messaging apps. The YAML itself is written by your coding agent: it reads the
plugin schema, generates the config, validates it with `myia test`, and
repairs broken sources on its own from `myia doctor` output. Humans decide;
AI does the rest.

```
fetch → classify → dedup → analyze → enrich → push
```

## Why MYIA

Existing tools each solve one slice of the problem; MYIA is the whole chain:

| Existing tools | What they give you | What they miss |
|---|---|---|
| Crawlab / Kestra | crawler & workflow orchestration | A vehicle only — no intelligence semantics: no classification, dedup or push |
| changedetection.io | website change monitoring | Watches for diffs, but no collection pipeline, no analysis, no LLM scoring |
| RSSHub | turns sites into RSS feeds | Source conversion only — no filtering, scoring or delivery |
| Single-purpose watchers (credential / deal trackers) | one niche category each | Category-locked; every new target means new tooling |
| **MYIA** | **Category-agnostic fetch → classify → analyze → push, all config-driven** | — |

The parts that make it an *intelligence* tool, not just a crawler:

1. **Seven-category classifier** (credit-card / proxy-node / buying-agent /
   server / token / ai-news / freebie) with free-vs-paid dual-signal
   adjudication — zero tokens, shipped as the standalone
   [`myia-classifier`](packages/myia-classifier/) package.
2. **URL-key dedup registry + AM/PM digest slots** — production-proven push
   semantics; you never get the same item twice.
3. **Adaptive multi-engine fetching**: a 7-rung degrade chain (API direct →
   static HTML → crawl4ai / Firecrawl → Scrapling → stealth browser → LLM
   browser). When a source breaks one layer, the next layer takes over, and
   the winning engine is remembered per source.
4. **Feedback loop**: mark pushed items valuable / not valuable (CLI today;
   Telegram/Feishu callback receivers ship now, in-card buttons land with the
   desktop UI) — negative feedback retunes watchlist weights and thresholds
   over time, and prompt notes are recorded for the tuning history.
5. **AI-NATIVE by design**: a fully documented 12-section YAML schema with
   defaults everywhere, `--json` on every command, structured diagnostics —
   every interface is built so an agent can drive it, not just a human.

## Quickstart

MYIA is pure Python (3.11+) with a light core — SQLite single file, no
Redis/Postgres, no daemon:

```bash
git clone https://github.com/xinzhuzi/MYIA
cd MYIA
uv sync                     # or: pip install -e .
uv run myia --version       # myia 0.1.0
```

Heavy fetch engines (crawl4ai / scrapling / firecrawl / skyvern / llm) are
optional extras; a missing engine degrades gracefully down the chain with a
structured `dependency_missing` error instead of crashing:

```bash
uv sync --extra crawl4ai    # L3 JS-rendered page engine
uv sync --extra llm         # LLM enrich scoring / event aggregation
```

Run your first category — a complete, zero-credential config is one small
file (`plugins/demo-min.yaml`, point `url` at any server-rendered list page):

```yaml
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
```

Save the YAML above as `plugins/demo-min.yaml` (the file ships in the repo —
skip this if it already exists), then:

```bash
uv run myia test plugins/demo-min.yaml --json            # trial fetch, no DB, no push
uv run myia run plugins/demo-min.yaml --dry-run --json   # full rehearsal, no push
uv run myia run plugins/demo-min.yaml                    # real run; add --loop for scheduling
```

Credentials never live in YAML — only `env:VAR` / `keychain:myia/<scope>/<name>`
references (plaintext in a config file is rejected at load). See
[docs/en/getting-started.md](docs/en/getting-started.md) for the full walk-through.

> PyPI packages (`myia`, `myia-classifier`) publish via a manual release
> workflow; until then, install from source as above.

## The AI-native loop

Give the loop to any coding agent (Claude Code, Cursor, …) — the
[Agent Skill](skill/SKILL.md) is a self-contained cheat sheet:

```
myia init --json                          # structured checklist of what to collect
( agent writes <id>.yaml )                # against the 12-section schema
myia test plugins/<id>.yaml --json        # trial fetch, inspect fields + dedup keys
myia run plugins/<id>.yaml --dry-run      # rehearsal
myia run plugins/<id>.yaml --loop         # scheduled operation
myia doctor --json                        # findings; the agent repairs and re-checks
```

## Documentation · 文档

Bilingual docs ship in-repo, kept consistent with the code by tests
(`tests/test_docs.py`):

| | English | 中文 |
|---|---|---|
| Getting started | [docs/en/getting-started.md](docs/en/getting-started.md) | [docs/zh/getting-started.md](docs/zh/getting-started.md) |
| Write a plugin | [docs/en/write-a-plugin.md](docs/en/write-a-plugin.md) | [docs/zh/write-a-plugin.md](docs/zh/write-a-plugin.md) |
| Schema reference | [docs/en/schema.md](docs/en/schema.md) | [docs/zh/schema.md](docs/zh/schema.md) |
| FAQ (ethics & boundaries) | [docs/en/faq.md](docs/en/faq.md) | [docs/zh/faq.md](docs/zh/faq.md) |

Agent-facing condensed reference: [`skill/SKILL.md`](skill/SKILL.md).

## Architecture

```
User layer      CLI (myia run <yaml>) · Agent Skill · desktop app (spike) · Web UI (planned)
                     │
Orchestration   Pipeline: fetch → classify → dedup → analyze → enrich → push
                (in-process APScheduler + asyncio; no external orchestrator)
                     │
Plugin layer    One YAML per category · 6 official categories · market plugins
                (proxy pool · changedetection · OSINT · douyin · …)
                     │
Fetch engines   L1 direct_api → L2 static_html → L3 crawl4ai (fallback: firecrawl)
                → L4 scrapling → L5 stealth_browser → L6 llm_browser
                (auto degrade chain; winning engine persisted per source)
                     │
Analysis        builtin 7-category keyword classifier (myia-classifier)
                + optional LLM enrich (value / relevance / credibility, 0–10)
                     │
Storage         SQLite single file (default) · retention + VACUUM · change baselines
                     │
Push            Feishu card · Telegram · webhook · stdout, threshold-routed
                (immediate / digest AM-PM / archive) + feedback loop
```

## Status & Roadmap

The core pipeline is implemented and tested (pytest suite runs in CI; no test
touches the real network). Rough edges remain — this is a pre-1.0 project.

| Milestone | Scope | Status |
|---|---|---|
| v0.1 skeleton | Core pipeline, direct_api/static/firecrawl engines, 12-section schema, change fingerprint, classifier, Feishu routing | ✅ shipped |
| v0.2 usable | SQLite store + registry + retention, full CLI (init/test/list/doctor), crawl4ai L3, LLM enrich + budget guardrails, docker compose, Telegram, keychain secrets | ✅ shipped |
| v0.3 ecosystem | Agent Skill, plugin market (local/remote dual-mode), Scrapling L4, feedback loop (CLI + callback receivers; in-card buttons land with desktop) | ✅ shipped |
| v0.4 deep water | stealth_browser L5, llm_browser L6, trend baselines, event aggregation | ✅ shipped |
| v1.0 launch | Bilingual docs, demo assets, GitHub facade, public launch | 🚧 in progress |
| Desktop / Web UI | Tauri desktop shell (spike validated), web UI | planned |

## Community

- Bug reports & feature requests: [issue templates](.github/ISSUE_TEMPLATE/)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy & MYIA's credential-handling design: [SECURITY.md](SECURITY.md)

## Acknowledgments

MYIA's own code is original (MIT), but it stands on giants — as dependencies,
plugin backends and design references:

- [crawl4ai](https://github.com/unclecode/crawl4ai) — L3 fetch engine (optional dependency)
- [Scrapling](https://github.com/D4Vinci/Scrapling) — L4 adaptive anti-bot engine (optional dependency)
- [Firecrawl](https://github.com/firecrawl/firecrawl) — L3 cloud/self-hosted rendering backend (optional dependency, called as an API)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) — L6 LLM-browser fallback (optional dependency)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) — source-management & diff UX reference; also the `myia-monitor` plugin backend
- [RSSHub](https://github.com/DIYgod/RSSHub) — the "everything is a feed/plugin" philosophy
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — `myia-proxy` plugin backend
- [Photon](https://github.com/s0md3v/Photon) — `myia-osint` plugin backend
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) — `myia-douyin` plugin backend
- [Maxun](https://github.com/getmaxun/maxun) — `myia-maxun` plugin backend
- [Tauri](https://github.com/tauri-apps/tauri) — desktop shell (Python core embedded as a sidecar)

Upstream projects are consumed as libraries / services / design references;
no upstream source is copied into this repository. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the dependency policy.

## License

[MIT](LICENSE) © 2026 xinzhuzi

---

## 中文说明

**MYIA** 是一个 AI 原生情报中枢——**说需求,AI 做其余**。

- **是什么**:配置驱动的情报流水线——抓取 → 分类 → 去重 → 分析 → 增强 → 推送。
  任何情报品类(AI 资讯、股票异动、羊毛、显卡行情……)就是一个 YAML 文件;
  YAML 本身由你的编码 agent 照规范现场生成、试抓验证、坏了自修;人只做决策。

### 市面空白:为什么是 MYIA

| 现有工具 | 给你什么 | 缺什么 |
|---|---|---|
| Crawlab / Kestra | 爬虫/工作流编排 | 只是载具——没有情报语义(分类/去重/推送不管) |
| changedetection.io | 网站变更监控 | 只盯 diff,没有采集管线、分析与打分 |
| RSSHub | 把站点转成 RSS | 只做源转换——没有过滤、打分与投递 |
| 单一用途盯盘工具(凭证/羊毛各一款) | 各覆盖一个细分品类 | 品类写死;换目标 = 换工具 |
| **MYIA** | **品类无关的采集→分类→分析→推送全链,配置驱动** | — |

### Demo

动图(三分钟上手:一个 YAML → 情报推送)见本文顶部;录制脚本与素材见
[`docs/demo/`](docs/demo/),成片外链(B 站/YouTube)发布后回填。

### 快速开始

```bash
git clone https://github.com/xinzhuzi/MYIA
cd MYIA
uv sync                     # 或 pip install -e .

# 保存最小品类配置(仓库未预置该文件;内容与上文英文节同款,可直接复制):
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
  builtin: false
push:
  - channel: stdout
YAML

uv run myia run plugins/demo-min.yaml --dry-run   # 演练一次:不推送,先看会发生什么
```

- **凭据永不进 YAML**:只允许 `env:VAR` / `keychain:myia/<scope>/<name>` 引用,
  配置文件出现明文凭据 = 启动即拒跑;凭据入系统钥匙链(macOS Keychain /
  Windows DPAPI)。
- **对 AI 说需求**:把 [`skill/SKILL.md`](skill/SKILL.md) 装进你的 agent
  (Claude Code / Cursor 均可),说「帮我盯着 XX」,agent 会读规范、写 YAML、
  试抓验证、跑起来,并凭 `myia doctor --json` 自行修复失效源。
- **亮点**:七层采集降级链(L1 API 直连 → L6 LLM 浏览器,源不写死引擎);
  七大类关键词粗筛(零 token,独立包
  [`myia-classifier`](packages/myia-classifier/))+ LLM 精评(价值/相关性/可信度);
  阈值分级路由(score≥8 立即推 / ≥5 进早晚摘要 / <5 只归档);
  反馈闭环:负反馈回写持续调优(`myia feedback mark` 手动标记;
  Telegram/飞书回调接收已就绪,卡片内按钮随桌面版交付)。
- **数据**:本地 SQLite 单文件,按保留期自动清理、定期 VACUUM。
- **伦理边界**:默认尊重 robots.txt、限速礼貌采集;「真人验证+手机号」类源
  结构化报错、不做绕过——见 [FAQ](docs/zh/faq.md)。

### 社区

- Bug 与需求:[issue 模板](.github/ISSUE_TEMPLATE/)
- 参与贡献:[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略与凭据处理设计:[SECURITY.md](SECURITY.md)

### 致谢

MYIA 自有代码全部原创(MIT),但站在巨人的肩膀上——以依赖、插件后端与设计
参考的形式向以下项目致谢:

- [crawl4ai](https://github.com/unclecode/crawl4ai) —— L3 采集引擎(可选依赖)
- [Scrapling](https://github.com/D4Vinci/Scrapling) —— L4 自适应反爬引擎(可选依赖)
- [Firecrawl](https://github.com/firecrawl/firecrawl) —— L3 云端/自建渲染后端(可选依赖,以 API 调用)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) —— L6 LLM 浏览器兜底(可选依赖)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) —— 源管理与 diff 交互参考,`myia-monitor` 插件后端
- [RSSHub](https://github.com/DIYgod/RSSHub) —— 「一切皆源/插件」的哲学参考
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) —— `myia-proxy` 插件后端
- [Photon](https://github.com/s0md3v/Photon) —— `myia-osint` 插件后端
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— `myia-douyin` 插件后端
- [Maxun](https://github.com/getmaxun/maxun) —— `myia-maxun` 插件后端
- [Tauri](https://github.com/tauri-apps/tauri) —— 桌面壳(Python 核心以 sidecar 嵌入)

上游项目只以库/服务/设计参考接入,**不复制任何上游源码进本仓库**;依赖接入
策略见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 许可证

[MIT](LICENSE) © 2026 xinzhuzi
