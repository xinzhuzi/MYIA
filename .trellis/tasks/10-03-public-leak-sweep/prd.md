# 公开仓私有信息清扫(泛化 88 处命中)

> 2026-10-03 主人令「按照你的建议做完它」(经 /workflow 起动态工作流 dwfrun-0cb2078c 执行)。前序:任务 10-03-spec-discipline-sop-gitnexus 决议 1 泛化了该任务范围的私有名,并报告了 image-input 残留;扩大扫描后发现泄漏面远不止于此。
> 本文档自身不出现私有标识字面量(它们是清扫对象);四词模式与真实指针的字面量存 LOCAL-NOTES.md。

## 背景

本仓库与 `.trellis/` 全部公开(开源定位;GitHub 已改名 shishi 并推公)。LOCAL-NOTES.md 红线:真实姓名、本机绝对路径、私有应用名、私有仓库名零容忍出现在 git 跟踪内容里。

四词模式 grep(私有应用中文名|私有仓库名|本机用户名|真实姓名,字面量见 LOCAL-NOTES.md)实测 **88 处命中**(EvalWorkflowSnippet 2026-10-03),分布:10-03-image-input(evidence 多文件)、10-03-ci-gates、10-03-gap-census 等已归档任务的 prd/jsonl/evidence——多为本机绝对路径(用户名前缀)、私有应用名、私有仓库名。

## Requirements

1. **扫描分类**:全量命中逐处判定 leak(需泛化)/ benign(合法提及)。
2. **逐文件泛化修复**,统一口径:
   - `/Users/<本机用户名>` 前缀 → `~`(home 相对化,其余路径段保留)
   - 私有应用名 → 「另一私有应用(真实名与路径见 LOCAL-NOTES.md)」
   - 私有仓库名 → 「另一私有仓库(真实指针见 LOCAL-NOTES.md)」
   - 真实姓名命名的本地外置卷 → 「本地外置卷(路径见 LOCAL-NOTES.md)」
   - 技术事实(模型文件名/字节数/耗时/命令)原样保留;单行内替换,不增删行
3. **LOCAL-NOTES.md 登记真实指针**(私有应用 ComfyUI 模型库等),承接被泛化的可追溯性。
4. **复扫归零门禁**:同一 grep 复扫,残留(非 benign)触发返修,轮上限 2。
5. **独立复核**:读 git diff 验只泛化不失实、未引入新泄漏、剩余命中合法理由成立。

## Acceptance Criteria

- [x] leak 类命中全部泛化,复扫残留 = 0(终扫门禁 exit=1 零命中;独立复核通过)
- [x] 修复未删改技术事实,diff 仅含泛化(复核员逐文件读过 git diff)
- [x] LOCAL-NOTES.md 有对应真实指针条目(档案员登记+复核员确认;主会话合并了修复员越权写入的重复条目)
- [x] 工作流产出清扫报告(markdown 工件)+ WorkflowReport 四字段

## 执行结果(2026-10-03)

- 工作流四阶段全过:88 处命中全判 leak、13 文件并行泛化、复扫归零、独立复核通过;重灾区为 image-input 三份 e2e transcript jsonl(共 74 处)
- 提交 `c97c897`(18 文件);主会话两处人工兜底:①`desktop/myia-core.spec` 工作流盲改 `~/` 字面量会炸构建(Python 不展开 ~)——查明该 spec 无消费方(build-sidecar.sh 走 CLI 现场生成),改 SPECPATH 相对化修复(py_compile 过),顺手治好本机硬编码旧病;②修复员越权抢写 LOCAL-NOTES 致双条目,已合并去重
- **门禁盲区教训(本收尾轮抓获)**:任务自身记账(prd/task.json 写四文字面量)在未跟踪状态下躲过 git grep 门禁(只搜已跟踪文件),提交后词入跟踪树——已泛化重写;凡新任务文档提私有标识,一律用「见 LOCAL-NOTES.md」指代,门禁须在提交前对将入库文件跑
- git grep 门禁接管道会吞 exit code(`| head` 后 `$?` 是 head 的),正规跑法无管道取码

## 明确不做

- **不重写 git 历史**:已 push 的历史提交中旧版本泄漏仍在(重写需 force-push,主人门禁);本任务只清当前工作树/未来提交面
- 不扫未跟踪文件(LOCAL-NOTES 本身、.gitnexus 等本机文件非公开面)
- 模式外私有形态(其他别名/新造词)不覆盖,模式固定为上述四词
