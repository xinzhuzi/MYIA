# fetch_base 公共底座:限速 / 退避 / 变更指纹 / 凭据引用

## Goal

六层引擎共享的异步请求底座,把「礼貌爬取」和「变更检测」收敛到一处,引擎只写差异逻辑。

## Requirements

- `rate_limit`:qps / jitter(随机延迟)/ backoff(exponential);429/5xx 指数退避 + 重试(源级 `retry` 次数)
- 同域名多源自动合并限速:进程内共享 per-host limiter 注册表,取最严 qps
- `respect_robots: true` 默认:robots.txt 拉取+缓存+解析,违规源跳过并记录
- 变更指纹(增量抓取的另一半,"测活"管死活,"基线"管变没变):
  - 优先 ETag / Last-Modified 协商缓存;无则正文哈希(规范化后 hash,避免模板噪声误判)
  - 对外接口:`check(url, response) -> changed | unchanged`,基线读写由 store 层提供(v01-store-dedup 的基线表)
- `headers` / `method` + `post_body` 透传;凭据引用解析:`env:VAR` 展开,`keychain:` 明确报 NotSupported(v0.2 实现)
- `proxy:` 节 v0.1 **仅解析校验、仅 `direct` 实际生效**(grill Q4 定案):`pool:*`/`residential:*` 引用报结构化「未实装」错误并注明排期(v0.2 单上游 transport / v0.3 池轮换)——不留半实现
- 全部异步(httpx.AsyncClient),超时可配

## Acceptance Criteria

- [ ] 单测:同域限速合并(两个源共享一个 limiter)、429 退避节奏、ETag 命中返回 unchanged、正文哈希兜底、env: 展开、robots 违规跳过、proxy 节解析(direct 生效;pool/residential 未实装报错)
- [ ] 真实源 smoke:对 linux.do 一类站点按 qps=0.5 抓 3 页无封禁(手动/可选测试)
- [ ] 不依赖任何重库(httpx + 标准库;robots 解析用 stdlib urllib.robotparser)

## Notes

- 填充现有 `src/myia/engines/fetch_base.py`(57 行壳)
- 编码探测(gb18030 等)放本层还是 static_html 引擎,design.md 定

## 验收记录(2026-10-03,受主人委托代验)

verdict: accepted

evidence: pytest tests/test_fetch_base.py -q → 37 passed, 1 skipped(唯一 skip 是 PRD 标注「手动/可选」的真实源 smoke,test_fetch_base.py:483);同域限速合并(:50)、429 退避节奏(:121)、ETag unchanged(:200)、正文哈希兜底(:217)、env: 展开(:273)、robots 违规跳过(:343)、proxy direct 生效/pool·residential 结构化未实装报错(:301/:311/:317)全过;依赖仅 httpx+stdlib urllib.robotparser+selectolax(fetch_base.py:33-67),无重库。
