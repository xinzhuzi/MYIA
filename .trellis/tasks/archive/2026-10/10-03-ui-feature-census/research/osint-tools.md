# OSINT/情报收集类 UI 调研(原始报告)

> 调研路 A(2026-10-03,网调代理全文留痕;能力点均带证据 URL)

调研对象:SpiderFoot(开源版)、Maltego(Graph/Cases/Evidence)、OpenCTI、MISP、Hunchly

**1. 仪表盘/概览**
- 自定义仪表盘:widget 自由拖放/resize、相对+固定时间范围、View/Edit/Manage 三级权限、JSON 导出导入/复制、公开仪表盘列表(OpenCTI)https://docs.opencti.io/latest/usage/dashboards/
- 面向分析员的 widget 化仪表盘(MISP 2.5.39 重制)https://misp-project.org/features/
- 网页活动流 Dashboard:案件下逐页捕获概览(Hunchly)https://support.hunch.ly/article/17-1-starting-hunchly-for-the-first-time

**2. 调查/案例管理**
- 案件容器:一案多图谱、与协作者共享、桌面/浏览器端互开(Maltego Cases)https://docs.maltego.com/support/solutions
- 图谱调查工作区:实体/关系手动创建、按容器级授权访问(OpenCTI Investigations)https://docs.opencti.io/latest/usage/pivoting/
- 事件视图内容切换面板:Pivots/Event graph/Event timeline/Correlation graph/Galaxy matrix/分析师笔记与讨论(MISP)https://www.circl.lu/doc/misp/using-the-system/
- 案件=目录:自动捕获+标注+附件,产出"court-ready"取证包(Hunchly)https://www.hunch.ly/

**3. 数据流/结果浏览**
- 事件/属性两级列表:按标签/galaxy/威胁等级过滤,属性搜索与 REST API 同引擎(MISP)https://www.circl.lu/doc/misp/using-the-system/
- 图谱之外提供表格视图与列映射导入导出,搜索结果可一键转图谱(Maltego)https://docs.maltego.com/support/solutions
- 扫描结果按模块/数据类型分面浏览,SQLite 后端支持自定义查询(SpiderFoot)https://github.com/smicallef/spiderfoot
- Transform Slider/结果上限滑块:控制单次展开量,防图谱爆炸(Maltego)https://docs.maltego.com/support/solutions

**4. 任务调度与自动化**
- Playbook 可视化画布:触发器+组件编排+安全覆盖视图(OpenCTI)https://docs.opencti.io/latest/usage/playbook-automation/
- Background tasks:数据上批量异步任务及进度管理(OpenCTI)https://docs.opencti.io/latest/usage/background-tasks/
- Machines:一键串联执行 transform 宏、可中途停止(Maltego)https://docs.maltego.com/support/solutions
- 单次扫描自动编排 200+ 模块,YAML 关联规则可配置(SpiderFoot)https://github.com/smicallef/spiderfoot

**5. 告警与通知**
- 通知中心:顶栏铃铛入口,Alerts/Triggers 双 tab;实时触发+日/周/月 digest;实体详情页"实例触发器"快捷订阅铃铛;通知通道在 Settings>Customization>Notifiers 管理(OpenCTI)https://docs.opencti.io/latest/usage/notifications/
- 攻击面监控变更通知:email/REST/Slack(SpiderFoot HX)https://github.com/smicallef/spiderfoot
- Data Forwarding:设置齿轮面板把捕获数据转发外部系统(Hunchly)https://support.hunch.ly/article/67-2-hunchly-data-forwarding

**6. 数据导出/报表**
- CSV/JSON/GEXF 三格式导出(GEXF 可直接进 Gephi)(SpiderFoot)https://github.com/smicallef/spiderfoot
- 事件级 Download as…:JSON/CSV/STIX/Suricata/RPZ 等(MISP)https://www.circl.lu/doc/misp/using-the-system/
- 手动导出+对外 Feed 共享(OpenCTI)https://docs.opencti.io/latest/usage/export/
- 图谱导出为表格/图片;报告包(PDF/HTML)+自动审计链(Maltego/Hunchly)https://docs.maltego.com/support/solutions 、https://www.hunch.ly/

**7. 配置/凭据管理**
- 设置页集中管理全部模块 API key,且支持 API key 整体导入导出(换机迁移)(SpiderFoot)https://github.com/smicallef/spiderfoot
- 内置 REST client、TOTP 管理、四步 Sharing Group 向导(MISP)https://www.circl.lu/doc/misp/using-the-system/
- 通知通道/连接器集中配置于 Settings 树(OpenCTI)https://docs.opencti.io/latest/usage/notifications/

**8. 值得 MYIA 抄的 UI 亮点**
- OpenCTI 实体页"快捷订阅铃铛":在结果详情面板就地创建监控触发器——契合 MYIA 关键词漏斗+推送
- OpenCTI 仪表盘/触发器 JSON 化分享:可迁移为 MYIA「数据源+关键词+渠道」配置包的导入导出
- Maltego Transform Slider:可化为 MYIA「粗筛量/精评配额」滑块,控制每轮 LLM 成本
- MISP 两步标签选择器(收藏/集合/分类法库):YAML 插件与关键词库的 picker 交互范本
- Hunchly 案件级不可变审计链:本地优先工具的存证式浏览 UI

**适用性边界:OpenCTI/MISP 重平台对 MYIA(单人桌面)**
- 整体不适用:多组织协作与权限域(RBAC/authorized members/Sharing Group/委托发布/proposals/事件讨论)、跨实例同步与 Feed 分发、STIX 数据标记治理、connector 生态管理——单人本地应用无共享与权限模型
- 弱适用:自定义仪表盘(仅保留 2-3 个固定概览图即可,widget 市场与公开仪表盘无意义)、Playbook 画布(YAML 插件+关键词+推送渠道的向导式表单足够,不需要节点画布)
- 高度可抄:通知触发器/digest 分级(OpenCTI)、列表过滤与属性搜索(MISP)、案件化捕获+取证导出(Hunchly)、凭据集中管理与整包迁移(SpiderFoot)
