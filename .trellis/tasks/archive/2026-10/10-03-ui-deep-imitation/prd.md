# 前端 UI 深度模仿:模仿总表彻底化

## Goal

按规划 v1.7 的视觉锚点与功能模仿总表,把 `desktop/ui-src` 全部界面从「能用的 shadcn 默认态」推到「对标成品软件的彻底模仿」。主人 2026-10-03 指令:深度将前端界面模仿彻底。配套技能已装 `.agents/skills/` 终态 7 件(前端 6 + tauri 1,台账见其 SOURCES.md)。

## 背景:模仿对象(自规划 v1.7 内嵌,原表在 LOCAL-NOTES.md 索引的权威规划文档)

**视觉锚点(总基调)**:Linear 的暗色质感 + Vercel Dashboard 的数据密度。

| 界面/功能 | 模仿对象 | 抄什么 |
|---|---|---|
| 仪表盘 | Grafana(监控密度)+ Vercel Dashboard(简洁) | 品类状态、源健康度、采集量趋势图 |
| 情报流卡片瀑布 | Linear Activity + Vercel Dashboard | 视觉与信息密度 |
| 源管理表格 | Ant Design Table + TanStack Table(交互抄八爪鱼导出体验) | 排序/筛选/分页/列宽拖拽/导出 |
| 可视化任务配置 | 八爪鱼采集器(商业)+ EasySpider 44.6k★ + Maxun 17.6k★ | 点选建任务体验(YAML 编辑器的远期形态) |
| 采集日志/运行历史 | Crawlab(日志 UI 标杆)+ Kestra run 视图 | 瀑布日志、耗时统计、错误高亮 |
| 任务调度/定时 | changedetection.io 34.7k★ | 检查频率、时区、暂停/恢复、失败重试节奏 |
| 代理池管理 UI | jhao104/proxy_pool 23.7k★ 面板 | 出口列表/测活/成功率统计 |
| 凭证猎手面板 | aipocket(主人已部署) | key/余额/高危列表 UI 与 API 设计 |
| 通知/推送 | Novu 40.1k★ + Apprise | 多通道/模板/静默时段 |

**取材策略(主人 2026-10-03 定案,写死不改)**:按源码可得性分三档——

| 档 | 对象 | 姿势 |
|---|---|---|
| 闭源商业软件 | Linear、Vercel Dashboard、八爪鱼、aipocket(主人已部署) | **拿界面布局 + 功能行为,自己模仿去做**(主人原话)。拿不到源码就先拆解后重实现:每个对象出一份「布局+功能拆解表」(布局结构、信息层级、交互清单、空态/加载态/错误态、微交互),用 shadcn 底座逐项重写 |
| MIT/Apache/BSD 开源 | Ant Design Table、TanStack Table、Novu、proxy_pool、changedetection.io、**Crawlab(BSD-3,2026-10-03 实核)** | 可直接借组件源码,文件头保留上游归属 |
| AGPL/GPL | Grafana、Maxun、**EasySpider(AGPL-3.0,2026-10-03 实核)** | 只看不抄(仓库既有红线,Cherry Studio 判例);看信息密度设计,代码不进仓 |

**执行原则(模仿优先铁律)**:以对标对象的实际布局与功能行为为准,最大程度贴近,不按自家惯例自由发挥;功能决议/依赖红线/入站边界仍守本仓 spec。

**闭源件观察通道**:官方公开演示页/文档截图/发布视频逐帧;主人有账号或已部署的(八爪鱼/aipocket/Linear/Vercel 如有)以实机界面为准,必要时请主人提供参考截图(存 research/)。

**配套技能 → 用途映射(2026-10-03 裁定:前端 6 + Rust 壳 1)**:`frontend-design`(视觉方向定调)/ `frontend-ui-engineering`(生产级质感+WCAG+反 AI 审美)/ `web-design-reviewer`(渲染态视觉审查闭环)/ `vercel-react-best-practices` + `vercel-composition-patterns`(React 19 模式+组件架构)/ `react-vite-best-practices`(Vite 构建)/ `tauri`(Rust 壳层:窗口/事件/updater/capabilities,verify.sh 已实跑过本仓)。UI 验证沿用本仓无头冒烟法,不另装测试技能。

## 现状(2026-10-03)

- 五屏(仪表盘/情报流/源管理/采集日志/设置)+ 第六屏 YAML 配置编辑器均为 shadcn 默认组件直出的「能用」态;暗色有了,但 Linear 级质感(层次/密度/字体排印/微交互)未兑现,各屏与上表对标对象差距未逐项核过。
- 功能缺口在档:ui-feature-census G1-G12(feed-ux 三件套已可 start,覆盖 G1-G5)。本任务管**视觉与交互深度**,与 feed-ux 的**功能缺口**互补不重叠。

## Requirements

- R1 逐屏对标:上表每一屏列出现状 vs 对标对象的具体差距清单(截图并排),逐项修到「放在一起不违和」;**闭源对标对象先出「布局+功能拆解表」再动手**(见取材策略),实现逐条对应拆解条目,不许自由发挥。
- R2 设计系统落地:把视觉锚点(Linear 暗色质感+Vercel 密度)沉淀为 ui-src 的设计 token(色板/间距/字号/层级/动效曲线),写入 shadcn 主题层,全屏统一消费。
- R3 反 AI 审美:按 frontend-ui-engineering 标准过一遍(模板化默认态、通用间距、无个性字体等),产出发现清单并修。
- R4 可访问性:WCAG 对比度/键盘导航/焦点可见(frontend-ui-engineering + web-design-reviewer 双标准)。
- R5 视觉审查闭环:web-design-reviewer 对渲染态(无头冒烟截图)逐屏跑,发现清零或降级留档说明。
- R6 不破坏功能面:sidecar 协议(27 方法,desktop/sidecar-protocol.md)零改动;vitest/tsc/构建门禁全绿。

## Acceptance Criteria(草稿,grill 后定稿)

- [ ] 每屏有「现状 vs 对标」并排截图存 evidence/,差距清单逐项勾销
- [ ] 闭源对标对象的「布局+功能拆解表」存 research/,实现与拆解条目一一对应
- [ ] 设计 token 层落地且全屏消费,无硬编码散色
- [ ] web-design-reviewer 逐屏审查:阻塞级发现清零,其余降级留档
- [ ] WCAG:暗色主题下文本对比度抽检全过
- [ ] 无头 GUI 冒烟(Playwright+bridge.mjs 法)全屏走通;vitest/tsc/build 绿
- [ ] 五屏截图(静默 open -g 法)供主人目验

## Constraints

- **排期硬约束**:大串行工作流 dwfrun-0e1749cb 在途(feed-ux/v112-desktop-batch/image-input/messaging-ui 等 12 任务全在改 `desktop/ui-src`)。本任务必须排在其收尾之后,或经主人明确裁定插队,否则同文件冲突。
- os-etiquette:禁抢前台;UI 验收一律无头冒烟或 open -g 静默截图。
- 本任务为视觉/交互层;G1-G5 功能缺口归 feed-ux,不得在此搭车扩 scope。

## Grill 决议(2026-10-03 主人批「按照你的建议落实到 trellis任务文档」=六问全按推荐,已定稿)

1. **scope = 五屏核心+壳层**(情报流/仪表盘/源管理/日志/设置+侧栏顶栏);远期形态不入本任务。
2. **远期形态(八爪鱼点选式配置/代理池管理面板/凭证猎手面板)= 入 v1.2 backlog 池**(已沉 `10-03-v12-backlog` 第 6 项;凭证面板注意与 G11 凭据导出红线区分:面板只展示管理,不导出)。
3. **token 策略 = 现有 `:root`+`@theme inline` 路线深化**(基线已证方向对),动效 Linear 克制级(120/180/240ms,快进快出无弹跳)。
4. **时序 = feed-ux(功能)先行,本任务(视觉)其后**;实际动工以大工作流 dwfrun-0e1749cb 收尾为门。
5. **字体 = 维持中文系统栈**(PingFang/雅黑已配,零包体;Inter/Geist +300KB 且中文不覆盖,弃)。
6. **仪表盘趋势图 = 自绘 SVG sparkline 零依赖**(~60 行组件多卡复用;不引 recharts,日后 feed-ux 加行情图再议)。

## 文档导航

- `research/baseline.md` — 前端地面真值(七屏/token 现状/组件缺口)
- `design.md` — 技术设计(token 深化 D2/逐屏方向 D4/图表 D5/拆解模板 D6/边界 D7)
- `implement.md` — 三阶段执行计划(Phase0 拆解研究→1 token+基件→2 逐屏→3 收口)+ 保绿与不做清单

## 进度注记(他會话追加,2026-10-03)

- **消息屏列已完成**(不必再排):`10-03-messaging-hermes-look` 已按「以上游为准最大程度贴近」交付(be044a2,review)——平台头像(AvatarChip 画法近逐字对应,Telegram 路径数据与 simple-icons 630B 逐字节相同)、左网格右详情面板(照上游 MasterDetail)、三态色彩走 CSS 变量、底部状态条;质检对照上游源码逐处核过系模仿非自由发挥。集成时直接复用其 platform-icons.tsx 与详情面板范式。
- **Phase 0 拆解研究已完成(2026-10-03)**:research/ 五件——三份闭源拆解表(34 条可抄,逐条映射到本仓组件/token)+ licenses.md(Crawlab=BSD-3 可直借;EasySpider=AGPL 只看不抄)。动工门只剩大工作流收尾。

> **2026-10-04 归档会话注记**:AC 框为交付会话遗留未逐勾,不作为未完成证据;交付与验收以既录证据为准——evidence/ 逐屏截图+research/ 拆解表与许可证档齐(33adb56/26d16b2/415ec9b);执行工作流 dwfrun-b35a7fa9 completed。装机/真机类冒烟项统一移交 `10-04-wrapup-checklist` 装机验收节。
