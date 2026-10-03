# V2EX 发帖草稿(中文,v1.1.1/1.1.2 开源发布)

> **状态:草稿,正文可直接定稿;发布节奏由主人定,发帖前必须过文末「发帖前
> 硬 gate」。** V2EX 偏好第一手「分享创造」复盘,技术细节 > 营销话术;建议
> 节点:分享创造(/go/create)或 Python(/go/python),以站点实际节点为准。
> 回复区保持真诚答疑,勿自顶刷屏。
>
> **附图说明**:V2EX 正文不渲染外链图片,截图须在发帖编辑器里作为附件上传。
> 建议 dashboard / feed / settings 三张(最多全五张),文件在仓库
> `docs/screenshots/`(dashboard.png / feed.png / sources.png / logs.png /
> settings.png,均为 demo 插件真实抓取数据;若当版 UI 已有新屏,按实际增补)。
>
> 本稿口径是 **v1.1.2 发布后的现实**(`pip install shishi` 直装、Docker 镜像
> `ghcr.io/xinzhuzi/shishi`)——截至本稿写就,仓库 README 仍是 1.1.1 +
> uv-only 口径、PyPI 双包与 v1.1.2 tag 均未上线,故硬 gate 里把这些列为发帖
> 前置条件,未满足前**不可发**。

## 标题候选(选一)

1. `开源 世事:一个 YAML 盯一类情报,AI 做抓取分类推送——v1.1.2 起上 PyPI,带 macOS 桌面版(MIT)`
2. `世事:把「采集→分类→去重→打分→推送」拧成一条配置驱动的流水线,agent 自己写配置,pip 直装 + 桌面 dmg(MIT)`

## 正文

这两年,我每多想盯一类情报(AI 资讯、美股异动、羊毛、显卡行情),就要把
同一套东西重新拼一遍:采集脚本、cron、diff 监控、去重、往聊天工具里推
webhook。直到把它拧成了一条流水线,索性开源——

**世事**,AI 原生情报中枢,MIT,纯 Python(3.11+)+ SQLite 单文件,核心依赖
极轻(无 Redis、无 Postgres、无常驻守护):

```
fetch → classify → dedup → analyze → enrich → push
```

v1.1.1(2026-10-03)定名「世事」正式开源:macOS(Apple Silicon)安装包上了
GitHub Releases,桌面端自这个版本起可日常使用;v1.1.2 起双包上了 PyPI——
`pip install shishi` 直装,Python 模块名同步从 `myia` 更名为 `shishi`
(`import shishi` 可用)。先讲设计,最后如实报告状态。

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
   现场生成配置,`shishi test` 试抓验证、`shishi run --dry-run` 演练;源坏了
   `shishi doctor --json` 的结构化诊断就是给 agent 自修看的。所有命令都有
   `--json`,退出码契约 0/1/2/3,agent 友好是第一设计原则。

3. **六级采集降级链**:L1 direct_api → L2 静态页 → L3 crawl4ai(云端备胎
   firecrawl)→ L4 Scrapling → L5 反检测浏览器 → L6 LLM 浏览器。某级失败
   自动降级下一级,胜出引擎按源记忆(存 SQLite,永不回写你的 YAML);重引擎
   全是可选依赖,没装也不炸,结构化报 `dependency_missing` 沿链继续。

4. **情报语义,不只是采集**:七大类关键词粗筛(零 token,以独立包
   `shishi-classifier` 发行,v1.1.2 起随主包上 PyPI)+ 可选 LLM 精评
   (价值/相关性/可信度 0–10);阈值分级路由:≥8 立即推、≥5 进早晚双摘要
   (AM/PM 槽位防重发)、其余归档。URL 键去重注册表,同一 URL 不会推第二遍。
   反馈闭环:CLI `shishi feedback mark` 手动标记现已可用(负反馈自动回写
   调参:降权类目 / mute 词),Telegram/飞书回调接收已就绪,桌面卡片内按钮
   还在后续批次。

5. **凭据零明文**:YAML 里凭据位只允许 `env:VAR` / `keychain:` 引用
   (落 macOS Keychain / Windows DPAPI),出现明文 Cookie/Token 加载即拒;
   JSON 输出设计上不出凭据值。

6. **采集伦理**:默认尊重 robots.txt(qps 默认 0.5、429/5xx 指数退避都是
   缺省行为);「真人验证+手机号」类源直接结构化报错,不做绕过。

运行形态三选:CLI 常驻(`shishi run --loop`,APScheduler 进程内调度)、
Docker(仓库自带 compose 文件,镜像 `ghcr.io/xinzhuzi/shishi`,CI 对 main
与 `v*` tag 自动构建),或桌面应用——Tauri 2 壳,Python 核心以 sidecar
嵌入,多屏 UI(仪表盘/源/信息流/日志/设置)。装机首跑自动种子官方插件,
含一个零凭据 demo(GitHub 新星榜),第一次点「运行第一个插件」就出真数据;
设置页「检查更新」走签名更新通道,验签后自动下载安装。桌面截图见附图
(即仓库 docs/screenshots/)。

**安装**:

- CLI:`pip install shishi`(v1.1.2 起 PyPI 直装;`shishi --version` →
  `shishi 1.1.2`)。重引擎可选:`pip install "shishi[crawl4ai]"` /
  `"shishi[llm]"`。源码开发走 uv workspace:`git clone` + `uv sync`。
- 桌面(macOS Apple Silicon):Releases 下载 dmg(文件名按 v1.1.1 的
  `shishi_1.1.1_aarch64.dmg` 规律应形如 `shishi_1.1.2_aarch64.dmg`,发帖
  当日从 Release 页复制实际资产直链)。安装包没做 Apple 公证(公证要付费
  开发者账号)——代码全开源、每个包由 GitHub Actions 公开构建、日志可溯;
  首次打开在「应用程序」里右键 世事 →「打开」→ 再点「打开」(或双击被拦后
  到 系统设置 → 隐私与安全性 → 点「仍要打开」),之后正常双击。

**如实交底**(没做的事不吹):

- PyPI 双包 v1.1.2 起已上架(`shishi` / `shishi-classifier`),
  `pip install shishi` 即装;uv 源码走法仍适用于开发;
- 本版 Release 只有 macOS(Apple Silicon)安装包,没有其他桌面平台产物;
- 桌面推送卡片里的反馈按钮还没做,在后续批次(反馈闭环 CLI + 回调接收
  现已可用);
- 安装包未公证(上面说了,右键打开)。

CI 全绿;2000+ 测试零真实网络(全部录制回放;公开可查:
https://github.com/xinzhuzi/shishi/actions/workflows/ci.yml )。

链接:

- 仓库:https://github.com/xinzhuzi/shishi
- Release v1.1.2(dmg + 签名更新通道):https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2
- 快速上手:https://github.com/xinzhuzi/shishi/blob/main/docs/zh/getting-started.md
- 许可:MIT

求拍砖,尤其是 schema 设计与降级链这两块。你会先拿它盯什么?

## 发帖前硬 gate(未全部满足不发;写稿时点实测状态已注)

1. **v1.1.2 Release 已上线**(写稿实测 tag 404):dmg 等资产 URL 当日从
   Release 页复制粘贴,禁止手改版本号拼链。
2. **PyPI 双包已上架**(写稿实测 pypi.org/pypi/shishi/json 与
   /shishi-classifier/json 均 404):两个 JSON API 返回 200 再发。
3. **模块更名落地**:仓库源码已 `myia` → `shishi`,干净环境
   `python -c "import shishi"` 实测通过(写稿时入口仍是 `myia.cli:main`)。
4. **README 已同步切到 pip + 1.1.2 口径**(快速开始 / 下载安装段 / 版本号 /
   `shishi-classifier` 目录链接——写稿时 README:76 的 `shishi-classifier/`
   链接指向的目录还叫 `myia-classifier/`,是死链,随更名一并修)——否则读者
   点进仓库第一屏就与帖子矛盾。
5. **CHANGELOG [Unreleased] 已定版为 [1.1.2]**;顺带把 README 与 CHANGELOG
   关于桌面反馈按钮交付批次的口径对齐(两处现在一个写 v1.1.2 批次、一个写
   v1.2,读者对照会发现打架)。
6. **屏数与截图按当版实际 UI 核对**:第六屏「消息」已在开发分支合入,若随
   v1.1.2 发布,正文「多屏 UI」列举与附图按实际增补。
7. **CI 测试数以当日实数刷新**:写稿时本地 `pytest --collect-only` 实测
   2159 条,正文用「2000+」;CI 实跑数若不同,以 CI 为准。

## 发帖后动作

- 当天把帖子链接记进任务日志,启动 `docs/launch/README.md` 的首周反馈表
- 评论区的 bug 回报引导到 issue 模板;schema 建议单独开 issue 讨论
- 若发出后桌面端有新版本 / 新平台产物,回帖补更,别让旧回复过期误导
