# PRD — aipocket 有用模块融合(功能重实现)

> 任务:10-03-aipocket-fusion · 状态:planning · 优先级:P1
> 主人指令(2026-10-03):「我需要你使用其中有用的模块,去做」「建一个 Trellis 任务进规划阶段」
> 前置结论:融合 = **功能重实现**,不是代码合并(见 §2 硬约束)。

## 1. 背景与动机

aipocket(AGPL-3.0-or-later,作者 fuyou <imshuazi@126.com>,本机 `Downloads/aipocket-main/` 只读参考)是 AI 凭证/基础设施 OSINT 工具:FOFA/Shodan 发现 AI 基础设施暴露面 + GitHub Artifact Hunter 猎取泄露凭证,再归因、验证、余额查询、持久化。命令面:`scan / watch / serve / balance / cve-sync / queries / config / shodan-info`。主人已自行部署一个实例。

MYIA 现状(融合前):

- 仅 `plugins/myia-credentials/`(v1.1.0,tier: remote)远程数据源插件——endpoint + keychain token 调主人部署的实例拉凭证情报;插件本体不带任何 aipocket 能力,离了外部部署就是空壳。
- 凭证猎手面板(key/余额/高危列表)在 10-03-ui-deep-imitation(闭源档模仿对象)与 10-03-v12-backlog 各有一条,均未动工。

动机:把 aipocket 验证过的能力**原生**融进 MYIA 的 Python 情报管线,消除对外部部署的硬依赖,并为 UI 面板计划供好数据侧。

## 2. 硬约束(许可证与红线,不可协商)

1. **aipocket = AGPL-3.0-or-later;MYIA = MIT(xinzhuzi;两者非同一作者,无再授权通道)→ AGPL 代码严禁入仓**。仓库既有红线(Cherry Studio 判例;10-03-ui-deep-imitation PRD 取材策略表)。
2. 唯一合法路线 = **功能重实现**:以 aipocket 的行为(命令面、接口语义、判定规则、数据形状)为模仿对象,用 MYIA 自己的 Python 技术栈从零写。执行纪律:不逐行翻译、不搬标识符/错误文案/注释;先落「行为规格」再写实现。
3. 观察通道 = `Downloads/aipocket-main/` 源码(只读)。该目录在仓库外,不进 git。
4. **授权定位**:仅用于已授权安全研究与自有/已授权资产的泄露凭证排查(对齐 aipocket 自身免责声明与本仓 security-baseline);不做大规模扫描、不滥用凭据。凭据类命中物按私有情报红线处理:不入 git、不外发、外发先脱敏。
5. 出网纪律:验证/余额探测对供应商端点限速串行起步,禁止高频打点(见 Q4)。

## 3. 模块价值评估(aipocket 8 crates → MYIA 融合候选)

| aipocket 模块 | 功能 | 价值 | 融合候选(MYIA 侧) |
|---|---|---|---|
| `clients/`(fofa/github/shodan/tavily) | 四个上游 REST 客户端 | 高 | 数据源插件的取数层 |
| `discovery/github_artifacts` | GitHub 工件猎取。**深化修正**:实况只有两泳道——code search(≤5页×100)+ commit message 搜索(≤12 查询),全文跑联合正则;统一 diff 解析器(三侧聚合)已建未接线 | **最高**(直连情报管线) | 新场景插件:GitHub 凭证猎手源(两泳道照抄+diff 解析收编为增强件) |
| `discovery/packs` | 发现层 **20** 个供应商查询包(openai/anthropic/gemini/xai/qoder/kiro/aws_bedrock/cursor/windsurf/azure_openai + 10 家仅 GitHub 键名查询)+ 验证层 **25** 个供应商规格(多 nvidia/ksyun/siliconflow/groq/openrouter);三套独立指纹(联合正则/细正则+归因表/resolve 链) | 高 | 供应商指纹库(Python 侧数据文件) |
| `prober/`(validator/engines/capability/products) | 凭证验证 + 被动探测(passive_prober:目标已知路径 GET,2xx → unauth_read 发现 + 证据快照) | 高 | enrich/验证阶段插件 |
| `services/balance` | 余额查询(balance_usd/tier/quota/usage/entitlements/identity)+ models 探测(401/403 = definitive auth rejection 判死) | **最高**(凭证猎手价值核心) | 新插件:余额/能力查询 |
| `services/`(scanner/scheduler/pipeline/analyzer) | 扫描编排/调度/流水线/分析 | 中(MYIA 已有同构物) | **复用 MYIA 现有 pipeline/dedup/调度,不重写** |
| `db/`(PostgreSQL/SQLx + Redis) | 持久化/去重/scan lease | 低 | MYIA 已有 SQLite + dedup.py,不融 |
| `api/`(Axum/JWT/SSE)+ `frontend/` | 服务端 Web API + React 前端 | 低(形态不同) | 不融;REST 形状可参考;面板归 10-03-ui-deep-imitation |

## 4. 需求(v1 终版——2026-10-03 grill 定档:全量含 R4)

- **R1 GitHub 工件凭证猎手(源插件)**:按供应商查询集跑 GitHub 两泳道——code search(`sk- filename:.env` 类查询,≤5页×100)+ commit message 搜索(≤12 查询)——联合正则十大密钥族全文匹配,apiurl 按前缀归因官方地址;统一 diff 解析(Added/Removed/Context 三侧聚合)作为工件二次加工的增强件(上游已建未接线,我们接线=增值);去重回用 `dedup.py`。
- **R2 凭证验证 + 余额查询(插件)**:对 R1 产出做 resolve(域名→前缀)→ 三态验证(final_verified / rejected=401/403 或 2xx 无模型 / transient)→ 余额/身份探测(13 供应商可匿名探测矩阵 + 仅存活性家族 + kiro/azure/vertex 无探测=诚实 unknown),BalanceResult 17 字段回填落库。
- **R3 供应商指纹库**:发现层 20 包 + 验证层 25 规格重实现为 MYIA 侧数据(两层数据形状见 fingerprints.md);留扩展接口,后续加供应商不动核心。
- **R4 FOFA/Shodan 曝面发现源**:凭 API key 的暴露面搜索 + 被动探测(unauth_read);API key 主人自备,插件无 key 时显式空态不报错。
- **R5 `myia-credentials` 远程插件处置(已决)**:保留共存,降为可选聚合源;原生插件为主力,不删远程件。

## 5. 验收标准(草案)

- **AC1 零代码污染**:融合范围内模块全部 Python 原生实现;全仓 `rg` 核验无 aipocket/AGPL 文件、无逐行翻译痕迹(文件头无上游归属需求 = 未借代码)。
- **AC2 真跑验证(Q10 定案)**:P2 冒烟=自备活 key 验活键路径 + 构造死 key 验 401 判死路径(可无主人 key 达成);R1 真跑需 GitHub token(缺则记主人侧待办+fixture 回归兜底);R3 需 FOFA/Shodan key(缺则空态演练=AC6);录制回放只作回归,不单独放行。
- **AC3 私有情报红线(Q9 掩码政策)**:凭据命中物不入 git、不外发;store 内部全文、items.fields 与一切推送模板前 8 后 4 掩码;测试 fixture 全脱敏。
- **AC4 工程配套**:docs zh/en 同步;测试齐;如有插件声明面变更,golden 基件同步(在案教训:改声明面必同步 golden)。
- **AC5 插件体系合规**:走 MYIA 插件体系(tier/requires/provides 声明正确),不破坏现有插件与桌面端。
- **AC6 R4 空态合规**:FOFA/Shodan 无 key 时插件显式空态降级,不报错不静默;被动探测证据快照按私有情报红线处理。

## 6. Grill 决议(2026-10-03,已回写)

- **Q1 范围 = 全量含 R4**(主人定:R1+R2+R3+R4 一期做完;FOFA/Shodan key 主人自备)。
- **Q2 远程插件 = 保留共存,降为可选**(按推荐)。
- **Q3 排期 = 等 dwfrun-0e1749cb 大工作流收尾后 start**(按推荐;规划三件套先行备齐,收尾即 start)。
- **Q4 出网纪律(未单问,按推荐默认)**:验证/余额探测默认串行 + 每供应商 RPM 上限(数值 design 阶段定);R4 入一期后,FOFA/Shodan API 调用同样走限速预算池。

### Grill Round 2(2026-10-03,主人 /workflow 开工令=六问全按推荐)

- **Q7 探测分层**:存活探测(models 端点,默认开)/ 余额+身份探测(默认**关**,`--balance` 显式开或品类配置项)。
- **Q8 出网缺省(数值定案)**:credcheck 串行、每供应商 RPM ≤30(间隔 2s);GitHub 每 run 查询预算 12 条(checkpoint 游标跨 run 轮转);FOFA/Shodan 照上游(0.3s/1.0s 页间、24/16 查询预算)。不抄上游并发 20。
- **Q9 掩码政策**:store 内部字段存 apikey 全文(库不出本机);items.fields 与一切推送模板一律**前 8 后 4 掩码**;官方品类模板 push 默认 `stdout`;全文永不进模板上下文。
- **Q10 真跑验收语义(AC2 定案)**:P2 冒烟=主人自备活 key(final_verified+余额正确)+ 构造死 key(401→rejected);对猎取产物的 credcheck 首跑仅存活探测且产出先落库供主人目视,不做余额/身份探测。
- **Q11 任务结构**:维持单任务 P0–P4,不拆父子。
- **Q12 品类挂接**:credentials/exposure 进官方件电池(OFFICIAL_PLUGINS/OFFICIAL_PACKAGES/golden 同步);桌面首跑种子四件套(ai-news/gpu-prices/stocks/wool)**不加**,首跑零门槛原则。
- **开工门修订**:主人 /workflow「按照 trellis 方式做完」=开工令,推翻「等 dwfrun-0e1749cb 收尾」;改为执行内分段——插件包纯增量先行,接线段(schema.py/cli.py/docs 三锁/golden)等并行工作流交出热文件。

## 7. 深化事实补注(2026-10-03「深化与补全」)

- **行为规格四件已产出**(`research/behavior-specs/`:ghhunt/credcheck/fingerprints/exposure),实现只认规格不回看 aipocket 源码;MYIA 侧集成真值落 `research/myia-integration-facts.md`(引擎通路、CLI 注册三件套、golden 五处同步清单)。
- **两大假设修正**:①上游 diff 解析泳道未接实况(实况=两泳道联合正则全文扫描),R1 照抄实况、diff 收编增强件;②MYIA 无进程内插件进管线现成通路 → 唯一路线=注册新引擎 `engine: credhunter`(schema EngineName + ENGINE_REGISTRY + engines/credhunter.py,下游 classify/dedup/store/push 零改动;EngineName 是 12 节公开契约,三锁+golden 同步在案)。
- **数量修正**:发现层 20 包、验证层 25 规格(原「21」为初版误计)。
- **探测档位收窄**:v1 只做 L0 unauth_read 被动探测(与上游扫描器硬编码一致);weak_password/idor/ssrf/sqli/rce 规格在案但 fail-closed 不开,授权范围机制留二期。
- **FOFA base 上游默认第三方代理域(fofoapi.com)**:我们做 endpoint 可配占位,官方/代理由部署者自决,不写死。
