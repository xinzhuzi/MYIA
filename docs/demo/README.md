# docs/demo —「一个 YAML → 情报推送」演示物料

README 第一屏的说服力素材(规划第九节):**3 分钟上手动图 + AI-NATIVE 视频**。
演示品类在本目录,演示源(假源页)与桌面端**共用同一套本地夹具**
(`desktop/fixture/`,2026-10-02 夹具合一后的事实源),全程零凭据、零外网
(演示源仅监听 `127.0.0.1`,安全基线的显式例外);演示页内容为
**仓库自带的虚构数据**,不要部署到公网。

## 文件地图

```
docs/demo/
├── README.md                  ← 本页:复现步骤与物料索引
├── demo-news.yaml             演示品类(12 节 schema;push=stdout,零凭据)
├── assets/
│   └── myia-demo.gif          动图产物:42.5s / 1100×640 / 529KB(<5MB 达标)
├── gif/
│   ├── storyboard.md          逐帧分镜:命令序列+预期画面+重录指引
│   ├── transcript/*.txt       真实终端输出(2026-10-02 本机真实执行留档)
│   ├── make_ass.py            transcript → frames.ass(渲染器,纯标准库)
│   ├── frames.ass             生成的帧文件(build_gif.sh 会重新生成)
│   └── build_gif.sh           ffmpeg 渲染命令(两遍调色板)
└── video/
    ├── script.md              2:30 视频分镜脚本(解说词中英双语)
    ├── myia-demo.srt          双语字幕(18 条,总长 150s,已校验)
    ├── myia-demo.mp4          成片(装配版):72s / 1920×1080 / H.264+AAC / ≈1.0MB
    ├── build_video.sh         成片装配脚本(ffmpeg 单遍滤镜图,可复跑)
    └── shot-list.md           素材清单 + 打码红线 + 发布清单(成片外链占位)

desktop/fixture/               演示源事实源(与桌面端共用;本目录引用之)
├── index.html                 演示源页(虚构数据;http.server 本机回放,URL `/`)
├── page.html                  桌面 sidecar 往返夹具页(桌面专用,URL /page.html)
├── plugin.yaml                桌面往返夹具品类(tauri resources 内置)
└── serve.py                   本机静态服务(等价于下方 http.server 一行命令)
```

## 3 分钟上手:真实复现

```console
# 1) 起演示源(仓库自带虚构数据页,仅本机;页面在 desktop/fixture/)
$ python3 -m http.server 8765 --bind 127.0.0.1 --directory desktop/fixture

# 2) 首轮:一条命令,抓取→分类→去重→推送
$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db
状态:success        # 7 条抓到,1 条与 AI 无关被分类跳过(原因可见),推送 1 张卡

# 3) 看推送卡片(stdout 通道;text 字段即渲染后的卡片,与飞书卡同模板)
#    同库马上重跑会 not_modified(见第 4 步)拿不到卡;先重置演示库再看一次
$ rm -rf /tmp/myia-demo
$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db > run.out
$ grep '^{"channel"' run.out | jq -r .text

# 4) 再跑一次:内容没变 → 变更指纹跳过,不打扰
$ myia run docs/demo/demo-news.yaml --db /tmp/myia-demo/demo.db
  源 demo-hub(跳过,engine=static_html,条目 0,skip=not_modified)

# 5) 改 YAML 后先单源试抓(不推送不入库)
$ myia test docs/demo/demo-news.yaml --source demo-hub
```

- 开发环境(本仓库)把 `myia` 换成 `uv run --no-sync myia`。
- 真实收卡:把 `demo-news.yaml` 的 push[] 换成文件内注释的 `feishu_card` 块,
  凭据 `myia secret set` 进系统钥匙链 —— 卡片模板不变。
- 完整分镜(每帧命令与预期画面)见 [`gif/storyboard.md`](gif/storyboard.md)。

## 动图:README 嵌入(已完成)

动图文件:`assets/myia-demo.gif`。**根 README 已嵌入该动图**(顶部徽标区之后,
`<img src="docs/demo/assets/myia-demo.gif" … width="820">`,见根 README 第 11 行;
嵌入时使用的片段如下,留作参考):

```markdown
<p align="center">
  <img src="docs/demo/assets/myia-demo.gif"
       alt="MYIA:一个 YAML → 情报推送(终端 myia run → 收到推送卡片)"
       width="820">
</p>
```

### 这个 GIF 是怎么来的(如实说明)

- 本机实测:`vhs` / `terminalizer` / `asciinema` 均未安装;`ffmpeg` 7.1.1 与
  macOS `screencapture` 可用。
- `screencapture -v` **技术上能录**(GUI 会话与录屏权限都在),但它只能录
  整块屏幕:实测帧里带着主人其它窗口的私人内容(内网地址、私有会话),
  不能作为公开素材,也没有「时间控制」。
- 因此交付的 GIF 是**终端合成录制**:分镜里每一段画面文本逐字来自
  `gif/transcript/*.txt`(真实执行的命令输出),由 `make_ass.py` 生成
  libass 帧、`build_gif.sh`(ffmpeg)渲染 —— 内容真实、可复跑、零隐私面。
- 想要「真终端 + 真打字」的版本:按 `gif/storyboard.md` §「给主人的真实
  屏幕重录指引」录一次即可,产出覆盖 `assets/myia-demo.gif`(openIssues
  已登记,需主人录一次,可选)。

## 视频物料(2:30,AI-NATIVE 用法)

- 分镜+解说词:[`video/script.md`](video/script.md)(对 AI 说需求 → agent
  写 YAML → 跑通 → 推送;成片外链占位在文件顶部)。
- 双语字幕:[`video/myia-demo.srt`](video/myia-demo.srt)(中英双行,18 条,
  总长 150s,时码与分镜一致)。
- 素材清单与发布清单:[`video/shot-list.md`](video/shot-list.md)(素材核对表、
  录制环境、打码红线、B站/YouTube 发布字段)。
- **成片(装配版)已交付**:[`video/myia-demo.mp4`](video/myia-demo.mp4) ——
  72s / 1920×1080 / H.264+AAC / ≈1.0MB。**装配版:由演示动图+卡片合成**
  (片头卡 → 动图放大+烧录中文字幕 → 五步能力卡 → 片尾卡),ffmpeg 一遍
  合成,`video/build_video.sh` 可复跑;**真人录屏版可选**,2:30 全版分镜
  (`video/script.md`)与外链占位(`video/shot-list.md`)为其保留。
- 装配版两条如实说明:音轨为静音 AAC(旁白配音未录,属真人版范畴);动图
  段画面即 `assets/myia-demo.gif` 的内容(真实命令输出合成,见
  `gif/storyboard.md`),场景间约 150ms 淡入淡出为动图原生转场。
