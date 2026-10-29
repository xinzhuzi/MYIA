# Getting Started

> Run your first category in three minutes: install → credentials → trial
> fetch → real run → where the data lands.
> Then continue with the [plugin guide](write-a-plugin.md) and the
> [schema reference](schema.md); see the [FAQ](faq.md) for crawling ethics
> and boundaries.
> Building the desktop app from source (Windows MSI): see
> [Windows local build](build-windows.md).

## 1. Install

### 1.1 Desktop app (recommended)

Grab the desktop installer (macOS dmg / Windows msi) from
[zhuzibaijia.online](https://zhuzibaijia.online/). **The Python runtime
and third-party dependencies do not ship inside the installer** (which keeps
it much smaller): on first launch open **Settings → Python environment** and
click "Start setup" — the app then installs a self-managed environment
online, with every step visible and retryable:

- The install chain has five steps: **download → sha256 verify → extract into
  the data root → pip install the locked dependency list → self-check
  handshake**, each lit up on the settings page. If it fails mid-way (offline,
  corrupt archive) a retry skips the steps already completed (idempotent
  resume) — you never start from scratch.
- The runtime is **pinned** to
  [indygreg/python-build-standalone](https://github.com/indygreg/python-build-standalone)
  `cpython-3.12.7+20241016` (install_only build, sha256 pinned by the bundled
  manifest — a mirror cannot bypass the check). It lands in `python/` under
  the data root (macOS `~/Library/Application Support/MYIA/python/`, Windows
  `%APPDATA%\MYIA\python\`), never in the install directory.
- **Both mirrors are overridable** (friendly for slow or restricted networks):
  the settings page can replace the runtime download URL and the PyPI index
  (e.g. `https://pypi.tuna.tsinghua.edu.cn/simple`) at the same time. A mirror
  only changes where bytes come from — the pinned version and the sha256
  check stay in force.
- The first setup needs network and ~500 MB of free disk. Until the
  environment is ready every screen shows a "Python runtime not configured"
  guide — **nothing downloads in the background**; once setup completes the
  core process starts automatically.
- Upgrading from an older version: your database / plugins / models / keychain
  credentials carry over untouched (zero migration); a one-time banner on
  first launch walks you to settings for that single setup. Later app updates
  that change the dependency list surface a "Sync dependencies" button in the
  same section — one idempotent click.

Once the environment is ready, categories on the desktop run in exactly the
same shape as the CLI — one YAML file. **What this repo open-sources is the
engine; the categories are yours to fill in**: the install ships the
zero-credential demo `myssia-demo` (GitHub's new-star board — real data on
the first run) plus sanitized per-category templates `config/*.example.yaml`;
you build real categories from those templates (see section 3). The demo,
abridged:

```yaml
id: myssia-demo
name: Demo · GitHub new stars
schedule: "0 9 * * *"
sources:
  - name: github-new-stars
    engine: direct_api
    url: "https://api.github.com/search/repositories?q=created:%3E2026-09-01&sort=stars&order=desc&per_page=30"
    extract:
      type: json_path
      fields:
        title: "$.items[*].full_name"
        url: "$.items[*].html_url"
push:
  - channel: stdout              # zero-credential channel; runs out of the box once configured
```

### 1.2 From source (CLI / development)

世事 is a pure-Python package (Python 3.11+) with zero heavy core
dependencies. Note: the root package depends on the in-repo subpackage
`myssia-classifier` (a uv workspace member, not published to PyPI), so
installing from source requires `uv sync` — a bare `pip install -e .`
cannot resolve that dependency and fails outright:

```bash
git clone https://github.com/xinzhuzi/myia
cd shishi
uv sync                     # the only from-source install (workspace deps resolve via uv alone)
uv run myssia --version       # prints myssia x.y.z (the installed version)
```

Heavy fetch engines are optional extras. When one is missing the pipeline
raises a structured `dependency_missing` error and keeps going down the
degrade chain — it never crashes the run:

```bash
uv sync --extra crawl4ai    # L3 JS-rendered page engine
uv sync --extra llm         # enrich scoring / aggregate event merging (openai client)
uv sync --extra table       # table restore (rapid_table: screenshot tables → metadata.tables)
uv sync --extra trafilatura # L2 article fallback (single-page body extraction for rule-less/revamped sources, opt-in switch)
```

There are twelve capability extras in total: `crawl4ai` / `scrapling` /
`firecrawl` / `skyvern` / `simplex` / `llm` / `vision` / `table` /
`trafilatura` / `apprise` / `telethon` / `browser`; an `all` aggregate key
installs six of them in one go (`crawl4ai` / `scrapling` / `firecrawl` /
`skyvern` / `llm` / `browser` — `simplex`/`vision`/`table`/`trafilatura`/
`apprise`/`telethon` are not part of it). `all` shares only the openai
package with `vision` — the OCR engines
(ocrmac / rapidocr with the onnxruntime backend) are not in `all`, so run
`uv sync --extra vision` separately for image reading. Table restore has a
second install path on the desktop: the **"table restore" component switch
under Settings → Python environment** installs the pinned component into
the managed environment — no repackaging, mirror overrides apply.
Categories opt in with `images.table: true` (see the images section in the
[schema reference](schema.md)). With the component missing the category
still runs — only table restore degrades silently, and `myssia doctor`
surfaces a `table_dependency_missing` warning ahead of time. For an
always-on server deployment use the compose form:
`docker compose -f docker/docker-compose.yml up -d` (see
`docker/README.md`).

**The trafilatura article-fallback switch (off by default)**: installing the
extras only provides the capability — the environment variable
`MYIA_EXTRACT_FALLBACK=1` must also be set explicitly before the
static_html engine falls back to single-page body extraction for sources
with no `extract` rules or whose rules went empty after a site revamp
(fallback items carry `metadata.extract_provenance = "trafilatura"`;
degenerate list-page output under 120 characters of body text is stopped by
the quality gate and stays zero-item). The switch is a global environment
variable and **depends on how you launch**:

- CLI / running the binary directly: prefix the env on the command line,
  e.g. `MYIA_EXTRACT_FALLBACK=1 myssia run <category>`;
- installed desktop app: `launchctl setenv MYIA_EXTRACT_FALLBACK 1`, then
  **restart the app** (`open` does not pass through shell env vars; the
  sidecar inherits the app's parent process environment — same channel as
  `MYIA_SMOKE_ROUTE`). To turn it off: `launchctl unsetenv
  MYIA_EXTRACT_FALLBACK` and restart.

Beyond the global env the switch can also be **overridden per source**:
`engine_options.static_html.extract_fallback: true/false` in category YAML
takes precedence over the global variable (precedence = source-level > global
`MYIA_EXTRACT_FALLBACK=1` > off by default; with the key unset, behavior is
byte-identical to the global setting; a non-boolean value is structurally
refused at fetch time) — full details in the `extract` row of the
[schema reference](schema.md).

## 2. Configure credentials

世事 **never accepts plaintext credentials in YAML** — you write references
that are resolved at run time:

- `env:VAR_NAME` — read from the environment at run time;
- `keychain:myia/<scope>/<name>` — read from the OS keychain (macOS
  Keychain / Windows DPAPI). The namespace is canonical: store the value
  first with `myssia secret set`, then reference it in YAML. Flat legacy
  names (e.g. `keychain:linuxsb_cookie`) are refused at resolve time.

```bash
# Push channel (Feishu bot; for Telegram it is TELEGRAM_CHAT_ID / TELEGRAM_BOT_TOKEN)
export FEISHU_CHAT_ID=...
export FEISHU_BOT_TOKEN=...

# LLM scoring endpoint (OpenAI-compatible; 世事 has no built-in endpoint, no default key)
export MYIA_LLM_BASE_URL=...
export MYIA_LLM_KEY=...

# Credentials that should not live in the environment (e.g. a site cookie)
# go into the keychain: pipe the value over stdin — never pass it as a
# command-line argument (it would land in shell history and process lists)
myssia secret set myia/stocks/site_cookie < cookie.txt
```

The Weixin channels are optional bridges with two paths: `weixin` (the
official bridge) sends outbound through a local resident Hermes-Agent install
that holds the login — without one, that platform is unavailable (the platform
card honestly says "needs a local Hermes"); `weixin-local` is the same-machine
convenience path through the managed wechat-bridge component, no Hermes
needed. myssia itself stores zero WeChat credentials on either path; the
per-channel conventions live in the [plugin guide](write-a-plugin.md).

Reddit sources go through the official Data API engine (`engine: reddit`,
off-chain explicit selection) with **optional** credentials: unconfigured =
an explicit empty state for that source (`credential_missing` skip, zero
requests, never blocks the category). To activate, register a script-type app
at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) (a placeholder
redirect uri `http://localhost:8080` is fine), then store the client
id/secret in the keychain:

```bash
myssia secret set myia/reddit/client-id < id.txt
myssia secret set myia/reddit/client-secret < secret.txt
```

…and reference them via `engine_options.reddit.client_id/client_secret` in
the source YAML (see the commented example in `config/ai-news.example.yaml`).
Timing note: Reddit's new-API application window closes 2026-10-31 and the
public Data API sunsets in phases from 2027-03 (the RSS channel retires
2026-11-13) — in-channel sources all ride OAuth-authorized calls; the
`.rss`/`.json` scraping path stays disabled by the site-wide robots ban.

Don't want to configure a paid endpoint? Both vision and enrich have
zero-cost paths (local mlx-vlm/Ollama → cloud free tiers → light Gemini
daily batches); see [Zero-cost setup](zero-cost.md) for endpoint choices
and the quota snapshot.

Misconfiguration needs no guesswork: `myssia doctor --json` probes every
reference for existence (is the env var set / is the keychain name present)
and turns a missing one into a concrete repair action (e.g. run
`myssia secret set ...`).

## 3. Run your first category

A minimal category with zero credentials and zero cost — save this as
`config/demo-min.yaml` (point `url` at any server-rendered list page):

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
  builtin: false                 # demo items match none of the seven built-in categories
push:
  - channel: stdout              # zero-credential local verification; every other field defaults
```

Three commands cover verify → rehearse → run:

```bash
uv run myssia test config/demo-min.yaml --json            # trial-fetch each source, no push/storage
uv run myssia run config/demo-min.yaml --dry-run --json   # full rehearsal, no push
uv run myssia run config/demo-min.yaml                    # the first real run
```

- `myssia test --json` reports per source: `ok` (did the chain get data),
  `engine` (the engine actually chosen), `items[].fields` (extracted-field
  preview) and `fingerprint.verdict` (`unchanged_skip` = content unchanged,
  normal; `changed_or_first_fetch` = items will be extracted).
- `myssia run --json` reports `stages[]` (fetch/classify/dedup/analyze/push
  items_in→items_out with skip reasons) and `pushes[]` (route buckets and
  send results).
- For long-term use add `--loop`: resident scheduling by the YAML's
  `schedule` + `timezone`. Sharing one bot token across categories (the
  bot token always comes from `env:TELEGRAM_BOT_TOKEN`) is supported:
  Telegram answers concurrent `getUpdates` polls with 409 Conflict, and the
  product enforces one poller per token in-library — the first resident
  process owns feedback receiving, later ones stay poll-idle (pushes
  unaffected; their feedback lands in the first category's store);
  `myssia doctor` discloses this single-receiver semantics as
  `telegram_token_poll_conflict`.

The repo ships no real categories (the engine is open-source; categories
are self-filled): `config/` carries only the demo and sanitized templates.
Each `config/<category>.example.yaml` keeps the structure and comments of a
real category, with source URLs / group names / channel ids replaced by
placeholders. Walk one self-filled category end to end with wool
(freebie/deal intel over community forums):

```bash
# 1. Copy the template into a runnable category file (data-root config/ or
#    the repo config/ both work)
cp config/wool.example.yaml config/wool.yaml

# 2. Edit config/wool.yaml: replace the https://EXAMPLE_FEED_N placeholders
#    with sources of your own; credential slots are already env: / keychain:
#    references — prepare the values as in section 2
uv run myssia test config/wool.yaml --json   # trial-fetch; catches leftovers
uv run myssia run config/wool.yaml            # the real run; --loop to stay resident
```

Every other template (stocks / ai-news / gpu-prices, …) works the same way.
For a zero-credential taste first: the demo runs out of the box,
`uv run myssia run config/myssia-demo.yaml`.

Or just tell an AI agent what you want — it reads the
[plugin guide](write-a-plugin.md) (condensed version:
[skill/SKILL.md](../../skill/SKILL.md)), generates the category YAML, and
verifies and runs it with the same commands.

## 4. Where data lives

Everything is stored in a single SQLite file (default `./myssia.db`, override
with `--db`): fetched items, the dedup registry, change baselines and
numeric history, push feedback, run history and per-source health.
`storage.retention` in the category YAML controls the retention window (expired
items are purged automatically) and `storage.vacuum` the VACUUM cadence;
categories declaring `baseline:` keep their numeric history for twice the
item retention (so the "vs last week" window stays complete).

## 5. Next steps

- [Plugin guide](write-a-plugin.md): the full path from a natural-language
  request to a running YAML — same source as
  [skill/SKILL.md](../../skill/SKILL.md).
- [Schema reference](schema.md): the twelve sections field by field, plus
  the v0.3/v0.4 sidecar sections (`plugin:` scenario plugin / `baseline:`
  trend baseline / `aggregate:` event aggregation).
- [FAQ](faq.md): crawling ethics and boundaries (robots.txt, captchas,
  human-verification walls), credential safety, and self-repair paths for
  common failures.
- [Zero-cost setup](zero-cost.md): the free LLM endpoint guide — local
  mlx-vlm/Ollama (the default) → cloud free tiers (Zhipu free models,
  OpenRouter `:free`) → light Gemini daily batches, with the quota
  snapshot and per-vendor wiring steps.
- Feedback loop: the "valuable / not valuable" buttons on pushed cards feed
  back and keep tuning; the CLI equivalents are
  `myssia feedback list / stats / mark`.
