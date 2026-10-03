# Design:messaging-w2-platforms — 三平台适配(蓝本形态)

上游蓝本对照(证据行号以探查代理 2026-10-03 报告为准,实现期复核):

| MYIA 模块 | 蓝本 | 移植要点 |
|---|---|---|
| `myia/push/ntfy.py`(新) | `<HA>/plugins/platforms/ntfy/adapter.py:44-78,280-354` | one-shot POST、可选鉴权头、4096 截断、X-Markdown;无目录 |
| `myia/push/dingtalk.py`(新) | `<HA>/plugins/platforms/dingtalk/adapter.py:749-774` | 静态 webhook + `{"msgtype":"text"}`;加签为 MYIA 增量(标准 HMAC-SHA256,timestamp+sign query) |
| `myia/push/wecom.py`(新) | `<HA>/plugins/platforms/wecom/callback_adapter.py:48-49,93-95,196-315` | gettoken(7200s 缓存/40001/42001 刷新重试)+ message/send(text,2048B 分块,touser) |

## D1:三家都是「单目标通道」,复用 feishu 之前的模型

- 钉钉/企微/ntfy 的 target 语义与 feishu_card 同构:`env:`/`keychain:` 引用解析出一个投递端点(webhook URL / token 组合 / server+topic)。
- `SendContext.target` 覆盖逻辑照抄 feishu/telegram 的既有实现:targeting 有则覆盖、无则 legacy target。
- `PLATFORMS` 注册 + schema 同平台映射字面表同步三行。

## D2:凭据与配置形态

- **ntfy**:target = `{server}/{topic}` 整串的引用;`ntfy_token`(可选 Bearer)与 `ntfy_user/ntfy_pass`(可选 Basic)走 schema 新增可选字段或凭据引用字段——**倾向后者**(与 timeout/retries 先例不同,这里属鉴权不是传输,用 `env:` 引用字段,channel 级可选)。
- **钉钉**:target = webhook URL 引用;`dingtalk_secret` 可选(配即加签:HMAC-SHA256 over `timestamp+"\n"+secret`,base64,query 参数 `timestamp`/`sign`)。
- **企微**:`wecom_corpid`/`wecom_corpsecret`/`wecom_agentid` 三个凭据引用;token 缓存放数据根 `wecom_token_cache.json`(原子写,含 fetched_at;>7200s 或遇 40001/42001 重取)。

## D3:错误分类(对齐 core Q3 死信语义)

| 平台 | 硬失败(标 dead) | 瞬态(不标) |
|---|---|---|
| ntfy | 404(topic 不存在)/403(forbidden) | 429/5xx/超时 |
| 钉钉 | errcode 310000 系列(token 无效/机器人不存在) | errcode -1/系统繁忙/超时 |
| 企微 | errcode 40001/42001 重取后仍失败、81004(userid 不存在)、60020(forbidden) | 45009(限频)/-1/超时 |

实现期对照各平台错误码文档逐码核订,档内此表为初稿。

## D4:目录与 refresh 语义

- 三家 `discover_directory()` 返回空 + `DirectoryDiscoverUnsupported` 结构化说明(「该平台无自动发现,蓝本事实;请用别名登记」);CLI `channels refresh ntfy|dingtalk|wecom` 打印该说明并以 0 退出(不是错误)。
- UI:三平台卡转实装;凭据指南文案(ntfy:自建 server 或 ntfy.sh 公共 topic;钉钉:群设置→机器人→自定义机器人→拿 webhook(+secret);企微:管理后台建自建应用→拿 corpid/secret/agentid)。

## 兼容与回滚

- 纯新增三通道文件 + schema 枚举扩展;不配即零影响。回滚 = revert 三文件与 schema hunk。
