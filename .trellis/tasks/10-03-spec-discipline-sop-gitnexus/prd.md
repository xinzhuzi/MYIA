# 工程纪律与搜索 SOP 迁移入 spec + GitNexus 索引落地

> 2026-10-03 主人指令:把源私有仓库(工程铁律体系与搜索 SOP;真实指针见 `LOCAL-NOTES.md`)中与 MYIA 相关的有用内容迁移过来。范围经三轮确认:三条通用工程纪律 + 搜索 SOP + `.gitnexus` 相关。

## 背景

源仓库的 AGENTS.md 在 Trellis 块外有四段内容(工程铁律指针、macOS 打包入口、GitNexus 块、ComfyUI 产线知识库)。逐段评估结论(2026-10-03 会话核实):

| 源仓库内容 | 迁移判定 |
|---|---|
| 铁律 0–7 正文(`.claude/CLAUDE.md`) | 仅蒸馏三条 MYIA 尚无明文的通用纪律;其余已被 MYIA 既有机制覆盖(Trellis workflow / security-baseline / os-etiquette / 跨会话记忆)或属源仓库专属 |
| macOS 打包入口(`build-mac.sh`) | 不迁:MYIA 是 Tauri + `build-sidecar.sh`,已有自己的冒烟铁律 |
| GitNexus 块与索引 | 迁:本机 CLI 已装(v1.6.6),给 MYIA 建索引 + AGENTS.md 使用块 |
| ComfyUI 产线知识库 | 不迁:与 MYIA(情报中枢)无关;唯一交集(qwen3vl 模型借用)已在 10-03-image-input prd 记录 |

## Requirements

1. **`.trellis/spec/guides/engineering-discipline.md`** — 三条纪律蒸馏(通用化改写,不带源仓库语境):
   - 长任务监控:后台任务 + `命令 > log 2>&1; echo $? > exit文件` + 完成通知,禁 sleep 轮询;
   - 先报量再动手:>30 分钟的活先报规模预估+步骤清单取得批准;查改分家(只读探查不受门禁,写操作才报批);
   - 高星参考:相关实现先参考 GitHub 高星项目,多方对比 + 可行性分析,不闭门自写。
2. **`.trellis/spec/guides/search-sop.md`** — 搜索 SOP 改写为 MYIA 布局(`src/myia` Python 包、`desktop/` Tauri、`tests/`、`docs/` zh/en、仓库外 `com.myia.app` 数据目录),保留通用骨架(中文路径陷阱、先 Read 再操作、rg/fd 分工、网络搜索路由、GitNexus 用法),剥除源仓库专属(ComfyUI/模型资产查源/其 APP 数据目录)。
3. **GitNexus 落地**:`.gitignore` 加 `.gitnexus/`;跑 `gitnexus analyze --embeddings` 建 MYIA 索引;AGENTS.md(Trellis 块外)加使用块(多仓消歧 `-r MYIA`、impact/detect-changes 门禁、技能指全局真身路径)。
4. **索引接线**:`.trellis/spec/index.md` 与 `.trellis/spec/guides/index.md` 增两 guide 条目;AGENTS.md 加指针段。

## Acceptance Criteria

- [x] 两个 guide 文件落盘,内容为 MYIA 语境(无源仓库路径与其项目名残留)
- [x] 两个 spec index 均有指向新 guide 的活链接
- [x] `.gitignore` 含 `.gitnexus/`;`gitnexus status` 在 MYIA 根可报出 MYIA 索引(符号/关系/执行流数 > 0)
- [x] AGENTS.md Trellis 管理块内容未动,新段落均在块外
- [x] 残留扫描:两 guide 对源仓库标识(中/英文名)与 ComfyUI 词根零命中(首轮 3 处出处/消歧类命中,决议 1 泛化后归零)

## 执行结果(2026-10-03)

- 提交:主体 `1ff1058`(两 guide + 双 index 接线 + AGENTS.md 块外指针段与实况段 + .gitignore 防副作用),收口 `c9747d2`(journal + 转 review);任务目录 check.jsonl / implement.jsonl 为并行会话 845f64a 顺带提交的空占位
- GitNexus:全量 `analyze --embeddings` 175s(7114 节点/15389 边)→ 增量 `--index-only`(7154 节点/15449 边,实测无 AI 上下文副作用);`query -r MYIA` 实测返回真实执行流;`status` 曾 up-to-date(并行会话持续推进 HEAD,常态 stale 属预期,见决议 5)
- 质量:trellis-check 两轮——首轮 FAIL(六项发现)→ 修复 → 复验 PASS,详见 [evidence/check-report.md](./evidence/check-report.md)
- PRD 前提偏差(执行中被 check 纠正):①gitnexus 技能全局真身在 `~/.agents/skills/gitnexus-*`,本 PRD 原写 `~/.zcode/skills/` 有误;②本机 MCP server 未注册,CLI 是唯一通道(PRD 隐含 MCP 可用);③裸 `analyze` 会改写 AGENTS.md 工具管理块并在仓库根生成 `.claude/`+`CLAUDE.md` 副本,PRD 未预见——处置:副本入 .gitignore + 日常刷新一律 `--index-only` + 块外引言段声明「冲突以本段为准」

## 主人决议(2026-10-03 grill 六问,全按推荐)

1. **公开仓私有名泛化**:公开跟踪文件中的源仓库名与本机路径全部泛化为「另一私有仓库/源仓库(真实指针见 `LOCAL-NOTES.md`)」,真实出处入 LOCAL-NOTES;扩大后范围 = 两 guide 3 处 + 本 prd + task.json description + 本会话 journal 条目
2. **两轮 check 报告落档** `evidence/check-report.md`
3. **prd 收口** = 勾验收框 + 执行结果节,维持 PRD-only,不补 design/implement 追记
4. **os-etiquette 反向互链不加**(该文件当时正被并行会话改;engineering-discipline 单向链接够用)
5. **索引保鲜不挂 pre-commit/CI**(并行提交频繁,恒 stale 属常态;靠 SOP「stale 就 --index-only」规程)
6. **AGENTS.md 工具块两处已知瑕疵维持现状**(计数漂移、CLI 表指向已删的 `.claude/skills/`):工具领地不对抗,块外引言段已覆盖

## 明确不做

- 不迁 macOS 打包段、ComfyUI 知识库、源仓库专属铁律(其固定工作流只读律、文件命名 reorg)
- 不在 MYIA 建 `.claude/`(gitnexus 技能真身在全局 `~/.agents/skills/gitnexus-*`;analyze 的 `.claude/` 副本已 gitignore)
- 不动并行会话在途的 yaml-editor 任务指针与状态
