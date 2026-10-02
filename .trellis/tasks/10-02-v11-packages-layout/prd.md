# packages 目录层级整理:消除 myia-classifier 路径套娃

## 问题(2026-10-02 主人指出)

`packages/` 里的目录"反复套娃"。现状一条完整路径:

```
packages/myia-classifier/src/myia_classifier/data/keywords.json
└─①packages  └─②myia-classifier  └─③src  └─④myia_classifier  └─data
```

**同一个名字以三种写法出现在一条路径里**(packages / myia-classifier / myia_classifier×2),任何代码/数据文件都要钻四层目录。虽然这是 Python workspace + src-layout 的"标准"形态,但对本仓库(单成员 workspace、个人项目)属于过度工程,观感与维护成本都差。

### 现状事实(2026-10-02 核查)

- 目录树:`packages/myia-classifier/{pyproject.toml, README.md, uv.lock?}` + `packages/myia-classifier/src/myia_classifier/`(4 个 .py)+ `.../myia_classifier/data/keywords.json`
- 根 pyproject:`[tool.uv.workspace] members = ["packages/myia-classifier"]`,dependencies 含 `myia-classifier>=0.1,<0.2`
- `src/myia/classify/` 保留 re-export shim,既有测试经它跑

## 方案(design 阶段定夺,推荐 A)

| 方案 | 结果路径 | 砍掉层数 | 代价 |
|---|---|---|---|
| **A(推荐)去 packages/ 包装 + 去 src/** | `myia-classifier/myia_classifier/data/keywords.json` | 2 层 | 改根 pyproject members、重生成 uv.lock、Dockerfile COPY、pypi-publish.yml 路径 |
| B 仅去 packages/ 包装 | `myia-classifier/src/myia_classifier/…` | 1 层 | 同上但保留 src-layout |
| C 仅去 src/** | `packages/myia-classifier/myia_classifier/…` | 1 层 | 最小改动 |

## Requirements

- 按定夺方案扁平化目录;`git mv` 保留历史
- **不得破坏以下任一项**(改完逐项验证):
  1. 根 pyproject workspace members 与 myia-classifier 依赖声明;`uv lock` + `uv sync` 干净重生成
  2. `uv build` 根包与 classifier 包双 wheel/sdist,数据文件断言测试(tests/test_classifier_package.py 等)全绿
  3. `src/myia/classify` shim 导入不变;全量 pytest 通过
  4. Dockerfile(`COPY packages ./packages` 行)与 `docker build` + 容器 `myia --version` 实跑
  5. `.github/workflows/pypi-publish.yml` 双包构建路径;CI(uv sync)绿
  6. 文档同步:根/包 README、docs/launch/RELEASE.md、CONTRIBUTING 中出现的路径(grep `packages/myia-classifier` 清零或更新)
  7. 安全扫描不引入新告警;不动 .gitignore 语义(如方案去掉 packages/,同步清理相关 ignore 规则)

## Acceptance Criteria

- [ ] `find` 确认目标形态落地,路径中名字重复 ≤1 次(myia-classifier/myia_classifier 视为可接受的一对)
- [ ] 上述 7 项约束逐项验证通过并记录命令与输出
- [ ] 全量 pytest 绿;`git grep "packages/myia-classifier"` 零残留(或仅历史文档说明)
- [ ] 提交信息说明层级行数变化(4→2)

## 执行记录(2026-10-02,主人指示后当日内完成)

- 方案 A 落地:packages/myia-classifier/src/myia_classifier → **myia-classifier/myia_classifier**(4 层→2 层,git mv 历史保留)
- 8 处引用同步:根 pyproject members、包 pyproject(packages/artifacts)、.gitignore 数据例外、Dockerfile COPY、pypi-publish.yml 构建路径×2、README×2、CONTRIBUTING;tests/test_classifier_package.py 构建路径修正
- 验证:uv lock/sync 重生成 ✓;全量 pytest 1217×2 绿 ✓;双包 wheel/sdist 构建 + classifier wheel 含 keywords.json ✓;docker build + 容器 myia --version ✓;git grep packages/myia-classifier 零残留(仅历史档案)✓

## Notes

- 背景:该结构由 v10-classifier-pypi 任务按"uv workspace + src-layout"惯例生成,功能正确但层级冗余;主人 2026-10-02 指出后建档,修复排 v1.1(或主人指示时立即做)
- 若未来出现第二个 workspace 成员(如 myia-skill),方案 A 的顶层平铺仍成立(每成员一个顶层目录),不因成员增多而劣化
