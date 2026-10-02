# v1.1 low backlog 建档素材(2026-10-02,主会话整理自收官报告)

三轮深度评审累计 129 条发现,high/medium 全部已修;以下 18 条 low 未修,按模块归档。
建档员据此创建 trellis 任务(建议:单个父任务 + PRD 逐条清单,或按模块 2-3 个子任务)。

## docs / 文档漂移(6 条)

1. `skill/SKILL.md:368` — §4.3 把 run --json 的 stages[] 多列了 enrich(运行时实际五阶段:fetch/classify/dedup/analyze/push;aggregate 仅在声明时插入)
2. `docs/zh/schema.md:193` + `docs/en/schema.md:210` — 「各通道凭据约定见 write-a-plugin」悬空引用:目标页无该节(真实约定在 SKILL.md §2.13 与各通道缺省 env 名);write-a-plugin 需补该节或改指 SKILL.md
3. `docs/demo/README.md:55` — 「根 README 嵌入由发布工位完成」指示过时(README 已嵌入同一 gif,照做会重复)
4. `docs/demo/` — 视频成片仍为占位(B站/YouTube 空链);若本日装配出成片则更新链接,否则保留「需主人录一次」
5. `src/myia/enrich/prompt.py:4` — docstring 引旧路径 classify/data/keywords.json(现为 packages/myia-classifier/src/myia_classifier/data/keywords.json)
6. `packages/myia-classifier/README.md:58` — 自定义词表示例标题「便宜出极速云主机三台」在打包词表零命中(照跑得 None);应改为可命中的标题(如「便宜出极速服务器三台」→server)

## plugins / 插件包(2 条)

7. `plugins/monitor.yaml:30` — 源 URL 注释宣称「resolved from plugin.modes.remote」,无此机制(两处手工维护),误导生成配置的 agent
8. `plugins/myia-douyin/README.md:20`、`myia-monitor:18`、`myia-osint:21`、`myia-proxy:18` — local 模式命令缺 `cd plugins/<id>`(仓库根照抄执行 compose 报 no configuration file;myia-maxun 有 cd,统一之)

## engines / 引擎(2 条)

9. `src/myia/engines/llm_browser.py:8` — docstring「no extra is added」与 pyproject skyvern extra 矛盾(实为服务端安装便利 extra);「Same zero-heavy-dependency approach as firecrawl」亦不成立(firecrawl 有 extra)
10. `src/myia/engines/llm_browser.py:478` — 解析后的 endpoint 值落 INFO 日志(模块自述「Resolved values never reach logs」;应对齐 fetch_base 的 mask 先例,记引用名或掩码)

## feedback / 反馈闭环(3 条)

11. `src/myia/push/feishu_callback.py:295` — 非 ASCII token 触发未处理 TypeError(hmac.compare_digest 限制),应先 encode 再比较并回结构化 401
12. `src/myia/pipeline.py:2002` — 多品类常驻进程各起 getUpdates 轮询同一 bot token → Telegram 409 竞争;至少文档警示,理想是跨进程互斥/单例说明
13. `src/myia/pipeline.py:2016` — `_feedback_poll_loop` 常驻轮询循环零测试(offset 书签/store 复用/异常隔离是唯一常驻接线)

## baseline / 趋势基线(1 条)

14. `src/myia/push/templates.py:462` — vs_msrp 钉死读 `price` 字段名,其他数值字段的品类恒空串;至少 docstring+双语文档披露该契约

## aggregate / 事件聚合(3 条)

15. `src/myia/push/telegram.py:163` — 「另见 N 源」截断路径行长可超自声明的 1024 上限 6 字符(预算未计后缀)
16. `src/myia/enrich/aggregate.py:417` + `enrich/__init__.py:149` + `errors.py:3` — EventAggregator/LLMEnricher 的 Raises 文档声称缺依赖是构造期失败,实际懒加载首调用才触发(同家族 docstring 一并改)
17. `src/myia/enrich/aggregate.py:140` — AggregateOutcome.to_dict 是死代码且 docstring 虚构消费方(run stats/doctor 均不消费);删或接

## release / 发布物料(1 条)

18. `CONTRIBUTING.md:95` — 「Run the suite exactly like CI does」措辞:ci.yml 已改 uv(2026-10-02),确认措辞与工作流一致即可关闭
