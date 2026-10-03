# design — aipocket 有用模块融合(功能重实现)

> 任务:10-03-aipocket-fusion · 依赖 PRD §2 硬约束(AGPL 零入仓、行为规格先行)
> 形态判例:`myia-proxy`(v1.1 进程内官方场景件——「参考上游思路自实现,本仓库不复制、不 vendored 上游任何源码」)与本任务纪律同构,直接沿用其接入模式。

## 1. 总体形态

**一个新官方进程内场景插件 + 品类侧叠加升级,核心流水线零侵入。**

```
plugins/myia-credhunter/            # 新场景件(命名可调, 直白优先)
├── plugin.yaml                     # tier: desktop · adapter: in_process · provides: [credhunt, credcheck, exposure]
├── README.md                       # 用法/凭据引用/授权定位
├── adapter.py                      # 适配器入口(动态加载挂点, 参照 myia-proxy)
└── credhunter/                     # 实现(插件内自包含, 核心包不膨胀)
    ├── fingerprints/               # R3: 供应商指纹库(数据文件 + loader, 加供应商=加文件)
    ├── ghhunt.py                   # R1: GitHub 工件猎取(GitHub 客户端 + 统一 diff 解析 + 新增行指纹匹配)
    ├── credcheck.py                # R2: 验证 + 余额(models/余额端点探测, 401/403 判死, 限速器)
    ├── exposure.py                 # R4: FOFA/Shodan 查询 + 被动探测(unauth_read, request_budget)
    └── findings.py                 # 三能力共用的 findings JSON 形状(对齐 pipeline items)
```

核心仓改动面仅两处:`src/myia/cli.py` 子命令注册(判例:`_add_proxy_parser` + `PROXY_PLUGIN_ID` 动态加载)、品类 YAML/`schema.py` 若需新字段的三锁同步(docs zh/en + `test_docs.py`/`test_skill_doc.py`)。

## 2. 模块映射与行为规格来源

| MYIA 侧 | 模仿对象(Downloads/aipocket-main, 只读) | 行为规格要点 |
|---|---|---|
| `fingerprints/` | `aipocket-discovery/src/packs.rs`(21 供应商) | 每供应商:识别 regex 集 + 探测端点/路径;数据驱动可扩展 |
| `ghhunt.py` | `discovery/github_artifacts.rs` + `clients/github.rs` | 统一 diff 解析:Added/Removed/Context 三侧、hunk 行号、diff/index/±文件头行跳过;**只取新增行**做指纹匹配;GitHub 搜索/工件分页语义 |
| `credcheck.py` | `prober/`(validator/engines/capability)+ `services/balance.rs` | models 探测先行(401/403=definitive auth rejection 判死);余额形状:balance_usd/tier/quota/usage/entitlements/identity/alive;串行 + 每供应商 RPM 上限 |
| `exposure.py` | `clients/fofa.rs`/`shodan.rs` + `prober/products.rs` | 查询语法/分页/配额语义;被动探测:已知路径 GET,2xx→unauth_read + 证据快照(≤512 字符),request_budget 内截断 |
| 复用不重写 | `services/`(scanner/scheduler/pipeline/analyzer)、`db/`、`api/`、`frontend/` | MYIA 已有 pipeline/dedup/SQLite 调度/桌面端;面板归 10-03-ui-deep-imitation |

**P0 交付物 = 行为规格文档**(本任务 `research/behavior-specs/`,按上表逐模块落「输入→规则→输出形状→边界情形」),实现只认规格不认 Rust 源——AGPL 纪律的执行载体。

## 3. 契约面

### 3.1 plugin.yaml(字段以 `src/myia/plugins/manifest.py` 为准)

`id: myia-credhunter` · `tier: desktop`(零 Docker 桌面默认集)· `requires: []` · `provides: [credhunt, credcheck, exposure]` · `adapter: {entry: adapter.py, mode: in_process}`。声明面变更 → **golden 基件同步**(在案教训)。

### 3.2 CLI 子命令(参照 `shishi proxy --json` 判例)

- `shishi credhunt --json` — R1 猎取,stdout 恰一份 JSON、日志 stderr
- `shishi credcheck --json` — R2 验证+余额(读库内凭证 items,回填探测结果)
- `shishi exposure --json` — R4 曝面发现
- 退出码族对齐全家桶:0 成功 / 1 配置错误 / 2 全部失败 / 3 部分失败;`--json` 下 stdout 恒可 `json.load`

### 3.3 品类 YAML(叠加式,不动现有行为)

- `plugins/credentials.yaml`:升级双源——`plugin:` 节挂 native(myia-credhunter)+ 原 remote 源(myia-credentials,降为可选聚合,Q2 决议);dedup.key 用字段组合(禁 `{title}`,凭据无语义 URL)。
- `plugins/exposure.yaml`(新):FOFA/Shodan 品类;key 全走 `keychain:myia/...` / `env:` 引用,无 key 显式空态(AC6)。

### 3.4 凭据与安全

GitHub token / FOFA key / Shodan key 一律引用制,永不明文(启动拒载铁律);探测限速默认串行;qps/jitter 走既有 `rate_limit` 节语义。凭据命中物 = 私有情报:不入 git、不外发、fixture 脱敏。

## 4. 数据流

```
R1: GitHub API(token) → 工件 → diff 解析 → 新增行指纹匹配 → findings ─┐
R4: FOFA/Shodan(key) → targets → 被动探测(budget 内) → findings ─────┤→ pipeline(classify/dedup/store/push)
R2: store 凭证 items → credcheck(串行+RPM 上限) → alive/balance 回填 → store
```

## 5. 取舍记录

- **单插件多 provides vs 多插件**:单插件(凭据线内聚、一个安装单元);R4 若边界膨胀可独立成件再拆。
- **实现位置**:插件目录自包含(判例 myia-proxy 的 adapter.py);核心包不收猎手代码。
- **不做什么**:db/api/frontend 不融(形态不同);scanner/scheduler/pipeline 不重写(复用);不做高频扫描、不碰验证码/登录墙源(既有引擎纪律)。
- **R2 余额探测的供应商端点清单**:以行为规格研究为准;无法匿名探测的端点诚实标 `unknown` 不硬造(SiliconFlow 判例精神)。

## 6. 兼容与回滚

- 兼容:现有品类/插件零行为变更;credentials.yaml 为叠加式升级(原 remote 源保留);插件装不上不拦核心流水线(铁律)。
- 回滚点:P1–P3 每阶段独立可回滚 = 删插件目录 + 还原对应品类 YAML + 撤 cli.py 注册行;store 无破坏性 schema 变更(R2 回填走 findings 侧字段)。

## 7. 分阶段交付(P0→P4,每阶段真跑验证后才进下阶段)

| 阶段 | 内容 | 出口判据 |
|---|---|---|
| P0 | 行为规格四件(fingerprints/ghhunt/credcheck/exposure) | 规格评审过,实现零开工 |
| P1 | R3 指纹库 + R1 ghhunt + CLI 子命令 + 最小品类挂接 | 真跑 GitHub 工件端到端 ≥1 条 finding 入库 |
| P2 | R2 credcheck + 限速器 + 回填 | 真跑对真实供应商端点验证/余额各 ≥1 条 |
| P3 | R4 exposure + exposure.yaml + credentials.yaml 双源升级 | FOFA/Shodan 真跑(主人 key);无 key 空态演练 |
| P4 | wrap:docs zh/en、golden、测试、真跑证据矩阵、AC 全核 | AC1–AC6 全绿 |
