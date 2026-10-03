# v0.1 骨架:核心流水线 + 引擎 + schema + 分类 + 推送

## Goal

跑通 MYIA 端到端最小闭环:一个 YAML 品类插件 → 采集(L1 API 直连 / L2 静态页 / firecrawl 重渲染)→ 七大类分类 → 去重/变更指纹 → 飞书阈值分级推送。完成后 `myia run plugins/stocks.yaml` 单命令可用。

## 背景与权威文档

- 完整规划:`LOCAL-NOTES.md 索引的规划文档(本地)`(v1.7,**仅本地参考,勿提交进仓库**)
- 第一原则 AI-NATIVE:工具的第一使用者是 AI;schema 完备度决定「AI 写 YAML」卖点成立与否
- 产品形态:桌面客户端为最终主形态,但 v0.1 只做 Python 核心 + CLI;桌面壳 spike 排在 v0.2
- 仓库现状:`src/myia/` 各模块为薄壳(共 ~650 行),本阶段是**填充**而非新建
- 生产参照:本地迁移包 `workflows/wf-crawl/`(分类语料、真实源 URL),**仅本地,勿把私有语料提交进仓库**

## 子任务地图(建议顺序即依赖顺序)

| # | 子任务 | 边界 | 依赖 |
|---|---|---|---|
| 1 | v01-yaml-schema | 12 节 schema 模型+校验,明文凭据拒跑 | 无(地基) |
| 2 | v01-fetch-base | 限速/退避/robots/变更指纹/headers 凭据引用 | 1 |
| 3 | v01-engine-l1-l2 | direct_api + static_html + registry 自动降级 | 2 |
| 4 | v01-engine-firecrawl | firecrawl 后端进降级链 | 2 |
| 5 | v01-store-dedup | SQLite 条目/注册表/基线表 + AM/PM 槽位 | 1 |
| 6 | v01-classify-builtin | 七大类+免费/付费双信号 | 1 |
| 7 | v01-push-feishu-route | 飞书卡片 + 阈值分级路由 | 5, 6 |
| 8 | v01-pipeline-orchestrator | Pipeline DAG + APScheduler + asyncio 并发 | 1-7 |
| 9 | v01-cli-basic | `myia run` 基础命令 | 8 |
| 10 | v01-plugins-official | ai-news / wool / stocks 三个官方插件 | 1-8 |

## 实现完成记录(2026-10-01,状态已置 review)

- 十个子任务全部实现;深度评审 45 条发现(44 确认),24 条 high/medium 已修复并回归(407 passed / 10 skipped)
- 未修 backlog:约 20 条 low(测试缺口/文档措辞类,见工作流报告);1 条 unconfirmed(Windows tzdata,复核被 uv.lock 传递解析证伪)
- 遗留 openIssues(实现期发现、未进修复清单的 schema 缺口):**json_path 提取器无法表达"请求 URL"字段**(L1 引擎 URL 模板与取数字段解耦,v0.2 schema 演进时定);enrich 缺 base_url 字段已在 v02-enrich-llm 排期(grill Q6);firecrawl 枚举缺失已修复
- 待主人手动验收:飞书真实收卡一次(quality.md 要求记录任务日志)、真实源 smoke(MYIA_SMOKE_REAL=1)
- 注:修复经回归测试验证,未再走独立评审轮(风险低:每条修复附回归测试)
- 分类金测夹具已净化(仅 title/expected 两列,私有语料文件名列已删)

## 跨子任务验收标准(v0.1 Done 定义)

- [ ] `myia run plugins/stocks.yaml` 端到端一次跑通:抓取→分类→去重→飞书分级推送
- [ ] 变更指纹生效:内容未变的源被跳过(日志可见 skip 原因)
- [ ] `engine: auto` 时 L1 失败自动降级 L2→firecrawl,选择结果有记录
- [ ] YAML 出现明文 Cookie/Token → 启动即报错拒跑
- [ ] ai-news / wool / stocks 三插件用真实源跑出真实数据,二跑不重发
- [ ] pytest 全绿,CI 通过

## 约束

- 纯 Python + uv;核心零重依赖(SQLite 单文件,默认不用 Redis/PG)
- schema 每一节必须有明确语义与缺省值(AI 生成命中率的前提)
- 采集默认尊重 robots.txt;429/5xx 指数退避
- 引擎重依赖(crawl4ai/scrapling 等)一律可选依赖,核心 pip install 即跑

## Open Questions(全部已关闭,2026-10-01)

已解决(主人确认):

1. ~~「X聚合」~~ = X 平台本体 `https://x.com/`(L1 示例指其公开 JSON 端点;实战对标 X→L6/cookie 池,见 v04 引擎任务)
2. ~~TapNow~~ = `https://app.tapnow.ai/`(L2 实例;v02-engine-crawl4ai 的 JS 渲染验证源候选)
3. ~~Hermes~~ = 开源 AI agent 框架(推断 NousResearch/hermes-agent);主人定调:不确定有帮助就不管——SKILL.md 按通用标准发行,不专门适配单一 agent,验收目标即 Claude Code/Cursor
4. ~~桌面 Tauri spike 是否提前~~ = **保持 v0.2**(grill Q10)

Grill 第一轮十项决议(route 缺省映射/Jinja2/AM-PM 分界/proxy 排期/webhook/enrich 端点/反馈闭环/MIT+MYIA/.trellis 公开)见 `research/grill-round1-decisions.md`。
