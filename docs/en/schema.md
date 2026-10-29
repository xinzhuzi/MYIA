# Schema Reference

> One intelligence category = one YAML file. This page lists every section's
> fields, values and defaults, field-for-field identical to
> `src/myssia/schema.py` (locked by `tests/test_docs.py` /
> `tests/test_skill_doc.py`); every `yaml` code block here is a complete,
> loadable category config — copy and run. For the tutorial-style guide see
> the [plugin guide](write-a-plugin.md).

## The twelve sections

| # | Section | Semantics |
|---|---|---|
| 1 | `id` | Category identifier, `[a-z0-9][a-z0-9_-]{0,63}`; used in storage and CLI output. |
| 2 | `name` | Human-readable category name (1-64 chars). |
| 3 | `schedule` | 5-field cron expression, e.g. `"0 9,21 * * *"` |
| 4 | `timezone` | IANA timezone (e.g. `Asia/Shanghai`); defaults to the system timezone. |
| 5 | `sources` | Fetch sources (at least 1) — detail in [sources](#sources). |
| 6 | `watchlist` | Relevance profile: keyword boost and mute; the baseline for the LLM relevance score. |
| 7 | `classify` | First funnel: built-in seven-category keyword scan (unmatched titles drop) and/or custom rules. Zero token. |
| 8 | `dedup` | Dedup key template; composite keys or `{url}` — never title fingerprints. |
| 9 | `enrich` | Second funnel: LLM precision scoring with batch/cache/budget rails. |
| 10 | `push` | Delivery channels; the `route` sub-section does threshold routing. |
| 11 | `push[].route` | Threshold routing: first match wins; rules referencing `score` stay dormant until scores are backfilled. |
| 12 | `storage` | Data lifecycle: retention and VACUUM cadence. |

Beyond these 12 sections there are six optional **sidecar sections** (they
do not grow the public 12-section contract and are validated at the load
entry point): `plugin:` (scenario plugin dual mode, v0.3), `baseline:`
(trend baseline, v0.4), `aggregate:` (event aggregation, v0.4), `images:`
(vision ring, 10-03), `timeouts:` (stage timeout caps, 10-12) and
`value_gate:` (first-run quiet + repush on change, 2026-10-18) — see
[sidecar sections](#sidecar-sections-v03v04).

## Vocabulary (enums, value-for-value with schema.py)

| schema constant | values |
|---|---|
| `ENGINES` | `auto` `direct_api` `static_html` `crawl4ai` `firecrawl` `scrapling` `stealth_browser` `llm_browser` `credhunter` `scraperapi` `zenrows` `reddit` `urlwatch` `searxng` `prompt` `store_report` `telegram` `tg_web` `wechat_local` `feishu` `vendor_market` |
| `PAGINATION_MODES` | `template` `selector` `scroll` |
| `EXTRACT_TYPES` | `list` `item` `json_path` `rss` |
| `BACKOFF_POLICIES` | `exponential` `linear` `none` |
| `PUSH_CHANNELS` | `feishu_card` `telegram` `ntfy` `dingtalk` `wecom` `weixin` `weixin-local` `webhook` `stdout` `bark` `apprise` `slack` `discord` `whatsapp_cloud` `line` `qqbot` `google_chat` `teams` `msgraph_webhook` `matrix` `mattermost` `irc` `simplex` `signal` `bluebubbles` `email` `sms` `homeassistant` `a2a` `yuanbao` `buzz` `photon` `raft` `macos_notify` |
| `ROUTE_MODES` | `immediate` `digest` `archive` |
| `ENRICH_SCORES` | `value` `relevance` `credibility` |
| `VACUUM_CADENCES` | `daily` `weekly` `monthly` `never` |
| `BASELINE_WINDOWS` | `day` `week` |
| `REQUIRES_TOKENS` | (empty — plugins declare no host capability) |
| `CREDENTIAL_KEY_SUFFIXES` | `cookie` `authorization` `token` `secret` `password` `passwd` `apikey` `session` |

## Credential reference syntax

- Only two forms: `env:VAR_NAME` (read from the environment at run time) or
  `keychain:myia/<scope>/<name>` (OS keychain: macOS Keychain / Windows
  DPAPI; the namespace is canonical, flat legacy names are refused at
  resolve time).
- An auth-scheme prefix round-trips: `Authorization: "Bearer env:AIPOCKET_TOKEN"`.
- A plaintext value under a credential-like key (key name containing a
  `CREDENTIAL_KEY_SUFFIXES` substring, case/hyphen-insensitive) is refused at
  load time (error type `credential_plaintext`). The rule covers
  `sources[].headers`, `post_body` and source-level extension parameters —
  the whole YAML document, not just headers.
- Credential values are never echoed and never logged;
  `myssia secret set myia/<scope>/<name>` stores one (value via a stdin pipe
  or a hidden prompt).
- `enrich.base_url` / `enrich.api_key` / `push[].target` must be **pure**
  references (no auth-scheme prefix).

## The `when` expression whitelist (shared by classify.rules and push.route)

- Literals: string/number/bool/null; a name reads an item field (missing
  fields read as null).
- Containers: `[a, b]` lists; operators `+ - * / // % **`, comparisons
  `== != < <= > >= in not in` (chainable), `and` / `or` / `not`.
- Function whitelist (positional args only): `abs` `min` `max` `round` `len`
  `int` `float` `str`, plus `match(pattern, text)` — a whitelisted
  `re.search` (case-insensitivity via an inline `(?i)` flag carried by the
  pattern itself; quantified-signal gate precedent, 10-24-huawei-ai-news-absorb).
- Forbidden: attribute access, subscripts, lambda, f-strings, comprehensions,
  any call outside the whitelist.
- Parsed by an AST whitelist at load time — arbitrary code is **never eval'd**.
- Examples: `"abs(change_pct) >= 3"`, `"5090 in title"`,
  `"category in ['freebie', 'proxy-node']"`.

## Fields, section by section

### Root (id / name / schedule / timezone)

| Field | Default | Semantics |
|---|---|---|
| `id` | required | Category identifier, used in storage and CLI output |
| `name` | required | Display name (1-64 chars) |
| `schedule` | required | 5-field cron expression |
| `timezone` | `null` | IANA timezone; defaults to the system timezone |

### sources

| Field | Default | Semantics |
|---|---|---|
| `name` | required | Source name (1-64 chars, unique within the plugin; doctor/test locate by it) |
| `engine` | `auto` | Engine name (see vocabulary; `auto` walks the degrade chain, the winner is recorded in SQLite hints, never in your YAML). Under `auto`, when L2 static extraction yields 0 items, sources **without a hint yet** (first encounter) get one L3 (crawl4ai) browser probe — JS-rendered empty shells are caught this way; a zero-result probe locks the source back to L2 empty-page semantics, at most 3 probes per run. Sources already locked to L2 are unaffected; the manual re-probe escape is an explicit `engine: crawl4ai`. `credhunter` is an **off-chain source engine**: it never fetches the url — the in-process scenario plugin myssia-credhunter assembles items itself (GitHub artifact hunting / FOFA-Shodan exposure / local text triage); it never joins the auto chain and only takes effect when selected explicitly. Credentials arrive via `engine_options.credhunter.{github_tokens,fofa_apikey,shodan_apikey}` references; keyless exposure lanes degrade to an explicit empty state |
| `url` | required | http(s) address; supports `{placeholder}` templates (`{page}` paging, `{symbol}` fan-out) |
| `method` | `GET` | `GET` / `POST`; POST requires `post_body`, GET forbids it |
| `post_body` | `null` | POST form/JSON body (mapping); credential keys are plaintext-refused like headers |
| `headers` | `{}` | Request headers; credential-key values must be references; do not fake a browser UA (the default UA is the honest `世事/0.1 (...)`) |
| `pagination` | `null` | Paging config, see table below |
| `extract` | `null` | Field extraction, see table below; empty → L3+ engines auto-structure. Additionally, with the `trafilatura` extras installed and the fallback switch on, L2 `static_html` falls back to single-page article extraction for rule-less sources and rule-empty (non-`rss`) revamps (single item with `metadata.extract_provenance="trafilatura"`; degenerate list-page output under 120 characters of body text is stopped by the quality gate and stays zero-item; rule-hitting sources are never touched). Switch precedence = **source-level `engine_options.static_html.extract_fallback: true/false` > global env `MYIA_EXTRACT_FALLBACK=1` > off by default** (with the source-level key unset, behavior is byte-identical to the global setting) |
| `rate_limit` | `see rate_limit table` | Politeness limits (throttling is enforced once, in the engine layer) |
| `proxy` | `direct` | `direct` / `pool:<name>` / `residential:<region>`; pools need a global `--config` |
| `retry` | `3` | Transient-error retry budget (0-10) |

**Source-level extension parameters**: unknown keys (e.g.
`symbols: [NVDA, AAPL]`) pass through to the engine — `{symbol}` in the URL
fans out per value (one request each); `engine_options.<engine>` is the
engine knob namespace (e.g. `engine_options.firecrawl.endpoint`,
`engine_options.scrapling.backend`,
`engine_options.stealth_browser.max_pages`,
`engine_options.static_html.extract_fallback` (per-source trafilatura
fallback switch, boolean; precedence = source > global `MYIA_EXTRACT_FALLBACK`
> off by default; a non-boolean value is structurally refused at fetch time
with `invalid_engine_options`); `engine_options.crawl4ai`
carries `timeout` (per-page budget in seconds, default 60s), `headless`
(default `true`) and the two passthrough maps `browser_options` /
`run_options` — merged into crawl4ai's `BrowserConfig` /
`CrawlerRunConfig`, opening the rest of its config surface; a passthrough
map may not carry the engine-owned keys (browser side `headless` / `proxy`
/ `proxy_config` / `headers`, run side `cache_mode` / `page_timeout` —
proxy parsing, credential masking, cache bypass and the budget rail are
single-sourced by the engine), a collision fails the fetch with the
structured error `invalid_browser_options` / `invalid_run_options`).
Credential-like keys inside extension parameters are plaintext-refused as
well.

**Source-level detail-text fetch** (`detail_text: true`, a flat parameter;
companion `detail_text_max` defaults to `10`): for **title-only list sources**
(items with just title + link and an empty `content`), fetch each item's
detail page at the tail of the fetch stage, **through the source's own
engine** (explicit `engine:` required; a firecrawl source cloud-renders past
Cloudflare), and backfill the returned markdown into `item.content`
(truncated to 4000 chars) — visible from classify word rules all the way to
push routing, so welfare posts whose `sk-` key lives in the post body can
pass the url+key hard gate. Only **fresh items** are fetched (dedup key
unseen; already-seen items skip silently, so cloud-render credits are never
re-burned per run); serial with ≥2s spacing per request, a per-source
independent budget and a 240s wall clock for the whole pass. Any failure
only writes `metadata.detail_text_status` (`failed:<reason>` / `empty` /
`skipped:engine`) and never blocks the pipeline; unset = the whole pass is
skipped (zero-impact default).

**Prompt-task engine** (`engine: prompt`, off-chain): define a monitor in
one sentence — `engine_options.prompt` carries `instructions` (the task) and
`urls` (targets, default `[source url]`); the engine fetches every URL
through the shared politeness layer (trafilatura for HTML pages,
feedparser for XML feeds), feeds them to **one** OpenAI-compatible LLM call
(`base_url` / `api_key` as required references, same contract as the
`enrich` section), and the markdown answer becomes the item `content` pushed
out (template key `{{ item.prompt_summary }}`); the item URL carries a
`#prompt-<date>` anchor so each day is a new item and same-day reruns dedupe.
All failures are structured (credentials / dependencies / fetch / LLM);
partial URL failures degrade and continue. See
`config/ai-vendor-watch.example.yaml`; extras `myssia[trafilatura]` +
`myssia[llm]` required.

### Global config pools (proxy pools, `--config`)

A source's `proxy: pool:<name>` references a pool declared in the global
config (injected via `--config`; run/test/doctor share one loader).
Credentials are only ever `env:` / `keychain:` references — plaintext is
refused at load time; all sources on one pool share a single pool transport:

```yaml
pools:
  main: "http://env:MYIA_PROXY_MAIN@proxy.example.com:8080"   # single upstream (v0.2 string form)
  rotating:                                                    # multi-upstream pool (v1.2)
    upstreams:
      - "http://env:MYIA_PROXY_A@p1.example.com:8080"
      - "socks5://keychain:myia/proxy/b@p2.example.com:1080"
    max_failures: 3          # optional; consecutive-failure removal threshold, default 3; must be ≥1
    probe_interval: 300      # optional; half-open recovery interval (seconds), default 300; must be ≥1
```

Runtime semantics (one engine for both forms): requests rotate across
upstreams in declaration order; a transport failure immediately moves to the
next healthy upstream (spending the source's retry budget). Each upstream
counts consecutive transport failures — reaching `max_failures` removes it;
after `probe_interval` the next traffic naturally triggers a half-open trial
(success reinstates, failure re-removes; no background prober). When every
upstream is removed the pool circuit-breaks: further requests **fail fast
with zero network** (`proxy_pool_exhausted`, short-circuiting the degrade
chain like the other `proxy_*` classes), and `myssia doctor --config` probes
every upstream of every pool in parallel (single-upstream pools merely gain
the additive fields `upstream_index`/`upstreams`). **Behavior change (v1.2
disclosure)**: string (single-upstream) pools move from "each source retries
on its own" to "3 consecutive failures remove the upstream → the pool
circuit-breaks and fails fast" — YAML compatibility means it still loads and
serves as-is; the failure path is deliberately not byte-identical (a dead
upstream no longer burns every source's retry budget).

pagination:

| Field | Default | Semantics |
|---|---|---|
| `mode` | `template` | `template` = iterate `{page}` in the URL; `selector` = follow next-page links; `scroll` = infinite scroll (L4 `scrapling` only) |
| `max_pages` | `1` | Page cap (1-10000); stops early on an empty page or an unchanged fingerprint |
| `selector` | `null` | Next-page selector for `mode: selector` (required in that mode) |

extract:

| Field | Default | Semantics |
|---|---|---|
| `type` | required | `list` = repeated HTML items; `item` = single page; `json_path` = JSON API; `rss` = RSS/Atom feed (feedparser entry mapping) |
| `item` | `null` | Per-item container CSS selector for `type: list` (required there; forbidden elsewhere) |
| `url_template` | `null` | Per-item URL template, plain `{field}` placeholders (at least one, and each must name a key of `fields` — load-time cross-check, typos are refused), rendered into `url` at the extraction outlet; allowed on `list`/`json_path` only (refused on `item` single-page extracts — the item URL is the request URL; also refused on `rss`, whose item URL is the `fields.url`←`entry.link` mapping); a placeholder field missing its *value* on one element → empty url for that item, which the pipeline then rejects as `invalid_item` (never stored with a broken link); either/or with the `url` field — both declared = the `url` field wins, the template is silently unused |
| `fields` | required | field name → selector/JSONPath, at least 1; `list`/`json_path` **must include `url` or declare `url_template`** (either/or); for `rss` the **values must be feedparser entry attribute names from the whitelist `title`/`link`/`published`/`updated`/`summary`/`author`** (keys = normalized field names, typos are refused at load time) and `url` **must be present** (mapping `link`) |

Selector grammar: L1/L2 use CSS (relative to the item; `a@href` reads an
attribute; relative URLs resolve against the page); `json_path` uses `$`
paths (`$.chart.result[0].meta.price`, `$[*].keyword` wildcards). APIs whose
payload has no page URL, only a slug or numeric id (the Epic/Steam shape),
render item links via `url_template` (`config/games.example.yaml` does
exactly this). Known gap: `json_path` cannot express "item URL = the request
URL" — such APIs use a stable business field as `url`
(`config/stocks.example.yaml` does exactly this).

`rss` (10-03-news-rss), field by field:

- **Mapping shape**: `fields` keys are normalized field names, values are
  feedparser entry attribute names (whitelist `title`/`link`/`published`/
  `updated`/`summary`/`author`); the typical form is
  `{title: title, url: link, published: published}`
  (`config/news.example.yaml`).
- **Whitelist**: a closed vocabulary — a typo (e.g. `pubdate`) is refused at
  load time (`invalid_rss_field`, path points at `fields.<key>`) instead of
  silently yielding zero items at run time.
- **Missing attributes are omitted per entry** (same per-entry semantics as
  `json_path`); `published` is an RFC 822 date string
  (`Fri, 02 Oct 2026 20:01:17 +0800`), sliced for display in templates.
- **`url` required**: `fields` must carry `url` (mapping `link`, the dedup
  key's foundation); both the `item` selector and `url_template` are mutually
  exclusive with `rss` (declaring either is refused).
- **Engine boundary**: `rss` rides the `static_html` text path only;
  `direct_api` stays JSON-only (an `rss` extract on it is refused at the
  engine layer so `engine: auto` degrades down the chain correctly).
- **CDATA pitfall**: `summary` is CDATA-wrapped HTML — pushing it raw into a
  template floods the card. The whitelist keeps the attribute, but the
  official template sticks to title+link+date; route summaries through
  `enrich` when they are needed.

rate_limit:

| Field | Default | Semantics |
|---|---|---|
| `qps` | `0.5` | Requests-per-second cap (0 < qps ≤ 1000); rate limiting is a default, not an option |
| `jitter` | `0.0` | Random jitter seconds (number or duration string `"2s"` / `500ms`) |
| `backoff` | `exponential` | 429/5xx backoff: `exponential` / `linear` / `none` |
| `respect_robots` | `true` | Respect robots.txt; setting `false` requires a YAML comment justifying it (a data API, not page scraping) |

### watchlist

| Field | Default | Semantics |
|---|---|---|
| `keywords` | `[]` | Keywords (1-64 chars each); matching items get a relevance boost — the LLM relevance baseline and the `keyword_trends` mention-tracking scope |
| `mute` | `[]` | Mute words; a match demotes/archives |

### classify

| Field | Default | Semantics |
|---|---|---|
| `builtin` | `true` | Built-in seven-category keyword scan (credit-card / proxy-node / buying-agent / server / token / ai-news / freebie); **unmatched items are dropped** (skip reason `classify_unmatched`); set `false` when the category fits none of the seven |
| `rules` | `[]` | Custom rules: `name` (required) + `when` (required, whitelist expression) + `tag` (≤64 chars); `builtin: false` with no rules = pass everything |

### dedup

| Field | Default | Semantics |
|---|---|---|
| `key` | `{url}` | Dedup key template; at least one placeholder; **`{title}` permanently forbidden**; placeholders must render from `extract.fields` or the reserved fields (`{url}` `{source}` `{category}` `{scores}` `{date}` `{slot}`) |

Composite key example: `"{symbol}-{date}-{slot}"` (one entry per symbol per
slot, fresh each day) — a bare `{symbol}` is a forever key: after the first
round the category stays permanently silent.

### enrich

| Field | Default | Semantics |
|---|---|---|
| `enabled` | `false` | LLM scoring switch; when on, `base_url`/`api_key` must be valid (structured `missing_base_url`/`missing_api_key` errors) |
| `model` | `glm-5.3-flash` | Model name (any OpenAI-compatible endpoint) |
| `scores` | `[value, relevance, credibility]` | Score dimensions (0-10 each), backfilled as the item `score` for routing |
| `batch` | `8` | Items scored per batch (1-1000); default lowered 20→8 (2026-10-29 rate-limit incident: smaller blast radius traded for request count) |
| `cache` | `true` | Per-URL score cache — a URL is never scored twice |
| `budget_per_run` | `50000` | Per-run token budget guardrail; exhausted → keyword-only for the rest of the run, with a WARNING |
| `base_url` | `null` | OpenAI-compatible endpoint, **a pure `env:`/`keychain:` reference only** (世事 has no built-in endpoint), e.g. `env:MYIA_LLM_BASE_URL` |
| `api_key` | `null` | API key, same, e.g. `env:MYIA_LLM_KEY` (世事 has no default key) |

Real calls also need the optional extra: `uv sync --extra llm` (missing →
structured `dependency_missing` and fallback to keyword-only scoring).

### push

| Field | Default | Semantics |
|---|---|---|
| `channel` | required | `feishu_card` / `telegram` / `ntfy` / `dingtalk` / `wecom` / `webhook` / `stdout` / `bark` (iOS push) / `apprise` (universal push; needs the `myssia[apprise]` extra) |
| `target` | `null` | Push target, a **pure** `env:`/`keychain:` reference; forbidden on `stdout`, required elsewhere |
| `route` | `[]` | Threshold routing (table below); empty = the seven-category default mapping (freebie/node/buying-agent → immediate, others → digest) |
| `template` | `null` | Jinja2 card template (sandboxed render; syntax errors refused at load); omit for the channel's built-in layout |
| `timeout` | `10.0` | Send timeout seconds (**`webhook` only**; configured elsewhere → refused) |
| `retries` | `2` | Send retry count (`webhook` only) |
| `retry_backoff_seconds` | `1.0` | Send retry backoff seconds (`webhook` only) |
| `ntfy_token` | `null` | Optional ntfy auth token reference (value = Bearer token or `user:pass` → Basic); omitted with `env:NTFY_TOKEN` unset = no auth (`ntfy` only) |
| `dingtalk_secret` | `null` | Optional DingTalk signing secret reference (set = HMAC-SHA256 signed; omitted = bare webhook) (`dingtalk` only) |
| `wecom_corpid` / `wecom_corpsecret` / `wecom_agentid` | `null` | WeCom self-built-app credential references; omitted falls back to env `WECOM_CORPID`/`WECOM_CORPSECRET`/`WECOM_AGENTID` (`wecom` only) |
| `bark_endpoint` | `null` | Bark (iOS push) server endpoint; **not a credential**, safe in YAML: unset = the official service `https://api.day.app`, self-hosted = host address (e.g. `http://<host>:8080`); must be http(s) (`bark` only) |
| `wechat_bridge_bin` | `null` | Path override for the managed wechat-bridge component binary (default `~/.myia/plugins/wechat-bridge/bin/wechat-bridge`); a local path, **not a credential** — no env:/keychain: reference (`weixin-local` only) |
| `weixin_local_enabled` | `null` | Explicit enable switch for the local WeChat bridge: a `weixin-local` entry must carry `true` explicitly to load (default off — same-machine convenience; the primary outbound = the `weixin` official bridge); off = channel not registered (`weixin-local` only) |

route rules:

| Field | Default | Semantics |
|---|---|---|
| `when` | required | Threshold expression, evaluated in declaration order, **first match wins**; rules referencing `score` stay dormant until scores are backfilled |
| `mode` | required | `immediate` (push now) / `digest` (join the AM/PM digest) / `archive` (store only) |

The usual three tiers: `score >= 8` → immediate; `score >= 5` → digest;
`score < 5` → archive. A score with no matching rule → conservative digest;
no route at all with a score → immediate. Template context: `items` (item
list, fields from extract), `date`, `slot`, `category`, `count`; per-channel
credential conventions in the [plugin guide](write-a-plugin.md).

### storage

| Field | Default | Semantics |
|---|---|---|
| `retention` | `90d` | Retention `<n>d` / `<n>w`; expired items purge automatically |
| `vacuum` | `monthly` | SQLite VACUUM cadence: `daily` / `weekly` / `monthly` / `never` |

## Sidecar sections (v0.3/v0.4)

Four optional top-level sections, **not part of the 12-section public
contract**: validated at the load entry point by `load_category` (error
paths prefixed `$.plugin` / `$.baseline` / `$.aggregate` / `$.images`;
`null` treated as absent) and attached to the matching `CategoryConfig`
attribute. Any sidecar validation failure refuses the whole YAML (exit
code 1).

### plugin: scenario plugin dual mode (v0.3)

Declares that this category depends on a market plugin (installed via
`myssia plugin install`) for its service. **A plugin that cannot install, is
misconfigured, or whose remote is unreachable never blocks the core
pipeline** — it degrades to a structured finding and the category keeps
running (security-baseline rule).

| Field | Default | Semantics |
|---|---|---|
| `id` | required | Plugin id (lowercase letters/digits/hyphens/underscores, alphanumeric first; convention `myssia-<name>`) |
| `requires` | `[]` | Host-capability vocabulary (**currently empty** — the plugin docker mode is gone, the `docker` token is retired, any non-empty value is refused); string or list both accepted |
| `modes` | required | At least one mode: `local` (a native install command `install`; zero Docker on this machine) or `remote` (endpoint required; token **must** be a `keychain:myia/<scope>/<name>` reference — even `env:` is refused). Plugins offer no docker mode (final ruling 2026-10-06) |

```yaml
id: site-watch
name: Page change watch
schedule: "*/15 * * * *"
timezone: Asia/Shanghai
plugin:                           # scenario plugin declaration (official packages: remote opt-in since v1.1)
  id: myssia-monitor
  requires: []
  modes:
    remote:                       # point at an already-deployed instance (desktop: zero Docker); the local side accepts a native install command only — plugins offer no docker mode
      endpoint: https://my-monitor.example.com
      token: keychain:myia/monitor/token    # myssia secret set myia/monitor/token
sources:
  - name: watch-api
    engine: direct_api
    url: "https://my-monitor.example.com/api/v1/watch"
    headers:
      X-Api-Key: "keychain:myia/monitor/token"
    extract:
      type: json_path
      fields:
        title: "$[*].label"
        url: "$[*].url"
push:
  - channel: feishu_card
    target: env:FEISHU_CHAT_ID
```

### baseline: trend baseline (v0.4)

Declares which numeric fields get per-item history snapshots; push templates
consume comparison text via sandbox functions; `watchlist.keywords` doubles
as the keyword mention-tracking scope.

| Field | Default | Semantics |
|---|---|---|
| `enabled` | `false` | Switch; when on, `fields` needs at least 1 |
| `fields` | `[]` | Numeric fields (names produced by the source extract, e.g. `price`), snapshotted per (category, item, field) |
| `windows` | `[day, week]` | Comparison windows: `day` = vs yesterday, `week` = vs last week |
| `msrp` | `{}` | Optional MSRP table (public suggested retail prices; keys are product-name substrings, values > 0) |

Template side: the three sandbox functions `vs_yesterday(item, 'price')` /
`vs_last_week(item, 'price')` / `vs_msrp(item)` plus the `keyword_trends`
context (weekly keyword mention change). Contract difference: the field of
`vs_yesterday` / `vs_last_week` is an argument (any numeric field), while
`vs_msrp(item)` takes **no field argument and only ever reads the item's
`price` field** — a price field named anything else (or non-numeric) makes
`vs_msrp` render an empty string.
Numeric history is kept for
`2 × storage.retention` so the week-over-week window stays complete.

```yaml
id: gpu-prices-lite
name: GPU prices (lite)
schedule: "0 10 * * *"
sources:
  - name: price-list
    engine: static_html
    url: "https://detail.example.com/vga/{page}.html"
    pagination:
      mode: template
      max_pages: 5
    extract:
      type: list
      item: "div.list-item"
      fields:
        title: "h3 a"
        url: "h3 a@href"
        price: "span.price-type"    # the numeric field baseline.fields depends on
dedup:
  key: "{url}"
baseline:
  enabled: true
  fields: [price]
  windows: [day, week]
  msrp:
    "RTX 5090": 16499
    "RTX 5080": 8299
push:
  - channel: stdout
    template: |
      **GPU prices · {{ date }}**
      {% for item in items %}
      - [{{ item.title }}]({{ item.url }}) ¥{{ item.price }} {{ vs_msrp(item) }} {{ vs_yesterday(item, 'price') }} {{ vs_last_week(item, 'price') }}
      {% endfor %}
      {% if keyword_trends %}
      **Keyword mentions WoW**:{% for t in keyword_trends %}{{ t.word }} {{ t.count }} ({{ t.change_text }}) {% endfor %}
      {% endif %}
```

### aggregate: event aggregation (v0.4)

When several sources report the same event, they merge into one pushed card
(the primary item plus a "see also N sources" list); it runs after dedup
(same URL/composite key — an orthogonal layer) and before push.

| Field | Default | Semantics |
|---|---|---|
| `enabled` | `false` | Switch |
| `window_hours` | `24.0` | Same-event time window (hours, > 0): two candidates with parseable timestamps further apart than the window are different events (zero token) |
| `similarity_threshold` | `0.6` | Title-similarity coarse-screen threshold (character shingle Jaccard, 0 < x ≤ 1): only candidates above it reach the LLM confirmation |

Two-level dedup: a local zero-token coarse screen picks candidate groups →
the LLM confirms (inside enrich's batch/cache/budget rails). **Endpoint
settings are shared with the `enrich:` section** — `aggregate.enabled: true`
requires enrich's `base_url`/`api_key` to be valid (and `--extra llm`
likewise); budget is consumed jointly with enrich — whichever hits the cap
first degrades. When enabled, `myssia run --json`'s `stages[]` gains an
`aggregate` stage.

```yaml
id: ai-news-merged
name: AI news (event-merged)
schedule: "0 8,20 * * *"
timezone: Asia/Shanghai
sources:
  - name: feed-a
    engine: static_html
    url: "https://news-a.example.com/latest"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
watchlist:
  keywords: [LLM, agent]
dedup:
  key: "{url}"
enrich:
  enabled: true
  base_url: env:MYIA_LLM_BASE_URL
  api_key: env:MYIA_LLM_KEY
aggregate:
  enabled: true
  window_hours: 24
  similarity_threshold: 0.6
push:
  - channel: stdout
```

### images: vision ring & table restore

Category-level image processing (the vision-ring sidecar section): when
enabled, the fetch tail runs each item's images through *download (SSRF
private-net refused / magic-byte whitelist / streaming 10MB cap / 10s
timeout) → optional table restore → local OCR → optional VL caption*, and
the results land on `metadata.image_ocr` / `image_caption` / `tables` /
`image_status` markers, feeding analyze/enrich scoring and push templates.
**Any failure only writes a marker — the pipeline is never blocked**;
disabled (or absent) means the whole ring never runs (zero-impact default).

Where image URLs come from: an extract `fields` entry like
`image: img@src` (the fetch side urljoins `src` attributes), or same-origin
image links collected from markdown when an L3+ engine runs without
`extract` (cross-origin ads/tracking pixels are skipped). Source-level flat
parameters `images_enabled` / `images_max_images` / `images_min_bytes` /
`images_vl` / `images_ocr_engine` / `images_detail_fetch` /
`images_detail_max_items` override the category section (`max_per_run` /
`persist` / `table` are global semantics — no source-level override).

| Field | Default | Semantics |
|---|---|---|
| `enabled` | `false` | Vision-ring master switch; off = the section never runs |
| `max_images` | `3` | Per-item cap (images, 1-10); the rest is silently truncated |
| `max_per_run` | `30` | Per-run total cap (VL-duration hard gate; when exhausted items get `skipped:run_limit`) |
| `min_bytes` | `10240` | Images below this size are treated as icons/tracking pixels and skipped |
| `vl` | `off` | Vision-caption channel: `off` = OCR only (zero VL cost) / `local` = local OpenAI-compatible endpoint (vision.yaml `local`) / `cloud` = cloud vision model (tokens share the enrich budget pool) |
| `ocr_engine` | `null` | OCR engine override (`vision` / `rapidocr`); default follows vision.yaml's `ocr.engine_default` |
| `detail_fetch` | `false` | Detail-page backfill: for imageless items, fetch their detail pages in pipeline order and feed same-origin `<img>` into the same ring |
| `detail_max_items` | `10` | Per-run backfill cap (items, 1-50; serial with ≥1s spacing; the source-level override is that source's own budget) |
| `persist` | `false` | Persist images that pass the download gates to `MYIA_HOME/images/<sha16>.<ext>` (content-addressed); metadata gains `image_files` / `image_ocr_lines` |
| `table` | `false` | Table restore: run rapid_table structure recovery on downloaded images; restored tables land in `metadata.tables = [{markdown, rows, cols}]` (GFM, push cards can embed them directly); a missing/failed engine only writes `metadata.table_status = "table_provider_error"` — never blocks the pipeline. No source-level `images_table` override |

Division of labor: `table` handles table *structure* (rows/columns), `vl`
handles whole-image *semantic* description — neither replaces the other.

**Installing the table-restore component** (a heavy dependency, kept out of
the core): CLI-side `uv sync --extra table` (or
`pip install "myssia[table]"`); on the desktop, flip the "table restore"
component switch under Settings → Python environment to install it into the
managed environment (mirror overrides apply; the SLANET-plus structure
model auto-downloads 6.8MB from modelscope on first use, then works
offline). With the component missing the category still runs — only table
restore degrades silently; `myssia doctor` reports a
`table_dependency_missing` warning (with the install command) for
categories declaring `images.table: true`.

```yaml
id: gpu-shots
name: GPU price screenshots
schedule: "0 10 * * *"
timezone: Asia/Shanghai
sources:
  - name: price-shots
    engine: static_html
    url: "https://detail.example.com/vga/"
    extract:
      type: list
      item: "div.list-item"
      fields:
        title: "h3 a"
        url: "h3 a@href"
        image: "img@src"          # image source for the ring: extract fields collect <img> addresses
images:
  enabled: true
  max_images: 2
  vl: "off"                       # bare off/on parse as YAML booleans — quote enum values
  table: true                     # restore price tables in screenshots to GFM, into metadata.tables
push:
  - channel: stdout
```

### value_gate: first-run quiet & repush on change

Category-level value-gate declaration (the sixth sidecar section): with both
flags at their `false` defaults the whole section is a no-op (behavior is
field-for-field identical to the section being absent); it is validated at
the load entry point (error paths carry the `$.value_gate` prefix;
`value_gate: null` counts as absent).

| Field | Default | Meaning |
|---|---|---|
| `first_run_quiet` | `false` | `true` = the first time a source is met, record the backlog quietly: first-fetch items are stored but kept out of the scoring/push stream. Items that would go out immediately anyway (route preview hits an immediate channel, e.g. freebie/deal categories) are not swallowed — they still push on the first run. There is no score on the first run, so score-gated immediate routes stay dormant; those items fold into the digest instead (store_report section `first_run: fold`) and get re-reported once they change. Only fires for brand-new sources or a brand-new store; swallowed items are counted in `report.skips["first_run_quiet"]`. |
| `repush_on_change` | `false` | `true` = when an item's content changes (fingerprint = normalized title + body; list/RSS sources without body degrade to title-change detection), it is worth reporting again next slot: a new row is stored under the same key (the new row inherits read/starred state) and the digest window naturally picks it up by first_seen. Legacy items have no fingerprint and are never repushed; only items stored after the flag is on are covered. |

Prerequisite signal: item `metadata["fetch_change_reason"]` (values
`first_fetch` / `validators_differ` / `hash_differ`) is stamped by the
static_html engine (including the rss extraction sub-mode); `engine: auto`
carries the signal only when L2 wins — after degrading to firecrawl there is
no marker and no suppression; standalone engines (prompt/urlwatch/telegram,
…) are not covered (no marker = unaffected by first-run suppression).

```yaml
id: ai-news
name: AI资讯
schedule: "0 8,20 * * *"
timezone: Asia/Shanghai
sources:
  - name: vendor-blog
    engine: static_html
    url: "https://blog.example.com/rss.xml"
    extract:
      type: rss
      fields: {title: title, url: link, published: published}
value_gate:
  first_run_quiet: true     # first meeting with a source: store quietly, no push
  repush_on_change: true    # content changed (title or body) → report again next slot
push:
  - channel: stdout
```

Install & verification prerequisites (never really push against the
production group target): ① before swapping the build, pause the matching
categories in `myssia cron` and quit the desktop app (cron spawns `myssia
run` child processes; half-old half-new code must not run), and restore them
afterwards; ② for a live-send verification use a throwaway category config
(private open_id target + a standalone url anchor), clear of the harvest
slot windows, and delete it right after; ③ bind verification to the
`report.skips["first_run_quiet"]` count and the
`metadata["fetch_change_reason"]` field — never to LLM scores (score jitter
pollutes such assertions); ④ verify first-run suppression with a temporarily
added source or a fresh store (existing sources already have a baseline and
will not trigger — that is expected, not a broken flag).

## Load-time errors (structured)

A failed load raises `LoadError`: **all** errors are reported in one pass,
each carrying a field path (JSONPath style, e.g.
`$.sources[0].rate_limit.qps`) + a machine error type + a human message in
Chinese; `myssia doctor --json` emits the same shape. Exit code 1. Common
error types:

| error_type | meaning |
|---|---|
| `yaml_parse_error` / `invalid_encoding` | YAML syntax broken / file is not UTF-8 (duplicate keys are refused too) |
| `invalid_root` | top level is not a mapping, or the file is empty |
| `credential_plaintext` | a credential-like key carries a plaintext value (use an `env:` / `keychain:` reference) |
| `unknown_field` | unknown field (fail-fast; a probable typo of a known field inside `sources[]` is reported too) |
| `missing_field` | a required field is missing |
| `invalid_value` | value outside the enum vocabulary (the legal values are listed) |
| `title_fingerprint_forbidden` | `dedup.key` uses `{title}` |
| `invalid_dedup_key` | the dedup key has no placeholder, or a placeholder cannot be rendered by any source |
| `missing_url_field` | `extract.fields` lacks `url` and no `url_template` is set (`list`/`json_path` need one of the two; `rss` must carry `url`) |
| `invalid_url_template` / `unexpected_url_template` | `url_template` has no placeholder or names a field outside `fields` / set on an `item` single-page extract or an `rss` extract |
| `invalid_rss_field` | an `rss` `fields` value is outside the feedparser entry attribute whitelist (typos are refused) |
| `unexpected_transport_field` | `timeout`/`retries`/`retry_backoff_seconds` on a non-webhook channel |
| `unexpected_platform_field` | a W2 platform credential field (`ntfy_token`/`dingtalk_secret`/`wecom_*`) on a non-host channel |

## Consistency guarantees

- `tests/test_skill_doc.py`: SKILL.md field/enum tables are checked item by
  item against the pydantic models; the skill and the docs cross-reference
  each other.
- `tests/test_docs.py`: zh/en page structures align; every `yaml` code block
  in docs loads through `load_category`; examples carry zero plaintext
  credentials; in-page relative links resolve.
- A schema change must update four places in lockstep: schema.py, SKILL.md,
  docs, and the plugin examples — the tests above turn drift into a red test
  instead of a silently misleading doc.
