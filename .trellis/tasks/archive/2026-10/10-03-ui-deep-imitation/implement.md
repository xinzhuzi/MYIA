# 执行计划

> 前置硬条件(prd.md Constraints):大串行工作流 dwfrun-0e1749cb 及在途 ui 工作流终态确认后才可 start;并行会话在场禁 task.py start(状态直改另议)。

## Phase 0 拆解研究(✅ 2026-10-03 完成,只读未碰代码)

1. [x] 闭源对象拆解表 ×3 已存 research/:`teardown-linear-activity.md`(情报流+侧栏,14 条可抄)、`teardown-vercel-dashboard.md`(仪表盘,11 条可抄,Geist tokens 实证:4px 基数/圆角 6px/hairline/三级暗面/tnum)、`teardown-linear-settings.md`(设置,9 条可抄,开关即时生效模式)。观察渠道=官方 docs/changelog/blog+Geist 系统;「未实证」处明标,实现时以主人实机截图校准
2. [x] 许可证核实:`research/licenses.md`——**Crawlab=BSD-3 可直借**;**EasySpider=AGPL 只看不抄**(与 Grafana/Maxun 同档)
3. [x] 产出核对:每份拆解表「可抄清单」已逐条映射到本仓组件/token(shadcn 件名/语义四态/动效 token),悬空条目零

## Phase 1 token 深化 + 基件补齐(不动屏)

改动面:index.css + components/ui/ 新增件。

- [ ] D2 清单落地:字号阶梯/动效/elevation/密度/焦点体系入 `:root`+`@theme inline`
- [ ] D3 基件补齐:table/dialog/dropdown-menu/tabs/tooltip/input/switch/scroll-area(shadcn 源码复制)
- [ ] 全屏视觉回归:无头冒烟(Playwright+bridge.mjs)逐屏截图 vs 基线,确认无破坏性漂移(此阶段屏只受 token 微影响)
- [ ] 门禁:vitest 全绿 / tsc 零错 / vite build 绿
- [ ] 提交 1:token+基件(一笔)

## Phase 2 逐屏改造(每屏=一个闭环,序按价值)

序:情报流 → 仪表盘 → 壳层(侧栏/顶栏) → 源管理 → 采集日志 → 设置 → (YAML/消息只做 token 微调随末批)。

每屏循环:
- [ ] 现状无头截图存 evidence/before-<screen>.png
- [ ] 按 design D4 该屏改造点实现(消费新 token;测试同步更新)
- [ ] after 截图 evidence/after-<screen>.png;before/after 并排图
- [ ] web-design-reviewer 对 after 审查:阻塞级发现当场修,降级项记 evidence/review-<screen>.md
- [ ] 门禁:vitest scoped + tsc;绿则单屏一笔提交;不绿修复≤2 轮,仍不绿 git stash 保现场退回

## Phase 3 收口审查 + spec 沉淀

- [ ] web-design-reviewer 全屏终审:阻塞级清零
- [ ] WCAG 抽检:暗色文本对比度(正文/辅文/muted 三档)、键盘导航全屏走查
- [ ] 反 AI 审美终检(frontend-ui-engineering 清单)
- [ ] 五重门禁:pytest 全量(确认未伤 Python 侧)/ vitest / tsc / vite build / tauri build
- [ ] 装机五屏截图(open -g 静默+screencapture -l 定窗)供主人目验
- [ ] spec 沉淀:token 体系与「改 UI 必读」写入 `.trellis/spec/desktop/`(新增 frontend-ui.md,挂 index)
- [ ] 证据齐:evidence/ 含 before/after 并排 ×屏数、review 记录、WCAG 结果、门禁输出

## 保绿与回滚

- 每屏独立提交,任一屏受阻 stash 保现场,main 永绿(沿 dwfrun-0e1749cb 同款纪律)
- 提交前核对无他会同文件在途改动(hunk 级甄别,共享 index 场景走临时 index + CAS,见记忆「共享 index 提交手法」)

## 不做清单(防 scope 漂移;决议①②已定案)

G1-G5 功能件(搜索框/详情原文/导出/排程管理/测试按钮)、协议新方法、Python 侧、远期形态(点选式配置/代理池/凭证面板——已沉 `10-03-v12-backlog` 第 6 项)、消息屏重做。
