# Schema Reference

> One intelligence category = one YAML file. This page lists every section's
> fields, values and defaults, field-for-field identical to
> `src/myia/schema.py` (locked by `tests/test_docs.py` /
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

Beyond these 12 sections there are three optional **sidecar sections** (they
do not grow the public 12-section contract and are validated at the load
entry point): `plugin:` (scenario plugin dual mode, v0.3), `baseline:`
(trend baseline, v0.4) and `aggregate:` (event aggregation, v0.4) — see
[sidecar sections](#sidecar-sections-v03v04).

## Vocabulary (enums, value-for-value with schema.py)

| schema constant | values |
|---|---|
| `ENGINES` | `auto` `direct_api` `static_html` `crawl4ai` `firecrawl` `scrapling` `stealth_browser` `llm_browser` |
| `PAGINATION_MODES` | `template` `selector` `scroll` |
| `EXTRACT_TYPES` | `list` `item` `json_path` |
| `BACKOFF_POLICIES` | `exponential` `linear` `none` |
| `PUSH_CHANNELS` | `feishu_card` `telegram` `webhook` `stdout` |
| `ROUTE_MODES` | `immediate` `digest` `archive` |
| `ENRICH_SCORES` | `value` `relevance` `credibility` |
| `VACUUM_CADENCES` | `daily` `weekly` `monthly` `never` |
| `BASELINE_WINDOWS` | `day` `week` |
| `REQUIRES_TOKENS` | `docker` |
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
  `myia secret set myia/<scope>/<name>` stores one (value via a stdin pipe
  or a hidden prompt).
- `enrich.base_url` / `enrich.api_key` / `push[].target` must be **pure**
  references (no auth-scheme prefix).

## The `when` expression whitelist (shared by classify.rules and push.route)

- Literals: string/number/bool/null; a name reads an item field (missing
  fields read as null).
- Containers: `[a, b]` lists; operators `+ - * / // % **`, comparisons
  `== != < <= > >= in not in` (chainable), `and` / `or` / `not`.
- Function whitelist (positional args only): `abs` `min` `max` `round` `len`
  `int` `float` `str`.
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
| `engine` | `auto` | Engine name (see vocabulary; `auto` walks the degrade chain, the winner is recorded in SQLite hints, never in your YAML) |
| `url` | required | http(s) address; supports `{placeholder}` templates (`{page}` paging, `{symbol}` fan-out) |
| `method` | `GET` | `GET` / `POST`; POST requires `post_body`, GET forbids it |
| `post_body` | `null` | POST form/JSON body (mapping); credential keys are plaintext-refused like headers |
| `headers` | `{}` | Request headers; credential-key values must be references; do not fake a browser UA (the default UA is the honest `MYIA/0.1 (...)`) |
| `pagination` | `null` | Paging config, see table below |
| `extract` | `null` | Field extraction, see table below; empty → L3+ engines auto-structure |
| `rate_limit` | `see rate_limit table` | Politeness limits (throttling is enforced once, in the engine layer) |
| `proxy` | `direct` | `direct` / `pool:<name>` / `residential:<region>`; pools need a global `--config` |
| `retry` | `3` | Transient-error retry budget (0-10) |

**Source-level extension parameters**: unknown keys (e.g.
`symbols: [NVDA, AAPL]`) pass through to the engine — `{symbol}` in the URL
fans out per value (one request each); `engine_options.<engine>` is the
engine knob namespace (e.g. `engine_options.firecrawl.endpoint`,
`engine_options.scrapling.backend`,
`engine_options.stealth_browser.max_pages`). Credential-like keys inside
extension parameters are plaintext-refused as well.

pagination:

| Field | Default | Semantics |
|---|---|---|
| `mode` | `template` | `template` = iterate `{page}` in the URL; `selector` = follow next-page links; `scroll` = infinite scroll (L4 `scrapling` only) |
| `max_pages` | `1` | Page cap (1-10000); stops early on an empty page or an unchanged fingerprint |
| `selector` | `null` | Next-page selector for `mode: selector` (required in that mode) |

extract:

| Field | Default | Semantics |
|---|---|---|
| `type` | required | `list` = repeated HTML items; `item` = single page; `json_path` = JSON API |
| `item` | `null` | Per-item container CSS selector for `type: list` (required there; forbidden elsewhere) |
| `fields` | required | field name → selector/JSONPath, at least 1; `list`/`json_path` **must include `url`** |

Selector grammar: L1/L2 use CSS (relative to the item; `a@href` reads an
attribute; relative URLs resolve against the page); `json_path` uses `$`
paths (`$.chart.result[0].meta.price`, `$[*].keyword` wildcards). Known gap:
`json_path` cannot express "item URL = the request URL" — such APIs use a
stable business field as `url` (official `plugins/stocks.yaml` does exactly
this).

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
| `model` | `glm-4-flash` | Model name (any OpenAI-compatible endpoint) |
| `scores` | `[value, relevance, credibility]` | Score dimensions (0-10 each), backfilled as the item `score` for routing |
| `batch` | `20` | Items scored per batch (1-1000) |
| `cache` | `true` | Per-URL score cache — a URL is never scored twice |
| `budget_per_run` | `50000` | Per-run token budget guardrail; exhausted → keyword-only for the rest of the run, with a WARNING |
| `base_url` | `null` | OpenAI-compatible endpoint, **a pure `env:`/`keychain:` reference only** (MYIA has no built-in endpoint), e.g. `env:MYIA_LLM_BASE_URL` |
| `api_key` | `null` | API key, same, e.g. `env:MYIA_LLM_KEY` (MYIA has no default key) |

Real calls also need the optional extra: `uv sync --extra llm` (missing →
structured `dependency_missing` and fallback to keyword-only scoring).

### push

| Field | Default | Semantics |
|---|---|---|
| `channel` | required | `feishu_card` / `telegram` / `webhook` / `stdout` |
| `target` | `null` | Push target, a **pure** `env:`/`keychain:` reference; forbidden on `stdout`, required elsewhere |
| `route` | `[]` | Threshold routing (table below); empty = the seven-category default mapping (freebie/node/buying-agent → immediate, others → digest) |
| `template` | `null` | Jinja2 card template (sandboxed render; syntax errors refused at load); omit for the channel's built-in layout |
| `timeout` | `10.0` | Send timeout seconds (**`webhook` only**; configured elsewhere → refused) |
| `retries` | `2` | Send retry count (`webhook` only) |
| `retry_backoff_seconds` | `1.0` | Send retry backoff seconds (`webhook` only) |

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

Three optional top-level sections, **not part of the 12-section public
contract**: validated at the load entry point by `load_category` (error
paths prefixed `$.plugin` / `$.baseline` / `$.aggregate`; `null` treated as
absent) and attached to the matching `CategoryConfig` attribute. Any sidecar
validation failure refuses the whole YAML (exit code 1).

### plugin: scenario plugin dual mode (v0.3)

Declares that this category depends on a market plugin (installed via
`myia plugin install`) for its service. **A plugin that cannot install, is
misconfigured, or whose remote is unreachable never blocks the core
pipeline** — it degrades to a structured finding and the category keeps
running (security-baseline rule).

| Field | Default | Semantics |
|---|---|---|
| `id` | required | Plugin id (lowercase letters/digits/hyphens/underscores, alphanumeric first; convention `myia-<name>`) |
| `requires` | `[]` | Host-capability vocabulary (currently `docker` only); string or list both accepted |
| `modes` | required | At least one mode: `local` (a compose file path and/or an install command) or `remote` (endpoint required; token **must** be a `keychain:myia/<scope>/<name>` reference — even `env:` is refused) |

```yaml
id: site-watch
name: Page change watch
schedule: "*/15 * * * *"
timezone: Asia/Shanghai
plugin:
  id: myia-monitor
  requires: docker
  modes:
    local:                        # local docker compose delivery
      compose: docker-compose.yml
      install: docker compose up -d
    remote:                       # or point at an already-deployed instance (desktop users: zero Docker)
      endpoint: https://my-monitor.example.com
      token: keychain:myia/monitor/token    # myia secret set myia/monitor/token
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
context (weekly keyword mention change). Numeric history is kept for
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
first degrades. When enabled, `myia run --json`'s `stages[]` gains an
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

## Load-time errors (structured)

A failed load raises `LoadError`: **all** errors are reported in one pass,
each carrying a field path (JSONPath style, e.g.
`$.sources[0].rate_limit.qps`) + a machine error type + a human message in
Chinese; `myia doctor --json` emits the same shape. Exit code 1. Common
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
| `missing_url_field` | `extract.fields` lacks `url` (required for `list`/`json_path`) |
| `unexpected_transport_field` | `timeout`/`retries`/`retry_backoff_seconds` on a non-webhook channel |

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
