# Write a Plugin

> A 世事 plugin is one YAML file: one intelligence category, twelve schema
> sections. This document is the reference AI agents read to generate
> plugins — every section has clear semantics and a default. The condensed
> agent-facing version of this page is the Agent Skill
> [`skill/SKILL.md`](../skill/SKILL.md); the two documents cross-reference
> each other and are locked to `src/myia/schema.py` field-for-field by
> `tests/test_skill_doc.py`, so they cannot drift apart.

## Schema sections

| # | Section | Semantics |
|---|---|---|
| 1 | `id` | Stable category identifier, used in storage and CLI output. |
| 2 | `name` | Human-readable category name. |
| 3 | `schedule` | Cron expression for pipeline runs. |
| 4 | `timezone` | IANA timezone for the schedule (defaults to the system timezone). |
| 5 | `sources` | Fetch sources. Per source: `engine` (auto / direct_api / static_html / crawl4ai / firecrawl / scrapling / stealth_browser / llm_browser — all implemented; L4–L6 have optional-dependency or external-service prerequisites, see the engine guide; plus the off-chain source engine `credhunter` — the in-process myia-credhunter scenario plugin assembles items itself and never joins the auto chain, explicit selection only), `url` (supports `{placeholder}` templates), `method` (GET/POST; POST takes `post_body`), `headers` (credential refs only), `pagination` (`template` / `selector` / `scroll` + `max_pages`; `scroll` is honored by L4 scrapling, other engines reject it structurally and the auto chain degrades to L4), `extract` (`list` / `item` / `json_path` plus field selectors; `extract.url_template` renders per-item URLs from `{field}` placeholders when the payload carries none — slug/appid-only APIs, see plugins/games.yaml), `rate_limit` (qps / jitter / backoff / respect_robots), `proxy` (direct / pool:name / residential:region), `retry`. |
| 6 | `watchlist` | Relevance profile: `keywords` (boost) and `mute` (demote/archive); the baseline for the LLM relevance score. |
| 7 | `classify` | First funnel: built-in seven-category keyword scan (`builtin`; unmatched titles drop) and/or custom `rules` (name / when expression / tag). Zero token. |
| 8 | `dedup` | Dedup key template, e.g. `{symbol}-{date}` or `{url}`. Composite keys only — never title fingerprints. |
| 9 | `enrich` | Second funnel: LLM precision scoring — `enabled`, `model` (any OpenAI-compatible endpoint), `scores` (value/relevance/credibility, 0–10), `batch`, `cache` (per-URL result cache), `budget_per_run` (token guardrail; exhausted → keyword-only for the rest of the run), plus `base_url` / `api_key` — each must be a pure `env:`/`keychain:` reference (世事 has no built-in endpoint and no default key). |
| 10 | `push` | Delivery channels: `channel` (`feishu_card` / `telegram` / `ntfy` / `dingtalk` / `wecom` / `weixin` / `webhook` / `stdout`), `target` (`env:` / `keychain:` refs only; `stdout` takes none), `template` (Jinja2, optional), and webhook-only transport knobs `timeout` / `retries` / `retry_backoff_seconds`. |
| 11 | `push.route` | Threshold routing per channel, first match wins: `score >= 8` → `immediate`, `>= 5` → `digest` (AM/PM slots), `< 5` → `archive`. Score rules stay dormant until LLM scoring backfills a score. |
| 12 | `storage` | Data lifecycle: `retention` (e.g. `90d`, expired items auto-purged) and `vacuum` (SQLite VACUUM cadence). |

Unknown top-level or per-section fields fail fast at load time (field path +
reason); only `sources[]` keeps an open namespace for engine extension
parameters (e.g. `symbols: [...]` for `{symbol}` fan-out, `engine_options.*`).

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
  run time) or `keychain:myia/<scope>/<name>` (system keychain — macOS
  Keychain / Windows DPAPI; the canonical namespace groups secrets by
  purpose, e.g. `keychain:myia/stocks/linuxsb_cookie`. Write the value with
  `shishi secret set myia/<scope>/<name>`; a flat legacy name like
  `keychain:linuxsb_cookie` is refused at resolve time).
- An auth-scheme prefix round-trips: `Authorization: "Bearer env:TOKEN"`.
- A YAML containing a plaintext credential **refuses to start** (exit code 1,
  error carries the field path). `shishi doctor --json` also probes every
  referenced credential for existence (`env_ref_missing` /
  `keychain_ref_missing` findings) so an agent can repair the environment
  by itself.

Per-channel credential conventions (the `push[]` `target` and its companion
token; details in `skill/SKILL.md` §2.13):

- `feishu_card`: `target` = chat/group ID (e.g. `env:FEISHU_CHAT_ID`); the
  bot token is read from `env:FEISHU_BOT_TOKEN`.
- `telegram`: `target` = chat id (`env:TELEGRAM_CHAT_ID`); the token is read
  from `env:TELEGRAM_BOT_TOKEN`.
- `ntfy`: `target` = `{server}/{topic}` string reference (e.g. `env:NTFY_TARGET`); optional auth token is read from `env:NTFY_TOKEN` (unset = anonymous public topic).
- `dingtalk`: `target` = custom-robot webhook URL (`env:DINGTALK_WEBHOOK_URL`); optional HMAC signing secret via the `dingtalk_secret` field (unset = bare webhook).
- `wecom`: `target` = touser userid (`env:WECOM_TUSER`); the self-built-app credentials are read from `env:WECOM_CORPID` / `env:WECOM_CORPSECRET` / `env:WECOM_AGENTID`.
- `weixin`: optional bridge channel — outbound goes through the local Hermes-Agent CLI
  (path overridable via `weixin_hermes_bin`); the login state lives only on the Hermes side,
  MYIA itself holds zero credentials. `target` = conversation peer id (`env:WEIXIN_PEER_ID`,
  direct push `weixin:<peer id>`); without a Hermes install, sends return a structured
  `bridge_unavailable` error (the config still loads).
- `webhook`: `target` = endpoint URL reference (e.g. `env:MYIA_WEBHOOK_URL`).
- `stdout`: zero credentials, first choice for local verification.

## Example agent session (demo script)

What "AI writes the YAML" looks like end to end (commands abbreviated):

```
user:   帮我盯着几个仓库的发版,重要更新立刻推给我。
agent:  shishi init --json
        # reads the structured checklist: required inputs (identity /
        # schedule / sources), optional sections with their defaults, the
        # five hard rules and the recommended next steps
agent:  writes plugins/repo-releases.yaml   # follows skill/SKILL.md §2 quick
        # reference: direct_api source with {page} pagination, json_path
        # fields (url included), classify.builtin: false + one custom rule,
        # dedup {url}, feishu_card push with a three-tier route, env refs
        # for every credential slot
agent:  shishi test plugins/repo-releases.yaml --json
        # trial-fetches each source (no push, no storage); checks
        # items[].fields, items[].dedup_key and fingerprint.verdict
agent:  shishi run plugins/repo-releases.yaml --dry-run --json
        # full rehearsal: stage in→out counts, skip reasons, route buckets
agent:  shishi run plugins/repo-releases.yaml
        # first real run; then shishi doctor --json until findings is empty
        # before handing the category to --loop scheduling
user:   (receives the release card in Feishu)
```

The same loop is what happens when a source breaks later: `shishi doctor
--json` reports the failing source with a structured code, the agent adjusts
the YAML (or the environment) and re-verifies — the user only decides.
