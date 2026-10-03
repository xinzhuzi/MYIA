# 免费层供应商地图:按 MYIA 决策面蒸馏 free-for.dev

- 任务:10-03-free-tier-supplier-map(research)· 快照:2026-10-03(同日二次核实:决策面三全部 ○ 项因 zero-cost-setup 引用而核,结果见该表)
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
| 智谱 GLM-4-Flash / GLM-4V-Flash | 定价页 GLM-4V-Flash 与 GLM-4-Flash-250414 输入/输出单价均标注「免费」(0 元,非限时);base_url `https://open.bigmodel.cn/api/paas/v4`(官方 OpenAI 兼容文档);限速数值不公开,登录账户速率限制页查看;caveat:模型总览页标上下文 16K 而定价页标 4K,以定价页为准待复核 | 中文大厂,免费政策已多年 | — | **高**:中文用户零成本接入首选;注意 MYIA 看图云端缺省模型 `glm-4.6v` 为付费档、精评缺省 `glm-4-flash` 即免费档 | ●(2026-10-03 官网一手:docs.bigmodel.cn/cn/guide/start/pricing) |
| Google AI Studio(Gemini) | 免费层存在;**2026-10-03 复核:官方 rate-limits 页已不公布免费档具体数字,须登录 aistudio.google.com/rate-limit 自查**(此前记录的「5 请求/分、20 请求/天」不再有公开出处);OpenAI 兼容 base_url `https://generativelanguage.googleapis.com/v1beta/openai/` 已核(官方 openai 兼容文档) | 稳定但配额紧(曾更宽) | — | 中:轻量日批够用,重批/视觉不够;文档标注配额 | ●(存在性与 base_url 已核;具体数字官方已不公开) |
| Gonka Broker | 100 万+ token/月,OpenAI 兼容 | 新兴 | — | 中:额度大方;去中心化 GPU 延迟有波动 | ● |
| SiliconFlow / DashScope(Qwen) | **2026-10-03 核:SiliconFlow 注册赠额在官方公开渠道(官网/注册页/财务 FAQ)已无明文,金额与有效期不可核实,不得对用户承诺;免费模型清单亦未核(定价页需登录)**;OpenAI 兼容 base_url `https://api.siliconflow.cn/v1` 已核(官方 quickstart);DashScope 未核 | 营销型(会耗尽) | — | 中:可列,但必须标「赠额用尽即止」;赠额金额不可承诺(zero-cost.md 据此未列入) | ○(赠额与免费档未核到,保留;base_url 一项已核) |
| Groq / Mistral | Groq Free plan 文本模型(openai/gpt-oss-120b 等)30 RPM、1000 请求/天、8K TPM、200K TPD,限速按组织计(console.groq.com/docs/rate-limits);Mistral 免费层为新账户默认(无需信用卡),定价页标注含 $10/月 API credits(mistral.ai/pricing) | 稳定;Mistral 赠金为月度营销型 | — | 中:备选池;enrich 适用 | ●(2026-10-03 官网一手;caveat:Mistral 免费层具体 RPS/TPM 未核到公开数字,须登录 Admin Panel Limits 页自查;Groq 全数已核) |
| Portkey / Keywords AI(AI 网关) | 免费层:网关+日志+多商故障转移 | 新兴配套 | — | 低:个人用户用不上网关;enrich 调试期可用 | ● |

用户文档取材建议(零成本接入指引):本地 mlx-vlm(默认)→ OpenRouter `:free` / GLM-4V-Flash(云端免费)→ Gemini(轻量批)。**已交付:`docs/zh/zero-cost.md`(2026-10-03,getting-started.md 已挂链;en 版按 PRD 后置)。**

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

## 后续出口(grill 决议 2026-10-03,五问全按推荐)

1. **出口一 → 已立任务 `10-03-zero-cost-setup`**(用户零成本接入指引,docs/zh|en/zero-cost.md,zh 先 en 后;其 PRD 硬约束:落笔前核完决策面三全部 ○ 项并回写本文件升 ●)
2. **出口二 → 已落 spec `.trellis/spec/domain/connector-selection.md`**(「云端连接器必须有免费路径」硬规则 + 信号分级用法;根 spec/index.md 已挂)
3. **出口三 → 入池不立项(Q3c)**:排期权留主人;若立项,首选 Discord/Slack webhook(成本最低、● 已核)

### ○ 项核实机制(Q4a:引用时核)

- 时机:某 ○ 项要写进用户文档或立项时才核,不为核而核(免费层时效短,预核会过期)
- 通道:zread 读官方仓库/文档 + 官网,双通道交叉(本机 GitHub 直连不稳,见日志 2026-10-03)
- 回写:核完回写本文件对应条目升 ●,并更新文首快照日期

### 档案形态与任务收尾(Q5a)

- 本文件是**活档案**:随引用时核持续保鲜,不设定期复审周期
- `10-03-free-tier-supplier-map` 任务在出口一(zero-cost-setup)执行完成后 archive;届时本文件随任务目录进 `archive/` 仍可查;版本级复审(v1.2+)按需重开新任务,不背周期债
