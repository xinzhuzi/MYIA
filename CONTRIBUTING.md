# Contributing to myssia / 参与贡献

Thanks for your interest! myssia is an AI-native intelligence hub: one YAML
file per intelligence category, and every interface (CLI `--json`, structured
errors, `myssia doctor`) is built so a coding agent can operate it. New
contributions should keep those two properties intact.

感谢关注!myssia 是 AI 原生情报中枢:一个品类一份 YAML,所有接口(CLI `--json`、
结构化错误、`myssia doctor`)都为「agent 可操作」设计。请在新贡献里保持这两点。

## Development setup / 开发环境

Pure Python (3.11+), managed with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/xinzhuzi/myia
cd myssia
uv sync                                  # core + dev deps (+ optional extras as needed)
uv run --no-sync python -m pytest -q     # full suite, zero real network
```

Layout map / 目录速览:

| Path | What lives there |
|---|---|
| `src/myssia/` | All first-party code: `schema.py` (12-section YAML model), `pipeline.py` (orchestration), `engines/` (7-rung fetch chain), `classify/`, `store/`, `enrich/`, `push/`, `feedback/`, `plugins/`, `cli.py` |
| `myssia-classifier/` | Standalone zero-dependency classifier package (own `pyproject.toml`) |
| `plugins/` | Official category YAMLs + market plugin packages |
| `skill/SKILL.md` | Agent-facing condensed schema & workflow |
| `docs/zh/`, `docs/en/` | Bilingual docs, drift-locked by tests |
| `tests/` | pytest suite grouped by module (CI gate); classifier tests in `myssia-classifier/tests/` |

## One-command environment (just) / 一键环境(just)

The fastest path from clean checkout to green gates is the root `justfile`
([just](https://github.com/casey/just) — `brew install just`, `cargo install
just`, or see [their docs](https://github.com/casey/just#installation)):

```bash
just setup          # uv sync --all-extras + npm ci ×2 (desktop + desktop/ui-src)
just test           # pytest + vitest (frontend component tests)
just check          # ruff + tsc/vite build + cargo check --locked
just build-desktop  # sidecar (PyInstaller) → tauri build
just docker         # docker build . (syntax/deps check, no push)
```

`just --list` is the living index of every recipe. The justfile is a pure
facade: each recipe runs the same underlying commands CI runs — it replaces
none of the native tools (uv / npm / cargo / docker). Tool versions are pinned
in-repo by native files, with zero extra tooling: `.python-version` (3.12,
read natively by uv), `desktop/src-tauri/rust-toolchain.toml` (stable +
minimal, read natively by rustup), and `engines.node: "22"` in **both**
`desktop/package.json` and `desktop/ui-src/package.json` (CI reads it via
`node-version-file`). **Windows**: run inside Git Bash (ships with the Git
installer) or WSL — the same convention the Windows release CI already uses
to run `build-sidecar.sh` under Git Bash. No PowerShell port is provided.

最快的「从零到门禁绿」路径是仓库根 `justfile`
([just](https://github.com/casey/just),`brew install just` /
`cargo install just`,安装方式见[官方文档](https://github.com/casey/just#installation)):

```bash
just setup          # uv sync --all-extras + 两个 npm 根 npm ci(desktop / desktop/ui-src)
just test           # pytest + vitest(前端组件测试)
just check          # ruff + tsc/vite build + cargo check --locked
just build-desktop  # sidecar 打包(PyInstaller)→ tauri build
just docker         # docker build .(语法/依赖检查,不 push)
```

`just --list` 即全部动作的活索引。justfile 是纯门面:每个 recipe 内部跑的
就是 CI 同款原命令,不替换任何原生工具(uv / npm / cargo / docker)。工具
版本由仓内钉版文件原生约束(零新工具):`.python-version`(3.12,uv 原生读)、
`desktop/src-tauri/rust-toolchain.toml`(stable + minimal,rustup 原生读)、
两个 package.json 的 `engines.node: "22"`(CI 经 `node-version-file` 读取,
两处必须同步改)。**Windows**:在 Git Bash(装 git 自带)或 WSL 中运行——
与 Windows 发版 CI 用 Git Bash 跑 `build-sidecar.sh` 的既有事实一致;
不提供 PowerShell 移植。

## Repo-root runtime artifacts / 仓库根运行产物说明

Several files you will see appear (and disappear) at the repo root are
**runtime artifacts, not repo content** — all gitignored on purpose. They land
at the root only when a command runs with the repo as its working directory;
the proper home for all of them is the data root (`MYIA_HOME`).

- `myssia.db` / `myia.db` — the single-file SQLite store (default
  `myssia.db`). `myia.db` is the rename-era name: the codebase was renamed to
  myssia while the data root (env `MYIA_HOME`, config dir) deliberately keeps
  the `myia` name, so both names stay guarded in `.gitignore`. Contains real
  fetched intelligence — never commit, and think twice before deleting a
  local one (that is your data).
- `channel_aliases.json` / `channel_directory.json` (sometimes
  `channel_dead.json`) — messaging-screen channel directory state, rewritten
  on every discover. Contains real platform chat IDs and group names, so the
  zero-tolerance rule is the same as `myssia.db`.
- `credhunter-keystore.json` — the credhunter full-text keystore
  (fingerprint → plaintext secret, written with 0600 perms). Even more
  sensitive than the channel files; keep it out of every commit.
- `*.bak` — editor/smoke leftovers next to plugin YAMLs (e.g.
  `plugins/stocks.yaml.bak`); safe to delete, ignored by pattern.

仓库根会反复出现(又消失)的几个文件是**运行产物,不是仓库内容**——全部
刻意 gitignore。它们只在命令以仓库为工作目录跑时落到根上,正经归宿都是
数据根(`MYIA_HOME`):

- `myssia.db` / `myia.db`——单文件 SQLite 库(缺省名 `myssia.db`)。
  `myia.db` 是更名期旧名:代码包更名 myssia,而数据根(环境变量
  `MYIA_HOME` 与配置目录)按纪律保留 `myia` 名,故 `.gitignore` 两个名字
  都守。内容是真实抓取的情报——严禁入库;本地这份是你的数据,删前想清楚。
- `channel_aliases.json` / `channel_directory.json`(偶有
  `channel_dead.json`)——消息屏频道目录运行态,每次 discover 都会改写,
  含真实平台 chat_id 与群名,零容忍口径同 `myssia.db`。
- `credhunter-keystore.json`——credhunter 全文密钥库(指纹→密钥原文,
  0600 权限落盘),比 channel_* 更敏感,任何提交里都不该出现。
- `*.bak`——插件 YAML 旁的编辑/冒烟残留(如 `plugins/stocks.yaml.bak`),
  按模式 ignore,可放心删。


## Ground rules / 红线(违反任何一条的 PR 会被拒绝)

1. **Credentials are never plaintext.** In code, YAML, tests and docs a
   credential slot only ever holds an `env:VAR` or
   `keychain:myia/<scope>/<name>` reference. Loading a config with plaintext
   credentials must fail fast — do not weaken that check.
2. **Tests never touch the real network.** Engine tests use recorded
   replays, injected fake modules, or mock transports. Real-source smokes are
   opt-in and skipped by default.
3. **Heavy engines are optional extras, lazily imported.** crawl4ai /
   scrapling / firecrawl / skyvern / openai are imported only inside their
   engine adapters and degrade with a structured `dependency_missing` error.
   A new *core* dependency requires a written argument in the relevant task's
   PRD first — the core stays `pip install`-light.
4. **No upstream source is copied into this repository.** External projects
   are consumed as libraries, services (API), or plugin compose references.
5. **The repo is public — zero private traces.** No credentials, internal
   hostnames, production corpora or private-system details in any committed
   file, including test fixtures and task documents.
6. **Respect the crawling ethics defaults.** robots.txt is respected and
   polite rate limiting is the default behavior, not an option; sources that
   require solving human verification are structurally rejected, never
   bypassed.

## Code conventions / 代码约定

- Python 3.11+, full type annotations, absolute imports, `__all__` in every
  package/module declaring the public API; Google-style docstrings on public
  functions (list `Raises` where relevant).
- Errors are **structured**: field path + error class + cause — never a bare
  one-line exception. CLI exit codes are a contract: `0` success · `1` config
  error · `2` all sources failed · `3` partial failure. Agents and CI depend
  on both.
- Fail fast on configuration (invalid YAML / plaintext credentials reject at
  load, exit 1); isolate at runtime (one failed source never aborts the
  batch — successes and failures are collected separately).
- Wrap lower-level exceptions with `raise ... from e`; timeouts raise
  `TimeoutError`; operational failures raise `RuntimeError`.
- Async style: a call path is all-sync or all-async; outbound calls are
  wrapped in `asyncio.wait_for(...)`; no blocking calls inside the event
  loop (`time.sleep` → `asyncio.sleep`, CPU-bound parsing →
  `asyncio.to_thread`).
- Logs are structured and never contain credential *values* (referencing the
  credential *name* is fine). Skip reasons (fingerprint unchanged / dedup /
  robots) are normal paths and must stay visible in output.
- Identifiers in English; error/log copy in Chinese (this is a bilingual
  project — user-facing messages are Chinese, code is English).
- One concept per file (split around 300–500 lines); file name matches its
  main class; flat directories — only true sub-domains get a subpackage.

## Tests / 测试

- Named `test_<unit>_<scenario>_<expected>`, AAA structure, mutually
  independent, no shared mutable state; shared fixtures in
  `tests/conftest.py`.
- Retry logic is tested with `Mock(side_effect=[...])` sequences; time-based
  logic (expiry, backoff) with freezegun.
- Every schema field keeps a default-value test; docs examples are validated
  end-to-end by `tests/test_docs.py` (every ```yaml block under `docs/` must
  load through the real `myssia.schema.load_category`) and `tests/test_skill_doc.py`
  locks `skill/SKILL.md` to the pydantic models field by field.

Run the suite exactly like CI does:

```bash
uv run --no-sync python -m pytest -q --tb=short
```

## Docs sync discipline / 文档同步纪律

Changing the YAML schema means changing **four** places in the same PR, or
the drift tests go red:

`src/myssia/schema.py` · `skill/SKILL.md` · `docs/` (zh + en) · `plugins/` examples.

## Commit & PR / 提交与评审

- Commit messages: English imperative (`feat: …`, `fix: …`, `docs: …`).
- One subtask per PR; fill in the PR template checklist (tests green, no
  plaintext credentials, docs synced).
- **First response / 响应流程**: every new issue gets a maintainer triage
  (labels `bug` / `enhancement` / `question`, a first reply, and a
  reproduce-or-clarify ask if needed) within 7 days. Behavior changes start
  as an issue so the schema/agent-facing impact can be discussed before code.
- Security issues are **never** filed as public issues — see
  [SECURITY.md](SECURITY.md).

## License / 许可

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
