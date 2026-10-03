# PRD:桌面 UI 功能对标普查(业界情报/爬虫软件)

## 需求与方法

主人 2026-10-03 指示:调研各大情报收集/爬虫软件,找出 MYIA 桌面端 UI 缺什么功能,落档待拍板。**仅普查不动代码。**

- 三路并行网调(原始报告全文在 `research/`,均带官方文档 URL):OSINT/情报收集(SpiderFoot/Maltego/OpenCTI/MISP/Hunchly)、爬虫平台(Octoparse/ParseHub/Apify/Zyte/Browse AI/changedetection.io)、订阅监听(Feedly/Inoreader/Distill/Visualping/Brand24/Google Alerts)。
- MYIA 侧基线 = 五屏 + 顶栏源码实读(2026-10-03,commit d84ec05 时点),非凭印象。

## MYIA UI 现状基线(源码实读)

- **仪表盘**:品类状态卡(只读文字)、源健康度四态分布、近期 run 成功率;仅「刷新」一个动作,无下钻、无单品类触发。
- **情报流**:未读/星标/稍后读/全部四页签(本地 localStorage 态)、卡片(标题/来源/品类/标签/精评分/2 行摘要)、游标分页;点击条目=仅标已读,无详情、无打开原文。
- **源管理**:TanStack 表(排序/筛选/分页)+ 健康徽标 + 启停开关(YAML 写回 + doctor 往返复核)。
- **采集日志**:run 列表 + 选中 run 流式终端(环形 4000 行、错误行高亮);无过滤、无日志内搜索、无重跑。
- **设置**:LLM/代理池/推送凭据表单(只入钥匙链)+ doctor 回显;无推送测试。
- **顶栏**:品类全局过滤 Select 为死骨架(defaultValue="all" 未接数据);sidecar 状态点。
- **规划中(已另立档,非本普查缺口)**:配置编辑屏(10-03-yaml-editor:编辑/新建/删除/模板)、看图屏(10-03-image-input)、updater「检查更新」(v111-release)。

## 缺口矩阵(业界普遍有 → MYIA 现状 → 建议)

| # | 能力域 | 业界证据(代表) | MYIA 现状 | 建议 |
|---|---|---|---|---|
| G1 | 条目搜索 | Inoreader 全文搜索+查询构建器;MISP 属性搜索;Apify 数据集过滤 | 零搜索(feed 无搜索框;store 数据在 SQLite 只能 CLI 查) | **P1**,feed 屏加搜索框,走 sidecar 查询扩展 |
| G2 | 条目详情/打开原文 | Feedly 原文视图+高亮笔记;各工具详情面板普遍 | 仅 2 行截断+点击标已读;URL 只在 title 悬浮,无外链打开 | **P1**:卡片展开全文 + 系统浏览器打开原文(Tauri shell open,零协议改动) |
| G3 | 数据导出 | SpiderFoot CSV/JSON;MISP 多格式;Apify 7 格式+字段裁剪;Octoparse 文件/DB/API | 零导出 | **P1**:feed JSONL+CSV 导出(本地写文件),复用 store 查询 |
| G4 | 排程管理+手动触发 | Apify cron 可视化+Next runs 预览;Octoparse 频率/最大运行时止损;ParseHub Test/Run/Schedule 三合一 | 排程只是品类卡上文字;无排程管理页;无单源/单品类手动触发(feed 仅空态 CTA) | **P1**:品类卡「跑一次」+ 排程页(Next runs 预览防 cron 写错) |
| G5 | 推送测试与告警规则 | OpenCTI 通知中心+触发器+分级 digest;Inoreader Rules(条件→动作);changedetection 70+ 渠道 | 推送凭据可配但无「发条测试消息」;无关键词即时告警/digest 汇总 | **P1 测试按钮**(设置屏,一次 RPC);**P2 告警规则**(Rules 式,衔接关键词漏斗) |
| G6 | 趋势统计 | Brand24 声量/异常尖峰;Inoreader Intelligence;OpenCTI 自定义图表 | 仅最近 N 次成功率数字,无时间序列 | **P2**:采集量/成功率折线(run 记录已有数据,纯前端可画) |
| G7 | run 重跑/过滤/日志搜索 | Apify run 详情+重试;Octoparse 历史检视 | run 列表无重跑、无按品类/状态过滤;日志无搜索 | **P2**(重跑依赖 G4 的手动触发通道) |
| G8 | AI 呈现增强 | Feedly 卡片摘要/去噪折叠;Visualping AI 告警摘要;Brand24 情绪标注 | 仅精评分数 Badge(已有);无摘要动作、无情绪/优先级标注 | **P2**:卡片「AI 摘要」按钮(enrich 管线现成);情绪标注 v2 再议 |
| G9 | 快捷键与批量操作 | Feedly S/T/X 快捷键;各阅读器全部标已读 | 零快捷键、无批量已读 | **P3** |
| G10 | 代理可用性测试 | changedetection Proxy Scanner 逐个实测+按 watch 绑定 | 设置只有池凭据录入,无连通性测试/状态 | **P3**(doctor --config 探测已有,差 UI 按钮) |
| G11 | 凭据整包迁移 | SpiderFoot API key 导入导出(换机) | 钥匙链凭据无导出/备份 UI | **P3**(有安全权衡,需单独拍板) |
| G12 | 就地订阅(亮点) | OpenCTI 实体页快捷订阅铃铛:结果就地转监控触发器 | 无(看到好情报无法就地沉淀为关键词/源) | **P2 候选亮点**:条目卡「沉淀为关键词」入口,衔接 yaml-editor |

## 明确不适用/刻意不做(F 类延伸)

- **无代码点选构建器**(Octoparse/ParseHub 路线):MYIA 定调 agent 读 SKILL.md 写 YAML,构建器与 AI-native 定位冲突,不做。
- **案件/图谱/协作域**(Maltego Cases、OpenCTI Investigations、MISP 共享/权限/RBAC、Hunchly 取证包):单人桌面无协作模型,整体不适用。
- **OPML/订阅发现/模板市场**(Inoreader/Feedly 生态):MYIA 源=git 源码化插件,非 RSS 订阅器;「模板」已按 yaml-editor 决议收敛为单最小模板。
- **云端索引类能力**(Inoreader 全局搜索、Brand24 社交语料、Feedly AI Feeds/Newsletter 投递、Storm Alerts):依赖全网索引,本地版天然不可行,不追。
- **资源编排类**(Apify 每 run 内存分配/秒级触发、云排队):单机只有并发上限+队列,只借鉴 UI 呈现。

## 与已有任务档的关系(不重复立档)

- 源的新建/编辑/删除/模板 → `10-03-yaml-editor`(grill Round 1 已全拍)。
- 看图(OCR+VL+云端) → `10-03-image-input`(已全拍)。
- updater「检查更新」UI → `v111-release`(R2-1 已批做全)。
- feed 游标分页协议注记 → gap-census C 类协议缺口(已有档)。
- 本普查新增缺口 G1-G12 **均未立实现档**,路由待主人拍板(建议 P1 四项 G1-G4 可并为一个 feed-ux/调度批次任务,P2/P3 进 v12-backlog 池)。

## 验收标准

- [x] 三路业界调研完成并留痕(`research/` 三份原始报告,能力点均带工具名+证据 URL)
- [x] MYIA 基线为源码实读结论(五屏+顶栏,commit d84ec05 时点)
- [x] 缺口矩阵 G1-G12 落档,含业界证据/MYIA 现状/优先级建议
- [x] 不适用域(F 类延伸)与已有任务档关系明确,无重复立档
- [ ] 路由拍板(待主人;见下)

## 待主人拍板

1. G1-G4 是否按建议并为一个批次任务先行(排 v111-release 之后、v112-desktop-batch 之前/并行?)
2. G5 告警规则(Rules 式条件→动作)是否纳入 v1.2 范围(涉 sidecar 协议扩展)
3. G11 凭据导出的安全口径(默认不做/明文导出需二次确认)
4. F 类四条「刻意不做」是否认可归档
