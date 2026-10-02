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

- [ ] `uv run pytest -q` 全绿(1395+3 通过,0 失败)
- [ ] 单独跑该文件亦绿(防 rootdir 依赖)
- [ ] 单独小 commit,信息引用本任务
