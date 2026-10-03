# 消息平台 W2:企微+钉钉+ntfy 三平台适配(蓝本形态,事实核订)

## 需求源

grill round-2 四决议(2026-10-03,全按推荐):W2=企微+钉钉+ntfy 打包一任务、微信移出单议(见 10-03-messaging-weixin-bridge)、文档立齐执行等主人令。事实探查(背景代理,证据 file:line 见 design)修正了两处想当然:企微蓝本**没有**群机器人 webhook 形态;钉钉纯 HTTP 形态只有静态自定义机器人(text-only、无加签)。

## 蓝本形态事实表(出站定向推送视角)

| 平台 | 蓝本形态 | MYIA 可否(纯 httpx) | 出站能力(蓝本边界) |
|---|---|---|---|
| ntfy | one-shot POST `{server}/{topic}`,可选 Bearer/Basic,4096 字符,`X-Markdown` 可选头 | ✅ 完全可以(蓝本 `adapter.py:323-354` 本身就是无常驻范例) | 纯文本/markdown 消息到 topic;无目录概念 |
| dingtalk | 静态自定义机器人 webhook(`DINGTALK_WEBHOOK_URL` + `{"msgtype":"text"}`,蓝本 `adapter.py:749-774`) | ✅ 可以 | text-only;**加签未实现(蓝本缺口)** |
| wecom | 自建应用 wecom_callback:corpid+secret 换 token(7200s 缓存,40001/42001 刷新重试)→ `cgi-bin/message/send` 纯 HTTP(蓝本 `callback_adapter.py:48,196-315`) | ✅ 出站可以 | **仅 msgtype:"text",2048B 分块,收件人只有 touser(私聊);无群、无 markdown、无卡片**。AI Bot 形态(WS)排除——出站即 WS 帧 |

**四平台零目录发现**(全仓唯一 list_channels 在 simplex;四平台全是入站回填):MYIA 的通道目录对这三家 = **别名手工登记 + 直达 id**,与 telegram 同款被动语义但更纯(连隐式积累都没有)。

## Requirements

1. **R1 ntfy 适配器**:通道 `ntfy`;target = `env:`/`keychain:` 引用 `{server}/{topic}`(或 server+topic 两段,design 定);可选 `Authorization: Bearer <token>` 或 Basic;4096 截断;`X-Markdown: true` 随模板配置;直达 `ntfy:topic名` 不经目录。
2. **R2 钉钉适配器**:通道 `dingtalk`;target = 自定义机器人 webhook URL 的凭据引用;`{"msgtype":"text"}`;**加签(HMAC-SHA256 timestamp+sign)为 MYIA 增量**——蓝本未实现,但不加签则开了加签的机器人全不可用,secret 可选配置、不配即蓝本裸 webhook 行为【偏离注记登记】。
3. **R3 企微适配器**:通道 `wecom`;凭据 corpid+corpsecret(+agentid);`gettoken` 换取 access_token(7200s 缓存、40001/42001 刷新重试,照蓝本);发送 `message/send` text、2048B 分块、`touser` 私聊寻址;**群聊/markdown 为蓝本外能力,不做**(群机器人 webhook 列为 MYIA 增量候选,默认不做,主人点头才加)。
4. **R4 目录与寻址**:三家全走「别名手工 + 直达」;`myia channels refresh` 对这三家报「该平台无自动发现(蓝本事实)」而不是假装刷新;UI 平台卡从灰卡转实装(头像/凭据指南文案按各平台官方流程写)。
5. **R5 注册与 schema**:`CHANNELS`/`PLATFORMS` 增三通道;schema `PushChannel` 枚举扩展;同平台约束映射表同步(`ntfy→ntfy` 等);死信分类按各平台错误码对齐 core 语义(design 定:ntfy 4xx、钉钉 errcode、企微 errcode)。

## Acceptance Criteria

- [ ] 三适配器单测(httpx mock):鉴权形态、分块/截断、错误分类(text 死信/瞬态)、直达与别名两条寻址路。
- [ ] 加签增量:配 secret 走加签、不配走裸 webhook,双路单测。
- [ ] 企微 token 生命周期:缓存命中、过期刷新、40001/42001 重试,单测覆盖。
- [ ] 旧配置零回归;黄金回归(push 子树)绿;UI 三卡转实装且凭据指南文案正确。
- [ ] 真机冒烟清单落档(每平台一条最小路径:ntfy 自建 topic 收一条、钉钉机器人收一条、企微应用私聊收一条)——执行等主人令。

## 非目标

- 企微 AI Bot(WS)/钉钉 Stream Mode(会话内 session_webhook)——蓝本事实决定它们不属于无常驻出站形态
- 三平台任何入站/目录自动发现(蓝本没有)
- 微信(独立任务 10-03-messaging-weixin-bridge)

> **冲突裁定注记(2026-10-03 · 来源:零冲突收尾工作流)**
>
> 双拦:schema.py / test_schema.py 在途(vision 线)+ messaging 域活跃(域规则);等
> src/myia/schema.py、tests/test_schema.py 净 + messaging 域线(channel JSON 落定、
> ui-deep-imitation 收尾)净后即可开工;三个适配器新文件(push/ntfy.py 等)本身零冲突,
> 但 implement 步骤 4-6(注册/schema/UI)与之连续,不建议拆单文件抢跑。
> (冲突证据:文件硬撞(git status 实查):src/myia/schema.py 在途 M(diff +119 行
> ImagesConfig,vision 线)、tests/test_schema.py 在途 M)
