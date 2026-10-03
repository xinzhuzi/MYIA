# implement — 执行清单(2026-10-03 深化版)

> 任务:10-03-aipocket-fusion · 开工门 = dwfrun-0e1749cb 大工作流收尾(grill Q3 决议)
> 纪律:每阶段出口判据达成才进下一阶段;AGPL 零入仓(AC1)贯穿全程;实现只认 `research/behavior-specs/` 四件规格,**不回看 aipocket 源码**;凭据命中物私有情报红线(AC3)。
> 证据基线:`research/myia-integration-facts.md`(file:line 落点)。

## P0 行为规格+集成真值 ✅(2026-10-03 已完成)

- [x] behavior-specs/ghhunt.md(两实况泳道+diff 解析器边界+token 轮转/限速语义)
- [x] behavior-specs/credcheck.md(resolve 链+三态归类+BalanceResult 17 字段+13 家可探测矩阵)
- [x] behavior-specs/fingerprints.md(20 发现包+25 验证规格+三套指纹+噪声过滤+URL 规范化)
- [x] behavior-specs/exposure.md(FOFA/Shodan 客户端+被动探测+scan 编排+默认参数速查)
- [x] myia-integration-facts.md(引擎通路 A/CLI 注册三件套/golden 五处同步/keychain/限速真值)
- [x] design.md 按实证修订(两大假设修正:diff 泳道未接线、无进程内进管线现成路)

## P1 R3 指纹库 + R1 GitHub 猎手 + 引擎接线(最小闭环)

- [ ] `plugins/myia-credhunter/` 骨架:plugin.yaml(tier desktop / adapter in_process / provides 三能力)+ README(授权定位+keychain 引用+上游参数表)+ adapter.py 挂点
- [ ] `credhunter/packs.py` + `specs.py`:发现层 20 包 + 验证层 25 规格,数据文件化(YAML/JSON),loader+单测;扩展=加数据不改码
- [ ] `credhunter/fingerprints.py`:十大族联合正则 + 17 条细正则 + 变量名归因表 + 噪声过滤(全按 fingerprints.md §7,语义对齐)
- [ ] `credhunter/ghhunt.py`:code 泳道(1..=5×100、公开性校验、噪音路径过滤、blob ≤1MiB)+ commit 泳道(第 1 页、≤12 查询)+ token 池轮转 + 403/429 退避(≤90s 单轮)+ apiurl 前缀归因映射 + 工件二次加工(≤200/并发 8/重试 5;diff 解析增强件在此接线)
- [ ] `credhunter/findings.py`:items 形状(对齐 pipeline 期望;dedup.key 字段组合,按 dedup.py 实测语义定稿,禁 {title});**apikey 出 items 一律前 8 后 4 掩码(Q9),全文只留 store 内部字段**
- [ ] **引擎注册三锁**:schema.py:175-177 EngineName + registry.py:84-92 + 新增 `src/myia/engines/credhunter.py`(动态加载 adapter;缺失→EngineFailure 结构化降级照 registry.py:179-212)
- [ ] `src/myia/cli.py`:credhunt 子命令(挂载 :265 / handlers :2804 / 常量 :175-182,proxy 样板)
- [ ] 品类挂接:credentials.yaml 加 `engine: credhunter` 源(remote 源保留)
- [ ] **docs 三锁同步**:docs/zh/schema.md + docs/en/schema.md 引擎表 + write-a-plugin.md 引擎分级表 + test_docs.py/test_skill_doc.py 期望
- [ ] **golden/官方件五处同步**(integration-facts.md §4):test_plugins.py:48 / test_plugin_packages.py:51-58+61-68 / push_targets golden(credentials.yaml push 若动)/ TestCategoryWiring / 桌面种子断言(exposure.yaml 若进默认集,P3 处理)

**验证**:`uv run pytest tests/ -x -q` 全绿(含三锁);`shishi test plugins/credentials.yaml --json` 源 ok;`shishi credhunt --json` 真跑 ≥1 条 finding;`shishi run plugins/credentials.yaml --dry-run --json` 全链演练;`shishi doctor --json` findings 清零;`rg -i 'aipocket' plugins/ src/` 仅叙述性引用。

## P2 R2 验证 + 余额

- [ ] `credhunter/credcheck.py`:resolve 链(域名→前缀→unknown;aws_bedrock 域名特判)→ 三态归类(final_verified / rejected=401/403 或 2xx 无模型 / transient)→ BalanceResult 17 字段回填;13 家匿名探测矩阵逐家按 credcheck.md §5;仅存活性家族(models_liveness);kiro/azure/vertex=无探测;header ASCII/CRLF 安全闸;**存活探测默认开、余额/身份探测默认关显式开(Q7);串行+每供应商 RPM≤30(Q8)**
- [ ] CLI `shishi credcheck --json`:读库凭证 items → 探测 → 回填 store(findings 侧字段,无破坏 schema);串行 + 每供应商 RPM 上限(PRD Q4)
- [ ] 脱敏 fixture 单测(每供应商至少一条 happy path + 一条 401 判死);不可探测端点诚实 unknown 断言
- [ ] README 补 credcheck 用法与授权定位重申

**验证**:真跑 ≥1 真实供应商端点验证+余额各 ≥1 条(主人 token/key);限速日志可观测;pytest 全绿。

## P3 R4 FOFA/Shodan 曝面 + 品类终态

- [ ] `credhunter/exposure.py`:FOFA 客户端(base 可配占位/qbase64/12 fields/页 ≤10×100 提前停/页间 0.3s/run 预算 24 查询)+ Shodan(FOFA 查询机械翻译/行归一化三级兜底/非 80/443 拼端口/页间 1.0s/预算 16)+ 被动探测 L0 unauth_read only(产品路径集按 exposure.md §4;2xx→finding;证据 512 字符;预算 12;fail-closed:非 L0 一律拒,开关留二期)
- [ ] 无 key 显式空态(keychain 引用缺失 → doctor finding + run 时源级结构化降级,不报错不静默——AC6)
- [ ] `plugins/exposure.yaml` 新品类 + credentials.yaml 双源终态(native 主力 + remote 可选聚合)
- [ ] 引擎参数面:credhunter engine 的 source 级参数(github/fofa/shodan 能力开关,按 schema sources[] 开放扩展语义定);golden 五处同步(exposure.yaml 进官方件/桌面种子断言决策)
- [ ] CLI `shishi exposure --json`(调试面)

**验证**:主人 key 真跑 FOFA/Shodan 各 ≥1 轮(命中数如实记录,0 命中≠失败);无 key 空态演练;`shishi run plugins/exposure.yaml --dry-run --json` 通过;pytest 全绿。

## P4 wrap(收官)

- [ ] docs zh/en 双语同步(write-a-plugin/schema/新插件文档/zero-cost.md 若涉免费层宣称)
- [ ] AC1 全仓核验:`rg -i 'aipocket' --hidden` 仅叙述性引用;无 AGPL 文件头/上游标识符/逐行翻译
- [ ] AC2 真跑证据矩阵(P1–P3 证据汇总落 research/)
- [ ] AC 全核(AC1–AC6)+ 全量回归 `uv run pytest tests/ -q`
- [ ] spec 更新(3.3):进程内多能力件+新引擎模式若成惯例,入 python spec
- [ ] 提交(3.4):分阶段随做随提,P4 收总

## 回滚点

- P1 回滚:撤 EngineName/ENGINE_REGISTRY 两行 + 删 plugins/myia-credhunter/ + 还原 credentials.yaml + 撤 cli.py 注册 + 还原 docs 三锁
- P2 回滚:credcheck 独立文件+子命令,撤注册即回;store 回填字段无害残留
- P3 回滚:删 exposure.yaml + exposure.py;credentials.yaml 还原 P1 态
- 全程不动:现有品类行为、核心 pipeline、store schema(只增不破)

## 已知风险(深化期识别)

- `EngineName` 公开契约变更是本任务唯一动「12 节 schema」的点——三锁+golden 漏一处即全量红(games-v2 golden 教训在案)。
- GitHub 无 token = 源不启用(上游同款);桌面首跑无 token 时空态引导要在 README/doctor 讲清。
- Shodan 无 scheme host 拼探测 URL 的成功率坑(exposure.md 未核实项)——实现时先做 scheme 归一再探测,与上游差异记档。
- 适配器 `import myia.*` 无先例(integration-facts.md §6)——P1 评审时给出口径(读 keychain/限速器=宿主能力)。

## P4 残留清单(2026-10-03 交付后,质检核实 7 条;主人过目后逐项收)

- [ ] **发现4(low)**:plugins/credentials.yaml push 仍 feishu_card,与 Q9「官方品类模板默认 stdout」不一致(exposure 已 stdout)——改 stdout 需同步 push_targets golden(改值=取值漂移,golden 手工同改)。
- [ ] **发现5(low)**:credcheck「读库→探测→回填」口径(adapter.py/cli.py/README 三处 docstring)与实现(--apikey 显式传键、零 store 代码)分叉——store 回填通路补齐或口径改写,二选一。
- [ ] **发现6(low)**:ghhunt.py:15 docstring「run 预算 12 条查询」重复连写两遍。
- [ ] **发现(低,规格文档)**:behavior-specs/credcheck.md 仍钉上游字面 error/source 值,实现已本地化机器码(auth_denied/no_api_url 等)——规格加注「输出值已本地化,上游字面值仅指语义」。
- [ ] **发现3(线权)**:plugins/myia-credentials/plugin.yaml compatible('>=0.1,<2.0'→'0.0.1 矩阵')工作树已修但属 tag-release 线领地不代收——提交树该行仍旧,依赖 tag-release 线版本落定后同批收。
- [ ] **发现7(提交卫生,主人裁决)**:ca613ee 混入 weixin 通道文档行(skill/SKILL.md,疑 messaging W2 在途内容被收编);未推送,可 rebase 拆分——是否重写历史归主人。
- [ ] **主人侧三项**:myia secret set myia/credhunter/github-token(credhunt 真跑 ≥1 finding,AC2)/活 key 显式 --balance(余额矩阵验证)/myia/credhunter/{fofa,shodan}-key(曝面真跑)。
