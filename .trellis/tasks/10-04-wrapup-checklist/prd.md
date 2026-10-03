# 发布与收尾待办清单(主人门禁+装机验收+待确认处置)

## Goal

2026-10-04 按 trellis 做完全部任务工作流(12/12 闭环)后的剩余问题清单落档:主人门禁/装机验收/在途知悉/待确认处置,待办不悬对话。

本档为收尾清单档:口径如实记录,不新增判断;各项处置动作按节内标注的责任方(主人/AI/并行会话)走,不在本档内展开实现。

## Requirements

### 一、主人门禁(只有主人能做)

1. PyPI 双包 pending publisher:pypi.org 注册 `myia`/`myia-classifier` 时 Repository 填 `shishi`;完成后回话,由 AI 代跑 Re-run + v0.0.1 发布线 AC8 终勾。
2. GHCR 包页清理。

### 二、装机/真机验收(代码已就绪,留装机)

1. 桌面端重装 /Applications 目视冒烟(12 任务闭环后的新构建,工作流止于 `tauri build`)。
2. 消息平台 W2 换装冒烟。
3. keychain `-25244` 修复的跨进程验收(修复已入库 `9652cf3`)。

### 三、长期挂账

- 真机飞书/TG 收卡。
- 活 GLM key 的 enrich 真跑。

### 四、待主人确认的处置

1. 疑死工作流处置(确认后停,防双跑):`dwfrun-202c9176` / `dwfrun-f1a9a0b9` / `dwfrun-c72abec5` / `dwfrun-2f494cf4` / `dwfrun-39d071be` / `dwfrun-2dc0d9a3`。
2. 归档会话收走 10 个 review 态任务档(grill 决议:独立会话)。

### 五、在途知悉(并行会话自收,勿重复立项)

- `messaging-w3-longtail`(残余红测 `test_join_403` 归它)。
- `shishi-everywhere`。
- `10-04-alert-rules`(G5 告警规则已立档)。
- `v12-backlog` 池。

## Acceptance Criteria

- [ ] 档已建:prd 四+一节齐全(一~五节按上口径落档)。
- [ ] `implement.jsonl` / `check.jsonl` 各一条指向本 prd。
- [ ] `task.py validate` 通过。
