# 质量复核报告(trellis-check 两轮)

> 复核方:trellis-check subagent(独立只读,两轮均未做任何修改);对象:本任务六项产出 + 仓内副作用。
> 2026-10-03:首轮 **FAIL**(六项发现)→ 修复 → 定向复验 **PASS**。本报告为两轮合并精简版,原文见当轮会话记录;按主人决议 1,源私有仓库名一律泛称「源仓库」。

## 首轮:FAIL(按严重度)

1. **[高] PRD「明确不做」违例**:裸跑 `gitnexus analyze` 在仓库根自动生成 `.claude/skills/gitnexus/`(6 个 SKILL.md)与 `CLAUDE.md` 副本(mtime 与 `.gitnexus/` 同秒),未跟踪状态有误提交风险。
2. **[中] MCP 注册声明失实**:SOP 原文称 gitnexus MCP server 已注册于 `~/.zcode/cli/config.json`;实测其 `mcp.servers` 仅 filesystem-ma/sequential-thinking/web-reader/web-search-prime/zread,无 gitnexus——按原文找 MCP 工具会扑空。
3. **[中] AGENTS.md gitnexus 块偏离 PRD**:analyze 用工具标准块**替换**了手写块(markers 间是工具领地,手写内容被覆盖);块内 CLI 表指向 `.claude/skills/`;多仓消歧未在块内。
4. **[低] 技能全局路径失实**:gitnexus-* 技能真身在 `~/.agents/skills/`(7 个,含 pr-review),PRD 与 SOP 原写的 `~/.zcode/skills/` 零命中(PRD 前提错误被沿袭)。
5. **[低] 中文命名举例失实**:原文「`.trellis/tasks/`、`docs/zh/`、`skill/` 下有中文命名」——os.walk 全扫三处 0 命中(检测方法经源仓库同目录 204 命中的阳性对照验证),系从源仓库语境误带入;铁律本身通用,事实举例错。
6. **[信息] 索引 stale**:并行会话持续推进 HEAD,indexed commit 落后——环境漂移,非缺陷。

六项产出本体(三纪律 guide、MYIA 版 SOP、双 index 接线、TRELLIS 块外追加、.gitignore 条目、内容准确性抽查全部命中)首轮即全部合格;FAIL 全部来自副作用与事实声明。

## 修复动作

- 删除 `.claude/` + `CLAUDE.md`(未跟踪副作用副本);`.gitignore` gitnexus 分组新增两条规则(注释注明:analyze 自动副本不提交,手写 CLAUDE.md 前须先移除规则)
- SOP:GitNexus 节改「本机 MCP server 未注册,CLI 是当前唯一通道;`rename` 仅 MCP 可用故暂不可用,符号重命名走手动多文件改+全树 `rg` 核验+测试兜底」;技能路径改 `~/.agents/skills/gitnexus-*`;中文举例改「本仓库当前文件名全 ASCII(2026-10-03 全仓扫描核实),风险主要在仓库外与未来新增文件」;维护节新增「AI 上下文副作用」条目(裸 analyze 副作用清单 + 日常刷新一律 `analyze --index-only`)
- AGENTS.md:保留工具管理块不对抗;块**外**新增「MYIA 的 GitNexus 实况」引言段(CLI 唯一通道 / 必须 `-r MYIA` / `detect_changes()` 的 CLI 等价与文档类豁免 / 刷新用 `--index-only` / rename 不可用与替代路径 / 技能真身路径 / 坑清单指针 / 冲突以本段为准)
- 实测:`gitnexus analyze --index-only` exit 0,索引 7154 节点/15449 边,副本零复发,`status` 一度 up-to-date

## 复验轮:PASS

- 六项修复逐项通过,且关键事实声明经复核方独立实测:`gitnexus setup --help` 证实支持列表(Cursor/Claude Code/OpenCode/Codex)无 ZCode;`--index-only` 旗标真实存在("Pure index mode: skip all file injection");`git check-ignore` 子路径命中验证 ignore 规则生效;7 个全局技能目录均含 SKILL.md
- 重跑首轮 AC:残留扫描仅 3 处允许类别(出处注记×2 + 多仓消歧仓库名×1,决议 1 泛化后归零);TRELLIS 块与 `git show HEAD:AGENTS.md` 逐字节一致(`TRELLIS-BLOCK-IDENTICAL`);交叉污染零——未跟踪项仅剩两个 guide 产出与并行会话的任务目录,`.claude/`/`CLAUDE.md` 残留为零
- 信息级备注两条(无需整改):①`status` 再显 stale 属并行 HEAD 推进的环境漂移,引言段+SOP「计数以 status 为准」已覆盖;②`.gitignore` 出现并行会话新增的 `desktop/.pyinstaller-cache/`(正确无害,已在主体提交信息注明)
