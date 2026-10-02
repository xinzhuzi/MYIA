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
