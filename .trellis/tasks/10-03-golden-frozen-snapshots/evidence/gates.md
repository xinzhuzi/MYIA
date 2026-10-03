# 门禁与反证实录(10-03-golden-frozen-snapshots,2026-10-03)

## 门禁(退出码原样)

### 定向面

```
$ uv run pytest tests/test_push.py tests/test_push_channels.py -q
97 passed in 0.12s
exit=0
```

### 黄金回归本体(改读冻结副本后)

```
$ uv run --no-sync pytest tests/test_push_schema_targets.py -q
34 passed in 0.06s        # 33 既有 + 1 新增 test_frozen_copies_match_golden_manifest
exit=0
```

### 全量

```
$ uv run pytest -q
2563 passed, 19 skipped in 58.82s
exit=0
```

基线对照:dc1cf86 全树 2562 绿 → 现 2563 = +1(本任务新增完整性测试),
零失败、零新红(含并行会话在途未跟踪测试文件 test_push_images /
test_vision_models_server / test_trellis_finish_guard,均绿)。

### 静态

```
$ uv run ruff check tests/regen_push_targets_golden.py tests/test_push_schema_targets.py
All checks passed!
exit=0
```

ruff format --check 对 test_push_schema_targets.py 报 1 处 would-reformat,
系 HEAD 既有(line 117 一行式 route 字面量),非本任务引入;不整文件重排
(避免搅动并行域行),diff 保持最小。

## 反证一:活体 plugins push 配置变异 → 测试不受影响(PRD AC2)

变异内容:plugins/games.yaml telegram 条目临时插入
`template: "PARALLEL-SESSION-EVOLVED {{ date }}"`(模拟并行会话合法演进
自己品类的 push 配置——正是 10-03 当天两起误伤的形态):

```
$ uv run --no-sync pytest tests/test_push_schema_targets.py::TestGoldenRegression -q
2 passed in 0.04s
live-mutation-test-exit=0        # 冻结前此变异必红:活体 model_dump 漂移
(随后 git checkout -- plugins/games.yaml 还原,工作树净)
```

## 反证二:冻结副本取值漂移 → 测试变红(证明确实读的是冻结副本)

变异内容:冻结副本 tests/fixtures/push_targets_golden/plugins/games.yaml
的 `target: env:TELEGRAM_CHAT_ID` → `env:TELEGRAM_CHAT_ID_MUTATED`
(改既有值,非重复键,走黄金比对路径而非 YAML 重复键拒绝):

```
$ uv run --no-sync pytest ... ::TestGoldenRegression -q
AssertionError: plugins/games.yaml.push[1].target:
取值漂移 'env:TELEGRAM_CHAT_ID' -> 'env:TELEGRAM_CHAT_ID_MUTATED'
frozen-value-drift pytest exit=1
(备份还原后复跑:2 passed,exit=0)
```

注:首轮反证二曾用"插入重复 template 键"变异,触发的是 YAML 重复键
LoadError(同样证读冻结副本,但未走黄金比对分支);已补上述取值漂移版,
比对路径与守门路径双双证实。
