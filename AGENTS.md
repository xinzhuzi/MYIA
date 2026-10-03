<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

## 工程纪律与搜索 SOP

本仓库通用工程纪律——长任务监控(后台任务 + exit 文件,禁 sleep 轮询)、先报量再动手(>30 分钟报规模清单;查改分家)、高星参考(写码先参考 GitHub 高星实现)——维护在 [`.trellis/spec/guides/engineering-discipline.md`](.trellis/spec/guides/engineering-discipline.md)。

**任何仓库/仓库外/网络搜索前,先完整读取[搜索 SOP](.trellis/spec/guides/search-sop.md)**(仓库内热路径 + 仓库外本地路径 + 网络搜索路由 + GitNexus 用法;两大铁律:中文路径勿手拼、先 Read 再操作)。

> **MYIA 的 GitNexus 实况**(下方 gitnexus 块为工具自动生成的通用版,与本段冲突时以本段为准):本机 MCP server 未注册,**CLI 是唯一通道**;全局多仓索引,查询命令必须带 `-r MYIA`(不带会报 "Multiple repositories indexed");`detect_changes()` 的 CLI 等价 = `gitnexus detect-changes -r MYIA --scope staged`(文档/配置类提交可豁免);索引刷新用 `gitnexus analyze --index-only`(免 `CLAUDE.md`/`.claude/` 副作用副本,二者已 gitignore);`rename` 仅 MCP 可用故暂不可用,符号重命名走手动多文件改 + 全树核验 + 测试兜底;技能参考在全局 `~/.agents/skills/gitnexus-*/SKILL.md`;WAL 损坏/单写者锁/管道吞退出码等坑清单见搜索 SOP 的 GitNexus 节。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **MYIA** (7114 symbols, 15389 relationships, 277 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/MYIA/context` | Codebase overview, check index freshness |
| `gitnexus://repo/MYIA/clusters` | All functional areas |
| `gitnexus://repo/MYIA/processes` | All execution flows |
| `gitnexus://repo/MYIA/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
