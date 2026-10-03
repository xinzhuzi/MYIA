# AC2/AC3 真机冒烟 runbook — 10-03-messaging-telegram(主人执行)

前置(本机已查均无,须主人补):BotFather 建 bot 取 token;`~/.hermes/.env`
无 TELEGRAM 键,env/keyring/桌面数据根均无。冒烟对象仅限主人 TG 私聊。

数据根建议用一次性目录(与 feishu 冒烟同法,不碰真实库):

```bash
export SMOKE=/tmp/tg-smoke && mkdir -p $SMOKE
export TELEGRAM_BOT_TOKEN=<BotFather 的 token>   # 只在本窗口,不落盘
E=.trellis/tasks/10-03-messaging-telegram/evidence
```

## 步骤 0(可选 sanity):passive 平台上报不失败

```bash
uv run myia channels refresh telegram --db $SMOKE/myia.db; echo EXIT=$?
# 期望 EXIT=0,输出「telegram 为被动目录平台(无主动发现 API)…」
```

## 步骤 A(AC2 前半):主人发消息 → 被动目录出现该私聊

1. 先启常驻轮询(另一个终端,或加 `&`):
   ```bash
   uv run myia run $E/smoke-accumulate.yaml --loop --db $SMOKE/myia.db
   ```
   路由永不命中,只跑 getUpdates 轮询(默认 30s 一轮)。
2. 主人从 TG 私聊给机器人发任意一条消息(首次须先 /start)。
3. 等 ≤30s 后查目录:
   ```bash
   uv run myia channels list --db $SMOKE/myia.db; echo EXIT=$?
   ```
   期望:`[telegram] 1 个可达对象:<主人名> (@username) (<纯数字 chat_id>) type=dm`。
   抄下数字 chat_id → 填进 `smoke-targets.yaml` 的 `<CHAT_ID>`。Ctrl-C 结束轮询。
   (同 token 只允许一个轮询方:轮询期间别同时跑桌面 serve 的 TG 轮询,409。)

## 步骤 B(AC2 后半):按 chat_id 定向推送收到

```bash
uv run myia run $E/smoke-targets.yaml --db $SMOKE/targets.db --json; echo EXIT=$?
```
期望 EXIT=0,report ok=True items=1,零死信;TG 私聊收到 3 条 spaceflight
标题消息(4096 分段对每目标各自生效)。目视确认由主人完成。

负对照(可选,证明确经定向解析):targets 改 `["telegram:NoSuchChatXYZ"]`
→ 期望 exit 3,`[target_unresolved]`(非数字非 @username 走目录解析,目录无此名)。

## 步骤 C(AC3):旧配置(无 targets)行为不变

```bash
TELEGRAM_CHAT_ID=<同一数字 chat_id> \
  uv run myia run $E/smoke-legacy.yaml --db $SMOKE/legacy.db --json; echo EXIT=$?
```
期望 EXIT=0,ok=True——legacy `target: env:TELEGRAM_CHAT_ID` 回退路零变化。

## 收尾

- 证据按 feishu 同款落 `smoke-log.txt`(命令+退出码)+ `smoke-summary.md`,
  PRD 三项勾选交验收(与 4fc4608 惯例一致)。
- `unset TELEGRAM_BOT_TOKEN`;`rm -rf $SMOKE`。
