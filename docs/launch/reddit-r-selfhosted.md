# Reddit r/selfhosted 首发文案(英文场景,未发出)

> **状态:草稿,发布节奏由主人定。** r/selfhosted 规矩:讲自托管故事、
> 接受提问、忌纯营销口吻;发布前再核对一次当周版规。主文案英文,
> 文末附中文对照草稿(便于主人先过目内容)。

## 标题候选(选一)

1. `I built an open-source, self-hosted intelligence hub: one YAML file per thing I want to watch, and my coding agent writes the YAML (MIT)`
2. `MYIA — self-hosted "tell it what to watch, AI does the rest" pipeline: fetch → classify → dedup → push to Telegram/Feishu (MIT, alpha)`
3. `Stop wiring RSS + diff-watchers + webhooks by hand: MYIA is one config-driven pipeline for any intelligence category (open source, MIT)`

## 正文

Hey r/selfhosted,

I kept assembling the same stack for every "I want to know when X happens"
problem: a scraper, a cron job, a diff watcher, a dedup hack, a webhook into
my messenger. Each new target (AI news, stock moves, freebies, GPU prices)
meant re-wiring all of it.

So I built **MYIA** — an AI-native intelligence hub, MIT-licensed, pure
Python + SQLite single file. **One YAML file = one intelligence category**:

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

The YAML is the whole deployment: schedule (cron + timezone), any number of
sources, dedup keys, scoring thresholds, push channels. And because MYIA is
AI-native, you don't even write it — the included Agent Skill teaches your
coding agent (Claude Code, Cursor, …) the 12-section schema, then it
generates the file, trial-fetches with `myia test`, rehearses with
`--dry-run`, and self-repairs broken sources from `myia doctor --json`.
You say what you want; AI does the rest.

What's inside:

- **7-rung fetch degrade chain** — API direct → static HTML → crawl4ai /
  Firecrawl → Scrapling → stealth browser → LLM browser. A source that fails
  one layer is retried on the next; the winning engine is remembered per
  source. Heavy engines are optional extras — the core is `pip install`-light.
- **Zero-token 7-category classifier** (deals, credit cards, AI news, …)
  plus optional LLM scoring (value / relevance / credibility), then
  threshold routing: score ≥ 8 pushes immediately, ≥ 5 joins AM/PM digests,
  below is archived.
- **Feedback buttons** on push cards (valuable / not valuable) that retune
  thresholds over time.
- **Credentials never touch the config**: only `env:` / system-keychain
  references are legal; a plaintext cookie in YAML refuses to load.
- **Ethics defaults**: robots.txt respected, polite rate limiting on by
  default; sources gated behind human verification are refused, not bypassed.

It runs as a plain CLI loop (`myia run --loop`) or via the included
docker compose; data lands in one SQLite file with retention + VACUUM.

Honest status: **alpha**. The core pipeline is implemented and covered by the
CI test suite (no test touches the real network), the desktop app is a spike,
and rough edges remain. Docs are bilingual (EN/中文) in-repo.

- Repo: https://github.com/xinzhuzi/MYIA
- Quickstart: https://github.com/xinzhuzi/MYIA/blob/main/docs/en/getting-started.md
- License: MIT

Happy to answer questions — especially on the degrade chain and the
credential handling. What would *you* point it at first?

## 中文对照草稿(发布前主人过目用,不直接发出)

各位好,我给自己造了个开源自托管情报中枢 **MYIA**(MIT,纯 Python + SQLite
单文件):想盯的每类情报(AI 资讯/股票异动/羊毛/显卡行情)就是一个 YAML 文件,
抓取→分类→去重→打分→推送到 Telegram/飞书全自动。YAML 都不用自己写:内置
Agent Skill 让编码 agent 照 12 节规范现场生成、试抓验证、坏了自修——说需求,
AI 做其余。采集默认尊重 robots.txt、限速礼貌;凭据只走环境变量/系统钥匙链,
配置里出现明文凭据直接拒载。状态如实说是 alpha,CI 全绿、文档双语在仓库里。
求建议:你会先拿它盯什么?
