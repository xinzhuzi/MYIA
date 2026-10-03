# 即刻发帖草稿(中文,v1.1.1/1.1.2 开源发布)

> **状态:草稿,正文可直接定稿;发布节奏由主人定,发帖前必须过文末「发帖前
> 硬 gate」。** 即刻是短文案 + 图帖的节奏:正文纯文本(不渲染 Markdown,
> 星号井号反引号都会原样显示),配图走本地文件上传(外链图不渲染),链接放
> 结尾一条 + 评论区置顶。话题标签以站内实际存在的为准(本稿末尾带了三个,
> 发前搜索确认),勿堆 tag。
> 本稿口径是 **v1.1.2 发布后的现实**(`pip install shishi` 直装、Docker 镜像
> `ghcr.io/xinzhuzi/shishi`)——截至本稿写就,仓库 README 仍是 1.1.1 + uv-only
> 口径、PyPI 双包与 v1.1.2 tag 均未上线,故硬 gate 里把这些列为发帖前置条件,
> 未满足前**不可发**。

## 文案(正文纯文本、无 Markdown;实测字符数见文末「发帖前硬 gate」末项;嫌长可拆两条——① 到「CLI」段为止,② 从「最想推的点」起到结尾)

这两年断断续续折腾,把自己的情报系统开源了:v1.1.1 定名「世事」正式发版,v1.1.2 把安装门槛打平 🎉

「世事」,一个 AI 原生情报中枢:想盯的每类情报(AI 资讯 / 股票异动 / 羊毛 /
显卡行情……)就是一个 YAML 文件,抓取 → 分类 → 去重 → 打分 → 推送到
飞书 / Telegram,全自动。

桌面端可以日常用了:macOS(Apple Silicon)安装包在 Release 里,拖进应用程序
就能跑;装机自带官方插件,其中一个零凭据的 GitHub 新星榜演示件,第一次点
「运行」就出真数据,不用先填任何 key;设置页「检查更新」,验签后自动下载
安装新版本。丑话说在前面:安装包没做 Apple 公证,首次打开需要右键 → 打开。

爱命令行的走 CLI:v1.1.2 起 pip install shishi 直装,Python 里
import shishi 也通了;服务器长跑用 Docker 镜像 ghcr.io/xinzhuzi/shishi。

最想推的点还是:YAML 都不用自己写。如果你在用 Claude Code 或 Cursor(两款
让 AI 替你干活的编程工具),把内置的「使用说明书」(Agent Skill)装进去,
说一句「帮我盯着 XX」,AI 就会照着 12 节规范现场把配置写出来、试抓验证、
跑起来;源坏了,shishi doctor 的诊断信息就是给它自己修看的。
说需求,AI 做其余。

几个认真做的地方:
- 六级采集降级梯:API 直连 → 静态页 → crawl4ai → … → LLM 浏览器,
  某级挂了自动降级,胜出引擎按源记忆
- 关键词粗筛零 token,LLM 精评可选;分数 ≥8 立即推,≥5 进早晚摘要
- 凭据零明文:只走环境变量 / 系统钥匙链,配置里出现明文 Cookie 直接拒载
- 默认尊重 robots.txt、限速礼貌;要过真人验证的源不碰

MIT,纯 Python + SQLite 单文件。求 Star、求拍砖 👇
https://github.com/xinzhuzi/shishi

#开源 #独立开发 #AI效率工具

## 发布要点

- 配图(全部本地文件上传,即刻不吃外链图):打头 demo 动图
  `docs/demo/assets/shishi-demo.gif`(README 同款,一个 YAML → 收到推送卡片);
  若动图上传后不播,退化为 `docs/screenshots/dashboard.png` +
  `feed.png` 两张静态图。后接桌面截图(均为 demo 插件真实抓取数据,发帖时
  按当版 UI 实际屏数增减):
  `docs/screenshots/dashboard.png`、`feed.png`、`sources.png`、`logs.png`、
  `settings.png` —— 连动图共 6 张,即刻单帖图数上限以 App 实际为准。
- 评论区置顶(安装入口集中在这里,别塞正文):
  - Release 页:https://github.com/xinzhuzi/shishi/releases/tag/v1.1.2
  - dmg 直链:**发帖当日从 Release 页复制资产 URL**(文件名按 v1.1.1 的
    `shishi_1.1.1_aarch64.dmg` 规律应形如 `shishi_1.1.2_aarch64.dmg`,但
    禁止手改版本号拼链,以页面为准)
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
  - 「质量怎么保证?」→ CI 2000+ 测试(发帖当日以 CI 实数回答),无一条
    碰真实网络。

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

## 发帖前硬 gate(未全部满足不发;写稿时点实测状态已注)

1. **v1.1.2 Release 已上线**(写稿实测 tag 404):dmg 等资产 URL 当日从
   Release 页复制粘贴。
2. **PyPI 双包已上架**(写稿实测 pypi.org/pypi/shishi/json 与
   /shishi-classifier/json 均 404):两个 JSON API 返回 200 再发。
3. **模块更名落地**:仓库源码已 `myia` → `shishi`,干净环境
   `python -c "import shishi"` 实测通过(写稿时入口仍是 `myia.cli:main`)。
4. **README 已同步切到 pip + 1.1.2 口径**(快速开始 / 下载安装段 / 版本号 /
   `shishi-classifier` 目录链接;写稿时 README 仍是 1.1.1 + uv-only)——否则
   读者点进仓库第一屏就与帖子矛盾。
5. **CHANGELOG [Unreleased] 已定版为 [1.1.2]**;顺带把 README 与 CHANGELOG
   关于桌面反馈按钮交付批次的口径对齐(两处现在一个写 v1.1.2 批次、一个写
   v1.2,读者对照会发现打架)。
6. **屏数按当版实际 UI 核对**:第六屏「消息」已在开发分支合入,若随 v1.1.2
   发布,配图与备答按实际增补。
7. **即刻正文重新数字数**:本稿写就时实测正文(「这两年断断续续」起至话题
   标签行止)936 字符(含空白)、去空白 812、其中 CJK+全角 452,即刻动态
   上限内;若再增删文案,发前用同口径重数,勿沿用旧数。
