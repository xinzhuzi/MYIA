# MYIA

> **AI-native intelligence hub — say what you want, AI does the rest.**(说需求,AI 做其余)

[![status](https://img.shields.io/badge/status-pre--alpha-orange)](README.md#readme)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**MYIA is a pre-alpha skeleton under active development.** The package
layout, the plugin YAML schema and the docs below are real; most runtime
behavior is still stubbed. Watch the repo to follow v0.1.

## What is MYIA

MYIA runs a config-driven intelligence pipeline:

```
fetch → classify → dedup → analyze → enrich → push
```

Any intelligence category — AI news, stock moves, freebies and deals,
leaked credentials, GPU prices, anything else — is **one YAML file**.
MYIA fetches it, classifies and dedups it, scores it (keywords first,
LLM for precision), and pushes what matters to your messaging apps.

You describe what you want to watch; an AI agent reads the plugin schema,
writes the category YAML for you, runs it, and repairs it when a source
breaks. Humans decide; AI does the rest.

## Quickstart

```bash
pip install myia
myia run plugins/stocks.yaml
```

> Placeholder for the v0.1 release — the CLI is a stub until then.

## Architecture

```
User layer      CLI (myia run <yaml>) · Agent Skill · desktop app · Web UI
                     │
Orchestration   Pipeline: fetch → classify → dedup → analyze → enrich → push
                     │
Plugin layer    One YAML per category (ai-news · stocks · wool · credentials · …)
                     │
Fetch engines   L1 API direct → L2 static HTML → L3 crawl4ai → L4 Scrapling
                (L5 anti-detect browser · L6 LLM browser — opt-in last resort)
                     │
Storage         SQLite single file (default) · pluggable
                     │
Push            Feishu card · Telegram · webhook
```

## Highlights

- **Six-layer degrade chain** — sources are not pinned to an engine:
  L1 API → L2 static HTML → L3 crawl4ai → L4 Scrapling, with L5/L6 browser
  engines as opt-in last resorts. A source that fails on one layer is
  retried on the next; the winning engine is written back to the config.
- **Seven-category classifier + LLM precision scoring** — a zero-token
  keyword pre-filter (credit-card / proxy-node / buying-agent / server /
  token / ai-news / freebie), then optional LLM scoring on
  value / relevance / credibility (0–10).
- **Threshold routing** — score ≥ 8 pushes immediately, ≥ 5 joins the daily
  digest (AM/PM slots), below 5 is archived only. Push cards carry
  valuable / not-valuable feedback buttons that retune prompts and thresholds.
- **AI writes the YAML** — the Agent Skill (`skill/SKILL.md`) plus the
  documented 12-section plugin schema let any coding agent turn a
  natural-language request into a working category config; `myia doctor`
  prints structured diagnostics the agent can act on by itself.

## Roadmap

| Milestone | Scope | Window |
|---|---|---|
| **v0.1 skeleton** | Core pipeline + direct_api/static/firecrawl engines, full 12-section YAML schema, change fingerprint, seven-category classifier, threshold routing (Feishu), ai/wool/stocks plugins | 1–2 weeks |
| **v0.2 usable** | SQLite store + registry + retention/VACUUM, full CLI (init/test/list), crawl4ai as L3 default, LLM enrich (batch/cache/budget) + watchlist, docker compose, Telegram push, secrets into keychain/DPAPI | +2 weeks |
| **v0.3 ecosystem** | Agent Skill release, plugin market directory (remote plugin mode), Scrapling (L4), push feedback-loop buttons | +2 weeks |
| **v0.4 deep water** | stealth_browser (L5, embedded MCP client), llm_browser (L6/skyvern), change/trend baselines, event aggregation | +2–3 weeks |
| **v1.0 launch** | Bilingual docs, video demo, release to Reddit/V2EX | +1 week |

## License

[MIT](LICENSE) © 2026 xinzhuzi

---

## 中文说明

**MYIA** 是一个 AI 原生情报中枢——**说需求,AI 做其余**。

- **是什么**:配置驱动的情报流水线——抓取 → 分类 → 去重 → 分析 → 增强 → 推送。
  任何情报品类(AI 资讯、股票异动、羊毛、凭证、显卡行情……)就是一个 YAML 文件。
- **使用方式**:对 AI 说"帮我盯着美股 AI 板块",agent 读插件规范、现场生成品类
  YAML、跑起来,源失效时读 `myia doctor` 结构化诊断自行修复;人只做决策。
- **亮点**:
  - 六层采集降级链(L1 API 直连 → L6 LLM 浏览器),源不写死引擎,失败自动降级并回写;
  - 七大类关键词粗筛(零 token)+ LLM 精评(价值/相关性/可信度三维打分);
  - 阈值分级路由:score≥8 立即推 / ≥5 进每日摘要(早晚槽位)/ <5 只归档;
  - 推送卡片带「有价值/没价值」按钮,反馈回写持续调优;
  - AI 写 YAML:Agent Skill + 12 节插件 schema,agent 照规范现场生成配置。
- **快速开始**(占位,v0.1 发布后可用):

  ```bash
  pip install myia
  myia run plugins/stocks.yaml
  ```

- **数据**:本地 SQLite 单文件,长期运行按保留期自动清理、定期 VACUUM。
- **许可证**:MIT。
