# 即刻首发文案(中文场景,未发出)

> **状态:草稿,发布节奏由主人定。** 即刻是短文案 + 动图的节奏,
> 配图建议用 demo 动图(已脱敏版);话题标签以站内实际存在的为准
> (如 #开源 #独立开发 #AI效率工具,发前搜索确认),勿堆 tag。

## 文案(约 300 字,分段发可拆两条)

折腾了大半年,把自己的情报系统开源了 🎉

**MYIA**,一个 AI 原生情报中枢:想盯的每类情报(AI 资讯 / 股票异动 /
羊毛 / 显卡行情……)就是一个 YAML 文件,抓取 → 分类 → 去重 → 打分 →
推送到飞书 / Telegram,全自动。

最想推的点:**YAML 都不用自己写**。把内置的 Agent Skill 装进 Claude Code
或 Cursor,说一句「帮我盯着 XX」,agent 照 12 节规范现场生成配置、试抓
验证、跑起来;源坏了,`myia doctor` 的结构化诊断就是给 agent 自修看的。
说需求,AI 做其余。

几个认真做的地方:
- 七层采集降级链:API 直连 → 静态页 → crawl4ai → … → LLM 浏览器,
  某层挂了自动降级,选中引擎按源记忆
- 七大类关键词粗筛零 token,LLM 精评再上,阈值分级路由(≥8 立即推 /
  ≥5 进早晚摘要)
- 凭据零明文:只走环境变量 / 系统钥匙链,配置里出现明文 Cookie 直接拒载
- 默认尊重 robots.txt、限速礼貌;要过真人验证的源不碰

MIT,纯 Python + SQLite 单文件,docker compose 也能跑。状态如实:
alpha,文档双语在仓库里。求 Star、求拍砖 👇
https://github.com/xinzhuzi/MYIA

## 发布要点

- 配图:demo 动图(docs/demo/,脱敏版),封面即「一个 YAML → 收到推送卡片」
- 评论区置顶补充:安装三行命令 + 快速上手链接
  (https://github.com/xinzhuzi/MYIA/blob/main/docs/zh/getting-started.md)
- 有人问「和 RSSHub/changedetection 区别」→ 市面空白对比表在 README「Why MYIA」节,直接引

## 附:评论区可贴的最小品类(「配置就这么大」的实证)

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
