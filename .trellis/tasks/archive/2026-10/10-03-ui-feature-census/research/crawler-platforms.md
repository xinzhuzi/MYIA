# 爬虫/采集平台类 UI 调研(原始报告)

> 调研路 B(2026-10-03,网调代理全文留痕;能力点均带证据 URL)

调研对象:Octoparse、ParseHub、Apify Console、Zyte Scrapy Cloud、Browse AI、changedetection.io(Firecrawl 仅轻量 playground 附带提及)

**1. 任务/爬虫创建**
- 无代码点选构建器(内置浏览器点元素生成规则):Octoparse 桌面客户端、ParseHub 桌面端(点选+左侧命令树) https://www.octoparse.com/docs/en/platform/schedules ; https://www.parsehub.com
- 模板市场(按网站搜预建任务直接套用):Octoparse 模板库 https://www.octoparse.com/docs/en
- 表单化运行(商店装"Actor",Input Schema 自动渲染成表单):Apify Console https://docs.apify.com/platform/actors
- 纯代码路线(写 Scrapy spider 部署,官方 starter 模板):Zyte Scrapy Cloud https://github.com/zytedata/zyte-spider-templates-project ; https://docs.zyte.com
- 变更监测式创建(贴 URL→点选要监控的区块):changedetection.io Visual Selector https://github.com/dgtlmoon/changedetection.io

**2. 调度器 UI**
- 6 位 cron+@daily 快捷键+"可视化排程构建器"+时区/DST+Disable/Enable 开关+"Next runs"预览未来 5 次:Apify Schedules https://docs.apify.com/platform/schedules
- 云/本地双模式调度、最高 1/5/10/30 分钟频率、本地任务 Maximum runtime 止损:Octoparse https://www.octoparse.com/docs/en/platform/schedules ; https://helpcenter.octoparse.com/en/articles/6470926-lesson-6-schedule-regular-runs
- Periodic Jobs(hourly/daily/weekly/自定义 cron):Zyte Scrapy Cloud https://www.scrapingbee.com/blog/scrapy-cloud-tutorial/
- Get Data 按钮三合一(Test/Run/Schedule):ParseHub https://www.parsehub.com

**3. 运行历史**
- 云/本地提取历史,可检视、计数、预览行再导出:Octoparse https://www.octoparse.com/docs/en/cli/guides/data
- Run 列表+详情页(状态、实时日志流、重试、耗时/资源):Apify Console https://docs.apify.com/platform/actors/running
- Task History(每次 capture/monitor 运行留档可回看):Browse AI https://www.browse.ai/monitor

**4. 数据浏览与导出**
- Table/JSON 双视图+Export 七种格式(JSON/CSV/Excel/XML/HTML/RSS/JSONL)+Select/Omit 字段+Preview+Clean items 剔除调试字段:Apify Dataset https://docs.apify.com/platform/storage/dataset
- 文件(Excel/CSV/HTML/JSON/XML)+数据库+API 三通道导出,含 MCP export_data:Octoparse https://helpcenter.octoparse.com/en/articles ; https://www.octoparse.com/docs/en/mcp/export-data
- REST 取数 `GET /v2/datasets/{id}/items?format=csv&fields=...`:Apify https://docs.apify.com/platform/storage/dataset

**5. 监控与通知**
- 70+ 通知渠道(邮件/Slack/Discord/Telegram/webhook/API 调用):changedetection.io https://github.com/dgtlmoon/changedetection.io
- 调度失败自动邮件告警+按 schedule 批量管理通知:Apify https://docs.apify.com/platform/schedules
- Monitor 告警内容即"变了什么"(价格/列表/排名级):Browse AI https://www.browse.ai/monitor

**6. 代理/凭据管理**
- 按 watch 绑代理+内置 Proxy Scanner 逐个实测可用性:changedetection.io https://github.com/dgtlmoon/changedetection.io
- Rotate proxies 一键开关(运行中自动换 IP):ParseHub(官方 help.parsehub.com)
- 代理浏览器+凭据(密码)保险库绑定运行:Apify https://docs.apify.com/platform/proxy

**7. 变更监测特有**
- Diff 视图可按词/行/字符粒度比对,附截图快照:changedetection.io https://github.com/dgtlmoon/changedetection.io
- Visual Filter Selector 点选后高亮命中区块:changedetection.io https://changedetection.io/tutorial/conditional-actions-web-page-changes
- Changelog 时间线(每行=一次变化,可跳转对应 capture):Browse AI https://www.browse.ai/monitor

**8. 值得抄的亮点**
- Apify Input Schema:YAML/JSON schema 自动渲染成带校验的设置表单(MYIA 插件配置页可直接套用)https://docs.apify.com/platform/actors
- Apify "Next runs" 预览:保存调度前先看未来 5 次触发时间,防 cron 写错 https://docs.apify.com/platform/schedules
- Octoparse 本地调度的 Maximum runtime:本地任务卡死的熔断开关 https://www.octoparse.com/docs/en/platform/schedules
- Apify Dataset 的 Select/Omit fields+Clean items:导出时才裁剪字段,采集与消费解耦 https://docs.apify.com/platform/storage/dataset
- Firecrawl 仅 firecrawl.dev/app 内嵌 playground(试爬单页看 Markdown),无完整任务管理 dashboard,参考价值低 https://firecrawl.dev/app

**依赖云端多租户、MYIA 单机不可直接照搬**
- Apify 每 run 的内存/构建/队列资源分配、秒级调度触发保证——本质是集群资源编排,MYIA 只有本机,只需并发上限+队列
- Apify 共享住宅代理池(按流量计费)——MYIA 只能做"用户自填代理列表+连通性测试"(changedetection.io 的 Proxy Scanner 是可照搬的本地版)
- Octoparse 云提取/云端排队——MYIA 对应本地 APScheduler,仅需借鉴其 UI 呈现而非架构
- Apify Actor 商店、Octoparse 模板市场的中心化生态——MYIA 可退化为本地 YAML 模板目录+导入/分享文件
- 数据集保留期计费——本地无配额概念,保留期可改为磁盘上限清理
- 邮件/Webhook 通知依赖外部服务——本地版保留 webhook 出站+系统通知即可,邮箱告警降级为可选项
