# 引擎 crawl4ai:L3 默认引擎

## Goal

crawl4ai(84k+ Apache)成为 L3 默认引擎:JS 渲染页 → 干净 LLM 文本 / 自动结构化提取;纯 pip 依赖,传播门槛最低。

## Requirements

- `crawl4ai.py`:对接 crawl4ai;`extract` 缺失时自动结构化兜底(规划 v1.7 定案)
- 进 registry 降级链:`engine: auto` 顺序 L1→L2→crawl4ai→firecrawl(crawl4ai 纯 pip 优先,firecrawl 需外部服务兜底;与 v01-engine-l1-l2 的三成链衔接)
- **可选依赖**:`pip install myia[crawl4ai]`;未安装且被引用时报结构化错误并提示安装命令(不拖累核心零依赖)
- crawl4ai 自身的浏览器二进制管理(playwright install)在 doctor 诊断中提示

## Acceptance Criteria

- [ ] JS 渲染源真实跑通:TapNow `https://app.tapnow.ai/`(主人 2026-10-01 确认的 L2/L3 实例)
- [ ] auto 链顺序单测(L2 失败→crawl4ai 成功,不再落 firecrawl)
- [ ] 未安装依赖时错误信息含 `pip install myia[crawl4ai]`

## Notes

- 填充 `src/myia/engines/crawl4ai.py` 壳

> **2026-10-03 grill Q4 注记**:本任务 v0.2 范围(接入位/薄层)已毕;L3 真引擎实装
> 是 v1.2 议题 → `10-03-v12-backlog`。本档不重开。

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: uv run --no-sync python -m pytest tests/test_crawl4ai.py tests/test_registry.py -q → 39 passed, 1 skipped;auto 链顺序=test_auto_chain_l2_failure_degrades_to_crawl4ai_not_firecrawl(断言 outcome.engine=='crawl4ai' 且 firecrawl 工厂零调用),链序见 registry.py:65-71;未安装错误含安装命令=src/myia/engines/crawl4ai.py:57 INSTALL_COMMAND='pip install myia[crawl4ai]' + test_load_crawl4ai_absent_raises_structured_dependency_error;extra 已入 pyproject.toml:28。唯一 skipped 即 AC1 的 TapNow 真实冒烟。

遗留(需主人手动完成):
- AC1『TapNow JS 渲染真实跑通』需主人手动:tests/test_crawl4ai.py:493 test_smoke_tapnow_js_render_auto_structures 默认跳过,需本地安装 myia[crawl4ai] 并设 MYIA_CRAWL4AI_SMOKE=1 实跑(涉真实网络);任务目录无手动验证记录。另注:2026-10-03 grill Q4 注记已将 L3 真引擎实装划入 10-03-v12-backlog,本档不重开。
