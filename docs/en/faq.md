# FAQ

> Common questions: crawling ethics and boundaries, credential safety, data
> lifecycle, LLM cost, and self-repair. Start with
> [Getting Started](getting-started.md); field detail in the
> [schema reference](schema.md).

## Crawling ethics and boundaries

### Does MYIA ignore robots.txt?

No. `rate_limit.respect_robots` **defaults to `true`** and is enforced once,
in the engine layer: each origin's robots.txt is fetched once and cached per
origin; a disallowed URL is skipped outright (structured
`robots_disallowed`, visible skip reason). When robots.txt cannot be fetched
or the site has none, MYIA fails open (the industry's permissive convention)
— but rate limiting still applies. To turn `respect_robots` off (say, the
target is a public data API, not page scraping), you must justify it in a
YAML comment:

```yaml
id: demo-robots
name: robots demo
schedule: "0 9 * * *"
sources:
  - name: public-data-api
    engine: direct_api
    url: "https://data.example.com/api/v1/items"
    extract:
      type: json_path
      fields:
        title: "$[*].name"
        url: "$[*].url"
    rate_limit:
      qps: 0.5
      respect_robots: false   # data API, not page scraping: the site's robots.txt disallows everything but the API itself is public
push:
  - channel: stdout
```

### What happens when a captcha shows up?

Layered by design: **basic shields** (Cloudflare Turnstile / hCaptcha
challenge pages, TLS fingerprinting) are the working range of L4
`scrapling` (stealth fingerprint + optional Turnstile solving) and L5
`stealth_browser` (anti-detect fingerprint) — the degrade chain tries them
automatically. **Hard walls** (phone verification codes / SMS OTP / manual
human review) hit a structured error on contact
(`captcha_phone_verification` / `captcha_human_review`) with zero attempts —
MYIA does not bypass them.

### Can it get past "human verification + phone number" sources?

No — this is a hard-coded safety line: **no solution is also the answer**.
MYIA ships no bypass and never will. L5 `stealth_browser` detects these hard
walls: zero attempts, a structured error (`captcha_phone_verification` /
`captcha_human_review`), and the source is skipped. The other engine layers
have no captcha recognition — they only ever surface generic failures (e.g.
`http_403`) or empty results, and they never attempt a bypass. Comment such
sources out of your plugin, or switch to an alternative data source with a
public API.

### Does MYIA disguise itself as a browser?

Not by default. The default User-Agent is an honest product identifier
(`MYIA/0.1 (config-driven intelligence hub)`). Only when a site returns 429
to the product UA but serves browser UAs does it make sense to configure a
browser UA explicitly in the source's `headers` — a site-compatibility
choice that still obliges you to follow the site's terms and robots.txt.

### Can rate limiting be turned off?

Not to unlimited. `qps` (default 0.5, ≤ 1000), jitter and exponential 429/5xx
backoff are defaults enforced in the engine layer, not options; polite
crawling is a product premise. You may raise `qps` within its legal range,
but there is no "no limit" declaration.

## Credentials and safety

### Why are plaintext credentials forbidden?

Config files get copied, committed, shared — plaintext credentials are the
first leak. MYIA refuses plaintext values under credential-like keys **at
load time** (error type `credential_plaintext`, exit code 1) and accepts
only `env:` / `keychain:myia/<scope>/<name>` references; values are never
echoed, never logged. `myia doctor --json` probes every reference and hands
you a repair action.

### Will my cookies leak?

Credentials stored in the OS keychain (macOS Keychain / Windows DPAPI) never
enter the repository, config files or logs; `myia secret list` shows names
only. The repository is public — zero credentials, intranet addresses or
production corpora in the repo is the project's own red line, and every
credential slot in the official plugins is a reference placeholder.

## Data and storage

### Where does data live? Does it grow forever?

One SQLite file (default `./myia.db`, override with `--db`).
`storage.retention` (default `90d`) purges expired items automatically and
`storage.vacuum` (default `monthly`) reclaims space periodically; categories
declaring `baseline:` keep numeric history for twice the item retention (so
the week-over-week window stays complete).

### Why were my items dropped?

Three normal drop paths, all visible in `myia run --json` under
`stages[].skips`:

1. **`classify_unmatched`**: the built-in seven-category scan
   (`classify.builtin: true`, on by default) did not match — set
   `builtin: false` and write custom rules when your category fits none of
   the seven;
2. **dedup hit**: the rendered `dedup.key` was seen — the same URL or
   composite key was already collected;
3. **unchanged fingerprint**: the source content matches the previous round,
   so the whole source is skipped (`unchanged_skip`).

### Why doesn't the second run push again?

Two orthogonal mechanisms: `push[].route` decides *whether to push* (score
tiers), the AM/PM slots (`{date}`/`{slot}` composite dedup keys) decide
*whether it was already sent*. To push the same source both morning and
evening, use a composite key like `"{url}-{date}-{slot}"` — a bare `{url}`
pushes once forever.

## LLM and cost

### Is an LLM required?

No. With `enrich.enabled: false` (the default) the whole pipeline runs at
zero token: keyword pre-filter and threshold routing work as usual, and the
trend baseline (`baseline:`) is zero-token numeric analysis by design. Only
precision scoring needs an OpenAI-compatible endpoint (`enrich.base_url`/
`api_key` — MYIA has no built-in endpoint, no default key) plus
`uv sync --extra llm`.

### What happens when the scoring budget runs out?

It degrades to keyword-only scoring, the run completes normally with a
WARNING — the budget guardrail caps cost, it does not fail the task. The
cache (`enrich.cache`) ensures a URL is never scored twice.

### Does event aggregation burn extra tokens?

Very little. `aggregate:`'s coarse screen (title similarity) is zero-token;
only candidate groups reach LLM confirmation, sharing enrich's batch/cache/
budget rails and the same endpoint config; candidate pairs with parseable
timestamps further apart than `window_hours` never reach the LLM at all.

## Failures and self-repair

### A source broke — now what?

The agent-facing repair loop: fix the YAML → `myia test --json` to verify
extraction → `myia doctor --json` until `findings` is empty → `--dry-run`
rehearsal → a real run. The source health state machine
(`ok`/`degraded`/`dead`/`unknown`) and the findings→repair table live in the
self-diagnosis section of the [plugin guide](write-a-plugin.md).

### How do I read the exit codes?

`0` success; `1` config or usage error (YAML refused / plaintext credential /
bad usage); `2` all fetch failed; `3` partial failure (some sources or
channels failed). The `status` field in `--json` output maps one-to-one:
`success`/`failed`/`partial`.

## Plugin market

### What if a plugin cannot be installed?

Nothing — the core pipeline keeps running. A scenario plugin (declared in
the `plugin:` section) that cannot install, is misconfigured, or whose
remote is unreachable degrades to one structured finding; the rest of the
category keeps fetching and pushing (the desktop tool's lifeline). Manage
with `myia plugin list / install / remove`; the community directory is at
`plugins/community/README.md`.
