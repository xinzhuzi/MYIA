# v1.1 engines:引擎修正(2 条 low)

## Goal

清偿 low backlog(engines 模块,素材编号 9-10,来源 `.trellis/tasks/10-01-myia-v10-launch/research/v11-low-backlog.md`),修正 llm_browser 引擎的 docstring 矛盾与日志泄露。

## Acceptance Criteria

- [ ] **9.** where: `src/myia/engines/llm_browser.py:8` — what: docstring「no extra is added」与 pyproject 中 skyvern extra 矛盾(实为服务端安装便利 extra);「Same zero-heavy-dependency approach as firecrawl」亦不成立(firecrawl 有 extra)。修法:docstring 如实描述 skyvern/firecrawl 的 extra 定位。
- [ ] **10.** where: `src/myia/engines/llm_browser.py:478` — what: 解析后的 endpoint 值直接落 INFO 日志,与模块自述「Resolved values never reach logs」矛盾。修法:对齐 `src/myia/engines/fetch_base.py` 的 mask 先例,日志记引用名或掩码,不记明文。

## Notes

- 条目 10 是安全基线问题(security-baseline:敏感值不落日志),优先级高于同任务其他条目。
- 改动后核对现有日志相关测试;回归:`uv run --no-sync python -m pytest` 全量绿。

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: 条目 9:src/myia/engines/llm_browser.py:9-13 docstring 如实改「skyvern/firecrawl extras 不是引擎运行时依赖,而是服务端安装便利」,旧「no extra is added」「zero-heavy-dependency approach」措辞已无;条目 10:日志三处掩码——:501-511 就绪 INFO 记 endpoint_display(凭据引用名/内置缺省)且 api_key 只报已配置与否、:565-566 重试 WARNING 走 mask_endpoint_url(fetch_base.py:433 先例)、:541-551 HTTP 失败只记展示形态不透传 httpx 明文 URL;tests/test_llm_browser.py 我实跑 47 passed。
