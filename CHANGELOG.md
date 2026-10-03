# Changelog

All notable changes to 世事 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Earlier v0.x milestones are summarized in the README roadmap table and are not
repeated here.

## [Unreleased]

### Added

- **Messaging screen** (commit `4be1325`): a sixth desktop screen (「消息」) for the
  messaging platform — platform-grouped channel directory (name / type / last-seen /
  dead-letter badge), inline alias editing, per-platform directory refresh, and a
  push-rule panel that picks concrete targets (`platform:name` specs) from the
  directory. Empty and disconnected states follow the existing screen patterns.
- **Four sidecar protocol methods**: `channels.list` (directory + aliases + dead +
  rules merged view), `channels.refresh` (per-platform `discover_directory` with
  bucket replace; old bucket untouched on failure), `channels.alias` (set/delete),
  and `push.write` (full push-array replacement per file, following the
  `sources.write` pattern: path fencing → comment-preserving text surgery →
  re-parse deep-equality gate → `load_category` gate → `.bak` → atomic write;
  validation failures leave the file untouched).
- **Sidecar protocol version bumped to 2** (`PROTOCOL_VERSION`, desktop/entry.py):
  the unified bump that was deferred while the `yaml.*`/`image.*` method families
  merged in parallel; v2 covers everything merged since v1 (10 → 27 methods).
- **v1.1.2 desktop parity batch — protocol methods** (10-03-v112-desktop-parity;
  absorbed into the v2 ledger above, no further bump per the one-bump merge
  policy): `run.cancel` (process-group kill — SIGTERM, 5s grace, SIGKILL
  fallback; signal-terminated runs report a distinguishable `cancelled` status),
  `runs.list` (runs-table read, newest first — run history survives a sidecar
  restart), `secret.delete` (mistyped-credential removal; `secret_not_found`
  on the second delete), and `sources.test` (async trial-fetch job fenced like
  `yaml.*`; the 120s per-source CLI timeout equals the shell's hard per-request
  timeout, so a synchronous implementation is forbidden by construction —
  results arrive via the new `test.completed` event).
- **v1.1.2 desktop parity batch — `store.items` cursor & search**: composite
  cursor `before` (strictly-older `first_seen`) + `before_id` (tuple
  comparison) so same-timestamp item bursts larger than one page can paginate
  to exhaustion, plus `query` (NOCASE LIKE over title/content/source). The
  feed screen now paginates with the composite cursor (the old
  since-reuse + client-dedup + added==0 stop is kept only as a defensive
  backstop). This lands the merged C1×G1/G3 shape agreed with the feed-ux
  batch (its Python half is superseded by this change).
- **Desktop shell respawn** (`desktop/src-tauri/src/main.rs`): the sidecar is
  automatically restarted after a crash — exponential backoff (1/2/4/8/16s,
  max 5 attempts, counter resets after 10s of stable life) with the lifecycle
  published as shell events (`sidecar://state`: respawning/online/dead);
  a new `sidecar_restart` command (idempotent) backs the top-bar badge's
  probe→restart→re-probe recovery path, so a killed sidecar no longer bricks
  the UI with permanent `sidecar_not_running` errors.
- **v1.1.2 desktop parity batch — second slice, protocol methods**:
  `feedback.mark` / `feedback.list` / `feedback.stats` (B2 — desktop feedback
  entries via the same `myia.feedback` code path as the CLI with
  `channel="desktop"`, so `shishi feedback list` sees the same rows;
  payload keys aligned with the CLI's row/stats shapes) and `store.trend`
  (B4 — per-day item counts over `first_seen`, UTC calendar days, window
  clamped to [1, 90]). No `PROTOCOL_VERSION` bump of its own: the batch rides
  the existing ledger per the one-bump merge policy (currently at 5 after the
  vision-v2 bump).
- **v1.1.2 desktop parity batch — second slice, UI**: feed-card 👍/👎 feedback
  (greyed out after marking — manual marks carry no idempotency key), a
  dashboard feedback-stats card, a dashboard collection-trend card (hand-rolled
  SVG sparkline, 7/14/30-day window switch, zero new npm deps; the
  `fillDailyCounts` aggregation is vitest-covered), a top-bar global
  "run once + cancel" control (C12; category follows the top-bar selector,
  ✕ calls `run.cancel`), and a settings「评分与反馈」partition (B3/C11 —
  per-category `enrich.enabled` toggle + `enrich.model` writeback via
  `yaml.read → comment-preserving text surgery → yaml.save (mtime optimistic
  lock) → doctor re-verify`; `budget_per_run` shown read-only as the guard
  rail; push-channel declarations get a link to the YAML editor screen, and
  the global pools writeback stays deferred pending the yaml-editor decision).
  `version` now also reports `app_version` (C10 — the shell injects
  `MYIA_APP_VERSION` from `package_info()`, i.e. the single source of truth in
  `tauri.conf.json`; dev/CLI report `null` honestly), surfaced in the top-bar
  connection tooltip.
- **feed-ux batch — three sidecar protocol methods** (10-03-feed-ux):
  `feed.export` (export the current filtered view as JSONL/CSV; the sidecar
  writes the user-picked `dialog.save()` path directly — data never transits
  the webview, only that one file is written; `export_path_invalid` /
  `export_write_failed`), `schedule.preview` (Apify-style next-runs preview
  per category via `build_cron_trigger`, pure computation, count clamped to
  [1,20]; a category without a schedule reports `schedule: null`, not an
  error), and `push.test` (sends one synthetic item through a real channel —
  credential resolution follows the existing `env:`/`keychain:` reference
  chain and channel `PushSendError` codes pass through verbatim; the stdout
  channel's card lands in the response `preview` field so the serve-mode
  protocol stream stays clean).
- **Sidecar protocol version bumped to 3** (`PROTOCOL_VERSION`,
  desktop/entry.py): covers the feed-ux batch's three methods above
  (27 → 30; the `store.items` cursor/search groundwork landed earlier under
  the v2 ledger via the C1×G1 merged shape).
- **feed-ux batch — feed screen UX** (10-03-feed-ux): server-side item search
  (G1 — debounced 300ms + Enter submit, `query` rides the pagination cursor so
  it covers the whole store, not just loaded pages; the counter line reports
  the server-search × local-filter layers honestly), inline card expansion
  with full content and metadata (G2, closing census C9), 「打开原文」 via
  `@tauri-apps/plugin-shell` `open()` — a scoped `shell:allow-open`
  capability allowing only `https?://` URLs (the v1.1 review posture of
  zero shell-execute grants for the webview is preserved; non-http(s) item
  URLs render no button), and view export (G3 — JSONL/CSV format toggle,
  `dialog.save()` with a dated default name, path/count feedback line).
- **feed-ux batch — dashboard / sources / settings** (10-03-feed-ux): a
  per-category 「跑一次」 button on the dashboard category cards (G4 —
  `run.start` + `completed`-event state machine copied from the feed empty
  CTA; busy disables the button, failures show inline), a 「排程一览」
  section on the sources screen (G4 — per-category `schedule.preview` fired
  concurrently via `Promise.allSettled`; a single category's failure collapses
  only its own row), the top-bar category selector is now wired (C8 — options
  from `health().plugins` deduped by id, selection lifted to the layout and
  applied as server-side `store.items` filtering via router outlet context;
  the dead `defaultValue="all"` skeleton comment is gone), and a 「发送测试」
  button in the settings push form (G5 first half — `push.test` with the
  form's current channel; when scope+credential-name are filled the target
  reference `keychain:myia/<scope>/<name>` is composed, otherwise the
  channel's default env chain is exercised as-is; ok/fail badge + structured
  error line).

### Changed

- **Versioning reset to 0.0.1** (owner decision, 2026-10-03): the version
  sequence restarts from `0.0.1`. The `v1.1.1` git tag and its GitHub Release
  were removed, and every version source was reset accordingly: root and
  classifier `pyproject.toml`, `myia.__version__`, `tauri.conf.json`,
  `Cargo.toml` (+ `Cargo.lock`), plus the `shishi-classifier` dependency
  window (`>=0.0.1,<0.1`). The `1.x` sections below remain as the historical
  record of the retired sequence.
- **Tag-driven releases** (10-03-tag-release): publishing is now triggered
  exclusively by pushing a `v*` tag. The Docker workflow no longer publishes on
  every push to main — GHCR receives only `X.Y.Z` + `latest` per release (and
  pre-release tags do not move `latest`); PyPI publishing rides the same tag
  with a version-consistency guard (tag must match both `pyproject.toml`
  versions) and attaches wheels/sdists to the GitHub Release once publish
  succeeds. **Status as of the v0.0.1 tag (2026-10-03): not yet landed** — the
  two PyPI runs on the tag both failed at the trusted-publisher gate (runs
  37115937696 / 37117015481; shishi/shishi-classifier pending publishers not
  registered yet), so `pip install shishi` is unavailable and the Release
  carries the four desktop assets only (`shishi_0.0.1_aarch64.dmg`,
  `shishi.app.tar.gz{,.sig}`, `latest.json`). Wheels/sdists land after the
  owner registers the pending publishers and re-runs the failed jobs.
  TestPyPI rehearsal remains available via manual dispatch.

## [0.0.1] — 2026-10-03

- **First release of the reset version sequence** (owner decision, 2026-10-03 —
  see the versioning-reset entry under Unreleased; the retired `1.x` sections
  below are the historical record). Released under the renamed distribution
  identity `shishi` / `shishi-classifier` via the tag-driven channels, with
  every version source at `0.0.1`:
  - **GitHub Release `v0.0.1`** (tag → commit `96751a5`): four desktop assets —
    `shishi_0.0.1_aarch64.dmg`, `shishi.app.tar.gz` + `.sig` (updater-signed),
    and `latest.json` (`version: 0.0.1`) for the signed update channel.
  - **GHCR**: `0.0.1` + `latest` image tags per release (no more per-push
    `sha-*` accumulation).
  - **PyPI: not landed yet** — both PyPI runs on the tag failed at the
    trusted-publisher gate (runs 37115937696 / 37117015481); `shishi` /
    `shishi-classifier` wheels/sdists are neither on PyPI nor attached to the
    Release until the owner registers the pending publishers and re-runs.

## [1.1.1] — 2026-10-03
- **定名「世事」,发行身份全面更名**:产品名 MYIA→世事(桌面端已于本版本完成);PyPI 发行名 `myia`→`shishi`、`myia-classifier`→`shishi-classifier`(均系首次发布,零迁移);CLI 命令 `myia`→`shishi`;GitHub 仓库更名 `MYIA`→`shishi`(旧链接自动重定向);Docker 镜像 ghcr 同步更名。Python 模块名 `myia`/`myia_classifier` 本版本过渡保留,下版本一并更名。


### Fixed

- **Desktop data paths** (commit `45639c3`): the installed app lost all data
  paths when launched from Finder (`cwd=/`) — three competing path policies,
  no bundled plugins, no first-run seed. Now unified behind a single rule:
  explicit params > `MYIA_HOME` env (injected by the Tauri shell) > frozen
  `.app` bundle detection > dev cwd fallback. Repo/CLI behavior is unchanged.
  - Official category YAMLs (ai-news / wool / stocks / gpu-prices, plus the new
    demo below) are bundled as app resources and seeded into the app data root
    on first run (idempotent via a `.seeded` marker).
  - A virgin data root reports a healthy empty state (and `first_run` for UI
    guidance) instead of a `plugins_dir` error; the feed screen offers a
    first-run "run your first plugin" call-to-action.

### Added

- **Out-of-the-box demo plugin** (`plugins/myia-demo.yaml`, bundled): a
  zero-credential GitHub new-stars watcher over the GitHub Search API
  (single unauthenticated JSON request). A fresh install shows real data on
  the very first plugin run — no `config_error`, no secrets to fill in.
- **Per-item AI summary on the feed** (desktop, protocol v6 `feed.enrich`):
  a card action that runs the enrich pipeline on a single item and shows the
  composite score, per-dimension scores and model — cached hits cost zero
  tokens; unconfigured enrichment degrades to a fix-it hint instead of an
  error.
- **Logs screen power tools** (desktop): re-run a past run from its header
  (replays the same yaml/dry/db flags), filter runs by category and status,
  and search within logs with match highlighting.
- **Feed batch actions & keyword pinning** (desktop): mark all loaded items
  read/unread in one click; pin any feed card's keywords into a category
  YAML watchlist straight from the card (mtime-guarded, cross-file id
  checked).
- **Signed update channel**: Tauri updater with signature verification
  (endpoints + pubkey injected by the release CI) and a new "Software update"
  card in the desktop settings screen — check for updates, review the notes,
  download & install (passive), then relaunch. Failures surface as structured
  errors.
- **Version alignment**: `shishi`, `shishi-classifier` and the desktop app all
  report `1.1.1` (`shishi --version` matches the .app bundle version).
- **PyPI dual packages**: `shishi` and `shishi-classifier` publish via a manual
  release workflow (TestPyPI rehearsal first, then PyPI).
- **README**: download & install section pointing at GitHub Releases with the
  macOS right-click-to-open Gatekeeper guidance (the installer is not
  Apple-notarized; builds are public and auditable), real-data desktop
  screenshots, and install instructions corrected to the uv-workspace path.

## [1.1.0] — 2026-10-02

### Added

- **Desktop app** (Tauri 2 shell, Python core embedded as a PyInstaller
  sidecar): JSON-RPC sidecar protocol (10 methods + streaming events) and a
  React 19 + Tailwind 4 UI with five real screens — dashboard, sources, feed,
  logs, settings — plus updater configuration and a tag-triggered release
  pipeline producing signed `.app`/`.dmg` artifacts (`desktop/UPDATER.md`).
- **Settings screen credential forms** (LLM / proxy pool / push channels):
  values go only through `secret.set` into the OS keychain (macOS Keychain /
  Windows DPAPI), never into component state or YAML; `doctor` verification
  is rendered inline.
- **Source-managed plugin architecture** (in-process plugin tier, zero
  docker): Photon OSINT as a pinned submodule with an in-process adapter,
  a lightweight proxy-pool fetcher, monitor/credentials as remote plugins,
  douyin/maxun documented as server-only; plugin compose files moved out of
  `plugins/` into `docker/plugins/`.
- `shishi skill install` / `skill path` commands (four agent targets,
  `--link` / `--force`).

### Notes

- In-card feedback buttons (desktop), the settings feedback toggle and
  collection-trend charts are **not** part of this release — the feedback
  loop works via the CLI (plus Telegram/Feishu callback receivers); the
  desktop pieces are scheduled for v1.2.

## [1.0.0] — 2026-10-02

### Added

- First public release: the complete pipeline
  (fetch → classify → dedup → analyze → enrich → push) with a fully
  documented 12-section YAML schema, six fetch engines on an auto-degrading
  ladder (L1 `direct_api` → L6 `llm_browser`), the seven-category keyword
  classifier, SQLite single-file storage with retention/VACUUM, threshold-
  routed push (Feishu card / Telegram / webhook / stdout), keychain-backed
  secrets, an agent-facing skill sheet, and bilingual (zh/en) docs kept
  consistent with the code by tests.

[0.0.1]: https://github.com/xinzhuzi/shishi/releases/tag/v0.0.1

<!-- 1.x sequence retired 2026-10-03 (versioning reset to 0.0.1): the
     v1.0.0/v1.1.0/v1.1.1 tags and their GitHub Releases were deleted, so the
     [1.1.1]/[1.1.0]/[1.0.0] headings above are kept as the historical record
     with no link targets (verified: `git tag` lists only v0.0.1 and
     releases/tags/v1.1.1 returns 404). -->
