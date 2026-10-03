# 消息平台:微信可选桥接(出站经本机 Hermes)

## 需求源

grill round-2 Q3 定案:微信走**可选桥接插件**(整个消息层桥接此前已否决,单平台可选桥接不同)。事实依据(探查代理 2026-10-03,蓝本 `<HA>/gateway/platforms/weixin.py`):

- 个人微信唯一出站路径 = QR 登录的 iLink Bot API;**每条出站须回显对端最新 `context_token`**,而该 token 只由常驻 long-poll 在处理入站时落盘(`weixin.py:180-225,797-852,889-891`);无 token 的冷主动发送确定性失败(`weixin.py:81-88`)。
- 即:MYIA 自实现 iLink = QR 登录 + context_token 存储 + long-poll 续期 = 半个常驻服务,违背轻依赖画像。**本机 Hermes gateway 常驻且已持 token——桥接它是对的。**

## Requirements

1. **R1 桥接通道**:通道 `weixin`(出站);投递 = 调本机 Hermes 的微信发送路径。候选通道(实现期读 `hermes_cli` 定,design 留骨架):①CLI 子进程(`~/.hermes/hermes-agent/.hermes/bin/hermes …` 的 send_message 一次性路径,蓝本 `send_weixin_direct` `weixin.py:1219-1243`);②gateway 控制接口(control_socket/api_server)。选择标准:零依赖、超时可控、错误可结构化回传。
2. **R2 显式可选**:未检出本机 Hermes(可执行不存在/未配置)时,通道配置加载不报错、发送返回结构化错误 `bridge_unavailable`(code+修复指引),UI 平台卡灰态标「需本机 Hermes」——开源用户无 Hermes 则该平台不可用,文档与卡面**如实披露**,不装可用。
3. **R3 寻址**:对象 = 微信会话(蓝本 ContextTokenStore 的 account:peer 语义);别名手工登记 + 直达 peer id;无目录发现。
4. **R4 错误语义**:桥接层错误(Hermes 不在/token 过期 `-14`/session-not-ready)按 core 分类映射——session-not-ready/未配对归硬失败标 dead?**不**:这是「须先让对方发条消息」的可修复态,归瞬态+结构化指引,不标 dead;仅 forbidden/对端不存在标 dead。蓝本参照 `weixin.py:81-88,817-820,970-995`。
5. **R5 依赖红线**:桥接实现只用标准库 subprocess/httpx;不引 Hermes 的任何 Python 代码,不 vendor。

## Acceptance Criteria

- [ ] 桥接通道单测(subprocess/httpx mock):成功路径、Hermes 缺失、token 过期、session-not-ready 四态各自结构化返回。
- [ ] 无 Hermes 环境冒烟:配置加载零报错、发送得 `bridge_unavailable`、UI 灰卡披露文案。
- [ ] 真机(主人机器,Hermes 常驻):向已知 peer 定向发一条送达;对端长期未回消息的冷发得到结构化指引而非死信。
- [ ] 开源边界:README/指南一句话披露「微信通道依赖本机 Hermes」。

## 非目标

- 在 MYIA 内实现 iLink 协议/QR 登录/context_token 轮询(蓝本自实现路线已否决)
- 微信入站/双向(定向出站决议)
- W3 长尾平台

> **冲突裁定注记(2026-10-03 · 来源:零冲突收尾工作流)**
>
> messaging 域活跃(域规则 conflict)+ schema.py 在途 + 与 platforms/W2 同屏同注册面
> 须排队;等 src/myia/schema.py 净 + messaging 域线收尾(platforms/W2 先后皆可,
> 同屏后动者 rebase)后即可开工;README 披露与桥接新文件(subprocess/httpx)本身
> 零冲突。(冲突证据:域级佐证同 messaging-platforms(channel_directory.json /
> channel_aliases.json untracked 在途)+ src/myia/schema.py 在途 M)
