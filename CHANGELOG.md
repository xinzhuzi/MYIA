# Changelog

All notable changes to MYIA are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Earlier v0.x milestones are summarized in the README roadmap table and are not
repeated here.

## [1.1.1] — 2026-10-03

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
- **Signed update channel**: Tauri updater with signature verification
  (endpoints + pubkey injected by the release CI) and a new "Software update"
  card in the desktop settings screen — check for updates, review the notes,
  download & install (passive), then relaunch. Failures surface as structured
  errors.
- **Version alignment**: `myia`, `myia-classifier` and the desktop app all
  report `1.1.1` (`myia --version` matches the .app bundle version).
- **PyPI dual packages**: `myia` and `myia-classifier` publish via a manual
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
- `myia skill install` / `skill path` commands (four agent targets,
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

[1.1.1]: https://github.com/xinzhuzi/MYIA/releases/tag/v1.1.1
[1.1.0]: https://github.com/xinzhuzi/MYIA/releases/tag/v1.1.0
[1.0.0]: https://github.com/xinzhuzi/MYIA/releases/tag/v1.0.0
