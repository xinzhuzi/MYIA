# AC2/AC3 真机冒烟证据总结 — 10-03-messaging-feishu

- 执行时间:2026-10-03 10:10–10:13(主人机器,Darwin arm64)
- 凭据:`~/.hermes/.env` 的 `FEISHU_APP_ID/SECRET` 现换 tenant_access_token
  (prd 需求 2:手工换取,仓库无换取代码;令牌不落盘、不回显,已删除)
- 原始输出:`smoke-log.txt`(含每步命令与退出码);冒烟配置四个 YAML 随档
- 提交基线:HEAD=b3c8084(feat(push): feishu adapter - directory discovery + targeted send)

## AC2 验收对照(prd.md:25)

| 验收点 | 结果 | 证据(smoke-log.txt) |
| --- | --- | --- |
| refresh 后目录自动发现的群含「AI中转站合伙人群」 | ✅ 唯一群即该群,oc_f38a491eae5baf19898de28107087f05,exit 0 | 步骤 1 |
| 私聊 oc_9a79… 经别名登记可达 | ✅ 别名「老板私聊」oc_9a79a91b3e48cfb44f8313dfd998f9c0,type=dm 出现在 channels list | 步骤 2 |
| `targets: ["feishu:AI中转站合伙人群"]` 端到端收到卡片 | ✅ run exit 0,push report ok=True items=1,零死信(卡片到达飞书服务端;群内目视由主人确认) | 步骤 3 |

加强对照:

- **负对照**(步骤 4):`feishu:NoSuchChatXYZ` → exit 3,`[target_unresolved]`
  错误列出全部目录候选——证明步骤 3 的投递确实经目录名解析,非静默回退。
- **别名私聊定向**(上一轮执行,同配置 smoke-dm.yaml):`targets:
  ["feishu:老板私聊"]` → ok=True,别名寻址路打通。

## AC3 旧配置回归(prd.md:26)

- 真机(步骤 5):同 YAML 去 `targets`、`target: env:FEISHU_CHAT_ID` →
  exit 0,ok=True,旧行为零变化。
- 单测:`tests/test_messaging_pipeline.py::test_no_targets_sends_without_target_context`
  等在提交 b3c8084 内随全量套件通过。

## 提交归属注记(上一轮问题 2)

b3c8084 只含本任务 7 文件(cli.py / push/__init__.py / feishu_card.py /
三份测试 / design.md),image-input(vision/ocr.py、desktop/*、
test_desktop_sidecar_protocol.py、.trellis/spec/python/index.md)与
messaging-telegram(push/directory.py、push/telegram.py、
test_messaging_telegram.py)的在途改动均**不在**该提交内——拆分已由主线
完成,后续按各自任务单独提交。
