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

---

## 复验记录(2026-10-03 晚,接手收口路线第二跑)

任务已归档(基线 ed1276f 仍在 HEAD 祖先链);工作树压着 vision-v2 /
feed-ux 等并行会话在途改动(telegram.py 的在途增量是 sendPhoto 带图,
属 10-03-vision-v2,非本任务面)。本次在**当前在途树**上原命令复验:

```
$ .venv/bin/python -m pytest tests/test_messaging_telegram.py -q
45 passed in 0.09s                                        EXIT_CODE=0
$ .venv/bin/python -m pytest tests/ -q -k telegram
80 passed, 2501 deselected in 0.30s                       EXIT_CODE=0
$ .venv/bin/python -m pytest tests/ -q
2562 passed, 19 skipped in 55.86s                         EXIT_CODE=0
```

全量较上午基线(1758 passed)多出的测试为并行线新增,零红。

附加无头核验:

- runbook 步骤 0:`python -m myia.cli channels refresh telegram --db <一次性db>`
  →「telegram 为被动目录平台…」,EXIT_CODE=0(passive 分支不触网不触凭据)。
- 三份冒烟 YAML 经 `myia.schema.load_category_file` 现载加载器逐一加载通过
  (targets/target 寻址形态与现 schema 一致)。
- `smoke-runbook.md` 的 `E=` 证据路径已随归档改指
  `.trellis/tasks/archive/2026-10/10-03-messaging-telegram/evidence`(归档后
  原路径失效,主人照抄会 file-not-found——本次修正)。

AC2 仍留主人(runbook 步骤 A/B/C 不变)。

---

## 复验确认(2026-10-03 晚,接手收口第三跑 = 本次会话)

工作树较第二跑又进(vision-v2 在途增量已随 dc1cf86 收编提交;本任务面
源文件零在途改动——`git diff --stat -- src/myia/push/ src/myia/pipeline.py
src/myia/cli.py tests/test_messaging_telegram.py` 为空,实现触点逐一在位:
telegram.py supports_targeting/parse_direct_ref、telegram_feedback.py on_chat
sink、directory.py merge_entries、pipeline.py:2414 注入、cli.py passive
分支、PLATFORMS["telegram"] 注册)。原命令三跑全绿:

```
$ .venv/bin/python -m pytest tests/test_messaging_telegram.py -q
45 passed in 0.10s                                        EXIT_CODE=0
$ .venv/bin/python -m pytest tests/ -q -k telegram
80 passed, 2973 deselected in 0.47s                       EXIT_CODE=0
$ .venv/bin/python -m pytest tests/ -q
3034 passed, 19 skipped in 61.54s                         EXIT_CODE=0
```

全量较第二跑(2562 passed)再增的测试为并行线新增,零红。附加两项亦
复验通过:passive 分支 CLI(一次性 db,输出「telegram 为被动目录平台…」
EXIT_CODE=0);三份冒烟 YAML 经 `myia.schema.load_category_file` 逐一
加载通过(pushes=['telegram'])。`smoke-runbook.md` 归档路径修正经复核
在位。AC2 真机冒烟仍留主人,runbook 步骤 A/B/C 不变。
