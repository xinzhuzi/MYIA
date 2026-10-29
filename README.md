<div align="center">

<img src="docs/assets/myssia-icon-1024.png" width="160" alt="世事 — 眼睛与雷达虹膜" />

# myssia(中文名:世事)

**AI 原生情报中枢 · AI-native intelligence hub**

**说需求,AI 做其余。** · Say what you want — AI does the rest.

<p>
<img src="https://img.shields.io/badge/tests-6900%2B%20passing-2EA44F" alt="tests: 6900+ passing" />
<img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="python 3.11+" />
<img src="https://img.shields.io/badge/license-MIT-3DA639" alt="MIT license" />
<img src="https://img.shields.io/badge/status-v0.0.16%20released-2EA44F" alt="status: v0.0.16 released" />
<img src="https://img.shields.io/github/downloads/xinzhuzi/MYIA/total?color=8B5CF6&label=downloads" alt="downloads" />
</p>
<p>
<img src="https://img.shields.io/badge/fetch%20engines-6%20(L1%E2%80%93L6)-22D3EE" alt="6 fetch engines, L1–L6" />
<img src="https://img.shields.io/badge/push-Feishu%20%C2%B7%20Telegram%20%C2%B7%20weixin%2Dlocal%20%C2%B7%20macos_notify%20%C2%B7%20bark%20%E7%AD%89%2034%20%E9%80%9A%E9%81%93-8B5CF6" alt="push: Feishu · Telegram · weixin-local · macos_notify · bark 等 34 通道" />
<img src="https://img.shields.io/badge/weight-SQLite%20single%20file%20%C2%B7%20no%20daemon-64748B" alt="SQLite single file, no daemon" />
</p>

<img src="docs/demo/assets/shishi-demo.gif" width="800" alt="世事 demo:一个 YAML → 情报推送(录制于 shishi 命名时期,物料为时代锁定原样保留;现 CLI 为 myssia run → 收到推送卡片)" />

<sub>世事 三分钟 —— 一个 YAML → 情报推送。录制脚本与分镜:<a href="docs/demo/"><code>docs/demo/</code></a></sub>

**[⬇️ macOS .dmg](https://github.com/xinzhuzi/MYIA/releases/download/v0.0.16/myssia_0.0.16_aarch64.dmg)** · **[⬇️ Windows .msi](https://github.com/xinzhuzi/MYIA/releases/download/v0.0.16/myssia_0.0.16_x64.msi)** · [全部版本 Releases](https://github.com/xinzhuzi/MYIA/releases)

<sub>🛰 同门出品 · AI API 中转站:<a href="https://zhuzibaijia.online/"><b>诸子百家 · zhuzibaijia.online</b></a></sub>

<br/>
<br/>

<code>fetch</code> ➜ <code>classify</code> ➜ <code>dedup</code> ➜ <code>analyze</code> ➜ <code>enrich</code> ➜ <code>push</code>

<br/>
<br/>

**中文** · **[English](README.en.md)**

</div>

---

## 理念

所有情报需求 —— AI 资讯、股票异动、补货监控、显卡行情、羊毛优惠 —— 形状都一样:
**盯住一些源,留下重要的,忽略其余的,该叫你的时候叫你。** 现有工具各给一片:
一个爬虫、一个订阅源、一个 diff 监视器。世事 给的是全链路,配置驱动,并且天生
就是给 AI agent 开的。

任何情报品类 = **一份 YAML 文件**。世事 负责抓取(六级引擎自动降级)、分类去重、
打分(先关键词粗筛 —— 零 token;可选 LLM 精评),把重要的推到你的即时通讯。
YAML 本身由你的编码 agent 照规范现场生成:读 schema、写配置、`myssia test` 试抓
验证、凭 `myssia doctor` 输出自行修复失效源。

微信出站走桥:`weixin` 官方桥经本机 Hermes-Agent(缺它则该通道不可用——
世事 自身零微信凭据);另有 `weixin-local` 本机桥经 wechat-bridge 受管组件
(缺省关,详见[插件开发指南](docs/zh/write-a-plugin.md))。

**人做决策,AI 做其余。**

## 市面空白:为什么是 世事

| 现有工具 | 给你什么 | 缺什么 |
|---|---|---|
| Crawlab / Kestra | 爬虫/工作流编排 | 只是载具 —— 没有分类、去重、推送 |
| changedetection.io | 网站变更监控 | 只盯 diff —— 没有采集管线、分析与打分 |
| RSSHub | 把站点转成 RSS | 只做源转换 —— 没有过滤、打分与投递 |
| 单一用途盯盘工具(凭证/羊毛各一款) | 各覆盖一个细分品类 | 品类写死;换目标 = 换工具 |
| **世事** | **品类无关的采集→分类→分析→推送全链,配置驱动** | — |

## 核心亮点

<table>
<tr>
<td width="50%" valign="top">

🧠 **是情报,不只是爬虫**
七大类关键词分类器(零 token,以独立包
[`myssia-classifier`](https://pypi.org/project/myssia-classifier/) 发行)+ 可选 LLM 精评(价值/相关性/
可信度 0–10)。阈值分级路由:score ≥ 8 立即推,≥ 5 进早晚摘要,其余归档。

</td>
<td width="50%" valign="top">

🪜 **六级采集降级梯**
`direct_api` → `static_html` → `crawl4ai` / `firecrawl` → `Scrapling` →
隐身浏览器 → LLM 浏览器。源永不写死引擎:某一级失灵,下一级顶上,胜出引擎
按源记忆(存 SQLite —— 永不回写你的 YAML)。

</td>
</tr>
<tr>
<td width="50%" valign="top">

📬 **推送尊重你的注意力**
URL 键去重注册表 + 早/晚摘要槽位 —— 生产验证过的语义;同一条情报你永远不会
收到第二遍。通道 34 个:飞书卡片、Telegram、weixin-local(微信本机桥)、
macos_notify、bark 等。

</td>
<td width="50%" valign="top">

🔐 **凭据永不落明文**
凭据永不进 YAML —— 只允许 `env:VAR` / `keychain:myia/<scope>/<name>` 引用;
配置文件里出现明文凭据,加载即拒。落 macOS Keychain / Windows DPAPI。

</td>
</tr>
<tr>
<td width="50%" valign="top">

🤖 **天生 Agent 友好**
全量文档化的 12 节 YAML schema、处处有缺省值、每条命令都有 `--json`
(stdout 恒为恰好一份 JSON 文档)、结构化诊断 —— 每个接口都为 agent 可驱动
而设计,不只是给人用的。

</td>
<td width="50%" valign="top">

🔁 **反馈闭环自我调优**
对推送标记有价值 / 无价值(CLI 已可用;桌面卡片内按钮已交付(桌面对齐批次,已随
Releases);Telegram/飞书回调接收已就绪)—— 负反馈持续
回写,调优盯盘权重与阈值。

</td>
</tr>
<tr>
<td colspan="2" width="100%" valign="top">

⏰ **定时任务:到点跑一遍 + 发摘要**
`myssia cron serve` 常驻、桌面端内置 ticker、外接 crontab 手动 `cron tick`
—— 同一底座(tick 文件锁 + fire 认领互斥)多宿主共存;到点自动跑一遍品类
管线,运行摘要定向投递(本地留档 / 飞书 / Telegram 等平台 spec)。schedule
吃自然语言(`every monday 9am`)也吃 5 段 cron(POSIX 周几)。

</td>
</tr>
</table>

开箱即用:SQLite 单文件存储(无 Redis、无 Postgres、无常驻守护),保留期
自动清理 + 定期 VACUUM,进程内调度。

## 快速开始

> 💡 本公开仓是**产品门面与素材仓**(README/文档/截图);一键安装请走上方 Releases 二进制,源码获取通道以仓库公告为准。

```bash
git clone https://github.com/xinzhuzi/MYIA.git
cd MYIA
uv sync                     # uv workspace(主口径):一并装好 myssia 与 myssia-classifier
uv run myssia --version       # myssia 0.0.16
```

> 本仓是 uv workspace(`myssia-classifier` 是 workspace 成员):裸 `pip install -e .`
> 装不齐依赖,源码安装请用 `uv sync`;桌面用户直接用下方安装包,无需碰源码。

重型采集引擎是可选 extras;缺引擎时沿梯子优雅降级,给出结构化
`dependency_missing` 错误而不是崩溃:

```bash
uv sync --extra crawl4ai    # L3 JS 渲染引擎
uv sync --extra llm         # LLM 精评 / 事件聚合
uv sync --extra table       # 表格还原(rapid_table:截图表格 → metadata.tables)
uv sync --extra trafilatura # L2 正文兜底(无规则/规则失效源的单页正文抽取,MYIA_EXTRACT_FALLBACK=1 打开)
```

表格还原是自管 Python 环境的第一件**可选组件**:桌面端在 设置 → Python
环境 用「表格还原」组件开关往自管环境装钉版组件(零重打包、镜像覆盖生效),
品类 YAML 里以 `images.table: true` 开启;CLI 侧即上面的 `--extra table`。
缺装不阻管线(只降级写 `metadata.table_status`),`myssia doctor` 会以
`table_dependency_missing` 提前披露。

trafilatura 正文兜底同样缺装不拦链(无规则源照旧 `extract_required` 走降级
链),且装了也只是具备能力——还需 `MYIA_EXTRACT_FALLBACK=1` 显式打开
(CLI=命令行 env 前缀;已装 app=`launchctl setenv MYIA_EXTRACT_FALLBACK 1`
后重启;缺省关=既有源零行为差,细节见
[快速上手](docs/zh/getting-started.md))。

全局 env 之外还可**按源覆写**:品类 YAML 里
`engine_options.static_html.extract_fallback: true/false` 优先于全局环境变量
(优先级=源级 > 全局 `MYIA_EXTRACT_FALLBACK` > 缺省关,源级未设时与全局口径
逐字节一致),详表见 [schema 参考](docs/zh/schema.md) 的 sources `extract` 行。

### 自托管 Firecrawl 兜底(L3 渲染后端,可选)

L3 `firecrawl` 引擎的渲染后端不必买云端——也可以自托管(零美元,
渲染流量不出你的机器):部署步骤与配方见上游官方
[self-host 文档](https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md)
(SELF_HOST.md;我方不复刻配方)。引擎内置缺省端点就是标准口
`http://127.0.0.1:3002`,非标准口或远程机才设 `MYIA_FIRECRAWL_URL`;
自托管关鉴权,`MYIA_FIRECRAWL_API_KEY` 不设(云端 `api.firecrawl.dev`
才需要)。上游 server 是 AGPL-3.0——世事 只以服务消费接入(HTTP API
调用,零源码复制,同 RSSHub 先例),不 vendor 其代码;强反爬绕过/截图等
Fire-engine 能力为云端专属,自托管栈不含。世事 侧接线(cloud/自托管
端点三级解析、降级链)、cloud-only 缺口清单与历史复验回执见
[零成本接入 §4](docs/zh/zero-cost.md)。

跑第一个品类 —— 零凭据的完整配置就一个小文件:

```bash
cat > config/demo-min.yaml <<'YAML'
id: demo-min
name: Minimal demo
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
  builtin: false                 # demo items match no category; disable the filter
push:
  - channel: stdout              # zero-credential local verification
YAML

uv run myssia test config/demo-min.yaml --json            # 试抓:不入库、不推送
uv run myssia run config/demo-min.yaml --dry-run --json   # 全链演练,不推送
uv run myssia run config/demo-min.yaml                    # 正式跑;--loop 常驻调度
```

完整走读:[docs/zh/getting-started.md](docs/zh/getting-started.md)。

> 桌面安装包见下方「下载安装」(随 GitHub Releases 交付);CLI 用户按上文源码
> 安装,PyPI 发布暂停中。

## 下载安装(桌面应用)

安装包随 GitHub Releases 交付(当前 **v0.0.16**:macOS dmg 与 Windows msi 均带签名更新通道,「检查更新」可用)。

### macOS(Apple Silicon)

1. 下载
   [`myssia_0.0.16_aarch64.dmg`](https://github.com/xinzhuzi/MYIA/releases/download/v0.0.16/myssia_0.0.16_aarch64.dmg)
   (或到 [Releases](https://github.com/xinzhuzi/MYIA/releases) 取最新版),
   把 世事 拖入「应用程序」;
2. 首次打开:**在「应用程序」里右键 世事 →「打开」→ 再点「打开」**
   (或双击被拦后到 系统设置 → 隐私与安全性 → 点「仍要打开」);
3. 安装包未做 Apple 公证(公证需付费开发者账号)—— 代码完全开源可审计,
   每个安装包由 GitHub Actions 公开构建、日志可溯;右键打开一次即完成
   Gatekeeper 放行,之后正常双击启动;
4. **Python 运行环境首次配置**:运行时与依赖不随包分发(安装包因此显著更小)
   ——启动后到 设置 → Python 环境 点「开始配置」,在线下载钉版运行时
   ([indygreg/python-build-standalone](https://github.com/indygreg/python-build-standalone)
   `cpython-3.12.7+20241016`,sha256 随包清单钉死)并按锁版清单装依赖,装进
   数据根 `python/` 子目录;下载源与 PyPI 索引均可在设置页换成镜像(只换地址,
   不绕校验),首跑需联网。详见
   [快速上手 §1.1](docs/zh/getting-started.md)。

### Windows(x64)

安装包(msi)已随 GitHub Releases 交付(v0.0.3 起挂 `myssia_<版本>_x64.msi`):

1. 下载
   [`myssia_0.0.16_x64.msi`](https://github.com/xinzhuzi/MYIA/releases/download/v0.0.16/myssia_0.0.16_x64.msi)
   (或到 [Releases](https://github.com/xinzhuzi/MYIA/releases) 取最新版),
   双击安装;
2. 首次运行弹 SmartScreen「Windows 已保护你的电脑」时,点
   **「更多信息」→「仍要运行」**(未购买代码签名证书的如实代价,与 macOS
   右键打开同一口径);
3. **Python 运行环境首次配置**:与 macOS 同一链路——设置 → Python 环境 →
   「开始配置」,在线下载同一钉版运行时的 `x86_64-pc-windows-msvc` 等效件并
   按锁版清单装依赖;下载源与 PyPI 镜像同样可在设置页覆盖(详见
   [快速上手 §1.1](docs/zh/getting-started.md));
4. 数据根在 `%APPDATA%\MYIA`(资源管理器地址栏粘贴即达),自管 Python 装在
   数据根 `python\` 子目录;
5. 升级:设置页「检查更新」,msi 静默(passive)安装后自动重启
   (见 [desktop/UPDATER.md](desktop/UPDATER.md));旧版数据原样沿用零迁移,
   首启一次性引导进设置完成环境配置。

Python 环境配置完成后,装机首跑自动种子零凭据演示件 `myssia-demo`
(GitHub 新星榜)与各品类脱敏模板——**引擎开箱即用,品类照模板自填**,
第一次点「运行第一个插件」就出真数据;
设置页「检查更新」走签名更新通道,应用更新后依赖有漂移时在同一分区一键
「同步依赖」(幂等,已装跳过)。

装完不会写 YAML 也能用:demo 品类一键跑、五步建源向导一步一步带你把第一个
源配好——[中文向导](docs/zh/source-wizard.md) · [English](docs/en/source-wizard.md)。

桌面十四屏(下方五张截图均为 demo 品类真实抓取数据;十四屏里还有 messaging
微信本机工作台、proxy-pool 代理池、credhunter 凭证猎手、sources 五步建源
向导等新屏):

<p align="center">
<img src="docs/screenshots/dashboard.png" width="400" alt="仪表盘:品类运行状态与最近采集概览" />
<img src="docs/screenshots/feed.png" width="400" alt="信息流:去重后的情报条目" />
</p>
<p align="center">
<img src="docs/screenshots/sources.png" width="260" alt="源管理:品类与源配置" />
<img src="docs/screenshots/logs.png" width="260" alt="日志:运行日志回溯" />
<img src="docs/screenshots/settings.png" width="260" alt="设置:凭据入钥匙链 + 软件更新" />
</p>

## AI 原生闭环

把闭环交给任何编码 agent(Claude Code、Cursor……)。
[Agent Skill](skill/SKILL.md) 是自包含速查表,一条命令安装
(`myssia skill install --agent claude`;也支持 `cursor` / `zcode`,
`--path` 自定义目录,`--link` 以链接代替复制):

```
myssia skill install --agent claude         # 一次性:速查表 → ~/.claude/skills/myia/
myssia init --json                          # 拿结构化信息清单
( agent 现场写 <id>.yaml )                # 对照 12 节 schema
myssia test config/<id>.yaml --json        # 试抓,核对字段与去重键
myssia run config/<id>.yaml --dry-run      # 演练
myssia run config/<id>.yaml --loop         # 常驻调度
myssia doctor --json                        # 拿 findings;agent 自修后复查
```

## 架构

```
用户层          myssia CLI · Agent Skill · 桌面应用(Tauri,v1.1)· Web UI(规划中)
                     │
编排层          流水线:fetch → classify → dedup → analyze → enrich → push
                (进程内 APScheduler + asyncio;无外部编排器、无守护进程)
                     │
品类层          一个品类一份 YAML · 品类自填(附 example 模板)· 市场插件
                desktop 级 = 进程内 adapter(零 docker)· remote/server 级走端点
                (代理池 · 变更监控 · OSINT · 抖音 · maxun · …)
                     │
采集引擎        L1 direct_api → L2 static_html → L3 crawl4ai ⇄ firecrawl
                → L4 scrapling → L5 stealth_browser → L6 llm_browser
                (自动降级链;胜出引擎按源持久化)
                链外显式引擎:reddit(官方 Data API,凭据可选)· credhunter
                · zenrows/scraperapi(gated 付费 SaaS)· searxng(自托管
                元搜索关键词日报)——auto 永不隐式路由
                     │
分析            内置七类关键词分类器(myssia-classifier)
                + 可选 LLM 精评(价值/相关性/可信度,0–10)
                     │
存储            SQLite 单文件 · 保留期 + VACUUM · 变更基线
                     │
推送            飞书卡片 · Telegram · weixin-local · macos_notify · bark
                等 34 通道,阈值分级路由(立即 / 早晚摘要 / 归档)+ 反馈闭环
```

## 文档

双语文档随仓库发行,由测试(`tests/test_docs.py`)锁住与代码一致 —— 每个
示例 YAML 都过真实 schema 入口加载,zh/en 两棵树结构上不许漂移:

| | English | 中文 |
|---|---|---|
| Getting started | [docs/en/getting-started.md](docs/en/getting-started.md) | [docs/zh/getting-started.md](docs/zh/getting-started.md) |
| Write a plugin | [docs/en/write-a-plugin.md](docs/en/write-a-plugin.md) | [docs/zh/write-a-plugin.md](docs/zh/write-a-plugin.md) |
| Schema reference | [docs/en/schema.md](docs/en/schema.md) | [docs/zh/schema.md](docs/zh/schema.md) |
| FAQ(伦理与边界) | [docs/en/faq.md](docs/en/faq.md) | [docs/zh/faq.md](docs/zh/faq.md) |
| 零成本接入(自托管栈) | [docs/en/zero-cost.md](docs/en/zero-cost.md) | [docs/zh/zero-cost.md](docs/zh/zero-cost.md) |
| Scheduled jobs (cron) | [docs/en/cron.md](docs/en/cron.md) | [docs/zh/cron.md](docs/zh/cron.md) |
| Windows 本地构建(MSI) | [docs/en/build-windows.md](docs/en/build-windows.md) | [docs/zh/build-windows.md](docs/zh/build-windows.md) |
| 五步建源向导(不想学 YAML 先读这份) | [docs/en/source-wizard.md](docs/en/source-wizard.md) | [docs/zh/source-wizard.md](docs/zh/source-wizard.md) |
| 微信本机工作台(wechat-local) | [docs/en/wechat-local.md](docs/en/wechat-local.md) | [docs/zh/wechat-local.md](docs/zh/wechat-local.md) |
| 凭证猎手(cred-hunter) | [docs/en/cred-hunter.md](docs/en/cred-hunter.md) | [docs/zh/cred-hunter.md](docs/zh/cred-hunter.md) |
| 代理池(proxy-pool) | [docs/en/proxy-pool.md](docs/en/proxy-pool.md) | [docs/zh/proxy-pool.md](docs/zh/proxy-pool.md) |
| **微信桥接协议(单册,随 `docs/` 根发行,zh/en 共用)** | | |
| wechat-http-api:本地库 HTTP API | — | [docs/wechat-http-api.md](docs/wechat-http-api.md) |
| wechat-keyprovider:取钥器子进程协议 | — | [docs/wechat-keyprovider.md](docs/wechat-keyprovider.md) |
| wechat-mediadecrypt:媒体解密件子进程协议 | — | [docs/wechat-mediadecrypt.md](docs/wechat-mediadecrypt.md) |

面向 agent 的浓缩参考:[`skill/SKILL.md`](skill/SKILL.md)。

## 路线图

核心流水线已实现且有测试覆盖 —— 6900+ 测试跑在 CI 里,无一条碰真实网络。
桌面端已可日常使用(数据通路统一、开箱 demo 与品类模板随包、签名更新通道,
v0.0.1 起随 Releases 交付);桌面卡片内反馈按钮、设置反馈开关、采集量趋势
已随桌面对齐批次交付;Windows 安装包(msi)已随 v0.0.3 挂上 Releases
(构建流水线 10-04 首绿)。

| 里程碑 | 范围 | 状态 |
|---|---|---|
| v0.1 骨架 | 核心流水线、direct_api/static/firecrawl 引擎、12 节 schema、变更指纹、分类器、飞书路由 | ✅ 已交付 |
| v0.2 可用 | SQLite 存储 + 注册表 + 保留期、完整 CLI(init/test/list/doctor)、crawl4ai L3、LLM 精评 + 预算护栏、docker compose、Telegram、钥匙链凭据 | ✅ 已交付 |
| v0.3 生态 | Agent Skill、插件市场(本地/远端双模)、Scrapling L4、反馈闭环(CLI + 回调接收) | ✅ 已交付 |
| v0.4 深水区 | stealth_browser L5、llm_browser L6、趋势基线、事件聚合 | ✅ 已交付 |
| v1.0 发布 | 双语文档、演示物料、GitHub 门面、公开交付 | ✅ 已交付 |
| v1.1 桌面优先 | Tauri 桌面壳(Python 核心以 sidecar 嵌入)、五屏 UI、进程内插件级;卡片内反馈按钮/settings 反馈开关/采集量趋势已交付(桌面对齐批次落地,已随 Releases) | ✅ 已交付 |
| v1.1.1 通路修复 | 桌面数据通路统一(MYIA_HOME/品类模板与 demo 随包/首跑种子)、开箱 demo 品类、签名更新通道(检查更新 + 自动安装) | ✅ 已交付 |
| Web UI | 同一核心上的浏览器前端 | 📋 规划中 |

## 伦理与边界

世事 默认讲礼貌:尊重 robots.txt、限速采集、凭据永不落明文。要求真人验证
(验证码、手机号)的源会以结构化错误失败 —— 世事 不做绕过。完整表述见
[中文 FAQ](docs/zh/faq.md) · [English FAQ](docs/en/faq.md)。

## 🛰 同门出品 · 诸子百家 AI API Gateway

同一作者出品的 **AI API 中转站**:[zhuzibaijia.online](https://zhuzibaijia.online/) —— 统一网关接入大模型 API。做 AI 应用、跑 agent、搭自动化流水线,都需要一条稳定顺手的推理补给线;诸子百家为此而设。

**👉 [立即访问 zhuzibaijia.online](https://zhuzibaijia.online/)**

---

## 社区

**世事 帮到你的话,给仓库点个 ⭐ Star —— 纯开源项目最有效的支持方式,也让更多人看到它。**

- Bug 与需求:[issue 模板](.github/ISSUE_TEMPLATE/)
- 参与贡献:[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略与凭据处理设计:[SECURITY.md](SECURITY.md)

## 致谢

世事 自有代码全部原创(MIT),但站在巨人的肩膀上 —— 以依赖、插件后端与设计
参考的形式接入:

- [crawl4ai](https://github.com/unclecode/crawl4ai) —— L3 采集引擎(可选依赖)
- [Scrapling](https://github.com/D4Vinci/Scrapling) —— L4 自适应反爬引擎(可选依赖)
- [Firecrawl](https://github.com/firecrawl/firecrawl) —— L3 云端/自建渲染后端(可选依赖,以 API 调用)
- [Skyvern](https://github.com/Skyvern-AI/skyvern) —— L6 LLM 浏览器兜底(可选依赖)
- [rapid-table](https://github.com/RapidAI/RapidTable)(RapidAI 家族)—— 表格还原组件(可选依赖,`myssia[table]`)
- [trafilatura](https://github.com/adbar/trafilatura) —— L2 正文兜底组件(可选依赖,`myssia[trafilatura]`,开关 `MYIA_EXTRACT_FALLBACK`)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io) —— 源管理与 diff 交互参考,`myssia-monitor` 插件后端
- [RSSHub](https://github.com/DIYgod/RSSHub) —— 「一切皆源」的哲学参考
- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) —— `myssia-proxy` 插件后端
- [Reddit Data API](https://www.reddit.com/dev/api/) —— `reddit` 引擎的官方授权通道
  (OAuth2,凭据可选;官方口径:robots.txt 面向搜索引擎而非 Data API 用户)
- [SearXNG](https://github.com/searxng/searxng) —— `searxng` 引擎的自托管元搜索
  聚合后端(AGPL,仅服务消费零代码复用;关键词日报,起栈指引 docs/zero-cost §5)
- [Photon](https://github.com/s0md3v/Photon) —— `myssia-osint` 插件后端(以 git 子模块引入)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— `myssia-douyin` 插件后端
- [Maxun](https://github.com/getmaxun/maxun) —— `myssia-maxun` 插件后端
- [Tauri](https://github.com/tauri-apps/tauri) —— 桌面壳(Python 核心以 sidecar 嵌入)

除明确标注的 git 子模块外,不复制任何上游源码进本仓库;依赖接入策略见
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE) © 2026 xinzhuzi
