# v0.2 可用:存储加固 + CLI 完整 + crawl4ai + LLM 精评 + 安全底座

## Goal

从「能跑」到「可用」:AI-NATIVE 的双层漏斗真正落地(LLM 精评+成本护栏),桌面用户的密钥安全底座建成,crawl4ai 成为 L3 默认引擎,服务端与桌面形态各迈一步。

## 前置

v0.1 全部子任务完成(v01-* 十项)。

## 子任务地图

| # | 子任务 | 边界 |
|---|---|---|
| 1 | v02-storage-hardening | retention / VACUUM / 断点续跑 |
| 2 | v02-cli-full | init / test / list / doctor(AI 可消费输出) |
| 3 | v02-engine-crawl4ai | L3 默认引擎(可选依赖) |
| 4 | v02-enrich-llm | 三维精评 / 批量 / 缓存 / token 预算护栏 / watchlist |
| 5 | v02-secrets-keychain | Keychain / DPAPI 凭据三态 |
| 6 | v02-push-telegram | TG + webhook 通道(grill Q5) |
| 7 | v02-docker-compose | 服务端形态 |
| 8 | v02-desktop-spike | Tauri+PyInstaller sidecar 原型(产出=决策) |
| 9 | v02-proxy-transport | fetch_base 代理 transport:单上游 HTTP/SOCKS5(grill Q4) |

## 实现完成记录(2026-10-02,九个子任务已置 review)

- 九个子任务全部实现;深度评审 7 区域 41 条发现全部独立复核确认,29 条 high/medium 已修复并回归(688 passed / 12 skipped ×3 稳定)
- 桌面 spike 结论:**继续 Tauri**(.app 25MiB<30MB 达标 / 壳冷启动 144-242ms / sidecar 往返 307ms / Rust 胶水 72 行),报告在 v02-desktop-spike/research/spike-report.md;品牌名/图标已按主人要求改为 MYIA + 眼睛雷达标(desktop-spike/branding/)
- 修复工程师修掉的关键 high(节选):.gitignore `data/`+`secrets.*` 误伤源码与数据文件(wheel 缺文件/fresh clone 必炸,含 desktop-spike 构建目录 ~970MB 防误提交)、run_maintenance 误删其它品类 baseline/hints、crawl4ai 无视代理配置(真实 IP 直连)、enrich content 断链与 CLI 不可达、mute 语义反转、telegram/webhook 管线壳分支、doctor --json 双文档、代理池未接 run 主链路
- 工作流后追加修复(主会话亲修):**WAL 并发首开 flaky**——`PRAGMA journal_mode=WAL` 换模式需独占锁且 busy handler 不重试,类级 `_OPEN_LOCK` 串行化 configure 阶段(sqlite.py);修复前隔离 5 挂 2,修复后 15/15 全过
- 未修 backlog:12 条 low(测试缺口/文档措辞);待主人手动验证:真实 TG/飞书推送、真实 GLM key、真实代理往返、compose 长跑定时推送、Windows DPAPI、crawl4ai 真实抓取(opt-in smoke)

## 阶段验收标准

- [ ] `myia init/test/list/doctor` 全部可用,doctor 输出结构化诊断供 agent 自修
- [ ] enrich 生效:score 回填后 push.route 真正三级分层;预算超限自动降级纯粗筛且有提示
- [ ] macOS 实机密钥入 Keychain 往返可用;YAML 全凭据位明文检查通过
- [ ] JS 渲染源经 crawl4ai 跑通;auto 链顺序 L1→L2→crawl4ai→firecrawl
- [ ] `docker compose up` 后按 schedule 自动跑并推送
- [ ] 源级 `proxy:` 指向单上游代理时采集走代理,代理失败与源失败分类正确(grill Q4)
- [ ] Tauri sidecar spike 有书面结论(继续 Tauri / 切 Flet)

## Open Questions

无(2026-10-01 grill 第一轮全部关闭):桌面 spike 保持 v0.2;enrich 缺省 glm-4-flash + base_url 显式 env:。决议见 v0.1 父任务 `research/grill-round1-decisions.md`。
