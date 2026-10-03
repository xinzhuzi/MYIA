# PRD:feed-ux 批次——条目搜索 / 详情原文 / 导出 / 排程管理

## 需求源

`10-03-ui-feature-census` 普查(2026-10-03)grill Round 1 Q1 主人批复「按推荐立项」。
范围、划界、排期均按批复执行;**复杂任务三件套已补齐(design.md + implement.md,
2026-10-03),达「可 start」状态;排期仍等 v1.1.1 tag**。

## 范围(六项)

1. **G1 条目搜索**:情报流屏搜索框(标题/摘要/来源);需 sidecar `store` 查询扩展。
2. **G2 条目详情与打开原文**(含普查 C9,同件事):卡片可展开全文;「在浏览器打开」
   走系统默认浏览器(item.url 现只在 title 悬浮)。
3. **G3 数据导出**:情报流导出 JSONL + CSV(本地写文件),复用 store 查询。
4. **G4 排程管理 + 手动触发**:仪表盘品类卡「跑一次」(run.start 现成,health.plugins
   供 yaml 路径);排程页(或源管理分区):排程一览 + Next runs 预览
   (Apify 式,防 cron 写错)+ 手动启停单品类调度。
5. **C8 顺路**:顶栏品类全局选择器接线(现为 defaultValue="all" 死骨架)——
   选中即过滤情报流(客户端过滤或协议参数,设计期定)。
6. **G5 前半搭车**:设置屏「推送测试消息」按钮(一次 RPC,验证飞书/TG 通道通断)。

## 划界(与 v112-desktop-batch,grill Q1 批复)

- **随本批**:C9(=G2)、C8。
- **留 v112-desktop-batch(协议侧)**:C5(secret.delete 入口)、C13(试抓此源)。
- **合参**:G1/G3 的 store 查询扩展与 v112-batch 的 C1(游标协议)同一协议面,
  设计期合并考虑;协议版本号(entry.py PROTOCOL_VERSION)统一 +1,不各加各的。

## 排期与批内顺序

- **v1.1.1 tag 后开工,与 v112-desktop-batch 并行**(C2 sidecar 救回仍最优先,
  v112-batch 线不受本批挤占)。
- 批内顺序:G2/C8(轻,零~小协议)→ G4(run 通道现成)→ G1/G3(协议扩展,与 C1 合参)。

## 设计期事实(2026-10-03 实查,定形于 design.md §0,以 design 为准)

- capabilities = `core:default` + `updater:default` + `process:allow-restart` +
  **`dialog:default`(看图屏已加,保存对话框可用)**;webview 零 shell 执行授权(v1.1 评审姿势)。
  G2 打开原文需新增 **scoped `shell:allow-open`(仅 https?://)+ JS 壳包**;
  G3 导出 = `dialog.save()` 选路径 + sidecar 直写(不经 webview)。
- 情报流本地态(已读/星标/稍后读)在 localStorage;搜索若要覆盖已读历史,
  走 sidecar 查询而非本地过滤(本地只有已加载页)。

## 验收标准

- [ ] G1:搜索框可用,可按关键词过滤条目(含未加载页数据,协议级)
- [ ] G2:卡片可展开全文;「打开原文」调起系统浏览器;C9 消号
- [ ] G3:可导出当前过滤视图为 JSONL 与 CSV,文件落盘可查
- [ ] G4:仪表盘品类卡「跑一次」可用且反馈 run 状态;排程页含 Next runs 预览
- [ ] C8:顶栏品类选择器实际过滤情报流,空壳注释清除
- [ ] G5 前半:设置屏可发推送测试消息,成功/失败结构化回显
- [ ] 协议扩展同步 tests/test_desktop_sidecar_protocol.py 与 ui-src vitest;协议版本 +1 有记录
- [ ] pytest + vitest 全绿;重打包冒烟照 brand-trim 口径(装 /Applications 目视)

## 关联

- 需求源:`.trellis/tasks/10-03-ui-feature-census/prd.md`(G 矩阵与证据)
- 并行批:`.trellis/tasks/10-03-v112-desktop-batch/prd.md`(C5/C13/C1 划界)
- 未入选项(G5 主体告警规则等)→ `10-03-v12-backlog` 池

> **冲突裁定注记(2026-10-03 · 来源:零冲突收尾工作流)**
>
> 排程门已过(git tag 实查存在 v1.1.1),拦路是并行 UI/视觉线占用 8 个在途文件;等
> feed-screen.tsx / feed-screen.test.tsx / client.ts / types.ts / settings-screen.tsx /
> App.tsx / test_desktop_sidecar_protocol.py / pipeline.py 净后即可开工;entry.py、
> store/sqlite.py、top-bar.tsx、app-layout.tsx、dashboard、sources 屏此刻干净,
> 开工时复测 git status 即可。(冲突证据:在途同文件(git status 实查):
> desktop/ui-src/src/screens/feed/feed-screen.tsx、feed-screen.test.tsx、
> desktop/ui-src/src/lib/api/client.ts、types.ts、
> desktop/ui-src/src/screens/settings/settings-screen.tsx、desktop/ui-src/src/App.tsx、
> tests/test_desktop_sidecar_protocol.py、src/myia/pipeline.py)

> **2026-10-04 归档会话注记**:AC 框为交付会话遗留未逐勾,不作为未完成证据;交付与验收以既录证据为准——六件套已入 HEAD(769ebd1/1d255b7);dwfrun-6adbfbd0 独立质检+全量门禁全绿(vitest 262/262、协议 pytest 105/105、全量 3037)。装机/真机类冒烟项统一移交 `10-04-wrapup-checklist` 装机验收节。
