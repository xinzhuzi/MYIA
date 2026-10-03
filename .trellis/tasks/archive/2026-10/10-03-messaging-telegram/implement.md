# Implement:messaging-telegram

前置:core 已收口、task.json 状态改 in_progress 后才动工。每步测试全绿进下一步。

## 步骤

1. **定向发送 + 直达解析 + 单测**
   - `supports_targeting = True`;`context.target.chat_id` 覆盖;`parse_direct_ref`(数字/@username)挂钩子
   - 单测:目标覆盖、legacy 回退、@username 报错文案(私聊须数字 id)
2. **poller sink + 单测**
   - `TelegramFeedbackPoller` 增 `on_chat` 回调(group/private 归一);单测用 fake updates 覆盖两类 chat、sink 抛错不中断轮询、不注入 sink 行为不变
3. **注册与桌面接线**
   - `PLATFORMS["telegram"] = TelegramChannel`;desktop serve 的 poller 实例化处注入 directory sink(接线点实现时定位,发现档缺先回改档)
   - 【实现期定位】接线点 = `pipeline._build_feedback_poller()`(run_forever 常驻模式,桌面 serve 与 CLI `--loop` 共用;无独立 desktop 实例化点)→ 注入 `on_chat=self._merge_telegram_chat`(落点 `ChannelDirectory.merge_entries("telegram", …)`,该增量合并方法为本任务新增于 directory.py);已回改 design D2
   - 【实现期发现】注册后 `myia channels refresh`(缺省全平台)会对无 `discover_directory` 的 telegram 抛 AttributeError——CLI 增被动平台分支:归入 `passive` 上报(退出码 0,不触目录桶),非失败
4. **收尾门禁**
   - `python -m pytest tests/ -q` 全量绿
   - 真机冒烟(Q6):给机器人发一条消息 → `myia channels list` 出现该私聊 → `targets: ["telegram:<chat_id>"]` 定向推送收到

## 验证命令

```bash
python -m pytest tests/ -q
python -m pytest tests/ -q -k telegram
```

## 回滚点

- 步骤 1/2 分别落在 telegram.py / telegram_feedback.py,独立 revert;步骤 3 是接线 hunk
