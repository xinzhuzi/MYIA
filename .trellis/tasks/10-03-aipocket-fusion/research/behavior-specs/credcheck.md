# 凭证验证 + 余额探测规格(credcheck)

> 行为规格——AGPL 只读观察产出,实现时只认本规格不回看源码。
> 源码:aipocket-main(Rust)。证据指针 file:line 均相对该仓库。

## 1. 总体顺序(scanner.rs:330-610)
管线相位:discovery → extract(regex+GPT 归因)→ **validate(批量,批 500/并发 20,scanner.rs:430-436;config.rs:147-149)** → GPT recheck → finalize(蜜罐/格式门控)→ **balance(仅对 finalize 后 valid+suspicious 集合,scanner.rs:528-560)**。即:models 探测是验证本体(不是前置),余额在验证之后按需做。增量模式可跳过已验证凭证(dedup 缓存,scanner.rs:439-450)。

## 2. Validator.validate(prober/validator.rs:17-135)
1. `ProviderRegistry.resolve(apiurl, apikey)`:先按 URL host 域名后缀匹配(reason="domain"),否则按密钥前缀匹配(reason="key_prefix"),否则 unknown(provider.rs:283-308)。aws_bedrock 域名特判:host 以 `.amazonaws.com` 结尾且以 `bedrock.`/`bedrock-` 开头(provider.rs:270-278)。
2. base = apiurl 为空则取 spec.official_api_url;仍为空 → `validation_state="rejected"`,error="no API URL"(validator.rs:45-49)。
3. **专用验证**(specialized.rs:39-176,覆盖 anthropic/gemini/azure_openai/openai/qoder/cursor/openrouter/aws_bedrock):
   - 密钥前缀命中官方族时强制改道官方 URL(openai/anthropic/gemini/openrouter,specialized.rs:177-207)。
   - 端点:anthropic→`GET {base}/v1/models`(x-api-key + anthropic-version: 2023-06-01);gemini→`GET {origin}/v1beta/models?key=`;azure_openai→`GET {base}/openai/models?api-version=2024-10-21`(api-key 头);openai→`{base}/v1/models`(Bearer);qoder→`{base}/api/v1/cloud/models`;cursor→`{base}/v1/me`;openrouter→先 `/api/v1/auth/key`,失败即终,成功再 `/api/v1/models`;aws_bedrock→`{base}/foundation-models`(Bearer)。
   - `valid = 2xx 且证据成立`;证据规则:cursor 要求 body 有 `apiKeyName` 或 `userEmail`,其余要求模型列表非空(specialized.rs:209-217)。base 已以 `/v1`(或 `/api/v1`、`/foundation-models`)结尾则不重复拼接。
   - error 文案:401/403→"unauthorized";非 2xx→"read-failed";2xx 无证据→"invalid-response-schema"。
4. 通用路径按 `ProtocolFamily`(validator.rs:71-116):Anthropic/Gemini/AwsBedrock 同上;默认 OpenAiCompatible→`GET {base}/v1/models`(Bearer)。
5. 模型提取:body 的 `data[].id` | `models[].name` | `modelSummaries[].modelId`(validator.rs:137-153)。
6. **状态归类**(validator.rs:122-128):valid(2xx 且有模型)→`final_verified`;401/403 **或 2xx-但-无模型**(error=invalid-response-schema)→`rejected`;其它(429/5xx)→`transient`。response_snippet=body 前 512 字符。
7. 附加:credential_kind(openai `sk-admin-`→admin、`sk-svcacct-`→service_account;anthropic `sk-ant-admin`→admin、`sk-ant-oat`/`sk-ant-sid`→oauth;空→unknown;否则 standard,specialized.rs:26-37);scope=body 的 organization_id|account_id;tier_evidence=body 的 tier|plan(specialized.rs:152-163)。

## 3. ModelsProbeResult(balance.rs:28-41)
字段:models[]、status_code、provider、key_state、error。key_state 映射(balance.rs:1424-1432):2xx 有模型→`active`;2xx 空模型→`invalid_response`;**401/403→`expired`("credential expired or revoked")**;429→`rate_limited`;其它→`unavailable`。`is_definitive_auth_rejection()` = 401/403(balance.rs:38-40)。探测前有 header 安全闸:apikey 必须纯 ASCII 且无 CR/LF,否则直接 unavailable(balance.rs:1933-1935,1302-1304)。

## 4. BalanceResult 全字段语义(balance.rs:7-26)
- `gateway`/`provider`:供应商标识,同值(gateway 兼容旧字段)。
- `balance_usd`:字符串;数字统一 4 位小数去尾零格式化(balance.rs:1866-1869);`"N/A"`=明确探测到但无余额概念;空串=未匹配。
- `tier`:档位推断结果;`plan`:套餐名;`account_type`:密钥/账号类型。
- `balance_native`+`currency`:本币余额(如 CNY / credits / tokens / quota)。
- `source`:`provider:端点` 形式(如 `deepseek:user_balance`);`evidence_kind` ∈ cash_balance|quota|liveness|identity|entitlement。
- `detail`:原始响应 JSON;`quota`/`usage`/`entitlements`/`identity`:结构化证据对象。
- `alive`(Option<bool>):Some(true)=密钥确认存活;Some(false)=确认死亡(如 deepseek 401);None=未匹配/未知。
- `matched`:是否命中任一余额策略(未命中=全默认值)。
- 回填:`apply_probe_result`(balance.rs:1789-1831)把 probe 写回 ValidationResult——cash_balance 才设 balance_provider;balance 展示规则:balance_usd 优先,否则本币拼 `¥x`/`x EUR`;provider_evidence 存 probe 全量(去 matched、加 observed_at)。

## 5. 每供应商可探测性(query_with_context 分派,balance.rs:125-159)
**可直接匿名探测**(带 key 打官方端点):
- deepseek:`GET {去/v1}/user/balance`,balance_infos 按币种求和,CNY→balance_native、USD→balance_usd;401/403→matched 且 alive=false(source=`deepseek:unauthorized`)(balance.rs:162-215)。
- kimi:moonshot.cn→CNY、moonshot.ai→USD,`GET /v1/users/me/balance` 取 data.available_balance;非 moonshot 域名退化为 models_liveness(balance.rs:217-268)。
- minimax:`GET /token_plan/remains`,校验 base_resp.status_code==0,quota=model_remains(balance.rs:270-295)。
- cohere:`POST /v1/check-api-key`(valid==true),identity=organization_id/owner_id(balance.rs:297-325)。
- together:`GET /v1/whoami`,identity=id/name/email/project_id/organization_id;有限速头则 evidence=quota(balance.rs:327-364)。
- replicate:`GET /v1/account`,identity=type/username/name/github_url(balance.rs:366-390)。
- fireworks:`GET /v1/accounts`→逐账号 `/v1/{name}`(accountType/suspendState)+ `/v1/{name}/quotas`(monthly-spend-usd 的 maxValue:50/500/5000/50000→tier1-4,ENTERPRISE→enterprise);balance_usd="N/A"(balance.rs:392-481,1698-1709)。
- openrouter:`GET /api/v1/auth/key`→limit_remaining 或 limit-usage;否则 `/api/v1/credits`(total_credits-total_usage);is_free_tier→0(balance.rs:483-543)。
- openai:按前缀分 kind(proj/svcacct/admin/session/ordinary);依次试 billing/credit_grants→billing/subscription(hard_limit_usd - usage)→`/v1/models` 存活(tier 由 x-ratelimit-limit-requests/tokens 头推断:≥10000rpm→tier5 候选等,balance.rs:544-719,2001-2034,1832-1844)。
- anthropic:admin/oauth 键→`/v1/organizations/me`+`cost_report`(1d 桶金额合计/100)+`rate_limits`(rpm/itpm 阈值→usage_tier:scale/build/start);普通键→`/v1/models` 存活,429 也算 alive(balance.rs:721-858,1870-1931)。
- qoder:`GET /api/v1/cloud/models`,plan 取 plan|subscription|tier,entitlements=models(balance.rs:860-884)。
- cursor:`GET /v1/me`,identity=apiKeyName/userEmail/姓名(balance.rs:886-908)。
- windsurf:`POST /api/v1/GetTeamCreditBalance`(JSON body {"service_key":key}),balance_native=addOnCreditsAvailable,currency=credits(balance.rs:910-947)。
**仅存活性(models_liveness,balance "N/A")**:gemini/google、xai、aws_bedrock、glm(无被动错误码时)、其余未列供应商(balance.rs:126-158,949-1000;429 亦 alive=true)。
**无直接匿名探测**:kiro、azure_openai、vertex——仅复用验证上下文的被动存活(balance.rs:152-154,1563-1581);longcat 同类(被动,含"余额不足/欠费"等 depleted 标记,balance.rs:1618-1651);glm 被动=错误码 1308/1310/1311/1314-1321 + reset_at 时间戳(balance.rs:1583-1616)。
**unknown/gateway/ambiguous**:网关指纹探测——new-api(/api/status+/api/user/self+/dashboard/billing/subscription+/api/usage/token/,多信号独立证实,quota_per_unit/quota_display_type 换算 CNY/TOKENS/USD,unlimited_quota→"Unlimited")→one-api→litellm(/key/info)(balance.rs:1001-1294)。

## 6. 限速/重试/超时
- 验证:单次请求,无应用层重试;传输层错误→transient 并按任务隔离失败(scanner.rs:495-500)。`validate_timeout` 默认 15s(config.rs:148;`未核实` 具体 http client 装配处)。
- 余额:每端点单次请求无重试;openai/anthropic 探测遇 429 记为 rate_limited 且 alive=true。
- GitHub 源限速处理见 ghhunt.md §2(token 轮转+单次 ≤90s 等待)。

## 未核实
- `validate_timeout`/`github_request_timeout`(20s) 如何注入 reqwest Client(config.rs:147-148,197;装配代码未读)。
- GPT recheck(analyzer.rs:163-233)对 401/403 语义无改动,但其提示词细节不属本规格范围。
- `Credential.routed_to_official` 字段(models.rs:36)的写入点未在已读路径出现。
