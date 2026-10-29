# Zero-Cost Setup

> Get vision and enrichment running without spending a cent: local endpoints
> (the default, zero egress) → cloud free tiers → light daily batches.
> **Every free quota below is point-in-time information snapshotted on
> 2026-10-03 — the vendors' official sites prevail**; marketing quotas that
> run out or expire are all explicitly labeled as such.

世事 ships with no built-in LLM endpoint and no default key
([Getting Started](getting-started.md), section 2). Both vision (image
reading) and enrich (precision scoring) accept **OpenAI-compatible
endpoints only** — swap the `base_url` and any provider below plugs in.
The two paths live on different configuration surfaces, so the wiring
steps are given separately:

| Path | Where it's configured | Credential syntax |
|---|---|---|
| Vision | `<data root>/vision.yaml` (same source as the desktop "Vision settings" pane; hand edits take effect too) | cloud keys accept `keychain:` references only |
| Enrich | the `enrich:` section of the category YAML | `env:` or `keychain:` references |

## 1. Local endpoints (the default): mlx-vlm / Ollama

The model runs on your own machine: unlimited quota, zero egress, zero
quota anxiety — fully usable for both vision and enrich, and the product's
default posture (mlx-vlm first on macOS; Ollama or LM Studio on
Linux/cross-platform).

**Vision** (the local channel is the default): start a local
OpenAI-compatible vision server,

```bash
uvx --from mlx-vlm mlx_vlm.server --model <model dir> --host 127.0.0.1 --port 8080
```

- The default endpoint is `http://127.0.0.1:8080/v1` (vision.yaml's
  `local.base_url`); `local.model` takes the local model directory path.
  The local channel sends no auth headers — no key needed at all.
- With LM Studio, change `local.base_url` to `http://127.0.0.1:1234/v1`;
  with Ollama, to `http://127.0.0.1:11434/v1` (Ollama's official
  OpenAI-compatible endpoint).

**Enrich**: just point the endpoint at a local service. Note that 世事
has no default key, so even a local no-auth endpoint needs a non-empty
placeholder value:

```bash
export MYIA_LLM_BASE_URL=http://127.0.0.1:11434/v1
export MYIA_LLM_KEY=ollama            # placeholder; local endpoints don't check it
```

The price is VRAM and speed: for vision, an 8B-class MLX quantized model
on Apple Silicon is recommended; plain-text scoring is fine with 2B–8B.
Machines that can't run those should use the cloud free tiers below.

## 2. Cloud free endpoints

Ranked by "rate-limited free" (no total quota, speed caps only) ahead of
"marketing quotas" (gone once used up):

| Service | What's free (snapshot 2026-10-03, official site prevails) | Nature | Vision | Enrich |
|---|---|---|---|---|
| Zhipu GLM-4V-Flash / GLM-4-Flash | Pricing page marks both input and output unit prices "free" (¥0, not a limited-time promotion); concurrency/RPM figures not public — check the account rate-limit page after logging in | Rate-limited | ✓ (4V-Flash) | ✓ (4-Flash is the enrich default model `glm-4-flash`) |
| OpenRouter `:free` models | Of the day's 466 models, 17 end with `:free`; 50 requests/day with no top-up, 1000 requests/day after a cumulative top-up of ≥$10 (official FAQ) | Daily quota, runs out | No stable free vision model verified — not recommended | ✓ text workhorse |
| Groq Free plan | Text models (e.g. `openai/gpt-oss-120b`) 30 RPM, 1000 requests/day, 8K TPM (official rate-limits page); rate limits are counted per organization | Rate-limited | Not verified | ✓ alternative |
| Mistral La Plateforme | Free tier is the default for new accounts (no credit card needed); the official pricing page notes $10/month API credits; check the console's Limits page after logging in for the exact rate limits | Credit-based, exhausts monthly | Not verified | ✓ alternative |

> Not listed: SiliconFlow / DashScope. As of the snapshot date,
> SiliconFlow's signup bonus no longer has any explicit mention in its
> official public channels (site / signup page / finance FAQ) — the amount
> and validity cannot be verified, so this doc makes no promise to users;
> if you use it anyway, treat it strictly as "runs out once the bonus is
> spent".

### Wiring up: vision (the cloud channel)

Zhipu is the default vendor for the vision cloud channel — going zero
cost is just a model-name change plus recording a key:

```text
# <data root>/vision.yaml (same source as the desktop "Vision settings"; hand edits take effect too)
channel_default: cloud          # the default is local; switch to cloud
cloud:
  base_url: https://open.bigmodel.cn/api/paas/v4   # the product default
  model: glm-4v-flash           # zero-cost vision model (exact identifier per Zhipu's model page)
  api_key: keychain:myia/image/api_key
```

Two cautions:

- The product's default cloud model `glm-5.3-flash` is a **paid tier**
  (default since 2026-10-15; the previous default `glm-4.6v` was paid too) —
  for zero cost you must change `model` to a free model name;
- `cloud.api_key` accepts `keychain:` references only (plaintext and
  `env:` are both refused at load) — record it in the keychain first:

```bash
myssia secret set myia/image/api_key < key.txt    # value goes over stdin, never into shell history
```

### Wiring up: enrich

In the category YAML's `enrich:` section (fields: `enabled` / `model` /
`base_url` / `api_key`, etc.), write both the endpoint and the key as
credential references; `glm-4-flash` remains a free-tier option (the default is now `glm-5.3-flash`; check Zhipu's pricing page) — Zhipu's
free tier. Complete category example (every other section is a minimal
skeleton — the `enrich:` section is the star):

```yaml
id: zero-cost-demo                    # save as config/zero-cost-demo.yaml and run
name: Zero-cost enrich demo
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
enrich:
  enabled: true
  model: glm-5.3-flash                            # current default; switch to free-tier glm-4-flash for zero cost (per Zhipu's pricing page)
  base_url: env:MYIA_LLM_BASE_URL
  api_key: env:MYIA_LLM_KEY
push:
  - channel: stdout
```

```bash
export MYIA_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export MYIA_LLM_KEY=<your Zhipu API key>          # or switch to a keychain: reference in the keychain
```

Switching vendors is only a `base_url` + `model` change: OpenRouter
`https://openrouter.ai/api/v1` (model names carry the `:free` suffix),
Groq `https://api.groq.com/openai/v1`, Mistral
`https://api.mistral.ai/v1` (all three OpenAI-compatible addresses come
from the vendors' official docs). When free-tier quota is tight, dial
`enrich.batch` down; the budget guardrail `budget_per_run` keeps applying
as usual.

## 3. Light daily batches: the Gemini free tier

The Google AI Studio free tier fits **a few light enrich batches a day**.
As of the snapshot date, the official rate-limits page no longer
publishes exact free-tier numbers — log in at
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit)
and check for yourself; the qualitative verdict: enough for light daily
batches, not enough for heavy or high-frequency ones. OpenAI-compatible
endpoint (official docs):

```bash
export MYIA_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export MYIA_LLM_KEY=<your AI Studio API key>
```

Set the enrich `model` to a Gemini model name; the free vision quota has
not been verified, so this doc makes no promise about it.

## 4. Self-hosted Firecrawl: a zero-dollar rendering backend

The three paths above cover LLM endpoints; on the fetch side, JS-heavy
sources riding the L3 `firecrawl` engine can point at a self-hosted backend
too — zero dollars, and rendering traffic never leaves your machine. The
engine talks plain HTTP (`POST {endpoint}/v1/scrape`), so a running instance
is a ready L3 rung on the degrade chain.

**Deployment steps and the recipe live in the upstream official
[self-host guide](https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md)
(SELF_HOST.md: an API + Playwright rendering + Redis/PostgreSQL/RabbitMQ
queue stack) — we do not replicate the recipe here** (server-side deployment
is the owner's ops domain; the upstream doc is the single authoritative
source). Wiring 世事 needs zero code: the engine's built-in default endpoint
is the standard port `http://127.0.0.1:3002`; set the variable only for a
non-standard port or a remote machine:

```bash
export MYIA_FIRECRAWL_URL=http://127.0.0.1:3002   # this is the default; only needed off the standard port
# MYIA_FIRECRAWL_API_KEY stays unset — self-host runs with auth off; only the api.firecrawl.dev cloud needs a key
```

Per-source override uses a credential reference (plaintext is always
refused), same as the enrich pattern:

```yaml
id: zero-cost-firecrawl
name: Self-hosted rendering backend demo
schedule: "0 9 * * *"
sources:
  - name: js-heavy-news
    engine: firecrawl
    url: "https://example.com/news"
    engine_options:
      firecrawl:
        endpoint: env:MYIA_FIRECRAWL_URL
push:
  - channel: stdout
```

Boundaries, stated plainly:

- **AGPL boundary**: the upstream server is AGPL-3.0; 世事 consumes it
  purely as a **service** (HTTP API calls) with zero source copying — same
  as the RSSHub precedent; its server code must not be vendored into this
  repository (MIT).
- **Cloud-only gaps** (per the official docs, no sugarcoating): the default
  self-host stack has **no Fire-engine** — hard anti-bot bypass (IP-block
  handling / bot-detection warfare) and screenshot / page-action formats
  are unavailable; LLM structured extraction needs your own
  OpenAI-compatible endpoint; agent / browser / interact are cloud
  features. Basic scrape (Fetch + Playwright rendering → markdown/html) is
  in the stack and exactly covers what the L3 fallback needs (the engine
  consumes only those two formats).
- **Resources and hardening**: the official stack caps the api container at
  4 CPUs / 8GB RAM — size lighter machines accordingly; the default stack
  ships no auth and no TLS, so a public deployment needs hardening (reverse
  proxy + strong PostgreSQL credentials + a changed `BULL_AUTH_KEY`). The
  degrade-chain contract (dead service degrades gracefully, cloud/self-host
  drop-in) is documented and evidenced in the
  `src/myssia/engines/firecrawl.py` module docstring.

## 5. Self-hosted SearXNG: keyword digests (zero dollars)

Search-style intelligence (competitor + launch, event + progress) rides the
off-chain `searxng` engine — a self-hosted
[SearXNG](https://github.com/searxng/searxng) metasearch aggregate, zero
dollars, search traffic aggregated through your own instance.
**Self-hosted only**: public instances' robots.txt bans every search
request (`/*?*q=*`) and API abuse is explicitly forbidden.

Where the instance comes from (plugins ship zero docker mode, final ruling
2026-10-06) — two paths, pick per your deployment:

- **Path ① native local service component (recommended, zero Docker,
  landed 2026-10-06)**: Settings → Python environment → the optional
  component row "keyword digest (SearXNG)" — install, then press "Start".
  pip installs natively into the app-managed Python environment; the shell
  generates `settings.yml` (the json format already on, random secret
  written automatically); the service listens on `127.0.0.1:8888`, so the
  default base works with zero config. It starts stopped by default;
  start/stop only via the settings card, and if the engine meets it
  stopped it surfaces a structured hint toward settings (no implicit
  start).
- **Path ② an already-deployed external instance (your own server)**:
  point the per-source `searxng_base_url` or the environment variable
  `MYIA_SEARXNG_URL` at it. Deploying SearXNG yourself is owner-ops
  territory — recipes and steps live in the
  [official upstream docs](https://docs.searxng.org/) (we do not
  duplicate them; the key to the json API is the instance's `settings.yml`
  override `search.formats: [html, json]` — without it every API query
  answers 403).

MYIA wiring needs zero changes: the engine's built-in default base is
`http://127.0.0.1:8888` (the same port as the Path ① component); point it
elsewhere only for a remote instance (environment variable, or the
per-source `searxng_base_url`):

```bash
export MYIA_SEARXNG_URL=http://127.0.0.1:8888   # the default value; only needed for non-standard ports / remote hosts
```

A keyword-digest category (queries are plaintext non-credentials — safe in YAML):

```yaml
id: zero-cost-searxng
name: self-hosted search digest demo
schedule: "0 8 * * *"
sources:
  - name: keyword-watch
    engine: searxng
    url: "http://127.0.0.1:8888"    # identity only; the request base comes from searxng_base_url / the env var
    queries:
      - "artificial intelligence regulation"
      - "data breach disclosure"
push:
  - channel: stdout
```

Honest boundaries: the **AGPL boundary** follows the Firecrawl/RSSHub
precedent — MYIA consumes it purely as a service (HTTP API calls), zero
source copying; its code must never be vendored into this repository (MIT).
Politeness: serial per-query fetching with a built-in 3s inter-query
delay; keep run intervals ≥30 minutes. Politeness toward the upstream
search providers is borne by the SearXNG aggregation layer (its own
circuit breaking). The two-path statement and instance-source conventions
live in the `config/searxng.yaml` header comment.

## Red lines and habits

- **Quotas move**: every number above is a snapshot from 2026-10-03 —
  the vendors' official sites prevail. Free tiers are a marketing lever:
  new paywalls, tighter rate limits, or outright shutdowns can happen;
  scan the vendor's pricing page before wiring one in.
- **No plaintext credentials**: YAML carries only `env:` / `keychain:`
  references, and values go through `myssia secret set`'s stdin pipe; the
  vision cloud key accepts `keychain:` only.
- **Don't bet on marketing quotas**: anything labeled "credits /
  one-off quota" can go to zero. For critical categories, never make a
  free tier the sole dependency — keep a local endpoint as the fallback.
