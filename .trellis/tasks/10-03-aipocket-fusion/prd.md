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
| `discovery/github_artifacts` | GitHub 工件猎取:统一 diff 解析(Added/Removed/Context 侧 + hunk 行号)提取**新增行**凭证 | **最高**(独门能力,直连情报管线) | 新场景插件:GitHub 凭证猎手源 |
| `discovery/packs` | 21 个 AI 供应商包(openai/anthropic/gemini/glm/kimi/qwen/deepseek/cursor/windsurf/xai/kiro/qoder/longcat/together/fireworks/replicate/cohere/minimax/aws_bedrock/azure_openai…,含 regex 指纹) | 高 | 供应商指纹库(Python 侧数据文件) |
| `prober/`(validator/engines/capability/products) | 凭证验证 + 被动探测(passive_prober:目标已知路径 GET,2xx → unauth_read 发现 + 证据快照) | 高 | enrich/验证阶段插件 |
| `services/balance` | 余额查询(balance_usd/tier/quota/usage/entitlements/identity)+ models 探测(401/403 = definitive auth rejection 判死) | **最高**(凭证猎手价值核心) | 新插件:余额/能力查询 |
| `services/`(scanner/scheduler/pipeline/analyzer) | 扫描编排/调度/流水线/分析 | 中(MYIA 已有同构物) | **复用 MYIA 现有 pipeline/dedup/调度,不重写** |
| `db/`(PostgreSQL/SQLx + Redis) | 持久化/去重/scan lease | 低 | MYIA 已有 SQLite + dedup.py,不融 |
| `api/`(Axum/JWT/SSE)+ `frontend/` | 服务端 Web API + React 前端 | 低(形态不同) | 不融;REST 形状可参考;面板归 10-03-ui-deep-imitation |

## 4. 需求(v1 终版——2026-10-03 grill 定档:全量含 R4)

- **R1 GitHub 工件凭证猎手(源插件)**:按供应商指纹扫描 GitHub 公开工件,统一 diff 解析取**新增行**,产出凭证情报进管线;去重回用 `dedup.py`。
- **R2 凭证验证 + 余额查询(插件)**:对 R1 产出做活性/余额/配额探测(401/403 判死语义按行为规格复刻),结果落库。
- **R3 供应商指纹库**:21 供应商包重实现为 MYIA 侧数据;留扩展接口,后续加供应商不动核心。
- **R4 FOFA/Shodan 曝面发现源**:凭 API key 的暴露面搜索 + 被动探测(unauth_read);API key 主人自备,插件无 key 时显式空态不报错。
- **R5 `myia-credentials` 远程插件处置(已决)**:保留共存,降为可选聚合源;原生插件为主力,不删远程件。

## 5. 验收标准(草案)

- **AC1 零代码污染**:融合范围内模块全部 Python 原生实现;全仓 `rg` 核验无 aipocket/AGPL 文件、无逐行翻译痕迹(文件头无上游归属需求 = 未借代码)。
- **AC2 真跑验证**:R1/R2 对真实 GitHub 工件与真实供应商端点各跑通端到端(最低条数待 grill 定);录制回放只作回归,不单独放行。
- **AC3 私有情报红线**:凭据命中物不入 git、不外发;测试 fixture 全脱敏。
- **AC4 工程配套**:docs zh/en 同步;测试齐;如有插件声明面变更,golden 基件同步(在案教训:改声明面必同步 golden)。
- **AC5 插件体系合规**:走 MYIA 插件体系(tier/requires/provides 声明正确),不破坏现有插件与桌面端。
- **AC6 R4 空态合规**:FOFA/Shodan 无 key 时插件显式空态降级,不报错不静默;被动探测证据快照按私有情报红线处理。

## 6. Grill 决议(2026-10-03,已回写)

- **Q1 范围 = 全量含 R4**(主人定:R1+R2+R3+R4 一期做完;FOFA/Shodan key 主人自备)。
- **Q2 远程插件 = 保留共存,降为可选**(按推荐)。
- **Q3 排期 = 等 dwfrun-0e1749cb 大工作流收尾后 start**(按推荐;规划三件套先行备齐,收尾即 start)。
- **Q4 出网纪律(未单问,按推荐默认)**:验证/余额探测默认串行 + 每供应商 RPM 上限(数值 design 阶段定);R4 入一期后,FOFA/Shodan API 调用同样走限速预算池。
