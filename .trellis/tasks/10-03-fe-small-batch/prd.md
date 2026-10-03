# 前端功能小批:G7/G8/G9/G12 解锁件+P1/P2 视觉残留

## Goal

v12-backlog 池中**已解锁的前端功能小件**一次做掉,并清掉 ui-deep-imitation 终审的参数级残留。主人 2026-10-03 批「按照你的建议做完」。

## 功能件(来自 ui-feature-census G 矩阵,v12-backlog 第 5 项)

| 项 | 内容 | 依赖现状 |
|---|---|---|
| G7 | 采集日志:run 重跑 / 按品类·状态过滤 / 日内搜索 | 重跑骑 feed-ux G4 已落的手动触发通道;过滤与搜索纯前端 |
| G8 | 情报流卡「AI 摘要」按钮 | enrich 管线现成;若 sidecar 无对应方法则新增(见协议规则) |
| G9 | 情报流批量操作(全部标已读等) | 单条 U 键已落;本项补批量 |
| G12 | 条目卡「就地沉淀为关键词」入口 | 衔接 yaml-editor;可复用其写通道或新增方法(见协议规则) |

## 视觉残留(ui-deep-imitation 终审 leftovers,勿扩大)

- **P1**:源管理表头排序键盘不可达(排序按钮非原生聚焦)+ destructive 徽章对比 4.15(<4.5)
- **P2×5**:①侧栏两处小字对比边缘;②日志错误行对比 4.43;③设置 destructive 按钮 3.41;④跨屏 text-[11px] 漂移 30 处→归 text-2xs token;⑤feed 搜索框 ring-1 焦点样式偏离全局 :focus-visible 约定
- P3(消息标题跳级)终审已判「登记勿扩大」,**不入本批**

## 协议规则

新增 sidecar 方法:PROTOCOL_VERSION +1 + CHANGELOG + desktop/sidecar-protocol.md spec 同步 + 协议测试(test_desktop_sidecar_protocol.py)四件套,缺一不算完成。

## Acceptance Criteria

- [ ] G7 三件可用:重跑真触发一次手动通道、过滤生效、日志搜索命中高亮
- [ ] G8 按钮:点击出摘要(enrich 真跑或 graceful 明示无配置),含 loading/错误态
- [ ] G9 全部标已读一键生效,UI 计数同步
- [ ] G12 从卡片沉淀关键词进 YAML,跨文件 id 查重不破(yaml-editor 既有语义)
- [ ] P1+5P2 全闭:WCAG 对比度复算全 ≥4.5,排序按钮键盘可达(tab focus+Enter 排序)
- [ ] text-[11px] 全仓 ui-src 残留清零(归 text-2xs)
- [ ] 新增方法四件套齐;vitest 全绿+tsc 零错+vite build 绿
- [ ] 触屏 before/after 截图存 evidence/

## Constraints

- 逐文件归属判定(并行流在场):白名单文件动手前逐一核净,脏则等/跳过如实报
- 告警规则(G5 主体)、G11、远期形态三项**不在本批**
- 不 push;任务转 review 不 finish;装机冒烟留主人
