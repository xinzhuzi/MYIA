# 视频《把信息源交给 AI:MYIA 两分半》— 分镜脚本(2:30)

> 定位:讲 AI-NATIVE 用法 —— **对 AI 说需求 → agent 写 YAML → 跑通 → 推送**。
> 画面与命令全部以 docs/demo 为素材(演示源为仓库自带虚构数据,仅
> 127.0.0.1,零凭据);字幕文件:`myia-demo.srt`(中英双语,时码与本脚本
> 一致)。拍摄与发布清单:`shot-list.md`。
>
> <!-- 成片外链占位(发布后回填):B站 ____________________ / YouTube ____________________ -->

## 节奏总表

| 镜 | 时码 | 画面 | 关键操作/素材 |
|----|------|------|----------------|
| S1 | 0:00–0:12 | 空镜:信息源散落(浏览器多标签/群消息快速闪切,可用仓库外自摄或素材库,注意许可) | 旁白点题:信息源太多,人盯不过来 |
| S2 | 0:12–0:35 | 录屏 A:coding agent 对话窗 | 对 AI 说需求;agent 跑 `myia init` 拿结构化信息清单 |
| S3 | 0:35–1:05 | 录屏 A→B:agent 写出 YAML | agent 产出 12 节 YAML(对照 docs/demo/demo-news.yaml) |
| S4 | 1:05–1:30 | 录屏 B:终端 `myia test` → `myia run` | 单源试抓 + 全链跑通,漏斗数字与 skip 原因可见 |
| S5 | 1:30–1:55 | 录屏 B:推送卡片 | stdout 卡片文本;旁白讲「换一行配置发飞书/TG」(飞书收卡镜头占位,拍时用测试群) |
| S6 | 1:55–2:15 | 录屏 B:`myia run` 第二次 + `myia list` | 增量语义:指纹跳过不打扰;健康度一目了然 |
| S7 | 2:15–2:30 | 片尾卡 | logo + 一句定位 + GitHub/docs 链接 |

## 分镜详解(解说词 = srt 内容,时码一致)

### S1 冷开场(0:00–0:12)

- 画面:三个窗口快速闪切(资讯网站、社群消息、RSS 阅读器),节奏快,最后全部模糊失焦。
- 解说(中):信息源越攒越多:官网、论坛、群、RSS。人盯不过来,爬虫脚本又难维护。
- 解说(英):Sources pile up — sites, forums, groups, RSS. You can't watch them all, and scraper scripts are painful to maintain.

### S2 对 AI 说需求(0:12–0:35)

- 画面:coding agent 对话窗(预打字,回车发出去)。
- 用户输入(打字或粘贴,建议原文):

  > 帮我盯几个 AI 实验室的动态和 HN 头条,每天早晚各推一次,大新闻及时叫醒我。

- agent 执行:`myia init` —— 终端显示单份 JSON(必填项/可选项/缺省值/硬规则)。
- 解说(中):在 MYIA,你不用写爬虫。对 AI 说一句需求;agent 跑 `myia init`,拿到机器可读的信息清单:要收集什么、哪些有缺省、哪些是硬规则。
- 解说(英):With MYIA you don't write scrapers. Tell your AI what you want; the agent runs `myia init` and gets a machine-readable checklist — required inputs, defaults, hard rules.

### S3 agent 写 YAML(0:35–1:05)

- 画面:agent 生成文件(可直接展示/微调 docs/demo/demo-news.yaml,镜头推近关键字段)。
- 特写顺序:sources(engine: auto 降级链注释)→ classify.builtin → dedup.key → push(stdout,route digest)。
- 解说(中):agent 据此生成 12 节 YAML:从哪抓、怎么礼貌地抓、怎么分类去重、推到哪里。凭据位置只允许写 `env:` 或 `keychain:` 引用 —— 明文直接拒载。本例把推送先指到 stdout:零凭据,先跑通。
- 解说(英):The agent turns it into a 12-section YAML: what to fetch, how to fetch politely, classify and dedup, where to push. Credential slots only accept `env:` or `keychain:` references — plaintext is rejected at load time. In this demo the push goes to stdout first: zero credentials, run it end to end.

### S4 跑通(1:05–1:30)

- 画面:终端(素材命令与预期画面 = gif/transcript/06-test.txt、03-run1.txt):

  ```console
  $ myia test docs/demo/demo-news.yaml --source demo-hub
  $ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db
  ```

- 解说(中):先单源试抓:提取字段、去重键,不推送不入库。然后整链跑一遍:抓到 7 条,1 条与 AI 无关被分类跳过 —— 跳过原因可见,不是黑盒。
- 解说(英):Test one source first: extracted fields and dedup keys, no push, no storage. Then run the full pipeline: 7 items fetched, 1 off-topic item visibly skipped by the classifier — skips are visible, never a black box.

### S5 推送(1:30–1:55)

- 画面:stdout 卡片(gif/transcript/04-card.txt);叠一个「飞书收卡」示意镜头(占位:拍摄时用真实测试群,把群名/头像打码,或用官方示例卡片图)。
- 解说(中):这就是推出去的卡片。想把同一份内容发进飞书或 Telegram:模板不动,通道换成 `feishu_card`,凭据用 `myia secret set` 放进系统钥匙链。
- 解说(英):This is the pushed card. To send the same content to Feishu or Telegram, keep the template, switch the channel to `feishu_card`, and store credentials with `myia secret set`.

### S6 增量与健康度(1:55–2:15)

- 画面:再跑一次(05-run2.txt:skip=not_modified)→ `myia list`(ok/健康度表)。
- 解说(中):再跑一次:内容没变,指纹直接跳过,不发打扰消息。每个源的健康度,`myia list` 一屏看完;出问题 `myia doctor` 给 agent 开结构化诊断。
- 解说(英):Run it again: nothing changed, the fingerprint skips it — no spam. Per-source health is one `myia list` away, and `myia doctor` gives your agent structured diagnostics.

### S7 收尾(2:15–2:30)

- 画面:片尾卡(与 GIF 片尾一致):MYIA · AI 帮你盯信息源;下方链接。
- 解说(中):MYIA:把信息源交给 AI。一个 YAML,一条管线,推送随取。GitHub 见简介。
- 解说(英):MYIA — hand your sources to the AI. One YAML, one pipeline, pushes on demand. GitHub link in the description.

## 制作备注

- 全片语速对齐 srt(每条 3–7 秒);录屏素材统一 2560×1600、30fps,导出 1080p。
- 终端字号 ≥20pt(视频分辨率下 GIF 素材的 15pt 偏小,重打一遍命令,别直接放大 GIF)。
- 打码红线:凭据、私人窗口、通知中心(开勿扰)、真实群名/头像。
- 素材与命令的「真实产物」都已在仓库:gif/transcript/*.txt(2026-10-02 真实执行)。
