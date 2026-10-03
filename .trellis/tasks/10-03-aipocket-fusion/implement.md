# implement — 执行清单

> 任务:10-03-aipocket-fusion · 开工门 = dwfrun-0e1749cb 大工作流收尾(grill Q3 决议)
> 纪律:每阶段出口判据达成才进下一阶段;AGPL 零入仓(AC1)贯穿全程;凭据命中物私有情报红线(AC3)。

## P0 行为规格(研究件,不写实现代码)

- [ ] `research/behavior-specs/fingerprints.md` — 21 供应商包:识别规则形状、探测端点语义、数据文件格式设计
- [ ] `research/behavior-specs/ghhunt.md` — GitHub 搜索/工件分页语义;统一 diff 解析边界情形(Added/Removed/Context、hunk 行号、文件头跳过、二进制/超大 diff 截断);新增行匹配流程
- [ ] `research/behavior-specs/credcheck.md` — models 探测先行 + 401/403 判死语义;余额/配额/身份字段形状;每供应商可探测性矩阵(不可匿名探测→`unknown`)
- [ ] `research/behavior-specs/exposure.md` — FOFA/Shodan 查询语法/分页/配额;被动探测路径集、request_budget、证据快照规则;无 key 空态行为
- [ ] 规格评审门:规格与 PRD/design 对齐,主人过目或按推荐默认

**验证**:`rg -i 'aipocket' research/behavior-specs/` 仅叙述性引用,无代码搬运。

## P1 R3 指纹库 + R1 GitHub 猎手(最小闭环)

- [ ] `plugins/myia-credhunter/` 骨架:plugin.yaml(tier desktop/in_process/provides 三能力)+ README(授权定位+凭据引用)+ adapter.py 挂点
- [ ] `credhunter/fingerprints/`:数据文件 + loader + 扩展接口;单测覆盖 loader
- [ ] `credhunter/ghhunt.py`:GitHub 客户端(token 引用制)+ diff 解析器(单测用脱敏 diff fixture)+ 指纹匹配
- [ ] `credhunter/findings.py`:findings JSON 形状(对齐 pipeline items;dedup.key 字段组合,禁 `{title}`)
- [ ] `src/myia/cli.py`:注册 `credhunt` 子命令(动态加载,参照 `_add_proxy_parser` 判例)
- [ ] 品类最小挂接:credentials.yaml 叠加 native 源(remote 源保留)
- [ ] golden 基件同步(插件声明面变更)

**验证**:`uv run pytest tests/ -x -q` 全绿;`shishi test plugins/credentials.yaml --json` 源 `ok`;`shishi credhunt --json` 真跑 ≥1 条 finding;`shishi doctor --json` findings 清零。

## P2 R2 验证 + 余额

- [ ] `credhunter/credcheck.py`:models 探测 → 401/403 判死 → 余额/配额/身份回填;不可探测端点诚实 `unknown`
- [ ] 限速器:默认串行 + 每供应商 RPM 上限(数值按 P0 规格);`shishi credcheck --json` 子命令
- [ ] store 回填路径(不破坏现有 schema);脱敏 fixture 单测
- [ ] docs:README 补 credcheck 用法

**验证**:真跑对 ≥1 个真实供应商端点完成验证/余额各 ≥1 条;限速可观测(日志含节流证据);pytest 全绿。

## P3 R4 FOFA/Shodan 曝面 + 品类升级

- [ ] `credhunter/exposure.py`:FOFA/Shodan 客户端(key 引用制)+ 被动探测(budget 内,证据快照脱敏规则)+ 无 key 显式空态
- [ ] `plugins/exposure.yaml` 新品类;credentials.yaml 双源终态(native 主力 + remote 可选聚合)
- [ ] `shishi exposure --json` 子命令;schema 若新增字段→`schema.py` + docs zh/en + `test_docs.py`/`test_skill_doc.py` 三锁同步
- [ ] golden 同步

**验证**:主人 key 真跑 FOFA/Shodan 各 ≥1 轮;无 key 空态演练(AC6);`shishi run plugins/exposure.yaml --dry-run --json` 全链演练通过;pytest 全绿。

## P4 wrap(收官)

- [ ] docs zh/en 双语同步(`docs/zh|en/write-a-plugin.md` 相关段、新插件文档)
- [ ] AC1 全仓核验:`rg -i 'aipocket' --hidden` 仅允许叙述性引用;无 AGPL 文件头/上游标识符/逐行翻译痕迹
- [ ] AC2 真跑证据矩阵(P1–P3 证据汇总,落 research/)
- [ ] AC 全核(AC1–AC6)+ 全量回归:`uv run pytest tests/ -q`
- [ ] spec 更新(3.3):plugins/python spec 若有新模式(进程内多能力件)入档
- [ ] 提交(3.4):分阶段提交已随做随提,P4 收总

## 回滚点

- P1 回滚:删 `plugins/myia-credhunter/` + 还原 credentials.yaml + 撤 cli.py 注册行
- P2 回滚:credcheck 独立文件,撤子命令即回
- P3 回滚:删 exposure.yaml + exposure.py,credentials.yaml 双源还原为 P1 态
- 全程不动:现有品类行为、核心 pipeline、store schema(只增不破)
