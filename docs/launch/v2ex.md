# V2EX 首发文案(中文场景,未发出)

> **状态:草稿,发布节奏由主人定。** V2EX 偏好第一手「分享创造」复盘,
> 技术细节 > 营销话术;建议节点:分享创造(/go/create)或 Python(/go/python),
> 以站点实际节点为准。回复区保持真诚答疑,勿自顶刷屏。

## 标题候选(选一)

1. `开源了一个 AI 原生情报中枢 MYIA:一个 YAML 盯一类情报,说需求,AI 做其余(MIT)`
2. `MYIA:把「爬虫 + 分类 + 去重 + 推送」拧成一条流水线,品类即配置,agent 写配置(MIT 开源)`

## 正文

两年前起,我每多想盯一类情报(AI 资讯、美股异动、羊毛、显卡行情),就要把
同一套东西重新拼一遍:采集脚本、cron、diff 监控、去重、往聊天工具里推
webhook。直到把它拧成了一条流水线,索性开源——

**MYIA**,AI 原生情报中枢,MIT,纯 Python(3.11+)+ SQLite 单文件,
核心 `pip install` 级轻依赖:

```
fetch → classify → dedup → analyze → enrich → push
```

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

2. **AI 写 YAML**:内置 Agent Skill(skill/SKILL.md,自包含速查),Claude
   Code / Cursor 里说「帮我盯着 XX」,agent 读 schema 现场生成配置,用
   `myia test` 试抓验证、`myia run --dry-run` 演练;源坏了 `myia doctor
   --json` 的结构化诊断就是给 agent 自修看的。所有命令都有 `--json`,
   退出码契约 0/1/2/3,agent 友好是第一设计原则。

3. **七层采集降级链**:L1 API 直连 → L2 静态页 → L3 crawl4ai(云端备胎
   firecrawl)→ L4 Scrapling → L5 反检测浏览器 → L6 LLM 浏览器。某层失败
   自动降级下一层,选中引擎按源回写 SQLite 记忆;重引擎全是可选依赖,
   没装也不炸,结构化报 `dependency_missing` 沿链继续。

4. **情报语义**:七大类关键词粗筛(零 token,已拆成独立包
   myia-classifier)+ 可选 LLM 精评(价值/相关性/可信度 0–10);阈值分级
   路由:≥8 立即推、≥5 进早晚双摘要(AM/PM 槽位防重发)、<5 只归档。
   负反馈回写持续调优:CLI `myia feedback mark` 手动标记,
   Telegram/飞书回调接收已就绪(卡片内按钮随桌面版交付)。

5. **凭据零明文**:YAML 里凭据位只允许 `env:VAR` /
   `keychain:myia/<scope>/<name>` 引用(macOS Keychain / Windows DPAPI),
   出现明文 Cookie/Token 启动即拒载;日志与 JSON 输出只出现凭据名,
   永不出现值。

6. **采集伦理**:默认尊重 robots.txt(qps/jitter/backoff 是默认行为);
   「真人验证+手机号」类源直接结构化报错,不做绕过。

运行形态:CLI 常驻(`myia run --loop`,APScheduler 进程内调度)或自带
docker compose;推送通道:飞书卡片 / Telegram / webhook / stdout。数据
单文件 SQLite,按保留期自动清理 + VACUUM。

如实汇报状态:alpha。核心流水线已实现,CI 测试全绿(测试零真实网络,
录制回放),桌面端只有 spike,坑肯定还有。文档双语在仓库里:

- 仓库:https://github.com/xinzhuzi/MYIA
- 快速上手:https://github.com/xinzhuzi/MYIA/blob/main/docs/zh/getting-started.md
- 许可:MIT

求拍砖,尤其是 schema 设计与降级链这两块。你会先拿它盯什么?

## 发帖后动作

- 当天把帖子链接记进任务日志,启动 `docs/launch/README.md` 的首周反馈表
- 评论区的 bug 回报引导到 issue 模板;schema 建议单独开 issue 讨论
