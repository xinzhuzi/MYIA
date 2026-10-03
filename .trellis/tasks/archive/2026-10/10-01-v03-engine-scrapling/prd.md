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

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC2/AC3 有硬证据:跑 `uv run --no-sync python -m pytest tests/test_scrapling.py tests/test_registry.py -q` → 48 passed, 1 skipped;五层链序 tests/test_scrapling.py:645(注入假引擎)+ AUTO_CHAIN 断言(test_scrapling.py:676、test_registry.py:88);scroll 翻页 tests/test_scrapling.py:551-575(page_action 注入、轮数上限=max_pages、与 static 互斥)。registry.py:67 链序 direct_api→static_html→crawl4ai→firecrawl→scrapling→stealth_browser→llm_browser。

遗留(需主人手动完成):
- AC1「nodeseek 一类基础盾源真实跑通(手动验证记录)」仓库内无验证记录:tests/test_scrapling.py:788 的 test_smoke_nodeseek_basic_shield_auto_structures 为 opt-in(MYIA_SMOKE_REAL=1 且本地装 myia[scrapling] 才跑),本次运行被 skip,且 `uv run --no-sync python -c "import scrapling"` → ModuleNotFoundError(本机未装可选依赖)—— 需主人装 myia[scrapling] 后跑该 smoke(或手动验证 nodeseek)并留记录
