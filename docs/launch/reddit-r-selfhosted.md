# Reddit r/selfhosted 首发文案(英文场景,未发出)

> **状态:草稿,已对齐 2026-10-04 终态,发布节奏由主人定。**
>
> Final facts as of 2026-10-29 (they supersede the 2026-10-04 snapshot used in the body below):
> named **myssia** (中文名 世事) · storefront repo `xinzhuzi/myia`. **Both PyPI packages are
> live** — `myssia` 0.0.16 (14 releases, 0.0.1→0.0.16) and `myssia-classifier` 0.0.16 (15);
> the CLI installs with `pip install myssia "selectolax<1.0"` (the upper bound is still manual —
> see the measured note in the storefront README). GitHub Release is at **v0.0.17** and carries
> both the macOS dmg (9.6 MB) and the Windows msi (8.7 MB). Every "pending on PyPI / don't write
> `pip install` / no msi in the Release / dmg 117 MB / source-only install" line in the body is
> void; the five capabilities (cron / alert rules / ⌘K palette / server-side read state /
> interaction batch) were on main when this was drafted — confirm against `CHANGELOG.md` and an
> actual install whether the shipped build already contains them.

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

1. `I built an open-source, self-hosted intelligence hub: one YAML file per thing I want to watch, and my coding agent writes the YAML (MIT, Docker image + desktop dmg)`
2. `myssia (世事) — self-hosted "tell it what to watch, AI does the rest": fetch → classify → dedup → push to Telegram/Feishu (MIT, v0.0.1, desktop app)`
3. `Stop wiring RSS + diff-watchers + webhooks by hand: myssia is one config-driven pipeline for any watch category (open source, MIT)`

## 正文

Hey r/selfhosted,

I kept assembling the same stack for every "I want to know when X happens"
problem: a scraper, a cron job, a diff watcher, a dedup hack, a webhook into
my messenger. Each new target (AI news, stock moves, freebies, GPU prices)
meant re-wiring all of it.

So I built **myssia (世事 — "the ways of the world" in Chinese)** — an
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
(`myssia skill install --agent claude`, Cursor supported too) teaches your
coding agent the 12-section schema; it generates the file, trial-fetches with
`myssia test`, rehearses with `--dry-run`, and repairs broken sources from
`myssia doctor --json` output. You say what you want; the agent does the rest.

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

It runs as a plain CLI loop (`myssia run --loop`), via Docker — the v0.0.1
multi-arch image (amd64/arm64) is on GHCR: `docker pull
ghcr.io/xinzhuzi/myia:0.0.1` (the repo carried a short-lived name when
0.0.1 shipped; it's back to `myia` now, and later releases land under
`ghcr.io/xinzhuzi/myia`) — or as the desktop app below. Data lands in one
SQLite file with retention + VACUUM.

**v0.0.1 shipped this week** in three forms — the desktop dmg on GitHub
Releases, the Docker image above, and the PyPI packages (that pair is still
in flight: registration pending, so CLI install stays source-based for
now). The desktop app got daily-drivable:

- macOS (Apple Silicon) installer from
  [zhuzibaijia.online](https://zhuzibaijia.online/) —
  grab the dmg (the download page is authoritative for the asset name and
  version).
  It is **not** Apple-notarized (notarization needs a paid developer
  account, which I don't have yet), so first launch takes the right-click →
  Open → Open dance; every installer is built in public CI with traceable
  logs, and the code is fully auditable.
- A fresh install auto-seeds the official plugins, including a
  **zero-credential demo** — GitHub's new-star board via one unauthenticated
  API call — so the first "run" click shows real data instead of a config
  error. Nothing to fill in.
- **Signed update channel**: the settings screen checks for updates,
  downloads and installs them with signature verification, then relaunches.
- Windows: the msi pipeline went green in CI right after this release (a
  verification run produced the msi artifact), but no Windows installer is
  attached to v0.0.1 and I haven't smoke-tested an install — the first
  Windows package ships with the next release. No "works on Windows" claims
  yet.

The five desktop screens (all fed by real demo-plugin data):
[dashboard](https://github.com/xinzhuzi/myia/blob/main/docs/screenshots/dashboard.png) ·
[feed](https://github.com/xinzhuzi/myia/blob/main/docs/screenshots/feed.png) ·
[sources](https://github.com/xinzhuzi/myia/blob/main/docs/screenshots/sources.png) ·
[logs](https://github.com/xinzhuzi/myia/blob/main/docs/screenshots/logs.png) ·
[settings](https://github.com/xinzhuzi/myia/blob/main/docs/screenshots/settings.png)

**Landed on main since the release** (shipping with the next version — not
in the v0.0.1 dmg): scheduled jobs — `myssia cron`, natural-language
schedules ("every monday 9am") or 5-field cron, three coexisting hosts
(CLI serve / desktop ticker / system crontab tick) with run summaries
delivered to targeted channels (local archive / Feishu / Telegram); alert
rules evaluated against every incoming item (push or tag on match, with a
dry-test that never sends); a ⌘K command palette; feed read/starred/later
state moved server-side (whole-library "mark all read", survives a device
change); and a keyboard-interaction batch (j/k navigation, context menu,
unread-first, collapsible sidebar).

Honest status:

- CLI install is one `pip` command: `pip install myssia "selectolax<1.0"` — both PyPI
  packages are live (`myssia` 0.0.16 over 14 releases, `myssia-classifier` 0.0.16 over 15;
  checked 2026-10-29). The `selectolax<1.0` upper bound still has to be typed by hand:
  selectolax 1.0 dropped the Modest parser behind `selectolax.parser.HTMLParser`, so without
  the bound every command fails at import (measured in a fresh venv; the next release folds
  the bound into the package metadata). Full walk-through:
  [docs/en/getting-started.md](https://github.com/xinzhuzi/myia/blob/main/docs/en/getting-started.md)
  (bilingual; note this repo is the storefront — docs and screenshots only, the source tree
  ships in the PyPI sdist).
- The Windows msi has been in Releases since v0.0.17 (8.7 MB, signed alongside the macOS dmg)
  — **packaged, but we have not install-smoke-tested it yet**.
- The test suite runs in CI without touching the real network (full-suite record 2026-10-29:
  7058 passed / 0 failed; re-run `--collect-only` before quoting a number).

Links:

- Repo: https://github.com/xinzhuzi/myia (MIT)
- Desktop installers: https://zhuzibaijia.online/
- Agent Skill (for your coding agent): https://github.com/xinzhuzi/myia/blob/main/skill/SKILL.md

Happy to answer questions — especially on the degrade chain and the
credential handling. What would *you* point it at first?

## 中文对照草稿(发布前主人过目用,不直接发出)

各位好,我给自己造了个开源自托管情报中枢 **myssia(中文名:世事)**(MIT,
纯 Python + SQLite 单文件、无守护进程):想盯的每类情报(AI 资讯/股票异动/
羊毛/显卡行情)就是一个 YAML 文件,抓取→分类→去重→打分→推送到
Telegram/飞书全自动。YAML 都不用自己写:内置 Agent Skill 让编码 agent 照
12 节规范现场生成、`myssia test` 试抓验证、坏了凭 `myssia doctor` 自修——
说需求,AI 做其余。

内里:六级采集降级梯(API→静态 HTML→crawl4ai/Firecrawl→Scrapling→隐身浏览器→
LLM 浏览器,胜出引擎按源记忆);零 token 七类关键词分类器 + 可选 LLM 精评,
score ≥ 8 立推、≥ 5 进早晚摘要;飞书/Telegram/webhook/stdout 四通道 + URL 键
去重;凭据只走 env/系统钥匙链,YAML 里出现明文凭据加载即拒;robots.txt 默认
尊重、要真人验证的源结构化报错不绕过;反馈闭环 CLI 现已可用(桌面卡片内按钮
下一版)。CLI `myssia run --loop`、Docker(v0.0.1 双平台镜像在 GHCR:
`ghcr.io/xinzhuzi/myia:0.0.1`,仓库已定名回 myia,后续版本镜像在
`ghcr.io/xinzhuzi/myia`)或桌面应用,数据单 SQLite 文件。

**v0.0.1 本周三形态**:桌面 macOS(Apple Silicon)安装包在 GitHub Releases
(资产名 `shishi_0.0.1_aarch64.dmg`——发布时仓库短暂用旧名,以 Release 页为
准;未做 Apple 公证,首开右键→打开,安装包公开 CI 构建)+ Docker 镜像 +
PyPI 双包在途(登记未完成,现在 pip 装不了);装机首跑种子官方插件,含零
凭据演示件(GitHub 新星榜,一次免鉴权 API 调用),第一次点运行就出真数据;
设置页走签名更新通道。五屏截图:仪表盘/信息流/源管理/日志/设置。

写稿时列在「发布后合入 main、随下个版本」的五项:定时任务
`myssia cron`(自然语言排程 + 运行摘要定向投递)、告警规则、⌘K 命令面板、
已读状态进服务端、feed 键盘流(j/k 导航/右键菜单/未读优先/侧栏折叠)——
当前安装包(v0.0.17)是否已含,对着 `CHANGELOG.md` 与装机实测核过再写。

如实说:CLI 走 `pip install myssia "selectolax<1.0"`(PyPI 双包已上架到 0.0.16,2026-10-29
实核;`selectolax<1.0` 上界暂需手带,原因见门面 README 实测注);Windows msi 自 v0.0.17 起
已挂 Release(8.7 MB,带 `.sig`),但**装机实测未完成**;CI 测试零真实网络(10-29 全量记录
7058 过 / 0 挂;门面 README 已摘除 CI 徽章,别再引「徽章口径」)。

仓库 https://github.com/xinzhuzi/myia ,求建议:你会先拿它盯什么?

---

> 2026-10-04 终态刷新,待主人定稿。
