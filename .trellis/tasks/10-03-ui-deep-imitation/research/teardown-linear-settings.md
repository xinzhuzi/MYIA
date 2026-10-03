# 拆解:Linear Settings(设置屏对标)

> 观察渠道:linear.app/docs(settings/notifications/search 章节)+ changelog + 官方博文(2026-10-03 三路网络研究);未实证处明标。

## 布局结构

- Settings 为全屏二级界面:**左侧分区导航**(四组:Account / Features / Administration(仅管理员)/ Your teams)+ 右侧内容区**每子区一屏**(URL 二级路径 `settings/<group>/<page>`)。
- 团队设置=「单一摘要视图+点击进入完整配置子页」双层模式(changelog 确认);成员/团队列表=**可按任意列筛选排序的数据表格**。
- 设置搜索入口未实证(cmd+K 只索引 issue/project/user/team/label/favorite/document 七类)。
- 来源:changelog(2024-12-18 personalized-sidebar)、docs/notifications、[settings-are-not-a-design-failure](https://linear.app/blog/settings-are-not-a-design-failure)

## 信息层级

- **分区→子页→卡片/表格** 三级。
- 通知页按渠道分组(Desktop/Mobile/Email/Slack),每渠道**绿点=启用/灰点=禁用**状态指示,先选渠道再逐项开关;类型按类别嵌套(类内项目不可单独拆选);Email 内有 Notification format 下拉(digest/immediate)。
- 行结构(label 左/控件右/说明文案)无逐像素实证。

## 交互清单

- **开关类设置即时生效、无保存按钮**(docs 明示)——本仓设置屏现多为显式保存,采纳此模式需按区渐进
- 危险操作:删除工作区在 Settings→Workspace(仅 admin);成员停用走「选中→Activate/Confirm」二次确认;输入名称式确认未实证
- 开关反馈:切换即生效+渠道状态点同步变化
- 未保存离开提示未实证

## 三态 / 微交互

加载/保存中/保存失败样式均未实证;微交互仅设计原则实证:标签/图标/按钮严格垂直水平对齐、LCH 三变量主题、Inter/Inter Display。→ 三态按本仓 frontend-ui-engineering 标准做。

## 可抄清单(→本仓 React+shadcn 映射)

1. 左列=分区导航(分组:通用/视觉/推送/更新/高级),当前项高亮左竖条,路由 `settings/:section`
2. 右列=每子区一屏:区标题+描述,下堆叠多个 Card,每 Card 一个设置主题
3. 概览页=「设置 homepage」:每区一行摘要(模板数/凭据数/推送通道数)+点击进入
4. 通知/推送区=渠道 Tab(带彩色状态点)+类别分组开关组(Tabs+Switch 行,类内联动)
5. 开关行=自动保存无按钮;下拉/输入类配右下角 sticky 保存条(Linear 未实证,合理补充,标注自创)
6. 危险操作(如清数据根)单独 Destructive Card 置于区页底部+AlertDialog 二次确认
7. 表格页(凭据/通道)复用源管理 DataTable 范式:任意列筛选排序+行尾操作菜单
8. 设置页文案=onboarding 语气+tooltip 辅助
9. 对齐纪律:label/icon/button 三者 baseline 严格对齐(Linear 实证原则)
