# PRD:情报流告警规则引擎(v12-backlog G5 主体)

## 背景

- 源头:UI 普查 G5(`archive/2026-10/10-03-ui-feature-census/prd.md:28`)——业界普遍有
  「通知中心+触发器+分级 digest」(OpenCTI)与「Rules 条件→动作」(Inoreader),MYIA 现状
  =推送凭据可配但无关键词即时告警、无 digest 汇总;拍板 P2,主体入 v12-backlog 池
  (池档 `10-03-v12-backlog/prd.md` 第 5 项)。
- **G5 前半已落地**:设置屏推送测试按钮(`push.test`,`desktop/entry.py:3613`,
  feed-ux 批);本档只做**主体:告警规则引擎**。
- 本档为 planning 档:收现状、Requirements/AC 草案与 grill 待决;**不实现**。
  开工前按 grill 决议拆 design/implement。

## 模仿对象:Inoreader Rules 语义

census 调研原始报告(`archive/2026-10/10-03-ui-feature-census/research/feed-monitors.md:21`):

> Rules:按**关键词/来源/提及**条件触发**打标、推送、邮件、标记已读**,2025 年起新增
> 「翻译/摘要」动作(Inoreader 官方博客,2025-10)。

MYIA 式转译:**条件(对一条情报的谓词)→ 动作(推送指定通道/加标签/沉淀关键词)**;
规则可启停、可即时测试(Inoreader 的 "test rule" 对应本仓 `push.test` 先例)。
OpenCTI 的「分级 digest」(汇总勿扰)是第二形态,v1 是否收见 Grill Q7。

## 现状实读(2026-10-04,main@1d4ad62)

### 条件候选(数据面支撑,`src/myia/store`)

| 条件 | 支撑字段/机制 | 出处 |
|---|---|---|
| 品类 | `ItemRecord.category`(七品类+channel) | `src/myia/store/models.py:85` |
| 关键词 | 标题/正文/来源三列 LIKE(`list_items.query`)与 metadata 任意字段 | `src/myia/store/base.py:61-79` |
| score 阈值 | `ItemRecord.scores`(enrich 精评回填,dict 维度→分值;前端取最大值) | `src/myia/store/models.py:86`、`ui-src/src/screens/feed/api.ts:254-256` |
| 免费信号 | 品类判定内部阶段「free signal beats paid signal」→ freebie/token;`BuiltinResult.matched` 留命中 trace | `myia-classifier/myia_classifier/builtin.py:14-15,140-145` |
| 提及量趋势 | `metric_history` 品类级 `keyword:<词>` mentions 快照(周环比现成) | `src/myia/store/base.py:258-330` |

**既有条件求值器(最大复用点)**:`push.route[]` 已是「条件→模式」雏形——
`RouteRuleConfig {when: ExprStr, mode, targets}`(`src/myia/schema.py:991-1007`),
`when` 表达式走 classify 白名单 AST 求值器(字面量/字段名/链式比较/and·or/
abs·min·max·round·len·int·float·str 白名单函数;禁属性访问/下标/lambda;
表达式 ≤1000 字符;资源护栏防 `9**9**9`;**never eval**),
`myia-classifier/myia_classifier/custom.py:11-31`。求值上下文 = `Item.view()`
(metadata 平铺 + url/title/source/category/scores/dedup_key),
`src/myia/pipeline.py:465-477`。

### 动作候选(方法面/管线面支撑)

| 动作 | 复用面 | 出处 |
|---|---|---|
| 推送(即时/指定通道+对象) | `send_immediate`(AM/PM 同槽位防重发注册表)、通道 targets 定向 | `src/myia/push/digest.py:292-351` |
| 推送(digest 汇总) | `DigestAggregator`(slot 内聚合、压制窗口) | `src/myia/push/digest.py:140-274` |
| 测试发送 | `push.test` 合成单条真发(先例,G5 前半) | `desktop/entry.py:3613` |
| 加标签 | `Item.add_tags` / classify rule tag 通路 | `src/myia/pipeline.py:480-486` |
| 沉淀为关键词 | G12 就地面板写品类 YAML `watchlist.keywords` | `ui-src/src/screens/feed/feed-screen.tsx:121-129` |

### 管线挂点(求值时机候选的物理位置)

`Pipeline.run` 阶段链 = fetch→classify→dedup→analyze(enrich 回填 score)→
aggregate(可选)→push→maintenance(`src/myia/pipeline.py:942-1166`)。
push 阶段内已有 `resolve_route` 逐条路由(`src/myia/push/route.py:1-22`,
决策序:score 规则→品类覆盖→七品类缺省映射→保守 digest)。
sidecar 面:`run.start` 单飞(`run_busy`),终态走 `completed` 事件;
`store.items` 直读条目;`_HANDLERS` 现 43 方法,`PROTOCOL_VERSION = 6`
(`desktop/entry.py:336,3571-3615`)——新增方法族须 bump v7 并同步
`.trellis/spec/desktop/sidecar-protocol.md` 镜像表。

### feed 屏既有过滤/交互(去重关系参照)

- 过滤页签 未读/星标/稍后读/全部 = **前端 localStorage 本地态**
  (`ui-src/src/screens/feed/api.ts:154-236`,loadFeedStates/toggleMarker/
  setMarkerBulk/applyFeedFilter),服务端无已读/星标列;
- 搜索 G1 走 `store.items.query`(防抖 300ms);G8 单卡 AI 摘要;G12 沉淀面板;
- watchlist mute(品类 YAML `WatchlistConfig.mute`,`src/myia/schema.py:858-862`)
  = 「不感兴趣」语义,与告警触发是压制关系候选(Grill Q5)。

## Requirements 草案(grill 后定稿)

1. **规则模型**:一条规则 = `{名称, 启停, when 表达式(复用 route 同款白名单
   AST 文法,零新语法), 动作, 作用域(全局/单品类)}`;坏表达式装载期拒(fail fast),
   运行期求值错按 classify 先例隔离(单条 WARNING + 视为未命中,不 break 批)。
2. **求值**:情报入流时对每条新条目求值(挂点与时机见 Grill Q3);同条目同规则
   只触发一次(fired 记录去重,见 Grill Q5)。
3. **动作(v1 候选集,裁剪见 Grill Q4/Q7)**:推送指定通道(含 targets 定向、
   可选 template)、加标签;每动作复用既有同门实现,不自建第二套发送路径。
4. **sidecar 协议扩展**:`alerts.*` 方法族(清单/保存/删除/试跑,形状见 Grill Q1)+
   `alerts.fired` 事件;`PROTOCOL_VERSION` 6→7;spec 镜像表同步。
5. **规则管理 UI**:入口见 Grill Q6;规则列表(启停开关、命中计数)+ 新建/编辑表单
   (when 表达式 + 动作选择)+ 即时测试(合成条目或取最近一条实跑)。
6. **可观测**:fired 历史可查(时间/规则/条目);规则求值错误进 logs.tail 可见。
7. **零惊扰默认**:不配规则 = 现行为逐字节不变;告警与既有 push.route 并行不扰
   (route 照旧决定 immediate/digest,告警是叠加通道)。

## Acceptance Criteria 草案(开工时按 grill 决议改定)

- [ ] 规则 CRUD 全走 sidecar `alerts.*`,协议版本 bump 至 v7 且 spec 镜像表对账一致
- [ ] when 表达式复用 classify 白名单 AST:越权构造(属性访问/下标/lambda/超长)装载期结构化拒;`9**9**9` 类资源炸弹不出进程
- [ ] 命中即触发且同条目同规则不重复触发(重启 sidecar 后仍不重发)
- [ ] 推送动作经 `send_immediate` 同门:同槽位防重发注册表生效;通道失败结构化上报不拦其余条目
- [ ] 不配规则时全链路行为与现状逐字节一致(既有 route/digest 测试全绿)
- [ ] mute 命中条目的告警行为符合 grill Q5 决议(触发/压制,测试钉死)
- [ ] 规则管理 UI:建/改/删/启停/即时测试五操作落地,坏表达式 UI 内可见结构化错误
- [ ] fired 历史可查且 logs.tail 可见求值错误

## Grill 待决(8 问,每问带推荐)

**Q1 协议面形状**:`alerts.*` 方法族怎么切?五方法(list/save/delete/test/dryrun)
还是三方法(list/save/delete)+ 既有 `push.test` 借用?
**推荐**:四方法 `alerts.list / alerts.save / alerts.delete / alerts.test`
(save 承建/改,全量或按 id 替换同 push.write 先例;test = 取参数里的合成条目或
`item` 引用实跑一次求值并 dry 式展示将触发的动作,不真发;真发测试借既有
`push.test`)。事件 `alerts.fired {rule_id, rule_name, item_id, action, ts}`
(UI 角标/通知用)。协议 v6→v7。

**Q2 规则存储位置**:品类 YAML 新顶层节(sidecar PrivateAttr 先例 `_baseline`/
`_aggregate`/`_images`,`src/myia/schema.py:1566-1580`)vs SQLite 新表 vs
独立 `<home>/alerts.yaml`?
**推荐**:**SQLite 新表 `alert_rules`(rule 定义)+ `alert_fired`(命中历史)**。
理由:规则是 UI 高频增删的**运行态**而非源码化配置(feedback/tuning 同为先例);
跨品类全局规则(关键词条件天然跨品类)塞进单品类 YAML 语义拧巴;fired 去重
必须落库,定义与命中同库可同事务;品类 YAML 12 节公开契约不动(SKILL.md 一致性
测试零波及)。代价:规则不随 git 源码化——接受(与 feedback/tuning 同待遇)。

**Q3 求值时机**:ingest 时(pipeline 新 stage)vs run 完成后(sidecar 扫
`since` 上次扫描点)vs 独立定时任务?
**推荐**:**ingest 时**,挂 `Pipeline` push 阶段之后、maintenance 之前,作
不进 EXECUTED_STAGES 的轻量附加步(或并入 push 阶段收尾)。理由:analyze 后
score 已回填,条件字段最全;`Item.view()` 求值上下文与 route 求值同点同构;
dry-run 语义自然跟随(零持久化副作用=零告警);断点续跑不重复求值
(dedup 后条目只过一次)。sidecar 扫描方案的「零 pipeline 改动」优势不成立:
store 侧 `raw` 投影不含全部 metadata 求值面,补投影反而更大。

**Q4 动作集与推送通道复用**:v1 动作收哪些?推送怎么复用?
**推荐**:v1 动作二选一存储但实现三态:`push`(channel+targets?+template?,
走 `send_immediate` 同门,槽位注册表防重发免费获得)与 `tag`(走
`Item.add_tags` 语义,入库前加标签);**「沉淀为关键词」动作留 v2**(G12 已有
就地面板,规则化沉淀等 UI 需求实证)。分级 digest 汇总(OpenCTI 式)留 v2,
v1 告警一律即时单卡。

**Q5 与既有过滤/dedup 的关系**:命中去重用什么?watchlist mute 与告警谁赢?
与 push.route 的优先级?
**推荐**:fired 去重 = `alert_fired (rule_id, dedup_key)` 唯一约束(独立于
`dedup_registry`——那是推送槽位防重发,语义不同不复用表);**mute 命中不触发**
(watchlist mute = 用户明示不感兴趣,告警硬穿 mute 违背漏斗语义,测试钉死);
push.route 照旧并行跑:告警是**叠加**通道不改变 route 的 immediate/digest 判定,
route immediate 命中 + 告警命中 = 两条都发(通道不同即不同卡;同通道同槽位时
槽位注册表自然压制重复卡)。

**Q6 规则管理 UI 入口**:消息屏规则区扩(现 push 规则面板旁,
`ui-src/src/screens/messaging/messaging-screen.tsx:451-475`)vs 设置屏新分区 vs
feed 屏条目卡就地「以此建规则」?
**推荐**:v1 = **消息屏下区**加「告警规则」子面板(与推送规则同属「消息怎么发」
心智,数据面 `alerts.list` 一次拉齐);feed 卡就地建规则(预填 title 关键词条件,
Inoreader 式顺手)留 v2——G12 沉淀面板已占就地交互位,先不叠加。

**Q7 v1 范围裁剪**:条件算子直接复用 when 白名单表达式,还是另造表单化条件构建器
(Inoreader Query Builder 式)?digest 汇总要吗?
**推荐**:条件 = **when 表达式原文,v1 不做表单构建器**(白名单 AST 已含
品类/关键词 contains(`in`)/score 阈值/免费信号=`category in ['freebie','token']`
全部候选形态;文法一致性测试与资源护栏现成);表单化(下拉拼条件)留 v2。
digest 汇总留 v2(见 Q4)。提及量趋势条件(metric_history 周环比)留 v2——
它是「品类级」而非「条目级」谓词,求值模型不同,不混入 v1。

**Q8 CLI 面要不要同步暴露**:`myia alerts` 子命令(建/删/列/历史)还是桌面独占?
**推荐**:v1 **桌面 sidecar 独占 + CLI 只读历史**(`myia alerts list` 查 fired,
对齐 `feedback list` 先例);规则的建删走 UI(运行态数据,与 feedback mark 的
desktop 通道同哲学)。CLI 写路径留 v2 视需求。

## 边界(明确不做)

- 不做云端索引式告警(Brand24/Google Alerts 全网监听,F 类延伸,census 拍板不追)。
- 不做 70+ 渠道矩阵(changedetection 式);通道面 = 既有 `myia.push` 通道集。
- 不做表单化条件构建器、趋势/提及量条件、digest 汇总、规则级翻译/摘要动作(v2 候选)。
- 本档不改任何代码;实现前须按 grill 决议补 design.md(协议契约钉死)与 implement.md。

## 关系档

- 父档(池):`10-03-v12-backlog`(第 5 项 G5 主体);拆任务引用闭环由父档 AC 约束。
- 证据档:`archive/2026-10/10-03-ui-feature-census/prd.md` G 矩阵 G5 行 +
  `research/feed-monitors.md`(Inoreader Rules 语义与官方 URL)。
- 前半搭车档:`10-03-feed-ux`(push.test)。
- 相邻入池项:G6 趋势折线(与 store.trend/metric_history 同族)、G12 沉淀入口
  (本档 v2 动作的 UI 依托)。
