# 拆解:Linear Activity/Inbox + 侧栏(情报流与壳层对标)

> 观察渠道:linear.app 官方 docs/changelog/blog + UI 重设计博文(2026-10-03 三路网络研究);**「未实证」=官方无数字/截图,实现时以主人实机截图校准,不许脑补数值**。

## 布局结构

- **Inbox/Activity 视图**:顶部两 Tab「Priority / Other」(2026-09 新增,算法挑高信号);列表单列;可配「preferred grouping」;2026 重设计「按通知类型重构、强调协作者头像」。列表宽度 px 未实证。
- **侧栏=「倒 L」结构**(侧栏+Tab 控制主视图):工作区区(Inbox、My Issues)→ Favorites(置顶视图)→ 各 Team(其下 Issues/Cycles/Projects/Roadmap)→ 底部用户头像/设置。图标+文字混排,重设计专门抠「label/icon/button 对齐」。
- 来源:docs/inbox、docs/notifications、docs/triage、changelog(2026-09-03 Priority Inbox)、[how-we-redesigned-the-linear-ui](https://linear.app/blog/how-we-redesigned-the-linear-ui)

## 信息层级

- 一条通知约 **3 级**:操作者头像+动作摘要(主)、条目标题(主、粗体)、元信息(时间/团队,弱色小一号)。
- 字体:正文 Inter、标题 Inter Display;**色板仅 3 变量**(base/accent/contrast)LCH 派生整套主题(重设计博文实证)——与本仓 token 思路同构。
- 具体字号/色值未实证。

## 交互清单(逐条)

| 交互 | 触发→终态 |
|---|---|
| hover/右键 | 右键出 ContextMenu(删除/Snooze/改属性) |
| 点击条目 | 进 Inbox 专属视图,操作后回列表 |
| 未读切换 | `U` 单条;`Option+U` 全部已读;「Show unread first」选项 |
| 删除 | `Backspace` 单条/`Shift+Backspace` 全部已读;**无归档**,>2000 条自动清 |
| Snooze | `H`,可输自然语言日期,到期重现 |
| 过滤 | `Cmd/Ctrl+F` 内联搜索(标题/类型/assignee/团队/项目/优先级),`Esc` 清空 |
| 键盘导航 | `J/K`/`↑↓` 移动,`G I` 跳 Inbox,`?` 快捷键帮助 |
| 侧栏 | `[` 折叠、拖边缘调宽 |

## 三态

未实证(官方无空/载/错态展示)。仅知文案风格极简。→ 实现按本仓 frontend-ui-engineering 标准做,勿模仿脑补。

## 微交互与动效

官方无动效规格;其设计工程师 Emil Kowalski 公开惯例(二手):**按压 ~150ms linear、松开/入场 ~200ms ease-out,只动 transform/opacity,禁 teleporting state**(内容瞬变必须有过渡)。→ 与 design D2 动效 token(120/180/240)吻合。

## 密度

官方自述目标「提高导航 hierarchy 与 density、减少视觉噪音」;行高/间距数字未实证。分隔以间距+分组标题为主、弱边框为辅(截图观感)。

## 可抄清单(→本仓 React+shadcn 映射)

1. 侧栏分组:顶部 Inbox 式主入口 → 中部分组 → 底部状态区;`[` 折叠;激活项=accent 前景+`--accent` 行背景(不加粗)
2. 分组可折叠,ChevronDown 随折叠旋转 ~150ms
3. 列表头部:左 Tabs(如「重要/全部」)+ 右 Display options DropdownMenu
4. 情报卡行=头像/品类标+摘要文本+粗体标题+右对齐灰色相对时间
5. hover:行背景 `--accent/50`,操作按钮浮现
6. 未读:左侧 2px accent 竖条;`U` 快捷键
7. 右键 ContextMenu(复制链接/标记已读/沉淀关键词——映射 G12 位置)
8. `Cmd+F` 内联过滤,Esc 清空
9. 全键盘 J/K 导航+focus 环
10. 删除无确认,条目 200ms ease-out 高度折叠离场
11. 数字 tabular-nums(本仓 tnum 已配);元信息 muted 小一号
12. 主题 3 变量派生法(本仓 :root 已同构,坚持)
13. Tab/内容切换 ~200ms ease-out 仅 opacity/transform
14. 图标 16px 与文字 baseline 严格对齐
