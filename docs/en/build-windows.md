# Building the Windows Desktop App Locally (from Source to MSI)

> Build, install, and run the MYIA desktop installer (MSI) from source on your own
> Windows machine. This is the same build chain as CI (the `windows-msi` job in
> `.github/workflows/desktop-release.yml`), written as a real-machine handbook
> (verified end-to-end on a fresh Windows 11 x64 machine in 2026-10).
> For the Python package (CLI / server), see [Getting Started](getting-started.md);
> this page covers the desktop app only.

## 0. Prerequisites

| Dependency | Install | Notes |
|---|---|---|
| Windows 10/11 x64 | — | ≥20GB free disk |
| Git for Windows | [git-scm.com](https://git-scm.com/download/win) | Must include Git Bash (default install does) |
| Node.js 20+ | [nodejs.org](https://nodejs.org/) | CI uses 22; 24 verified working |
| uv | PowerShell: `irm https://astral.sh/uv/install.ps1 \| iex` | Entry point for the Python side; takes effect in new terminals |
| Rust (msvc) | `curl -L -o %TEMP%\rustup-init.exe https://win.rustup.rs/x86_64 && %TEMP%\rustup-init.exe -y --default-toolchain stable-x86_64-pc-windows-msvc` | Needs MSVC C++ tools: any Visual Studio with C++ or VS Build Tools works — rustup detects and reuses it. Verify in a new terminal: `cargo --version`; `rustc -vV` must report `host: x86_64-pc-windows-msvc` |
| WebView2 Runtime | Usually preinstalled on Win11 | If missing, the MSI installer auto-runs the download bootstrapper (needs network) |

Network: GitHub / npm / PyPI / crates.io all work over a direct connection — no proxy
or mirrors needed. If your git is configured for a local proxy, see [Pitfall 1](#common-pitfalls).

## 1. Clone

```bat
git clone https://github.com/xinzhuzi/myia.git D:\dev\myia
```

Any Git client works (GUI clients: use their bundled git).

## 2. npm Dependencies (two places)

```bat
cd /d D:\dev\myia\desktop && npm ci --no-fund --no-audit
cd /d D:\dev\myia\desktop\ui-src && npm ci --no-fund --no-audit
```

## 3. Build the Python Sidecar

The sidecar is the PyInstaller onefile `myssia-core` (the Python engine behind the
desktop app). It must be built from Git Bash (the script handles Windows specifics):

```bat
"C:\Program Files\Git\bin\bash.exe" -c "cd /d/dev/myia/desktop && bash build-sidecar.sh x86_64-pc-windows-msvc"
```

- First run takes 3–10 minutes: `uv sync` pulls Python deps from PyPI (the vision
  extra includes the large onnxruntime wheel);
- Output: `desktop\src-tauri\binaries\myssia-core-x86_64-pc-windows-msvc.exe` (~120MB);
- The PyInstaller stage may stall a few minutes on Windows Defender's first scan —
  that's normal, **wait, it is not a failure** (see Pitfall 4).

## 4. Write the productName Overlay (required — dodges the WiX non-ASCII trap)

```bat
echo {"productName": "myssia"} > D:\dev\myia\desktop\src-tauri\tauri.local.conf.json
```

**Why it is required**: the base config's `productName` is non-ASCII, and the WiX
linker cannot produce non-ASCII artifact names — the bundle step fails at the end
without this override. CI injects the same thing. The file is local-only:
**do not commit it**.

Local builds have no updater signing key (it lives only in repository Secrets), so by
design the build is **unsigned and produces no update artifacts** — this does not
affect installation or use. The MSI is for local install experience only, not for
updater upgrade testing.

## 5. Tauri Build (MSI)

```bat
cd /d D:\dev\myia\desktop
set "PATH=C:\Program Files\Git\bin;%PATH%"
set "MYIA_SIDECAR_SKIP=1"
npx tauri build --bundles msi --config src-tauri/tauri.local.conf.json
```

- Prepending Git Bash to `PATH` solves **Pitfall 3** (prevents npm scripts from
  resolving `bash` to the WSL stub);
- `MYIA_SIDECAR_SKIP=1` reuses the step-3 sidecar instead of rebuilding it;
- First full Rust compile takes roughly 5–20 minutes (WiX is auto-downloaded by the
  tauri CLI);
- Output: `src-tauri\target\release\bundle\msi\myssia_<version>_x64_en-US.msi` (~122MiB).

## 6. Install

```bat
msiexec /i D:\dev\myia\desktop\src-tauri\target\release\bundle\msi\myssia_0.0.1_x64_en-US.msi /qn /norestart /L*v %TEMP%\myssia-install.log
echo %ERRORLEVEL%
```

Exit code `0` (or `3010` = reboot required) means success; double-clicking the MSI
works too. Default install location: `C:\Program Files\myssia\` (containing
`MYIA.exe` + `myssia-core.exe` + plugins).

## 7. Launch & Smoke Test

**Shows on launch (since 2026-10)**: Windows builds (local and CI share the
same chain) merge the platform overlay `desktop/src-tauri/tauri.windows.conf.json`
(a full mirror of the base config's window entry with `visible` flipped to
`true`), so the window is created visible — double-clicking the icon shows it
right away, and `focus: false` keeps it from stealing the foreground (a
taskbar flash instead). Clicking the icon again while the app is already
running goes through the single-instance plugin and focuses the existing
window. Just launch it:

```bat
start "" "C:\Program Files\myssia\MYIA.exe"
```

(Historical note) before 2026-10 the base config's `visible: false` silent
start leaked onto every platform, and Windows had no Dock/tray path to summon
the window — double-clicking looked like "nothing happened", and the supported
workaround back then was launching with `MYIA_SHOW_ON_START=1`. That variable
still works today (force show on start; focus is only taken by dev builds or
with an explicit `MYIA_SMOKE_FOCUS=1`) and mainly serves the macOS release
silent-start semantics — Windows no longer needs it. Honest caveat: "shows on
launch" is currently anchored by the overlay's config shape plus a cargo unit
test; real-machine MSI double-click verification lands with the next tag's
windows-msi artifact.

Verify three things: Task Manager shows both `MYIA.exe` and a `myssia-core` process;
the window renders the dashboard; the `%APPDATA%\MYIA` data directory exists.

(Optional) to smoke-test one level deeper: drop a minimal category into
`config/smoke-local.yaml` (below) before building — after install, the dashboard
should list the category and the data directory should grow a `myssia.db`, proving
the whole Python engine chain is alive:

```yaml
id: smoke-local
name: Local smoke
schedule: "*/30 * * * *"
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
push:
  - channel: stdout
```

## Common Pitfalls

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `git clone` fails with `Failed to connect to 127.0.0.1 port xxxx` | git is configured for a local proxy (`http.proxy` or a **URL-scoped** `http."https://github.com/".proxy`) but the proxy process is not running | Per-command empty override, leaving machine config untouched: `git -c http.proxy= -c http.https://github.com/.proxy= clone …`. Note the **URL-scoped setting wins over the global one, so the override key must match the scope**; alternatively start your proxy |
| 2 | WiX/`light.exe` errors at the end of bundling | non-ASCII productName | The step-4 overlay (required) |
| 3 | `npm run sidecar` prints a WSL error and exits | `bash` on PATH resolves to the WSL stub (no distro installed) | Prepend Git Bash per-command: `set "PATH=C:\Program Files\Git\bin;%PATH%"` (included in step 5) |
| 4 | Sidecar packaging stalls for minutes | PyInstaller onefile being first-scanned by Defender | Just wait — not a failure |
| 5 | MYIA launched via SSH/remote session exits instantly (exit 0) | WebView2 cannot start in a headless non-interactive context | Run from the local desktop session; for remote automation use Task Scheduler (`schtasks /create … /run`) to launch it in the interactive desktop session (the root cause is WebView2 failing to start — window visibility is unrelated) |
| 6 | `curl -I https://crates.io/api/…` returns 403 | bot protection rejecting HEAD probes on the API | Harmless — cargo uses the sparse index and fetches normally |

## Differences from CI

CI pins Node 22 (anything 20+ works here) / CI has a Rust cache, a real machine
compiles everything the first time / CI injects signing keys and produces update
artifacts, local builds are unsigned by design / clone with any client. The full
execution record is maintainer-private working material and stays out of the
public repository.
