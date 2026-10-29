# Proxy pools

> Sources no longer pin to a single egress: declare **pools** in the global
> `pools.yaml` and reference them from category YAML as `proxy: pool:<name>`.
> Multiple upstreams rotate in declaration order, transport-level consecutive
> failures remove an upstream (passive health), half-open recovery is triggered
> naturally by traffic after the interval expires, and once every upstream of a
> pool is removed the pool circuit-breaks with a zero-network fast fail
> (per-pool breaker). On the CLI side pools load via `--config`; on the desktop
> side the Settings proxy card stores credentials and the G10 one-click
> connectivity test probes, while the Proxy pool screen shows the egress list /
> liveness / probe success rate. For the field reference see the pools section
> of the [schema reference](schema.md); credential reference forms are covered
> in [Getting started](getting-started.md), section 2.

## 1. A pool in thirty seconds

First put the credential in the keychain (pool-URL userinfo only accepts
`env:` / `keychain:` references — plaintext is refused at load time; the value
goes through a stdin pipe, never into shell history):

```bash
myssia secret set myia/proxy/b < key.txt
```

Then write the global config (the desktop app auto-discovers
`<data root>/pools.yaml` by default and the CLI takes it explicitly via
`--config` — no sample file ships in the repo, create your own from the YAML
below):

```yaml
pools:
  main: "http://env:MYIA_PROXY_MAIN@proxy.example.com:8080"   # single upstream (v0.2 string form)
  rotating:                                                    # multi-upstream pool (v1.2 pooled form)
    upstreams:
      - "http://env:MYIA_PROXY_A@p1.example.com:8080"
      - "socks5://keychain:myia/proxy/b@p2.example.com:1080"
    max_failures: 3          # optional; consecutive transport-failure removal threshold, default 3; must be >= 1
    probe_interval: 300      # optional; half-open recovery interval (seconds), default 300; must be >= 1
```

Category YAML sources carry references only — proxy credentials never appear
there:

```yaml
id: proxy-pool-demo
name: Proxy pool demo
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    proxy: pool:rotating        # references a pool declared in the global config; direct = no proxy
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
push:
  - channel: stdout
```

```bash
myssia run --config pools.yaml config/proxy-pool-demo.yaml
```

`--config` is the single loader shared by run / test / doctor: a broken file
fails immediately with exit 1, each error carrying a field path + error class
(`invalid_pool_upstreams` / `invalid_pool_policy` / `unknown_pool_field` and
kin — typos are refused here, never silently swallowed). Pool names allow only
letters / digits / hyphen / underscore (same grammar as `pool:<name>`); the
upstream scheme is limited to `http` / `https` / `socks5` / `socks5h` (socks
needs the `httpx[socks]` extra — a missing dependency is a structured error,
not a network failure). String and mapping forms ride the same engine: a string
is equivalent to a single-upstream pool with `upstreams=(url,)`.

## 2. Runtime semantics: rotation / passive health / half-open recovery / per-pool breaker

The health model is **passive with zero background tasks**: no probe timer,
every state derives from real traffic; the lifecycle is one run (health state
burns when the run ends — the next run starts fully healthy).

- **Rotation**: each request starts from the stop after the sequential cursor
  and ring-scans in declaration order for the first admissible healthy
  upstream; an upstream that just failed at transport level is not immediately
  re-ridden by the next attempt (when pool size > 1), so the source retry
  budget is spent on the remaining upstreams.
- **Passive-health removal**: each upstream keeps a **consecutive**
  transport-level failure counter (`httpx.TransportError` including the
  timeout family; HTTP 4xx/5xx neither increments nor resets — the proxy
  delivered, the fault is the source's). Reaching `max_failures` (default 3)
  removes it; one delivery (status < 400) resets the counter and re-admits a
  removed upstream.
- **Half-open recovery**: once `probe_interval` (default 300s) has passed since
  removal, the next traffic naturally triggers a **single-flight trial** —
  success re-admits, failure re-removes immediately (no second N-failure
  accumulation); zero background probing.
- **Per-pool breaker**: all upstreams removed with no due half-open = pool
  circuit-broken; subsequent requests fast-fail **with zero network**
  (`proxy_pool_exhausted`, the error carrying the earliest recovery in
  seconds; the degrade chain short-circuits it like the rest of the `proxy_*`
  family) instead of sleeping until half-open — the decision returns to
  source-level degradation.

Browser engines (stealth_browser / crawl4ai / llm_browser and friends) consume
the same pool facade's currently admissible upstream at mount time; the image
ring's streaming downloads also ride the pool channel (the `stream` form —
headers arriving already counts as delivered). Multiple sources on one pool
share one pool channel (one facade per pool per run); the run's teardown closes
every upstream client.

## 3. doctor: per-upstream connectivity probing

```bash
myssia doctor --config pools.yaml --json --probe-timeout 10
```

Pools stay ordered and every upstream of every pool joins **one parallel set**
(asyncio.gather): each upstream consumes its own `--probe-timeout` (default
10s), so total wall time approximates the slowest upstream rather than the sum
— the probe must answer within the desktop sidecar's fixed shell timeout
(120s). A probe is one lightweight GET; the answering endpoint reports the
egress IP (the `exit_ip` field — directly checkable against the expected
exit). Each result row:

| Key | Meaning |
|---|---|
| `pool` / `upstream_index` / `upstreams` | pool name / index within the pool / total upstreams in the pool (single-upstream pools are always `0` / `1` — purely additive for existing consumers) |
| `proxy_url` | the upstream URL (credentials masked; same form as logs) |
| `ok` / `latency_seconds` / `status_code` | whether the probe passed (< 400) / latency / HTTP status |
| `exit_ip` | the egress IP seen through this upstream |
| `message` / `error_type` | human-readable reason / error class (`proxy_error` / `proxy_timeout` / `proxy_network` / `http_<status>` and kin) |

A pool whose credential resolution fails does not drag down the others
(partial-failure semantics); the failing pool answers one fallback row with the
masked URL and error class.

## 4. Desktop

### Settings screen: credentials into the keychain + one-click connectivity test (G10)

Settings → General → the "Proxy pool" card does two things:

- **Pool credential into the keychain**: fill in the pool name + credential
  value; saving writes `myia/proxy/<pool>` (the value is never echoed back;
  storing a new value under the same pool name is a password change). The
  global pools YAML references it as `keychain:myia/proxy/<pool>`; writing the
  pool URL structure back is a known protocol gap — `pools.yaml` stays
  hand-written.
- **One-click connectivity test**: filling the "Global pools YAML path" field
  probes with an explicit `--config`; **leaving it empty auto-discovers
  `<data root>/pools.yaml`** (probe only on hit; a miss is reported honestly,
  never as an error). The result renders in the "Verify after saving (doctor
  echo)" card below — everything shown comes from the doctor response, the
  same code path as the CLI.

### Proxy pool screen: egress list / liveness / probe success rate

Sidebar "Configuration" group → "Proxy pool" (route `/proxy-pool`), backed by
two protocol methods (sidecar `proxy_panel.py`):

- `proxy.probe` `{config?, config_auto?, probe_timeout?}`: the probe
  literally reuses doctor internally (zero logic fork); the response is the
  doctor response shape plus the additive key `snapshot: {persisted,
  reason?}`. After a successful probe the masked pool rows are appended to the
  local history as one snapshot (a persistence failure does not fail the probe
  — `persisted=false` plus the honest reason).
- `proxy.history` `{limit?, pool?}`: zero-network pure read returning
  `{snapshots, count, truncated, home_available, skipped}` (newest first);
  limit clamps to [1,100] with default 20, and a given `pool` filters to that
  pool's rows; corrupt lines are skipped and `skipped` counts them honestly.

Screen behavior (V1 read-only): **zero network on mount** (mount only reads
history, never probes) and **probing happens only when the user clicks
"Probe"** (zero background probing). Four blocks:

1. **Overview card**: grouped counts of the latest probe (pools / parsed
   upstreams / ok / bad / unprobed);
2. **Per-pool health cards**: one row per declared upstream — masked URL +
   three-state badge (connected · latency / unreachable / unprobed) + egress
   IP + human-readable reason + error-class tag;
3. **Probe history area**: per-pool **probe success rate** (share of ok rows
   within the last-20 window; probe rate, not traffic rate) and the snapshot
   timeline;
4. **Empty states**: with no default `pools.yaml`, a hint to add credentials in
   Settings; in dev mode without a data root, probing works but history does
   not persist.

Snapshots persist to `<data root>/proxy-probe-history.jsonl`, one
`{at, config, pools}` line per snapshot (pool rows already masked); the
retention cap is 200 entries — beyond it the oldest are trimmed with an atomic
rewrite; the file is created 0600 (the content is masked anyway — defense in
depth). **Only probes fired from this screen land in history** (the settings
screen's probe goes through doctor echo and is not recorded). The screen never
writes `pools.yaml` back (upstream add/edit/remove is V2).

## 5. Behavior notes and red lines

- **Behavior-change disclosure (v1.2)**: string (single-upstream) pools move
  from "each source retries on its own" to "removed after 3 consecutive
  failures → pool breaker fast-fail" — YAML compatibility means the file loads
  as-is and requests still run; the failure path is no longer byte-identical
  (a dead upstream no longer burns through every source's retry budget).
- **Credential red line**: category YAML and `pools.yaml` carry only `env:` /
  `keychain:` references; plaintext is refused at load. Every display (logs /
  doctor / panel / history) is masked — credential values never enter results.
- **`residential:<region>` remains a structured not-implemented**
  (`proxy_not_scheduled`); residential rotation is future plugin work. The
  usable forms today are `direct` and `pool:<name>`.
- **Health state does not survive a run**: removal and breaking are in-memory
  per run — restarting the run starts fully healthy again; for cross-run
  "trends" use the panel's probe history, not runtime state.
