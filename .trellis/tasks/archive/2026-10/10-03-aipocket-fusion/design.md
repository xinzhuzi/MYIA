# design — aipocket 有用模块融合(功能重实现)

> 任务:10-03-aipocket-fusion · 依赖 PRD §2 硬约束(AGPL 零入仓、行为规格先行)
> 形态判例:`myia-proxy`(v1.1 进程内官方场景件——「参考上游思路自实现,不搬上游源码」)。
> **2026-10-03 深化**:行为规格四件已产出(`research/behavior-specs/`),MYIA 集成点已实证(`research/myia-integration-facts.md`);本版按实证修订。

## 0. 深化期两大实证修正(推翻设计初版两个假设)

1. **diff 解析泳道在上游是「已建未接线」**:aipocket 实况扫描只有两条泳道——code search(≤5 页×100)+ commit message 搜索(≤12 查询),全文跑**单一联合正则**(十大密钥族);统一 diff 解析器(三侧 Added/Removed/Context 各自聚合跑指纹,`change_side` 标注)只有定义与测试引用,主循环未接线(ghhunt.md §1/§3)。→ **R1 v1 照抄实况两泳道;diff 解析器作为增强件**(能力仍要——它是上游已验证的模块,接进我们的工件二次加工)。
2. **MYIA 无「进程内插件进管线」现成通路**(proxy/osint 均纯 CLI stdout 不落库;engine 是封闭 8 值 Literal)。→ 唯一让 findings 走 classify/dedup/store/push 全链的路 = **注册新引擎 `engine: credhunter`**(integration-facts.md §2 通路 A,下游零改动)。

## 1. 总体形态

**一个进程内场景插件 + 一个新 fetch 引擎 + 品类侧叠加,核心流水线零侵入(除引擎注册三点)。**

```
plugins/myia-credhunter/
├── plugin.yaml                # tier: desktop · adapter: in_process · provides: [credhunt, credcheck, exposure]
├── README.md                  # 用法/凭据引用/授权定位
├── adapter.py                 # 适配器入口(compile+exec 动态加载挂点, 照 cli.py:2611-2628 判例)
└── credhunter/
    ├── packs.py|yaml          # R3: 发现层 20 供应商包(fofa/shodan/github_terms 查询集)——数据驱动
    ├── specs.py|yaml          # R3: 验证层 25 供应商规格(domain_suffixes/key_prefixes/protocol/models/official_api_url)
    ├── fingerprints.py        # 联合正则十大密钥族 + 17 条细正则 + 变量名归因表 + 噪声过滤(行为见 fingerprints.md §7)
    ├── ghhunt.py              # R1: GitHub 两泳道(code search + commit search)+ 工件二次加工 + diff 解析(增强)
    ├── credcheck.py           # R2: resolve 链(域名→前缀→unknown)+ 三层验证 + 余额/身份探测
    ├── exposure.py            # R4: FOFA/Shodan 查询 + 被动探测(unauth_read only)
    └── findings.py            # findings JSON 形状(对齐 pipeline items)
src/myia/engines/credhunter.py # 新引擎: 复用动态加载器调 adapter, 产出 items(照 registry.py EngineFailure 降级)
```

核心仓改动面(全部三点,均有精确落点,见 integration-facts.md):
1. `src/myia/schema.py:175-177` `EngineName` 加 `credhunter`(**12 节公开契约 → docs zh/en + test_docs.py/test_skill_doc.py 三锁同步**)。
2. `src/myia/engines/registry.py:84-92` `ENGINE_REGISTRY` 注册。
3. `src/myia/cli.py` 三个子命令注册(§3.2;credcheck 的 CLI 面)。

**分工原则**:credhunt/exposure 是「取数」= 走引擎进管线;credcheck 是「读库→探测→回填」后处理 = 走独立 CLI 子命令(非 fetch,不入 ENGINE_REGISTRY)。

## 2. 模块映射与行为规格对照(规格已产出,实现只认规格)

| MYIA 侧 | 行为规格(权威) | 关键行为要点 |
|---|---|---|
| `packs.py`+`specs.py` | fingerprints.md | 发现层 20 包(查询集三源:fofa/shodan/github_terms)+ 验证层 25 规格(nvidia/ksyun/siliconflow/groq/openrouter 仅验证无猎取查询);`resolve`=域名后缀→密钥前缀→unknown;`sk-` 泛前缀刻意不进 key_prefixes(防吞网关键) |
| `fingerprints.py` | fingerprints.md §7 | 三套独立指纹:GitHub 联合正则十大族 / FOFA-Shodan 17 条细正则+30 项变量名归因 / 验证层 resolve 链;噪声过滤(长度<15、39 项 NOISE_SUBSTRINGS、连续 8 字母序、同 key>5 位置剔除) |
| `ghhunt.py` | ghhunt.md | code 泳道 `/search/code` 1..=5 页×100(条数<per_page 提前停)+ 公开性校验 + 噪音路径过滤 + blob ≤1MiB;commit 泳道 `/search/commits` 仅第 1 页 ≤12 查询;token 池轮转,403/429 读 retry-after ≤90s 仅重试一轮;apiurl 归因=前缀→静态官方地址映射;工件二次加工 ≤200 条并发 8 重试 5 |
| `credcheck.py` | credcheck.md | validate(resolve→专用端点按 protocol 族→模型提取→三态归类:final_verified/rejected(401/403 **或 2xx 无模型**)/transient)→ 余额仅对 valid 集:13 供应商可匿名探测(端点逐家在案)、gemini/xai/bedrock/glm 等仅存活性、kiro/azure/vertex 无探测;BalanceResult 17 字段;key_state 五值(active/invalid_response/expired/rate_limited/unavailable);ASCII+无 CRLF header 安全闸 |
| `exposure.py` | exposure.md | FOFA(代理域可配 base,单 key,qbase64,12 固定 fields,页 ≤10×100 提前停,页间 0.3s,run 预算 24 查询)+ Shodan(FOFA 查询机械翻译 `body="X"`→`http.html:"X"`,行归一化 hostnames[0]→http.host→ip 兜底,非 80/443 拼端口,页间 1.0s,预算 16)+ 被动探测(unauth_read only:产品路径集在案,2xx→finding,证据 512 字符,宏预算 12/总预算 600;**我们 v1 只做 L0 unauth_read,与上游扫描器硬编码一致**);无 key=源级结构化降级不拦管线 |
| 复用不重写 | — | scanner/scheduler/analyzer/pipeline/db/api/frontend 全不融(MYIA 有同构物或形态不同) |

**上游默认参数速查(我们的缺省直接照抄,配置名对齐 MYIA 习惯)**:GitHub per_page=100/commit_budget=12/max_blob=1MiB/artifact_concurrency=8/rate_wait≤90s;FOFA page_size=100/max_pages=10/delay 0.3s/query_budget=24;Shodan max_pages=10/delay 1.0s/query_budget=16;验证 timeout=15s;探测证据 512 字符;passive 宏预算 12/spec 总预算 600;GitHub lookback 24h(incremental)/720h(full)。

## 3. 契约面

### 3.1 plugin.yaml(字段以 `src/myia/plugins/manifest.py` 为准)

`id: myia-credhunter` · `tier: desktop` · `requires: []` · `provides: [credhunt, credcheck, exposure]` · `adapter: {entry: adapter.py, mode: in_process}`。**plugin: 节不加新 mode**(integration-facts.md §3 规避方案:credhunter 能力经品类 source 的 `engine: credhunter` 表达,remote 模式留给旧源)。声明面变更 → golden/官方件五处同步(integration-facts.md §4)。

### 3.2 CLI 子命令(credcheck 必做;credhunt/exposure 可选直跑调试面)

- `shishi credcheck --json` — 读库内凭证 items → 探测 → 回填(store 无破坏性变更,回填写 findings 侧字段)。注册三件套:cli.py:265 挂 parser / :2804 handlers / :175-182 PLUGIN_ID+失败码(proxy 样板 cli.py:2700-2732)。
- `shishi credhunt --json` / `shishi exposure --json` — 单次取数 stdout JSON(调试/冒烟用;正式产出走引擎进管线)。
- 退出码族对齐全家桶:0 成功 / 1 配置错误 / 2 全部失败 / 3 部分失败;`--json` 下 stdout 恒可 `json.load`。

### 3.3 品类 YAML(叠加式)

- `plugins/credentials.yaml` 升级:source 加 `engine: credhunter`(native 主力);原 remote 源(myia-credentials)保留为可选聚合(Q2 决议)。dedup.key 用字段组合(凭证无语义 URL,禁 `{title}`;建议 `{provider}-{apikey指纹}` 形态,design 定稿时按 dedup.py 实测语义敲定)。
- `plugins/exposure.yaml` 新品类:同引擎或独立 engine 参数;FOFA/Shodan key 全走 `keychain:myia/...`;无 key 显式空态(AC6)。

### 3.4 凭据、限速与出网

- GitHub tokens/FOFA key/Shodan key 一律 `keychain:myia/<scope>/<name>` 引用;适配器经 `from myia.secrets import get_secret` 读(integration-facts.md §5;import 宿主理由=读 keychain 属宿主能力,评审注明)。
- 引擎跑在 async fetch 上下文 → 复用 `RateLimiter`/`HostLimiterRegistry`(fetch_base.py:852/917);credcheck CLI 自带串行+间隔(无同步限速器可复用)。
- 限速缺省对齐上游节奏(FOFA 0.3s/Shodan 1.0s 页间;GitHub 仅 403/429 退避)且叠加 MYIA `rate_limit` 节语义;凭据命中物私有情报红线(fixture 脱敏、不外发)。

## 4. 数据流

```
R1: GitHub API(token) ─code/commit 泳道→ 联合正则 → 凭证 items ────────────┐
R4: FOFA/Shodan(key) → targets → unauth_read 探测(预算内) → 曝面 items ────┤→ engine: credhunter → pipeline(classify/dedup/store/push)
R2: store 凭证 items → shishi credcheck(resolve→validate→balance) → alive/balance 回填 → store
```

上游「跨 run 去重/验证缓存」由 MYIA 同构物承接:detect 变更指纹跳过 + dedup.py + store 历史(不引 Redis;aipocket 的 Redis dedup 在我们=SQLite 查询)。

## 5. 取舍记录

- **Grill Round 2 六决(2026-10-03,开工令即按推荐;详见 prd §6)**:探测分层(存活默认/余额显式 `--balance`)、出网缺省(credcheck 串行 RPM≤30、GitHub run 预算 12 轮转、FOFA/Shodan 照上游)、掩码(**插件侧 keystore 存全文**(引擎路径无安全通道,metadata 会漏进模板)/items+模板前 8 后 4/push 默认 stdout——keystore 已落地)、真跑语义(自备活 key+构造死 key;猎取产物仅存活探测先目视)、单任务不拆、品类电池进/桌面种子不进。
- **新引擎 vs 纯 CLI vs 新 plugin mode**:新引擎(PRD 要求进管线;下游零改动;代价=EngineName 三锁+golden,可控)。纯 CLI 止步 stdout 被否;plugin: 节加 native mode 被否(契约面更大,规避)。
- **R1 泳道取舍**:实况两泳道照抄(已被上游验证);diff 解析器收编为工件二次加工增强件(上游已建未接线,我们接线=增值而非偏离)。
- **探测档位**:只做 L0 unauth_read(与上游扫描器硬编码一致);weak_password/idor/ssrf/sqli/rce 引擎规格在案但 v1 不开(fail-closed 语义照抄,authorized_probe_scope 概念引入留二期)。
- **FOFA base 可配**:上游默认第三方代理域 fofoapi.com——我们 endpoint 占位可配(官方/代理由主人自决),不写死。
- **单插件多 provides**(凭据线内聚);R4 边界膨胀可再拆。
- **不可探测即诚实 unknown**(SiliconFlow 判例精神;credcheck.md §5 的可探测性矩阵为权威)。

## 6. 兼容与回滚

- 兼容:现有品类/插件零行为变更;credentials.yaml 叠加式;引擎缺失/适配器缺失结构化降级不拦核心流水线(铁律,照 EngineFailure 模式)。
- 回滚点:撤引擎注册两行(EngineName/ENGINE_REGISTRY)+ 删插件目录 + 还原品类 YAML + 撤 cli.py 注册;store 无破坏性 schema 变更。

## 7. 分阶段交付(P0 已完成;P1–P4 每阶段真跑出口判据后才进下阶段)

| 阶段 | 内容 | 出口判据 |
|---|---|---|
| **P0 ✅ 行为规格+集成真值(2026-10-03)** | behavior-specs 四件 + myia-integration-facts | 规格落盘,本设计修订即消费 |
| P1 | R3 packs/specs/fingerprints + R1 ghhunt 两泳道 + 引擎注册三锁 + CLI + credentials.yaml 挂接 + golden 五处同步 | 真跑 GitHub 工件端到端 ≥1 条 finding 入库;pytest/docs 三锁绿 |
| P2 | R2 credcheck(resolve/三态/13 家余额矩阵)+ 串行限速 + 回填 | 真跑 ≥1 真实供应商验证+余额;限速可观测 |
| P3 | R4 exposure(FOFA/Shodan/被动探测)+ exposure.yaml + credentials.yaml 双源终态 | 主人 key 真跑两源各 ≥1 轮;无 key 空态演练 |
| P4 | wrap:docs zh/en、golden、真跑证据矩阵、AC1–AC6 全核 | AC 全绿 |
