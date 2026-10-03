# 执行报告 — 10-03-aipocket-fusion(2026-10-03)

> 撰写:收官文员(执行工作流末段)。口径:事实 + 证据路径,没做到的明写。
> 标注约定:**[仓库实证]** = 本员本轮在仓库实际读文件/跑命令核实(附路径或命令);**[流内转述]** = 执行工作流传给本员的事实,仓库内无独立日志可复核。
> 门禁纪律:全量 pytest / ruff / shishi 演练由脚本统一跑,本报告**不含**也不冒充门禁结果(ask 明令:收官文员不自行跑全量 pytest)。

## 1. 阶段清单

| 阶段 | 结果 | 依据 |
|---|---|---|
| 基线红 0 条 | 开工基线全绿(0 红) | [流内转述] |
| 插件包落地(P1a) | 纯增量插件包 + 四件单测,7885 行 | [仓库实证] 提交 `a862bcd`(14 文件:plugin.yaml/README/adapter.py/credhunter 七模块 + 测试四件) |
| 插件包门禁 | 绿 | [流内转述](脚本侧统一跑) |
| 提交两笔 | `a862bcd` P1a + `ca613ee` P1b-P3,均已入 main | [仓库实证] `git log --grep='aipocket-fusion'` 恰两笔 feat |
| 等门轮次 | 接线段等并行工作流交出热文件(schema.py/cli.py/docs schema/test_plugins.py)后开工;轮次数字未随 ask 给明细 | [仓库实证] task.json notes「执行工作流分两段:插件包纯增量先行,接线段等热文件…清空」 + [流内转述] |
| 接线(P1b-P3) | engine: credhunter 注册 + CLI 三子命令 + credentials 双源 + exposure 品类 + docs 三锁 + golden 同步,1831 行 | [仓库实证] 提交 `ca613ee`(18 文件) |
| 接线门禁 | 绿 | [流内转述](脚本侧统一跑) |
| 独立质检 | **已修 3 项;残留 2 项**(残留条目明细未随 ask 给出,以脚本侧质检记录为准) | [流内转述];工作树可见未提交修复(见 §5) |
| 真跑验证 | 两条可自跑路径全绿;三条主人侧路径如实记 ownerSide | [仓库实证] `research/evidence/` 三件(见 §2/§3) |

## 2. 真跑详情(证据:`research/evidence/`)

### 2.1 credcheck 401 判死路径 — ran,全绿(断言 6/6)

- 方法:模块函数 `check_credential` 真跑,经 `import_credhunter_adapter('plugins')` 加载适配器(与 CLI 同一加载通路);输入 = 合成假 key(真前缀 `sk-` + 假串,掩码 `sk-0f1e2…MASKED…e1f0`)+ `apiurl=https://api.deepseek.com`(域名归因)。
- 真实出网:对 `https://api.deepseek.com/v1/models` **单次 GET**(Bearer,15s 超时);`probe_balance` 默认关(Q7),零余额/身份端点请求。
- 断言 6/6 通过([credcheck-401-death-path.json](evidence/credcheck-401-death-path.json) `checks` 全 true、`all_passed: true`):
  `status_code=401` → `key_state=expired`(credcheck.md §3「credential expired or revoked」)→ `validation_state=rejected` / `error=auth_denied`(credcheck.md §2.6 三态判死语义);`balance=None`(Q7);全文假键零出现在任何输出字段(Q9;deepseek 回包本身只回掩码尾 4)。

### 2.2 exposure 无 key 空态 — ran,全绿(断言 7/7)

- 命令:`MYIA_HOME=/tmp/myia-credhunter-smoke .venv/bin/shishi run plugins/exposure.yaml --dry-run --json` → exit 0。
- 断言 7/7 通过([exposure-empty-state.json](evidence/exposure-empty-state.json) `checks` 全 true、`all_passed: true`):
  run `status=success` 且 `failures=[]`;`exposure-scan` 源 `skipped=true / skip_reason=credential_missing / item_count=0 / failed=false / error=null`(AC6 结构化空态,stderr warning 留痕不静默);同品类 `manual-triage` 本地 scan lane 照常 1 条,`apikey_masked` 与 `mask_apikey()` 逐字符一致(前 8 后 4),全文合成键零泄漏;push stdout 且 `dry_run=true` 不实发。

### 2.3 credhunt GitHub 真跑 — ownerSide(缺 token,零出网判死)

- 钥匙串只读复核:`MYIA_HOME=/tmp/myia-credhunter-smoke .venv/bin/shishi secret list` → 仅 `myia/image/api_key`,无 `myia/credhunter/github-token`(与脚本所查一致)→ 按口径记主人侧。
- 佐证(零出网):`shishi credhunt --json` → 结构化 `{"error": "tokens_missing", ...}` exit 1,与 ghhunt.md §2「无 token 该源不启用」语义一致。
- 达标动作(主人侧):`myia secret set myia/credhunter/github-token` 后,引擎面品类 YAML 源经 `github_tokens` keychain 引用、或 CLI `--github-token` 真跑,≥1 finding 即达 AC2。

### 2.4 未跑项(主人侧,如实记录)

- **credcheck 活 key 路径**(AC2/Q10「主人自备活 key:final_verified + 余额正确」)与**余额矩阵(13 家匿名端点)**:主人活 key 未备,未跑;待主人 key 显式 `--balance` 验证。
- **FOFA/Shodan 有 key 路径**:钥匙串无 `myia/credhunter/fofa-key` 与 `shodan-key`,未跑(AC6 无 key 空态路径本次已验证,见 §2.2)。

## 3. 证据文件清单

| 文件 | 内容 | git 状态 |
|---|---|---|
| `research/evidence/real-run-smoke.md` | 真跑总结:结论一览表 + 四段步骤/命令/断言 | 未跟踪(`git status` `??`) |
| `research/evidence/credcheck-401-death-path.json` | 401 判死路径完整证据(掩码-only 回包 + 6 断言 + 规格对齐表) | 未跟踪 |
| `research/evidence/exposure-empty-state.json` | 空态演练完整 run payload + 7 断言 + stderr 留痕 | 未跟踪 |

证据目录整体不入 git、不外发(AC3 口径;[仓库实证] `git status` 显示 `?? .trellis/tasks/10-03-aipocket-fusion/research/evidence/`,`git check-ignore` 未命中=非 ignore 而是刻意未 add)。文件内只存掩码形态(`fake_key_masked` / `apikey_masked`),无任何全文键。

## 4. PRD AC1–AC6 逐条状态

### AC1 零代码污染 — 达标(本员复核口径;权威判定 = 脚本门禁 rg + 全量)

- [仓库实证] 本员跑 `rg -il 'aipocket' plugins/myia-credhunter src/myia/engines src/myia/schema.py src/myia/cli.py` → 13 文件命中,逐条抽看全为叙述性引用(任务号/行为规格路径/许可边界注释,如 `plugins/myia-credhunter/plugin.yaml:6`、`src/myia/schema.py:160`、`src/myia/engines/registry.py:91`),无 AGPL 文件头、无上游代码搬运形态。P1a/P1b 两笔提交说明均自declared「AGPL 零入仓」。
- 质检后续:工作树未提交改名 `ProviderPack→QueryPack`、`ProviderSpec→ProviderProfile`、`ProviderRegistry→ProviderResolver`(见 §5)——上游标识符(fingerprints.md §1 载 `ProviderPack`/`ProviderSpec`、credcheck.md §2 载 `ProviderRegistry.resolve` 均为上游 Rust 侧标识符)全部清除;[仓库实证] `rg 'ProviderPack|ProviderSpec' tests/ plugins/myia-credhunter/` 零命中。

### AC2 真跑验证 — 部分达标,缺口如实记主人侧(Q10 口径内)

- 401 判死路径:ran 全绿(§2.1)。
- 活 key 冒烟(final_verified + 余额正确):**未跑**,主人活 key 未备(§2.4)。
- R1 GitHub 真跑:**未跑**,缺 token → 按口径记主人侧待办 + fixture 回归兜底(§2.3;测试四件在库:`tests/test_credhunter_{ghhunt,credcheck,fingerprints,exposure}.py`)。
- FOFA/Shodan 真跑:**未跑**,缺 key(§2.4);无 key 空态演练 = AC6 已验。录制回放未用于放行。

### AC3 私有情报红线 — 达标

- [仓库实证] 证据文件全掩码(§3);`research/evidence/` 未入 git。
- [仓库实证] `tests/test_plugins.py:610-631` exposure 品类命中物形状钉「掩码-only」(`"exposure": "sk-4f1c9…MASKED…abcd"`);`plugins/credentials.yaml:12` 注释钉「full keys never enter items/push templates」;真跑 payload 内 push 走 stdout + dry_run 不实发(§2.2)。
- 测试 fixture 脱敏:测试四件随 P1a 入库([仓库实证] `git show --stat a862bcd`);逐 fixture 抽验未做(以脚本侧全量 + 质检为准)。

### AC4 工程配套 — 达标(接线面本员实证;测试全绿权威在脚本门禁)

- docs zh/en 同步:[仓库实证] `ca613ee` 触 `docs/{zh,en}/schema.md`、`docs/{zh,en}/write-a-plugin.md`、`docs/write-a-plugin.md`、`skill/SKILL.md`(三锁文件全在提交清单)。
- 测试齐:[仓库实证] 五件 credhunter 测试在库(四件 P1a + `tests/test_credhunter_wiring.py` P1b)。
- golden 同步(五处清单 = myia-integration-facts.md §4):[仓库实证] ① `tests/test_plugins.py:65` OFFICIAL_PLUGINS 补 exposure;② `tests/test_plugin_packages.py:60,71` OFFICIAL_PACKAGES 补 myia-credhunter + EXPECTED_TIERS desktop;④ `tests/test_plugin_packages.py:260` TestCategoryWiring 钉 exposure ↔ myia-credhunter 咬合;⑤ 桌面种子断言**未动** = Q12 决议「首跑种子四件套不加」;③ push golden 文件未改(`ca613ee` 提交清单无 `tests/fixtures/push_targets_golden_before.json`)——credentials.yaml push 节未动([仓库实证] `git show ca613ee -- plugins/credentials.yaml` 增改仅注释 + native 源,push: 节在 :76 未入 diff),exposure 走 stdout 通道例外(`tests/test_plugins.py:453` 注释)。

### AC5 插件体系合规 — 达标(接线面本员实证;全量回归权威在脚本门禁)

- [仓库实证] `plugins/myia-credhunter/plugin.yaml`:id `myia-credhunter`、version 0.1.0、tier `desktop`、`requires: []`(纯进程内,零 docker 零服务依赖);`tests/test_plugin_packages.py:133` 注明官方件电池对其「纯进程内源码件」例外形状的口径。
- [仓库实证] 引擎注册:`src/myia/schema.py:161,179` EngineName 词表含 `credhunter`;`src/myia/engines/registry.py:101` ENGINE_REGISTRY 注册,且注释钉死「链外源引擎:auto 永不路过,显式选择才生效」(:91-92)。
- [仓库实证] CLI 三子命令:`src/myia/cli.py:285-287` 挂载 credhunt/credcheck/exposure parser,handlers 分发 :2886-2893 起(proxy 样板三件套齐)。
- [仓库实证] credentials 双源终态:`plugins/credentials.yaml:36` native 源 `engine: credhunter` + :50 remote 源 `direct_api` 保留(Q2 共存决议);现有品类/pipeline/store 零破坏以脚本全量门禁为准。

### AC6 R4 空态合规 — 达标

- [仓库实证] 真跑 7/7(§2.2):无 key 时源级 `skipped=true/skip_reason=credential_missing/item_count=0/failed=false/error=null`,run 仍 `success`、`failures=[]`,stderr warning 留痕——「显式空态、不报错不静默」逐字段满足。
- 被动探测证据快照私有红线:exposure.py 证据 512 字符口径按 exposure.md §4 实现(P3 提交 `a862bcd`/`ca613ee` 内);有 key 真跑未发生,快照红线未见违例形态。

## 5. 工作树残留(如实记录,非门禁项)

[仓库实证] 两笔提交之后,`plugins/myia-credhunter/` 尚有**未提交**改动(`git diff --stat HEAD` 共 8 文件,+456/-107):

- 标识符改名 ×3:`ProviderPack→QueryPack`、`ProviderSpec→ProviderProfile`、`ProviderRegistry→ProviderResolver`(packs.py/specs.py/credcheck.py/adapter.py);
- 指纹数据外置:新增 `credhunter/data/provider_packs.yaml`(154 行)+ `provider_specs.yaml`(195 行),loader 改读数据文件(R3「加供应商不动核心」纪律);
- `plugin.yaml` compatible 版本窗 `">=0.1,<2.0"` → `">=0.0.1,<0.1"`(对齐 tag-release 版本序列归零)。

该批改动与 ask 所给「质检已修 3 项;残留 2 项」的对应关系未随 ask 给明细,本员不做归属推断;其提交与残留 2 项的处置归脚本/主会话侧。改动范围内测试引用一致性已核:`rg 'ProviderPack|ProviderSpec' tests/ plugins/myia-credhunter/` 零命中(旧标识符无残留引用)。

## 6. 未做/未能验证清单(勿虚报)

- 全量 pytest、ruff、shishi 演练门禁:未跑(ask 明令由脚本统一跑;本员只读了接线面代码与证据文件)。
- 质检「已修 3 项/残留 2 项」的具体条目:仓库内无质检日志,ask 未给明细,未验证。
- 等门轮次的轮次数字:ask 未带,未验证。
- §2.4 三条主人侧真跑(credhunt token、活 key + 余额矩阵、FOFA/Shodan key):未跑,证据与达标动作已落档。
- AC3 逐 fixture 脱敏抽验、AC1 全仓 `--hidden` rg:未逐件复跑(本员抽查为 `plugins/`+`src/` 范围;权威以脚本门禁为准)。
