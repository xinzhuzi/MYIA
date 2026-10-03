# 拆解:Vercel Dashboard(仪表盘对标)

> 观察渠道:vercel.com 官方 docs/changelog/blog + Geist 设计系统文档 + 官方截图视觉分析(2026-10-03 三路网络研究)。Geist 是 Vercel 官方公开设计系统,以下数字多有实证。

## 布局结构

- **项目列表页**:无全局统计条(2021 重设计刻意移除 header/activity stream);工具行=搜索框+右上「Add New…」主按钮;卡片网格响应式 2-3 列、等宽等高。
- **项目概览页**:顶部并排两卡——Production(主)+ Preview(次),各含截图/状态/commit/作者/时间;其下 Deployments/Analytics/Logs 等导航;内容窄列非整宽。
- 来源:[a-new-dashboard-overview](https://vercel.com/changelog/a-new-dashboard-overview-is-now-available)、[docs/projects](https://vercel.com/docs/projects)、[geist/grid](https://vercel.com/geist/grid)

## 信息层级(实证较多)

- 卡内 **4 级**:状态点+词 → 项目名(粗)→ commit/域名(次)→ 相对时间(muted 右侧)。
- 状态=**纯色圆点(8-10px)+文字,非 Badge**:Ready 薄荷绿 #50e3c2 系/Error 红/Building 琥珀/Queued 灰(截图实证)→ 本仓语义四态 ok/warning/dead/unknown 直接映射。
- Geist 字体阶梯:Label 14 Strong=最常用标题;数字 13px+tabular-nums;mono 仅 12/13/14;12px 用于密集次级。来源:[geist/typography](https://vercel.com/geist/typography)

## 交互清单

- 整卡点击进项目;hover 浮出「Visit/直达 Production」
- Deployments 过滤四下拉:Branch/日期/Environment/Status;**多选 trigger 渲染色点簇+「4/5」计数**(截图实证)
- 图表页:日期+时间范围选择器、拖选 zoom、表格按错误率/时长排序
- Redeploy 走确认弹窗

## 三态

- 空态:Add New… 引导入口(实证;首屏文案未实证)
- 加载:官方未实证;第三方复刻均用同构 skeleton 卡(照抄结构:灰块截图区+两行短条)
- 错误:红点+Error 行/卡,详情带 build logs;浏览器 tab 图标随状态变色

## 微交互与动效

hover 卡亮起可点(实证);边框发光/状态点呼吸/数字滚动未实证(building 态 pulse 按惯例可做)。

## 密度(Geist tokens 实证,[designmd 汇总](https://designmd.fun/vercel/design-md))

- **4px 基数**(4/8/12/16/24/32);卡内边距 16-32px;**in-app 圆角 6px**
- 阴影=多层叠加+**1px inset hairline 代替 border**
- 列表行 1px hairline 分隔、行高 40-48px(舒适档,与本仓 compact 表格 36px 区分场景)
- 大数字:展示级 24-48px/600 字重/负字距/tnum
- 暗色三级面:页 gray-1000 / 卡 gray-100 / 边 gray-600;hover gray-200([geist/colors](https://vercel.com/geist/colors) 语义步阶)

## 可抄清单(→本仓 React+shadcn 映射)

1. 仪表盘顶栏=搜索位+主操作按钮,**不放统计条**(统计下沉为概览卡)
2. 概览条=一行四格:小标签(大写+弱色)+大数字(tnum)——今日采集/活跃源/推送成功/告警
3. StatusDot 组件=8px 圆点+13px 标签,四色映射 ok/building→warning/dead/unknown,本仓语义四态现成
4. 源健康卡=Card:名称(14 medium)+muted 次行+相对时间;网格 gap-6(24px)
5. 多选过滤 trigger=点簇+「n/m」徽章(源管理筛选可用)
6. 趋势卡=Select 时间范围+自绘 sparkline(决议⑥),building 态可 pulse
7. 空态=居中标题+描述+主按钮+次链接
8. skeleton=同构卡:灰块区+两行短条
9. 4px 基数、卡 p-4~p-6、列表 py-3+divide-y——收进 design D2 密度 token
10. 圆角 6px(Vercel)vs 本仓 --radius 0.5rem(8px):Linear 档更大,维持 8px 不追 Vercel(视觉锚点以 Linear 为主)
11. 数字一律 tnum(已配);时间/ID/commit-sha 用 mono 13px
