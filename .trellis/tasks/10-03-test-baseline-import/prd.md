# 修复 test_baseline 预存失败(ModuleNotFoundError: tests)

## Goal

`tests/test_baseline.py::test_pipeline_run_feeds_trend_context_into_rendered_card`
在干净 main 上即失败(`ModuleNotFoundError: No module named 'tests'`,test_baseline.py:789),
是 v1.1 终检连环抓出后遗留的唯一红测。CI 红线(pytest 全绿才可并)要求归零。

## Requirements

- 根因(普查 D3 已定位):`tests/test_baseline.py:789` 的
  `from tests.conftest import ...` 依赖 cwd 进 sys.path —— 裸 `pytest`
  (rootdir 不上 path)必假红,只有 `python -m pytest` 可跑;CI 用的是后者所以绿。
- 修法取最小侵入,候选按实测定:改局部导入(不经 `tests.` 包路径)/ conftest
  显式注入 rootdir 进 sys.path / 补 `tests/__init__.py`(注意勿触发包扫描副作用,
  参见 ee2d9db 的 __pycache__ 教训)。
- 不得顺带重构:只修导入路径,断言与夹具不动。
- 修后统一验证口径:`uv run python -m pytest -q` 与裸 `uv run pytest -q` 都必须绿。

## Acceptance Criteria

- [x] `uv run pytest -q` 全绿(1395+3 通过,0 失败)
  → 复验 2026-10-03:本任务缺口(ModuleNotFoundError: tests)已归零;当日全量唯一红为
  并行会话 WIP(`tests/test_desktop_sidecar_protocol.py::test_sources_write_backup_stops_comment_loss`,
  裸/`python -m` 双跑法均败、与导入问题无关、文件在并行禁区禁碰),字面「全绿」待并行收口后由门禁复验。
- [x] 单独跑该文件亦绿(防 rootdir 依赖)
- [x] 单独小 commit,信息引用本任务

## 验证记录(2026-10-03,基线导入复核)

- 缺口已修:`tests/test_baseline.py:789` 现为 `from conftest import FakeClock`
  (commit `718d56c` "fix(tests): bare pytest fails on tests.conftest import — use prepend-mode path",
  HEAD 祖先链上,diff 仅此一行,commit message 引用本任务);`grep -rn "from tests" tests/ --include='*.py'`
  除 conftest.py:4 防回归注记外零命中。
- `uv run --no-sync python -m pytest tests/test_baseline.py -q` → `58 passed in 0.24s`。
- `uv run --no-sync pytest tests/test_baseline.py -q`(裸跑,防 rootdir 依赖)→ `58 passed in 0.15s`。
- `uv run --no-sync python -m pytest --collect-only -q` → `1718 tests collected`,0 收集错误。
- `uv run --no-sync pytest -q` → `1 failed, 1703 passed, 14 skipped`(唯一红即上述并行 WIP)。
- `uv run --no-sync python -m pytest -q` → `1 failed, 1703 passed, 14 skipped`(同一红)。
- 2026-10-03 收尾复验:批次工作流最终全量 pytest(CI 同款 `uv run --no-sync python -m pytest -q`,收尾员复跑)= 1759 passed / 14 skipped / 0 failed——先前唯一红(并行 WIP test_sources_write_backup_stops_comment_loss)已随并行线收口清零;任务维持 review,详细结论见工作流报告《MYIA 批次报告:写回保注释+基线+代验收》。
