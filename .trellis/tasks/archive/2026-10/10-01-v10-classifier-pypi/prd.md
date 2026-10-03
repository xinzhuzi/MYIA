# 独立包:myia-classifier

## Goal

七大类分类引擎独立 pip 包(规划第九节):可被单独引用,自身也是传播入口。

## Requirements

- 从 `src/myia/classify/` 拆出为独立发行(单仓库内 namespace 或独立仓库,design 定)
- PyPI 发布:零重依赖、双信号规则数据内置、自定义规则 API
- MYIA 核心改为依赖 myia-classifier(固定版本范围)

## Acceptance Criteria

- [ ] `pip install myia-classifier` 独立可用(README + 最小示例)
- [ ] MYIA 全量测试在新依赖关系下全绿
- [ ] PyPI 页面完整(描述/分类器/许可)

## Notes

- 拆分时机注意:反馈闭环(v0.3)若改了词表结构,拆分以最终形态为准

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: 原失败检查实跑 uv run --no-sync python -m pytest tests/test_classifier_package.py -q → 6 passed(上轮 1 failed, 5 passed);myia-classifier/myia_classifier/__init__.py:67 现为 __version__ = "1.1.1",与 myia-classifier/pyproject.toml:7 及主包 src/myia/__init__.py:7 一致,tests/test_classifier_package.py:36 钉死值已同步为 "1.1.1"。

遗留(需主人手动完成):
- PyPI 实际发布与页面核验仍未发生:root pyproject.toml:49-51 注释明示 'myia-classifier 不发布到 PyPI 前先按 workspace 成员解析'——需主人凭据走 docs/launch/RELEASE.md runbook(真实凭据类,需主人)
- 发布后回改 root pyproject 源为注册表并做 pip install myia 终验(需主人,依赖上一项完成)
