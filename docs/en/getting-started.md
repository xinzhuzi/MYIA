# Getting Started

> Pre-alpha note: this documents the target v0.1 experience; most commands
> are stubs in the current skeleton.

## 1. Install

```bash
pip install myia
```

## 2. Configure credentials

MYIA never stores plaintext credentials in YAML. Reference them with:

- `env:VAR_NAME` — read from the environment at run time
- `keychain:name` — read from the OS keychain (macOS Keychain / Windows DPAPI)

Typical setup: an LLM key for enrichment and a push channel target:

```bash
export OPENAI_API_KEY=...   # or any OpenAI-compatible endpoint
export FEISHU_CHAT_ID=...   # feishu_card push target
```

## 3. Run your first plugin

```bash
myia run plugins/wool.yaml
```

Or ask an AI agent instead — it reads
[write-a-plugin.md](../write-a-plugin.md) and generates the category YAML
for you (see [skill/SKILL.md](../../skill/SKILL.md)).

## 4. Where data lives

Everything is stored in a single SQLite file (`myia.sqlite` by default):
fetched items, the dedup registry, change baselines, push feedback and run
history. Set `storage.retention` in a plugin YAML to auto-purge old items.
