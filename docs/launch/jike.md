# 即刻首发文案(中文场景,未发出)

> **状态:草稿,已对齐 2026-10-04 终态,发布节奏由主人定。** 即刻是短文案 +
> 图帖的节奏:正文纯文本(不渲染 Markdown,星号井号会原样显示),配图走本地
> 文件上传(外链图不渲染),链接放结尾一条 + 评论区置顶。话题标签以站内实际
> 存在的为准(如 #开源 #独立开发 #AI效率工具,发前搜索确认),勿堆 tag。
>
> 终态口径(2026-10-29 实核,取代正文的 10-04 快照):定名 myssia(中文名:世事)· 门面仓 `xinzhuzi/myia`。
> **PyPI 双包已上架**:`myssia` 0.0.16(0.0.1→0.0.16 共 14 版)、`myssia-classifier` 0.0.16(15 版),
> CLI 装法 `pip install myssia "selectolax<1.0"`(上界原因见门面 README 实测注)。
> GitHub Release 已到 **v0.0.17**,macOS dmg(9.6 MB)与 Windows msi(8.7 MB)同挂。
> 正文凡是「PyPI 未上架 / 别 pip install / Release 无 msi / dmg 117 MB / 源码安装仅 uv」的旧口径一律作废,
> 发贴前以门面 README「快速开始」与 `docs/zh/getting-started.md` 为准逐条复核。

## 文案(正文 1019 字符(含空白)/去空白 886/CJK+全角 501——2026-10-04 终态版实测,即刻上限够用;嫌长可拆两条——① 到「CLI」段为止,② 从「最想推的点」起到结尾)

折腾大半年,把自己的情报系统开源了,今天 v0.0.1 首发 🎉

「世事」(技术名 myssia,仓库 myia),一个 AI 原生情报中枢:想盯的每类情报
(AI 资讯 / 股票异动 / 羊毛 / 显卡行情……)就是一个 YAML 文件,抓取 → 分类
→ 去重 → 打分 → 推送到飞书 / Telegram,全自动。

从这版起,桌面端可以日常用了:macOS(Apple Silicon)安装包在 Release 里,
拖进应用程序就能跑;装机自带官方插件,其中一个零凭据的 GitHub 新星榜演示件,
第一次点「运行」就出真数据,不用先填任何 key;设置页「检查更新」走签名更新
通道。丑话说在前面:安装包没做 Apple 公证,首次打开需要右键 → 打开。

爱命令行的走 CLI:`pip install myssia "selectolax<1.0"` 就够(双包已在 PyPI 上架,当前
0.0.16;上界暂时要带——selectolax 1.0 删了旧解析器,下个版本收进包内声明);
服务器长跑用 Docker——镜像在 GHCR,能 pull 的 tag 以包页当日实际列出为准。

最想推的点还是:YAML 都不用自己写。把内置的 Agent Skill 装进 Claude Code
或 Cursor,说一句「帮我盯着 XX」,agent 照 12 节规范现场生成配置、试抓验证、
跑起来;源坏了,myssia doctor 的结构化诊断就是给 agent 自修看的。
说需求,AI 做其余。

几个认真做的地方:
- 六级采集降级梯:API 直连 → 静态页 → crawl4ai → … → LLM 浏览器,
  某级挂了自动降级,胜出引擎按源记忆
- 关键词粗筛零 token,LLM 精评可选;分数 ≥8 立即推,≥5 进早晚摘要
- 凭据零明文:只走环境变量 / 系统钥匙链,配置里出现明文 Cookie 直接拒载
- 默认尊重 robots.txt、限速礼貌;要过真人验证的源不碰

下一批已经写完、合在 main 上等下个版本(v0.0.1 安装包里还没有):定时任务
(一句自然语言排程,到点跑完把运行摘要推给你)、告警规则、⌘K 命令面板、
已读状态进服务端、feed 键盘流(j/k 导航、右键菜单)。

MIT,纯 Python + SQLite 单文件,docker compose 也能跑,双语文档在仓库里。
求 Star、求拍砖 👇
https://github.com/xinzhuzi/myia

## 发布要点

- 配图(全部本地文件上传,即刻不吃外链图):打头 demo 动图
  `docs/demo/assets/shishi-demo.gif`(README 同款,一个 YAML → 收到推送卡片);
  若动图上传后不播,退化为 `docs/screenshots/dashboard.png` +
  `feed.png` 两张静态图。后接五屏截图(均为 demo 插件真实抓取数据):
  `docs/screenshots/dashboard.png`、`feed.png`、`sources.png`、`logs.png`、
  `settings.png` —— 连动图共 6 张,即刻单帖图数上限以 App 实际为准。
- 评论区置顶(安装入口集中在这里,别塞正文):
  - 取包页:https://zhuzibaijia.online/
  - dmg 直链:**发帖当日从取包页复制实际资产 URL**(禁止手改文件名拼链,以页面为准)
  - Docker:`docker pull ghcr.io/xinzhuzi/myia:0.0.1`(0.0.1 的镜像 tag 落在
    发布时的旧仓名下;仓库已定名回 myia,后续版本镜像在
    `ghcr.io/xinzhuzi/myia`)
  - CLI 三行:
    `pip install myssia "selectolax<1.0"`
    `myssia --version   # 0.0.16 = PyPI 当前版`
    `myssia test <品类>.yaml --json`(品类怎么写见上手走读)
  - 上手走读:https://github.com/xinzhuzi/myia/blob/main/docs/zh/getting-started.md
- 备答(全部如实口径,别替产品许愿):
  - 「和 RSSHub / changedetection.io 区别?」→ README「市面空白:为什么是
    世事」对比表,直接引(表格在远端 main 已存在)。
  - 「有 Windows 吗?」→ v0.0.17 Release 已挂 Windows msi(8.7 MB,带 `.sig`);如实补一句
    装机实测还没跑完——自己没装过之前不说「Windows 已验证」。
  - 「pip install 行吗?」→ 能,双包都在 PyPI(`myssia` 0.0.16 / `myssia-classifier` 0.0.16,
    2026-10-29 实核);命令给全 `pip install myssia "selectolax<1.0"`,上界暂需手带
    (原因见门面 README 实测注)。
  - 「桌面卡片里能标记有用/没用吗?」→ 卡片内按钮已随桌面对齐批次落地
    main、随下个发布版交付;反馈闭环现在 CLI 就能用。
  - 「质量怎么保证?」→ 测试全在 CI 跑、零真实网络(2026-10-29 引擎侧全量记录
    7058 过 / 0 挂;门面 README 已摘除 CI 徽章,别再说「徽章口径」)。

## 附:评论区可贴的最小品类(「配置就这么大」的实证;已过真实 schema 入口加载验证,与 README 快速开始同款)

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

---

> 2026-10-04 终态刷新,待主人定稿。
