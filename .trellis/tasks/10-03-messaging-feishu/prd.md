# 消息平台:飞书适配器(目录发现+定向发送)

## Goal

飞书平台接入 messaging-core 引擎:目录发现(机器人可达的全部群/私聊/话题)+ 按对象定向发送。蓝本:`~/.hermes/hermes-agent/plugins/platforms/feishu/`;上游对照与总纲见父任务。

**前置**:messaging-core 已收口(`SendContext.target`、`PLATFORMS` 注册表、`ChannelDirectory`/别名/账本契约就位);本任务在 core 之后 start。

## 现状底子

- `FeishuCardChannel` 已走 `open.feishu.cn/open-apis/im/v1/messages` + `receive_id_type=chat_id`(应用 API,非群机器人 webhook)——发送侧只差 `SendContext.target` 覆盖(翻 `supports_targeting`)。
- 缺口在目录发现:飞书开放平台 `GET /open-apis/im/v1/chats`(机器人所在群列表,分页)。

## Requirements

1. **目录发现**:`discover_directory()` 调 `im/v1/chats`(page_size 上限,循环翻页)→ `ChannelEntry(platform="feishu", chat_id, name, type: group, thread_id=None)`;私聊不在该 API 返回内——私聊/话题条目靠别名文件手工补录(Hermes 同语义:别名先行,未发现也可寻址;grill Q5 定案:纯别名手工,不做 callback 半吊子入站记录)。【偏离注记(grill 事实轮):Hermes feishu 不调任何列表 API,目录纯靠入站会话回填;MYIA 出站-only 无入站可回填,列表 API 是 MYIA 侧新增设计】
2. **鉴权**:复用 `env:FEISHU_BOT_TOKEN`(feishu_card.py:54——手工换取的 tenant_access_token,仓库无换取代码);发现与发送共用同一凭据引用;token 失效时发现报结构化错误并退回旧目录,不阻塞推送。
3. **定向发送**:`SendContext.target.chat_id` 优先于通道自带 target;卡片构造/模板渲染零改动。
4. **注册**:`PLATFORMS["feishu"] = FeishuCardChannel`。
5. **refresh 接线**(grill Q4 定案):CLI `myia channels refresh feishu`(与 secret/plugin/feedback「名词+子动词」家族同构,事实轮已核 cli.py:361-458 先例)+ run 前节流懒刷(>5 分钟,core 提供;发现失败退回旧目录+告警)。

## Acceptance Criteria

- [ ] 单测(httpx mock):翻页聚合、name/type 映射、token 失败容错、目录 merge 进 ChannelDirectory。
- [ ] 真机冒烟(主人机器,grill Q6 定案):refresh 后目录自动发现的群含「AI中转站合伙人群」;私聊 oc_9a79… 经别名登记可达(与 Hermes 目录 3 会话构成对照集);`targets: ["feishu:AI中转站合伙人群"]` 端到端收到卡片。
- [ ] 旧配置(无 targets)行为不变(回归测试)。

## 非目标

- 入站消息/回调路由变化(feishu_callback 维持现状)
- 话题(thread)定向发送的实装——目录先记 thread_id,发送侧等真实需求再开
