# 快速上手

> 3 分钟跑通第一个品类:安装 → 配凭据 → 试抓 → 正式运行 → 看数据。
> 读完本页再看[插件开发指南](write-a-plugin.md)与 [schema 参考](schema.md);
> 采集伦理与边界见 [FAQ](faq.md)。
> 桌面端从源码本地构建(Windows MSI)见 [Windows 本地构建](build-windows.md)。

## 1. 安装

### 1.1 桌面应用(推荐)

桌面安装包(macOS dmg / Windows msi)从
[Releases](https://github.com/xinzhuzi/myia/releases) 下载。**Python 运行时与
第三方依赖不随安装包分发**(包体因此显著更小):装好后首次启动,到
**设置 → Python 环境** 分区点「开始配置」,应用在线安装一套自管环境,全程状态
可见、失败可重试:

- 安装链五步:**下载 → sha256 校验 → 解压到数据根 → pip 按锁版清单装依赖 →
  自检握手**,设置页逐项亮灯;断网/坏包中途失败,重试时已完成的步骤自动跳过
  (幂等续装),不用从头再来。
- 运行时是**钉版**的
  [indygreg/python-build-standalone](https://github.com/indygreg/python-build-standalone)
  `cpython-3.12.7+20241016`(install_only 发行,sha256 随包清单钉死,换镜像也
  绕不过校验);装进数据根下的 `python/`(macOS
  `~/Library/Application Support/MYIA/python/`,Windows `%APPDATA%\MYIA\python\`),
  绝不写安装目录。
- **双镜像可覆盖**(弱网/国内网络友好):设置页可同时替换「运行时下载源」
  (整串 URL)与「PyPI 镜像」(如 `https://pypi.tuna.tsinghua.edu.cn/simple`)。
  镜像只换下载地址,版本钉死与 sha256 校验不放松。
- 首跑需联网,预留约 500MB 磁盘;环境就绪前各屏显示「Python 运行环境未配置」
  引导,**不会自动后台下载**,配置完成后核心进程即自动拉起。
- 从旧版本升级:数据库/插件/模型/钥匙串凭据**原样沿用零迁移**,首启出现一次性
  引导横幅,进设置完成同一次配置即可;之后应用更新若依赖清单有变,同一分区出现
  「同步依赖」一键幂等补齐。

环境就绪后,桌面端跑的品类与 CLI 完全同形——都是一份 YAML。**本仓开源的是
引擎,品类由你自填**:装机自带零凭据演示件 `myssia-demo`(GitHub 新星榜,
首跑即可出真数据)与各品类的脱敏模板 `config/*.example.yaml`,真实品类照
模板自建(走法见第 3 节)。演示件简化形态如下:

```yaml
id: myssia-demo
name: 演示 · GitHub 新星
schedule: "0 9 * * *"
sources:
  - name: github-new-stars
    engine: direct_api
    url: "https://api.github.com/search/repositories?q=created:%3E2026-09-01&sort=stars&order=desc&per_page=30"
    extract:
      type: json_path
      fields:
        title: "$.items[*].full_name"
        url: "$.items[*].html_url"
push:
  - channel: stdout              # 零凭据通道,环境配置完成即可开箱跑
```

### 1.2 源码安装(CLI / 开发)

世事 是纯 Python 包(Python 3.11+),核心零重依赖。注意:根包依赖同仓
子包 `myssia-classifier`(uv workspace 成员,未发布 PyPI),源码安装只能走
`uv sync`——裸 `pip install -e .` 解析不到该依赖,会直接失败:

```bash
git clone https://github.com/xinzhuzi/myia
cd shishi
uv sync                     # 源码安装唯一走法(workspace 依赖仅 uv 可解析)
uv run myssia --version       # 输出 myssia x.y.z(x.y.z 为实际安装版本)
```

重引擎按需装可选依赖,未安装时流水线会结构化报错(`dependency_missing`)
并沿降级链继续,不会中断:

```bash
uv sync --extra crawl4ai    # L3 JS 渲染引擎
uv sync --extra llm         # enrich 精评 / aggregate 事件聚合(openai 客户端)
uv sync --extra table       # 表格还原(rapid_table:截图表格 → metadata.tables)
uv sync --extra trafilatura # L2 正文兜底(无规则/规则失效源的单页正文抽取,opt-in 开关)
```

能力 extras 共 12 个:`crawl4ai` / `scrapling` / `firecrawl` / `skyvern` /
`simplex` / `llm` / `vision` / `table` / `trafilatura` / `apprise` /
`telethon` / `browser`;另有 `all` 聚合键一键装其中六件(`crawl4ai` /
`scrapling` / `firecrawl` / `skyvern` / `llm` / `browser`,
`simplex`/`vision`/`table`/`trafilatura`/`apprise`/`telethon` 不在其内——
`all` 只与 `vision` 共享 openai 一个包,看图双引擎
ocrmac / rapidocr(+ onnxruntime 后端)不在 `all` 内,要看图请单装
`uv sync --extra vision`。表格还原在桌面端还有第二条安装通道:
**设置 → Python 环境的「表格还原」组件开关**——往自管环境装钉版组件,零重
打包,镜像覆盖生效;品类里用 `images.table: true` 开启(schema 细节见
[schema 参考](schema.md) 的 images 节)。缺装时品类照常跑,只是表格还原静默
降级,`myssia doctor` 会出 `table_dependency_missing` 提前披露。
生产长跑可用服务器形态:`docker compose -f docker/docker-compose.yml up -d`(见
`docker/README.md`)。

**trafilatura 正文兜底的开关(缺省关)**:装了 extras 只是具备能力,还需环境
变量 `MYIA_EXTRACT_FALLBACK=1` 显式打开——static_html 引擎才会对「无 extract
规则」与「规则跑空(改版失效)」的源做单页正文兜底(产出条目带
`metadata.extract_provenance = "trafilatura"`,正文量不足 120 字符的列表页退化
形态会被质量门拦下维持零条)。开关是全局环境变量,**按启动形态分两态**:

- CLI / 直跑二进制:命令行 env 前缀,如
  `MYIA_EXTRACT_FALLBACK=1 myssia run <品类>`;
- 已安装的桌面 app:`launchctl setenv MYIA_EXTRACT_FALLBACK 1` 后**重启 app**
  (`open` 不透传 shell 环境变量,sidecar 由 app 父进程环境继承;同
  `MYIA_SMOKE_ROUTE` 通路)。取消:`launchctl unsetenv MYIA_EXTRACT_FALLBACK`
  后重启。

全局 env 之外还可**按源覆写**:品类 YAML 里
`engine_options.static_html.extract_fallback: true/false` 优先于全局环境变量
(优先级=源级 > 全局 `MYIA_EXTRACT_FALLBACK=1` > 缺省关,源级未设时与全局口径
逐字节一致;值非布尔 fetch 期结构化拒),详表见
[schema 参考](schema.md) 的 sources `extract` 行。

## 2. 配置凭据

世事 **不允许在 YAML 里写明文凭据**,只能写引用,运行时解析:

- `env:VAR_NAME` —— 运行时读环境变量;
- `keychain:myia/<scope>/<name>` —— 读系统钥匙链(macOS Keychain /
  Windows DPAPI)。名空间必须规范;先用 `myssia secret set` 写入值,再在 YAML
  里引用。扁平旧名(如 `keychain:linuxsb_cookie`)在解析期被拒。

```bash
# 推送通道(飞书机器人;Telegram 则是 TELEGRAM_CHAT_ID / TELEGRAM_BOT_TOKEN)
export FEISHU_CHAT_ID=...
export FEISHU_BOT_TOKEN=...

# LLM 精评端点(OpenAI 兼容;世事 无内置端点、无默认 key)
export MYIA_LLM_BASE_URL=...
export MYIA_LLM_KEY=...

# 不进环境变量的凭据(如源站 Cookie)入钥匙链:值走 stdin 管道,
# 不要用命令行参数传(会落 shell history 与进程列表)
myssia secret set myia/stocks/site_cookie < cookie.txt
```

微信通道是可选桥接,两条路:`weixin` 官方桥出站经本机常驻的 Hermes-Agent
持有登录态,无 Hermes 的环境该平台不可用(平台卡会如实标「需本机 Hermes」);
同机便捷路径 `weixin-local` 走受管组件 wechat-bridge,无需 Hermes。两者 myssia
侧都零微信凭据,约定见[插件开发指南](write-a-plugin.md)。

Reddit 源走官方 Data API 引擎(`engine: reddit`,链外显式选用),凭据**可选**:
不配 = 该源显式空态(`credential_missing` skip,零请求,不拦品类);要启用,
先在 [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) 注册 script 型
app(redirect uri 占位 `http://localhost:8080` 即可),再把 client_id/secret
写入钥匙串:

```bash
myssia secret set myia/reddit/client-id < id.txt
myssia secret set myia/reddit/client-secret < secret.txt
```

然后在源 YAML 里配 `engine_options.reddit.client_id/client_secret` 引用
(示例见 `config/ai-news.example.yaml` 注释块)。时效提醒:Reddit 官方新 API 申请
窗口 2026-10-31 截止、公开 Data API 2027-03 起分阶段终结(RSS 通道
2026-11-13 退役)—— 通道内源全部经 OAuth 授权调用,`.rss`/`.json` 直抓
路线因 robots 全站禁抓被禁用。

不想配付费端点?看图与精评都有零成本走法(本地 mlx-vlm/Ollama → 云端
免费档 → Gemini 轻量日批),端点选型与额度快照见[零成本接入](zero-cost.md)。

配错也不用猜:`myssia doctor --json` 逐个核验引用是否存在(env 变量是否设置 /
钥匙链里是否有该名字),缺失即给出修复动作(如补一句 `myssia secret set ...`)。

## 3. 跑第一个品类

零凭据、零成本的最小品类:把下面内容存成 `config/demo-min.yaml`
(`url` 换成任何一个服务端渲染的列表页即可):

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

三个命令走完「验证 → 演练 → 正式」:

```bash
uv run myssia test config/demo-min.yaml --json            # 逐源试抓,不入库不推送
uv run myssia run config/demo-min.yaml --dry-run --json   # 全链演练,不推送
uv run myssia run config/demo-min.yaml                    # 正式跑一次
```

- `myssia test --json` 逐源看:`ok`(拿到数据没有)、`engine`(实际选中引擎)、
  `items[].fields`(提取字段预览)、`fingerprint.verdict`(`unchanged_skip`
  = 内容未变属正常;`changed_or_first_fetch` = 会正常提取)。
- `myssia run --json` 看 `stages[]`(fetch/classify/dedup/analyze/push 各步
  items_in→items_out 与 skip 原因)和 `pushes[]`(分级分桶与发送结果)。
- 长期使用加 `--loop`:按 YAML 的 `schedule` + `timezone` 常驻调度。
  多品类共享同一 bot token(bot token 固定取 `env:TELEGRAM_BOT_TOKEN`)是
  受支持形态:Telegram 对同 token 并发 `getUpdates` 回 409 Conflict,产品
  已内置同 token 单轮询器防护——先到的常驻进程独占反馈接收,后到者轮询
  禁动(推送不受影响,反馈会入先到者的库);`myssia doctor` 会以
  `telegram_token_poll_conflict` 披露多品类共配时的这一单接收方语义。

仓库不携带任何真实品类(引擎开源,品类自填):`config/` 下只有演示件与
脱敏模板。每一份模板 `config/<品类>.example.yaml` 与真实品类同构同注释,
只是源 URL / 群名 / 频道 id 换成了占位。以羊毛(社区优惠情报)为例走通一份
自填品类:

```bash
# 1. 复制模板为可跑的品类文件(数据根 config/ 或仓库 config/ 均可)
cp config/wool.example.yaml config/wool.yaml

# 2. 编辑 config/wool.yaml:把 https://EXAMPLE_FEED_N 占位换成本人自选源,
#    凭据位模板里已写成 env: / keychain: 引用,按第 2 节备好即可
uv run myssia test config/wool.yaml --json   # 逐源试抓,核占位是否换净
uv run myssia run config/wool.yaml            # 正式跑;--loop 常驻调度
```

其余模板(美股 `stocks` / AI 资讯 `ai-news` / 显卡行情 `gpu-prices` 等)
同一走法。零凭据先体验:演示件 `myssia-demo` 开箱即跑,
`uv run myssia run config/myssia-demo.yaml`。

也可以直接对 AI 说需求——agent 会读[插件开发指南](write-a-plugin.md)
(速查版见 [skill/SKILL.md](../../skill/SKILL.md))现场生成品类 YAML,
再用同一套命令验证与运行。

## 4. 数据在哪

所有数据在一个 SQLite 单文件里(默认 `./myssia.db`,`--db` 可改):抓取条目、
去重注册表、变更基线与数值历史、推送反馈、运行历史与源健康度。品类 YAML 的
`storage.retention` 控制保留期(过期自动清理),`storage.vacuum` 控制
VACUUM 周期;声明了 `baseline:` 的品类,数值历史按保留期的 2 倍保存
(保证「较上周」窗口完整)。

## 5. 下一步

- [插件开发指南](write-a-plugin.md):从需求到可运行 YAML 的完整流程,
  与 [skill/SKILL.md](../../skill/SKILL.md) 同源一致。
- [schema 参考](schema.md):12 节逐字段说明,外加 v0.3/v0.4 三个 sidecar 节
  (`plugin:` 场景插件 / `baseline:` 趋势基线 / `aggregate:` 事件聚合)。
- [FAQ](faq.md):采集伦理与边界(robots.txt、验证码、真人验证)、
  凭据安全、常见故障的自诊断路径。
- [零成本接入](zero-cost.md):免费 LLM 端点指引——本地 mlx-vlm/Ollama
  (默认)→ 云端免费(智谱免费模型、OpenRouter `:free`)→ Gemini 轻量日批,
  额度快照与逐家接入步骤。
- 反馈闭环:负反馈回写并持续调优;命令行对应
  `myssia feedback list / stats / mark`(Telegram/飞书回调接收已就绪,
  卡片内按钮随桌面版交付)。
