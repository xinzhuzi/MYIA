# v1.2 待办池:Windows 构建 + crawl4ai L3 实装 + proxy_pool 对接

## Goal

grill 决议 2026-10-03 Q1:发布工程(v1.1.1)先行,以下三项排 v1.2。本档是
**待办池**(非执行档):收边界、现状与验收指针;开工时逐项拆正式任务
(复杂项按规矩补 design/implement)。

## Requirements(三项边界)

1. **Windows 产物化**
   - 现状:`desktop-release.yml` 已有 windows-latest job(msi,**构建级验证、
     允许失败**);PyInstaller 不可交叉编译 → CI native matrix 是唯一路。
   - v1.2 目标:msi/exe 产物可用(真机安装冒烟,对齐 v1.1.1 的 macOS 验收
     口径:cwd 无关路径解析 + 首跑种子 + 五屏数据/空态)。
   - 开放:主人有无 Windows 真机/VM 做冒烟(拆任务时问)。
2. **crawl4ai L3 实装**
   - 现状:`src/myia/engines/crawl4ai.py` 为薄层(~10KB);v0.2 任务
     (10-01-v02-engine-crawl4ai)按其范围已毕,L3 降级档实质缺位。
   - v1.2 目标:真引擎进降级链(L2 失败→L3 接管),extras 可选依赖
     `myia[crawl4ai]`,测试走录制回放(CI 零外网,Python spec)。
3. **proxy_pool 对接**
   - 现状:fetch_base 有 143 处 proxy 引用,transport 单上游(v0.2 已毕,
     10-01-v02-proxy-transport);池化(轮换/健康检查/住宅 IP)未写。
   - v1.2 目标:池化抽象 + 至少一家服务商实装;**服务商与预算是主人决策**,
     拆任务时先问。
4. **B2/B3/B4 桌面补实现**(grill Round 3 Q6:v1.1.1 先改宣称,补实现排 v1.2)
   - 桌面反馈入口(feed 卡片反馈按钮,对应 CLI feedback mark/list/stats)
   - settings 屏反馈开关分区(routes 骨架曾列)
   - dashboard 采集量趋势(v1.1 PRD 承诺项)
   - 与第 2 项 C 组批次(10-03-v112-desktop-batch)的取舍:实现顺序上
     C2/C1/C7 优先于本项。
   - **回标(2026-10-03 路由落档):本项整体移交 `10-03-v112-desktop-parity`
     (v1.1.2 桌面对齐批次)吸收,提前于 v1.2 开工——上文「排 v1.2」已被覆盖;
     本池剩余三项(Windows/crawl4ai/proxy_pool)与第 5 项 UI 池不变。**
5. **UI 普查 P2/P3 项入池**(2026-10-03 ui-feature-census grill Q2 批复「全部入池不加码」;
   证据与业界参照见 `.trellis/tasks/10-03-ui-feature-census/prd.md` G 矩阵)
   - G5 主体:告警规则(Inoreader Rules 式条件→动作,涉 sidecar 协议扩展;
     G5 前半「推送测试按钮」已随 `10-03-feed-ux` 批次先行)
   - G6:采集量/成功率趋势折线(与 B4 采集量趋势同属一块,拆任务时合并考虑)
   - G7:run 重跑/按品类状态过滤/日志内搜索(重跑依赖 feed-ux G4 的手动触发通道)
   - G8:情报流卡片「AI 摘要」按钮(enrich 管线现成);情绪标注 v2 再议
   - G9:快捷键与批量操作(全部标已读等)
   - G10:代理池连通性测试按钮(doctor --config 探测已有,差 UI)
   - G12:条目卡「就地沉淀为关键词」入口(OpenCTI 快捷订阅铃铛式,衔接 yaml-editor)
   - G11 不入池:并入 F 类刻意不做(凭据导出,security-baseline 红线)

## 主人侧前置(2026-10-03 grill Q4 已答)

- **Windows 真机/VM:有**——v1.2 Windows 拆任务按完整安装冒烟口径(对齐 macOS 的
  cwd 无关路径解析 + 首跑种子 + 五屏数据/空态),不做降级。
- **proxy 服务商与预算:有**——拆 proxy_pool 任务时主人提供具体服务商,先做池化
  抽象 + 该服务商实装。

## Acceptance Criteria(池档口径)

- [ ] v1.2 规划时三项各自拆成正式任务并引用本档
- [ ] 本档不挂 in_progress(它是池,不是工)
