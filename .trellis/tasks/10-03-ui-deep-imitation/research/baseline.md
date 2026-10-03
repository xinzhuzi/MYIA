# 前端地面真值基线(2026-10-03 实读 desktop/ui-src)

> 本文件是任务开工前的现状盘点,全部来自源码实读(file:line 可核)。改造后核对「差异是否都属任务范围」以此为准。

## 七屏清单(routes/ → screens/)

| 屏 | 路由壳 | 屏实现 | 行数 | 现状 |
|---|---|---|---|---|
| 仪表盘 | routes/dashboard.tsx (85) | screens/dashboard/ | 272 | 品类状态/源健康度为主,无趋势图(无 chart 依赖) |
| 情报流 | routes/feed.tsx (51) | screens/feed/ | 433 | 最大一屏;卡片列表,Linear Activity 级密度未兑现 |
| 源管理 | routes/sources.tsx (44) | screens/sources/ | 304+ | 表格(TanStack 已装)+健康徽章+启停开关+编辑入口 |
| 采集日志 | routes/logs.tsx (50) | screens/logs/ | 282 | 运行历史列表,Crawlab 级瀑布日志/耗时统计未做 |
| 设置 | routes/settings.tsx (70) | screens/settings/ | 多件 | doctor/updater-card/vision-form 等分区组件已拆 |
| YAML 编辑 | —(sources 联动 dialog) | screens/yaml-editor/ | 多件 | CodeMirror one-dark;完整编辑闭环已落地 |
| 消息 | —(messaging,他会话交付) | screens/messaging/ | 多件 | **已按 Hermes 重皮(10-03-messaging-hermes-look,be044a2)**:platform-icons.tsx/详情面板/三态 CSS 变量,本任务只做融入性微调,不重做 |

壳层:components/layout/(app-layout 22 / sidebar 44 / top-bar 101 / page-header 20 行)——侧栏与顶栏很薄,Linear 级导航质感(分组/快捷键提示/折叠)未做。

## 设计 token 现状(index.css,~120 行)

**已有**(质量不低,锚点正确):
- 三级面分层 `--background #0a0d16 < --card #0e1220 < --popover #141a2b` + `--sidebar`,细描边 `--border #1d2536`
- 语义四态 `--ok/--warning/--dead/--unknown`(仪表盘/源管理/日志共用)+ `--destructive`
- 品牌青 `--primary #22d3ee` + `--brand-from/to` 青→紫渐变;`--ring` 焦点
- Tailwind 4 `@theme inline` 全量映射(色/radius sm-md-lg-xl/`--font-sans` 中文系统栈/`--font-mono`)
- Linear 质感细节:thin 滚动条、`font-feature-settings: "tnum"`(表格数字对齐)、`::selection` 品牌色

**缺口**(对标 Linear/Vercel 未兑现的部分):
- 字号阶梯未定义:屏内直接用 Tailwind 默认 `text-sm/base`,没有 Linear 的 11/12/13/14/16 紧凑密度体系
- 动效零 token:无 duration/easing 变量,过渡各写各的
- elevation 无体系:无阴影分层变量(Linear 以细边框+轻阴影分层,popover 需一层)
- 密度梯度无:同一 spacing 节奏通吃,无 compact(表格/日志)与 comfortable(卡片)之分
- 焦点/键盘态不成体系:focus-visible ring 只有一处全局 outline-ring/50

## 组件基件(components/ui/ 仅 6 件)

badge / button / card / select / separator / skeleton。**缺**:table(源管理表格手搓在 sources-table.tsx)、dialog、dropdown-menu、tabs、tooltip、input、switch、scroll-area。shadcn 惯例=按需复制源码进项目,补齐成本低(这正是选它的理由)。

## 依赖关键项(package.json)

- 已装:`@tanstack/react-table 8.21`(表格引擎在)、Radix 三件(select/separator/slot)、lucide、CodeMirror 系、react-router 7、Tauri 插件四件
- 未装:任何 chart 库(规划提过 Recharts/Tremor,未落);**design 决策点:趋势图用自绘 SVG sparkline 还是引库**

## 与相邻任务的边界

- 功能缺口 G1-G12 归 feed-ux(G1-G5)与 v112-batch(C5/C13);本任务只做视觉/交互深度,**不做**:搜索框(G1)、导出(G3)、排程管理(G4)、测试按钮(G5)——但它们的容器布局密度按对标改造时一并给位置留白
- 消息屏已由 hermes-look 交付,复用其 platform-icons.tsx 与「左网格右详情」范式
