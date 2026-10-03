# 分类引擎:七大类 + 免费/付费双信号

## Goal

第一层漏斗(零 token 粗筛):七大类关键词分类 + 免费/付费双信号裁决,把生产系统 wf_crawl.py 的分类引擎沉淀为 `classify/builtin.py`。这是 MYIA 的原创护城河。

## Requirements

- 七大类:信用卡 / 节点 / 代买 / 服务器 / token / AI资讯 / 羊毛;每类关键词表 + 优先级
- 免费/付费双信号裁决:同一标题同时命中免费与付费信号时的裁决规则(沉淀自生产系统两次纠错经验;规则细节 design.md 与主人确认)
- 自定义规则:`classify.rules[]` 的 `when` 表达式(如 `abs(change_pct) >= 3`)+ `tag`;安全求值(白名单 AST,不 eval 任意代码)
- 输出:category(大类)+ tags[],写入 items 表,供推送模板与后续 enrich(v0.2)使用
- 命中明细可追溯:哪条关键词命中(日志/debug 输出,方便主人调规则)

## Acceptance Criteria

- [ ] 金测集:用本地迁移包 `workflows/wf-crawl/urls-*.txt` 的真实标题(仅本地)做成测试夹具摘要(脱敏、只留标题),分类结果与生产语义抽查一致(准确率目标 design.md 定,初版 ≥ 90%)
- [ ] 双信号冲突用例有单测(免费 vs 付费撞车)
- [ ] 关键词表数据与代码分离(数据文件可被 AI/用户增改,不用改代码)
- [ ] 零网络、零 token:纯本地规则

## Notes

- 填充 `src/myia/classify/builtin.py`(41 行壳)与 `custom.py`(35 行壳)
- **私有语料只做夹具摘要,不进仓库**;未来 myia-classifier 独立 pip 包(v10-classifier-pypi)从本模块拆出

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: pytest tests/test_classify.py -q → 67 passed;金测夹具 tests/fixtures/classify_gold.json(脱敏标题+期望分类)存在,40-60 条规模与 ≥90% 准确率断言(:42/:47)过;双信号冲突用例(:70-101)过;关键词数据与代码分离(test_classify_uses_new_keyword_from_data_file_without_code_change:249,数据在 myia-classifier 包 data/keywords.json,src/myia/classify 为兼容 shim);零网络零 token。跨任务发现(不属本任务):pytest tests/test_classifier_package.py 1 failed——未提交的 myia-classifier/pyproject.toml 版本抬 0.1.0→1.1.1 但 myia_classifier/__init__.py:67 __version__ 仍 0.1.0,属 10-03 v1.1.1 双包对齐工作的一行修复项。
