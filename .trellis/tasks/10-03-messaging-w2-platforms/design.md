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
| ntfy | HTTP 404(topic 不存在)→ not_found;403(forbidden) | 429/5xx/超时 |
| 钉钉 | errcode 310000 族(关键词/加签/IP/时间戳安全校验未通过,官方表统一 310000)→ forbidden | errcode -1/系统繁忙/超时 |
| 企微 | errcode 40001/42001 重取后仍失败、60020(不安全的访问 IP)、60021(userid 不在应用可见范围)、81013(touser 全部非法或无权限)→ forbidden;40003(无效的 UserID)/60111(UserID 不存在)/46004(指定的用户不存在)→ not_found | 45009(接口调用超过限制)/-1(系统繁忙)/超时 |

【实现期核订回改(2026-10-03)】初稿的「81004(userid 不存在)」在企微官方
全局错误码表(developer.work.weixin.qq.com/document/path/90313)中**不存在**;
userid 失效的真实错误码为 40003/60111/46004(not_found 族)+ 60021/81013
(forbidden 族),已按官方表逐码核订如上。钉钉 310000 族经官方文档
「自定义机器人发送群消息」核订:安全校验失败统一记 310000,errmsg 区分
关键词不匹配/sign not match/IP 不在白名单/timestamp 无效。分类表落地在
`myia/push/delivery.py` 的 marker 表(W2 增量段,含 `errcode=3100` 前缀匹配)。

## D4:目录与 refresh 语义

- 三家 `discover_directory()` 返回空 + `DirectoryDiscoverUnsupported` 结构化说明(「该平台无自动发现,蓝本事实;请用别名登记」);CLI `channels refresh ntfy|dingtalk|wecom` 打印该说明并以 0 退出(不是错误)。
- UI:三平台卡转实装;凭据指南文案(ntfy:自建 server 或 ntfy.sh 公共 topic;钉钉:群设置→机器人→自定义机器人→拿 webhook(+secret);企微:管理后台建自建应用→拿 corpid/secret/agentid)。

## 兼容与回滚

- 纯新增三通道文件 + schema 枚举扩展;不配即零影响。回滚 = revert 三文件与 schema hunk。
