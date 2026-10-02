# 引擎 Scrapling:L4 自适应反爬

## Goal

Scrapling(84k+ BSD)接入:元素定位自愈(改版不死)、隐身指纹过 CF 基础盾、无限滚动,补全降级链第四层。

## Requirements

- `scrapling.py`:对接 Scrapling;`pagination.mode: scroll` 在本引擎实装(L1-L3 不支持)
- 可选依赖 `myia[scrapling]`;未安装结构化报错
- 进 registry 链:L1→L2→crawl4ai→firecrawl→scrapling(L4 之后是 v0.4 的 L5/L6)
- 适用场景注释:基础盾/改版频繁源;企业级风控明确标注不支持(→L5/L6)

## Acceptance Criteria

- [ ] nodeseek 一类基础盾源真实跑通(手动验证记录)
- [ ] auto 链五层顺序单测(注入假引擎)
- [ ] scroll 翻页单测

## Notes

- 填充 `src/myia/engines/scrapling.py` 壳(25 行)
