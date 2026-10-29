# V2EX 首发文案(中文场景,未发出)

> **状态:已对齐 2026-10-04 终态,待主人定稿,发布节奏由主人定。** V2EX 偏好第一手
> 「分享创造」复盘,技术细节 > 营销话术;建议节点:分享创造(/go/create)或
> Python(/go/python),以站点实际节点为准。回复区保持真诚答疑,勿自顶刷屏。
>
> **附图说明**:V2EX 正文不渲染外链图片,截图须在发帖编辑器里作为附件上传。
> 建议 dashboard / feed / settings 三张(最多全五张),文件在仓库
> `docs/screenshots/`(dashboard.png / feed.png / sources.png / logs.png /
> settings.png,均为 demo 插件真实抓取数据)。
>
> 终态口径(2026-10-29 实核,取代正文的 10-04 快照):定名 myssia(中文名:世事)· 门面仓 `xinzhuzi/myia`。
> **PyPI 双包已上架**:`myssia` 0.0.16(0.0.1→0.0.16 共 14 版)、`myssia-classifier` 0.0.16(15 版),
> CLI 装法 `pip install myssia "selectolax<1.0"`(上界原因见门面 README 实测注)。
> GitHub Release 已到 **v0.0.17**,macOS dmg(9.6 MB)与 Windows msi(8.7 MB)同挂。
> 正文凡是「PyPI 未上架 / 别 pip install / Release 无 msi / dmg 117 MB / 源码安装仅 uv」的旧口径一律作废;
> 五项新能力当前安装包是否已含,对着 `CHANGELOG.md` 与装机实测核一遍再发。

## 标题候选(选一)

1. `开源 世事:一个 YAML 盯一类情报,AI 做抓取分类推送——v0.0.1 已发,带 macOS 桌面版(MIT)`
2. `世事:把「采集→分类→去重→打分→推送」拧成一条配置驱动的流水线,agent 自己写配置,桌面版已能日常用(MIT)`

## 正文

两年前起,我每多想盯一类情报(AI 资讯、美股异动、羊毛、显卡行情),就要把
同一套东西重新拼一遍:采集脚本、cron、diff 监控、去重、往聊天工具里推
webhook。直到把它拧成了一条流水线,索性开源——

**世事**(技术名 **myssia**:发行名/CLI/Python 模块名一律 `myssia`,GitHub
仓库是 `xinzhuzi/myia`;钥匙链等既有装机数据身份保留旧名 `myia/` 以兼容,
见下文第 5 点),AI 原生情报中枢,MIT,纯 Python(3.11+)+ SQLite 单文件,
核心依赖极轻(无 Redis、无 Postgres、无常驻守护):

```
fetch → classify → dedup → analyze → enrich → push
```

最新是 v0.0.17:macOS(Apple Silicon)dmg 与 Windows msi 同挂
GitHub Releases(装机实测待补)、Docker
镜像在 GHCR(可 pull 的 tag 以包页当日实际列出为准)、PyPI 双包已上架(当前 0.0.16)。桌面端自 v0.0.1 起可日常
使用。先讲设计,最后如实报告状态。

核心设计:

1. **品类即配置**:一类情报 = 一份 YAML(12 节 schema,每节有缺省值,
   只写必填项也能跑)。下面是零凭据可跑的最小示例:

```yaml
id: demo-min
name: 最小演示
schedule: "0 9 * * *"
sources:
  - name: example-news
    engine: static_html
    url: "https://example.com/news"
    extract:
      type: list
      item: "article"
      fields:
        title: "h2 a"
        url: "h2 a@href"
classify:
  builtin: false                 # 演示条目对不上七大类,关掉避免全被丢弃
push:
  - channel: stdout              # 零凭据本地验证
```

2. **AI 写 YAML**:内置 Agent Skill(skill/SKILL.md,自包含速查,一条命令装进
   Claude Code / Cursor)。你在编码 agent 里说「帮我盯着 XX」,agent 读 schema
   现场生成配置,`myssia test` 试抓验证、`myssia run --dry-run` 演练;源坏了
   `myssia doctor --json` 的结构化诊断就是给 agent 自修看的。所有命令都有
   `--json`,退出码契约 0/1/2/3,agent 友好是第一设计原则。

3. **六级采集降级链**:L1 direct_api → L2 静态页 → L3 crawl4ai(云端备胎
   firecrawl)→ L4 Scrapling → L5 反检测浏览器 → L6 LLM 浏览器。某级失败
   自动降级下一级,胜出引擎按源记忆(存 SQLite,永不回写你的 YAML);重引擎
   全是可选依赖,没装也不炸,结构化报 `dependency_missing` 沿链继续。

4. **情报语义,不只是采集**:七大类关键词粗筛(零 token,已拆成独立包
   myssia-classifier)+ 可选 LLM 精评(价值/相关性/可信度 0–10);阈值分级
   路由:≥8 立即推、≥5 进早晚双摘要(AM/PM 槽位防重发)、其余归档。URL 键
   去重注册表,同一条情报不会推第二遍。反馈闭环:CLI `myssia feedback mark`
   手动标记现已可用(负反馈自动回写调参:降权类目 / mute 词),Telegram/飞书
   回调接收已就绪,桌面卡片内按钮排后续批次。

5. **凭据零明文**:YAML 里凭据位只允许 `env:VAR` /
   `keychain:myia/<scope>/<name>` 引用(macOS Keychain / Windows DPAPI;
   钥匙链命名空间为历史稳定名 `myia/`(不改名以兼容已存凭据),与模块名 `myssia` 不同),
   出现明文 Cookie/Token 加载即拒;JSON 输出设计上不出凭据值。

6. **采集伦理**:默认尊重 robots.txt(qps 默认 0.5、jitter、429/5xx 指数退避
   都是缺省行为);「真人验证+手机号」类源直接结构化报错,不做绕过。

7. **下一批已在 main(随下个发布版交付,v0.0.1 安装包里没有)**:定时任务
   `myssia cron`(自然语言排程 `every monday 9am` 或 5 段 cron,三宿主共存,
   到点跑品类管线 + 运行摘要定向投递本地/飞书/Telegram)、告警规则(对每条
   新入流情报求值,命中即推送/打标,dry 测试不真发)、⌘K 命令面板、
   已读/星标/稍后状态服务端化(整库「全部已读」、换设备不丢)、feed 键盘流
   (j/k 导航、右键菜单、未读优先/分组切换、侧栏折叠)。

运行形态:CLI 常驻(`myssia run --loop`,APScheduler 进程内调度)、Docker
(v0.0.1 双平台镜像已上 GHCR;0.0.1 的 tag 因发布时仓库短暂更名落在
`ghcr.io/xinzhuzi/myia:0.0.1`,仓库已定名回 `myia`,后续版本在
`ghcr.io/xinzhuzi/myia`;仓库自带 compose 文件),或桌面应用——Tauri 2
壳,Python 核心以 sidecar 嵌入,五屏 UI(仪表盘/源/信息流/日志/设置)。装机
首跑自动种子官方插件,含一个零凭据 demo(GitHub 新星榜),第一次点「运行
第一个插件」就出真数据;设置页「检查更新」走签名更新通道。桌面截图见附图
(即仓库 docs/screenshots/)。

**安装**:

- 桌面(macOS Apple Silicon):从 https://zhuzibaijia.online/ 取 dmg(v0.0.17 实测 9.6 MB,
  以取包页实际资产为准)。安装包没做 Apple
  公证(公证要付费开发者账号)——代码全开源、每个包由 GitHub Actions 公开
  构建、日志可溯;首次打开右键 →「打开」放行 Gatekeeper,之后正常双击。
- CLI:`pip install myssia "selectolax<1.0"` → `myssia --version` 出 `myssia 0.0.16`
  (`selectolax<1.0` 上界暂时要带:selectolax 1.0 删了 Modest 解析器,不带上下任何命令
  都在 import 期 `ImportError`;全新 venv 实测,下个版本收进包内声明)。

**如实交底**(没做的事不吹):

- `selectolax<1.0` 上界还得手带一阵(原因见上),包内声明收拢前别只写 `pip install myssia`;
- Windows:v0.0.17 Release 已挂 msi(8.7 MB)+ 更新签名,但**装机实测未完成**——发贴前
  自己装一遍,没装过就只写「已出包、装机待验」;
- 桌面推送卡片里的反馈按钮还没随 v0.0.1 发出(已落地 main、随下个发布版
  交付;反馈闭环 CLI 现已可用);
- 安装包未公证(上面说了,右键打开)。

CI 全绿;测试零真实网络(全部录制回放)。门面 README 已摘除 CI 徽章(门面仓无
CI),别再引「徽章口径」;引擎侧全量 2026-10-29 记录 7058 过 / 0 挂,发贴前在源码树
`--collect-only` 复核。

链接:

- 仓库:https://github.com/xinzhuzi/myia
- 桌面取包(dmg + 签名更新通道):https://zhuzibaijia.online/
- 快速上手:https://github.com/xinzhuzi/myia/blob/main/docs/zh/getting-started.md
- 许可:MIT

求拍砖,尤其是 schema 设计与降级链这两块。你会先拿它盯什么?

## 发帖后动作

- 当天把帖子链接记进任务日志,启动 `docs/launch/README.md` 的首周反馈表
- 评论区的 bug 回报引导到 issue 模板;schema 建议单独开 issue 讨论
- 若发出后 PyPI 上架 / Windows 装机验证完成,回帖补更,别让「PyPI 未发」的
  老回复过期误导

---

> 2026-10-04 终态刷新,待主人定稿。
