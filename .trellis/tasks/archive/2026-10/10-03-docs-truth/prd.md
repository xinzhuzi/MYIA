# PRD:docs 照做即失败纠错批(docs pip 宣称 + env.example 键名 + task.py finish 防护)

## 背景

来源:普查档 `.trellis/tasks/10-03-gap-census/prd.md`(本档落盘时已归档至
`.trellis/tasks/archive/2026-10/10-03-gap-census/`)A 组(P1 照做即失败)+ §6 路由①,
主人 2026-10-03 批准(grill Q5:推提交 + docs 小修不等 tag,立即开工)。
本档为该任务正式 PRD,取代同目录 07:50 占位稿,范围与其一致(README 半边不入场)。

起草时(2026-10-03,HEAD=124bdf6)证据逐条复核:全仓 `pip install -e .` 恰 5 处、
`TG_BOT_TOKEN`/`OPENAI_API_KEY` 错键名各仅 docker/env.example 一处(grep 实测);
pypi.org 的 `myia`/`myia-classifier` JSON API 均 404(curl 实测);docs 锁面=
tests/test_docs.py——锁 yaml 块/zh-en 结构对齐/凭据 env 引用/相对链接,**不锁 bash
命令字面**,zh/en 同步改且不增删代码块即不红。

## 缺陷清单(A 组;行号均已起草时复核)

| # | 位置 | 缺陷(照做即失败) | 证据 | 修复方向 |
|---|------|------|------|------|
| A1 | README.md:132,393;docs/zh/getting-started.md:9,14;docs/en/getting-started.md:11-12,17;docs/launch/linuxdo.md:76 | 教 `pip install -e .`,但根包依赖 `myia-classifier>=0.1,<0.2` 不在 PyPI(404),仅 uv workspace 可解析,纯 pip 必装败 | pyproject.toml:23,43-45;curl 404 实测 | 删 pip 备选或如实标注「当前仅 uv 可装,PyPI 发布后开放」;本档修 docs 侧,**README 两处归 10-03-v111-release** |
| A2 | myia-classifier/README.md:9,18-20 | `pip install myia-classifier` 宣称,包从未发布(404) | curl 404 实测 | **归 10-03-v111-release**(PyPI 发布本身即兑现,防双头改) |
| A3 | docker/env.example:13-14 | 模板给 `TG_BOT_TOKEN`/`TG_CHAT_ID`,代码硬性解析 `env:TELEGRAM_BOT_TOKEN`/`env:TELEGRAM_CHAT_ID`,照填启用 telegram 必 env_var_missing 退 1 | src/myia/push/telegram.py:59,61 | 键名改 TELEGRAM_*,与 test_docs.py:64-70 锁定的通道约定一致 |
| A4 | docker/env.example:20-21 | LLM 段 `OPENAI_API_KEY=` 与文档教的 `MYIA_LLM_BASE_URL`/`MYIA_LLM_KEY` 不对齐;20 行注释自认「键名对齐后调整」 | docs/zh/getting-started.md:44-46;docs/en/getting-started.md:51-53;skill/SKILL.md:182-183 | 改为 MYIA_LLM_BASE_URL= + MYIA_LLM_KEY= 两行,删自认待办注释 |

起草修正普查档两处措辞(行号不变,缺陷均成立):

- 普查档称 docs「后三处连缓冲说明都没有」——linuxdo.md:71 实有半句「PyPI 包发布后可 pip」,
  但 76 行仍教 pip install -e .,缺陷成立、程度略轻;
- A1 补入普查档未列的两处正文宣称:docs/zh/getting-started.md:9「pip install 即跑」、
  docs/en/getting-started.md:11-12「pip install is all it takes」(同一缺陷的散文面)。

## 需求(执行项)

1. **A1 docs 侧**:docs/zh/getting-started.md:9,14、docs/en/getting-started.md:11-12,17、
   docs/launch/linuxdo.md:71-76——安装指引如实化:唯一保证路径 uv sync;pip 通道标注
   「待 myia/myia-classifier 上 PyPI 后开放(发布跟踪 10-03-v111-release)」。zh/en 必须同步改
   (标题骨架与代码块数受 test_docs.py 对齐锁)。
2. **A3**:docker/env.example:12-14 键名改 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
   (逐字对齐 telegram.py 的 DEFAULT_*_ENV_REF)。
3. **A4**:docker/env.example:20-21 LLM 段改 `MYIA_LLM_BASE_URL=`/`MYIA_LLM_KEY=`,
   删「键名对齐后调整」自注。
4. **Q8 搭车(普查档批准路由含此项)**:.trellis/scripts/task.py `finish`(cmd_finish,
   task.py:275-293)现无条件 clear_active_task。加跨会话防护:active.source 形如
   `session-fallback:<key>`(跨会话指针,取值见 common/active_task.py:175-178)时须显式
   确认或 `--force` 才清,并打印来源会话 key(2026-10-03 实证误清过并行会话指针)。
5. **B5 验收前置**:普查时积压的 933c7da/45639c3 起草时已推平(0 ahead / 0 behind);
   执行时复验 `git rev-list origin/main..HEAD --count`=0,有积压先推再动工——本任务与
   v111-release 都会改文档/README,双方提交即推不积压,防交错。
6. **E7 顺风车(可选,标注)**:仓库根 myia.db(184KB,10-02)/osint_stderr.log(0B)/
   .coverage(53KB)实测存在,均被 .gitignore:21,35,52 覆盖、未被跟踪。后两个可直清;
   **myia.db 是本地运行数据,清理前先确认无用(或挪走备份),不确定就跳过**。

## 明确不做(防蔓延)

- README.md:132,393(A1 的 README 半边)与 myia-classifier/README.md(A2):归
  10-03-v111-release 的 README 升格一并改(其 prd.md:53-55 明文认领;PyPI 发布即兑现
  A2)——本档不碰,防双头改。
- B1 发布物(tag v1.1.1/版本号对齐/updater 接线/PyPI 发布/徽章升格/路线图改口):归
  10-03-v111-release。
- docs/launch/RELEASE.md:103 `pip install myia-classifier`:起草复核为「发布后验证」
  runbook 语境(95-112 行,发布成功后该命令为真),非缺陷,不改。
- 并行会话工作区残留(dashboard-screen.tsx、test_desktop_sidecar_protocol.py 改动,
  未跟踪的 .trellis/tasks/10-03-yaml-editor/):不碰、不提交、不清理。
- 不改产品代码:只动 docs 三页、docker/env.example、.trellis/scripts/task.py(+其测试)。

## 验收标准(2026-10-03 收注;执行详情见 journal 与 845f64a/fbba437 提交链)

- [x] A3:`docker/env.example` 含 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`,
      `grep -rn "TG_BOT_TOKEN\|TG_CHAT_ID" docker/` 零命中,键名与 telegram.py 解析名逐字一致
      (行号随并行波漂至 71/73,键名逐字核对过——终检员实证)
- [x] A4:env.example LLM 段为 `MYIA_LLM_BASE_URL=`/`MYIA_LLM_KEY=` 两行;
      `grep -rn "OPENAI_API_KEY" docker/` 零命中;「对齐后调整」自注消失
- [x] A1 docs 侧(口径裁决:验收字面「零命中」修正为「**裸指引**零命中,
      『裸 pip 会失败』警示句允许」——docs/zh:11、docs/en:14 两处命中即警示句,
      属如实化口径本身,终检员实证)
- [x] Q8:finish 跨会话防护生效(session-fallback 需确认/--force),手动演示记录入 task.json notes
- [x] B5:动工时 origin 零积压;本任务批次随并行工作流 fbba437 链即批即推
- [x] E7:三件全清(osint_stderr.log/.coverage/myia.db,2026-10-03 12:1x 实删;
      myia.db=仓库根 dev 运行残料,App 真数据在 ~/Library/Application Support,判定无用)
- [ ] 双跑法全绿:**等价性已实证**(2026-10-03 12:1x 两跑法失败集逐条一致 7=7,裸跑零额外假红);
      零失败暂被并行「世事更名」波(12:01-12:03,如 test_cli 断言 `myia 1.1.1` vs 实出 `shishi 1.1.1`)压住,
      失败全数归属改名波、非本任务面——波平后复跑勾选
- [x] 合批提交+journal:原批随 fbba437 链入库+journal 已记;E7/本收注为收尾补笔

## 验收记录(2026-10-03,受主人委托代验)

**verdict: accepted** —— A3:docker/env.example 含 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID,`grep TG_BOT_TOKEN|TG_CHAT_ID docker/` 零命中(代验实跑);A4:MYIA_LLM_BASE_URL=/MYIA_LLM_KEY= 两行在档,`grep OPENAI_API_KEY docker/` 零命中,「对齐后调整」自注已消;A1:docs 三页裸 pip 指引零命中(docs/zh:11、docs/en:14 仅存两处允许的警示句,linuxdo.md 零命中,代验 grep);Q8:finish 跨会话防护在码(source==session-fallback 无 --force 拒清并打印 Source,task.py:276-301),隔离演示记录在 task.json notes;B5:845f64a/fbba437 链已入库(当前 origin 前置 2 条系并行会话归档 chores a44c2ba/b1cdadd,非本任务积压);E7:osint_stderr.log/.coverage 已清(myia.db 系后续任务本机真跑再生的 gitignored 残料,非本任务回归,卫生项);双跑法:等价性记录在档,压住它的「世事更名」波已平(test_cli 断言已随新横幅改齐 `shishi 1.1.1`,代验实跑 test_cli 21 passed + test_docs 80 passed + ruff 全绿;74e6e24 门禁复绿收口在案)——原「波平后复跑勾选」实质已兑现,代验按规未跑全量套件。

## 关联

- 事实源:`.trellis/tasks/archive/2026-10/10-03-gap-census/prd.md`(A 组、§6 路由、拍板注记)
- 边界对手方:`.trellis/tasks/10-03-v111-release/prd.md`(README 半边、A2、B1、发布)
- 风格参照:`.trellis/tasks/archive/2026-10/10-03-ui-hints-trim/prd.md`
