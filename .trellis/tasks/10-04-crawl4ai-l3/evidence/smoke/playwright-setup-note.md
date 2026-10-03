# 首跑环境注记:playwright 浏览器二进制缺失(非网络失败)

真跑序:real-gate 首跑发生在 2026-10-04 01:4x,当时引擎结构化失败(非网络/反爬)——
playwright 1.63 需 `chromium_headless_shell-1243`,本机缓存只有 1200/1208/1223。
**注意:该输出当时经管道 tee 写入 pytest-real-gate.log,补装后复跑同路径覆盖了它;
本文为执行者当场捕获的错误摘录重建(非原始字节),补装后复跑日志才是原始文件。**

首跑命令:`MYIA_SMOKE_REAL=1 uv run --no-sync python -m pytest tests/test_crawl4ai.py -k real -q -s`
首跑结果:`1 failed, 1 skipped, 39 deselected in 0.68s`(exit 1)

失败摘录(引擎结构化包装,含 crawl4ai-setup 提示——顺带实证 low-A 路径):

```
shishi.engines.fetch_base.FetchError: crawl4ai 浏览器启动/运行失败: BrowserType.launch:
Executable doesn't exist at /Users/zhengbingjin/Library/Caches/ms-playwright/
chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell
...(浏览器二进制缺失?请执行 crawl4ai-setup 安装 playwright 浏览器;详见 doctor 诊断)
src/shishi/engines/crawl4ai.py:297: FetchError
```

修复:`uv run --no-sync playwright install chromium`(exit 0,17:17 下载 94.3 MiB,
装得 Chrome Headless Shell 153.0.8010.12 v1243)→ 复跑 1 passed(pytest-real-gate.log)。

定性:环境缺件,非目标站网络失败;cocoloop 备选口径(任务第 3 条)不因此触发。
