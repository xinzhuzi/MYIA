# Security Policy / 安全策略

myssia is a crawler-and-pusher: by design it **handles credentials** (LLM keys,
push tokens, source cookies, proxy credentials) and **talks to the public
internet**. This page documents (1) how myssia handles credentials — the
contract you can rely on and hold us to — and (2) how to report a
vulnerability.

myssia 是一个采集与推送工具:它天然要**经手凭据**(LLM key、推送 token、源站
Cookie、代理凭据)并**访问公网**。本页说明:(1) myssia 自身的凭据处理策略——
这是你可以依赖、也可以拿来要求我们的契约;(2) 如何报告漏洞。

## Reporting a vulnerability / 报告漏洞

**Do not open a public issue for security problems.** 请勿用公开 issue 报告安全问题。

Use GitHub's private vulnerability reporting:
**https://github.com/xinzhuzi/myia/security/advisories/new**

Include: affected version (`myssia --version`), reproduction steps, and — if
relevant — a config snippet **with credentials replaced by
`env:EXAMPLE_VAR` placeholders**. If you discovered a leak affecting your own
deployment (e.g. a crash log containing a secret), say so explicitly; we will
help you reason about rotation.

Response targets / 响应时效:

| Step | Target |
|---|---|
| Acknowledgement | within 7 days |
| Triage (accepted / declined + why) | within 14 days |
| Fix or mitigation for accepted issues | within 30 days, or a public ETA |

Coordinated disclosure: we credit reporters in the release notes unless you
prefer otherwise. 请勿在修复发布前公开细节。

In scope: `src/myssia/` credential resolution (env/keychain), plaintext-credential
rejection, push channels (Feishu card / Telegram / webhook), the plugin
market loader (`src/myssia/plugins/`), feedback/callback endpoints, Docker
images and compose files, and the GitHub Actions workflows. Out of scope:
upstream engines' own vulnerabilities (report those upstream — crawl4ai,
Scrapling, Firecrawl, Skyvern), and social engineering of your own
infrastructure.

## Credential-handling policy / 凭据处理策略(设计契约)

These are load-bearing guarantees, not suggestions:

1. **No plaintext credentials in configuration — ever.** A credential slot
   (key names containing `cookie` / `authorization` / `token` / `secret` /
   `password` / `apikey` / `session`, case- and hyphen-insensitive) only
   accepts an `env:VAR` or `keychain:myia/<scope>/<name>` reference. A
   plaintext value in a category YAML is a **load-time rejection** (exit
   code 1), not a warning.
2. **Values live in the OS keychain or your environment.** `myssia secret set
   myia/<scope>/<name>` writes to the system keychain (macOS Keychain /
   Windows DPAPI via `keyring`); values passed on the command line are
   discouraged (they land in shell history) — stdin piping is documented
   instead.
3. **Values never reach logs, errors or CLI output.** Diagnostics may name a
   credential (`keychain:myia/stocks/site_cookie missing`) but never echo its
   value; `myssia secret list` shows names only.
4. **Flat legacy secret names are rejected.** Names must be namespaced
   `myia/<scope>/<name>` — this keeps myssia's secrets from colliding with
   other tooling on your machine.
5. **Push/ingest endpoints default closed.** Callback/ingest API surfaces are
   disabled by default; when enabled they require token auth and bind to
   localhost/intranet only.
6. **Compose files carry zero plaintext credentials.** Official plugin
   packages reference secrets via keychain/env; the remote plugin mode uses
   `endpoint` + `keychain:` token placeholders only.
7. **CI publishes through manual, secret-injected workflows.** The PyPI
   publish workflow is `workflow_dispatch`-only and takes tokens exclusively
   via GitHub secrets or OIDC trusted publishing — no credentials are stored
   in the repository.

If you find any of the above violated — in code, docs, plugins or CI — that
is a security bug; report it as such.

## Handling *your* secrets safely / 使用侧建议

- Paste **redacted** `--json` output in issues: myssia's JSON output keeps
  credential values out by design, but double-check anything you copy from
  debug logs.
- Rotate a token if it ever appeared in a screenshot, a shared terminal, or a
  public post — first-week launch posts included.
- Scoped, revocable tokens where the provider allows (e.g. a
  category-scoped Feishu bot token) beat account-level keys.

## Supported versions / 支持版本

Pre-1.0: security fixes land on `main` and ship in the next release. There
are no long-term support branches yet.

| Version | Supported |
|---|---|
| `main` / latest release | ✅ |
| older tags | ❌ (upgrade) |
