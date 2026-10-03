# 单测门禁记录 — 10-03-messaging-telegram(2026-10-03)

实现基线:ed1276f `feat(push): telegram adapter - passive directory + targeted send`
(接手收口路线:该提交为本任务全部产物,本次会话只做验证收口,无代码改动)。

## AC1 单测(httpx mock)✅

```
$ .venv/bin/python -m pytest tests/ -q -k telegram
72 passed, 1694 deselected in 0.38s     EXIT_CODE=0
```

覆盖对照(tests/test_messaging_telegram.py,547 行随 ed1276f 入库):

| PRD 验收点 | 测试 |
| --- | --- |
| getUpdates → 目录 merge(title/username 映射、私聊与群各自 type) | TestChatNormalization 全组 + test_sink_merges_into_directory_and_persists / test_merge_refreshes_existing_and_alias_still_wins |
| 定向发送目标覆盖 | test_context_target_overrides_legacy_chat_id / test_legacy_fallback_without_target_context / test_explicit_target_reference_still_resolves |
| 分段不回归 | test_splitting_applies_to_every_targeted_chunk |
| 直达解析(数字/@username、私聊须数字 id 文案) | TestDirectRefParse + test_username_not_found_error_hints_numeric_id |
| sink 旁路(抛错不中断、不注入逐字节不变) | test_sink_error_never_breaks_polling / test_without_sink_behaviour_is_unchanged |
| 注册 + CLI passive + 管线接线 | TestRegistration / TestChannelsCliPassivePlatform / test_poller_build_injects_directory_sink |

## AC3 旧配置行为不变 ✅

```
$ .venv/bin/python -m pytest tests/ -q
1758 passed, 14 skipped in 22.20s     EXIT_CODE=0
```

(全量含 legacy 回退单测与既有 push 全家;一次中途重跑见的
test_channels_list_four_views 红为并行会话 10-03-messaging-ui 编辑
entry.py/测试文件的瞬态,复跑即绿,与本任务无关。)

## AC2 真机冒烟 ⏳ 留主人

本机无 TELEGRAM_BOT_TOKEN(env/zshrc/keyring/`~/.hermes/.env`/桌面数据根
均查,均无;`~/.hermes/.env` 只有 FEISHU 键)。冒烟对象仅限主人 TG 私聊,
须主人自 BotFather 取 token 并亲自发消息——步骤与配套 YAML 见
`smoke-runbook.md`(accumulate/targets/legacy 三份 YAML 已随档备好)。
