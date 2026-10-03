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
| W2 | weixin 微信、wecom 企微、dingtalk 钉钉、ntfy | `gateway/platforms/weixin.py`、`plugins/platforms/{wecom,dingtalk,ntfy}` | 中文 IM 群+最轻推送服务;weixin 依赖 bot relay 架构,是 W2 最重项 |
| W3 | slack、discord、whatsapp_cloud、signal、line、matrix、mattermost、google_chat、teams、email、sms、irc、simplex、bluebubbles、msgraph_webhook、qqbot、yuanbao、a2a、buzz、photon、raft、homeassistant | `gateway/platforms/` 与 `plugins/platforms/` 对应目录 | 长尾;官方 API 直连可行的先做,需外部守护进程的(signal/bluebubbles)标 extras |

W2/W3 不预建子任务,到波次开工时再建(避免空壳任务);本 PRD 的清单即它们的登记锚点。

## 子任务地图(W1)

| 子任务 | 交付 |
|---|---|
| `10-03-messaging-core` | 平台无关引擎:目录存储/对象解析/定向投递/死信/适配器接口 + schema 扩展 |
| `10-03-messaging-feishu` | 飞书适配器:目录发现(机器人所在群/私聊/话题)+ 定向发送 |
| `10-03-messaging-telegram` | Telegram 适配器:chat_id 被动积累目录 + 定向发送 |
| `10-03-messaging-ui` | 桌面端第 6 屏「消息」:目录浏览/别名编辑/规则挑对象(完整写回,含 `push.write` 新端点) |

执行顺序:core → feishu/telegram(可并行)→ ui(依赖前三者可演示)。core 未落地前其余三个不得 start。

## 跨子验收(父任务收口)

- [ ] 端到端:一条 YAML 规则 `when: category in ['freebie']` + `targets: ["feishu:AI中转站合伙人群"]`,run 后该群收到卡片;同规则去掉 targets 行为与现网完全一致(向后兼容)。
- [ ] 目录:真机飞书适配器自动发现的群含「AI中转站合伙人群」;私聊经别名登记后可达(飞书列表 API 不返回私聊,grill Q5 定案);别名改动后按名推送命中。
- [ ] 死信:`forbidden`/chat 级 `not_found` 单次即标 dead、跳过+结构化日志;成功一次自愈(grill Q3 定案,Hermes 原味错误分类制)。
- [ ] 真机冒烟对象(grill Q6 定案):飞书「AI中转站合伙人群」+ Telegram 主人与机器人的私聊。
- [ ] 全量测试绿:push 层新旧单测 + 桌面协议测试不回归。
- [ ] W1 收口后回填 W2 开工条件;W2 不自动接力,等主人指令排期(grill Q7 定案)。

## Grill 决议记录(2026-10-03,round 1 终,无第二轮)

- **Q1 集成方式 = 蓝本移植**(逐字 vendor 否决;spec 红线不动)
- **Q2 targets 同平台约束**:元素平台前缀须与条目通道一致,加载期即拒;跨平台 = 多条 push 条目;targets 在场时 legacy `target` 可省
- **Q3 死信 = Hermes 原味**:错误分类制(forbidden/chat 级 not_found 单次标 dead,瞬态不标;成功自愈;跳过+日志),无 N 阈值无配置口
- **Q4 刷新 = run 节流懒刷**(>5 分钟才刷,失败退回旧目录)+ CLI `myia channels refresh` + UI 按钮三层
- **Q5 飞书私聊/话题 = 纯别名手工登记**(不做 callback 半吊子入站记录)
- **Q6 冒烟对象 = 飞书「AI中转站合伙人群」+ TG 私聊**
- **Q7 W2(微信/企微/钉钉/ntfy)= 等主人指令,不自动接力**
- **Q8 UI = 完整写回**:新增 `push.write` sidecar 端点(照 `sources.write` 范式),选择器直写 YAML

事实核订两轮(MYIA 侧:装配点/凭据/协议/CLI/工具链;Hermes 侧:解析顺序/死信语义/目录发现/别名机制),证据与偏离注记已落各子任务档案。

## 非目标

- 双向收发 / 入站消息处理 / 会话状态机(主人明确定向出站;将来单独立项)
- 桥接或内嵌 Hermes gateway(已否决)
- W2/W3 平台的任何实装(仅登记)
