# 技术设计:UI 深度模仿(token 深化 + 逐屏对标)

> 依据:prd.md 取材策略(闭源=布局+功能拆解表前置)+ Grill 决议(2026-10-03 主人批六问全按推荐)/ research/baseline.md(地面真值)。原决策点 1/2/3 已全部定案,见文内「已定案」标注。

## D1 总路线:在现有 token 上深化,不另起炉灶

index.css 的 `:root` + `@theme inline` 路径已对(三级面/语义四态/品牌渐变/中文字体栈都在),问题是**贯彻度与深度**,不是方向。因此:

1. **Phase 1 只动 index.css + components/ui/**,不碰任何屏——token 与基件先立住,全屏自动受益
2. **Phase 2 逐屏消费新 token**,每屏一个闭环(现状截图→改→截图→reviewer)

## D2 token 深化清单(index.css 追加,全部经 @theme inline 暴露)

| 类别 | 追加内容 | 对标依据 |
|---|---|---|
| 字号阶梯 | `--text-2xs 11px / --text-xs 12px / --text-sm 13px / --text-base 14px / --text-lg 16px / --text-xl 18px`,配行高与字重变体 | Linear UI 密度(正文 13-14px、辅文 11-12px);Vercel Dashboard 数据密度同档 |
| 动效 | `--duration-fast 120ms / --duration-base 180ms / --duration-slow 240ms`;`--ease-out-expo`、`--ease-spring`;全过渡统一走变量 | Linear 克制级(快进快出,无弹跳) |
| elevation | `--shadow-popover`(一层轻阴影)、`--shadow-drawer`;层级规则:面=细边框分层为主,浮层才用阴影 | Linear 以 border 分层,重阴影是反模式 |
| 密度梯度 | `--spacing-table`(表格/日志紧凑节奏)、默认节奏不变;表格行高 36px(compact) | Vercel Dashboard 表格 / Grafana 密度 |
| 焦点体系 | `:focus-visible` 统一 ring + ring-offset 规范;Tab 顺序与键盘导航纳入每屏验收 | WCAG 2.4.7 + Linear 键盘优先 |

## D3 组件基件补齐(shadcn 源码复制进项目,按屏需要)

table / dialog / dropdown-menu / tabs / tooltip / input / switch / scroll-area。源管理表格从手搓迁到 ui/table(TanStack 引擎不变,渲染层换);dialog 迁移 yaml-editor 已有的自研 dialog 归并。**不装全家桶**,用到才补。

## D4 逐屏设计方向

| 屏 | 对标 | 拆解表重点(闭源件先做,存 research/) | 具体改造点 |
|---|---|---|---|
| 情报流 | Linear Activity + Vercel Dashboard | Linear Activity:卡片信息层级(标题/元信息/正文摘录三级)、时间分组、hover 行内操作、未读强调、键盘 j/k 导航(可选) | 卡片密度重排(13px 正文/11px 元信息)、品类色条、分组时间轴、hover 浮现操作、空/载/错三态按 frontend-ui-engineering |
| 仪表盘 | Vercel Dashboard(简洁)+ Grafana(密度) | Vercel Dashboard:卡片网格节奏(12 栅格/等高卡)、顶部概览条、每卡右上角时间范围选择器 | 概览条(今日采集/活跃源/推送成功数)、源健康度卡片网格、采集量趋势(自绘 SVG sparkline,决议⑥) |
| 源管理 | Ant Design Table 交互 + 八爪鱼导出体验 | Ant Table:列宽拖拽手感、筛选下拉、批量选择条、空态插画位 | 迁 ui/table、列宽拖拽、健康徽章统一语义四态、启停开关动效、行密度 compact |
| 采集日志 | Crawlab(日志 UI)+ Kestra run 视图 | Crawlab:运行瀑布(每 run 一段)、耗时统计条、错误高亮;Kestra:步骤折叠 | run 分组折叠、每 run 耗时/条数统计行、错误行高亮(dead 色)、等宽字体日志体、自动滚底 |
| 设置 | Linear Settings(闭源,布局功能拆解) | Linear Settings:左侧分区导航+右侧表单卡片、每区独立保存条、危险区隔离 | 分区导航(通用/视觉/推送/更新/高级)、表单卡片化、保存态反馈;vision/updater 已有件融入 |
| 壳层(侧栏/顶栏) | Linear 侧栏 | Linear:分组导航、激活态左侧竖条、图标+文字、底部账户区;快捷键提示(kbd) | 侧栏分组+激活竖条+底部状态(sidecar 状态已有 hook)、顶栏面包屑+全局命令位(留白) |
| YAML 编辑 / 消息 | —(已有范式) | — | 只做 token 贯彻微调,不动结构;消息屏复用 hermes-look 件 |

## D5 图表 **已定案(决议⑥):自绘 SVG sparkline**

- 仪表盘趋势用自绘 SVG sparkline——零依赖、包体零增、Linear/Vercel 的迷你趋势本就是简单折线+渐变填充,~60 行组件可复用到多卡
- 不引 recharts(+100KB 级,当前仅仪表盘一处用图,不划算)
- 若日后 feed-ux 加行情图再议引库

## D6 拆解表模板(research/ 产物格式,闭源件每对象一份)

```
# 拆解:<对象名>(<观察渠道+日期>)
## 布局结构(栅格/分区/尺寸感)
## 信息层级(每区域几级、字号体感、强调手法)
## 交互清单(逐交互:触发→反馈→终态)
## 三态(空/加载/错误 各什么样)
## 微交互与动效(时长/缓动感)
## 可抄清单(逐条,映射到本仓组件/token)
```

观察渠道优先级:主人实机截图 > 官方文档截图/演示页 > 发布视频逐帧。Linear/Vercel 若主人有账号可提供参考截图(存本目录,勿外发)。

## D7 边界与风险

- 协议零改动:全任务不碰 desktop/sidecar-protocol.md 的 27 方法面;若发现 UI 必须的新方法=停止并回 PRD 重新裁定(那是功能,归 feed-ux/v112)
- 并行在途:动工前核 ListWorkflowRuns 确认 dwfrun-0e1749cb 及 ui 相关工作流终态;开工后每屏提交前 `git -C <repo>` 核对无他会同文件在途
- golden/vitest 兼容:屏内 DOM 结构变化会牵动既有 vitest 断言(七屏都有 *.test.tsx),每屏改造含测试同步更新;不碰 Python 侧与 golden 基件
- 字体 **已定案(决议⑤):维持系统栈**(中文界面 PingFang/雅黑已配,零包体;Inter/Geist 变量字体 +300KB 且中文不覆盖,弃)
- scope **已定案(决议①③):五屏核心+壳层全做,远期形态(八爪鱼点选式配置/代理池面板/凭证面板)不入场,已沉 v12-backlog 第 6 项**——核心屏密度质感是主人「模仿彻底」的直接痛点,远期形态是功能面大件
