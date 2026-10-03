# 桌面前端 UI 规范:token 体系 + 改 UI 必读

> 事实源 = `desktop/ui-src/src/index.css`(token 唯一定义处,10-03-ui-deep-imitation 落地)。
> 动任何 `desktop/ui-src/src/` 下的屏/基件之前先读完本页;token 缺口先改 index.css,不在屏里写死值。

## 改 UI 必读检查单

1. **先查 token 再写值**:颜色/字号/时长/缓动/阴影/紧凑间距一律用 token 工具类;index.css 没有的 → 评估加 token(说明对标依据),而不是在屏内写 arbitrary 值。
2. **单源原则**:源健康度四色(`ok/warning/dead/unknown`)三屏共用(仪表盘/源管理/日志),改色只改 `:root`,不动屏。
3. **布局骨架统一**:七屏 PageHeader 惯例 = `h1 text-base font-semibold` + 内容区 `px-6 pb-6`;新屏照抄,不自创页头。
4. **出处注释**:对标 Linear/Vercel 的模仿点须带 teardown 出处注释(例:`feed-screen.tsx` 三级密度卡注 `teardown-linear-activity #5`;`dashboard-screen.tsx:385` 概览条注 `teardown #2`)。无出处的新样式默认是 AI 默认态,review 会打回。
5. **测试同步**:屏结构变化牵动 `*.test.tsx` 断言,同笔更新;scoped vitest 自跑(`npx vitest run <files>`)、`npx tsc --noEmit -p tsconfig.app.json` 过;vite build 归统一门禁,不在任务内跑。
6. **协议红线**:UI 层不得触发 sidecar 协议变更(见 [sidecar-protocol.md](./sidecar-protocol.md));要新方法 = 停止回 PRD 重新裁定。

## Token 体系(index.css `:root`,经 `@theme inline` 暴露为工具类)

### 色彩:三层暗面 + 极细描边(Linear 分层)

| 层 | token | 值 |
|---|---|---|
| 背景 | `--background` | `#0a0d16` |
| 侧栏 | `--sidebar` | `#080b13` |
| 卡片 | `--card` | `#0e1220` |
| 悬浮 | `--popover` | `#141a2b` |
| 描边 | `--border` / `--input` | `#1d2536` |
| 前景 | `--foreground` 系 | `#e7ebf3` / `--muted-foreground #8b94a7` |

- 主色 = 品牌青 `--primary #22d3ee`(按钮/焦点/激活),**禁紫色主基调**;品牌青→紫渐变 `--brand-from/--brand-to` 仅限 logo 字标/空态光晕。
- 语义:`--destructive #e5484d`;健康度四态 `--ok #3dd68c` / `--warning #f5b544` / `--dead #e5484d` / `--unknown #8b94a7`。
- 色系锚点取自 `desktop/branding/shishi-icon.svg`(深空蓝底+青紫渐变);暗色单主题,token 直接落 `:root`,不做亮色切换。

### 字号阶梯(Linear 密度;覆盖 Tailwind 默认 14/16/18/20)

`2xs 11 / xs 12 / sm 13 / base 14 / lg 16 / xl 18`,行高 16→26 逐档配,`2xs` 默认 medium(微标签/表头惯例)。正文 13-14、辅文 11-12、页头/标题 16-18;展示级大数字(24-48px)不进阶梯,按屏局部 arbitrary。**数字一律 tnum**(body 级 `font-feature-settings:"tnum"` 已全局配)。

### 动效:三级时长 + 双缓动(Linear 克制级,快进快出无弹跳)

| token | 值 | 用途 |
|---|---|---|
| `--duration-fast` | 120ms | hover/按压/离场微反馈 |
| `--duration-base` | 180ms | 浮层入场/展开切换 |
| `--duration-slow` | 240ms | 抽屉/大面板 |
| `--ease-out-expo` | `cubic-bezier(0.16,1,0.3,1)` | 常规过渡默认 |
| `--ease-spring` | `cubic-bezier(0.34,1.56,0.64,1)` | **仅浮层入场点缀**,常规过渡禁用 |

⚠️ 消费语法:`--duration-*` 不是工具类命名空间,写任意值语法 `duration-(--duration-base)`;`ease-*` / `shadow-*` / `p-table` 系是工具类直接用。浮层动画用现成 `animate-fade-in/overlay-in/dialog-in/pop-in(-out)` 系(keyframes 自持,本仓不装 tw-animate-css)。`prefers-reduced-motion` 已全局归零(base 层),新动效无需另配。

### elevation 与密度

- 分层以**细边框为主**;阴影只给浮层:`shadow-popover` / `shadow-drawer`(色带深空蓝底)。重阴影是反模式。
- 紧凑节奏 `--spacing-table 0.25rem` → `p-table / py-table / gap-table`;表格行高 compact 36px。
- 圆角 `--radius 0.5rem` 系;`rounded-full` 仅限状态点/进度条/pill,禁大圆角卡(`rounded-2xl/3xl` 属反 AI 审美违禁)。

### 焦点体系(WCAG 2.4.7)

键盘焦点全局统一 = `:focus-visible { ring-[3px] ring-ring/40 }`(index.css base 层),屏内**不另写** focus 样式、不加 ring-offset(浮层内嵌套焦点例外,局部同色 offset)。鼠标点击不触发(仅键盘/顺序焦点)。

## 反 AI 审美红线(review 硬打回项)

- 渐变白名单仅 2 处:sparkline 线下填充、空态品牌光晕(20% blur);其余禁。
- 阴影白名单:浮层 `shadow-popover/drawer` + 卡片 `shadow-xs`;其余禁。
- 间距全走 scale,禁任意 `mt-[13px]` 类;行内 `style` 仅限动态值(动态色/动态列宽)。
- 禁 stock hero 感、禁紫色主基调、禁装饰性大圆角与多层阴影。

## 可达性约定(终审已按此过检)

- 状态不得仅靠颜色传达:配文字/图形(健康度 = 点 + 文字)。
- 开关用 `button[role=switch]`(自研规范,勿换 checkbox);表格列表头 `aria-sort`;可切换项 `aria-pressed`;折叠组原生 button + `aria-expanded/controls`;hover 浮现的操作簇容器须 `focus-within` 键盘可达。
- 不手加正 `tabIndex` 抢 Tab 序;用原生可聚焦元素(NavLink/button/input)。
- heading 层级不跳级(已知残留:消息屏 h1→h3,登记在案勿扩大)。
- null 数据如实显「—」,不虚构;各屏空/载/错三态齐备(feed 为范式:骨架/空态/错误卡互斥)。

## 已知残留(改 UI 时勿扩大,修缮归口待定)

- `text-[11px]` arbitrary 漂移 30 处(应逐步归 `text-2xs`)。
- 红色系对比:`destructive` 徽章 4.15、设置屏 destructive 按钮 3.41、日志错误行 4.43(均 <4.5 边缘)。
- feed 搜索框 `ring-1` 焦点样式偏离全局 `:focus-visible` 约定;源管理表头排序键盘不可达(排序按钮非原生聚焦)。
