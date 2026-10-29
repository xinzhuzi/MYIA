# Scheduled jobs (cron)

> Put a category pipeline on a clock: at each due fire myssia runs it once and
> delivers the summary. One engine, three hosts — CLI daemon
> (`myssia cron serve`), the desktop app (built-in ticker), and manual
> `myssia cron tick` (external cron / debugging) — coexisting under mutual
> exclusion. A job's payload is a category YAML (see
> [Write a plugin](write-a-plugin.md) and the [schema reference](schema.md)).

## 1. A job in thirty seconds

```bash
myssia cron create "every 2h" --category config/myssia-demo.yaml --deliver local
myssia cron list
myssia cron serve    # resident host, one tick per 60s; Ctrl-C to stop
```

`--category` points at a category YAML (stored as an absolute path; creation
runs the full load validation, so a broken file fails immediately with exit 1).
A minimal category you can hang a job on:

```yaml
id: cron-demo
name: Cron demo
schedule: "0 9 * * 1-5"        # the category's own cadence; the job uses the create schedule argument
timezone: Asia/Shanghai
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
dedup:
  key: "{url}"
push:
  - channel: stdout
storage:
  retention: 30d
```

Each due fire spawns one `myssia run <category> --json` subprocess (fetch →
classify → dedup → analyze → built-in push, unchanged), then delivers a
**run summary** (status / duration / source stats / retained items / push
buckets / failure lines) to the `--deliver` target; the local archive lands
under `<data root>/cron/output/`.

## 2. Schedule syntax

### Natural language

| Form | Examples | Semantics |
|---|---|---|
| Interval | `30m`, `every 30m`, `every 2h`, `1d` | recurring; re-anchored at each **completion** |
| Weekly/daily | `every monday 9am`, `weekdays at 9am`, `every day at 9am`, `monday, wednesday at 14:00` | compiled to a 5-field cron |
| One-shot delay | `in 30m`, `in 2h` | fires once (repeat=1 implied) |
| One-shot timestamp | `2026-11-01T09:00:00` | fires once; naive timestamps anchor to `--timezone` |
| Cron expression | `0 9 * * 1-5` | see below |

Time words accept `9am` / `9:30am` / `14:00` / `7` (bare 24h hour) / `noon` /
`midnight`; durations accept `30m` / `2h` / `1d` and bare units (`hour` = hourly).

An interval's next due instant is completion + period, and it is only picked
up by the next tick (one per 60s by default) — `every 1m` under the default
tick lands roughly every 2 minutes; tighten serve's `--interval` if you need
denser fires.

### The 5-field cron and POSIX dow

`minute hour day month dow`, **exactly 5 fields** (6-field forms are rejected
with a structured error). The day-of-week field follows **POSIX semantics: both
0 and 7 mean Sunday**; lists (`1,3,5`), ranges (`1-5`), steps (`*/2`),
wrap-around ranges (`5-1` = Friday through Monday), and English day names
(`MON-FRI`) all work. An unparseable schedule reports the full five-form usage
list, so you can switch forms on the spot.

### Timezone

`--timezone` wins; the fallback is the category YAML's `timezone`, then the
local zone. Cron fields match the wall clock of the configured zone, and the
DST fall-back hour strictly advances — no past instants are produced.

## 3. Deliver: where the run summary goes

- `local` (default): the summary lands in `<data root>/cron/output/<job_id>/`;
- platform specs: `feishu:<chat name>`, `telegram:12345` (reusing the messaging
  layer's directory/direct-ref resolution; credentials follow each channel's
  default env/keychain reference chain);
- `stdout:debug`: for debugging — the summary card is echoed as a JSON line
  into the log;
- `--failure-deliver`: where failure alerts go, same spec syntax; it falls
  back to `--deliver` by default, and `none` disables it explicitly. A
  successful run whose delivery failed records
  `last_status="delivery_failed"` without touching the failure streak.

## 4. Hosts: who runs the tick

### CLI daemon (serve)

```bash
myssia cron serve --db /data/myssia.db --interval 60
```

Headless/server form: a supervised ticker thread (auto-respawn on crash);
`myssia cron status` reports heartbeat liveness and the next due instant.

### Desktop

The desktop app starts the same kind of ticker (home mode only) and exposes the
`cron.*` method family to the UI; it coexists safely with CLI serve (below).

### Manual tick (external cron)

```bash
myssia cron tick --db /data/myssia.db
```

One scan-and-dispatch pass for every due job — for system crontabs/CI to drive;
when the lock is held by another host it quietly returns 0.

### Multi-host coexistence

A process-wide tick file lock plus fire claims (with TTL) guarantee that with
serve, desktop, and manual tick all running, one job fires at most once per
instant. While the desktop has a user-triggered run in flight, that fire is
skipped (`skipped_busy`) — the human wins.

## 5. Behavioral notes

### The digest pool does not accumulate across fires

Digest-slot aggregation is in-memory state within a single run: every fire
starts a fresh pipeline, and digest-slot items are composed into one card at
the end of that run — **nothing accumulates across fires** (to get a daily
digest, space out the schedule; don't rely on cross-fire pooling).

### Coexisting with `run --loop`

A cron fire is a one-shot run subprocess that never starts message polling, so
it never conflicts with `run --loop` daemons; the existing constraint is
unchanged — at most one `--loop` process per telegram bot token (409 clashes),
and cron does not alter that. Jobs sharing one db dispatch serially; different
dbs run in parallel.

### docker compose resident form

The stock compose defaults to `command: run … --loop`; for the scheduled form
change the `command:` line in `docker/docker-compose.yml` to
`cron serve --db /data/myssia.db` (the image entrypoint is the `myssia` CLI) —
jobs live in `/data/cron/jobs.json`, persisted with the volume. Create the
jobs inside the container — category YAMLs are mounted read-only at
`/config/` (the demo runs out of the box; for your own category, copy an
`*.example.yaml` template and fill it in on the host side first):

```bash
docker compose -f docker/docker-compose.yml run --rm myssia \
  cron create "every 2h" --category /config/myssia-demo.yaml --db /data/myssia.db
```
