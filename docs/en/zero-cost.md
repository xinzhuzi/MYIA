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
  api_key: keychain:shishi/image/api_key
```

Two cautions:

- The product's default cloud model `glm-4.6v` is a **paid tier** — for
  zero cost you must change `model` to a free model name;
- `cloud.api_key` accepts `keychain:` references only (plaintext and
  `env:` are both refused at load) — record it in the keychain first:

```bash
shishi secret set shishi/image/api_key < key.txt    # value goes over stdin, never into shell history
```

### Wiring up: enrich

In the category YAML's `enrich:` section (fields: `enabled` / `model` /
`base_url` / `api_key`, etc.), write both the endpoint and the key as
credential references; the default model `glm-4-flash` is itself Zhipu's
free tier. Complete category example (every other section is a minimal
skeleton — the `enrich:` section is the star):

```yaml
id: zero-cost-demo                    # save as plugins/zero-cost-demo.yaml and run
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
  model: glm-4-flash                              # the default; exact identifier per Zhipu's model page
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

## Red lines and habits

- **Quotas move**: every number above is a snapshot from 2026-10-03 —
  the vendors' official sites prevail. Free tiers are a marketing lever:
  new paywalls, tighter rate limits, or outright shutdowns can happen;
  scan the vendor's pricing page before wiring one in.
- **No plaintext credentials**: YAML carries only `env:` / `keychain:`
  references, and values go through `shishi secret set`'s stdin pipe; the
  vision cloud key accepts `keychain:` only.
- **Don't bet on marketing quotas**: anything labeled "credits /
  one-off quota" can go to zero. For critical categories, never make a
  free tier the sole dependency — keep a local endpoint as the fallback.
