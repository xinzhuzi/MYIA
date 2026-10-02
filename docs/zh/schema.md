# Schema 参考

> 一个情报品类 = 一份 YAML。本文逐节给出字段、取值与缺省值,与
> `src/myia/schema.py` 逐字段一致(由 `tests/test_docs.py` /
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

除这 12 节外,还有三个**可选 sidecar 节**(不改变 12 节公开契约,由装载入口
单独校验):`plugin:`(场景插件双模式,v0.3)、`baseline:`(趋势基线,v0.4)、
`aggregate:`(事件聚合,v0.4),见 [sidecar 节](#sidecar-节v03v04)。

## 词汇表(枚举,与 schema.py 常量逐值一致)

| schema 常量 | 取值 |
|---|---|
| `ENGINES` | `auto` `direct_api` `static_html` `crawl4ai` `firecrawl` `scrapling` `stealth_browser` `llm_browser` |
| `PAGINATION_MODES` | `template` `selector` `scroll` |
| `EXTRACT_TYPES` | `list` `item` `json_path` |
| `BACKOFF_POLICIES` | `exponential` `linear` `none` |
| `PUSH_CHANNELS` | `feishu_card` `telegram` `webhook` `stdout` |
| `ROUTE_MODES` | `immediate` `digest` `archive` |
| `ENRICH_SCORES` | `value` `relevance` `credibility` |
| `VACUUM_CADENCES` | `daily` `weekly` `monthly` `never` |
| `BASELINE_WINDOWS` | `day` `week` |
| `REQUIRES_TOKENS` | `docker` |
| `CREDENTIAL_KEY_SUFFIXES` | `cookie` `authorization` `token` `secret` `password` `passwd` `apikey` `session` |

## 凭据引用语法

- 只允许两种写法:`env:VAR_NAME`(运行时读环境变量)或
  `keychain:myia/<scope>/<name>`(系统钥匙链:macOS Keychain / Windows
  DPAPI;名空间必须规范,扁平旧名解析期被拒)。
- 可带认证 scheme 前缀:`Authorization: "Bearer env:AIPOCKET_TOKEN"`。
- 凭据类键(键名含词表 `CREDENTIAL_KEY_SUFFIXES` 子串,大小写/连字符不敏感)
  的值出现明文 → 加载期拒载(错误码 `credential_plaintext`)。该规则覆盖
  `sources[].headers`、`post_body`、源级扩展参数——整份 YAML 文档,不止头部。
- 凭据值永不回显、永不落日志;`myia secret set myia/<scope>/<name>` 写入,
  值走 stdin 管道或安全输入。
- `enrich.base_url` / `enrich.api_key` / `push[].target` 必须是**纯**引用
  (不允许 scheme 前缀)。

## when 表达式白名单(classify.rules 与 push.route 通用)

- 字面量:字符串/数字/布尔/null;名字 = 条目字段(缺字段按 null 读)。
- 容器:`[a, b]` 列表;运算符:`+ - * / // % **`、比较 `== != < <= > >= in
  not in`(可链式)、`and` / `or` / `not`。
- 函数白名单(仅位置参数):`abs` `min` `max` `round` `len` `int` `float` `str`。
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
| `engine` | `auto` | 引擎名(见词汇表;auto 按降级链,选中回写 SQLite hints 不回写 YAML) |
| `url` | `必填` | http(s) 地址;支持 `{placeholder}` 模板(翻页 `{page}`、扇出 `{symbol}`) |
| `method` | `GET` | `GET` / `POST`;POST 必配 `post_body`,GET 禁止 |
| `post_body` | `null` | POST 表单/JSON 体(映射);凭据键同 headers 禁明文 |
| `headers` | `{}` | 请求头;凭据键的值必须是引用;不要伪装浏览器 UA(默认 UA 是诚实的 `MYIA/0.1 (...)`) |
| `pagination` | `null` | 翻页配置,见下表 |
| `extract` | `null` | 字段提取,见下表;留空时 L3+ 引擎自动结构化兜底 |
| `rate_limit` | `见 rate_limit 表` | 礼貌限速(限速在引擎层统一执行) |
| `proxy` | `direct` | `direct` / `pool:<名称>` / `residential:<区域>`;pool 需 `--config` 全局配置 |
| `retry` | `3` | 瞬时错误重试预算(0-10) |

**源级扩展参数**:未知键(如 `symbols: [NVDA, AAPL]`)原样传给引擎——URL 里的
`{symbol}` 按列表逐值扇出(一值一请求);`engine_options.<引擎名>` 是引擎
旋钮命名空间(如 `engine_options.firecrawl.endpoint`、
`engine_options.scrapling.backend`、`engine_options.stealth_browser.max_pages`)。
扩展参数里的凭据类键同样禁明文。

pagination:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `mode` | `template` | `template` = URL 里 `{page}` 逐页;`selector` = 跟随下一页链接;`scroll` = 无限滚动(仅 L4 `scrapling` 支持) |
| `max_pages` | `1` | 最多翻页数(1-10000);空页/指纹未变提前停 |
| `selector` | `null` | `mode: selector` 时的下一页链接选择器(该模式必填) |

extract:

| 字段 | 缺省 | 语义 |
|---|---|---|
| `type` | `必填` | `list` = HTML 列表项;`item` = 单页;`json_path` = JSON API |
| `item` | `null` | `type: list` 时的条目容器 CSS 选择器(该类型必填;其余禁写) |
| `fields` | `必填` | 字段名 → 选择器/JSONPath,至少 1 个;`list`/`json_path` **必须含 `url`** |

选择器语法:L1/L2 用 CSS(条目内相对选择器,`a@href` 取属性,相对 URL 自动
补全);`json_path` 用 `$` 路径(`$.chart.result[0].meta.price`、`$[*].keyword`
通配)。已知缺口:`json_path` 表达不了「条目 URL = 请求 URL」,此类 API 用
稳定业务字段充当 `url`(官方 `plugins/stocks.yaml` 即此写法)。

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
| `model` | `glm-4-flash` | 模型名(任意 OpenAI 兼容端点) |
| `scores` | `[value, relevance, credibility]` | 评分维度(各 0-10),回填为条目 `score` 供 route 使用 |
| `batch` | `20` | 批量评分条数(1-1000) |
| `cache` | `true` | 按 URL 缓存评分,同一 URL 永不打两次分 |
| `budget_per_run` | `50000` | 单次 run 的 token 预算护栏;耗尽自动降级纯关键词粗筛并有 WARNING |
| `base_url` | `null` | OpenAI 兼容端点,**只能是纯 `env:`/`keychain:` 引用**(MYIA 无内置端点),如 `env:MYIA_LLM_BASE_URL` |
| `api_key` | `null` | API key,同上,如 `env:MYIA_LLM_KEY`(MYIA 无默认 key) |

真实调用还需可选依赖:`uv sync --extra llm`(未装时结构化报错
`dependency_missing` 并降级关键词粗筛)。

### push

| 字段 | 缺省 | 语义 |
|---|---|---|
| `channel` | `必填` | `feishu_card` / `telegram` / `webhook` / `stdout` |
| `target` | `null` | 推送目标,只能是**纯** `env:`/`keychain:` 引用;`stdout` 禁止配置,其余通道必填 |
| `route` | `[]` | 阈值路由(见下表);留空 = 七大类缺省映射(羊毛/节点/代买 → immediate,其余 → digest) |
| `template` | `null` | Jinja2 卡片模板(沙箱渲染,语法错误加载期拒);省略用通道内置版式 |
| `timeout` | `10.0` | 发送超时秒数(**仅 `webhook` 生效**,其他通道配置即拒) |
| `retries` | `2` | 发送重试次数(仅 `webhook`) |
| `retry_backoff_seconds` | `1.0` | 发送重试退避秒数(仅 `webhook`) |

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

三个可选顶层节,**不属于 12 节公开契约**:由 `load_category` 在装载入口单独
校验(错误路径带 `$.plugin` / `$.baseline` / `$.aggregate` 前缀,`null` 视为
未声明),挂到 `CategoryConfig` 的同名属性。任何一个 sidecar 校验失败,整份
YAML 拒载(退出码 1)。

### plugin:场景插件双模式(v0.3)

品类依赖某个市场插件(`myia plugin install` 安装)提供的服务时声明。
**任何插件装不上/配置坏/remote 不可达都不拦核心流水线**——降级为结构化
finding,品类照常跑(安全基线铁律)。

| 字段 | 缺省 | 语义 |
|---|---|---|
| `id` | `必填` | 插件 id(小写字母/数字/连字符/下划线,字母数字开头,惯例 `myia-<名称>`) |
| `requires` | `[]` | 宿主能力词表(当前仅 `docker`);字符串或列表皆可 |
| `modes` | `必填` | 双模式至少声明一个:`local`(compose 文件路径 / install 命令至少其一)或 `remote`(endpoint 必填;token **必须** `keychain:myia/<scope>/<name>` 引用,`env:` 也不行) |

```yaml
id: site-watch
name: 页面变更监控
schedule: "*/15 * * * *"
timezone: Asia/Shanghai
plugin:                           # 场景插件声明(v1.1 起官方包为 remote 可选接入)
  id: myia-monitor
  requires: []
  modes:
    remote:                       # 指向已部署实例(桌面零 Docker);local compose 仍是合法 schema,官方部署文件在 docker/plugins/
      endpoint: https://my-monitor.example.com
      token: keychain:myia/monitor/token    # myia secret set myia/monitor/token
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
合计消费,谁先到顶谁降级。开启后 `myia run --json` 的 `stages[]` 会多出
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

## 加载期错误(结构化)

装载失败抛 `LoadError`:一次报告**全部**错误,每条含字段路径(JSONPath 风格
如 `$.sources[0].rate_limit.qps`)+ 机器错误类 + 中文原因;`myia doctor
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
| `missing_url_field` | `extract.fields` 缺 `url`(`list`/`json_path` 必填) |
| `unexpected_transport_field` | `timeout`/`retries`/`retry_backoff_seconds` 出现在非 webhook 通道 |

## 一致性保证

- `tests/test_skill_doc.py`:SKILL.md 字段表/枚举表逐项对照 pydantic 模型,
  两份文档互相引用。
- `tests/test_docs.py`:zh/en 双语页面章节结构对齐;docs 全部 `yaml` 代码块
  经 `load_category` 验证可载;示例零明文凭据;页内相对链接有效。
- 改 schema 必须同步四处:schema.py、SKILL.md、docs、plugins 示例——
  上述测试让漂移变成红测,而不是静默的误导文档。
