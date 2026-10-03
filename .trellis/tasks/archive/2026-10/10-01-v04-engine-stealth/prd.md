# 引擎 stealth_browser:L5 反检测浏览器

## Goal

invisible_playwright_mcp(feder- 版,MIT)接入:真浏览器过反爬+基础验证码,cookie 注入过登录墙。

## Requirements

- `stealth_browser.py`:**内置 MCP 客户端**调用 invisible_playwright_mcp(规划 v1.7 定案,进程内 stdio/HTTP 客户端,不依赖外部 agent)
- cookie 注入登录墙:凭据经 `keychain:`(账号-站点-cookie 三级管理 UI 是桌面端后续,先 CLI/keychain)
- 验证码边界:hCaptcha/Turnstile 基础盾尝试;手机验证码/真人审 → 结构化错误(不做绕过)
- 引擎级成本护栏:浏览器实例复用、单 run 页面预算;失败原因结构化(风控类型分类)

## Acceptance Criteria

- [ ] linux.do 未登录可读页跑通(手动验证)
- [ ] cookie 注入往返单测(mock MCP server)
- [ ] auto 链六层顺序验证

## Notes

- 填充 `src/myia/engines/stealth_browser.py` 壳(17 行);MCP 客户端实现保持轻(标准 MCP 协议子集)

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC2/AC3 有硬证据:跑 `uv run --no-sync python -m pytest tests/test_stealth.py -q` → 34 passed。AC2=test_cookie_injection_round_trip_from_keychain(keychain: → browser_set_cookies 参数,open→cookies→navigate 顺序,同 host 只注一次)+ cookie_unsupported 结构化分支(mock MCP server);AC3=test_auto_chain_stealth_layer(断言七层链序 stealth=L5、llm_browser=L6)+ test_auto_chain_first_five_fail_degrade_to_stealth(假引擎注入集成)。成本护栏在 src/myia/engines/stealth_browser.py(实例复用 browser_status 探测、page_budget_exhausted)。

遗留(需主人手动完成):
- AC1「linux.do 未登录可读页跑通(手动验证)」仓库内无记录:test_stealth.py 无真实源 smoke,tests/test_fetch_base.py:482 的 linux.do skip-marked smoke 属 fetch_base 引擎且默认跳过,workspace/任务目录亦无手动验证记录 —— 需主人真实访问 linux.do 验证并留记录
