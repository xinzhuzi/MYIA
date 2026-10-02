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
