# 即刻发帖草稿(中文,v1.1.1/1.1.2 开源发布)

> **状态:草稿,可直接定稿;发布节奏由主人定。** 即刻是短文案 + 图帖的节奏:
> 正文纯文本(不渲染 Markdown,星号井号会原样显示),配图走本地文件上传
> (外链图不渲染),链接放结尾一条 + 评论区置顶。话题标签以站内实际存在的
> 为准(如 #开源 #独立开发 #AI效率工具,发前搜索确认),勿堆 tag。
> 本稿口径已切到 v1.1.2 发布现实:`pip install shishi` 直装、Docker 镜像
> `ghcr.io/xinzhuzi/shishi`(v1.1.1 时代模板里「PyPI 未上、只能 uv 源码装」
> 的说法已过时,不再使用)。

## 文案(正文约 480 字,即刻上限够用;嫌长可拆两条——① 到「CLI」段为止,② 从「最想推的点」起到结尾)

折腾大半年,把自己的情报系统开源了:v1.1.1 定名「世事」正式发版,v1.1.2 跟进把安装门槛打平 🎉

「世事」,一个 AI 原生情报中枢:想盯的每类情报(AI 资讯 / 股票异动 / 羊毛 /
显卡行情……)就是一个 YAML 文件,抓取 → 分类 → 去重 → 打分 → 推送到
飞书 / Telegram,全自动。

桌面端可以日常用了:macOS(Apple Silicon)安装包在 Release 里,拖进应用程序
就能跑;装机自带官方插件,其中一个零凭据的 GitHub 新星榜演示件,第一次点
「运行」就出真数据,不用先填任何 key;设置页「检查更新」,验签后自动下载
安装新版本。丑话说在前面:安装包没做 Apple 公证,首次打开需要右键 → 打开。

爱命令行的走 CLI:v1.1.2 起 pip install shishi 直装,Python 里
import shishi 也通了;服务器长跑用 Docker 镜像 ghcr.io/xinzhuzi/shishi。

最想推的点还是:YAML 都不用自己写。把内置的 Agent Skill 装进 Claude Code
或 Cursor,说一句「帮我盯着 XX」,agent 照 12 节规范现场生成配置、试抓验证、
跑起来;源坏了,shishi doctor 的结构化诊断就是给 agent 自修看的。
说需求,AI 做其余。

几个认真做的地方:
- 六级采集降级梯:API 直连 → 静态页 → crawl4ai → … → LLM 浏览器,
  某级挂了自动降级,胜出引擎按源记忆
- 关键词粗筛零 token,LLM 精评可选;分数 ≥8 立即推,≥5 进早晚摘要
- 凭据零明文:只走环境变量 / 系统钥匙链,配置里出现明文 Cookie 直接拒载
- 默认尊重 robots.txt、限速礼貌;要过真人验证的源不碰

MIT,纯 Python + SQLite 单文件。求 Star、求拍砖 👇
https://github.com/xinzhuzi/shishi

## 发布要点

- 配图(全部本地文件上传,即刻不吃外链图):打头 demo 动图
  `docs/demo/assets/shishi-demo.gif`(README 同款,一个 YAML → 收到推送卡片);
  若动图上传后不播,退化为 `docs/screenshots/dashboard.png` +
  `feed.png` 两张静态图。后接五屏截图(均为 demo 插件真实抓取数据):
  `docs/screenshots/dashboard.png`、`feed.png`、`sources.png`、`logs.png`、
  `settings.png` —— 连动图共 6 张,即刻单帖图数上限以 App 实际为准。
- 评论区置顶(安装入口集中在这里,别塞正文):
  - Release 页:https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2
  - dmg 直链:https://github.com/xinzhuzi/shishi/releases/download/v1.1.2/shishi_1.1.2_aarch64.dmg
    (文件名按 v1.1.1 的 `shishi_1.1.1_aarch64.dmg` 命名规律推写,**发前以
    Release 页实际资产名为准**)
  - CLI 两行:
    `pip install shishi`
    `shishi --version   # shishi 1.1.2`
  - 上手走读:https://github.com/xinzhuzi/shishi/blob/main/docs/zh/getting-started.md
- 备答(全部如实口径,别替产品许愿):
  - 「和 RSSHub / changedetection.io 区别?」→ README「市面空白:为什么是
    世事」对比表,直接引。
  - 「有 Windows 吗?」→ 本版 Release 只有 macOS Apple Silicon 安装包,
    没有其他桌面平台的产物,不做任何相关宣称。
  - 「pip install 行吗?」→ 行,v1.1.2 起双包(`shishi` /
    `shishi-classifier`)已上 PyPI;重引擎是可选 extras
    (`pip install "shishi[crawl4ai]"`)。
  - 「桌面卡片里能标记有用/没用吗?」→ 卡片内按钮还在后续批次;反馈闭环
    现在 CLI(`shishi feedback mark`)就能用。
  - 「质量怎么保证?」→ 1300+ 测试跑在 CI,无一条碰真实网络。

## 附:评论区可贴的最小品类(「配置就这么大」的实证;与 README 快速开始同款)

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
