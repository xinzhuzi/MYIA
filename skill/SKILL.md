---
name: myia
description: Turn a natural-language intelligence request into a MYIA category plugin and run it. Use when the user asks to monitor, collect, classify, or be pushed any information category (stocks, deals, AI news, credentials, GPU prices, custom) — read the schema quick reference below, generate the 12-section YAML, validate with `myia test`, run with `myia run`, and self-repair from `myia doctor --json` output. Generic standard, not tied to any single agent framework.
---

# MYIA Agent Skill — AI 写源速查

MYIA 是一个配置驱动的情报流水线:`fetch → classify → dedup → analyze → enrich → push`。
一个情报品类 = 一份 YAML。用户说需求,agent 做其余:读规范 → 写 YAML → 试抓验证 → 正式运行 → 自诊断修复。
本文件是自包含速查:不依赖仓库其他文档也能写出合法 YAML(详细版见仓库
`docs/write-a-plugin.md`,与本文互相引用、由一致性测试 `tests/test_skill_doc.py` 锁住不漂移)。

安装:`myia skill install` 一条命令把本文件装进 agent 的技能目录(各平台目标与开关见文末 §7);内容是通用标准,不专门适配任何单一 agent 框架。

## 总工作流(先记这个)

```
用户需求
  → myia init --json           # 拿结构化信息清单(要收集什么、缺省是什么)
  → 写 <id>.yaml(放到 plugins/ 下,对照 §2 速查表)
  → myia test plugins/<id>.yaml --json    # 逐源试抓,核对字段与去重键(不入库不推送)
  → myia run plugins/<id>.yaml --dry-run --json   # 全链演练(不推送)
  → myia run plugins/<id>.yaml            # 正式跑一次;长期使用再加 --loop 常驻调度
  → myia doctor --json         # findings 清零后收工(见 §5 自诊断)
```

所有命令的 `--json` 输出是**恰好一份 JSON 文档**(stdout),日志走 stderr——
那是 agent 的行动依据;`stdout` 推送通道的卡片行在 `--json` 下同样改写
stderr(整份 stdout 恒可 `json.load`)。退出码:`0` 成功 / `1` 配置或用法错误 / `2` 采集全部失败 / `3` 部分失败。

## 1. 硬规则(违反任何一条,YAML 会被启动即拒载,退出码 1)

1. **凭据禁明文**:凭据类键(键名含 `cookie` / `authorization` / `token` / `secret` /
   `password` / `apikey` / `session` 子串,大小写与连字符不敏感)的值只允许
   `env:VAR` 或 `keychain:myia/<scope>/<name>` 引用;明文 = 拒载。
2. **永不标题指纹**:`dedup.key` 禁用 `{title}`,只用 URL 或字段组合键。
3. **未知字段 fail-fast**:除 `sources[]` 开放源级扩展参数外,拼错字段名即拒载
   (错误带字段路径);与已知字段接近的未知键按拼写错误报。
4. **缺省值完备**:每个字段都有缺省值(见 §2 表格),只写必填项也能跑。
5. **`engine: auto` 降级链顺序固定**:L1 `direct_api` → L2 `static_html` → L3
   `crawl4ai` → `firecrawl`(L3 的云端替代后端)→ L4 `scrapling` → L5
   `stealth_browser` → L6 `llm_browser`(链尾,烧 token 只做兜底);选中结果回写
   SQLite `engine_hints`,**永不回写用户 YAML**。
6. **推送语义分层**:`push[].route` 管「推不推」(按 score/字段分级),
   AM/PM 槽位管「发没发过」(防重发),两层正交。
7. **YAML 必须 UTF-8**;顶层必须是映射;重复键拒载。

## 2. 插件规范速查(12 节 schema,与 `src/myia/schema.py` 逐字段一致)

「12 节」计数:根节 11 个字段 + `push[].route` 单独算一节。字段/缺省值由
`tests/test_skill_doc.py` 对照 pydantic 模型逐项校验。

### 2.1 枚举词表(与 schema.py 常量逐值一致)

| schema 常量 | 取值 |
|---|---|
| `ENGINES` | `auto` `direct_api` `static_html` `crawl4ai` `firecrawl` `scrapling` `stealth_browser` `llm_browser` |
| `PAGINATION_MODES` | `template` `selector` `scroll` |
| `EXTRACT_TYPES` | `list` `item` `json_path` `rss` |
| `BACKOFF_POLICIES` | `exponential` `linear` `none` |
| `PUSH_CHANNELS` | `feishu_card` `telegram` `ntfy` `dingtalk` `wecom` `webhook` `stdout` |
| `ROUTE_MODES` | `immediate` `digest` `archive` |
| `ENRICH_SCORES` | `value` `relevance` `credibility` |
| `VACUUM_CADENCES` | `daily` `weekly` `monthly` `never` |
| `CREDENTIAL_KEY_SUFFIXES` | `cookie` `authorization` `token` `secret` `password` `passwd` `apikey` `session` |

### 2.2 根节(id/name/schedule/timezone/sources/watchlist/classify/dedup/enrich/push/storage)(CategoryConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `id` | `必填` | 品类标识,`[a-z0-9][a-z0-9_-]{0,63}`;用于存储与 CLI 输出 |
| `name` | `必填` | 品类显示名(1-64 字符) |
| `schedule` | `必填` | 5 段 cron 表达式,如 `"0 9,21 * * *"` |
| `timezone` | `null` | IANA 时区名(如 `Asia/Shanghai`);缺省跟随系统时区 |
| `sources` | `必填` | 采集源列表,至少 1 个(语义见下) |
| `watchlist` | `见 watchlist 节` | 相关性画像(关键词加权/静默) |
| `classify` | `见 classify 节` | 第一层漏斗(零 token 关键词粗筛) |
| `dedup` | `见 dedup 节` | 去重键模板 |
| `enrich` | `见 enrich 节` | 第二层漏斗(LLM 精评) |
| `push` | `[]` | 推送通道列表;留空 = 只采集推送零输出,本地验证用 `stdout` 通道 |
| `storage` | `见 storage 节` | 数据生命周期 |

### 2.3 sources[](SourceConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `name` | `必填` | 源名(1-64 字符,插件内唯一,doctor/test 按它定位) |
| `engine` | `auto` | 引擎或 `auto`(取值见枚举表;全部引擎已实装,L4-L6 的依赖/前提见 §4.1) |
| `url` | `必填` | http(s) 地址;支持 `{placeholder}` 模板(翻页 `{page}`、扇出 `{symbol}`) |
| `method` | `GET` | `GET` 或 `POST`;POST 必须配 `post_body`,GET 禁止 `post_body` |
| `post_body` | `null` | POST 表单/JSON 体(映射);其中凭据类键同样禁明文 |
| `headers` | `{}` | 请求头;凭据类键的值必须是 `env:`/`keychain:` 引用;不要伪装浏览器 UA(有源对 Chrome UA 回 429、对诚实的 MYIA 产品 UA 回 200) |
| `pagination` | `null` | 翻页配置(见 pagination 节);`template` 模式要求 url 含 `{page}` |
| `extract` | `null` | 字段提取(见 extract 节);留空时 L3+ 引擎自动结构化兜底 |
| `rate_limit` | `见 rate_limit 节` | 礼貌限速(限速在引擎层统一执行) |
| `proxy` | `direct` | `direct` / `pool:<名称>` / `residential:<区域>`;pool 需全局配置 `--config` |
| `retry` | `3` | 瞬时错误重试预算(0-10) |

源级扩展参数:未知键(如 `symbols: [NVDA, AAPL]`)原样传给引擎——URL 里的
`{symbol}` 占位符按列表逐值扇出(一值一请求);`engine_options.<引擎名>` 是
引擎旋钮命名空间(如 `engine_options.firecrawl.endpoint`)。扩展参数里的凭据类键同样禁明文。

### 2.4 sources[].pagination(PaginationConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `mode` | `template` | `template` = URL 里 `{page}` 逐页;`selector` = 跟随下一页链接;`scroll` = 引擎侧无限滚动(仅 L4 `scrapling` 实装;其余引擎结构化拒绝并沿链降级到 L4) |
| `max_pages` | `1` | 最多翻页数(1-10000);翻页遇空页/指纹未变会提前停 |
| `selector` | `null` | `mode: selector` 时的下一页链接选择器(该模式必填) |

### 2.5 sources[].extract(ExtractConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `type` | `必填` | `list` = HTML 列表项;`item` = 单页;`json_path` = JSON API;`rss` = RSS/Atom feed(feedparser 条目映射,只挂 `static_html` 文本通路;`direct_api` 保持 JSON-only,配即拒) |
| `item` | `null` | `type: list` 时每条目的容器 CSS 选择器(该类型必填;其余类型禁写) |
| `url_template` | `null` | 条目 URL 渲染模板,`{field}` 纯占位(与 `dedup.key` 同款语法,至少一个占位符,且**必须在 `fields` 字段名内——装载期交叉校验,拼错即拒**),提取出口逐条渲染并填入 `url`;仅 `list`/`json_path` 可配(`item` 单页源条目 url 即请求 URL,`rss` 条目 url 由 `fields.url`←`entry.link` 映射,配即拒);运行期单条目占位缺「值」→ url 置空串、该条目被管线按 `invalid_item` 拒掉(不入库);与 `fields` 里的 `url` 二选一,**都有 = `url` 字段胜出、模板静默不用** |
| `fields` | `必填` | 字段名 → 选择器/JSONPath 的映射,至少 1 个;`list`/`json_path` 类型**必须含 `url` 或配 `url_template`**(二选一;去重键依赖 URL);`rss` 类型**值必须是 feedparser entry 属性白名单 `title`/`link`/`published`/`updated`/`summary`/`author` 之一**(键=归一字段名,如 `url: link`;拼错装载即拒),且**必须含 `url`**(映射 `link`) |

选择器语法:L1/L2 用 CSS(item 内相对选择器,`a@href` 取属性,相对 URL 自动按页面地址补全);
`json_path` 用 `$` 路径(`$.chart.result[0].meta.price`、`$[*].keyword` 通配)。
响应没有页面 URL 只有 slug/appid 的 API(Epic/Steam 形态)用 `url_template` 渲染条目链接
(官方 `plugins/games.yaml` 即此写法);`json_path` 仍表达不了「条目 URL = 请求 URL」,
此类 API 用稳定业务字段(如 symbol)充当 `url` 字段(官方 `plugins/stocks.yaml` 即此写法)。
`rss`(10-03-news-rss)走 feedparser:fields 值=entry 属性白名单,条目缺某属性该字段
逐条省略;`summary` 是 CDATA HTML,直出模板会刷屏,要摘要先进 enrich(官方
`plugins/news.yaml` 即此写法,模板只有标题+链接+日期)。

### 2.6 sources[].rate_limit(RateLimitConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `qps` | `0.5` | 每秒请求上限(0 < qps ≤ 1000);限速是默认行为不是选项 |
| `jitter` | `0.0` | 随机抖动秒数(数字或时长字符串如 `"2s"`/`500ms`) |
| `backoff` | `exponential` | 429/5xx 退避策略:`exponential` / `linear` / `none` |
| `respect_robots` | `true` | 尊重 robots.txt;改 `false` 必须在 YAML 注释里写明理由(数据 API 而非页面爬取) |

### 2.7 watchlist(WatchlistConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `keywords` | `[]` | 关键词列表(各 1-64 字符),命中的条目在相关性评估中加权;LLM 相关性分的基线 |
| `mute` | `[]` | 静默词,命中即降权/归档 |

### 2.8 classify(ClassifyConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `builtin` | `true` | 内置七大类关键词扫描(信用卡/代理节点/代买/服务器/token/AI资讯/羊毛);**未命中条目会被丢弃**(skip 原因 `classify_unmatched`)——品类对不上七大类时改 `false` 并用自定义规则 |
| `rules` | `[]` | 自定义规则列表(见 rules 节);`builtin: false` 且无规则 = 全部放行 |

### 2.9 classify.rules[](ClassifyRuleConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `name` | `必填` | 规则名(1-64 字符) |
| `when` | `必填` | 白名单表达式,对条目字段求值(语法见 2.10);加载期 AST 解析,**永不 eval** |
| `tag` | `""` | 命中后打的标签(≤64 字符) |

### 2.10 when 表达式白名单语法(classify.rules 与 push.route 通用)

- 字面量:字符串/数字/布尔/null;名字 = 条目字段(缺字段按 null 读)
- 容器:`[a, b]` 列表;运算符:`+ - * / // % **`、比较 `== != < <= > >= in not in`(可链式)、`and` / `or` / `not`
- 函数白名单(仅位置参数):`abs` `min` `max` `round` `len` `int` `float` `str`
- 禁止:属性访问、下标、lambda、f-string、推导式、白名单外的任何调用
- 例:`"abs(change_pct) >= 3"`、`"5090 in title"`、`"category in ['freebie', 'proxy-node']"`

### 2.11 dedup(DedupConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `key` | `{url}` | 去重键模板;至少一个占位符;**`{title}` 永久禁止**;占位符必须能从 `extract.fields` 或保留字段渲染(保留字段:`{url}` `{source}` `{category}` `{scores}` `{date}` `{slot}`,`{date}`=本地日期,`{slot}`=am/pm) |

组合键示例:`"{symbol}-{date}-{slot}"`(每符号每槽位一条,次日重新开始)——
裸 `{symbol}` 是全期键,首轮之后品类会被永久静音。

### 2.12 enrich(EnrichConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `enabled` | `false` | LLM 精评开关;开启时 `base_url`/`api_key` 必须有效(构造期结构化报错 `missing_base_url`/`missing_api_key`) |
| `model` | `glm-4-flash` | 模型名(任意 OpenAI 兼容端点) |
| `scores` | `[value, relevance, credibility]` | 评分维度(各 0-10),回填为条目 `score` 供 route 使用 |
| `batch` | `20` | 批量评分条数(1-1000) |
| `cache` | `true` | 按 URL 缓存评分,同一 URL 永不打两次分 |
| `budget_per_run` | `50000` | 单次 run 的 token 预算护栏;耗尽自动降级纯关键词粗筛并有 WARNING |
| `base_url` | `null` | OpenAI 兼容端点,**只能是 `env:`/`keychain:` 引用**(MYIA 无内置端点),如 `env:MYIA_LLM_BASE_URL` |
| `api_key` | `null` | API key,同上,如 `env:MYIA_LLM_KEY`(MYIA 无默认 key) |

### 2.13 push[](PushConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `channel` | `必填` | `feishu_card` / `telegram` / `ntfy` / `dingtalk` / `wecom` / `webhook` / `stdout` |
| `target` | `null` | 推送目标,只能是 `env:`/`keychain:` 引用;`stdout` 禁止配置;其余通道必填(配 `targets` 的通道可省) |
| `targets` | `[]` | 定向推送对象列表,元素 `platform:名称或id`(如 `feishu:AI中转站合伙人群`,自动去重保序);仅寻址通道 `feishu_card`/`telegram`/`ntfy`/`dingtalk`/`wecom` 支持(webhook/stdout 配即拒),同平台约束:平台前缀须与本条目通道一致,跨平台写多条 push;在场时 `target` 可省;优先级:规则级 `targets` > 通道级 `targets` > legacy `target` |
| `route` | `[]` | 阈值路由(见 route 节);留空 = 七大类缺省映射(羊毛/节点/代买 → immediate,其余 → digest) |
| `template` | `null` | Jinja2 卡片模板(沙箱渲染,未知变量报错);省略用通道内置版式 |
| `timeout` | `10.0` | 发送超时秒数(**仅 `webhook` 生效**,其他通道配置即拒) |
| `retries` | `2` | 发送重试次数(仅 `webhook`) |
| `retry_backoff_seconds` | `1.0` | 发送重试退避秒数(仅 `webhook`) |
| `ntfy_token` | `null` | ntfy 可选鉴权 token 引用(值 = Bearer 或 `user:pass`);省略且 `env:NTFY_TOKEN` 未设 = 无鉴权(仅 `ntfy` 可配) |
| `dingtalk_secret` | `null` | 钉钉可选加签密钥引用(配即 HMAC-SHA256 加签;省略 = 裸 webhook)(仅 `dingtalk` 可配) |
| `wecom_corpid` | `null` | 企微自建应用 corpid 引用;省略走 `env:WECOM_CORPID`(仅 `wecom` 可配) |
| `wecom_corpsecret` | `null` | 企微自建应用 secret 引用;省略走 `env:WECOM_CORPSECRET`(仅 `wecom` 可配) |
| `wecom_agentid` | `null` | 企微自建应用 AgentId 引用(数值串);省略走 `env:WECOM_AGENTID`(仅 `wecom` 可配) |

各通道凭据约定:`feishu_card` 的 target = 收件/群 ID(如 `env:FEISHU_CHAT_ID`),
机器人 token 从 `env:FEISHU_BOT_TOKEN` 读;`telegram` 的 target = chat id
(`env:TELEGRAM_CHAT_ID`),token 从 `env:TELEGRAM_BOT_TOKEN` 读;`ntfy` 的
target = `{server}/{topic}` 整串引用(如 `env:NTFY_TARGET`,定向写
`ntfy:<topic>` 免配 target、公共 ntfy.sh 兜底),可选鉴权 token 从
`env:NTFY_TOKEN` 读;`dingtalk` 的 target = 自定义机器人 webhook URL
(`env:DINGTALK_WEBHOOK_URL`,定向写 `dingtalk:<完整 webhook URL>`),
可选加签密钥配 `dingtalk_secret`;`wecom` 的 target = touser userid
(`env:WECOM_TUSER`,定向写 `wecom:<userid>`),corpid/corpsecret/agentid
从 `env:WECOM_CORPID` / `env:WECOM_CORPSECRET` / `env:WECOM_AGENTID` 读;
`webhook` 的 target = 端点 URL 引用(如 `env:MYIA_WEBHOOK_URL`);`stdout`
零凭据,本地验证首选。ntfy/钉钉/企微三平台无目录自动发现(蓝本事实),
`targets` 走直达 id 或别名手工登记(channel_aliases.json)。

模板上下文:`items`(条目列表,字段来自 extract)、`date`、`slot`、`category`、`count`。

### 2.14 push[].route[](RouteRuleConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `when` | `必填` | 阈值表达式(语法同 2.10),按声明顺序求值,**首条命中生效**;引用 `score` 的规则在精评分回填前自动休眠(v0.1 无 score 时跳过) |
| `mode` | `必填` | `immediate`(立即推)/ `digest`(进 AM/PM 摘要)/ `archive`(只归档) |
| `targets` | `[]` | 规则级定向推送对象,元素 `platform:名称或id` 同 2.13;命中该规则时覆盖通道级推送对象(优先级:规则级 > 通道级 > legacy `target`);同平台约束同 2.13 |

常规三档写法:`score >= 8` → immediate;`score >= 5` → digest;`score < 5` → archive。
有 score 但无任何规则命中 → 保守 digest;完全没配 route 且有 score → immediate。

### 2.15 storage(StorageConfig)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `retention` | `90d` | 保留期 `<n>d` / `<n>w`,过期条目自动清理 |
| `vacuum` | `monthly` | SQLite VACUUM 周期:`daily` / `weekly` / `monthly` / `never` |

### 2.16 凭据引用规则(安全底线)

- 只允许两种写法:`env:VAR_NAME`(运行时读环境变量)或
  `keychain:myia/<scope>/<name>`(读系统钥匙链,macOS Keychain / Windows DPAPI;
  **名空间必须规范** `myia/<scope>/<name>`,扁平旧名 `keychain:foo` 在解析期被拒)。
- 写入钥匙链:`myia secret set myia/<scope>/<name>`(值走 stdin 管道或安全输入,
  **不要**用 `--value` 传——会落 shell history 与进程列表);`myia secret list`
  只列名字;`myia secret delete <name>` 删除。
- 可带认证 scheme 前缀:`Authorization: "Bearer env:AIPOCKET_TOKEN"`。
- 明文凭据 = 启动即拒载(退出码 1,错误带字段路径);凭据值永不回显、永不落日志
  (引用名可以出现,展开后的值禁止)。

## 3. `myia init`:生成 YAML 前先拿信息清单

`myia init --json` 输出恰好一份 JSON(恒定,不问交互问题),四块内容:

- `required_inputs`:必须向用户收集的信息——品类 id/name、schedule/timezone、
  每个源的 name/url/引擎/extract 方式(含示例)
- `optional_inputs`:可省略的节及其**缺省值**(watchlist/classify/dedup/enrich/push/storage)
- `rules`:五条硬规则(凭据禁明文、永不标题指纹、未知字段 fail-fast、auto 降级链、推送语义分层)
- `next_steps`:推荐的后续命令序列(与本文总工作流一致)

把它当问卷:没收集到的可选项按 `default` 补全,直接生成 12 节 YAML。

## 4. 写源工作流:需求 → YAML → myia test → run

### 4.1 判定数据在哪(引擎选择经验,L1-L6)

| 层 | 引擎 | 什么时候用 | 代价/前提 |
|---|---|---|---|
| L1 | `direct_api` | 数据有公开 JSON/REST API(行情、发版、社区 REST) | 最快最省;`extract.type: json_path`;先 curl 确认返回结构再写 fields |
| L2 | `static_html` | 服务端渲染 HTML(论坛列表、新闻页、Discourse `/latest`);RSS/Atom feed | 零依赖;`extract.type: list` + CSS 选择器(feed 用 `type: rss`,CSS 的 `<link>` void 元素拿不到条目 url) |
| L3 | `crawl4ai` | JS 渲染页面,源码里看不到数据 | 可选依赖 `shishi[crawl4ai]`,未装时报 `dependency_missing` 并继续降级;无 `extract` 时自动结构化兜底 |
| L3' | `firecrawl` | crawl4ai 的云端替代后端 | 需 endpoint+key(`MYIA_FIRECRAWL_URL` / `MYIA_FIRECRAWL_API_KEY` 环境变量,或 `engine_options.firecrawl.endpoint/api_key`,值必须是 `env:`/`keychain:` 引用);未配置该层失败并继续降级 |
| L4 | `scrapling` | 基础盾/改版频繁源:自适应选择器自愈 + 隐身指纹 + `pagination.mode: scroll` 无限滚动 | 可选依赖 `shishi[scrapling]`,未装时报 `dependency_missing` 并继续降级;企业级风控(手机验证码/真人审核)零尝试并结构化报错,明确不支持 |
| L5 | `stealth_browser` | 反检测真浏览器:登录墙(cookie 注入,凭据走 `keychain:`)/ 基础验证码 | 需 invisible_playwright_mcp 服务(`engine_options.stealth_browser`,未启动报 `mcp_server_missing`);手机验证码/真人审核零尝试,报 `captcha_*` 结构化错误,不绕过 |
| L6 | `llm_browser` | LLM 驱动浏览器(skyvern 自然语言指挥),多步交互/复杂表单死源的最后手段 | 需 skyvern endpoint+key(`engine_options.llm_browser.endpoint/api_key`,值必须是 `env:`/`keychain:` 引用);硬护栏:单源白名单(仅 `engine: llm_browser` 显式指定或 `auto` 链尾触达)+ 每 run 次数/预算熔断 |

经验法则:先看页面源码——搜得到数据写 L2,搜不到找 API 走 L1,都 JS 化才 L3;
拿不准就 `engine: auto`,用 `myia test --json` 看实际选中引擎(`engine` 字段)。
不要给 API 源写 CSS 选择器,不要给 SSR 页面写 json_path。

### 4.2 生成 YAML

对照 §2 速查表写 `plugins/<id>.yaml`。两个可载模板(均通过 `load_category` 验证):

完整示例(L1 API + feishu 推送,展示大部分节):

```yaml
id: repo-releases
name: 仓库发版监控
schedule: "0 9,21 * * *"
timezone: Asia/Shanghai
sources:
  - name: releases-api          # L1:公开 JSON API,分页走 {page}
    engine: direct_api
    url: "https://api.example.com/v1/releases?page={page}"
    method: GET
    pagination:
      mode: template
      max_pages: 3
    extract:
      type: json_path
      fields:
        title: "$[*].name"
        url: "$[*].html_url"    # url 字段必填(去重键依赖它)
        tag: "$[*].tag_name"
    rate_limit:
      qps: 0.5
      jitter: "1s"
      backoff: exponential
      respect_robots: true
    proxy: direct
    retry: 3
watchlist:
  keywords: [LTS, security, release]
  mute: [广告]
classify:                        # 发版条目对不上七大类 → 关内置扫描
  builtin: false
  rules:
    - name: 安全更新
      when: "'security' in title"
      tag: security
dedup:
  key: "{url}"
enrich:
  enabled: false                 # 不启用 LLM 精评就保持 false,零配置
  model: glm-4-flash
  scores: [value, relevance, credibility]
  batch: 20
  cache: true
  budget_per_run: 50000
push:
  - channel: feishu_card
    target: env:FEISHU_CHAT_ID   # 凭据位只写引用;token 从 env:FEISHU_BOT_TOKEN 读
    route:
      - when: "score >= 8"       # 精评分回填前此规则休眠
        mode: immediate
      - when: "score >= 5"
        mode: digest
      - when: "score < 5"
        mode: archive
    template: |
      **仓库发版 · {{ date }}**
      {% for item in items %}
      - [{{ item.title }}]({{ item.url }}) {{ item.tag }}
      {% endfor %}
storage:
  retention: 90d
  vacuum: monthly
```

最小示例(L2 静态页 + stdout,零凭据,5 分钟跑通):

```yaml
id: demo-min
name: 最小演示
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
classify:
  builtin: false                 # 演示条目对不上七大类,关掉避免全被丢弃
push:
  - channel: stdout              # 零凭据本地验证;其余字段全省用缺省
```

### 4.3 验证与运行

```bash
myia test plugins/<id>.yaml --json          # 逐源试抓(不入库不推送,每源默认 120s 超时)
myia run plugins/<id>.yaml --dry-run --json # 全链演练,不推送无副作用
myia run plugins/<id>.yaml                  # 正式跑一次
myia run plugins/<id>.yaml --loop           # 常驻:按 schedule+timezone 自动触发
```

`myia test --json` 逐源看:`ok`(引擎链是否拿到数据)、`engine`(实际选中引擎)、
`items[].fields`(提取字段预览)、`items[].dedup_key`(去重键预览,出现
`dedup_key_error` 说明模板占位符渲染不出)、`fingerprint.verdict`
(`unchanged_skip` = 内容未变属正常,线上调度会跳过;`changed_or_first_fetch` = 会正常提取;
`unknown` = 引擎链耗尽)、`failures[]`(engine / error_type / message)。

`myia run --json` 看:`stages[]`(常驻五阶段 fetch/classify/dedup/analyze/push,
各步 items_in→items_out 与 skips 原因;品类声明 `aggregate:` 时才在 push 前
条件性插入 aggregate 阶段,未声明的品类不见它)、`sources[]`(源级条目数与
skip 原因)、`push[]`(逐通道:immediate/digest/archive 分桶计数、路由
decisions 与发送 reports)。status:`success`/`partial`/`failed`
对应退出码 0/3/2。

## 5. 自诊断流程(doctor JSON → 修复动作)

```bash
myia doctor --json                    # 缺省体检 plugins/ 全部插件;也可 myia doctor <yaml> --json
```

退出码诊断完成即 0;`healthy: false` 表示存在 error 级 `findings`。
JSON 结构:`plugins[]`(loaded / load_errors / sources[].health / engine_hint /
next_fire_at / enrich)、`credentials.entries[]`(kind / name / exists / note)、
`proxy.pools[]`(带 `--config` 时)、`findings[]`(severity / scope / code / message)、`summary`。

源健康度状态机:`ok`(有产出或指纹未变的正常跳过)/ `degraded`(指纹未跳过却 0 条,
或低于近 5 次基线的 50%)/ `dead`(连续 3 轮采集失败)/ `unknown`(无运行记录)。
注意:run 级 success 不掩盖单源静默 0 条——以源级健康度为准。

findings → 修复动作对照:

| code / 情形 | 修复动作 |
|---|---|
| `credential_plaintext`(load_errors) | 把明文改成 `env:`/`keychain:` 引用(见 §2.16) |
| `unknown_field` | 对照 §2 速查表改字段名;`sources[]` 下的扩展参数若疑似已知字段拼错也会报 |
| `missing_field` / `invalid_value` 等校验错 | 按 `path` 字段路径定位,补必填项或改取值 |
| `source_dead`(连续 3 轮失败) | 用 `myia test --json` 看 `failures[]`:URL 失效换 URL;页面改版调 extract;被风控考虑 L3/firecrawl 或代理;确认无解就注释掉该源 |
| `source_degraded`(0 条/腰斩) | 页面结构疑似变化:重跑 `myia test --json`,检查 `items[].fields` 是否为空,修 extract 选择器 |
| `env_ref_missing` | 设置环境变量(如 `export FEISHU_CHAT_ID=...`) |
| `keychain_ref_missing` | `myia secret set <引用名>` 写入钥匙链 |
| `keychain_name_noncanonical` | 引用名改为 `myia/<scope>/<name>`,旧值重新 set 后改 YAML |
| `store_error` | SQLite 库损坏或 schema 版本过新:换 `--db` 路径或删除重建(会丢历史) |
| 代理类 finding(带 `--config`) | 代理池连不通是 warning;`credential_unresolved`/`invalid_proxy_url` 是 error,修全局配置 |

修复循环:改 YAML → `myia test --json` 验证提取 → `myia doctor --json` 直到
`findings` 清零 → `myia run --dry-run --json` 演练 → 正式 `run`/`--loop`。
不要让用户去读日志——agent 读 JSON、改 YAML、复验。

## 6. 自查清单(交 YAML 前逐条打勾)

1. 文件 UTF-8,顶层是映射,无重复键;放到 `plugins/<id>.yaml`。
2. `id` 匹配 `[a-z0-9][a-z0-9_-]{0,63}`;`name` 非空;`schedule` 是 5 段 cron;
   `timezone` 是 IANA 名称(或省略)。
3. `sources` 至少 1 个;每个 `url` 是 http(s);`method: POST` 配了 `post_body`,
   `GET` 没配;`pagination.mode: template` 时 url 含 `{page}`;`mode: selector`
   给了 `selector`。
4. `extract.type: list` 给了 `item`;`list`/`json_path` 的 `fields` 里有 `url`
   或配了 `extract.url_template`(二选一);完全没写 `extract` 时引擎选了 L3+
   (自动结构化)。
5. 所有凭据类键(Cookie/Authorization/Token/Secret/Password/ApiKey/Session 等,
   含子串匹配)的值都是 `env:` / `keychain:myia/<scope>/<name>` 引用;
   `push[].target` 除 `stdout` 外都已写引用;没出现任何明文凭据。
6. 除 `sources[]` 扩展参数外没有未知字段(对照 §2 表格逐节核对)。
7. `dedup.key` 至少一个占位符、不含 `{title}`、占位符都在 `extract.fields` 或
   保留字段里;想每槽位重复推送的品类用了 `{date}`/`{slot}` 组合键而不是全期裸键。
8. `enrich.enabled: true` 时 `base_url`/`api_key` 是有效 `env:`/`keychain:` 引用
   (无内置端点、无默认 key);不想配就保持 `false`。
9. `timeout`/`retries`/`retry_backoff_seconds` 只出现在 `channel: webhook`。
10. `engine` 取值都在枚举表里;选 L4-L6 前确认其依赖/外部服务前提(见 §4.1);
    `engine: auto` 是默认且安全的选择(拿不准就让链自己降级)。
11. 品类对不上七大类时 `classify.builtin: false`(否则未命中条目全被丢弃)。
12. `myia test` 通过(退出码 0 或 3,且源 `ok: true`、字段预览非空、无 `dedup_key_error`);
    `myia doctor` 的 `findings` 清零。

## 7. 本文件的安装与更新(自述)

这份速查由 `myia skill` 子命令安装,不靠手工复制。默认探测四类技能根:
`~/.claude/skills`(Claude Code)、`~/.cursor/skills`(Cursor)、
`~/.zcode/skills`(Zcode)、`~/.agents/skills`(Codex 等通用惯例),安装位置
一律是 `<技能根>/myia/SKILL.md`:

```bash
myia skill path --json                    # 源位置 + 各 agent 推荐路径与安装状态(装没装/副本还是链接/是否落后于源)
myia skill install --agent claude         # 复制安装(缺省 --agent 时探测已存在的技能根)
myia skill install --agent cursor --link  # 符号链接代替复制(源更新即跟随,无需重装)
myia skill install --path ~/my-skills/myia  # 自定义安装目录(目录名 = 技能名)
myia skill install --agent claude --force # 目标已存在时覆盖;缺省结构化拒绝(退出码 1,code=target_exists)
```

契约与修复动作:

- 纯文件操作,退出码只有 `0`(成功)/ `1`(源缺失、目标已存在未 `--force`、用法错误);
  `--json` 输出恰好一份 JSON。
- `myia skill path` 里某 agent 的 `matches_source: false` = 已装副本落后于源
  (SKILL.md 更新过),对该 agent 重跑 `myia skill install`,带上 `--agent <名>`
  与 `--force`(见上方代码块)。
- 装进 wheel 后(非源码仓库)找不到源时,设 `MYIA_SKILL_SOURCE` 指向 SKILL.md。
