# 消息平台 W3 伞:长尾平台登记与开工门(待续标记)

**血缘归属**:消息平台树(父档 `archive/2026-10/10-03-hermes-messaging` 波次表;因父档已归档,本任务独立成档)。主人 2026-10-03 指示:「还没有做的、需要做的要有区分,因为等别的会话做完,要继续再做」——本档就是那条「继续」的显性标记。

## 待办登记(本伞持有,不做实装)

| 待办 | 性质 | 开工门 |
|---|---|---|
| 22 家长尾平台实装(slack/discord/whatsapp_cloud/signal/line/matrix/mattermost/google_chat/teams/email/sms/irc/simplex/bluebubbles/msgraph_webhook/qqbot/yuanbao/a2a/buzz/photon/raft/homeassistant) | 分批开发 | 主人排期指令 + 并行战场安静;蓝本范式已定(有官方 HTTP 出站→照 feishu/telegram 范式;无→extras 结构化报错;仅 simplex 有 list_channels,其余全别名+直达) |
| 企微群机器人 webhook 增量候选 | 增量开发(默认不做) | 主人点头即做(W2 档 R3 登记) |
| 飞书话题(thread)定向发送 | 增量开发(目录已记 thread_id) | 真实需求出现 |
| 黄金回归治本(冻结快照) | 独立任务 `10-03-golden-frozen-snapshots`(planning,活动区) | 建议随 W3 首批一起跑 |
| 主人手工门禁(TG/企微/钉钉/ntfy 真机冒烟、消息屏视觉过目、拉 bot 入群) | manual | 见归档父档「遗留」节清单 |

## Requirements(开工时)

1. 每批 3-5 家平台一任务,照 W2 范式(事实探查先行→三件套→流水线:实现→本域双门禁→独立质检→提交)。
2. 事实探查模板同 W2:蓝本形态/纯 httpx 出站可否/目录发现有无/错误码→死信映射。
3. 需要外部守护进程的(signal 需 signal-cli、bluebubbles 需服务端)进 extras 并结构化报错,不进核心。

## Acceptance Criteria

- [ ] 每批平台:适配器+测试+UI 卡转实装+凭据指南落地,门禁双绿,真机冒烟清单留主人。
- [ ] 全量平台覆盖达 28/28 或明确豁免理由(决议外/需 extras)。

## 非目标

- 本伞自身不实装任何平台(纯登记与开工门)。
- 入站/双向(定向出站决议不变)。
