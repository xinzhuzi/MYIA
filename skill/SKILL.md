---
name: myia
description: Turn a natural-language intelligence request into a MYIA category plugin and run it. Use when the user asks to monitor, collect, classify, or be pushed any information category (stocks, deals, AI news, credentials, GPU prices, custom) — read the plugin schema, generate the YAML, run `myia run`, and self-repair from `myia doctor` output.
---

# MYIA — AI-native intelligence hub (Agent Skill)

## What is MYIA

MYIA is a config-driven intelligence pipeline:
`fetch → classify → dedup → analyze → enrich → push`. One YAML file per
intelligence category. The user states a need and the agent does everything
else — this skill is MYIA's primary distribution form because its primary
user is an AI agent, not a human.

## How an agent uses it

1. **Read the plugin schema** — [docs/write-a-plugin.md](../docs/write-a-plugin.md)
   defines the 12-section category YAML (`id` / `name` / `schedule` /
   `timezone` / `sources` / `watchlist` / `classify` / `dedup` / `enrich` /
   `push` / `push.route` / `storage`) with one-line semantics for every
   field. Respect the credential rules: only `env:` / `keychain:`
   references; a YAML with plaintext credentials refuses to start.
2. **Generate the category YAML** from the user's request: pick one engine
   per source (prefer L1 API → L2 static HTML → L3 crawl4ai; the runtime
   auto-degrades), set a cron schedule, watchlist keywords, a dedup key
   template, and a push route. Use `plugins/stocks.yaml` as the full
   12-section reference.
3. **Run it**: `myia run <category>.yaml`.
4. **Interpret `myia doctor`** — it prints structured diagnostics (source
   health, failing engine layer, missing credentials). Apply the fix to the
   YAML or the environment and re-run; do not ask the user to read logs.

## The AI-native loop

```
user states a need → agent reads the schema → agent writes the YAML
→ myia runs the pipeline → agent reads doctor output and self-repairs
→ push cards reach the user → user decides (valuable / not valuable)
```

The user decides; the agent does the rest.
