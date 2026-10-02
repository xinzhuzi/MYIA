# docs 小修批次:env.example 键名 + docs pip 宣称 + task.py finish 防护

## Goal

普查 A3/A4 与 A1 的 docs 部分一次小 PR 纠错(照做即失败级),搭车 task.py finish
跨会话防护。**立即开工**(grill 2026-10-03 Q5:推提交 + docs 小修不等 tag)。

## Requirements

1. **A3**:docker/env.example 的 `TG_BOT_TOKEN`/`TG_CHAT_ID` 改为代码真实解析的
   `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`(src/myia/push/telegram.py:59-61)。
2. **A4**:env.example LLM 段 `OPENAI_API_KEY=` 与文档教的
   `MYIA_LLM_BASE_URL`/`MYIA_LLM_KEY` 键名对齐(docs/zh/getting-started.md:44-46、
   skill/SKILL.md:182-183 为准),文件内「键名对齐后调整」自注随之消除。
3. **A1 docs 部分**:docs/zh/getting-started.md:14、docs/en/getting-started.md:17、
   docs/launch/linuxdo.md:76 的裸 `pip install -e .` 宣称修正(根包依赖
   myia-classifier 不在 PyPI,pip 不可解析)——改 uv 用法或加缓冲说明;
   **README.md 两处(132/393)不在本档**,随 `10-03-v111-release` 的 README 升格
   一并改(防双头改打架)。
4. **Q8 搭车**:.trellis/scripts/task.py 的 finish 增加跨会话防护——resolve 出的
   当前任务若属其他会话(session-fallback),须显式确认/`--force` 才清,并打印
   来源会话(2026-10-03 实证误清过并行会话指针,无状态损伤但风险在)。
5. 顺带:B5 推提交是**动作不是工单项**(grill 批后即推,本档注记即可)。

## Acceptance Criteria

- [ ] A3/A4:env.example 全部键名与代码解析名一致,grep 无自认待办注释
- [ ] A1 docs 部分:三处文档的安装指引照做可达(或明确标注仅 uv workspace 可装)
- [ ] task.py finish:清非本会话任务时有确认拦截,单测或手动演示记录
- [ ] pytest 双跑法全绿;一个 commit
