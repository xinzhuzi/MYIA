# LinuxDo 首发文案(中文场景,未发出)

> **状态:草稿,发布节奏由主人定。** LinuxDo 偏好真诚分享 + 可复现的
> 自部署细节,反感营销腔;注意版规(发帖板块选「资源荟萃/前沿快讯」类
> 开源分享,以站内实际板块为准)与信任等级要求。社区对「白嫖/羊毛」
> 接受度高,但请把伦理边界讲在前面。

## 标题候选(选一)

1. `[开源] 世事:一个 YAML 盯一类情报的 AI 情报中枢,agent 写配置、坏了自修(MIT)`
2. `开源了自己用的情报中枢 世事:采集→分类→去重→推送全链,凭据全进钥匙链(MIT)`

## 正文

先说清楚这是什么:一个开源自托管情报中枢 **世事**(MIT,纯 Python 3.11+
SQLite 单文件)。所有「我想第一时间知道」的事——AI 资讯、股票异动、羊毛
线报、显卡行情、某种 key 泄漏——都是一份 YAML 配置,流水线自动做
采集 → 分类 → 去重 → 打分 → 推送(飞书卡片 / Telegram / webhook)。

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

论坛朋友可能关心的几点,展开说:

**1. 羊毛/线报场景是「原生」用法,不是衍生**
内置七大类关键词粗筛(含羊毛/优惠类,零 token)+ 免费/付费双信号裁决,
可选再叠一层 LLM 精评(价值/相关性/可信度 0–10);阈值分级路由:score≥8
立即推、≥5 进早晚双摘要(AM/PM 槽位保证同一条不重发)、<5 只归档。
负反馈点一下就回写调优(CLI `shishi feedback mark`;卡片内按钮随桌面版交付)。

**2. 反爬是分层降级,不是无脑硬刚**
采集链七层:auto 默认 L1 API 直连 → L2 静态页 → L3 crawl4ai(JS 渲染,
云端备胎 firecrawl)→ L4 Scrapling(隐身指纹)→ L5 反检测浏览器 →
L6 LLM 浏览器兜底。某层失败自动降级,选中的引擎按源回写记忆,下次直接走
能过的那层。重引擎全是可选依赖,没装也能跑,结构化报错沿链继续。

**3. 凭据安全是硬约束**
YAML 里凭据位只认 `env:VAR` / `keychain:shishi/<scope>/<name>` 引用
(macOS Keychain / Windows DPAPI),明文 Cookie/Token 启动即拒载;`shishi
secret set` 管录入,值不进 shell history(走 stdin)、不进日志、不进
`--json` 输出。源站 Cookie 这类敏感值全程只存在系统钥匙链里。

**4. 伦理边界讲在前面**
默认尊重 robots.txt,限速/退避是默认行为不是选项;「真人验证 + 手机号」
类源直接结构化报错拒采,不做绕过。README 和 FAQ 都有专节。

**5. 对 AI 友好是第一设计原则**
内置 Agent Skill(自包含速查),Claude Code / Cursor 说「帮我盯着 XX」
就能生成品类配置;所有命令带 `--json`,退出码契约 0/1/2/3,`shishi doctor
--json` 的 findings 就是给 agent 自修的行动清单。

安装(git clone + uv sync;根包依赖同仓子包 `shishi-classifier`,裸 pip
解析不到、装不了,PyPI 包发布后才可 pip):

```bash
git clone https://github.com/xinzhuzi/shishi
cd 世事
uv sync    # 源码装法仅此一条(workspace 依赖仅 uv 可解析)
```

- 仓库:https://github.com/xinzhuzi/shishi
- 中文快速上手:https://github.com/xinzhuzi/shishi/blob/main/docs/zh/getting-started.md
- 状态如实:alpha,CI 测试全绿(零真实网络),桌面端 spike 阶段

求反馈,尤其想听:你们想先盯什么品类?哪些源该进官方插件清单?

## 发帖后动作

- 链接记任务日志,进首周反馈表(docs/launch/README.md)
- 「求源」类回复归集成候选插件清单,开 issue 讨论
