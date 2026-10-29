# 微信本机工作台

> 消息屏的「微信本机」区:把本机微信的历史与现状变成可查、可统计、可导出的
> 只读工作面——聊天记录查询与导出、统计报告、媒体与朋友圈、联系人与找回、
> 新消息通知、本地库 HTTP API,六块功能面挂在同一张卡片里。数据全部来自
> 受管组件 wechat-bridge 的只读通道(解密副本 + 像素链):零写微信数据、
> 零进程干预。解密钥匙的外置协议见[微信取钥器协议](../wechat-keyprovider.md),
> HTTP API 完整契约见[本地库 HTTP API](../wechat-http-api.md),
> 媒体解密见[媒体解密件协议](../wechat-mediadecrypt.md)。

## 1. 一屏总览

卡片头是探针三态胶囊(可用 / 降级 / 不可用)+ 版本行(微信版本 /
桥组件版本,mono);降级与不可用时逐条列出原因码与修复指引(如密钥缺位、
微信未开)。六块功能面:

| 功能面 | 干什么 | 数据来源 |
|---|---|---|
| 聊天记录 | 会话直读、本地库查询、分页浏览、六格式导出 | 当前窗口像素链 + 解密副本 |
| 统计与报告 | 私聊 / 群聊 / 年度 / 双人四类统计 + AI 报告 | 解密副本聚合 |
| 媒体与朋友圈 | 聊天图片缩略图、朋友圈时间线与导出 | 本机媒体文件(经解密件)+ 解密副本 |
| 联系人与找回 | 五桶浏览、搜索、「曾经的好友」考古 | 解密副本 |
| 新消息通知 | 分钟级轮询新消息与撤回,系统通知弹窗 | 解密副本(品类形态) |
| 本地库 HTTP API | 把只读能力开成本机 HTTP 服务 | 解密副本 |

工作台读回只在面板内展示,不入情报管线(要进情报流,用显式声明的品类,
见第 7 节);发送不在本工作台(出站推送走 `weixin-local` 通道,约定见
[插件开发指南](write-a-plugin.md))。

## 2. 前置条件

### 2.1 桥组件与系统依赖

工作台的一切能力经一次性行子进程调用受管组件 wechat-bridge。安装位置:
**设置 → Python 环境的组件区**,「微信本机桥(wechat-bridge)」一键安装
(依赖 pyobjc + pillow);本地库通道另需 `brew install sqlcipher`(缺装时
解密步骤结构化报指引,不静默半装)。卡头的「刷新状态」随时重跑探针。

### 2.2 数据库密钥(取钥器)

本地库查询 / 统计 / 朋友圈 / 联系人读的是 SQLCipher 加密库的**解密副本**,
密钥不在 MYIA 产品代码里——取钥能力整体外置给你自备的取钥器,协议见
[微信取钥器协议](../wechat-keyprovider.md)。设置页「微信账号」卡提供三件:

- **数据库密钥状态行**:三态徽标(已就绪 N 库 / 已失效 / 未获取)+ 取得时间;
- **获取密钥 / 重新获取按钮**:走交互式取钥(会弹系统密码框,并要求在微信里
  退出登录并重新扫码;全程只读,微信数据不会被修改);ready 态的「重新获取」
  在新密钥验证通过后才覆盖旧钥;
- **取钥器配置行**:保存取钥器可执行文件路径;未配置时可**一键安装**官方
  参考部署件(落到数据根私有区 `wechat-localdb/bin/` 并自动登记为取钥器路径)。

全量验真通过的密钥才整体落密钥缓存 `keys.json`(数据根私有区,权限 600);
换号登录后旧钥全不匹配,如实报 `key_unavailable` 并指引重取。CLI 等价:

```console
wechat-bridge localdb key-status --json    # 三态真源:ready | stale | missing
wechat-bridge localdb key-acquire --json   # 交互式取钥(ready 且无 --force 时短路)
```

### 2.3 屏幕录制权限

「只读会话入口」读的是**当前打开会话**的屏幕内容(截窗 + OCR 像素链),
依赖 macOS「屏幕录制」权限,授权主体是 MYIA app 本体:打开
系统设置 → 隐私与安全性 → 屏幕录制,开启 MYIA 的开关,回到本屏点
「刷新状态」。像素链全程不抢前台焦点、不合成鼠标点击、不激活微信窗口。

## 3. 聊天记录:查询、分页与导出

### 3.1 只读会话入口(当前窗口)

「拉取会话列表」→ 选中会话 → 展示最近消息(时间 / 发送者 / 类型徽标 /
正文;媒体类显示「类型 + 占位」)。读的是当前窗口已加载的内容——查历史
请用下方的本地库查询;会话列表为空时先确认微信主窗口已开。

### 3.2 本地库查询与分页浏览

- **查询行**:会话 id(如 `filehelper`)+ 天数(缺省 30)→ 三域
  (message / session / contact)联查;展示名与各域状态徽标如实呈现,
  缺域不装成功;
- **分页浏览**:时间窗快捷档(近 7 天 / 近 30 天 / 全部)→「拉取最新一页」,
  消息旧在上新在下;「加载更早」按 keyset 游标在头部续拉,不重翻旧页。

密钥未就绪时查询报 `key_unavailable`(带修复指引),不静默降级。

### 3.3 六格式导出

「导出本会话」:格式下拉 + 「选择目录并导出」(壳弹目录对话框,取消即
静默收手)→ 进度百分比 + 可取消 → 回执列出产物路径(点击可在访达中查看)。
六个格式:

| 格式 | 说明 |
|---|---|
| JSON 文件 | 结构化数据 |
| 纯文本 | `.txt` |
| CSV 表格 | 表格行 |
| Markdown | 文档 |
| 网页 | HTML |
| Excel 表格 | `.xlsx` |

回执附 manifest 对账(件名 / 路径 / 行数 / sha256 / 总行数 / 时间窗 /
耗时)。CLI 等价(stdout NDJSON 进度流):

```console
python -m wechat_bridge localdb export <chat> \
  --format json|txt|csv|md|html|xlsx --out <dir> [--all]
```

## 4. 统计与报告

### 4.1 四种报告

报告类型四选 → 会话选择(私聊 / 双人选好友,群聊分析选 `@chatroom`,
年度免选改选年份)→ 时间窗(近 30 天 / 近一年 / 全部)→「生成」:

| 报告 | 出什么 |
|---|---|
| 私聊分析 | 总消息 / 我发 / 收到 / 最长连续聊天天数、会话段数与双方发起数、平均回复速度、24 小时时段条形、消息类型分布(文字 / 图片 / 语音 / 视频 / 表情 / 文件链接 / 位置 / 其他 / 系统 / 撤回)、一周分布、高频短语 |
| 群聊分析 | 私聊全项 + 发言排行(前 10) |
| 年度报告 | 总消息(私聊)/ 聊过的人 / 深夜(0-5:59)消息与深夜之最 / 最常聊的人(前 10)/ 高频短语 / 时段条形 |
| 双人报告 | 我与对方两列对照(消息数 / 开启话题次数 / 平均回复 / 高频短语)、活跃天数、最初一条、7×24 聊天热度格 |

纯 CSS 条形与热度格,零图表库;结果带缓存提示——首次统计较慢,之后秒出
(桥侧文件缓存,命中显示耗时)。

### 4.2 AI 报告与导出

「生成 AI 报告」把**面板已持有的统计聚合**回传生成叙事报告(桥不重查;
入口做防御性剥离,喂模型的只有聚合数字与高频短语,零逐条消息原文),渲染
为四章(总览 / 时段画像 / 关系洞察 / 结语),并标注所用模型与 token 消耗。
端点配置在 `<数据根>/config/wechat-stats.yaml` 的 `stats_report` 段:
缺省关,未开启报 `ai_disabled` 带配置指引;openai 客户端缺位报
`dependency_missing` 带安装指引。「导出」经保存对话框一次落同名
`.json` + `.md` 两件。

## 5. 媒体与朋友圈

### 5.1 聊天图片(解密件)

会话 id + 天数 →「扫描媒体」:窗口内图片 / 视频清单与缩略图实显。图片
缩略图经**你的媒体解密件**解出(加密 `.dat` → 解密缓存,幂等零重解);
视频显示明文 `_thumb.jpg`;微信实况格式(wxgf)如实标注「实况格式,
暂不支持预览」。点击条目展开详情(媒体 key、源文件路径、解密缓存产物,
mono 块)。统计行如实计数(消息 / 图片 / 视频 / 其他 / 文件缺失 / 已解出)。
未配置解密件时媒体面结构化降级(exit 23,文本链零影响),协议见
[媒体解密件协议](../wechat-mediadecrypt.md)。

### 5.2 朋友圈时间线与导出

天数 +「拉取朋友圈」→ 只读时间线(发帖人 / 时间 / 正文 / 媒体徽标
「图片 ×n / 视频 ×n」;转发注明来源)。导出两格式「网页(HTML)/
数据(JSON)」,产物落本机私有目录(回执给出目录与件数)。媒体下载
缺省关;CLI 面显式开启时是桥的唯一受限出网豁免(只 GET,URL 全来自库内):

```console
python -m wechat_bridge localdb sns export --format html --since 30d --with-media
python -m wechat_bridge localdb media scan <chat> --since 30d --decrypt --json
```

## 6. 联系人与「曾经的好友」

### 6.1 五桶浏览与搜索

计数 chip 行(好友 / 群聊 / 公众号 / 曾经的好友 / 其他;数字来自查询
实值,非硬编码)点击切桶;搜索框按备注 / 昵称 / 别名 / 微信号 / 拼音过滤
(200ms 防抖,清空恢复首页);列表 100 条 / 页 +「加载更多」游标续拉;
群聊行带成员数徽标。

### 6.2 找回考古面

只有「曾经的好友」chip 才进,顶部常驻声明:从本地库残留找出,**不保证
完整与准确**,按证据分层,可能含群内陌生人缓存。行内证据徽标分两层
(「会话痕迹」/「身份字段残留」),另给分层计数(有会话痕迹 / 仅通讯录
残留);伴随子卡:**非好友联系人缓存**(尚未入正常联系集的类联系人记录)
与**群申请记录**(群验证 / 入群申请,来源表只读呈现)。考古查询按需解密
消息库副本(大库首跑「考古中…」如实等待);标签尾注只给库内标签计数
——标签与联系人的归属在加密缓冲内,不做逐人关联。

### 6.3 三格式导出

勾选 JSON 文件 / CSV 表格 / 通讯录 VCF →「导出当前筛选」→ 保存对话框
定基础名(取消静默)→ 回执列总行数与各件路径。

## 7. 新消息通知与撤回

### 7.1 品类形态(weixin-notify)

通知不是面板轮询,而是一个标准品类源:仓库自带的模板
`config/weixin.example.yaml`(随源码发布,本门面仓不含)(引擎
`wechat_local` 的 `notify` 通道;2026-10-20 四档归一后微信是**单档多通道**
——pixel/localdb/sns/notify 四通道的源同栖一份 `weixin` 品类档共享
schedule,notify 通道样例以注释形态收在该文件样例区;复制为
`<数据根>/config/weixin.yaml` 后启用)。品类在场 ≠ 在跑
——注册 cron job(桌面排程或 `myssia cron`,见[定时任务(cron)](cron.md))
后才按 schedule 分钟级拉取;出厂 `chat_whitelist` 为空清单 = 缺省关(引擎
构造期即拒,防功能空转)。启用示例(填入你的目标会话):

```yaml
id: weixin-notify-demo
name: 微信通知(示例)
schedule: "*/1 * * * *"            # 分钟级轮询(notify-scan 恒轻,可调疏)
timezone: Asia/Shanghai
timeouts:
  fetch: 120
sources:
  - name: weixin-通知              # 源名须带 weixin- 前缀(feed 渠道名自动判定)
    engine: wechat_local
    url: "https://weixin.qq.com"   # 锚定形状用;引擎零 HTTP,端点 = 本机桥
    empty_ok: true                 # 密钥缺位 skip / 安静窗零事件 = 合法空
    engine_options:
      wechat_local:
        channel: notify            # 通知通道:轮询水位,零截窗零 AX
        chat_whitelist:
          - "50438708676@chatroom" # 会话 id 直通
          - 张三                   # 显示名:引擎二查解析成 id
        notify_self: false         # 自己发的消息不通知(缺省)
        read_timeout_seconds: 120  # 单次桥扫描上界(须 ≤ timeouts.fetch)
    rate_limit: {qps: 0.5, jitter: "2s", backoff: exponential, respect_robots: true}
    proxy: direct                  # 零 HTTP;本机读回
    retry: 1
watchlist:
  keywords: []
  mute: []
classify:
  builtin: false
  rules: []
dedup:
  key: "{url}"                     # #wxn-/#wxnr- server_id 强锚,重读恒沉默
enrich:
  enabled: false                   # 红线:私有内容不出本机(不送 LLM)
push:
  - channel: macos_notify          # 本机通知中心弹窗,零网络
storage:
  retention: 30d                   # 隐私谨慎缺省
  vacuum: monthly
```

`chat_whitelist` 填会话 id(`wxid_xxx` / `xxx@chatroom` 直通)或显示名;
**不声明即不通知**(白名单外的会话零扫描)。键名是 `chat_whitelist`
不是 `sessions`——schema 凭据基线把含 session 的键当凭据位,声明
`sessions` 会被结构化拒并指路。品类字段含义见[schema 参考](schema.md),
品类写法见[写一个插件](write-a-plugin.md)。

### 7.2 轮询水位与撤回恢复

每轮一次桥 `localdb notify-scan`(白名单与水位走 stdin JSON,argv 零
内容):会话基线水位先筛(白名单内会话比对 `{ts, unread}`)→ 仅候选会话
定点增量查 → `message.new` 事件;撤回谓词(`local_type` 10002 /
10000 + 内容标记)→ `message.revoke` 事件,原文恢复走阶梯:server_id
直查 → 时间近邻启发式 → replacemsg 文本兜底 → 未能恢复(三态如实,不
臆造内容)。首轮只建基线零事件(防装机首弹轰炸);条目锚
`#wxn-<server_id>` / `#wxnr-<server_id>` 强幂等,重读恒沉默;密钥缺位 =
本轮跳过不算失败,水位不推进。

### 7.3 通知呈现(macos_notify)

投递走 `macos_notify` 通道:本机通知中心弹窗,**零凭据零端点零出网**
(通知正文不进日志 / 网络面)。合并纪律:同轮同会话的新消息并成一条
(标题 = 会话名,副标题 = N 条新消息,正文 = 最新一条「发送者: 预览」,
正文 200 字帽、条目标题 80 字帽);撤回事件独立成条,正文带三态文案
(恢复原文 / replacemsg 兜底 / 未能恢复)。桌面应用在跑时以 app 身份
代发(图标 / 归组 / 点击唤起归 MYIA);CLI / 无壳时 osascript 直弹
(归组显示为 osascript)。系统通知中心会留通知历史(本机态)。条目同时
入本机 store + feed;`enrich` 关、`watchlist` / `mute` 空——私有内容
不出本机、不送 LLM。会话历史撤回清单另有 CLI:

```text
wechat-bridge localdb revoke-list <chat> --since 7d
```

## 8. 本地库 HTTP API

### 8.1 起服与鉴权

把桥 localdb 只读能力开成本机 HTTP 常驻服务,供外部系统 / 自动化脚本 /
LLM 工具对接(纯只读):

```console
python -m wechat_bridge localdb http-token generate --json   # 生成 token(轮换热生效)
python -m wechat_bridge localdb serve --json                 # 前台常驻,127.0.0.1:5032
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:5032/api/v1/health
```

token 未配置 = 一切非 health 请求 401(防裸奔);`generate` / `clear`
无须重启即刻生效(每请求热重读)。回环强制:服务只绑
`127.0.0.1 | localhost | ::1`,配置 `0.0.0.0` 等非回环值启动即结构化拒绝
——对外暴露解密聊天数据是红线。端口缺省 5032(刻意避开 chatlog 族工具)。

### 8.2 端点与事件流

只读端点族(GET / POST 统一,POST body 参数并入查询参数):

| 端点 | 用途 |
|---|---|
| `/api/v1/health` | 健康与密钥三态(token 豁免) |
| `/api/v1/sessions` | 会话列表 |
| `/api/v1/messages` | 时间窗消息查询 |
| `/api/v1/sessions/{chat_id}/messages` | ChatLab Pull 水位增量拉取 |
| `/api/v1/group-info` | 群信息(缺表降级空结构) |
| `/api/v1/sns` | 朋友圈动态 / 互动通知 |
| `/api/v1/media/<rel>` | 白名单目录内媒体文件下发 |
| `/api/v1/push/messages` | SSE 事件流(`message.new` / `message.revoke`) |

SSE:25s 心跳,断线带 `Last-Event-ID` 重连重放(环形缓冲 ≤1000 事件 /
10 分钟);事件延迟为扫描间隔量级(分钟级,缺省 60s 一轮),要更细粒度
用 Pull 端点按 watermark 轮询。桥错误族映射 503,码名与 CLI 同源
(`key_unavailable` / `db_not_found` / `decrypt_failed`)。完整契约、
鉴权三通道与媒体白名单守卫见[本地库 HTTP API](../wechat-http-api.md)。

## 9. 只读红线

- **零写微信数据**:一切查询读数据根私有区的解密副本(600 / 700 权限;
  缺省 24h TTL + 容量上限,源库更新才重建),微信侧文件零写入。
- **零进程干预(读链)**:像素链不抢焦点、不合成点击、不激活微信窗口;
  通知扫描零截窗零 AX,只读解密副本。
- **密钥不出数据根私有区**:`keys.json` 权限 600;取钥与媒体解密能力整体
  外置(协议见[取钥器](../wechat-keyprovider.md)与
  [媒体解密件](../wechat-mediadecrypt.md)),MYIA 产品代码零取钥、零解密实现。
- **唯一受限出网豁免**:朋友圈媒体回源(`--with-media`,缺省关;只 GET、
  URL 全来自库内);其余一切桥路径零出网。
- **读回不入情报管线**:工作台各面板纯展示,不入 store / 去重 / 分类;
  要进情报流只有显式声明的品类一途(第 7 节)。
- **如实降级**:密钥缺位 `key_unavailable`、库缺位 `db_not_found`、
  解密失败 `decrypt_failed`——结构化报错带修复指引,不静默装成功。
