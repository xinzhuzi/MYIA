# Contributing to MYIA / 参与贡献

Thanks for your interest! MYIA is an AI-native intelligence hub: one YAML
file per intelligence category, and every interface (CLI `--json`, structured
errors, `myia doctor`) is built so a coding agent can operate it. New
contributions should keep those two properties intact.

感谢关注!MYIA 是 AI 原生情报中枢:一个品类一份 YAML,所有接口(CLI `--json`、
结构化错误、`myia doctor`)都为「agent 可操作」设计。请在新贡献里保持这两点。

## Development setup / 开发环境

Pure Python (3.11+), managed with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/xinzhuzi/MYIA
cd MYIA
uv sync                                  # core + dev deps (+ optional extras as needed)
uv run --no-sync python -m pytest -q     # full suite, zero real network
```

Layout map / 目录速览:

| Path | What lives there |
|---|---|
| `src/myia/` | All first-party code: `schema.py` (12-section YAML model), `pipeline.py` (orchestration), `engines/` (7-rung fetch chain), `classify/`, `store/`, `enrich/`, `push/`, `feedback/`, `plugins/`, `cli.py` |
| `packages/myia-classifier/` | Standalone zero-dependency classifier package (own `pyproject.toml`) |
| `plugins/` | Official category YAMLs + market plugin packages |
| `skill/SKILL.md` | Agent-facing condensed schema & workflow |
| `docs/zh/`, `docs/en/` | Bilingual docs, drift-locked by tests |
| `tests/` | pytest suite (CI gate) |

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
  load through the real `myia.schema.load_category`) and `tests/test_skill_doc.py`
  locks `skill/SKILL.md` to the pydantic models field by field.

Run the suite exactly like CI does:

```bash
uv run --no-sync python -m pytest -q
```

## Docs sync discipline / 文档同步纪律

Changing the YAML schema means changing **four** places in the same PR, or
the drift tests go red:

`src/myia/schema.py` · `skill/SKILL.md` · `docs/` (zh + en) · `plugins/` examples.

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
