# aipocket 曝面发现线 — 行为规格

> 行为规格——AGPL 只读观察产出,实现时只认本规格不回看源码。
> 源码观察对象:/Users/zhengbingjin/Downloads/aipocket-main(Rust workspace,AGPL-3.0)。本文只描述行为语义,`file:line` 均指该仓库。行内 `cr=` 缩写 = crates。

## 1. FOFA 客户端(cr=aipocket-clients/src/fofa.rs)
- 端点:`GET {FOFA_BASE_URL}/api/v1/search/all`,默认 base `https://fofoapi.com`(第三方代理域,core/config.rs:113)。
- 认证:单 query 参数 `key`;keys 来自 `FOFA_KEYS`(逗号分隔多 key),**search 只用第一个 key**,多 key 无轮询(fofa.rs:27)。
- 参数:`qbase64`(查询串先 UTF-8 再 base64)、`page`、`size`、固定 `fields=host,ip,port,protocol,title,header,banner,server,product,link,domain,cert`(fofa.rs:31-42)。
- 查询语言形状(实际查询集,discovery/legacy_queries.rs:1-33):`header="authorization: bearer sk-"`、`banner="OPENAI_API_KEY=sk-"`、`body="litellm" && body="sk-"`、`body="A" || body="B"`;产品查询统一追加 `&& status_code="200"`(legacy_queries.rs:253)。共 31 条直凭据查询 + 25 组产品查询。
- 分页:source 侧逐页 1..=FOFA_MAX_PAGES(默认 10),size=FOFA_PAGE_SIZE(默认 100),**返回行数 < size 即提前停页**;页间 sleep FOFA_PAGE_DELAY(默认 0.3s)(sources.rs:263-304)。
- 配额语义:每次 run 只取前 `FOFA_QUERY_BUDGET`(默认 24)条查询(经 plan_query_ids 按历史指标挑选,scanner.rs:144-154);配额=查询条数预算,不是点数配额。
- 返回结构:`{"results":[...12字段数组或对象...],"error":..,"size":..,"page":..,"mode":..,"query":..}`;行→字典映射见 sources.rs:30-59(数组按 FOFA_FIELDS 顺序;`body` 空时别名进 `banner`)。
- `check()` = 用探针查询 `title="123"` page=1 size=1(fofa.rs:47-49)。
- 错误分类:非 2xx → 报错带前 200 字符预览;非 JSON → 报错(clients/lib.rs:11-37)。**无错误码细分,统一 anyhow 字符串**。

## 2. Shodan 客户端(cr=aipocket-clients/src/shodan.rs)
- 端点:`GET {SHODAN_BASE_URL}/shodan/host/search`(search)、`/shodan/host/count`(count)、`/api-info`(info_all),默认 base `https://api.shodan.io`(config.rs:120)。
- 认证:query 参数 `key`;search/count 只用第一个 key;`info_all` 遍历全部 key 返回 `[(key, Result<ShodanInfo>)]`,`ShodanInfo={plan, query_credits, scan_credits}`(shodan.rs:12-49)。
- 参数:search 带 `key/query/page`(页大小固定 100);count 只带 query,取响应 `total` 为 i64(shodan.rs:50-79)。
- Shodan 查询由 FOFA 查询机械翻译:`body="X"`→`http.html:"X"`、`&&`→空格、`||`→` OR `(legacy_queries.rs:193-207)。
- 分页:1..=SHODAN_MAX_PAGES(默认 10),**本页 matches < 100 提前停**,页间 delay 默认 1.0s;预算 SHODAN_QUERY_BUDGUARD 默认 16 条查询(sources.rs:341-383,config.rs:126)。
- 行映射(sources.rs:63-145):host=hostnames[0]→http.host→ip_str→host 兜底;port 非 80/443 且 host 无冒号则拼 `:port`;protocol=443?"https":"http";`data`→header、`http.html`→banner、ssl 证书 subject/issuer commonName 拼进 cert。产出 FOFA 同形 12 字段。
- 错误分类:同 FOFA 统一字符串,无结构化降级。

## 3. Tavily 角色(cr=aipocket-clients/src/tavily.rs)
- 仅服务于 `cve-sync` 命令:`POST {TAVILY_BASE_URL}/search`,body=`{api_key, query, search_depth:"advanced"}`;key 未配置直接 bail(tavily.rs:19-31)。主入口固定查询 `"AI security CVE latest"` 并把原始 JSON 打印到 stdout(cr=aipocket/src/main.rs:108-114)。README 称结果进 `cves` 表(README.md:199)。**曝面发现线本身不用 Tavily**。

## 4. 被动探测规格(cr=aipocket-prober)
- 目标来源:hit 的 `host`(缺失退 `url`)字段;产品 hint = hit `_product`(由查询→产品映射打标,legacy_queries.rs:183-222,25 组产品名→snake_case;无映射=generic)。探测 URL = `target.trim_end_matches('/') + path`,**探测路径上不做 scheme 归一**(engines.rs:661-671);FOFA host 自带 scheme、Shodan 归一化产物不带 scheme(sources.rs:92-95),后者拼出的 URL 是否可用取决于上游 —— 未核实运行期实际成功率。
- `passive_prober` 宏语义(products.rs:4):按声明顺序 GET 每个路径,**累计 findings ≥ request_budget 即 break**;任一 2xx → 产出 finding `{product, vuln_class:"unauth_read", risk:0, evidence:{path, snippet:正文前512字符}, credentials:[]}`;请求错误静默吞掉。
- 产品路径集(products.rs:5-34,与 product_specs.rs:14-135 的 unauth 列表一致):dify=`/console/api/system-features`,`/v1/info`;litellm=`/health/readiness`,`/v1/models`;openwebui=`/api/config`,`/api/version`;flowise/langflow=`/api/v1/version`;newapi=`/api/status`,`/v1/models`;generic=`/v1/models`,`/api/status`;anythingllm=`/api/system`,`/api/v1/system`;chatgpt_next_web/librechat/lobechat=`/api/config`;fastgpt=`/api/system/getInitData`;openrouter/portkey=`/v1|/api/v1/models`。另 flowise/langflow 的 spec 版 unauth 含 `/api/v1/credentials|chatflows|variables|flows`(更强证据)。
- 请求预算:passive 宏用 `GENERIC_MAX_REQUESTS_PER_TARGET`(默认 12);spec 引擎用 `MAX_REQUESTS_PER_TARGET`(默认 600)为总预算,按 spec 顺序扣减,`allowance=min(剩余, spec.max_requests)`,耗尽→`SkippedBudget`(scanner.rs:716-741,engines.rs:106-160)。
- spec 引擎(product_specs.rs:137-251):每产品生成 unauth(L0)、weak_password(L1,max 12 请求,内置弱口令字典+admin/root)、idor(L1,requires_auth,依赖 weak_password)、ssrf(L2,max 3)、sqli(L2,max 4)、rce(L3,max 2),entry 形如 `{paths:[..]}` / `{login,body:{username:"{user}",password:"{pass}"}}` / `{path,param,payloads:["' OR '1'='1","'"]}`。
- 风险门控(engine.rs:31-41 + capability.rs:163-187):risk>max_risk 拒;**risk>L0 且未开 intrusive_checks 一律拒(fail closed)**;类白名单 `*|all|精确类名`;ssrf/sqli/rce 各有独立开关(默认全 false);L>0 还要求 authorized_probe_scope 包含目标 origin(intrusive 开且 scope 非空时)。PROBE_MAX_RISK 默认 1,合法域 0..=3。
- spec 引擎的 2xx 语义:unauth 2xx → 读正文,用正则(sk-/AIza/gsk_/nvapi- 前缀)抽凭据,只有**抽到凭据才发 finding**(engines.rs:196-213);weak_password 2xx 且响应含 token 字段(token/access_token/key/api_key/JWT 或 data.* )→ 记 Bearer 进 auth_headers 供 idor 复用(engines.rs:263-323)。
- 扫描器调 passive 宏时硬编码 `max_risk=L0, intrusive=false, allowed_classes=["unauth_read"]`(scanner.rs:716-723),即扫描主路径只做未授权读。

## 5. scan 编排(cr=aipocket-services/src/scanner.rs + core/domain.rs:205-225)
- 阶段:acquire ScanLease(Redis 单飞锁,TTL 7200s)→ create run(`run_YYYY_MM_DD_HH-MM-SS`)→ 逐源发现(FOFA/Shodan 先行,GitHub 滞后排水)→ 存 discovery_hits → 蜜罐组过滤 + 跨 run 去重 → probe_hits → 凭据抽取+GPT 增强 → 分批验证(签名量 VALIDATE_BATCH_SIZE=500,并发=20)→ GPT recheck → 余额查询 → 高价值入库 → 写 JSONL/raw_hits|valid|suspicious → finished。
- 模式差异:`full` = 忽略跨 run 去重、强制重新验证/重新查余额;`incremental`(默认)= 开 Redis 跨 run 去重(dedup target_seen "probe"),验证/余额命中缓存即跳过。**增量不含时间窗**,去重靠 Redis TTL(host 7d/cred 3d/rejected 30d/transient 6h/balance 1d,config.rs:173-179)与 GitHub checkpoint 水位(github_lookback_hours=24 vs full=720)。
- run 断点续扫:resume_run_id + phase 阶级表(started<discovery<extract<probe<gpt<validate<finalize<finished,scanner.rs:1030-1044),已过 discovery/validate 则从 DB 回放。
- watch 节奏:Scheduler 每 SCHEDULER_INTERVAL(默认 3600s)无条件跑一次 `run_scan("all", incremental)`;serve 时 SCHEDULER_ENABLED 才挂后台任务(main.rs:194-221)。无退避、无并发保护(靠 ScanLease)。
- 无 key / 上游不可达:**结构化降级而非崩溃**——源级 fetch 失败记 Log 事件继续下一源(sources.rs:295-299);查询页失败 push errors 后 break 该查询;FOFA/Shodan key 未配置时 search 返回 Err("not configured"),源被跳过;GitHub 无 token 或无 PG 则不装配(main.rs:270-273)。单源 0 命中≠失败。

## 6. cve-sync 与 queries 命令(main.rs:90-114)
- `cve-sync`:调 Tavily 搜索 "AI security CVE latest",原样打印 JSON(同步 cves 表的动作在 serve 启动时 seed_cves,main.rs:155-158)。离线/无 key 时直接报错退出。
- `queries`:遍历 Provider Packs,打印每个 pack 的 `id + fofa/shodan/github 查询条数`(计数而非查询文本),pack 形如 `{id, fofa_queries:["body=\"sk-\""], shodan_queries:["http.html:sk-"], github_terms:[..]}`(packs.rs:3-18)。

## 7. 关键默认值速查(core/config.rs:109-206)
FOFA page_size=100/max_pages=10/delay 0.3s/query_budget 24;Shodan max_pages=10/delay 1.0s/query_budget 16;SCAN_PROBER=true;prober 并发 50、批 200;generic_max_requests=12;max_requests=600;max_probe_redirects=2(HTTP client 全局超时=validate_timeout=15s,no_proxy);VALIDATE_CONCURRENCY=20。

## 未核实清单
- FOFA base 默认 `fofoapi.com` 是代理站,其 fields 分隔/配额计费与官方 FOFA 是否一致 未核实。
- Shodan 无 scheme host 在 passive 探测下的实际请求成功率 未核实(见 §4)。
- `migrated_specs`(CVE 级 spec 迁移表)内容未读,仅知它会覆盖同 id 的静态 spec(product_specs.rs:216-222)。
- plan_query_ids 的指标挑选算法细节(在 aipocket-db,未读)。
- balance/gpt/validator 线为曝面线邻接件,本文只覆盖到调用点。
