# Write a Plugin

> One 世事 plugin = one YAML file = one intelligence category, twelve schema
> sections. This is the full reference for humans and AI alike: every section
> has clear semantics and a default. The condensed version is the Agent Skill
> [skill/SKILL.md](../../skill/SKILL.md); the two documents cross-reference
> each other and are locked field-for-field to `src/myssia/schema.py` by
> `tests/test_skill_doc.py` and `tests/test_docs.py`, so they cannot drift.
> This page teaches the *how*; field-by-field detail lives in the
> [schema reference](schema.md).

## The workflow

```
user request
  → myssia init --json           # fetch the structured input checklist (what to collect, what defaults)
  → write plugins/<id>.yaml    # section by section, against the schema reference (docs/en/schema.md)
  → myssia test plugins/<id>.yaml --json    # trial-fetch each source; check fields and dedup keys (no push/storage)
  → myssia run plugins/<id>.yaml --dry-run --json   # full rehearsal (no push)
  → myssia run plugins/<id>.yaml            # the first real run; add --loop for resident scheduling
  → myssia doctor --json         # done when findings is empty
```

Every command's `--json` output is **exactly one JSON document** (stdout);
logs go to stderr — and with `--json`, the `stdout` push channel's card lines
are rerouted to stderr too, so the whole stdout always `json.load`s. Exit codes: `0` success / `1` config or usage error /
`2` all sources failed / `3` partial failure. A source that breaks later
follows the same loop: `myssia doctor --json` reports the structured problem →
the agent fixes the YAML (or the environment) → re-verify — the user only
decides.

## Hard rules

Violating any of these gets the YAML refused at load time (exit code 1, the
error carries the field path):

1. **No plaintext credentials**: values of credential-like keys (key name
   containing `cookie` / `authorization` / `token` / `secret` / `password` /
   `apikey` / `session`, case- and hyphen-insensitive) may only be
   `env:VAR` or `keychain:myia/<scope>/<name>` references.
2. **Never fingerprint titles**: `dedup.key` must not use `{title}` — URL or
   composite keys only.
3. **Unknown fields fail fast**: outside the open `sources[]` extension
   namespace, a misspelled field name is refused; a near-match of a known
   field is reported as a probable typo.
4. **Complete defaults**: every field has a default; the required minimum
   alone is runnable.
5. **Fixed `engine: auto` degrade chain**: L1 `direct_api` → L2
   `static_html` → L3 `crawl4ai` → `firecrawl` → L4 `scrapling` → L5
   `stealth_browser` → L6 `llm_browser`; the winning engine is written to
   SQLite `engine_hints`, **never back to your YAML**.
6. **Layered push semantics**: `push[].route` decides *whether to push*
   (score/field thresholds), AM/PM slots decide *whether it was already
   sent* (re-send prevention) — the two are orthogonal.
7. **YAML must be UTF-8**; the top level must be a mapping; duplicate keys
   are refused.

## Choosing an engine (L1–L6)

| Layer | Engine | Use when | Cost / prerequisite |
|---|---|---|---|
| L1 | `direct_api` | the data has a public JSON/REST API (quotes, releases, community REST) | fastest and cheapest; `extract.type: json_path`; curl the endpoint to confirm the shape before writing fields; when the payload carries no page URL, render item links with `extract.url_template` from `{field}` placeholders (see config/games.example.yaml) |
| L2 | `static_html` | server-rendered HTML (forum lists, news pages) | zero deps; `extract.type: list` + CSS selectors |
| L3 | `crawl4ai` | JS-rendered pages — the data is not in the page source | optional dep `myssia[crawl4ai]`; when missing, `dependency_missing` and the chain continues; without `extract` it auto-structures |
| L3' | `firecrawl` | cloud alternative to crawl4ai | needs endpoint+key (`MYIA_FIRECRAWL_URL` / `MYIA_FIRECRAWL_API_KEY`, or `engine_options.firecrawl.endpoint/api_key` references); unconfigured → this rung fails and the chain continues |
| L4 | `scrapling` | basic anti-bot shields (TLS/HTTP2 fingerprints, basic CF challenges), frequently-redesigned pages (self-healing selectors), infinite-scroll lists | optional dep `myssia[scrapling]`; backend via `engine_options.scrapling.backend` (`stealth`/`dynamic`/`static`); `pagination.mode: scroll` is supported here only |
| L5 | `stealth_browser` | hard-anti-bot pages (anti-detect browser driven over MCP) | needs a Playwright-MCP server; per-run page budget `engine_options.stealth_browser.max_pages` (default 10); `headers.Cookie` on the source is injected past login walls |
| L6 | `llm_browser` | last resort when everything above failed (LLM-driven browser, skyvern) | burns tokens — fallback only; `MYIA_SKYVERN_URL` / `MYIA_SKYVERN_API_KEY` or `engine_options.llm_browser.endpoint/api_key` references |

**Off-chain source engine `credhunter`** (not one of the L1-L6 layers): the
in-process scenario plugin `myssia-credhunter` (see `plugins/myssia-credhunter/`) —
items are assembled by the adapter itself, the `url` is never fetched. Three
lanes selected via `engine_options.credhunter.lane`: `credhunt` (GitHub
artifact hunting, token-pool references, no token → explicit empty state,
the lane stays disabled until a secret is written), `exposure` (FOFA/Shodan
exposure + L0 passive probing, keyless lanes degrade to an explicit empty
state), and `scan` (local
text triage, zero network). It **never joins the auto degrade chain** — it
only takes effect when selected explicitly, and a failure is a structured
per-source failure that never blocks the category. Authorized use only:
credential-leak research on your own or explicitly authorized assets; every
finding is masked head-8/tail-4.

**Off-chain source engine `wechat_local`** (not one of the L1-L6 layers):
feeds local WeChat sessions and Moments into the intelligence stream — via
subprocess calls to the local wechat-bridge component against the logged-in
WeChat desktop client. Zero credentials (the login state lives on the WeChat
side) and zero HTTP from the engine (the source `url` is only a schema shape
anchor; the endpoint is the local bridge). Four channels are selected via
`engine_options.wechat_local.channel` (default `pixel`): `pixel`
(screenshot+OCR of the **currently open** chat only; a declared `chat` that
does not match what the bridge sees = a `chat_not_open` explicit empty
state — never the wrong chat), `localdb` (read-only queries over the
decrypted copy, zero screenshots, zero AX; a missing key = a
`key_unavailable` structured skip), `sns` (the Moments domain: `feed`
posts / `notices` interactions), and `notify` (notification scanning for
new and revoked messages). It **never joins the auto degrade chain** —
explicit selection only; prerequisites are the wechat-bridge component
installed and the WeChat desktop client logged in. Fully commented live
template: `config/weixin.example.yaml`(随源码发布,本门面仓不含) — since the 2026-10-20
four-files-into-one merge, WeChat is a **single file, multiple channels**
(live pixel/localdb samples plus commented sns/notify samples; sources
across all four channels live in one category file sharing one schedule).

**The WeChat notification whole-machine shape** (the `notify` channel +
local popups): a `wechat_local` `notify` source plus `push: macos_notify`
local Notification Center popups (zero network), scanning at the
minute-level cron schedule; `engine_options.wechat_local.chat_whitelist`
is the session whitelist — an empty list refuses at engine construction,
and sessions outside the whitelist are never scanned or notified; deployment
goes through a cron job (desktop scheduler / `myssia cron` — the file's
presence alone never runs it). The sample is the notify commented block in
the sample area of `config/weixin.example.yaml`(随源码发布,本门面仓不含) (fill in
`chat_whitelist` and it is ready to use).

Rule of thumb: view the page source first — data visible → L2, data via an
API → L1, JS-only → L3; when unsure pick `engine: auto` and read the chosen
engine from `myssia test --json` (the `engine` field). Never write CSS
selectors for an API source, never write json_path for an SSR page. Every
engine respects robots.txt and rate-limits (qps 0.5) by default; sources
behind human verification + phone numbers are never bypassed — L5
`stealth_browser` detects the hard wall, attempts nothing, and reports a
structured `captcha_*` error, while other layers only surface generic
failures or empty results (see the [FAQ](faq.md)).

## Full example

L1 API + Feishu push, showing most sections (copy-paste ready — verified
loadable by `load_category`):

```yaml
id: repo-releases
name: Repo release watch
schedule: "0 9,21 * * *"
timezone: Asia/Shanghai
sources:
  - name: releases-api          # L1: public JSON API, paging via {page}
    engine: direct_api
    url: "https://api.example.com/v1/releases?page={page}"
    method: GET
    pagination:
      mode: template
      max_pages: 3
    extract:
      type: json_path
      fields:
        title: "$[*].name"
        url: "$[*].html_url"    # the url field is mandatory (dedup depends on it)
        tag: "$[*].tag_name"
    rate_limit:
      qps: 0.5
      jitter: "1s"
      backoff: exponential
      respect_robots: true
    proxy: direct
    retry: 3
watchlist:
  keywords: [LTS, security, release]
  mute: [ads]
classify:                        # releases match none of the seven built-ins → turn the scan off
  builtin: false
  rules:
    - name: security update
      when: "'security' in title"
      tag: security
dedup:
  key: "{url}"
enrich:
  enabled: false                 # keep false when you don't want LLM scoring — zero config
  model: glm-5.3-flash
  scores: [value, relevance, credibility]
  batch: 20
  cache: true
  budget_per_run: 50000
push:
  - channel: feishu_card
    target: env:FEISHU_CHAT_ID   # credential slots take references; the bot token comes from env:FEISHU_BOT_TOKEN
    route:
      - when: "score >= 8"       # dormant until an LLM score is backfilled
        mode: immediate
      - when: "score >= 5"
        mode: digest
      - when: "score < 5"
        mode: archive
    template: |
      **Releases · {{ date }}**
      {% for item in items %}
      - [{{ item.title }}]({{ item.url }}) {{ item.tag }}
      {% endfor %}
storage:
  retention: 90d
  vacuum: monthly
```

Every category in the repo's `config/` ships a sanitized `*.example.yaml`
template (wool / stocks / ai-news / gpu-prices / monitor / credentials, …:
structure and comments identical to a real category, sources and group
names replaced by placeholders). Sidecar-section usage lives in
`config/gpu-prices.example.yaml`(随源码发布,本门面仓不含) (baseline) and
`config/monitor.example.yaml`(随源码发布,本门面仓不含) (plugin dual mode).

## Minimal example

L2 static page + stdout, zero credentials, running in five minutes:

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
  builtin: false                 # demo items match none of the seven built-ins; turn it off so they are not all dropped
push:
  - channel: stdout              # zero-credential local verification; every other field defaults
```

## Credential rules

- Credentials are **never written in plaintext** in a plugin YAML — only
  `env:VAR_NAME` or `keychain:myia/<scope>/<name>` references.
- Store a keychain value with `myssia secret set myia/<scope>/<name>`; the
  value goes through a stdin pipe or a hidden prompt (**never** `--value` —
  it would land in shell history and process lists); `myssia secret list`
  lists names only; `myssia secret delete <name>` removes one.
- An auth-scheme prefix round-trips: `Authorization: "Bearer env:AIPOCKET_TOKEN"`.
- A plaintext credential **refuses to start** (exit code 1, the error carries
  the field path); credential values are never echoed and never logged
  (reference names may appear, resolved values must not).
- `myssia doctor --json` probes every reference for existence
  (`env_ref_missing` / `keychain_ref_missing`) so an agent can repair the
  environment by itself.

Per-channel credential conventions (the `push[]` `target` and its companion
token; details in [skill/SKILL.md](../../skill/SKILL.md) §2.13):

- `feishu_card`: `target` = chat/group ID (e.g. `env:FEISHU_CHAT_ID`); the
  bot token is read from `env:FEISHU_BOT_TOKEN`.
- `telegram`: `target` = chat id (`env:TELEGRAM_CHAT_ID`); the token is read
  from `env:TELEGRAM_BOT_TOKEN`.
- `ntfy`: `target` = `{server}/{topic}` string reference (e.g. `env:NTFY_TARGET`); optional auth token is read from `env:NTFY_TOKEN` (unset = anonymous public topic).
- `dingtalk`: `target` = custom-robot webhook URL (`env:DINGTALK_WEBHOOK_URL`); optional HMAC signing secret via the `dingtalk_secret` field (unset = bare webhook).
- `wecom`: `target` = touser userid (`env:WECOM_TUSER`); the self-built-app credentials are read from `env:WECOM_CORPID` / `env:WECOM_CORPSECRET` / `env:WECOM_AGENTID`.
- `weixin`: optional bridge channel — outbound goes through the local Hermes-Agent CLI
  (path overridable via `weixin_hermes_bin`); the login state lives only on the Hermes side,
  myssia itself holds zero credentials. `target` = conversation peer id (`env:WEIXIN_PEER_ID`,
  direct push `weixin:<peer id>`); without a Hermes install, sends return a structured
  `bridge_unavailable` error (the config still loads).
- `webhook`: `target` = endpoint URL reference (e.g. `env:MYIA_WEBHOOK_URL`).
- `bark` (iOS push): `target` = device key reference (`env:BARK_DEVICE_KEY`, copied from the Bark app on the iPhone); the endpoint is **not** a credential — leave `bark_endpoint` unset for the official service (`https://api.day.app`) or set it to a self-hosted host (e.g. `http://<host>:8080`, docker `finb/bark-server`); notifications carry the fixed `MYIA` group; no `targets` (no directory semantics).
- `weixin-local` (local WeChat bridge, same-machine convenience, default off): `target` = session id reference (e.g. `env:WEIXIN_LOCAL_CHAT_ID`); the entry must carry `weixin_local_enabled: true` explicitly to load (off = channel not registered); outbound goes through one-shot `send_text` subprocesses of the managed wechat-bridge component (path overridable via `wechat_bridge_bin`); send rate limiting (single message, hard ≥10s gap) is hardcoded inside the bridge and not configurable; missing bridge/WeChat environments return structured `bridge_unavailable`/`bridge_degraded` errors and no error is ever classified dead; the primary outbound remains the `weixin` official bridge.
- `apprise` (universal push): `target` = Apprise target-string reference (`env:APPRISE_URL`; the value is one or more native Apprise URLs such as `bark://…` or `pushover://…`, comma/newline separated — one reference, many targets); needs the optional extra `pip install "myssia[apprise]"` (or `uv sync --extra apprise`) — when missing, sends fail structurally with `apprise_unavailable` and `myssia doctor` reports `apprise_not_installed` (users who never configure this channel are never bothered); no `targets` (no directory semantics).
- `stdout`: zero credentials, first choice for local verification.
- `macos_notify` (macOS Notification Center, 10-14-wechat-notify-revoke): zero network, zero credentials, no `target`/`targets` allowed (same as `stdout`); messages render as native notifications via a literal `/usr/bin/osascript` argv call; failures degrade to a per-message error row and never block other channels.
- `weixin-winauto` (Windows WeChat desktop automation, 10-26 observation phase): `target` is locked to the literal `文件传输助手` (owner's order: group sending unlocked only after stability); `targets` unsupported; messages go through the win_auto ctypes/pywinauto paste-send chain with built-in delivery verification and auto-retry; failures are environmental transients and never marked dead.

## Verify and run

```bash
myssia test plugins/<id>.yaml --json          # trial-fetch (no push/storage; 120s per-source timeout; --source picks one)
myssia run plugins/<id>.yaml --dry-run --json # full rehearsal, no push, no side effects
myssia run plugins/<id>.yaml                  # the first real run
myssia run plugins/<id>.yaml --loop           # resident: fires on schedule+timezone
```

- `myssia test --json` reports per source: `ok`, `engine`, `items[].fields`,
  `items[].dedup_key` (a `dedup_key_error` means the key template does not
  render), `fingerprint.verdict`, and `failures[]` (engine / error_type /
  message).
- `myssia run --json` reports `stages[]` (items_in→items_out and skip reasons
  per stage), `sources[]`, `push[]` (per-channel buckets, routing decisions,
  send reports); `status` is `success`/`partial`/
  `failed`, mapping to exit codes 0/3/2.
- Sources skipped by the change fingerprint (visible skip reason) are the
  **normal path**, not a failure.

## Self-diagnosis (doctor findings → repair actions)

```bash
myssia doctor --json                    # by default checks every plugin in plugins/; or myssia doctor <yaml> --json
```

Source health state machine: `ok` / `degraded` (not fingerprint-skipped yet
0 items, or below 50% of the last-5-run baseline) / `dead` (3 consecutive
failed rounds) / `unknown` (no run history). A run-level `success` never
masks a source silently returning 0 items — trust the per-source health.
Common findings and repairs:

| code / situation | repair action |
|---|---|
| `credential_plaintext` | replace the plaintext with an `env:`/`keychain:` reference |
| `unknown_field` | fix the field name against the [schema reference](schema.md) |
| `missing_field` / `invalid_value` | locate by the `path` field, add the required key or fix the value |
| `source_dead` (3 failed rounds) | read `failures[]` from `myssia test --json`: new URL / adjust extract / step up an engine or proxy; comment the source out if it is hopeless |
| `source_degraded` (0 items / halved) | the page structure likely changed: rerun `myssia test --json`, fix the extract selectors |
| `env_ref_missing` | set the environment variable (e.g. `export FEISHU_CHAT_ID=...`) |
| `keychain_ref_missing` | store it with `myssia secret set <name>` |
| `keychain_name_noncanonical` | rename the reference to `myia/<scope>/<name>` and update the YAML |
| `store_error` | corrupt SQLite or a too-new schema version: switch `--db` or rebuild (history is lost) |
| plugin finding (v0.3) | a market plugin that cannot install or is unreachable degrades to a finding and **never blocks the core pipeline**; fix the `plugin:` section or reinstall per the message |
| `telegram_token_poll_conflict` | two or more categories share one bot token (`env:TELEGRAM_BOT_TOKEN`): Telegram answers concurrent `getUpdates` polls with 409 Conflict, and the product now enforces one poller per token in-library (the first resident process owns feedback receiving; later ones stay poll-idle, pushes unaffected); note that feedback for later resident categories lands in the first category's store — configure a separate bot token per category (another BotFather bot) or keep only one category resident if each needs its own feedback |

## Pre-flight checklist

1. `myssia test` passes (exit code 0 or 3, sources `ok: true`, field previews
   non-empty, no `dedup_key_error`).
2. `myssia doctor` reports an empty `findings`.
3. Every credential slot is a reference — no plaintext; `engine` values are
   legal; `dedup.key` does not contain `{title}`.
4. Categories outside the seven built-ins set `classify.builtin: false`
   (otherwise unmatched items are all dropped).
5. Want trend comparisons → add a `baseline:` section; want multi-source
   same-event merging → add an `aggregate:` section (syntax in the sidecar
   part of the [schema reference](schema.md)).

## Going further: plugin market and feedback loop

- **Scenario plugins** (v0.3, slimmed in v1.1): `myssia plugin list / install /
  remove`; a category YAML declares its dependency in the top-level `plugin:`
  section (`remote` endpoint + keychain token; the `local` mode declares a
  native install command only — plugins offer no docker mode, the repo carries
  no plugin deployment recipes; any already-deployed instance is wired in via
  `remote`); a plugin that cannot
  install never blocks the core pipeline (security baseline). The community
  directory lives in `plugins/community/README.md`.
- **Source-type / in-process plugins** (v1.1, desktop-first): official
  packages declare a `tier` (`desktop` default set / `remote` opt-in /
  `server-only`), shown by `myssia plugin list`. The `myssia-osint` sample pins
  upstream source as a git submodule under the plugin's `vendor/` directory
  (manifest gains optional `vendor:` / `adapter:` sections; unknown fields
  still fail fast), and an adapter shells out to the upstream CLI inside an
  isolated uv environment (deps fetched on demand, never into the root
  project) — `myssia osint https://example.com --json` runs one structured
  recon with zero Docker. `myssia-proxy` is the in-process counterpart: `myia
  proxy --json` fetches public free proxies and liveness-checks them in
  process (zero Redis, zero Docker). A missing upstream only degrades with
  structured errors and never blocks the core pipeline.
- **Feedback loop** (v0.3): negative feedback is stored —
  `myssia feedback list / stats / mark` — and maintenance retunes watchlist
  weights and thresholds automatically (prompt notes are recorded in the
  tuning history and surfaced in stats; Telegram/Feishu callback receivers
  ship now — in-card buttons land with the desktop UI).
- **Trend baseline and event aggregation** (v0.4): `baseline:` numeric
  history plus "vs yesterday / vs last week", `aggregate:` merging the same
  event across sources into one card ("see also N sources"); syntax in the
  [schema reference](schema.md).
