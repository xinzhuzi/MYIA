# 公开仓私有信息清扫(泛化 88 处命中)

> 2026-10-03 主人令「按照你的建议做完它」(经 /workflow 起动态工作流执行)。前序:任务 10-03-spec-discipline-sop-gitnexus 决议 1 泛化了该任务范围的私有名,并报告了 image-input 残留;扩大扫描后发现泄漏面远不止于此。

## 背景

本仓库与 `.trellis/` 全部公开(开源定位;GitHub 已改名 shishi 并推公)。LOCAL-NOTES.md 红线:真实姓名、本机绝对路径、私有应用名、私有仓库名零容忍出现在 git 跟踪内容里。

`git grep -i -E '漫影|MYStudio|zhengbingjin|郑冰津'` 实测 **88 处命中**(EvalWorkflowSnippet 2026-10-03),分布:10-03-image-input(evidence 多文件)、10-03-ci-gates、10-03-gap-census 等已归档任务的 prd/jsonl/evidence——多为 `/Users/zhengbingjin/...` 本机绝对路径、`漫影工作室` 私有应用名、MYStudio 私有仓库名。

## Requirements

1. **扫描分类**:全量命中逐处判定 leak(需泛化)/ benign(合法提及,如指向 LOCAL-NOTES 的引用)。
2. **逐文件泛化修复**,统一口径:
   - `/Users/zhengbingjin` → `~`(home 相对化,其余路径段保留)
   - `漫影工作室` → 「另一私有应用(真实名与路径见 LOCAL-NOTES.md)」
   - `MYStudio` → 「另一私有仓库(真实指针见 LOCAL-NOTES.md)」
   - `/Volumes/郑冰津` 本地外置卷 → 「本地外置卷(路径见 LOCAL-NOTES.md)」
   - 技术事实(模型文件名/字节数/耗时/命令)原样保留;单行内替换,不增删行
3. **LOCAL-NOTES.md 登记真实指针**(漫影 ComfyUI 模型库等),承接被泛化的可追溯性。
4. **复扫归零门禁**:同一 grep 复扫,残留(非 benign)触发返修,轮上限 2。
5. **独立复核**:读 git diff 验只泛化不失实、未引入新泄漏、剩余命中合法理由成立。

## Acceptance Criteria

- [ ] leak 类命中全部泛化,复扫残留 = 0(或均为 benign 判定且复核通过)
- [ ] 修复未删改技术事实,diff 仅含泛化
- [ ] LOCAL-NOTES.md 有对应真实指针条目
- [ ] 工作流产出清扫报告(markdown 工件)+ WorkflowReport 四字段

## 明确不做

- **不重写 git 历史**:已 push 的历史提交中旧版本泄漏仍在(重写需 force-push,主人门禁);本任务只清当前工作树/未来提交面
- 不扫未跟踪文件(LOCAL-NOTES 本身、.gitnexus 等本机文件非公开面)
- 模式外私有形态(其他别名/新造词)不覆盖,模式固定为上述四词
