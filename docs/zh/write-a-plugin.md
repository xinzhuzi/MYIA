# 插件开发指南

> 一个 MYIA 插件 = 一份 YAML = 一个情报品类,十二节 schema。本文是 AI 与人
> 共用的完整参考:每一节都有明确语义与缺省值。速查版是 Agent Skill
> [skill/SKILL.md](../../skill/SKILL.md),两份文档互相引用、由
> `tests/test_skill_doc.py` 与 `tests/test_docs.py` 逐字段锁定到
> `src/myia/schema.py`,不会漂移;本文只讲「怎么做」,逐字段细节见
> [schema 参考](schema.md)。

## 总工作流

```
用户需求
  → myia init --json           # 拿结构化信息清单(要收集什么、缺省是什么)
  → 写 plugins/<id>.yaml       # 对照 schema 参考(docs/zh/schema.md)逐节生成
  → myia test plugins/<id>.yaml --json    # 逐源试抓,核对字段与去重键(不入库不推送)
  → myia run plugins/<id>.yaml --dry-run --json   # 全链演练(不推送)
  → myia run plugins/<id>.yaml            # 正式跑一次;长期使用再加 --loop 常驻调度
  → myia doctor --json         # findings 清零后收工
```

所有命令的 `--json` 输出是**恰好一份 JSON 文档**(stdout),日志走 stderr;
`stdout` 推送通道的卡片行在 `--json` 下同样改写 stderr(整份 stdout 恒可 `json.load`)。
退出码:`0` 成功 / `1` 配置或用法错误 / `2` 采集全部失败 / `3` 部分失败。
修坏了的源也是同一个循环:`myia doctor --json` 报结构化问题 → agent 改
YAML(或环境)→ 复验,用户只做决策。

## 硬规则

违反任何一条,YAML 会在启动即被拒载(退出码 1,错误带字段路径):

1. **凭据禁明文**:凭据类键(键名含 `cookie` / `authorization` / `token` /
   `secret` / `password` / `apikey` / `session` 子串,大小写与连字符不敏感)
   的值只允许 `env:VAR` 或 `keychain:myia/<scope>/<name>` 引用。
2. **永不标题指纹**:`dedup.key` 禁用 `{title}`,只用 URL 或字段组合键。
3. **未知字段 fail-fast**:除 `sources[]` 开放源级扩展参数外,拼错字段名即拒
   载;与已知字段接近的未知键按拼写错误报。
4. **缺省值完备**:每个字段都有缺省值,只写必填项也能跑。
5. **`engine: auto` 降级链顺序固定**:L1 `direct_api` → L2 `static_html` →
   L3 `crawl4ai` → `firecrawl` → L4 `scrapling` → L5 `stealth_browser` →
   L6 `llm_browser`;选中结果回写 SQLite `engine_hints`,**永不回写用户 YAML**。
6. **推送语义分层**:`push[].route` 管「推不推」(按 score/字段分级),
   AM/PM 槽位管「发没发过」(防重发),两层正交。
7. **YAML 必须 UTF-8**;顶层必须是映射;重复键拒载。

## 引擎怎么选(L1–L6)

| 层 | 引擎 | 什么时候用 | 代价/前提 |
|---|---|---|---|
| L1 | `direct_api` | 数据有公开 JSON/REST API(行情、发版、社区 REST) | 最快最省;`extract.type: json_path`;先 curl 确认返回结构再写 fields;响应无页面 URL 时用 `extract.url_template` 从 `{field}` 渲染条目链接(见 `plugins/games.yaml`) |
| L2 | `static_html` | 服务端渲染 HTML(论坛列表、新闻页) | 零依赖;`extract.type: list` + CSS 选择器 |
| L3 | `crawl4ai` | JS 渲染页面,页面源码里搜不到数据 | 可选依赖 `myia[crawl4ai]`,未装报 `dependency_missing` 并继续降级;无 `extract` 时自动结构化兜底 |
| L3' | `firecrawl` | crawl4ai 的云端替代后端 | 需 endpoint+key(`MYIA_FIRECRAWL_URL` / `MYIA_FIRECRAWL_API_KEY`,或 `engine_options.firecrawl.endpoint/api_key` 引用);未配置该层失败并继续降级 |
| L4 | `scrapling` | 基础反爬盾(TLS/HTTP2 指纹、CF 基础质询)、改版频繁源(自愈选择器)、无限滚动列表 | 可选依赖 `myia[scrapling]`;后端经 `engine_options.scrapling.backend`(`stealth`/`dynamic`/`static`);`pagination.mode: scroll` 仅本层支持 |
| L5 | `stealth_browser` | 强风控页面(反检测浏览器,经 MCP 协议驱动) | 需 Playwright-MCP 服务器;单 run 页面预算 `engine_options.stealth_browser.max_pages`(默认 10);源 `headers.Cookie` 可注入过登录墙 |
| L6 | `llm_browser` | 以上全部失败的最后手段(LLM 驱动浏览器,skyvern) | 烧 token 只做兜底;`MYIA_SKYVERN_URL` / `MYIA_SKYVERN_API_KEY` 或 `engine_options.llm_browser.endpoint/api_key` 引用 |

经验法则:先看页面源码——搜得到数据写 L2,搜不到找 API 走 L1,JS 化才 L3;
拿不准就 `engine: auto`,用 `myia test --json` 看实际选中引擎(`engine` 字段)。
不要给 API 源写 CSS 选择器,不要给 SSR 页面写 json_path。所有引擎默认尊重
robots.txt、默认限速(qps 0.5);「真人验证+手机号」类源不碰、不绕过:L5
`stealth_browser` 识别硬墙后零尝试并结构化报错(`captcha_*`),其余层只有泛化
失败或空结果(见 [FAQ](faq.md))。

## 完整示例

L1 API + 飞书推送,展示大部分节(可直接复制,`load_category` 验证可载):

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

仓库 `plugins/` 下的官方品类(wool / stocks / ai-news / gpu-prices /
monitor / credentials)是更完整的活例子,覆盖 sidecar 节的官方写法见
[gpu-prices.yaml](../../plugins/gpu-prices.yaml)(baseline)与
[monitor.yaml](../../plugins/monitor.yaml)(plugin 双模式)。

## 最小示例

L2 静态页 + stdout,零凭据,5 分钟跑通:

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

## 凭据规则

- 凭据**永不明文**写进插件 YAML;只写 `env:VAR_NAME` 或
  `keychain:myia/<scope>/<name>` 引用。
- 写入钥匙链:`myia secret set myia/<scope>/<name>`,值走 stdin 管道或安全
  输入(**不要**用 `--value` 传——会落 shell history 与进程列表);
  `myia secret list` 只列名字;`myia secret delete <name>` 删除。
- 可带认证 scheme 前缀:`Authorization: "Bearer env:AIPOCKET_TOKEN"`。
- 明文凭据 = 启动即拒载(退出码 1,错误带字段路径);凭据值永不回显、永不落
  日志(引用名可以出现,展开后的值禁止)。
- `myia doctor --json` 核验每个引用的存在性(`env_ref_missing` /
  `keychain_ref_missing`),agent 可据此自修环境。

各通道凭据约定(`push[]` 的 `target` 与配套 token;细节同
[skill/SKILL.md](../../skill/SKILL.md) §2.13):

- `feishu_card`:`target` = 收件/群 ID(如 `env:FEISHU_CHAT_ID`),机器人
  token 从 `env:FEISHU_BOT_TOKEN` 读。
- `telegram`:`target` = chat id(`env:TELEGRAM_CHAT_ID`),token 从
  `env:TELEGRAM_BOT_TOKEN` 读。
- `webhook`:`target` = 端点 URL 引用(如 `env:MYIA_WEBHOOK_URL`)。
- `stdout`:零凭据,本地验证首选。

## 验证与运行

```bash
myia test plugins/<id>.yaml --json          # 逐源试抓(不入库不推送,每源默认 120s 超时;--source 只试一个源)
myia run plugins/<id>.yaml --dry-run --json # 全链演练,不推送无副作用
myia run plugins/<id>.yaml                  # 正式跑一次
myia run plugins/<id>.yaml --loop           # 常驻:按 schedule+timezone 自动触发
```

- `myia test --json` 逐源看:`ok`、`engine`、`items[].fields`、
  `items[].dedup_key`(出现 `dedup_key_error` 说明模板占位符渲染不出)、
  `fingerprint.verdict`、`failures[]`(engine / error_type / message)。
- `myia run --json` 看:`stages[]`(各步 items_in→items_out 与 skips 原因)、
  `sources[]`、`push[]`(逐通道分桶/路由/发送结果);`status` 为 `success`/`partial`/`failed`,
  对应退出码 0/3/2。
- 内容未变的源被变更指纹跳过(skip 原因可见)是**正常路径**,不是故障。

## 自诊断(doctor findings → 修复动作)

```bash
myia doctor --json                    # 缺省体检 plugins/ 全部插件;也可 myia doctor <yaml> --json
```

源健康度状态机:`ok` / `degraded`(指纹未跳过却 0 条,或低于近 5 次基线的
50%)/ `dead`(连续 3 轮失败)/ `unknown`(无运行记录)。run 级 success 不掩盖
单源静默 0 条——以源级健康度为准。常见 findings 与修复动作:

| code / 情形 | 修复动作 |
|---|---|
| `credential_plaintext` | 把明文改成 `env:`/`keychain:` 引用 |
| `unknown_field` | 对照 [schema 参考](schema.md) 改字段名 |
| `missing_field` / `invalid_value` | 按 `path` 字段路径定位,补必填项或改取值 |
| `source_dead`(连续 3 轮失败) | `myia test --json` 看 `failures[]`:换 URL / 调 extract / 升引擎或代理;确认无解就注释掉该源 |
| `source_degraded`(0 条/腰斩) | 页面结构疑似变化:重跑 `myia test --json`,修 extract 选择器 |
| `env_ref_missing` | 设置环境变量(如 `export FEISHU_CHAT_ID=...`) |
| `keychain_ref_missing` | `myia secret set <引用名>` 写入钥匙链 |
| `keychain_name_noncanonical` | 引用名改为 `myia/<scope>/<name>` 后改 YAML |
| `store_error` | SQLite 库损坏或 schema 版本过新:换 `--db` 路径或删除重建(会丢历史) |
| 插件类 finding(v0.3) | 市场插件装不上/remote 不可达只降级为 finding,**不拦核心流水线**;按 message 修 `plugin:` 节或重装 |
| `telegram_token_poll_conflict` | 两个及以上品类共用同一 bot token(`env:TELEGRAM_BOT_TOKEN`)且各自 `--loop` 都会轮询 `getUpdates` → Telegram 回 409 Conflict;同一 token 下至多一个常驻品类保留 telegram 通道,其余品类改用其他推送渠道(单次 run 不轮询,不受影响) |

## 交付前自查

1. `myia test` 通过(退出码 0 或 3,源 `ok: true`、字段预览非空、无
   `dedup_key_error`)。
2. `myia doctor` 的 `findings` 清零。
3. 所有凭据位都是引用,无明文;`engine` 取值合法;`dedup.key` 不含 `{title}`。
4. 品类对不上七大类时 `classify.builtin: false`(否则未命中条目全被丢弃)。
5. 要趋势对比加 `baseline:` 节,要多源同事件合并加 `aggregate:` 节
   (写法见 [schema 参考](schema.md) 的 sidecar 节)。

## 进阶:插件市场与反馈闭环

- **场景插件**(v0.3,v1.1 瘦身):`myia plugin list / install / remove`;
  品类 YAML 用顶层 `plugin:` 节声明依赖(`remote` 端点+钥匙链 token;
  `local` docker compose 仍是合法 schema,但官方插件不再携带本地部署
  文件 —— 服务端部署统一在 `docker/plugins/`);插件装不上不拦核心流水线
  (安全基线铁律)。社区插件目录见 `plugins/community/README.md`。
- **源码/进程内插件**(v1.1,桌面优先):官方包声明 `tier` 分级
  (`desktop` 桌面默认集 / `remote` 桌面可选 / `server-only` 服务端可选),
  `myia plugin list` 按级展示。样板 `myia-osint` 上游源码以 git submodule
  钉在插件目录 `vendor/`(pin commit;manifest 增可选 `vendor:` /
  `adapter:` 两节,未知字段照旧 fail-fast),适配器以子进程调用上游 CLI
  (uv 临时环境按需装依赖,不进根依赖)——`myia osint https://example.com
  --json` 零 Docker 完成一次结构化侦察;`myia-proxy` 为进程内对应路径:
  `myia proxy --json` 进程内抓取公开免费代理并逐个测活(零 Redis 零
  Docker)。上游缺失/装不上只结构化降级,不拦核心流水线。
- **反馈闭环**(v0.3):负反馈入库;`myia feedback list / stats / mark`,
  维护阶段按负反馈自动调优 watchlist 词表权重与阈值(prompt 要点随调整历史
  记录并在 stats 呈现;Telegram/飞书回调接收已就绪,卡片内按钮随桌面正式版
  交付)。
- **趋势基线与事件聚合**(v0.4):`baseline:` 数值历史 + 「vs 昨日/上周」,
  `aggregate:` 多源同事件合并成单卡(「另见 N 源」);写法见
  [schema 参考](schema.md)。
