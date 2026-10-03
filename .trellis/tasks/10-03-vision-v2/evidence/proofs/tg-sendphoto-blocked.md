# 实证1: TG sendPhoto 真发 —— BLOCKED(凭据不全)
日期: 2026-10-03 22:32:29  探测人: vision-v2 实证员

## 钥匙链(security find-generic-password,-s myia 按名探 tg/telegram 相关)
$ security find-generic-password -s myia -a "__index__" -w
["myia/image/api_key", "myia/llm/base_url"]
→ 名字索引仅 myia/image/api_key(视觉云 key),无任何 tg/telegram 条目
probe myia/telegram/bot_token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/telegram/chat_id => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/telegram/token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/tg/bot_token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/push/telegram_bot_token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/push/telegram_chat_id => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/messaging/telegram_bot_token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.
probe myia/games/telegram_bot_token => security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.

## env / launchctl / shell profiles
$ env | grep -iE "telegram|feishu|lark" → 空
$ launchctl getenv TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID → 空
$ grep -iE "TELEGRAM" ~/.zshrc ~/.zshenv ~/.zprofile ~/.profile ~/.bashrc → 无命中

## MYIA_HOME 配置(真实桌面根)
$ ls "<home>/plugins" → ai-news/gpu-prices/myia-demo/stocks/wool 五 YAML,均无 telegram 通道(只 feishu_card)
## 仓库 plugins(telegram 通道声明的唯一在场)
$ grep -n "channel: telegram" plugins/games.yaml → 314:  - channel: telegram(target: env:TELEGRAM_CHAT_ID;bot token 走缺省 env:TELEGRAM_BOT_TOKEN)
→ 纯 env: 引用声明,本机两变量均未设置(games.yaml 未装入真实根)

## ~/.hermes/.env(键名清单,值不读)
$ grep -oE "^(TELEGRAM|FEISHU|LARK)[A-Z_0-9]*" ~/.hermes/.env → 仅 FEISHU_* 六键,零 TELEGRAM 键

## 既有档案佐证
.trellis/tasks/archive/2026-10/10-03-messaging-telegram/evidence/smoke-runbook.md 前置节原文:
  「前置(本机已查均无,须主人补):BotFather 建 bot 取 token;~/.hermes/.env 无 TELEGRAM 键,env/keyring/桌面数据根均无」

## 结论
BLOCKED:缺 bot token 与 chat_id 双件(机器上任何凭据态均无,连群 chat_id 也不存在,
故不涉「群打扰降级」分支)。要打通需主人:BotFather 建 bot → 设 TELEGRAM_BOT_TOKEN +
TELEGRAM_CHAT_ID(或 myia secret set myia/telegram/*)→ 重跑本实证。
