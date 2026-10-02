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
