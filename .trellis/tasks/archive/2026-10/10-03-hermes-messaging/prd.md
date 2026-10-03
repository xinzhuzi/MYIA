# 消息平台总纲:Hermes 全平台定向推送移植

## 需求源

主人 2026-10-03 指令:按 Hermes 源码(本地 `~/.hermes/hermes-agent`,NousResearch/Hermes-Agent,MIT)在 MYIA 内做一个消息平台,使「推送有具体的推送对象」;Hermes 有的平台都要迁移。

AskUserQuestion 三项决议(2026-10-03):

1. **集成方式 = 源码移植进 MYIA**(否决桥接本机 Hermes、否决整体内嵌 gateway)
2. **平台范围 = Hermes 有的全量迁移**(分波次,见下)
3. **深度 = 定向出站**(每条推送可指定具体会话;不做双向收发)

## 名词

- **消息平台**:通道目录(channel directory)+ 对象解析(target resolution)+ 定向投递(targeted delivery)三层,总称。
- **推送对象(target)**:某平台下的一个具体会话——群 / 私聊 / 话题,如飞书「AI中转站合伙人群」。
- **通道目录**:各平台全部可达推送对象的缓存地图(名称、类型、id),Hermes 对应 `gateway/channel_directory.py`。

## 移植原则(grill 2026-10-03 Q1 定案:蓝本移植)

- **蓝本移植,不 vendor 原文**:逐文件对照 Hermes 源码结构重写为 MYIA 风格,模块 docstring 标注上游文件与 MIT 归属;不整块拷贝原文、不引 git 子模块。逐字 vendor 已否决;若将来改主意,先改 spec 再动工。
- **依赖红线不破**:核心 6 依赖(httpx/selectolax/PyYAML/APScheduler/pydantic/jinja2)之外不加核心依赖;各平台一律 httpx 直连官方 API,不引平台 SDK;接不上官方 API 的平台(如 signal 需 signal-cli)进 extras 并结构化报错。
- **上游对照表留档**:每个移植模块在任务档案登记 Hermes 源文件路径,便于日后 diff 上游演进。

## Hermes 全量平台清单与波次

| 波次 | 平台 | Hermes 源位置 | 说明 |
|---|---|---|---|
| W1(本轮) | feishu 飞书 | `plugins/platforms/feishu/` | MYIA 已有 im/v1/messages 通道,补目录发现 |
| W1(本轮) | telegram | `plugins/platforms/telegram/` | MYIA 已有 sendMessage;目录靠被动积累 |
| W1(本轮) | —(核心引擎+UI) | `gateway/channel_directory.py` 等 | 见子任务 |
| W2(已执行完 2026-10-03,commit 48284fd) | wecom 企微、dingtalk 钉钉、ntfy | `plugins/platforms/{wecom,dingtalk,ntfy}` | `10-03-messaging-w2-platforms`:三平台蓝本形态(事实核订:企微=自建应用 text 私聊出站、钉钉=静态自定义机器人 text、ntfy=一次性 POST;三家零目录发现;「bot relay 依赖」旧说不实——微信走 iLink,见下) |
| 独立(已执行完 2026-10-03,commit fa9aaf6;真机桥接冒烟 ✓ 实发主人微信) | weixin 微信 | `gateway/platforms/weixin.py` | `10-03-messaging-weixin-bridge`:可选桥接(iLink 每条出站须回显 context_token,token 只由常驻 long-poll 落盘,冷发必拒——自实现即半个常驻服务,已否决;出站经本机 Hermes) |
| W3 | slack、discord、whatsapp_cloud、signal、line、matrix、mattermost、google_chat、teams、email、sms、irc、simplex、bluebubbles、msgraph_webhook、qqbot、yuanbao、a2a、buzz、photon、raft、homeassistant | `gateway/platforms/` 与 `plugins/platforms/` 对应目录 | 长尾;官方 API 直连可行的先做,需外部守护进程的(signal/bluebubbles)标 extras |

W3 不预建子任务,到波次开工时再建(本 PRD 清单即登记锚点);W2 经 grill round-2 已立档两任务(见子任务地图),执行等主人令。

## 子任务地图(W1)

| 子任务 | 交付 |
|---|---|
| `10-03-messaging-core` | 平台无关引擎:目录存储/对象解析/定向投递/死信/适配器接口 + schema 扩展 |
| `10-03-messaging-feishu` | 飞书适配器:目录发现(机器人所在群/私聊/话题)+ 定向发送 |
| `10-03-messaging-telegram` | Telegram 适配器:chat_id 被动积累目录 + 定向发送 |
| `10-03-messaging-ui` | 桌面端第 6 屏「消息」:目录浏览/别名编辑/规则挑对象(完整写回,含 `push.write` 新端点) |
| `10-03-messaging-platforms`(review,56728a1+bb71d57+2e7e252) | 平台总览网格/三档筛选/三态徽标/分平台凭据指南(截图对照矩阵四个出站缺口) |
| `10-03-messaging-hermes-look`(在途流水线) | 照 Hermes 源码重皮消息页:平台头像/左网格右详情面板/三态色彩/状态条(模仿纪律:以上游为准最大程度贴近) |
| `10-03-messaging-w2-platforms`(planning,执行等令) | W2 三平台适配:企微/钉钉/ntfy 蓝本形态,三件套齐 |
| `10-03-messaging-weixin-bridge`(planning,执行等令) | 微信可选桥接:出站经本机 Hermes,PRD 齐(design/implement 于 start 前补) |

执行顺序:core → feishu/telegram(可并行)→ ui(依赖前三者可演示)。core 未落地前其余三个不得 start。

## 跨子验收(父任务收口)

- [ ] **[manual]** 端到端:一条 YAML 规则 `when: category in ['freebie']` + `targets: ["feishu:AI中转站合伙人群"]`,run 后该群收到卡片;同规则去掉 targets 行为与现网完全一致(向后兼容)。
  - 向后兼容半项已自动核对(2026-10-03 收口真跑):`tests/test_push_schema_targets.py::TestGoldenRegression::test_legacy_yaml_load_byte_identical_to_before` + `tests/test_messaging_pipeline.py::TestLegacyPathUnchanged::test_no_targets_sends_without_target_context` 绿;「该群收到卡片」须真机 run,未验
- [ ] **[manual]** 目录:真机飞书适配器自动发现的群含「AI中转站合伙人群」;私聊经别名登记后可达(飞书列表 API 不返回私聊,grill Q5 定案);别名改动后按名推送命中。
  - 机制半项已单测覆盖:`tests/test_push_directory.py::TestAliasOverlay`(别名重建后仍生效、未发现 id 占位)+ `tests/test_push_targets.py::TestDirectoryResolution`(按名/唯一前缀命中);「真机发现含该群」须真机 refresh,未验
- [x] 死信:`forbidden`/chat 级 `not_found` 单次即标 dead、跳过+结构化日志;成功一次自愈(grill Q3 定案,Hermes 原味错误分类制)。
  - 证据(2026-10-03 收口真跑):`tests/test_push_delivery.py::TestSendBatchToTargets` 的 `test_forbidden_marks_dead_after_single_failure_no_threshold` / `test_chat_level_not_found_marks_dead_thread_level_does_not` / `test_dead_skip_logs_structured_info_no_alert_card` / `test_success_heals_dead_mark` / `test_transient_failure_never_marks_dead`,管线落账 `tests/test_messaging_pipeline.py::TestDeadLedgerWiring::test_forbidden_via_pipeline_lands_in_ledger_file`;commit 2b54865(core 主体随 fbba437 批量入库,见 W1 执行结果)
- [ ] **[manual]** 真机冒烟对象(grill Q6 定案):飞书「AI中转站合伙人群」+ Telegram 主人与机器人的私聊。未验,须主人真机执行(清单见 W1 执行结果·遗留)
- [x] 全量测试绿:push 层新旧单测 + 桌面协议测试不回归。
  - 证据(2026-10-03 收口两次真跑,混合工作树含并行会话在飞改动):首跑(世事改名入树前)`python -m pytest tests/ -q` → **1759 passed / 14 skipped 全绿**,`-k push` → 221 passed,`tests/test_desktop_sidecar_protocol.py` → 85 passed,desktop/ui-src `npm test` → 97 passed(含 messaging-screen 8 项);终跑(并行 cb87302「世事/shishi 改名」与 docs 批次入树后)→ 7 failed / 1764 passed / 14 skipped,**7 失败全部归属并行在飞会话**(4 = CLI/协议测试仍断言 `myia 1.1.1`/`MYIA run:` 而 cb87302 已改 `shishi 1.1.1`/`世事 run:`;3 = test_docs 文档重构在飞),消息平台范围零失败(`-k push` → 222 passed;消息九测试文件 200 passed;sidecar 84/85,唯一失败即上述品牌改名契约测试,与四端点无关;desktop/ui-src `npm test` → 105 passed)
- [x] W1 收口后回填 W2 开工条件;W2 不自动接力,等主人指令排期(grill Q7 定案)。
  - 证据:W2 开工条件已回填至「W1 执行结果」节;grill round-2(2026-10-03 晚)进一步立档 `10-03-messaging-w2-platforms`(三件套齐)与 `10-03-messaging-weixin-bridge`(PRD),执行仍等主人令

## Grill 决议记录(2026-10-03,round 1 终;round 2 = W2 立项,见下)

- **Q1 集成方式 = 蓝本移植**(逐字 vendor 否决;spec 红线不动)
- **Q2 targets 同平台约束**:元素平台前缀须与条目通道一致,加载期即拒;跨平台 = 多条 push 条目;targets 在场时 legacy `target` 可省
- **Q3 死信 = Hermes 原味**:错误分类制(forbidden/chat 级 not_found 单次标 dead,瞬态不标;成功自愈;跳过+日志),无 N 阈值无配置口
- **Q4 刷新 = run 节流懒刷**(>5 分钟才刷,失败退回旧目录)+ CLI `myia channels refresh` + UI 按钮三层
- **Q5 飞书私聊/话题 = 纯别名手工登记**(不做 callback 半吊子入站记录)
- **Q6 冒烟对象 = 飞书「AI中转站合伙人群」+ TG 私聊**
- **Q7 W2(微信/企微/钉钉/ntfy)= 等主人指令,不自动接力**
- **Q8 UI = 完整写回**:新增 `push.write` sidecar 端点(照 `sources.write` 范式),选择器直写 YAML

事实核订两轮(MYIA 侧:装配点/凭据/协议/CLI/工具链;Hermes 侧:解析顺序/死信语义/目录发现/别名机制),证据与偏离注记已落各子任务档案。

## Grill 决议记录 round 2(2026-10-03 晚,W2 立项,四条全按推荐)

- **Q1 排期口径 = 文档立齐、执行等主人令**(维持 round-1 Q7;冒烟欠账不自动滚进 W2)
- **Q2 W2 范围重排 = 企微+钉钉+ntfy 三轻平台,微信移出单议**(事实核订修正:企微蓝本无群机器人 webhook 形态、钉钉纯 HTTP 仅静态自定义机器人 text、微信无官方出站 API)
- **Q3 微信 = 可选桥接插件**(出站调本机 Hermes;整体桥接的否决不适用于单平台可选形态;无 Hermes 则灰卡披露,开源边界如实)
- **Q4 任务切分 = 三平台打包一任务**(`messaging-w2-platforms`),微信桥接独立任务
- 事实探查(背景代理,file:line 落 W2 任务档):四平台零目录发现;微信 iLink 冷发必须吃常驻轮询的 context_token(自实现已否决的硬依据);旧说「微信依赖 bot relay」不实(bot_relay 是桌面 bot 模式的文件中继,与微信无关)

## W1 执行结果(2026-10-03 收口)

### 交付 commit(四个子任务全部实现入库)

| 子任务 | commit | 交付 |
|---|---|---|
| messaging-core | **fbba437**(主体:directory.py/targets.py/delivery.py/schema `targets`/管线接线+五个测试文件随批量提交先行入库)+ **2b54865**(core 专属收口:directory.py 修剪 + test_messaging_pipeline.py 管线接线测试) | 平台无关引擎:通道目录/对象解析/定向投递/死信账本 + `PLATFORMS` 注册表 + schema `targets` 字段(同平台约束);上游对照表见 core 子任务 prd.md |
| messaging-feishu | **b3c8084** | 目录发现(im/v1/chats 翻页、429 退避、401 结构化错误)+ 定向发送(`SendContext.target` 优先、receive_id_type 适配)+ CLI `myia channels refresh/list` + 测试×3(test_feishu_discovery / test_feishu_targeting / test_cli_channels) |
| messaging-telegram | **ed1276f** | 被动目录(poller `on_chat` sink → `ChannelDirectory.merge_entries` 增量积累)+ 定向发送 + CLI passive 分支(refresh 缺省全平台时无发现 API 的 telegram 归 passive 上报,非失败)+ test_messaging_telegram |
| messaging-ui | **4be1325** | 第 6 屏「消息」(平台分组目录/别名行内编辑/死信徽标 + 规则挑对象)+ sidecar 四端点(channels.list / channels.refresh / channels.alias、push.write 照 sources.write 文本手术范式)+ spec/desktop/sidecar-protocol.md 镜像同步 |

注:messaging-core 主体代码随 fbba437「P0 desktop data-path fix…parallel v1.1.2 feature batch」批量提交先行入库(该 commit 混载多任务),2b54865 为其专属定案 commit——考据边界如实记档。

### 收口核验(2026-10-03,集成会话真跑)

- 首跑(世事改名入树前):`python -m pytest tests/ -q` → **1759 passed / 14 skipped 全绿**;`-k push` → 221 passed;`tests/test_desktop_sidecar_protocol.py` → 85 passed;desktop/ui-src `npm test` → **97 passed**(11 文件,含 messaging-screen 8 项)
- 终跑(并行 cb87302 改名/docs 批次入树后):全量 7 failed / 1764 passed / 14 skipped——7 失败逐条归属:2× test_cli.py + 1× sidecar `test_oneshot_passthrough_preserves_cli_contract`(cb87302 把 CLI 改名 shishi/世事,测试仍断言 myia/MYIA 前缀)+ 3× test_docs.py(docs/en/schema.md、docs/zh/schema.md、docs/demo/README.md 文档重构在飞)+ 1× test_skill_install.py(AGENTS.md/skill 路径在飞);**消息平台范围零失败**(`-k push` 222 passed;消息九测试文件 200 passed);desktop/ui-src `npm test` → **105 passed**(messaging-screen 在内,UI 侧无失败)
- 收口期间并行会话持续提交(845f64a/da390fc/cb87302 落地);golden 回归测试自 845f64a 起以「纯增字段容忍」断言形态入库(容忍他任务缺省为空的新增键,取值漂移仍失败)

### 遗留

- **真机冒烟实测记录(2026-10-03,主控代跑,凭据铸自 ~/.hermes/profiles/ai-analyst/.env)**:①`channels refresh feishu` 真跑成功——但 bot 实际所在群只有**「AI福利群」(oc_5fdad6bc…)**,Q6 定案的「AI中转站合伙人群」不在(bot 未入群;Hermes 目录里的它来自入站历史而非 bot 所在群)——**若要以该群为推送对象,先把机器人拉进群再 refresh 即可发现**;②私聊 oc_9a79… 别名登记为「冒烟私聊」后 `feishu:冒烟私聊` 解析命中 ✓;③定向卡端到端真发 ✓(API 200,卡入 AI福利群,dedup_key=smoke-20261003-feishu-001)。**仍留主人**:TG 侧冒烟(本机与 Hermes 均无 TELEGRAM 凭据,需主人建 bot 配 token 后发消息进目录)+ 重皮后消息屏视觉过目(Tauri dev)。
- 四个子任务 task.json 状态字段仍为 planning(仓库惯例:并行会话在场直改 task.json;本收口不触碰 task.json,状态翻转与归档留主人)
- 飞书话题(thread)定向发送:目录先记 thread_id,发送侧未实装(feishu 子任务明记非目标,等真实需求再开)

### W2 开工条件回填(grill Q7:等主人指令排期,不自动接力)

- 引擎侧零障碍:`PLATFORMS` 注册表、`Channel` 协议三能力(`supports_targeting`/`parse_direct_ref`/`discover_directory`)、死信账本、`myia channels` CLI 均已就位
- **round-2 已立档并完成架构 grill**(原「weixin 开工先 grill 架构」条件已清):`10-03-messaging-w2-platforms`(企微/钉钉/ntfy 蓝本形态,三件套齐)+ `10-03-messaging-weixin-bridge`(可选桥接,iLink 事实定案);执行等主人令,最佳时机=真机冒烟清账后
- 事实更正:微信依赖是 iLink context_token(非 bot relay);wecom/dingtalk 走 telegram 范式(无目录发现,别名+直达)而非 feishu 范式(仅飞书有列表 API)

## W2 执行结果(2026-10-03 收口,主控代跑)

- `10-03-messaging-w2-platforms`:48284fd(企微/钉钉/ntfy 蓝本形态适配;注意该 commit 顺带收编了 vision 会话在 schema/pipeline/entry 等共享文件的在飞挂点,其提交信息已自认混载);task.json review。
- `10-03-messaging-weixin-bridge`:fa9aaf6(出站经 hermes CLI 子进程桥接,design/implement 档随流水线补齐);**真机冒烟 ✓**——标注消息经 MYIA→hermes→iLink 实发主人微信私聊(dedup_key=smoke-20261003-weixin-001);task.json review。
- 门禁教训沉淀:多会话仓库门禁必须「本域」判定(pytest 文件白名单 + vitest 消息屏白名单),全绿要求会被并行在途红死锁;配额断供(1308)经 resume 零重付续跑。
- 剩主人:企微/钉钉/ntfy 各一条真机冒烟(凭据清单在各任务 prd)+ W3 排期。

## 非目标

- 双向收发 / 入站消息处理 / 会话状态机(主人明确定向出站;将来单独立项)
- 整体内嵌 Hermes gateway(已否决);**单平台可选桥接仅微信一处**(round-2 Q3 批,其余平台不桥接)
- W3 平台的任何实装(仅登记)
