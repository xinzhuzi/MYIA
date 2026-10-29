# Schema 参考

> 一个情报品类 = 一份 YAML。本文逐节给出字段、取值与缺省值,与
> `src/myssia/schema.py` 逐字段一致(由 `tests/test_docs.py` /
> `tests/test_skill_doc.py` 锁定);文中全部 `yaml` 代码块都是完整可载的品类
> 配置,复制即用。教程向的写法指南见[插件开发指南](write-a-plugin.md)。

## 十二节总览

| # | 节 | 语义 |
|---|---|---|
| 1 | `id` | 品类标识,`[a-z0-9][a-z0-9_-]{0,63}`,用于存储与 CLI 输出。 |
| 2 | `name` | 品类显示名(1-64 字符)。 |
| 3 | `schedule` | 5 段 cron 表达式,如 `"0 9,21 * * *"`。 |
| 4 | `timezone` | IANA 时区名(如 `Asia/Shanghai`);缺省跟随系统时区。 |
| 5 | `sources` | 采集源列表(至少 1 个),细节见 [sources](#sources)。 |
| 6 | `watchlist` | 相关性画像:关键词加权与静默,LLM 相关性分的基线。 |
| 7 | `classify` | 第一层漏斗:内置七大类关键词扫描(未命中即丢弃)与自定义规则,零 token。 |
| 8 | `dedup` | 去重键模板;组合键或 `{url}`,永不标题指纹。 |
| 9 | `enrich` | 第二层漏斗:LLM 精评,批量/缓存/预算护栏。 |
| 10 | `push` | 推送通道列表;`route` 子节做阈值分级路由。 |
| 11 | `push[].route` | 阈值路由:首条命中生效;`score` 回填前引用它的规则自动休眠。 |
| 12 | `storage` | 数据生命周期:保留期与 VACUUM 周期。 |

除这 12 节外,还有六个**可选 sidecar 节**(不改变 12 节公开契约,由装载入口
单独校验):`plugin:`(场景插件双模式,v0.3)、`baseline:`(趋势基线,v0.4)、
`aggregate:`(事件聚合,v0.4)、`images:`(看图环,10-03)、`timeouts:`(阶段
超时帽,10-12)、`value_gate:`(首跑静默与变化重推,2026-10-18),见
[sidecar 节](#sidecar-节v03v04)。

## 词汇表(枚举,与 schema.py 常量逐值一致)

| schema 常量 | 取值 |
|---|---|
| `ENGINES` | `auto` `direct_api` `static_html` `crawl4ai` `firecrawl` `scrapling` `stealth_browser` `llm_browser` `credhunter` `scraperapi` `zenrows` `reddit` `urlwatch` `searxng` `prompt` `store_report` `telegram` `tg_web` `wechat_local` `feishu` `vendor_market` |
| `PAGINATION_MODES` | `template` `selector` `scroll` |
| `EXTRACT_TYPES` | `list` `item` `json_path` `rss` |
| `BACKOFF_POLICIES` | `exponential` `linear` `none` |
| `PUSH_CHANNELS` | `feishu_card` `telegram` `ntfy` `dingtalk` `wecom` `weixin` `weixin-local` `webhook` `stdout` `bark` `apprise` `slack` `discord` `whatsapp_cloud` `line` `qqbot` `google_chat` `teams` `msgraph_webhook` `matrix` `mattermost` `irc` `simplex` `signal` `bluebubbles` `email` `sms` `homeassistant` `a2a` `yuanbao` `buzz` `photon` `raft` `macos_notify` |
| `ROUTE_MODES` | `immediate` `digest` `archive` |
| `ENRICH_SCORES` | `value` `relevance` `credibility` |
| `VACUUM_CADENCES` | `daily` `weekly` `monthly` `never` |
| `BASELINE_WINDOWS` | `day` `week` |
| `REQUIRES_TOKENS` | (空,插件不声明宿主能力) |
| `CREDENTIAL_KEY_SUFFIXES` | `cookie` `authorization` `token` `secret` `password` `passwd` `apikey` `session` |

## 凭据引用语法

- 只允许两种写法:`env:VAR_NAME`(运行时读环境变量)或
  `keychain:myia/<scope>/<name>`(系统钥匙链:macOS Keychain / Windows
  DPAPI;名空间必须规范,扁平旧名解析期被拒)。
- 可带认证 scheme 前缀:`Authorization: "Bearer env:AIPOCKET_TOKEN"`。
- 凭据类键(键名含词表 `CREDENTIAL_KEY_SUFFIXES` 子串,大小写/连字符不敏感)
  的值出现明文 → 加载期拒载(错误码 `credential_plaintext`)。该规则覆盖
  `sources[].headers`、`post_body`、源级扩展参数——整份 YAML 文档,不止头部。
- 凭据值永不回显、永不落日志;`myssia secret set myia/<scope>/<name>` 写入,
  值走 stdin 管道或安全输入。
- `enrich.base_url` / `enrich.api_key` / `push[].target` 必须是**纯**引用
  (不允许 scheme 前缀)。

## when 表达式白名单(classify.rules 与 push.route 通用)

- 字面量:字符串/数字/布尔/null;名字 = 条目字段(缺字段按 null 读)。
- 容器:`[a, b]` 列表;运算符:`+ - * / // % **`、比较 `== != < <= > >= in
  not in`(可链式)、`and` / `or` / `not`。
- 函数白名单(仅位置参数):`abs` `min` `max` `round` `len` `int` `float` `str`
  `match(pattern, text)`(`re.search` 白名单形态,大小写由模式自带 `(?i)`
  内联旗标控制;量化门槛规则判例,10-24-huawei-ai-news-absorb)。
- 禁止:属性访问、下标、lambda、f-string、推导式、白名单外的任何调用。
- 加载期 AST 解析,**永不 eval** 任意代码。
- 例:`"abs(change_pct) >= 3"`、`"5090 in title"`、`"category in ['freebie', 'proxy-node']"`。

## 逐节字段

### 根节(id / name / schedule / timezone)

| 字段 | 缺省 | 语义 |
|---|---|---|
| `id` | `必填` | 品类标识,用于存储与 CLI 输出 |
| `name` | `必填` | 品类显示名(1-64 字符) |
| `schedule` | `必填` | 5 段 cron 表达式 |
| `timezone` | `null` | IANA 时区名;缺省跟随系统时区 |

### sources

| 字段 | 缺省 | 语义 |
|---|---|---|
| `name` | `必填` | 源名(1-64 字符,插件内唯一,doctor/test 按它定位) |
| `engine` | `auto` | 引擎名(见词汇表;auto 按降级链,选中回写 SQLite hints 不回写 YAML)。auto 下 L2 静态抓到 0 条时会对**首遇源**(尚无 hint)做一次 L3(crawl4ai)浏览器探测——JS 渲染空壳页由此被接住;探测零结果则锁回 L2 空页语义,每 run 至多探测 3 个源。已锁 L2 的存量源不受影响;手动重探出口 = 显式写 `engine: crawl4ai`。`credhunter` 是**链外源引擎**:不抓取 url、由进程内场景件 myssia-credhunter 就地装配 items(GitHub 工件猎取/FOFA-Shodan 曝面/本地文本分诊),不参与 auto 降级链,显式选择才生效;凭据经 `engine_options.credhunter.{github_tokens,fofa_apikey,shodan_apikey}` 引用注入,无 key 的曝面 lane 显式空态(AC6) |
| `url` | `必填` | http(s) 地址;支持 `{placeholder}` 模板(翻页 `{page}`、扇出 `{symbol}`) |
| `method` | `GET` | `GET` / `POST`;POST 必配 `post_body`,GET 禁止 |
| `post_body` | `null` | POST 表单/JSON 体(映射);凭据键同 headers 禁明文 |
| `headers` | `{}` | 请求头;凭据键的值必须是引用;不要伪装浏览器 UA(默认 UA 是诚实的 `世事/0.1 (...)`) |
| `pagination` | `null` | 翻页配置,见下表 |
| `extract` | `null` | 字段提取,见下表;留空时 L3+ 引擎自动结构化兜底;L2 `static_html` 在装了 `trafilatura` extras 且兜底开关打开时,对无规则源与规则跑空(`rss` 除外)的**文章页**做单页正文兜底(产出单条,带 `metadata.extract_provenance="trafilatura"`,正文量不足 120 字符的列表页退化形态被质量门拦下维持零条;规则命中的源永不触达)。开关优先级=**源级 `engine_options.static_html.extract_fallback: true/false` > 全局环境变量 `MYIA_EXTRACT_FALLBACK=1` > 缺省关**(源级未设时行为与全局口径逐字节一致) |
| `rate_limit` | `见 rate_limit 表` | 礼貌限速(限速在引擎层统一执行) |
| `proxy` | `direct` | `direct` / `pool:<名称>` / `residential:<区域>`;pool 需 `--config` 全局配置 |
| `retry` | `3` | 瞬时错误重试预算(0-10) |

**源级扩展参数**:未知键(如 `symbols: [NVDA, AAPL]`)原样传给引擎——URL 里的
`{symbol}` 按列表逐值扇出(一值一请求);`engine_options.<引擎名>` 是引擎
旋钮命名空间(如 `engine_options.firecrawl.endpoint`、
`engine_options.scrapling.backend`、`engine_options.stealth_browser.max_pages`、
`engine_options.static_html.extract_fallback`(trafilatura 兜底单源开关,布尔,
优先级=源级 > 全局 `MYIA_EXTRACT_FALLBACK` > 缺省关,值非布尔 fetch 期结构化拒
`invalid_engine_options`);
`engine_options.crawl4ai` 有 `timeout`(单页预算秒,缺省 60s)、`headless`
(缺省 `true`)与 `browser_options` / `run_options` 两个透传映射——分别合并进
crawl4ai 的 `BrowserConfig` / `CrawlerRunConfig`,打开其余配置面;透传映射不得
携带引擎自管键(browser 侧 `headless` / `proxy` / `proxy_config` / `headers`,
run 侧 `cache_mode` / `page_timeout`——代理解析、凭据脱敏、缓存旁路与预算护栏
均由引擎单一来源推导),冲突即 fetch 期结构化报错 `invalid_browser_options` /
`invalid_run_options`)。扩展参数里的凭据类键同样禁明文。

**源级正文追抓**(`detail_text: true`,平铺参数;配套 `detail_text_max` 缺省
`10`):对**标题级列表源**(只有标题+链接、`content` 空缺的条目)在 fetch 尾
部逐条追抓其详情页,**经源自己的引擎**(显式 `engine:` 配置;FC 云渲染源即
firecrawl 过 CF)取回 markdown 回填 `item.content`(截断 4000 字符),从
classify 词表到 push 路由全程可见——「sk- key 在帖体正文里」的福利帖由此能
过 url+key 硬门。只追**新鲜条目**(去重键未见过的;旧条目零标记跳过,防每轮
重烧云渲染额度),串行 + 每请求 ≥2s 间隔 + 每源独立预算 + 整环 240s 墙钟。
任何失败只写 `metadata.detail_text_status`(`failed:<原因>` / `empty` /
`skipped:engine`),绝不阻管线;未配置 = 整环零进入(零影响默认)。

**prompt 任务引擎**(`engine: prompt`,链外):一句话定义监控——
`engine_options.prompt` 的 `instructions`(任务指令)+ `urls`(目标清单,
缺省 `[源 url]`);引擎对每个 URL 走既有礼貌面抓正文(HTML 页 trafilatura、
XML feed 走 feedparser),喂**一轮** OpenAI 兼容 LLM(`base_url` / `api_key`
必配引用,enrich 节同契约),markdown 回答直接做条目 `content` 进 push
(模板键 `{{ item.prompt_summary }}`);条目 URL 铸 `#prompt-<日期>` 锚点,
每日新条目、同日重跑幂等。失败全部结构化(凭据/依赖/抓取/LLM 四类),
部分 URL 抓取失败降级继续。示范件 `config/ai-vendor-watch.example.yaml`,
extras 需 `myssia[trafilatura]` + `myssia[llm]`。

### 全局配置 pools(代理池,`--config`)

源的 `proxy: pool:<名称>` 引用全局配置(`--config` 注入,run/test/doctor
共用同一加载器)声明的代理池;凭据只允许 `env:` / `keychain:` 引用,明文
加载期拒载;同池多源共享同一条池通道:

```yaml
pools:
  main: "http://env:MYIA_PROXY_MAIN@proxy.example.com:8080"   # 单上游(v0.2 字符串形态)
  rotating:                                                    # 多上游池(v1.2)
    upstreams:
      - "http://env:MYIA_PROXY_A@p1.example.com:8080"
      - "socks5://keychain:myia/proxy/b@p2.example.com:1080"
    max_failures: 3          # 可选;连续失败摘除阈值,默认 3;须 ≥1
    probe_interval: 300      # 可选;半开恢复间隔(秒),默认 300;须 ≥1
```

运行语义(两种形态同一引擎):请求按声明顺序游标轮换,transport 失败即
换下一个健康上游(消耗源 retry 预算);每上游连续 transport 失败计数,达
`max_failures` 摘除,过 `probe_interval` 后由下一次流量自然触发半开试炼
(成功归队、失败再摘除;零后台探活);全部上游摘除即池熔断 —— 后续请求
**零网络快速失败**(`proxy_pool_exhausted`,降级链同 `proxy_*` 家族短路),
`myssia doctor --config` 逐池逐上游并行探测(单上游池输出仅多
`upstream_index`/`upstreams` 两个纯增字段)。**行为变化(v1.2 披露)**:
字符串(单上游)池的失败语义从「逐源各自重试」变为「3 连败摘除 → 池熔断
快速失败」—— YAML 兼容指可原样加载、请求照跑,失败路径不再逐字节等价
(死上游不再被每源反复烧穿重试预算)。

pagination:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `mode` | `template` | `template` = URL 里 `{page}` 逐页;`selector` = 跟随下一页链接;`scroll` = 无限滚动(仅 L4 `scrapling` 支持) |
| `max_pages` | `1` | 最多翻页数(1-10000);空页/指纹未变提前停 |
| `selector` | `null` | `mode: selector` 时的下一页链接选择器(该模式必填) |

extract:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `type` | `必填` | `list` = HTML 列表项;`item` = 单页;`json_path` = JSON API;`rss` = RSS/Atom feed(feedparser 条目映射) |
| `item` | `null` | `type: list` 时的条目容器 CSS 选择器(该类型必填;其余禁写) |
| `url_template` | `null` | 条目 URL 渲染模板,`{field}` 纯占位(至少一个占位符,且必须在 `fields` 字段名内——装载期交叉校验,拼错即拒),提取出口逐条渲染并填入 `url`;仅 `list`/`json_path` 可配(`item` 单页源配即拒;`rss` 条目 url 由 `fields.url`←`entry.link` 映射,配即拒);运行期单条目占位缺「值」→ url 置空串、该条目被管线按 `invalid_item` 拒掉(不入库);与 `fields` 的 `url` 二选一,都有 = `url` 字段胜出、模板静默不用 |
| `fields` | `必填` | 字段名 → 选择器/JSONPath,至少 1 个;`list`/`json_path` **必须含 `url` 或配 `url_template`**(二选一);`rss` 的**值必须是 feedparser entry 属性白名单 `title`/`link`/`published`/`updated`/`summary`/`author` 之一**(键=归一字段名,拼错装载即拒),且**必须含 `url`**(映射 `link`) |

选择器语法:L1/L2 用 CSS(条目内相对选择器,`a@href` 取属性,相对 URL 自动
补全);`json_path` 用 `$` 路径(`$.chart.result[0].meta.price`、`$[*].keyword`
通配)。响应没有页面 URL 只有 slug/appid 的 API(Epic/Steam 形态)用
`url_template` 渲染条目链接(`config/games.example.yaml` 即此写法);
`json_path` 仍表达不了「条目 URL = 请求 URL」,此类 API 用稳定业务字段充当
`url`(`config/stocks.example.yaml` 即此写法)。

`rss`(10-03-news-rss)逐字段语义:

- **映射形态**:`fields` 键=归一字段名,值=feedparser entry 属性名(白名单
  `title`/`link`/`published`/`updated`/`summary`/`author`);典型写法
  `{title: title, url: link, published: published}`(`config/news.example.yaml`)。
- **白名单**:封闭词表,拼错(如 `pubdate`)装载即拒(`invalid_rss_field`,
  path 指到 `fields.<键>`)——防运行期整源静默零产出。
- **缺属性省略**:条目缺某属性 → 该字段逐条省略(同 `json_path` 逐条目语义);
  `published` 是 RFC 822 日期串(`Fri, 02 Oct 2026 20:01:17 +0800`),模板切
  片显示。
- **url 必填**:`fields` 必须含 `url`(映射 `link`,去重键根基);`item` 选择器
  与 `url_template` 对 `rss` 均互斥(配即拒)。
- **引擎边界**:`rss` 只挂 `static_html` 文本通路;`direct_api` 保持 JSON-only
  (配 `rss` 在引擎层拒,让 `engine: auto` 沿链正确降级)。
- **CDATA 坑**:`summary` 是 CDATA 包 HTML,直出模板会刷屏——白名单保留该
  属性但官方模板只用标题+链接+日期,要摘要先进 enrich。

rate_limit:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `qps` | `0.5` | 每秒请求上限(0 < qps ≤ 1000);限速是默认行为不是选项 |
| `jitter` | `0.0` | 随机抖动秒数(数字或时长字符串 `"2s"` / `500ms`) |
| `backoff` | `exponential` | 429/5xx 退避:`exponential` / `linear` / `none` |
| `respect_robots` | `true` | 尊重 robots.txt;改 `false` 必须在 YAML 注释里写明理由(数据 API 而非页面爬取) |

### watchlist

| 字段 | 缺省 | 语义 |
|---|---|---|
| `keywords` | `[]` | 关键词列表(各 1-64 字符),命中条目在相关性评估中加权;LLM 相关性分基线,亦是 `keyword_trends` 提及量统计口径 |
| `mute` | `[]` | 静默词,命中即降权/归档 |

### classify

| 字段 | 缺省 | 语义 |
|---|---|---|
| `builtin` | `true` | 内置七大类关键词扫描(信用卡/代理节点/代买/服务器/token/AI资讯/羊毛);**未命中条目被丢弃**(skip 原因 `classify_unmatched`);品类对不上七大类时改 `false` |
| `rules` | `[]` | 自定义规则:`name`(必填)+ `when`(必填,白名单表达式)+ `tag`(≤64 字符);`builtin: false` 且无规则 = 全部放行 |

### dedup

| 字段 | 缺省 | 语义 |
|---|---|---|
| `key` | `{url}` | 去重键模板;至少一个占位符;**`{title}` 永久禁止**;占位符须能从 `extract.fields` 或保留字段渲染(保留字段:`{url}` `{source}` `{category}` `{scores}` `{date}` `{slot}`) |

组合键示例:`"{symbol}-{date}-{slot}"`(每符号每槽位一条,次日重新开始)——
裸 `{symbol}` 是全期键,首轮之后品类会被永久静音。

### enrich

| 字段 | 缺省 | 语义 |
|---|---|---|
| `enabled` | `false` | LLM 精评开关;开启时 `base_url`/`api_key` 必须有效(结构化报错 `missing_base_url`/`missing_api_key`) |
| `model` | `glm-5.3-flash` | 模型名(任意 OpenAI 兼容端点) |
| `scores` | `[value, relevance, credibility]` | 评分维度(各 0-10),回填为条目 `score` 供 route 使用 |
| `batch` | `8` | 批量评分条数(1-1000);缺省 20→8(10-29 限流案下调:失败半径换 token 效率) |
| `cache` | `true` | 按 URL 缓存评分,同一 URL 永不打两次分 |
| `budget_per_run` | `50000` | 单次 run 的 token 预算护栏;耗尽自动降级纯关键词粗筛并有 WARNING |
| `base_url` | `null` | OpenAI 兼容端点,**只能是纯 `env:`/`keychain:` 引用**(世事 无内置端点),如 `env:MYIA_LLM_BASE_URL` |
| `api_key` | `null` | API key,同上,如 `env:MYIA_LLM_KEY`(世事 无默认 key) |

真实调用还需可选依赖:`uv sync --extra llm`(未装时结构化报错
`dependency_missing` 并降级关键词粗筛)。

### push

| 字段 | 缺省 | 语义 |
|---|---|---|
| `channel` | `必填` | `feishu_card` / `telegram` / `ntfy` / `dingtalk` / `wecom` / `webhook` / `stdout` / `bark`(iOS 即时推送) / `apprise`(统一推送,需 extras `myssia[apprise]`) |
| `target` | `null` | 推送目标,只能是**纯** `env:`/`keychain:` 引用;`stdout` 禁止配置,其余通道必填 |
| `route` | `[]` | 阈值路由(见下表);留空 = 七大类缺省映射(羊毛/节点/代买 → immediate,其余 → digest) |
| `template` | `null` | Jinja2 卡片模板(沙箱渲染,语法错误加载期拒);省略用通道内置版式 |
| `timeout` | `10.0` | 发送超时秒数(**仅 `webhook` 生效**,其他通道配置即拒) |
| `retries` | `2` | 发送重试次数(仅 `webhook`) |
| `retry_backoff_seconds` | `1.0` | 发送重试退避秒数(仅 `webhook`) |
| `ntfy_token` | `null` | ntfy 可选鉴权 token 引用(值 = Bearer 或 `user:pass` → Basic);省略且 `env:NTFY_TOKEN` 未设 = 无鉴权(仅 `ntfy` 可配) |
| `dingtalk_secret` | `null` | 钉钉可选加签密钥引用(配即 HMAC-SHA256 加签;省略 = 裸 webhook)(仅 `dingtalk` 可配) |
| `wecom_corpid` / `wecom_corpsecret` / `wecom_agentid` | `null` | 企微自建应用三凭据引用;省略走缺省 env `WECOM_CORPID`/`WECOM_CORPSECRET`/`WECOM_AGENTID`(仅 `wecom` 可配) |
| `bark_endpoint` | `null` | Bark(iOS 推送)服务端端点;**非凭据**可落 YAML:留空 = 官方服务 `https://api.day.app`,自建填主机地址(如 `http://<host>:8080`);须 http(s) 形态(仅 `bark` 可配) |
| `wechat_bridge_bin` | `null` | 受管组件 wechat-bridge 的 bin 路径覆写(缺省 `~/.myia/plugins/wechat-bridge/bin/wechat-bridge`);本地路径**非凭据**,不走 env:/keychain: 引用(仅 `weixin-local` 可配) |
| `weixin_local_enabled` | `null` | 微信本机桥显式启用开关:`weixin-local` 条目必须显式 `true` 才能通过加载(缺省关——同机便捷补充,主出站口 = `weixin` 官方桥);关闭 = 通道不注册(仅 `weixin-local` 可配) |

route 规则:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `when` | `必填` | 阈值表达式,按声明顺序求值,**首条命中生效**;引用 `score` 的规则在精评分回填前自动休眠 |
| `mode` | `必填` | `immediate`(立即推)/ `digest`(进 AM/PM 摘要)/ `archive`(只归档) |

常规三档:`score >= 8` → immediate;`score >= 5` → digest;`score < 5` →
archive。有 score 但无规则命中 → 保守 digest;完全没配 route 且有 score →
immediate。模板上下文:`items`(条目列表,字段来自 extract)、`date`、
`slot`、`category`、`count`;各通道凭据约定见
[插件开发指南](write-a-plugin.md)。

### storage

| 字段 | 缺省 | 语义 |
|---|---|---|
| `retention` | `90d` | 保留期 `<n>d` / `<n>w`,过期条目自动清理 |
| `vacuum` | `monthly` | SQLite VACUUM 周期:`daily` / `weekly` / `monthly` / `never` |

## Sidecar 节(v0.3/v0.4)

四个可选顶层节,**不属于 12 节公开契约**:由 `load_category` 在装载入口单独
校验(错误路径带 `$.plugin` / `$.baseline` / `$.aggregate` / `$.images`
前缀,`null` 视为未声明),挂到 `CategoryConfig` 的同名属性。任何一个 sidecar
校验失败,整份 YAML 拒载(退出码 1)。

### plugin:场景插件双模式(v0.3)

品类依赖某个市场插件(`myssia plugin install` 安装)提供的服务时声明。
**任何插件装不上/配置坏/remote 不可达都不拦核心流水线**——降级为结构化
finding,品类照常跑(安全基线铁律)。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `id` | `必填` | 插件 id(小写字母/数字/连字符/下划线,字母数字开头,惯例 `myssia-<名称>`) |
| `requires` | `[]` | 宿主能力词表(**当前为空**——插件 docker 模式已删,`docker` 词退役,任何非空值拒载);字符串或列表皆可 |
| `modes` | `必填` | 双模式至少声明一个:`local`(原生安装命令 `install`,本机零 Docker)或 `remote`(endpoint 必填;token **必须** `keychain:myia/<scope>/<name>` 引用,`env:` 也不行)。插件不提供 docker 模式(终裁 2026-10-06) |

```yaml
id: site-watch
name: 页面变更监控
schedule: "*/15 * * * *"
timezone: Asia/Shanghai
plugin:                           # 场景插件声明(v1.1 起官方包为 remote 可选接入)
  id: myssia-monitor
  requires: []
  modes:
    remote:                       # 指向已部署实例(桌面零 Docker;local 侧只收原生安装命令 install,插件不提供 docker 模式)
      endpoint: https://my-monitor.example.com
      token: keychain:myia/monitor/token    # myssia secret set myia/monitor/token
sources:
  - name: watch-api
    engine: direct_api
    url: "https://my-monitor.example.com/api/v1/watch"
    headers:
      X-Api-Key: "keychain:myia/monitor/token"
    extract:
      type: json_path
      fields:
        title: "$[*].label"
        url: "$[*].url"
push:
  - channel: feishu_card
    target: env:FEISHU_CHAT_ID
```

### baseline:趋势基线(v0.4)

声明哪些数值字段进入逐条目历史快照,推送模板经沙箱函数消费对比文本;
`watchlist.keywords` 同时成为关键词提及量统计口径。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `enabled` | `false` | 开关;开启时 `fields` 至少 1 个 |
| `fields` | `[]` | 数值字段清单(源 extract 产出的字段名,如 `price`),按(品类,条目,字段)存历史快照 |
| `windows` | `[day, week]` | 对比窗口:`day` = vs 昨日,`week` = vs 上周 |
| `msrp` | `{}` | 可选 MSRP 对照表(公开建议零售价;键为商品名子串,值 > 0) |

模板侧:`vs_yesterday(item, 'price')` / `vs_last_week(item, 'price')` /
`vs_msrp(item)` 三个沙箱函数 + `keyword_trends` 上下文(关键词提及量周环比)。
注意契约差异:`vs_yesterday` / `vs_last_week` 的字段是入参(任意数值字段),
而 `vs_msrp(item)` **没有字段参数、钉死只读条目的 `price` 字段**——价格字段
不叫 `price`(或非数值)时 `vs_msrp` 恒为空串。
数值历史按 `2 × storage.retention` 保留,保证周环比窗口完整。

```yaml
id: gpu-prices-lite
name: 显卡行情(精简)
schedule: "0 10 * * *"
sources:
  - name: price-list
    engine: static_html
    url: "https://detail.example.com/vga/{page}.html"
    pagination:
      mode: template
      max_pages: 5
    extract:
      type: list
      item: "div.list-item"
      fields:
        title: "h3 a"
        url: "h3 a@href"
        price: "span.price-type"    # baseline.fields 依赖的数值字段
dedup:
  key: "{url}"
baseline:
  enabled: true
  fields: [price]
  windows: [day, week]
  msrp:
    "RTX 5090": 16499
    "RTX 5080": 8299
push:
  - channel: stdout
    template: |
      **显卡行情 · {{ date }}**
      {% for item in items %}
      - [{{ item.title }}]({{ item.url }}) ¥{{ item.price }} {{ vs_msrp(item) }} {{ vs_yesterday(item, 'price') }} {{ vs_last_week(item, 'price') }}
      {% endfor %}
      {% if keyword_trends %}
      **关键词提及周环比**:{% for t in keyword_trends %}{{ t.word }} {{ t.count }} 条({{ t.change_text }}) {% endfor %}
      {% endif %}
```

### aggregate:事件聚合(v0.4)

多源报道同一事件时合并为单卡推送(主条目 + 「另见 N 源」列表),在 dedup
(同 URL/组合键,正交层)之后、push 之前执行。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `enabled` | `false` | 开关 |
| `window_hours` | `24.0` | 同事件时间窗(小时,> 0):两个候选都带可解析发布时间且相差超窗 → 直接视为不同事件(零 token) |
| `similarity_threshold` | `0.6` | 标题相似度粗筛阈值(字符 shingle Jaccard,0 < x ≤ 1):达标才进 LLM 精筛候选 |

两级判重:本地零 token 粗筛圈候选 → LLM 确认(并入 enrich 的批量/缓存/预算
护栏)。**端点配置复用 `enrich:` 节**——`aggregate.enabled: true` 要求
enrich 的 `base_url`/`api_key` 有效(同样需要 `--extra llm`);预算与 enrich
合计消费,谁先到顶谁降级。开启后 `myssia run --json` 的 `stages[]` 会多出
`aggregate` 阶段。

```yaml
id: ai-news-merged
name: AI 资讯(同事件聚合)
schedule: "0 8,20 * * *"
timezone: Asia/Shanghai
sources:
  - name: feed-a
    engine: static_html
    url: "https://news-a.example.com/latest"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
watchlist:
  keywords: [LLM, agent]
dedup:
  key: "{url}"
enrich:
  enabled: true
  base_url: env:MYIA_LLM_BASE_URL
  api_key: env:MYIA_LLM_KEY
aggregate:
  enabled: true
  window_hours: 24
  similarity_threshold: 0.6
push:
  - channel: stdout
```

### images:看图与表格还原

品类级图片处理声明(看图环 sidecar 节):开启后 fetch 阶段尾部对条目携带的
图片执行「下载(SSRF 拒私网 / 魔法字节白名单 / 流式 10MB 截断 / 10s 超时)→
可选表格还原 → 本地 OCR → 可选 VL 情报向描述」,产物挂
`metadata.image_ocr` / `image_caption` / `tables` / `image_status` 等标记,
喂给 analyze/enrich 评分与推送模板。**任何失败只写标记、绝不阻断管线**;
未开启 = 整环零进入,行为与本节不存在时逐字段一致(零影响默认)。

图片 URL 的来源:extract `fields` 配 `image: img@src`(fetch 侧对 src 属性做
urljoin),或 L3+ 引擎无 `extract` 时的 markdown 同域图链接收集(跨域广告/
追踪像素不收)。源级平铺参数 `images_enabled` / `images_max_images` /
`images_min_bytes` / `images_vl` / `images_ocr_engine` / `images_detail_fetch`
/ `images_detail_max_items` 覆写品类节(`max_per_run` / `persist` / `table`
是全局语义,不开放源级覆写)。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `enabled` | `false` | 看图环总开关;关 = 整节零进入 |
| `max_images` | `3` | 每条目处理上限(张,1-10),超出静默截断 |
| `max_per_run` | `30` | 每 run 图处理总上限(VL 时长硬闸;耗尽标 `skipped:run_limit`) |
| `min_bytes` | `10240` | 小于该字节数的图视为图标/追踪像素跳过 |
| `vl` | `off` | 视觉描述通道:`off` = 只 OCR(零 VL 开销)/ `local` = 本地 OpenAI 兼容端点(vision.yaml `local` 节)/ `cloud` = 云端视觉模型(token 走 enrich 预算池) |
| `ocr_engine` | `null` | OCR 引擎覆写(`vision` / `rapidocr`);缺省按 vision.yaml 的 `ocr.engine_default` |
| `detail_fetch` | `false` | 详情页追抓:对无图条目按管线顺序追抓详情页,同域收 `<img>` 写回 `metadata.images` 后进同一识图环 |
| `detail_max_items` | `10` | 每 run 追抓条目上限(1-50;串行 + 每请求 ≥1s 间隔;源级覆写 = 该源独立预算) |
| `persist` | `false` | 通过下载关的图落盘 `MYIA_HOME/images/<sha16>.<ext>`(内容寻址),metadata 增 `image_files` / `image_ocr_lines` |
| `table` | `false` | 表格还原:对通过下载关的图跑 rapid_table 结构化,还原表落 `metadata.tables = [{markdown, rows, cols}]`(GFM,推送卡片可直接嵌表);引擎缺装/失败只写 `metadata.table_status = "table_provider_error"`,绝不阻管线。不开放源级 `images_table` 覆写 |

分工边界:`table` 管表格结构化(行列还原),`vl` 管整图语义描述,互不替代。

**表格还原的组件安装**(重依赖,不进核心):CLI 侧
`uv sync --extra table`(或 `pip install "myssia[table]"`);桌面端在
设置 → Python 环境用「表格还原」组件开关一键装入自管环境(镜像覆盖生效;
SLANET-plus 结构模型首用时从 modelscope 自动下载 6.8MB 后离线复用)。缺装时
品类照常跑——图片环其余产物不受影响,仅表格还原静默降级;
`myssia doctor` 对声明 `images.table: true` 的品类出 `table_dependency_missing`
warning 提前披露(带安装命令)。

```yaml
id: gpu-shots
name: 显卡行情截图
schedule: "0 10 * * *"
timezone: Asia/Shanghai
sources:
  - name: price-shots
    engine: static_html
    url: "https://detail.example.com/vga/"
    extract:
      type: list
      item: "div.list-item"
      fields:
        title: "h3 a"
        url: "h3 a@href"
        image: "img@src"          # 看图环的图片来源:extract 字段收 <img> 地址
images:
  enabled: true
  max_images: 2
  vl: "off"                       # YAML 的裸 off/on 会被解析成布尔,枚举值要加引号
  table: true                     # 截图里的行情表还原成 GFM,落 metadata.tables
push:
  - channel: stdout
```

### value_gate:首跑静默与变化重推

品类级价值裁决声明(第 6 个 sidecar 节):两个开关都在 `false` 缺省时整节
零影响(行为与本节不存在时逐字段一致);装载入口单独校验(错误路径统一
`$.value_gate` 前缀,`value_gate: null` 视为未声明)。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `first_run_quiet` | `false` | `true` = 新源第一次见面,先把家底记进库,不打扰你:首跑条目只入库、不进评分推送流。马上要单发的条目(route 预演命中 immediate,如羊毛/优惠类)不吞,首轮照常单发;首轮没有评分,score 型即时路由判不了,这类首跑条目进日报折叠(store_report 分区 `first_run: fold`),下轮有变化再报。只对新源/新库首跑触发;被吞条目在 `report.skips["first_run_quiet"]` 计数留痕。 |
| `repush_on_change` | `false` | `true` = 同一条目内容变了(指纹 = 标题+正文归一;列表/RSS 源无正文退化为标题变化),下个时段再报一次:同键入库新行(新行继承旧行已读/星标),日报窗口按 first_seen 自然拉到。存量老条目没有指纹,永不重推;只对开关开启后新入库的条目生效。 |

前置信号:条目 `metadata["fetch_change_reason"]`(值域 `first_fetch` /
`validators_differ` / `hash_differ`)由 static_html 引擎铸造(含 rss 提取子
模式);`engine: auto` 仅 L2 命中时带信号,降级到 firecrawl 后无标记不抑制;
prompt/urlwatch/telegram 等独立引擎不覆盖(无标记 = 不受首跑抑制影响)。

```yaml
id: ai-news
name: AI资讯
schedule: "0 8,20 * * *"
timezone: Asia/Shanghai
sources:
  - name: vendor-blog
    engine: static_html
    url: "https://example.com/blog/rss.xml"
    extract:
      type: rss
      fields: {title: title, url: link, published: published}
value_gate:
  first_run_quiet: true     # 新源首跑只入库不打扰;马上单发的(羊毛类)照常推
  repush_on_change: true    # 内容变了(标题或正文有改动)下个时段再报一次
push:
  - channel: stdout
```

装机与验证前置(真实发送严禁对生产 target 群真跑):①换装前 `myssia cron`
暂停对应品类 + 退出桌面 app(cron spawn 的 run 子进程不能跑半新半旧代码),
换装后恢复;②真发验证用临时品类配置(私聊 open_id target + 独立 url 锚),
避开采集槽位窗口,验完即删;③验证判据绑 `report.skips["first_run_quiet"]`
计数与 `metadata["fetch_change_reason"]` 字段,不绑 LLM score(评分抖动会
污染断言);④验证首跑抑制须用临时新增源或新库(存量源基线已在不触发,勿
误判开关失效)。

## 加载期错误(结构化)

装载失败抛 `LoadError`:一次报告**全部**错误,每条含字段路径(JSONPath 风格
如 `$.sources[0].rate_limit.qps`)+ 机器错误类 + 中文原因;`myssia doctor
--json` 输出同一结构。退出码 1。常见错误类:

| error_type | 含义 |
|---|---|
| `yaml_parse_error` / `invalid_encoding` | YAML 语法坏 / 文件不是 UTF-8(含重复键拒载) |
| `invalid_root` | 顶层不是映射或文件为空 |
| `credential_plaintext` | 凭据类键出现明文(应改 `env:` / `keychain:` 引用) |
| `unknown_field` | 未知字段(fail-fast;`sources[]` 下疑似已知字段拼错也报) |
| `missing_field` | 缺必填字段 |
| `invalid_value` | 取值不在枚举词表内(错误信息列出合法值) |
| `title_fingerprint_forbidden` | `dedup.key` 用了 `{title}` |
| `invalid_dedup_key` | 去重键无占位符,或占位符无法由源渲染 |
| `missing_url_field` | `extract.fields` 缺 `url` 且未配 `url_template`(`list`/`json_path` 二者必居其一;`rss` 必须含 `url`) |
| `invalid_url_template` / `unexpected_url_template` | `url_template` 无占位符或占位符不在 `fields` 字段名内 / 配在 `item` 单页源或 `rss` 上 |
| `invalid_rss_field` | `rss` 的 `fields` 值不在 feedparser entry 属性白名单内(拼错即拒) |
| `unexpected_transport_field` | `timeout`/`retries`/`retry_backoff_seconds` 出现在非 webhook 通道 |
| `unexpected_platform_field` | W2 平台凭据字段(`ntfy_token`/`dingtalk_secret`/`wecom_*`)配在非宿主通道 |

## 一致性保证

- `tests/test_skill_doc.py`:SKILL.md 字段表/枚举表逐项对照 pydantic 模型,
  两份文档互相引用。
- `tests/test_docs.py`:zh/en 双语页面章节结构对齐;docs 全部 `yaml` 代码块
  经 `load_category` 验证可载;示例零明文凭据;页内相对链接有效。
- 改 schema 必须同步四处:schema.py、SKILL.md、docs、plugins 示例——
  上述测试让漂移变成红测,而不是静默的误导文档。
