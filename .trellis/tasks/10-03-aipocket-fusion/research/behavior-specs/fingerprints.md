# 供应商指纹库规格(fingerprints)

> 行为规格——AGPL 只读观察产出,实现时只认本规格不回看源码。
> 源码:aipocket-main(Rust)。证据指针 file:line 均相对该仓库。
> 重要修正:任务假设"21 个供应商包",实测 **PACKS=20**(packs.rs),**SPECS=25**(provider.rs,另加 unknown 兜底)。

## 1. 数据结构
- `ProviderPack`(discovery/packs.rs:2-8)——发现层查询包:`id`、`fofa_queries[]`、`shodan_queries[]`、`github_terms[]`,全静态字符串数组。**不含** regex/探测端点/auth 风格;那些在验证层。
- `ProviderSpec`(prober/provider.rs:12-20)——验证层规格:`name`、`category`(international|domestic|gateway|coding_agent|cloud)、`domain_suffixes[]`(host 匹配用)、`key_prefixes[]`(密钥前缀匹配用)、`protocol`(`ProtocolFamily`:OpenAiCompatible|Anthropic|Gemini|Vertex|AwsBedrock,provider.rs:3-10)、`models[]`(默认探测模型)、`official_api_url`。无内嵌示例请求;请求形状由 validator/balance 代码按 protocol+provider 硬编码(见 credcheck.md §2/§5)。
- 解析规则 `resolve(apiurl, apikey)`:域名优先(reason=domain),后密钥前缀(reason=key_prefix),否则 UNKNOWN(models 默认 gpt-4o-mini、空 URL)(provider.rs:26-34,283-308)。

## 2. 发现层 20 包清单(id + 一行概括,packs.rs:9-197)
| id | 概括 |
|---|---|
| openai | fofa body="sk-";shodan http.html:sk-;github `sk- filename:.env` |
| anthropic | 三源均锚定 `sk-ant-` |
| gemini | GEMINI_API_KEY / GOOGLE_API_KEY+AIza / generativelanguage 域名,三组查询 |
| xai | XAI_API_KEY、api.x.ai、grok-4.6/4.7(新模型名即指纹) |
| qoder | QODER_PAT / QODER_PERSONAL_ACCESS_TOKEN / api.qoder.com / Cantus+Qoder |
| kiro | KIRO_API_KEY、`ksk_`+kiro |
| aws_bedrock | AWS_BEARER_TOKEN_BEDROCK、bedrock-runtime+amazonaws.com |
| cursor | CURSOR_API_KEY、`crsr_`+api.cursor.com |
| windsurf | WINDSURF_SERVICE_KEY、CODEIUM_SERVICE_KEY、GetTeamCreditBalance+service_key |
| azure_openai | fofa/shodan 锚 openai.azure.com;github 锚 AZURE_OPENAI_API_KEY |
| cohere | 仅 github:COHERE_API_KEY |
| deepseek | 仅 github:DEEPSEEK_API_KEY |
| fireworks | 仅 github:FIREWORKS_API_KEY |
| glm | 仅 github:ZHIPUAI_API_KEY |
| kimi | 仅 github:MOONSHOT_API_KEY |
| longcat | 仅 github:LONGCAT_API_KEY |
| minimax | 仅 github:MINIMAX_API_KEY |
| qwen | 仅 github:DASHSCOPE_API_KEY |
| replicate | 仅 github:REPLICATE_API_TOKEN |
| together | 仅 github:TOGETHER_API_KEY |
FOFA/Shodan 之外另有 legacy 查询集:31 条直接凭证查询 + 25 组产品查询(LiteLLM/Dify/One-API…,legacy_queries.rs:1-181),产品命中打 `_product` 标签(:183-222)。

## 3. 验证层 25 供应商 SPECS(provider.rs:35-269)
openai、anthropic、deepseek、kimi、glm、minimax、nvidia、ksyun、longcat、qwen、siliconflow、cohere、replicate、together、fireworks、groq、openrouter、xai、qoder、kiro、aws_bedrock、cursor、windsurf、gemini、azure_openai。
其中 packs(发现)与 SPECS(验证)的差集:SPECS 多 nvidia/ksyun/siliconflow/groq/openrouter(无 GitHub 猎取查询);packs 多 azure_openai 查询但 azure_openai 的 official_api_url 为空(provider.rs:261-268)。

## 4. 代表包 A:openai(provider.rs:36-44)
- category=international;domain_suffixes=[openai.com, oaiusercontent.com];key_prefixes=[sk-proj-, sk-admin-, sk-svcacct-];protocol=OpenAiCompatible;models=[gpt-4o-mini];official_api_url=https://api.openai.com/v1。
- 行为串联:猎取正则 `sk-…{16,}` 命中(github_artifacts.rs:252)→ apiurl 留空或取命中页 URL → 验证 GET {base}/v1/models Bearer(specialized.rs:76-85)→ 余额三段式 credit_grants/subscription/models(credcheck.md §5)→ key 前缀细分 kind(project/admin/service_account/session/ordinary,balance.rs:544-556)。
- 注意 `sk-` 泛前缀不进 key_prefixes(会吞掉所有 sk-* 网关键);域名兜底才是主通道。

## 5. 代表包 B:openrouter(网关型,provider.rs:184-192)
- category=gateway;domain_suffixes=[openrouter.ai, together.xyz? 否——openrouter 仅 openrouter.ai];key_prefixes=[sk-or-];protocol=OpenAiCompatible;models=[openai/gpt-4o-mini];official_api_url=https://openrouter.ai/api。
- 行为串联:猎取正则 `\bsk-or-v1-[a-f0-9-]{30,}`(pipeline.rs:11)→ 验证两段式:先 `/api/v1/auth/key`(401 即 rejected,不再打 models),成功后 `/api/v1/models`(specialized.rs:106-127)→ 余额 `/api/v1/auth/key` 的 limit_remaining,缺失时降级 `/api/v1/credits`,free_tier→0(balance.rs:483-543)。
- 端点规范化特例:openrouter.ai 域强制 api_base 后缀 `/api`(endpoint.rs:57),`/v1/models` 结尾会被剥离重挂。

## 6. 代表包 C(补充):windsurf(coding_agent,provider.rs:242-250)
- domain_suffixes=[server.codeium.com, windsurf.com, codeium.com];无 key_prefix(服务键无特征前缀,靠上下文变量名 `windsurf_service_key`/`codeium_service_key` 归因,pipeline.rs:79-94);余额是全库唯一 POST 探测:`POST /api/v1/GetTeamCreditBalance` body {"service_key"}(balance.rs:910-947)。

## 7. 指纹匹配的执行位置(三处,各不同正则集)
1. **猎取时(GitHub 泳道)**:单一联合正则十大密钥族 + 前缀→apiurl 静态映射(github_artifacts.rs:221-279;详见 ghhunt.md §4)。
2. **猎取时(FOFA/Shodan 泳道)**:`KEY_PATTERNS` 17 条更细正则(sk-or-v1- 带 hex 约束、pplx-/hf_/key_/JWT eyJ…/32hex.16alnum 等,pipeline.rs:9-32)+ 上下文变量名→供应商提示表 `provider_hint`(含 grok→xai、zhipu/bigmodel→glm、dashscope→qwen 等 30 项,analyzer.rs:713-770)+ 变量名后缀剥离(_API_KEY/_TOKEN/…,analyzer.rs:697-712)+ 官方 URL 默认表 `provider_default`(analyzer.rs:785-811)。噪声过滤:长度<15、NOISE_SUBSTRINGS(example/dummy/your-key/sk-mocha… 39 项)、连续 8 字母字母序段(pipeline.rs:37-77,360-374)、blocked_key_format(gocspx-/akia/纯 hex32,pipeline.rs:376-382)、同 key 出现于 >5 个不同位置剔除(prefilter,pipeline.rs:183-205)。
3. **验证/余额时**:`ProviderRegistry.resolve`(域名→前缀→unknown,provider.rs:283-308)+ specialized 的前缀改道(specialized.rs:177-207)。产品漏洞探测引擎另有 4 族子集正则(engines.rs:699-710)。

## 8. URL 规范化(验证前置,core/endpoint.rs + url_sanitize.rs)
- `canonicalize_endpoint(raw, provider)`:补 https://;host 小写、去尾点;端口仅保留非默认(https≠443/http≠80);官方矩阵强制路径(openai→/v1、deepseek→""、kimi→/v1、glm→/api/paas/v4、nvidia/ksyun/xai→/v1、openrouter→/api、qoder/cursor→""、windsurf→/api/v1、minimax→/v1、longcat→/anthropic 或 /openai 按路径判定,endpoint.rs:50-69);非官方域名剥操作后缀(/chat/completions /messages /models /user/balance /users/me/balance /token_plan/remains;`/v1/models` 整体剥成根,endpoint.rs:10-17,79-101)。产出 {api_base, origin}。
- `sanitize_origin`:剥路径、scheme/host 小写、留显式端口(url_sanitize.rs:5-29);`host_key`=host:port(:31-37);`honeypot_group_key`:IP 原样,域名取末两级(蜜罐聚类,:38-51)。

## 未核实
- `product_specs.rs`(prober,270 行)未读——疑为 migrated_specs 的另一份规格来源,与 35 条产品探测规格的关系未确认。
- packs 与 SPECS 的 id 差集(siliconflow/groq/nvidia/ksyun 无 GitHub 查询)是刻意取舍还是滞后,源码无说明。
