# PRD:启停写回保注释——sources.write 文本手术(根治)

## 背景

- 缺口:源管理启停写回(`sources.write`,desktop/entry.py:585-708)用 `yaml.safe_dump` 重写整份品类 YAML(entry.py:687)——用户点一次启停开关,该文件**全部注释被抹掉**。官方品类 YAML 大半是注释(monitor.yaml 63 行约 2/3 注释),注释即文档,丢失不可逆(止血前)。
- 来源:10-03-yaml-editor 探查时发现,grill Round 1 决议 2 定处置:**止血并入 yaml-editor**(写回前存 `.bak`),**根治拆本任务**。
- 本任务独立于 yaml-editor,可并行或其后;不动编辑器链路。

## Requirements

1. `sources.write` 的写回改为**文本手术**:解析原文 → 在 YAML 文本上按行搬运 `sources:` 节点(移出/移入源条目),不整份 dump 重写
2. 契约零变更:请求/应答/错误码/往返一致语义(sources/api.ts 模块头契约)原样;`.disabled.json` 暂存机制原样
3. 注释/顺序/引号风格逐字节保真:被搬运的源条目自身及其上方注释随行移动,其余行一律不动
4. 零新依赖(不引 ruamel;核心 6 依赖封顶红线)
5. 保底:文本手术解析失败(非常规缩进/锚点/流式风格等边角)→ 结构化报错拒写,**不静默回退到 safe_dump 抹注释**

## Acceptance Criteria

1. 对 6 份官方品类 YAML 各做一轮停用+启用:往返后文件与操作前**逐字节一致**(diff 为空);启停期间应答名单与现状契约一致
2. 重注释文件(含源条目上方注释、行内注释、节间注释)搬运后注释全在且归属正确
3. 手术不支持的结构(如 YAML 锚点引用源条目)→ 结构化错误 + 零写入,主文件不动
4. 既有 pytest 基线全绿(sources.write 现有用例零改动通过)
5. 零新依赖:pyproject 依赖表不变

## 边界(刻意不做)

- 不改启停语义/暂存格式;不做编辑器功能(那是 yaml-editor);流式风格(`{a: 1}` 单行源)支持与否以实现实测定,不支持即走 AC3 拒写

## 执行记录

- 2026-10-03:文本手术落地(desktop/entry.py `_sources_surgical_rewrite`,新测试 tests/test_sources_write_surgery.py 24 条),经批次工作流「实现→全量门禁→写回保真专项评审→确认项修复→回归」收口;最终全量 pytest(CI 同款 `uv run --no-sync python -m pytest -q`,收尾员复跑)= 1759 passed / 14 skipped / 0 failed,任务转 review;详细结论见工作流报告《MYIA 批次报告:写回保注释+基线+代验收》。


## 评审遗留(2026-10-03 批次专项评审,4 条确认;high/medium 已修回归,以下为留痕)

- [medium·已修+留痕] AC4「既有用例零改动」与 AC1 字面矛盾:旧断言钉死 safe_dump 抹注释行为(正是本任务根治对象),实现必然使其失败——断言改写方向正确且更强,此处记录豁免;后续修订 PRD 模板时避免自相矛盾条款。
- [low·待修] 暂存保留键泄漏:_myia_toggle.raw_block 缺失/非字符串时兜底 safe_dump 把内部键写进主 YAML,该源此后停用被拒(触发需手编损坏暂存,现网零路径)——排 v1.1.2 批次顺手修(兜底前剥离 _myia_toggle 再 dump)。
- [low·待修] 列 0 节间注释被末条目吸收:紧贴末条目(无空行)的列 0 分节注释随停用整体离开主文件,启用还原;中间态一节注释不可见(16 份现网 YAML 零命中)——排 v1.1.2(块尾判定对列 0 注释设界)。
- 回归测试:无主注释块夹缝场景已入 tests/test_sources_write_surgery.py(36 测);两 low 的触发布局用例随修随补。
