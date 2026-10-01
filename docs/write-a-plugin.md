# Write a Plugin

> A MYIA plugin is one YAML file: one intelligence category, twelve schema
> sections. This document is the reference AI agents read to generate
> plugins — every section has clear semantics and a default.

## Schema sections

| # | Section | Semantics |
|---|---|---|
| 1 | `id` | Stable category identifier, used in storage and CLI output. |
| 2 | `name` | Human-readable category name. |
| 3 | `schedule` | Cron expression for pipeline runs. |
| 4 | `timezone` | IANA timezone for the schedule (defaults to the system timezone). |
| 5 | `sources` | Fetch sources. Per source: `engine` (auto / direct_api / static_html / crawl4ai / scrapling / stealth_browser / llm_browser), `url` (supports `{placeholder}` templates), `method` (GET/POST; POST takes `post_body`), `headers` (credential refs only), `pagination` (`template` / `selector` / `scroll` + `max_pages`), `extract` (`list` / `item` / `json_path` plus field selectors), `rate_limit` (qps / jitter / backoff / respect_robots), `proxy` (direct / pool:name / residential:region), `retry`. |
| 6 | `watchlist` | Relevance profile: `keywords` (boost) and `mute` (demote/archive); the baseline for the LLM relevance score. |
| 7 | `classify` | First funnel: built-in seven-category keyword scan and/or custom `rules` (name / when expression / tag). Zero token. |
| 8 | `dedup` | Dedup key template, e.g. `{symbol}-{date}` or `{url}`. Composite keys only — never title fingerprints. |
| 9 | `enrich` | Second funnel: LLM precision scoring — `enabled`, `model` (any OpenAI-compatible endpoint), `scores` (value/relevance/credibility, 0–10), `batch`, `cache` (per-URL result cache), `budget_per_run` (token guardrail; exhausted → keyword-only for the rest of the run). |
| 10 | `push` | Delivery channels: `channel` (feishu_card / telegram / webhook), `target` (`env:` / `keychain:` refs only), `template`. |
| 11 | `push.route` | Threshold routing per channel: `score >= 8` → `immediate`, `>= 5` → `digest` (AM/PM slots), `< 5` → `archive`. |
| 12 | `storage` | Data lifecycle: `retention` (e.g. `90d`, expired items auto-purged) and `vacuum` (SQLite VACUUM cadence). |

## Minimal example

```yaml
id: demo
name: Demo
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: example
    engine: auto
    url: "https://example.com/list?page={page}"
    pagination:
      mode: template
      max_pages: 3
    extract:
      type: list
      item: "div.post"
      fields:
        title: "a.title"
        url: "a.title@href"
    rate_limit:
      qps: 0.5
      respect_robots: true
    proxy: direct
    retry: 3
dedup:
  key: "{url}"
push:
  - channel: webhook
    target: env:MYIA_WEBHOOK_URL
    route:
      - when: "score >= 8"
        mode: immediate
      - when: "score >= 5"
        mode: digest
      - when: "score < 5"
        mode: archive
```

See [plugins/stocks.yaml](../plugins/stocks.yaml) for the complete
12-section showcase.

## Credential rules

- Credentials are **never written in plaintext** in a plugin YAML.
- Reference them instead: `env:VAR_NAME` (environment variable, read at
  run time) or `keychain:name` (macOS Keychain / Windows DPAPI).
- A YAML containing a plaintext credential **refuses to start**.
