# v1.1.2 桌面补全批次:C2 sidecar 救回最优先

## Goal

普查 C 组 13 条桌面协议/UX 缺口按伤害度分批修;**排 v1.1.1 tag 后开工**
(grill 2026-10-03 Q5)。复杂批次:start 前补 design/implement。

## Requirements(优先级序,自普查 C1–C13)

1. **C2(最伤用户)**:sidecar 崩溃(Terminated)后不重启、此后所有请求永得
   `sidecar_not_running`,顶栏「重新探测」救不回 → 壳层 respawn 策略 +
   run 取消通道(与 C12 同源,一并考虑)。
2. **C1**:store.items 无 before/offset,同刻条目超单页 limit 翻页游标卡死 →
   协议补游标(协议版本号随之 +1,entry.py PROTOCOL_VERSION)。
3. **C3**:run.status 只读内存,重启即空 → sidecar 增 runs.list 方法直读
   store runs 表(仪表盘历史成功率可达)。
4. **C7(顺手)**:client.ts 补 sources.write 封装;sources/api.ts 与
   client.ts 的「method_not_found/一一对应」失实注释修正。
5. 批内按余量取舍:C5(secret.delete 入口)/C8(品类选择器接线或移除)/
   C9(条目卡打开原文)/C13(试抓此源);C4/C6/C11 属协议缺口登记,
   有余力再议。

> **划界注记(2026-10-03 ui-feature-census grill Q1 批复)**:C8(品类选择器
> 接线)与 C9(打开原文)划归 `10-03-feed-ux` 批次随批修(feed-ux 的 G2/C8 项);
> 本批留守 C5/C13。G1/G3 的 store 查询扩展与本批 C1(游标协议)同一协议面,
> 两批设计期合并考虑,协议版本号(entry.py PROTOCOL_VERSION)统一 +1。

## Constraints

- 不动 v1.1.1 已修的数据通路语义(spec python/index.md「桌面发行数据根」节)。
- 每项协议扩展同步 tests/test_desktop_sidecar_protocol.py 与 ui-src vitest。

## Acceptance Criteria

- [ ] C2:杀掉 sidecar 进程后,UI 触发重连/自动 respawn 恢复可用(演示记录)
- [ ] C1:同刻批量 > limit 场景翻页可推进(协议级测试)
- [ ] C3:重启 .app 后仪表盘仍见历史 run(演示记录)
- [ ] C7:共享 client 封装与 _HANDLERS 一一对应,注释如实
- [ ] pytest + vitest 全绿;协议版本变更记录在 CHANGELOG
