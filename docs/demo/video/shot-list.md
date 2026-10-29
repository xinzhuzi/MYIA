# 素材清单与发布清单 —《把信息源交给 AI:世事 两分半》

> 配套:`script.md`(分镜+解说词)、`shishi-demo.srt`(双语字幕,已校验:
> 18 条,总长 150s,时码无重叠)。本页是「拿去就能拍」的材料单。
>
> **成片(装配版)已交付:`shishi-demo.mp4`**(本目录)—— 72s / 1920×1080 /
> H.264+AAC / ≈1.0MB。**装配版:由演示动图+卡片合成**(片头卡 → 动图放大+
> 烧录中文字幕 → 五步能力卡 → 片尾卡,`build_video.sh` 一键复跑);
> **真人录屏版可选**,下方核对表与发布清单即为该版本所备。
>
> **成片外链占位(发布后回填这里与 script.md 顶部):**
> - B站:`` (占位)
> - YouTube:`` (占位)

## 一、素材核对表

| 镜 | 素材 | 形式 | 状态 |
|----|------|------|------|
| ✅ | **成片(装配版)**:`shishi-demo.mp4`,72s/1080p30/H.264+AAC/≈1.0MB,中文字幕已烧录(取 srt 中文行按动图分镜重排) | `build_video.sh`(ffmpeg 合成,无真人录屏) | ✅ 已交付 |
| S1 | 信息过载空镜(标签页/群消息闪切,3×2s) | 自摄或素材库(注意许可,商用许可留档) | ☐ 待拍/选(装配版未用) |
| S2 | coding agent 对话窗:输入需求 → 跑 `shishi init`(JSON 清单) | 录屏(新会话,需求文本预打好) | ☐ 待录 |
| S3 | agent 生成/讲解 12 节 YAML(对照 `../demo-news.yaml`) | 录屏 + 镜头推近 | ☐ 待录 |
| S4 | `shishi test` → `shishi run` 全链 | 录屏(预期画面 = `../gif/transcript/06-test.txt`、`03-run1.txt`) | ☐ 待录 |
| S5 | stdout 推送卡片;飞书收卡示意 | 卡片文本录屏(`../gif/transcript/04-card.txt`)+ 飞书测试群实拍(打码) | ☐ 待录 |
| S6 | 二次 run(skip=not_modified)+ `shishi list` | 录屏(`../gif/transcript/05-run2.txt` 为预期画面) | ☐ 待录 |
| S7 | 片尾卡 | 可直接取 `../gif/make_ass.py` 的 S8 样式重排一张 1920×1080 静帧 | ☐ 待做 |
| — | 解说配音 / TTS | 依 srt 中文行逐条念(每条 3–7s) | ☐ 待做 |
| — | 双语字幕 | `shishi-demo.srt` | ✅ 已备 |

## 二、录制环境清单

- [ ] 终端 profile:深色底 #0d1117、Menlo **20pt**、窗口 2560×1600、无透明、单标签。
- [ ] 系统勿扰模式开启;隐藏桌面图标;退出/遮挡与演示无关的窗口。
- [ ] 演示源:`python3 -m http.server 8765 --bind 127.0.0.1 --directory desktop/fixture`
- [ ] 干净库:`rm -rf /tmp/shishi-demo`(S4 前重置一次,保证「首轮推送」画面)。
- [ ] coding agent:新会话;需求文本预打好(S2 原文见 script.md §S2)。
- [ ] 录制:QuickTime → 新建屏幕录制 → 选区框住目标窗口;或 OBS 2560×1600@30。

## 三、S4–S6 实拍命令序列(逐条敲,每条之间停 1–2 秒)

```console
$ shishi test docs/demo/demo-news.yaml --source demo-hub
$ shishi run docs/demo/demo-news.yaml --db /tmp/shishi-demo/demo.db
$ shishi run docs/demo/demo-news.yaml --db /tmp/shishi-demo/demo.db > run.out
$ grep '^{"channel"' run.out | jq -r .text        # S5 卡片画面
$ shishi run docs/demo/demo-news.yaml --db /tmp/shishi-demo/demo.db   # S6:skip=not_modified
$ shishi list --db /tmp/shishi-demo/demo.db           # S6:健康度(可选 --plugins-dir plugins)
```

注:`shishi list` 扫描的是 `--plugins-dir`(默认 `./plugins`);demo 品类在
docs/demo 下,要让 demo-hub 的健康度行出镜,录制时临时把
`docs/demo/demo-news.yaml` 拷入 `plugins/`,拍完删除(本次未执行该拷贝)。
开发环境把 `shishi` 换成 `uv run --no-sync shishi`(素材预期画面均按此真实执行,
见 `../gif/storyboard.md` 素材事实表)。

## 四、红线与打码清单(发布前逐项过)

- [ ] 全片无任何凭据(token/cookie/密码),无 env 变量展开值。
- [ ] 无私人窗口、个人书签、通知横幅;飞书镜头群名/头像打码(测试群拍摄)。
- [ ] 演示页内容为仓库自带虚构数据(`desktop/fixture/index.html`,docs/demo 共用),无需打码;
      但**不要**把镜头对准真实生产库或真实收卡群的全名。
- [ ] 内网地址仅允许 127.0.0.1 出镜(安全基线显式例外)。

## 五、剪辑与导出

> **装配版现状**:`shishi-demo.mp4` 已按 60–90s 目标合成 —— 72.0s、1920×1080@30、
> H.264(CRF 20)+ 静音 AAC、≈1.0MB,ffprobe 实测全达标;中文字幕已烧录,
> 音轨静音(旁白未录)。`shishi-demo.mp4` 是**演示动图+卡片的合成片,不含真人
> 录屏**;要出 2:30 真人版,按下述原参数走完整流程即可(装配脚本
> `build_video.sh` 只覆盖装配版)。

- 时间线按 `script.md` 节奏总表;解说与 `shishi-demo.srt` 时码对齐(总长 150s)。
- 字幕:压制中文行,英文行做可选字幕轨(平台 CC);或直接上传 srt。
- 导出:H.264、1080p、CRF 20、AAC 192k、响度 -16 LUFS;封面取 S1 或片尾卡。
- 体积参考:150s@1080p 约 20–40MB,两平台均可直接上传。

## 六、发布清单

**B站**
- 标题(建议):`把信息源交给 AI:一个 YAML,世事 帮你盯到推送 | 开源`
- 简介:一句话定位 + GitHub 链接 + 时间轴章节(S1–S7)。
- 标签:`开源` `AI` `自动化` `爬虫` `效率工具` `Agent`
- 发布后:把成片外链回填本文件顶部与 `script.md` 顶部占位。

**YouTube**
- Title(建议):`世事: Hand Your Info Sources to AI — One YAML to Push`
- Description:定位段 + GitHub link + chapters(00:00 Sources overload / 00:12 Tell your AI / 00:35 Agent writes YAML / 01:05 Test & run / 01:30 The push / 01:55 Incremental & health / 02:15 Outro)。
- Tags: `open source`, `ai agent`, `automation`, `web scraping`, `pipeline`
- 发布后:同样回填外链占位。
