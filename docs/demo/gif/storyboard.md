# GIF 分镜脚本:「一个 YAML → 情报推送」3 分钟上手动图

> 本分镜的所有「预期画面」文本与 `transcript/*.txt` 一一对应 —— **全部来自
> 2026-10-02 在本机真实执行的命令输出**(演示源为仓库自带的虚构数据页,仅
> 监听 127.0.0.1,零凭据零外网)。开发环境下命令以 `uv run --no-sync myia …`
> 执行;分镜画面为阅读友好写作 `myia …`(等价,安装后即可直接用)。
>
> 产物:`assets/myia-demo.gif`(<5MB)。重录/重渲染见本目录 `build_gif.sh`
> 与 `../README.md` 的「动图制作与重录」一节。

## 素材事实(真实执行记录)

| # | 命令(实际执行) | 退出码 | transcript |
|---|------------------|--------|------------|
| 1 | `python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/demo/assets/demo-site` | 0 | `02-http-server.txt` |
| 2 | `uv run --no-sync myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db`(空库首轮) | 0 | `03-run1.txt` |
| 3 | `grep '^{"channel"' run1.out \| jq -r .text`(提取本轮推送卡片文本) | 0 | `04-card.txt` |
| 4 | 同 2(同库第二轮,内容未变) | 0 | `05-run2.txt` |
| 5 | `uv run --no-sync myia test docs/demo/demo-news.yaml --source demo-hub` | 0 | `06-test.txt` |

## 逐帧分镜(总时长 42.5s @12fps,画布 1100×640,GitHub 深色终端风)

### S1 · 片头(0.0s–3.0s)

- 画面:`MYIA` 大字;副题「一个 YAML → 情报推送 · 3 分钟上手」;小字「AI-NATIVE 信息情报管线 · 本演示零凭据零外网」。

### S2 · 展示品类 YAML(3.0s–9.0s)

- 命令行:`$ cat docs/demo/demo-news.yaml`
- 预期画面(真实文件节选,保留原始行文;省略处标注 `# …(12 节完整定义见 docs/demo/demo-news.yaml)`):

```yaml
id: demo-news
name: 演示情报
schedule: "0 9,21 * * *"          # 12 节之 schedule/timezone
timezone: Asia/Shanghai
sources:
  - name: demo-hub                # 12 节之 sources[]
    engine: static_html           # 真实源可留 auto:L1→L2→L3→firecrawl 自动降级
    url: "http://127.0.0.1:8765/"
    extract:
      type: list
      item: "article.post"
      fields:
        title: "h3"
        url: "h3 a@href"
# …(watchlist / enrich / storage 等节见原文件)
classify:
  builtin: true                    # 第一层漏斗:七大类关键词,零 token
dedup:
  key: "{url}"                     # 永不标题指纹
push:
  - channel: stdout                # 演示通道:零凭据;换 feishu_card 即发飞书群
    route:
      - when: "category in ['ai-news']"
        mode: digest
```

### S3 · 起演示源(9.0s–12.0s)

- 命令行:`$ python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/demo/assets/demo-site`
- 预期画面(真实输出,见 `02-http-server.txt`):

```text
Serving HTTP on 127.0.0.1 port 8765 (http://127.0.0.1:8765/) ...
```

- 灰色注释:`# 演示源 = 仓库自带的虚构数据页,仅监听本机回环地址`

### S4 · 首轮 run(12.0s–21.0s)

- 命令行:`$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db`
- 预期画面(真实输出,见 `03-run1.txt` 人读摘要;stdout 的 JSON 卡片行另见 S5):

```text
MYIA run:demo-news(演示情报) run_id=1
  - fetch:ok | 1→7 条
  - classify:ok | 7→6 条 | 跳过 classify_unmatched×1
  - dedup:ok | 6→6 条
  - analyze:ok | 6→6 条
  - push:ok | 6→6 条
  源 demo-hub(成功,engine=static_html,条目 7)
  推送 stdout:immediate=0 digest=6 archive=0,发送成功
状态:success
```

- 灰色注释:`# 每层漏斗可见:7 条抓到,1 条与 AI 无关被分类跳过(原因可见)`

### S5 · 收到推送卡片(21.0s–27.5s)

- 命令行(两行,均为真实可复现命令):

```console
$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db > run.out
$ grep '^{"channel"' run.out | jq -r .text
```

- 预期画面(真实卡片文本,见 `04-card.txt`;stdout 通道一条 JSON,`text` 字段即渲染后的卡片,与飞书卡片同一模板同一内容):

```text
**演示情报 · 2026-10-02**(channel=stdout,换 feishu_card 即发飞书群)
- [「萤火」开源大模型正式发布:70B 参数,中文评测登顶](http://127.0.0.1:8765/articles/2026/huguang-70b.html)
- [智能体框架 MyiaFlow v2.0 上线:多智能体协作与工具编排](http://127.0.0.1:8765/articles/2026/myiaflow-v2.html)
- [「湖光」推理引擎更新:7B 模型可离线跑在手机端](http://127.0.0.1:8765/articles/2026/huguang-infer.html)
- [万象实验室多模态接口上线:图像理解能力正式商用](http://127.0.0.1:8765/articles/2026/wanxiang-multimodal.html)
- [「星辰」发布量化工具链:消费级显卡可微调 30B 模型](http://127.0.0.1:8765/articles/2026/star-quant.html)
- [湖光云上线智能体托管服务:按调用量计费,支持私有化部署](http://127.0.0.1:8765/articles/2026/lakeview-agent.html)
```

### S6 · 再跑一次:增量语义(27.5s–33.0s)

- 命令行:`$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db`
- 预期画面(真实输出,见 `05-run2.txt`):

```text
MYIA run:demo-news(演示情报) run_id=2
  - fetch:ok | 1→0 条
  源 demo-hub(跳过,engine=static_html,条目 0,skip=not_modified)
  推送 stdout:immediate=0 digest=0 archive=0,发送成功
状态:success
```

- 灰色注释:`# 内容没变 → 变更指纹跳过;同一 URL → dedup 拦截:不打扰`

### S7 · 单源试抓(33.0s–38.5s)

- 命令行:`$ myia test docs/demo/demo-news.yaml --source demo-hub`
- 预期画面(真实输出节选,见 `06-test.txt`):

```text
MYIA test:docs/demo/demo-news.yaml(试抓不入库不推送)
  源 demo-hub(成功,engine=static_html,条目 7)
    指纹:内容有变化或首次抓取,线上调度会正常提取
    条目 dedup_key=http://127.0.0.1:8765/articles/2026/huguang-70b.html fields={"title": "「萤火」开源大模型正式发布:70B 参数,中文评测登顶", "url": …}
状态:success
```

- 灰色注释:`# 改 YAML 后先 test 单源:提取字段与去重键预览,不推送不入库`

### S8 · 片尾(38.5s–42.5s)

- 画面:`MYIA · AI 帮你盯信息源`;副题「对 AI 说需求 → agent 写 YAML → 跑通 → 推送」;小字「完整演示:docs/demo/README.md · 真实收卡:push 换 feishu_card + myia secret set」。

## 给主人的真实屏幕重录指引(可选)

当前 `assets/myia-demo.gif` 是**用上面的真实输出经 ffmpeg 合成的终端动图**
(非 GUI 录屏 —— 本机 `screencapture -v` 已实测可用,但它录的是整块屏幕,
实测帧里带主人其它窗口的私人内容,不能直接作为公开素材)。想要
「真终端 + 真打字」的版本,按下列步骤录一次即可:

1. 新建干净终端 profile:深色底(#0d1117)、Menlo 15pt、窗口 ≥1100×640,
   关掉透明与多余标签页;只保留本分镜要用的窗口。
2. 起演示源:`python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/demo/assets/demo-site`
3. 录制(二选一):
   - QuickTime Player → 文件 → 新建屏幕录制(选「录选区」框住终端窗口);或
   - `mkdir -p /tmp/myia-cap && screencapture -v /tmp/myia-cap/demo.mov`,
     结束按 ⌘Ctrl+Esc;再用
     `ffmpeg -i /tmp/myia-cap/demo.mov -vf "crop=w:h:x:y,fps=12" …` 裁出终端区。
4. 按 S1→S8 顺序敲命令(每幕间停 1–2s);全程约 60s,后期可加速到 42s。
5. 转 GIF(两遍调色板,体积小、文字锐):
   `ffmpeg -i demo.mov -vf "fps=12,scale=1100:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" assets/myia-demo.gif`
6. 红线检查(发布前必做):逐帧确认画面没有凭据、私人窗口/通知、真实语料;
   演示页内容为仓库自带虚构数据,无需打码。
7. 校验:`ls -l assets/myia-demo.gif` < 5MB;在 GitHub 网页预览确认小字号
   依然可读(不行就加大终端字号重录,别靠拉大 GIF)。
