# 工程纪律与搜索 SOP 迁移入 spec + GitNexus 索引落地

> 2026-10-03 主人指令:把 MYStudio(`/Users/zhengbingjin/Project/Github/MYStudio`)AGENTS.md 体系中与 MYIA 相关的有用内容迁移过来。范围经三轮确认:三条通用工程纪律 + 搜索 SOP + `.gitnexus` 相关。

## 背景

MYStudio 的 AGENTS.md 在 Trellis 块外有四段内容(工程铁律指针、macOS 打包入口、GitNexus 块、ComfyUI 产线知识库)。逐段评估结论(2026-10-03 会话核实):

| MYStudio 内容 | 迁移判定 |
|---|---|
| 铁律 0–7 正文(`.claude/CLAUDE.md`) | 仅蒸馏三条 MYIA 尚无明文的通用纪律;其余已被 MYIA 既有机制覆盖(Trellis workflow / security-baseline / os-etiquette / 跨会话记忆)或属 MYStudio 专属 |
| macOS 打包入口(`build-mac.sh`) | 不迁:MYIA 是 Tauri + `build-sidecar.sh`,已有自己的冒烟铁律 |
| GitNexus 块与索引 | 迁:本机 CLI 已装(v1.6.6),给 MYIA 建索引 + AGENTS.md 使用块 |
| ComfyUI 产线知识库 | 不迁:与 MYIA(情报中枢)无关;唯一交集(qwen3vl 模型借用)已在 10-03-image-input prd 记录 |

## Requirements

1. **`.trellis/spec/guides/engineering-discipline.md`** — 三条纪律蒸馏(通用化改写,不带 MYStudio 语境):
   - 长任务监控:后台任务 + `命令 > log 2>&1; echo $? > exit文件` + 完成通知,禁 sleep 轮询;
   - 先报量再动手:>30 分钟的活先报规模预估+步骤清单取得批准;查改分家(只读探查不受门禁,写操作才报批);
   - 高星参考:相关实现先参考 GitHub 高星项目,多方对比 + 可行性分析,不闭门自写。
2. **`.trellis/spec/guides/search-sop.md`** — 搜索 SOP 改写为 MYIA 布局(`src/myia` Python 包、`desktop/` Tauri、`tests/`、`docs/` zh/en、仓库外 `com.myia.app` 数据目录),保留通用骨架(中文路径陷阱、先 Read 再操作、rg/fd 分工、网络搜索路由、GitNexus 用法),剥除 MYStudio 专属(ComfyUI/Civitai/漫影数据目录)。
3. **GitNexus 落地**:`.gitignore` 加 `.gitnexus/`;跑 `gitnexus analyze --embeddings` 建 MYIA 索引;AGENTS.md(Trellis 块外)加精简使用块(多仓消歧 `-r MYIA`、impact/detect-changes 门禁、技能指全局 `~/.zcode/skills/gitnexus-*`)。
4. **索引接线**:`.trellis/spec/index.md` 与 `.trellis/spec/guides/index.md` 增两 guide 条目;AGENTS.md 加指针段。

## Acceptance Criteria

- [ ] 两个 guide 文件落盘,内容为 MYIA 语境(无 MYStudio 路径/漫影术语残留)
- [ ] 两个 spec index 均有指向新 guide 的活链接
- [ ] `.gitignore` 含 `.gitnexus/`;`gitnexus status` 在 MYIA 根可报出 MYIA 索引(符号/关系/执行流数 > 0)
- [ ] AGENTS.md Trellis 管理块内容未动,新段落均在块外
- [ ] `rg 'MYStudio|漫影|comfyui' .trellis/spec/guides/engineering-discipline.md .trellis/spec/guides/search-sop.md` 无业务残留(GitNexus 多仓消歧示例中的仓库名除外)

## 明确不做

- 不迁 macOS 打包段、ComfyUI 知识库、MYStudio 专属铁律(漫影工作流只读、09-22 文件命名 reorg)
- 不在 MYIA 建 `.claude/`(ZCode 技能已全局存在于 `~/.zcode/skills/gitnexus-*`)
- 不动并行会话在途的 yaml-editor 任务指针与状态
