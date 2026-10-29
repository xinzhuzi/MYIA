# Credential hunter (cred-hunter)

> The three stages of AI credential intelligence — discovery → verification /
> balance → exposure — fused natively into the pipeline: a category source
> declares `engine: credhunter` and the in-process scenario plugin
> `myssia-credhunter` assembles items in place (the `url` is never fetched),
> producing **masked-only** output (first 8 + last 4) — full keys land only in
> the local keystore. **For authorized security research and leak triage on
> assets you own or are authorized to test only** (the authorization boundary
> is in the red lines at the end). The desktop "Credential hunter" panel is a
> read-only view of the keystore: key / provider / verification state /
> high-risk view — no export, no full-key echo. The engine vocabulary is in the
> [schema reference](schema.md); the plugin-development surface is in
> [Write a plugin](write-a-plugin.md).

## 1. Three lanes and category integration

`engine: credhunter` is an **out-of-chain source engine**: registered in the
engine vocabulary but **never part of the auto L1-L6 degrade chain** — it only
takes effect when explicitly selected; a failure is a structured per-source
failure that never blocks the category's other sources. The `extract` node is
not consumed (configuring it is rejected structurally so the misconfiguration
is visible instead of silently ignored). The three lanes are chosen via
`engine_options.credhunter.lane`:

| Lane | What it does | Network |
|---|---|---|
| `scan` (default) | local text triage: paste suspicious log/config/diff fragments into `documents`; the provider fingerprint library (union-regex ten key families + 17 fine-grained regexes + variable-name attribution + noise filtering) produces masked items | zero egress |
| `credhunt` | GitHub artifact hunting: code search + commit message swimlanes; per-request token-pool rotation + 403/429 backoff; cross-run checkpoint resume; hits land in the local keystore | GitHub API |
| `exposure` | FOFA/Shodan exposure discovery + L0 passive probing (unauthenticated reads only) | FOFA/Shodan/targets |

A category you can drop in as-is (`exposure` + `scan` dual sources, credentials
all `keychain:` references):

```yaml
id: cred-hunter-demo
name: Credential hunter demo
schedule: "0 3 * * *"
timezone: Asia/Shanghai
sources:
  - name: exposure-scan
    engine: credhunter              # out-of-chain source engine (myssia-credhunter scenario plugin)
    url: "https://fofa.info"        # upstream anchor (real calls go through engine_options)
    engine_options:
      credhunter:
        lane: exposure
        fofa_apikey: keychain:myia/credhunter/fofa-key
        fofa_base: https://fofa.info
        shodan_apikey: keychain:myia/credhunter/shodan-key
    proxy: direct
    retry: 0                        # the lane has its own inter-page politeness, no engine retries

  - name: manual-triage
    engine: credhunter
    url: "https://api.github.com"   # placeholder anchor: the scan lane sends no requests
    engine_options:
      credhunter:
        lane: scan
        documents:                   # fragments to triage (the text holds a synthetic placeholder key)
          - text: |
              export OPENAI_API_KEY="sk-4f1c9d2e8b7a6305abcd"
            url: "https://example.com/triage/config.env"
            source_type: manual
            file_path: config.env
    proxy: direct
    retry: 0

classify:
  builtin: false                     # credential/exposure intel skips the seven news categories

dedup:
  key: "{url}"

push:
  - channel: stdout                  # private intel does not leave this machine
```

The `credhunt` lane's `github_tokens` is a list of credential references (e.g.
`keychain:myia/credhunter/github-token`), with `checkpoint_file` persisting a
cross-run resume cursor — the complete (sanitized, structure-identical)
templates are
`config/credentials.example.yaml`(随源码发布,本门面仓不含)
(native hunt + optional aipocket remote aggregation, dual sources) and
`config/exposure.example.yaml`(随源码发布,本门面仓不含). For pure-credential
categories the recommended dedup key is a field combination
(`{provider}-{key_fingerprint}`; `{title}` never enters a key). The engine
itself lives at `src/myssia/engines/credhunter.py` and the scenario plugin at
`plugins/myssia-credhunter/` (the adapter is loaded dynamically by the host via
compile+exec — the core repo and the plugin share zero static coupling; a
missing component raises the structured `credhunter_adapter_missing` with a
hint to install it from the Settings screen). Remote aggregation over an
already-deployed aipocket instance goes through the sister plugin
`myssia-credentials` (remote tier; endpoint + token in the category's
`plugin:` section), coexisting with — never replacing — this one (the aipocket
fusion delivery surface).

## 2. Credential references and the empty state

All three lanes' credentials (`github_tokens` / `fofa_apikey` /
`shodan_apikey`) are written only as `env:` / `keychain:` references, resolved
by the host at fetch time and **injected as parameters** into the adapter —
the adapter itself reads no keychain, no environment, writes no files. The
canonical namespace is `myia/credhunter/*`:

```bash
myssia secret set myia/credhunter/github-token < token.txt
myssia secret set myia/credhunter/fofa-key < fofa.txt
myssia secret set myia/credhunter/shodan-key < shodan.txt
```

**A missing key is an explicit empty state, not an error** (the engine-face
AC6 rule): the source skips this round with `credential_missing`, sends zero
requests, never blocks the category — write the reference and output resumes.
In detail:

- `credhunt` with no usable token = the source is **not enabled** this round
  (the upstream "never registers without a token" semantics); one bad
  reference in the token pool does not void the pool (skipped loudly with a
  warning, the rest keep working);
- `exposure`'s FOFA/Shodan lanes degrade independently — configure only the
  FOFA key and only the FOFA lane runs; both missing is the only whole-source
  empty state;
- the "explicit direct-run" CLI face `myssia credhunt` is the one that treats
  `tokens_missing` as a config error with exit 1 (the engine-face contract is
  unchanged);
- misconfiguration is not guesswork: `myssia doctor --json` verifies each
  reference exists and names the fix (the missing
  `myssia secret set ...` line).

## 3. The local keystore: hunt → store → verify

When the hunter produces items the full key is discarded right after masking;
the **plugin-side keystore** closes the hunt → store → verify loop: a hit goes
straight into the store (fingerprint → full text) → credcheck probes from the
store → `check_state` / `last_check` are written back.

- **Location and permissions**: `$MYIA_HOME/credhunter-keystore.json` (without
  that environment variable it lands in the current working directory); file
  mode **600**, atomic writes (same-directory tmp + rename — a crash never
  leaves half a JSON); the file is created on first hit only.
- **Shape**: `{"version": 1, "records": {fingerprint: {"apikey" (full text),
  "provider", "first_seen", "last_seen", "hit_count", "sources" (deduped
  evidence URLs, capped at 20), "last_check", "check_state"}}}`; fingerprint
  = first 16 hex of sha256.
- **Masking rule** (items / push templates / CLI stdout always use this form):
  length > 12 keeps the first 8 + `…MASKED…` + last 4; 9-12 keeps the first 8;
  ≤8 keeps the first 2. **The full text exists only in the local keystore** —
  the store is a data-domain artifact that never leaves this machine, never
  enters git, never gets sent anywhere. `exposure` hits (no credentials) do
  not enter the store.

Verification/balance runs through the CLI subcommand `myssia credcheck` with
dual entry points (not through the engine face):

```bash
myssia credcheck --from-keystore --balance --json   # probe the keystore record by record and write back
myssia credcheck --apikey "sk-..." --balance --json # explicit key (bring your own for smoke tests)
```

`check_state` has three states: `final_verified` (verified alive) /
`rejected` (confirmed dead) / `transient` (undecidable); checks run serially
at ≤ 30 RPM per vendor (2s spacing) with a 15s per-probe timeout — discovered
credentials are never abused.

## 4. The desktop credential hunter panel

Sidebar "Configuration" group → "Credential hunter" (route `/credhunter`).
The data surface is a single protocol method, `credhunter.keys.list` (empty
params): a **pure file read** of the local keystore — zero network, zero
writes, and a missing store is never created; response records carry only the
server-computed `masked_apikey` — the full `apikey` is dropped at handler
projection, so the protocol stream and the UI never see a full key. Error
family: `keystore_unreadable` (IO) / `keystore_invalid` (broken JSON or
shape mismatch).

The panel is a V1 read-only view:

- **Two views**: "All keys" (default, full set) and "High risk" — the
  high-risk criterion is `check_state == final_verified` (verified alive =
  leaked and valid, the top live risk; dead and undecidable are excluded);
- **Six-column table**: key (masked + fingerprint) / provider / verification
  state badge (verified alive / confirmed dead / undecidable / unchecked) /
  hits (count + first-to-last seen dates) / evidence (row-expand for the
  evidence URL list) / last check; search (mask/provider), provider chip
  filtering, sorting by hit count / recency, and draggable column widths
  (persisted locally);
- **Three red lines (G11, never built)**: no export, no full-key reveal/copy,
  and no testing hunted keys against live services — the screen contains no
  copy or view actions whatsoever;
- **Empty-state paths**: store not created (the hunter has not run or has no
  hits yet) → "The credential hunter has no keystore yet — enable an
  `engine: credhunter` source in a category YAML and run a collection round";
  store present but zero records → "The keystore has no records yet"; empty
  high-risk view → "No verified-live leaked credentials".

## 5. Default parameters and politeness

| Parameter | Default | Notes |
|---|---|---|
| GitHub code-search pagination | ≤5 pages × 100 items/page | stops early when returned < per_page |
| GitHub query budget | 12 queries/run | checkpoint cursor rotates across runs |
| GitHub rate-limit wait cap | 90s | only on 403/429, single retry round |
| GitHub blob size cap | 1 MiB | oversized blobs dropped, no truncation |
| credcheck concurrency | serial, ≤ 30 RPM per vendor (2s spacing) | does not copy upstream concurrency |
| Probe timeout | 15s | per verification/balance request |
| FOFA | page size 100, ≤10 pages, 0.3s between pages, 24 queries/run | base configurable (official/mirror — deployer's choice) |
| Shodan | page size 100, ≤10 pages, 1.0s between pages, 16 queries/run | — |
| Passive probing | L0 unauth_read only, 12-request budget per target | high-risk classes stay fail-closed |
| Masking | first 8 + last 4 | items/push/CLI always masked; full text only in the local keystore |

## Red lines and boundaries

- **Authorization scope (hard boundary)**: authorized security research and
  leak triage on assets you own or are authorized to test only; no mass
  scanning, no high-frequency probing, no abusing discovered credentials.
  Passive probing is L0 unauthenticated reads only; weak_password / idor /
  ssrf / sqli / rce probing stays fail-closed — the authorization-scope
  mechanism is future work.
- **Private-intel red line**: hits never enter git, never leave the machine;
  items and every push template carry the first-8/last-4 mask — full keys
  never enter template context; these category templates default to push
  `stdout` (rework the template masking policy before any outbound push).
- **License boundary**: the upstream reference objects are AGPL-3.0 projects;
  this plugin is a **functional re-implementation** of their behavior — zero
  upstream code/identifier/copy duplication, all code and data authored from
  scratch in this repo (the same clean boundary as the RSSHub/Firecrawl
  service-consumption precedent).
- **The panel displays but never exports**: the credential hunter panel will
  permanently never do export / full-key echo / live testing (the G11 three
  nevers); verify triggers, balance persistence and similar interactions are
  later work, not V1.
