# 免费层供应商地图:按 MYIA 决策面蒸馏 free-for.dev

- 任务:10-03-free-tier-supplier-map(research)· 快照:2026-10-03
- 情报源:[ripienaar/free-for.dev](https://github.com/ripienaar/free-for-dev)(139k★,当日仍有 PR 合入;Docsify 站点 [free-for.dev](https://free-for.dev))
- 情报边界:该仓库整体**无 LICENSE** → 本文档只记事实要点与链接,不搬运清单原文;其仓库明令禁止 AI 贡献(与我们无关,只读不投)
- 核实标记:**●**=本轮经 zread 读其当前文档核实(快照 2026-10-03);**○**=清单收录或社区常识,**写入用户文档前必须二次核实**
- 元风险(先读再查表):清单自身记录的翻车案例——Fly.io 新用户免费层已取消、X API 免费层彻底弃用、免费层可新增信用卡墙、服务可倒闭;清单无自动质量门禁,小众条目会混入。**免费层是结构性营销手段("free tier trap"),不是承诺。**

## 选型总规则(建议写进后续引擎/通道选型 spec)

1. **本地/自托管优先**:crawl4ai、scrapling、mlx-vlm、ntfy 自托管、GlitchTip 自托管——零依赖、免翻车,与 MYIA local-first 哲学同构
2. **云端连接器必须有「免费路径」**(免费层或自托管替代):BYO-key 架构下这是用户装机门槛,建议作为选型硬规则
3. **OpenAI 兼容端点优先**:MYIA vision/enrich 客户端换 `base_url` 即可接入(源文档原话:只需替换 base_url 即可把任何 OpenAI SDK 客户端指向它们)
4. **新兴聚合器(XFlux 型)只做可选后端,不做默认**:高收益高波动

## 决策面一:push 通道(锚点:src/myia/push/directory.py 通道目录 + 定向投递)

现状:telegram ✓、feishu ✓(b3c8084)。候选按建议接入顺序排列:

| 服务 | 免费层快照 | 信号 | 自托管/本地 | MYIA 适配 | 核实 |
|---|---|---|---|---|---|
| Discord webhook | 消息 API 免费(平台特性) | 长期稳定 | — | **高**:实现成本最低(webhook 即通),海外用户覆盖广 | ● |
| Slack webhook | 免费层含消息 API | 长期稳定 | — | 中:同 webhook 模式,偏企业场景 | ● |
| ntfy | 公共实例 ntfy.sh 免费;MIT 开源 | 极稳(开源) | ntfy 自托管 | **高**:极简 HTTP push,移动端齐全,自托管免翻车 | ○ |
| Bark(iOS) | 免费;bark-server 开源 | 极稳(开源) | bark-server | **高**:中文 iOS 用户刚需;**不在 free-for.dev,需补核** | ○ |
| Server酱 / WxPusher(微信) | 免费配额(条/天级,较紧) | 配额紧 | — | **高**:中文微信场景刚需;**不在 free-for.dev,需补核** | ○ |
| AWS SES(邮件) | 3000 封/月,仅前 6 个月 | **会到期** | 自建 SMTP | 中:邮件通道候选;文档必须标 6 个月时限 | ● |
| AWS SNS | 100 万发布/月(永久) | 云厂商级稳定 | — | 中:需 AWS 账号+信用卡,中文用户不友好 | ● |
| Azure Notification Hubs | 100 万推送 | 云厂商级 | — | 低:同上,且偏移动推送场景 | ● |

顺序建议:Discord/Slack webhook(零成本高频)→ ntfy/Bark(自托管 + 中文移动端)→ 邮件(标明时限)→ 云厂商 SNS(账号门槛,最后)。

## 决策面二:engines 抓取(锚点:src/myia/engines/registry.py)

现状:static_html、direct_api、llm_browser、stealth_browser、crawl4ai、scrapling、firecrawl(10-01 落地)——本地引擎已覆盖大半场景,云端引擎定位是「可选增强」:

| 服务 | 免费层快照 | 信号 | 自托管/本地 | MYIA 适配 | 核实 |
|---|---|---|---|---|---|
| Firecrawl 云端 | 一次性 500 credits | 营销型(一次性额度) | firecrawl 开源可自部署 | **高**:引擎已支持,云端只需文档指路 | ○ |
| Browserless | 免费额度(单位制,历史有收缩) | 收缩史 | browserless 开源 | 中:stealth_browser 的云端替代 | ○ |
| Zenrows / ScraperAPI / Crawlbase | ~1000 credits/月级 | 营销型 | — | 中:反爬场景备用池 | ○ |
| XFlux 型聚合器 | 免费读取推文数据(免信用卡) | 新兴,高波动 | — | 低-中:若做 X/Reddit 源再评估,优先于官方付费 API | ●(存在性与模式) |

结论:本地引擎(crawl4ai/scrapling/stealth_browser)是免费层陷阱的根治方案;Firecrawl 已领先落地,无需追新聚合器。

## 决策面三:vision + enrich LLM 端点(锚点:src/myia/vision/client.py OpenAI 兼容,本地通道 api_key=None 走 mlx-vlm)

| 服务 | 免费层快照 | 信号 | 自托管/本地 | MYIA 适配 | 核实 |
|---|---|---|---|---|---|
| 本地 mlx-vlm / Ollama | 无限(本机) | 根治 | —(即是) | **默认推荐**,已是产品现状 | ● |
| OpenRouter `:free` 模型 | DeepSeek R1/V3、Llama、Moonshot 等,限速制 | 模型池随供需波动 | — | **高**:OpenAI 兼容,换 base_url 即接;enrich 文本主力候选 | ● |
| 智谱 GLM-4-Flash / GLM-4V-Flash | 文本+视觉免费 | 中文大厂,免费政策已多年 | — | **高**:中文用户零成本接入首选;**不在 free-for.dev,需补核当前政策** | ○ |
| Google AI Studio(Gemini) | 5 请求/分、20 请求/天(Flash 档) | 稳定但配额紧(曾更宽) | — | 中:轻量日批够用,重批/视觉不够;文档标注配额 | ● |
| Gonka Broker | 100 万+ token/月,OpenAI 兼容 | 新兴 | — | 中:额度大方;去中心化 GPU 延迟有波动 | ● |
| SiliconFlow / DashScope(Qwen) | 注册赠送 / 限时免费 | 营销型(会耗尽) | — | 中:可列,但必须标「赠额用尽即止」 | ○ |
| Groq / Mistral | 免费层(限速制) | 稳定 | — | 中:备选池 | ○ |
| Portkey / Keywords AI(AI 网关) | 免费层:网关+日志+多商故障转移 | 新兴配套 | — | 低:个人用户用不上网关;enrich 调试期可用 | ● |

用户文档取材建议(零成本接入指引):本地 mlx-vlm(默认)→ OpenRouter `:free` / GLM-4V-Flash(云端免费)→ Gemini(轻量批)。

## 决策面四:项目运营(开源项目自身,非用户侧)

| 用途 | 首选(免费) | 快照 | 核实 |
|---|---|---|---|
| 文档站/落地页 | GitHub Pages / Cloudflare Pages | 公开仓库免费,长期稳定(常识级) | ● |
| 崩溃上报(若做) | GlitchTip 自托管(完全免费)或 Sentry 5k 错误/月 | Sentry 开发者计划多年稳定;Rollbar 5k/月(30 天留存)、GlitchTip 1k 事件/月在列 | ● |
| 拨测/状态页(若做) | UptimeRobot 50 监控 @5min | 稳定 | ● |
| i18n(docs 已 en/zh 双语) | Crowdin 开源计划 / Localazy | 开源项目免费计划存在多年 | ○(本轮未核到当前条款) |
| LLM 调用可观测(若做 enrich 调试) | Arize AX 25k spans/月(1GB 接入)或 telemetry.dev 10k spans/月(7 天留存) | 新兴 | ● |
| 子域名(若需要) | is-a.dev / js.org | 活跃 | ●(存在性) |

## 趋势与反情报(影响路线图)

- **官方 API 收紧 → 免费层聚到 wrapper/聚合器**:X API 弃免费层,XFlux 补位;此模式正快速扩张(Reddit/LinkedIn 同趋势)。MYIA 未来做收紧源时,优先评估聚合器而非官方付费 API
- **AI 免费层通胀**:DeepSeek(无限制、无需注册)、Gemini(免费 1M 上下文)、Claude(免费 Sonnet)竞争,把真实模型能力压进 $0 层——MYIA 用户的免费可选面只会变宽,「零成本接入」是长期卖点而非权宜
- **免费层陷阱结构性存在**:凡把免费额度写进用户文档的条目,一律带快照日期 +「以官网为准」;○ 项引用前逐条二次核实

## 后续取材出口(非本任务范围)

1. 用户零成本接入指引:取材自决策面三,挂 docs/zh|en/getting-started.md(引用前二次核实全部 ○ 项)
2. 引擎/通道选型 spec 增补硬规则:「云端连接器必须有免费路径」(可入 .trellis/spec 对应层)
3. push 通道扩展候选池:Discord/Slack webhook、ntfy、Bark、Server酱(前两个成本最低,可直接立子任务)
