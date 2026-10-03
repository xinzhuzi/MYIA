# 黄金回归治本:活 fixture 换冻结快照副本(防并行会话误伤)

## 需求源

主人 2026-10-03 终验问「确定没有问题?」时的已知问题清单落档:黄金回归 2026-10-03 一天内被并行会话误伤两次(games 的 `url_template`、vision 的 `image: img@src`/games.yaml push 扩列),两次都是「别的会话合法演进活体 fixture」撞上「我方回归测试读活库」的结构性竞态,不是谁的 bug。治本一次,永绝后患。

## 现状与根因

- `tests/test_push_schema_targets.py::TestGoldenRegression` 对 `plugins/*.yaml`(七份)**活体文件**做 `load_category_file → model_dump` 与黄金快照比对(当前已收窄到 push 子树 + 纯增空键容忍,commit 2e7e252)。
- 活库是全仓库各任务的合法配置面:任何会话给自己的品类 yaml 加 push 条目/字段都会改变 model_dump → 误伤我方回归。多会话仓库此竞态必然复发。

## Requirements

1. **冻结副本**:黄金生成期(基线脚本/一次性命令)把七份 fixture 复制为 `tests/fixtures/push_targets_golden/<原名>.yaml` 冻结副本,只读、不随活库变。
2. **测试改读冻结副本**:TestGoldenRegression 与黄金 JSON 同源于冻结副本;活体 plugins/ 不再参与该测试。
3. **承诺语义不变**:仍只断言「我方 schema 改动(push/targets)不改变旧 YAML 加载语义」——push 子树 + 纯增空键容忍的现行断言形态保持。
4. **升级路径注明**:若某品类 fixture 冻结版与活版漂移过大需刷新基线,须显式重新生成(命令留档在测试 docstring),不允许静默跟随活库。

## Acceptance Criteria

- [ ] 测试改读冻结副本后全绿;黄金 JSON 与副本同源可复现(生成命令留档)。
- [ ] 反证:临时改活体 plugins/ 某 yaml 的 push 配置,该测试**不再**受影响(单测或手工反证记录)。
- [ ] 全量 pytest 本域零失败(文件白名单判定)。

## 非目标

- 不改 schema、不改 push 层任何行为——纯测试基建加固。
- 不替别的会话修他们的 fixture 演进(那是他们的合法工作)。
