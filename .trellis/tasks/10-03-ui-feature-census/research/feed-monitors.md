# 订阅/监听/情报面板类 UI 调研(原始报告)

> 调研路 C(2026-10-03,网调代理全文留痕;能力点均带证据 URL)

调研对象:Feedly、Inoreader、Distill.io、Visualping、Brand24、Google Alerts(下限对照)

**1. 信息流浏览**
- 过滤页签(All/Unread/Saved 按源/文件夹树):Inoreader 左侧栏订阅树+过滤器 https://www.inoreader.com/features
- 全文搜索+可视化查询构建器(免写搜索语法):Inoreader Query Builder https://www.inoreader.com/blog/2023/04/making-complex-searches-easy-with-our-new-query-builder.html
- 全局搜索(超出自己订阅、检索全网公开文章):Inoreader Global Search https://www.inoreader.com/features
- 列表视图切换(卡片/杂志/标题仅列表):Feedly 顶部视图切换 https://docs.feedly.com/category/763-ai-feeds
- AI 看板式聚合(AI Feeds 跨全网按模型聚流):Feedly AI Feeds https://docs.feedly.com/category/763-ai-feeds

**2. 阅读体验**
- 稍后读(Save to Read Later 快捷键 S)、存 Board(T)、标记已读并隐藏(X):Feedly 快捷键表 https://docs.feedly.com/article/81-what-are-the-keyboard-shortcuts
- 保存文章/网页稍后读+浏览器扩展随手存:Inoreader https://www.inoreader.com/features
- 原文高亮+笔记(阅读中划线沉淀情报):Feedly Notes & Highlights https://docs.feedly.com/category/710-notes-and-highlights
- 朗读+跟读进度追踪:Inoreader https://www.inoreader.com/blog/2026/09/follow-along-with-text-to-speech-and-narration-tracking.html

**3. 规则引擎 UI(条件→动作)**
- Rules:按关键词/来源/提及 条件触发 打标、推送、邮件、标记已读,2025 年起新增"翻译/摘要"动作:Inoreader https://www.inoreader.com/blog/2025/10/introducing-new-rule-triggers-and-actions-translations-summaries-and-more.html
- Mute Filters(按关键词/站点静音降噪):Feedly https://docs.feedly.com/category/706-mute-filters
- 变更触发 Conditions→Actions(通知/webhook/回放宏):Distill https://distill.io/docs/web-monitor/what-is-distill/

**4. 告警**
- 关键词 Alert(Google News 关键词即订阅即告警):Feedly https://docs.feedly.com/category/443-web-alertskeyword-alerts;对照下限 Google Alerts(仅邮件、频率/来源类型/语言/地区四项设置)https://support.google.com/websearch/answer/4815696
- 检查频率控制(2 分钟~每周任选):Visualping https://visualping.io/blog/visualping-ai;Distill Schedule Checks https://distill.io/docs/web-monitor/what-is-distill/
- 多渠道推送(邮件/短信/App 推送/Slack/Discord/Teams/webhook):Distill https://distill.io/docs/web-monitor/what-is-distill/
- 量级突增告警(Storm Alerts 附关键提及与热度统计):Brand24 https://brand24.com/features
- 自动化 Newsletter 摘要输出:Feedly https://docs.feedly.com/category/691-automated-newsletters

**5. AI 能力的 UI 呈现**
- AI 摘要(卡片上一键生成摘要):Feedly Summarization https://docs.feedly.com/category/704-summarization;Inoreader 摘要规则动作
- 去噪/去重(重复报道折叠、低信噪过滤):Feedly Deduplication https://docs.feedly.com/category/708-deduplication
- 告警 AI 摘要+自定义提示词的"重要告警"筛选:Visualping https://visualping.io/blog/visualping-ai
- 情绪标注(正/中/负打在提及流)+讨论主题提取:Brand24 https://brand24.com/features
- 异常检测(提及量/触达异常尖峰,卡片内感叹号入口):Brand24 Anomaly Detector https://brand24.com/features
- AI 问答式分析(Ask AI 对 Feed 内容提问):Feedly https://docs.feedly.com/

**6. 统计分析**
- AI Insights 个性化周报(图表+趋势+建议):Brand24 https://brand24.com/features
- 来源/作者分布、影响力评分、声量份额:Brand24 https://brand24.com/features
- Inoreader Intelligence(订阅内容洞察看板):Inoreader https://www.inoreader.com/blog/2026/01/get-insights-with-inoreader-intelligence.html
- 变更历史 diff 视图(逐条对比高亮变化):Distill https://distill.io/docs/web-monitor/what-is-distill/

**7. 订阅源管理**
- 添加/发现(编辑策划的主题合集发现新源):Inoreader https://www.inoreader.com/features
- 导入/导出 OPML:Feedly https://docs.feedly.com/category/493-export-import
- 监控源健康(本地 vs 云监控双轨):Distill https://distill.io/docs/web-monitor/cloud-local-monitors/
- 监控源类型多样(网页/PDF/JSON/XML/Feed/站点地图/uptime):Distill https://distill.io/docs/web-monitor/what-is-distill/

**8. 值得 MYIA 抄的 UI 亮点**
- Mentions 实体提及层(文章中人物/品牌/组织自动识别,可作规则触发器):Inoreader https://www.inoreader.com/blog/2026/09/introducing-mentions-redesigned-search-and-smarter-monitoring-feeds.html
- 宏录制回放(检测到变更后自动执行点击/翻页等采集动作):Distill https://distill.io/docs/web-monitor/what-is-distill/
- Telegram 频道当订阅源直接跟进:Feedly https://docs.feedly.com/category/662-telegram
- LLM Listening(监测品牌在 ChatGPT/Gemini 等 9 个 AI 模型答案中的可见度):Brand24 https://brand24.com/features
- Board 情报看板(卡片+笔记聚合,团队共享):Feedly https://docs.feedly.com/category/711-boards

**云端强依赖能力 → MYIA 本地版适用边界**
- 可本地化:规则引擎(Rules/Mute 可完全本地跑)、快捷键/稍后读/Board/高亮笔记、OPML 导入导出、变更 diff 历史、检查频率调度(参考 Distill 本地监控模式,需进程常驻)
- 需本地替代实现:Feedly AI Feeds/Ask AI、Brand24 情绪/异常检测、Visualping AI 摘要 → MYIA 已有 LLM 精评管线可承接,但需自建"摘要/情绪/异常尖峰"的卡片 UI 呈现
- 本地版基本不可行:Brand24 社交监听语料(闭云端索引)、Inoreader 全局搜索/监控源、Feedly 自动化 Newsletter 投递、Storm Alerts(依赖全网实时索引);MYIA 只能覆盖"自订阅源+自调度抓取"范围
- 推送渠道(飞书/Telegram webhook)本身不强依赖账号体系,Distill 的 webhook+Conditions 模式最适合 MYIA 模仿
