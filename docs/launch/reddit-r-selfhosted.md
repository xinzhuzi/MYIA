# Reddit r/selfhosted 首发文案(英文场景,未发出)

> **状态:草稿,发布节奏由主人定。**

> **发前必做:人工点开核实现行版规(下方版规信息均未经直接核实)。** 本写稿
> 环境对 reddit 的全部抓取通道均被拒(WebFetch 与 curl 带 UA 请求 `.json` API
> 均返回 403 拦截页;webReader 对 www 域报 "blocked by network security";
> old.reddit 强制登录),下列版规信息全部来自搜索引擎快照,且快照显示该版规则
> 近期反复调整。发帖前请亲自打开
> https://www.reddit.com/r/selfhosted/wiki/rules (或版块侧栏),以当日现行规则为准:
>
> 1. **项目展示贴的 flair 与时机——勿默认沿用旧规则。** 快照史料一(帖
>    `1rmt39o`,"New Project Friday here to stay, updated rules"):项目展示集中
>    "New Project Friday" flair、同名项目重发间隔 3 个月;快照史料二(更晚的
>    帖 `1sey9ch`,"Quarter 2 Update - Revisiting Rules. Again.",帖 ID base36
>    晚于前者):"New project Friday became new project whenever. That rule was
>    not followed."——即 NPF 集中制此后又已放松/改动,现行要求未定,发帖时按
>    侧栏当日规则选 flair 与时机。两帖链接(供人点开,需正常浏览器/登录态):
>    https://www.reddit.com/r/selfhosted/comments/1rmt39o/rules_update_new_project_friday_here_to_stay/
>    https://www.reddit.com/r/selfhosted/comments/1sey9ch/quarter_2_update_revisiting_rules_again/
> 2. **社区对 AI 腔营销文高度反感**(检索快照可见多条同类抱怨帖)——保持第
>    一人称、给具体细节、如实报限制。
> 3. 英文正文未绑定任何 flair/时机策略;规则核对结果只影响发帖时的 flair
>    选择与日期,不影响正文本身。
>
> 主文案英文,文末附中文对照草稿(便于主人先过目内容)。

## 标题候选(选一)

1. `I built an open-source, self-hosted intelligence hub: one YAML file per thing I want to watch, and my coding agent writes the YAML (MIT)`
2. `Shishi (世事) — self-hosted "tell it what to watch, AI does the rest": fetch → classify → dedup → push to Telegram/Feishu (MIT, v1.1.1, desktop app)`
3. `Stop wiring RSS + diff-watchers + webhooks by hand: Shishi is one config-driven pipeline for any watch category (open source, MIT)`

## 正文

Hey r/selfhosted,

I kept assembling the same stack for every "I want to know when X happens"
problem: a scraper, a cron job, a diff watcher, a dedup hack, a webhook into
my messenger. Each new target (AI news, stock moves, freebies, GPU prices)
meant re-wiring all of it.

So I built **Shishi (世事)** — an open-source (MIT), self-hosted intelligence
hub. Pure Python, one SQLite file, no daemon, no Redis, no Postgres.
**One YAML file = one intelligence category:**

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
sources, dedup keys, scoring thresholds, push channels. And because it's
agent-native, you don't even write it — the bundled Agent Skill
(`shishi skill install --agent claude`, Cursor supported too) teaches your
coding agent the 12-section schema; it generates the file, trial-fetches with
`shishi test`, rehearses with `--dry-run`, and repairs broken sources from
`shishi doctor --json` output. You say what you want; the agent does the rest.

What's inside:

- **Six-rung auto-degrading fetch ladder** — direct API → static HTML →
  crawl4ai / Firecrawl → Scrapling → stealth browser → LLM browser. A source
  never hard-codes an engine: when one rung fails, the next takes over, and
  the winning engine is remembered per source (in SQLite — never written back
  into your YAML). Heavy engines are optional extras; the core stays light.
- **Zero-token classifier** (7 keyword categories) plus optional LLM scoring
  (value / relevance / credibility, 0–10), then threshold routing: score ≥ 8
  pushes immediately, ≥ 5 joins the AM/PM digest, the rest is archived.
- **Push that respects your attention** — Feishu cards, Telegram, webhook or
  stdout, with a URL-key dedup registry: you never receive the same item twice.
- **Credentials never touch the config** — only `env:VAR` / `keychain:`
  references are legal; a plaintext credential in YAML is rejected at load
  time. Backed by macOS Keychain / Windows DPAPI.
- **Feedback loop** — mark pushed items valuable / not valuable (works from
  the CLI today, plus Telegram/Feishu callback receivers; in-card buttons on
  the desktop app land in the next release) — negative feedback retunes
  weights and thresholds over time.
- **Polite by default** — robots.txt respected, rate limiting on by default,
  and sources that demand human verification (CAPTCHA, phone numbers) fail
  with a structured error instead of being bypassed.

It runs as a plain CLI loop (`shishi run --loop`) or via the bundled
[docker compose](https://github.com/xinzhuzi/shishi/blob/main/docker/docker-compose.yml);
data lands in one SQLite file with retention + VACUUM.

**v1.1.1 shipped this week**, and the desktop app got daily-drivable:

- macOS (Apple Silicon) installer on
  [GitHub Releases](https://github.com/xinzhuzi/shishi/releases/tag/v1.1.1) —
  grab `shishi_1.1.1_aarch64.dmg`. It is **not** Apple-notarized (notarization
  needs a paid developer account, which I don't have yet), so first launch
  takes the right-click → Open → Open dance; every installer is built in
  public CI with traceable logs, and the code is fully auditable.
- A fresh install auto-seeds the official plugins, including a
  **zero-credential demo** — GitHub's new-star board via one unauthenticated
  API call — so the first "run" click shows real data instead of a config
  error. Nothing to fill in.
- **Signed update channel**: the settings screen checks for updates,
  downloads and installs them with signature verification, then relaunches.

The five desktop screens (all fed by real demo-plugin data):
[dashboard](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/dashboard.png) ·
[feed](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/feed.png) ·
[sources](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/sources.png) ·
[logs](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/logs.png) ·
[settings](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/settings.png)

Honest status:

- CLI install is **from source** for now (`git clone` + `uv sync` — the repo
  is a uv workspace); the PyPI packages are pending a manual release
  workflow. Full walk-through:
  [docs/en/getting-started.md](https://github.com/xinzhuzi/shishi/blob/main/docs/en/getting-started.md)
  (bilingual; the 中文 tree ships in-repo and tests keep both in sync).
- The Windows build is verified at **build level only** — I haven't
  smoke-tested an install on Windows yet.
- 1300+ tests run in CI, none of them touching the real network.

Links:

- Repo: https://github.com/xinzhuzi/shishi (MIT)
- Release v1.1.1: https://github.com/xinzhuzi/shishi/releases/tag/v1.1.1
- Agent Skill (for your coding agent): https://github.com/xinzhuzi/shishi/blob/main/skill/SKILL.md

Happy to answer questions — especially on the degrade chain and the
credential handling. What would *you* point it at first?

## 中文对照草稿(发布前主人过目用,不直接发出)

各位好,我给自己造了个开源自托管情报中枢 **世事 / Shishi**(MIT,纯 Python +
SQLite 单文件、无守护进程):想盯的每类情报(AI 资讯/股票异动/羊毛/显卡行情)
就是一个 YAML 文件,抓取→分类→去重→打分→推送到 Telegram/飞书全自动。YAML
都不用自己写:内置 Agent Skill 让编码 agent 照 12 节规范现场生成、`shishi test`
试抓验证、坏了凭 `shishi doctor` 自修——说需求,AI 做其余。

内里:六级采集降级梯(API→静态 HTML→crawl4ai/Firecrawl→Scrapling→隐身浏览器→
LLM 浏览器,胜出引擎按源记忆);零 token 七类关键词分类器 + 可选 LLM 精评,
score ≥ 8 立推、≥ 5 进早晚摘要;飞书/Telegram/webhook/stdout 四通道 + URL 键
去重;凭据只走 env/系统钥匙链,YAML 里出现明文凭据加载即拒;robots.txt 默认
尊重、要真人验证的源结构化报错不绕过;反馈闭环 CLI 现已可用(桌面卡片内按钮
下一版)。CLI `shishi run --loop` 或随包 docker compose 跑,数据单 SQLite 文件。

**v1.1.1 本周发布**,桌面端可日常用了:macOS(Apple Silicon)安装包在 GitHub
Releases(`shishi_1.1.1_aarch64.dmg`;未做 Apple 公证,首开右键→打开,安装包
公开 CI 构建);装机首跑种子官方插件,含零凭据演示件(GitHub 新星榜,一次免
鉴权 API 调用),第一次点运行就出真数据;设置页走签名更新通道。五屏截图:
仪表盘/信息流/源管理/日志/设置。

如实说:CLI 目前源码安装(git clone + uv sync,uv workspace),PyPI 待手动发布
流程;the Windows build did not ship in v1.1.1 (no Windows installer in Releases — macOS only for now);CI 1300+ tests never touch the real network.

仓库 https://github.com/xinzhuzi/shishi ,求建议:你会先拿它盯什么?
