# 快速上手

> 3 分钟跑通第一个品类:安装 → 配凭据 → 试抓 → 正式运行 → 看数据。
> 读完本页再看[插件开发指南](write-a-plugin.md)与 [schema 参考](schema.md);
> 采集伦理与边界见 [FAQ](faq.md)。

## 1. 安装

世事 是纯 Python 包(Python 3.11+),核心零重依赖。注意:根包依赖同仓
子包 `shishi-classifier`(uv workspace 成员,未发布 PyPI),源码安装只能走
`uv sync`——裸 `pip install -e .` 解析不到该依赖,会直接失败:

```bash
git clone https://github.com/xinzhuzi/shishi
cd 世事
uv sync                     # 源码安装唯一走法(workspace 依赖仅 uv 可解析)
uv run shishi --version       # 输出 shishi x.y.z(x.y.z 为实际安装版本)
```

重引擎按需装可选依赖,未安装时流水线会结构化报错(`dependency_missing`)
并沿降级链继续,不会中断:

```bash
uv sync --extra crawl4ai    # L3 JS 渲染引擎
uv sync --extra llm         # enrich 精评 / aggregate 事件聚合(openai 客户端)
```

可选依赖共 5 个:`crawl4ai` / `scrapling` / `firecrawl` / `skyvern` / `llm`。
生产长跑可用服务器形态:`docker compose -f docker/docker-compose.yml up -d`(见
`docker/README.md`)。

## 2. 配置凭据

世事 **不允许在 YAML 里写明文凭据**,只能写引用,运行时解析:

- `env:VAR_NAME` —— 运行时读环境变量;
- `keychain:myia/<scope>/<name>` —— 读系统钥匙链(macOS Keychain /
  Windows DPAPI)。名空间必须规范;先用 `shishi secret set` 写入值,再在 YAML
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
shishi secret set myia/stocks/site_cookie < cookie.txt
```

不想配付费端点?看图与精评都有零成本走法(本地 mlx-vlm/Ollama → 云端
免费档 → Gemini 轻量日批),端点选型与额度快照见[零成本接入](zero-cost.md)。

配错也不用猜:`shishi doctor --json` 逐个核验引用是否存在(env 变量是否设置 /
钥匙链里是否有该名字),缺失即给出修复动作(如补一句 `shishi secret set ...`)。

## 3. 跑第一个品类

零凭据、零成本的最小品类:把下面内容存成 `plugins/demo-min.yaml`
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
uv run shishi test plugins/demo-min.yaml --json            # 逐源试抓,不入库不推送
uv run shishi run plugins/demo-min.yaml --dry-run --json   # 全链演练,不推送
uv run shishi run plugins/demo-min.yaml                    # 正式跑一次
```

- `shishi test --json` 逐源看:`ok`(拿到数据没有)、`engine`(实际选中引擎)、
  `items[].fields`(提取字段预览)、`fingerprint.verdict`(`unchanged_skip`
  = 内容未变属正常;`changed_or_first_fetch` = 会正常提取)。
- `shishi run --json` 看 `stages[]`(fetch/classify/dedup/analyze/push 各步
  items_in→items_out 与 skip 原因)和 `pushes[]`(分级分桶与发送结果)。
- 长期使用加 `--loop`:按 YAML 的 `schedule` + `timezone` 常驻调度。
  **注意**(一个 bot token 只允许一个轮询方):每个配置了 `telegram` 通道的
  常驻进程都会轮询同一条 `getUpdates` 流(bot token 固定取
  `env:TELEGRAM_BOT_TOKEN`),Telegram 对并发轮询方回 409 Conflict——同一
  token 下至多一个品类以 `--loop` 常驻,其余品类改用其他推送渠道;
  `shishi doctor` 会以 `telegram_token_poll_conflict` 提示多品类共配的情形。

跑仓库里的官方品类(羊毛/美股/AI 资讯/显卡行情等)同理,例如
`uv run shishi run plugins/wool.yaml`;官方插件用到的凭据位都写着
`env:` / `keychain:` 引用,按第 2 节备好即可。

也可以直接对 AI 说需求——agent 会读[插件开发指南](write-a-plugin.md)
(速查版见 [skill/SKILL.md](../../skill/SKILL.md))现场生成品类 YAML,
再用同一套命令验证与运行。

## 4. 数据在哪

所有数据在一个 SQLite 单文件里(默认 `./shishi.db`,`--db` 可改):抓取条目、
去重注册表、变更基线与数值历史、推送反馈、运行历史与源健康度。插件 YAML 的
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
  `shishi feedback list / stats / mark`(Telegram/飞书回调接收已就绪,
  卡片内按钮随桌面版交付)。
