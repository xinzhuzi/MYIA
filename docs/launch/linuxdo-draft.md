# LinuxDo 发帖草稿(中文,v1.1.1/1.1.2 开源发布)

> **状态:草稿,可直接定稿;发布节奏由主人定。** LinuxDo 偏好真诚分享 +
> 可复现的自部署细节,反感营销腔;注意版规(发帖板块选「资源荟萃/前沿快讯」
> 类开源分享,以站内实际板块为准)与信任等级要求。社区对「白嫖/羊毛」接受度
> 高,但请把伦理边界讲在前面。
> 发帖时:五张截图一律用论坛附件上传(本地路径见正文截图占位清单;正文里
> 不要留任何图片外链——Discourse 外链图可能不渲染,且易被视为引流);
> 代码块用 ```yaml / ```bash 高亮。
> 本稿口径已切到 v1.1.2 发布现实:`pip install shishi` 直装、Docker 镜像
> `ghcr.io/xinzhuzi/shishi`(v1.1.1 时代模板里「PyPI 未上、只能 uv 源码装」
> 的说法已过时,不再使用)。

## 标题候选(选一)

1. `[开源] 世事 v1.1.2:一个 YAML 盯一类情报,采集→分类→去重→推送全链;桌面 dmg 即装,pip 直装,MIT`
2. `开源了自己用的情报中枢「世事」:agent 写配置、坏了自修,凭据全进钥匙链;v1.1.1 桌面版日常可用,v1.1.2 上 PyPI`

## 正文

先说清楚这是什么:一个开源自托管情报中枢 **世事**(MIT,纯 Python 3.11+,
SQLite 单文件,无守护进程)。所有「我想第一时间知道」的事——AI 资讯、股票
异动、羊毛线报、显卡行情——都是一份 YAML 配置,流水线自动做
采集 → 分类 → 去重 → 打分 → 推送(飞书卡片 / Telegram / webhook / stdout)。

**v1.1.1 定名「世事」正式开源**(桌面数据通路统一、开箱 demo、签名更新通道,
桌面端从这版起可日常使用);**v1.1.2 刚把 PyPI 双包发了上去**——
`pip install shishi` 直装,Python 模块名也同步从 `myia` 更名为 `shishi`
(`import shishi` 可用)。桌面、pip、Docker 三条安装路都给,任选。

### 桌面端(不想碰命令行的走这条)

下载:`shishi_1.1.2_aarch64.dmg` →
https://github.com/xinzhuzi/shishi/releases/download/v1.1.2/shishi_1.1.2_aarch64.dmg
(Release 页:https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2)

丑话说在前面:

- **没做 Apple 公证**(公证要付费开发者账号)。首次打开需要在「应用程序」里
  **右键 世事 →「打开」→ 再点「打开」**(或双击被拦后到 系统设置 → 隐私与
  安全性 → 点「仍要打开」),过一次 Gatekeeper 之后正常双击。
- 安装包由 GitHub Actions 公开构建、日志可溯,代码全开源可审计——信不过就
  别装,或者走下面 CLI 路线自己跑。
- 装机首跑自动种子官方插件(含一个零凭据的 GitHub 新星榜演示件,免凭据
  JSON 单请求),第一次点「运行第一个插件」就出真数据,不用先填任何 key。
- 设置页有「检查更新」,走签名更新的通道(Release 附带 `.sig` 签名与
  `latest.json`,更新包验签后自动下载安装,再重启)。
- 本版 Release 只有 macOS(Apple Silicon)安装包,没有其他桌面平台的产物。

桌面五屏,截图就是 demo 插件真实抓取的数据,不是摆拍(发帖时把下面五张
本地截图用论坛附件上传、按此顺序插入,说明文字可带走):

1. 仪表盘——插件运行状态与最近采集概览(`docs/screenshots/dashboard.png`)
2. 信息流——去重后的情报条目(`docs/screenshots/feed.png`)
3. 源管理——插件与源配置(`docs/screenshots/sources.png`)
4. 日志——运行日志回溯(`docs/screenshots/logs.png`)
5. 设置——凭据入钥匙链 + 软件更新(`docs/screenshots/settings.png`)

### CLI(论坛朋友大概率更想看这条)

```bash
pip install shishi              # v1.1.2 起 PyPI 直装;命令行 shishi 开箱即用
shishi --version                # shishi 1.1.2
```

重引擎是可选 extras,按需叠加(没装也能跑,沿降级梯结构化报
`dependency_missing`,不崩):

```bash
pip install "shishi[crawl4ai]"  # L3 JS 渲染引擎
pip install "shishi[llm]"       # LLM 精评 / 事件聚合
```

从源码跑 / 参与开发仍走 uv(本仓是 uv workspace,`shishi-classifier`
是 workspace 成员):`git clone` + `uv sync`。

服务器长跑可用 Docker:仓库自带
[docker compose](https://github.com/xinzhuzi/shishi/blob/main/docker/docker-compose.yml),
镜像在 `ghcr.io/xinzhuzi/shishi`(CI 对 main 与 `v*` tag 自动构建发布)。

最小品类长这样,零凭据、复制就能跑(`url` 换成任意服务端渲染的列表页):

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

三个命令走完「验证 → 演练 → 正式」:`shishi test --json`(逐源试抓,不入库
不推送)、`shishi run --dry-run --json`(全链演练)、`shishi run`(正式跑,
`--loop` 常驻调度)。

论坛朋友可能关心的几点,展开说:

**1. 羊毛/线报场景是「原生」用法,不是衍生**
内置七大类关键词粗筛(含羊毛/优惠类,零 token)+ 免费/付费双信号裁决,
可选再叠一层 LLM 精评(价值/相关性/可信度 0–10);阈值分级路由:score≥8
立即推、≥5 进早晚双摘要(AM/PM 槽位保证同一条不重发)、<5 只归档。
负反馈现在就能闭环:CLI `shishi feedback mark` 回写调优,Telegram/飞书回调
接收也已就绪;桌面卡片内按钮还在后续批次,不画饼。

**2. 反爬是六级降级梯,不是无脑硬刚**
L1 API 直连 → L2 静态页 → L3 crawl4ai(JS 渲染,云端备胎 firecrawl)→
L4 Scrapling(隐身指纹)→ L5 反检测浏览器 → L6 LLM 浏览器兜底。某级失败
自动降级,胜出的引擎按源回写记忆(存 SQLite,不回写你的 YAML),下次直接
走能过的那级。重引擎全是可选依赖,没装也能跑。

**3. 凭据安全是硬约束**
YAML 里凭据位只认 `env:VAR` / `keychain:` 引用(macOS Keychain /
Windows DPAPI),明文 Cookie/Token 启动即拒载;`shishi secret set` 管录入,
值走 stdin 不进 shell history、不进日志、不进 `--json` 输出。源站 Cookie
这类敏感值全程只存在系统钥匙串里。

**4. 伦理边界讲在前面**
默认尊重 robots.txt,限速/退避是默认行为不是选项;「真人验证 + 手机号」
类源直接结构化报错拒采,不做绕过。README 和 FAQ 都有专节,这是红线不是
卖点修饰。

**5. 对 AI 友好是第一设计原则**
内置 Agent Skill(自包含速查,`shishi skill install --agent claude` 一条命令
装进 Claude Code,也支持 Cursor / zcode),说「帮我盯着 XX」就能生成品类
配置;所有命令带 `--json`(stdout 恒为恰好一份 JSON 文档),退出码契约
0/1/2/3,`shishi doctor --json` 的 findings 就是给 agent 自修的行动清单。

### 状态如实

- CI 1300+ 测试全绿,无一条碰真实网络(全部录制回放);
- macOS 桌面端 v1.1.1 起日常可用;
- PyPI 双包(`shishi` / `shishi-classifier`)v1.1.2 起已上架,
  `pip install shishi` 即装;
- 本版 Release 只有 macOS(Apple Silicon)安装包,没有其他桌面平台产物,
  这里不做任何相关宣称;
- 桌面卡片内反馈按钮还在后续批次(反馈闭环 CLI + 回调接收现已可用);
- 安装包未做 Apple 公证(上面说了,右键打开)。

链接:

- 仓库:https://github.com/xinzhuzi/shishi
- 中文快速上手:https://github.com/xinzhuzi/shishi/blob/main/docs/zh/getting-started.md
- v1.1.2 Release:https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2

求反馈,尤其想听:你们想先盯什么品类?哪些源该进官方插件清单?桌面端
macOS 的打开体验有没有被 Gatekeeper 恶心到(除了右键打开还有什么顺手的
写法,评论区教教我)?

## 发帖前核对

- dmg 直链文件名 `shishi_1.1.2_aarch64.dmg` 按 v1.1.1 的
  `shishi_1.1.1_aarch64.dmg` 命名规律推写,**发前以 Release 页实际资产名为准**;
- 正文凭据段写的是 `keychain:` 引用形态,未展开命名空间前缀——发前与
  当版 `docs/zh/getting-started.md` 的写法对齐(模块已更名 `shishi`,
  钥匙链命名空间以当版文档为准);
- v1.1.2 tag 与 PyPI 页面(pypi.org/project/shishi)确认可访问后再发。

## 发帖后动作

- 链接记任务日志,进首周反馈表(docs/launch/README.md)
- 「求源」类回复归集成候选插件清单,开 issue 讨论
- LinuxDo 反馈里的采集伦理问题优先回应(launch/README.md 的处理原则)
