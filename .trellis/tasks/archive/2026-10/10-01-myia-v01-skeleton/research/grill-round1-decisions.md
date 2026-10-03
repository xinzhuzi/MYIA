# Grill 第一轮决议(2026-10-01,主人确认"按照你的建议去做")

Round 1 十问全部按推荐答案执行。逐项决议与落点:

| Q | 决议 | 落点 |
|---|---|---|
| Q1 | v0.1 无 score 时 route 缺省=**按大类映射**:羊毛/节点/代买→immediate;AI资讯/服务器/token/信用卡→digest;YAML 可覆盖;分类未命中→保守 digest。v0.2 LLM score 回填后 score 路由优先于大类映射 | v01-push-feishu-route PRD |
| Q2 | 模板引擎=**Jinja2** + SandboxedEnvironment 沙箱;规划示例的 Handlebars 语法改写为 Jinja2 并同步文档;jinja2 进核心依赖(第 6 个) | v01-push PRD、spec/python/index.md |
| Q3 | AM/PM 槽位=**本地时区 12:00 分界**(AM=00:00–11:59,PM=12:00–23:59) | v01-store-dedup PRD |
| Q4 | proxy 三层排期(roadmap 缺口补齐):**v0.1** 仅解析校验、仅 direct 生效 → **v0.2** fetch_base 代理 transport(单上游 HTTP/SOCKS5)→ **v0.3** myia-proxy 池轮换+住宅 IP 适配器 | v01-fetch-base PRD、v02/v03 对应任务 |
| Q5 | **webhook 并入 v0.2**(v02-push-telegram 任务扩为 TG+webhook);**email 砍掉**(社区贡献) | v02-push PRD、v0.2 父 PRD |
| Q6 | enrich 缺端点策略:**model 缺省 glm-4-flash;base_url 必须显式配置(env:)**;MYIA 无内置端点、无默认 key | v02-enrich-llm PRD |
| Q7 | 反馈闭环分形态:**桌面=TG 轮询(getUpdates)+ `myia feedback mark` CLI;飞书卡片回调仅服务端/compose 形态**(默认关+token+内网) | v03-feedback-loop PRD |
| Q8 | **.trellis/ 随开源仓库公开提交**;任务文档自下笔起按公开标准撰写(私有信息零容忍红线提级) | spec/domain/security-baseline.md |
| Q9 | **LICENSE=MIT**(与 pyproject 一致);**项目名=MYIA**(ScoopHub/InfoForge 备选作废) | v10-release PRD |
| Q10 | **Tauri spike 保持 v0.2**(v0.1 完成后 CLI 可打包,spike 才有实体) | v0.1 父 PRD OQ4 关闭 |

## 技术自决项(非用户决策,记录备查)

- CLI 框架=argparse(标准库零依赖,命令量不需要 typer/click)
- schema 模块名=`schema.py`;编码探测放 fetch_base(统一解码后交引擎)
- 降级 hint 回写 SQLite `engine_hints`,**不回写用户 YAML**
- myia-classifier 采用同仓库 uv workspace 发包(v1.0 时如遇发布摩擦再议独立仓库)

## 事实修正(同轮查证,无需决策)

- 七大类引擎源头在:`LOCAL-NOTES.md 索引的 wf_crawl.py(本地)`(原以为缺失)——v01-classify-builtin 从它提炼关键词表与双信号规则
- "wool 等 7 源"=linux.do、cocoloop.cn、linux.sb、v2ex.com、bbs.bt.sb、nodeloc.com、sb.sb(迁移包 urls-*.txt 全量域名统计)

## 追加决议:Q11(同日,主人确认推荐)

- **v1.0 发布形态 = CLI(PyPI)+ Docker(compose)+ Agent Skill 三形态**;桌面客户端正式版(Tauri 壳+五大界面+updater)与 Web 面板正式版排 **v1.1+**——待 v02-desktop-spike 实测结论后细化任务树;v0.x 阶段桌面唯一交付就是 spike。落点:myia-v10-launch PRD、v02-desktop-spike PRD。
- **至此 grill frontier 清空**:产品与路线层决策全部闭合,无静默假设;剩余 design 级选择(编码探测位置、pools 配置位置、模板细节等)均在各任务 design.md 阶段进行且已在 PRD 标注。v01-classify-builtin 的双信号裁决规则细节会在该任务 design 阶段提请主人确认(源头 wf_crawl.py 已定位)。
