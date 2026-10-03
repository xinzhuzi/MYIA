# Reddit r/selfhosted 发帖草稿(英文,v1.1.1/1.1.2 开源发布)

> **状态:草稿,正文可直接定稿;发布节奏由主人定,发帖前必须过「发帖前硬
> gate」与版规核实。**

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
> 本稿口径是 **v1.1.2 发布后的现实**(`pip install shishi` on PyPI、Docker
> 镜像 `ghcr.io/xinzhuzi/shishi`)——截至本稿写就,仓库 README 仍是 1.1.1 +
> "install from source" 口径、PyPI 双包与 v1.1.2 tag 均未上线(实测均 404),
> 故硬 gate 里把这些列为发帖前置条件,未满足前**不可发**(正文里的 "on PyPI"
> 等表述发早一天就是假话)。
>
> 主文案英文,文末附中文对照草稿(便于主人先过目内容)。

## 标题候选(选一)

1. `I open-sourced my self-hosted intelligence hub: one YAML per thing to watch, fetch → classify → dedup → push — and my coding agent writes the YAML (MIT, pip-installable)`
2. `Shishi (世事) — self-hosted "tell it what to watch, AI does the rest": one YAML per category, push to Telegram/Feishu, coding-agent-native (MIT, v1.1.2)`
3. `Stop rewiring scrapers + cron + diff watchers for every new thing to watch — Shishi is one config-driven pipeline for any category (open source, MIT)`

## 正文

Hey r/selfhosted,

I kept assembling the same stack for every "I want to know when X happens"
problem: a scraper, a cron job, a diff watcher, a dedup hack, a webhook into
my messenger. Each new target (AI news, stock moves, freebies, GPU prices)
meant re-wiring all of it.

So I built **Shishi** (世事 — Chinese for "the ways of the world") — an
open-source (MIT), self-hosted intelligence hub. Pure Python, one SQLite
file, no daemon, no Redis, no Postgres. **One YAML file = one intelligence
category:**

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
  stdout, with a URL-key dedup registry: the same URL never pushes twice.
- **Credentials never touch the config** — only `env:VAR` / `keychain:`
  references are legal; a plaintext credential in YAML is rejected at load
  time. Backed by macOS Keychain / Windows DPAPI.
- **Feedback loop** — mark pushed items valuable / not valuable (works from
  the CLI today, plus Telegram/Feishu callback receivers; in-card buttons on
  the desktop app land in a later batch) — negative feedback retunes weights
  and thresholds over time.
- **Polite by default** — robots.txt respected, rate limiting on by default,
  and sources that demand human verification (CAPTCHA, phone numbers) fail
  with a structured error instead of being bypassed.

Three ways to run it:

- **CLI**: `pip install shishi` (v1.1.2, on PyPI; heavy engines are optional
  extras, e.g. `pip install "shishi[crawl4ai]"`), then `shishi run --loop`
  for scheduled operation.
- **Docker**: a
  [compose file](https://github.com/xinzhuzi/shishi/blob/main/docker/docker-compose.yml)
  ships in the repo; the image is `ghcr.io/xinzhuzi/shishi`, built by CI on
  every push to main and every `v*` tag.
- **Desktop app** — see below.

**On the versions:** v1.1.1 made the desktop app daily-drivable, and v1.1.2
adds the PyPI packages and a module rename:

- v1.1.1 unified the desktop data paths, bundled the official plugins on
  first run, and added a **zero-credential demo** — GitHub's new-star board
  via one unauthenticated API call — so the first "run" click shows real data
  instead of a config error. Nothing to fill in.
- v1.1.1 also added a **signed update channel**: the settings screen checks
  for updates, downloads and installs them with signature verification, then
  relaunches.
- v1.1.2 put both packages on PyPI — `pip install shishi` — and renamed the
  Python module `myia` → `shishi`, so `import shishi` works.
- The macOS (Apple Silicon) installer is on
  [GitHub Releases](https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2)
  (grab the dmg; the filename follows the v1.1.1 pattern
  `shishi_1.1.1_aarch64.dmg`). It is **not** Apple-notarized (notarization
  needs a paid developer account, which I don't have yet), so first launch
  takes the right-click → Open → Open dance; every installer is built in
  public CI with traceable logs, and the code is fully auditable.

Desktop screenshots (all fed by real demo-plugin data):
[dashboard](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/dashboard.png) ·
[feed](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/feed.png) ·
[sources](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/sources.png) ·
[logs](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/logs.png) ·
[settings](https://github.com/xinzhuzi/shishi/blob/main/docs/screenshots/settings.png)

Honest status:

- Releases currently ship a macOS (Apple Silicon) installer only — I make no
  claims about other desktop platforms.
- The desktop in-card feedback buttons aren't in yet (the feedback loop works
  via CLI + Telegram/Feishu callback receivers today).
- 2000+ tests run in CI, none of them touching the real network.

Links:

- Repo: https://github.com/xinzhuzi/shishi (MIT)
- Release v1.1.2: https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2
- Getting started: [docs/en/getting-started.md](https://github.com/xinzhuzi/shishi/blob/main/docs/en/getting-started.md)
  (bilingual; the Chinese (zh) tree ships in-repo and tests keep both in sync)
- Agent Skill (for your coding agent): https://github.com/xinzhuzi/shishi/blob/main/skill/SKILL.md

Happy to answer questions — especially on the degrade chain and the
credential handling. What would *you* point it at first?

## 发帖前硬 gate(未全部满足不发;写稿时点实测状态已注)

1. **v1.1.2 Release 已上线**(写稿实测 tag 404):dmg 等资产 URL 当日从
   Release 页复制粘贴,禁止手改版本号拼链。
2. **PyPI 双包已上架**(写稿实测 pypi.org/pypi/shishi/json 与
   /shishi-classifier/json 均 404):两个 JSON API 返回 200 再发——正文
   "on PyPI" 的全部表述以此为前提。
3. **模块更名落地**:仓库源码已 `myia` → `shishi`,干净环境
   `python -c "import shishi"` 实测通过(写稿时入口仍是 `myia.cli:main`)。
4. **README 已同步切到 pip + 1.1.2 口径**(快速开始 / 下载安装段 / 版本号 /
   `shishi-classifier` 目录链接——写稿时该链接指向的目录还叫
   `myia-classifier/`,是死链)——否则读者点进仓库第一屏就与帖子矛盾。
5. **CHANGELOG [Unreleased] 已定版为 [1.1.2]**;顺带把 README 与 CHANGELOG
   关于桌面反馈按钮交付批次的口径对齐(两处现在一个写 v1.1.2 批次、一个写
   v1.2,读者对照会发现打架)。
6. **截图清单按当版实际 UI 核对**:第六屏「消息」已在开发分支合入,若随
   v1.1.2 发布,截图链接清单按实际增补。
7. **CI 测试数以当日实数刷新**:写稿时本地 `pytest --collect-only` 实测
   2159 条,正文用 "2000+";CI 实跑数若不同,以 CI 为准。
8. **版规核对**(见顶部「发前必做」):按当日侧栏规则选 flair 与发帖时机。

## 中文对照草稿(发布前主人过目用,不直接发出)

各位好,我给自己造了个开源自托管情报中枢 **世事 / Shishi**(MIT,纯 Python +
SQLite 单文件、无守护进程):想盯的每类情报(AI 资讯/股票异动/羊毛/显卡行情)
就是一个 YAML 文件,抓取→分类→去重→打分→推送到 Telegram/飞书全自动。YAML
都不用自己写:内置 Agent Skill 让编码 agent 照 12 节规范现场生成、`shishi test`
试抓验证、坏了凭 `shishi doctor` 自修——说需求,AI 做其余。

内里:六级采集降级梯(API→静态 HTML→crawl4ai/Firecrawl→Scrapling→隐身浏览器→
LLM 浏览器,胜出引擎按源记忆);零 token 七类关键词分类器 + 可选 LLM 精评,
score ≥ 8 立推、≥ 5 进早晚摘要;飞书/Telegram/webhook/stdout 四通道 + URL 键
去重(同一 URL 不推第二遍);凭据只走 env/系统钥匙链,YAML 里出现明文凭据
加载即拒;robots.txt 默认尊重、要真人验证的源结构化报错不绕过;反馈闭环 CLI
现已可用(桌面卡片内按钮后续批次)。三种跑法:CLI `shishi run --loop`、
Docker(镜像 `ghcr.io/xinzhuzi/shishi`)、桌面应用;数据单 SQLite 文件。

**v1.1.1 起桌面端可日常用**:macOS(Apple Silicon)安装包在 GitHub Releases
(dmg 文件名按 v1.1.1 规律,发帖当日从 Release 页复制;未做 Apple 公证,首开
右键→打开,安装包公开 CI 构建);装机首跑种子官方插件,含零凭据演示件
(GitHub 新星榜,一次免鉴权 API 调用),第一次点运行就出真数据;设置页走
签名更新通道,验签后自动下载安装。桌面截图(均为 demo 插件真实数据):
仪表盘/信息流/源管理/日志/设置。

**v1.1.2 起**:PyPI 双包上架,`pip install shishi` 直装;Python 模块名
`myia` → `shishi`,`import shishi` 可用。

如实说:Release 目前只有 macOS 安装包,其他桌面平台没有产物、不做宣称;桌面
卡片内反馈按钮还没做(反馈闭环 CLI + 回调接收现已可用);CI 2000+ 测试零真实
网络。

仓库 https://github.com/xinzhuzi/shishi ,求建议:你会先拿它盯什么?
