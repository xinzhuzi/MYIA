#!/usr/bin/env bash
# 装配《世事 演示成片》:shishi-demo.mp4(72.0s / 1920x1080 / H.264 + 静音 AAC)
#
# 装配版 = 现有素材合成,无真人录屏:
#   片头卡 5.0s → shishi-demo.gif 放大 42.5s(烧录中文字幕)→ 能力点文字卡 18.0s → 片尾卡 6.5s
#
# 素材(均为仓库既有产物,未引入新依赖):
#   - ../assets/shishi-demo.gif   1100x640@12fps、42.5s(内容事实见 ../gif/storyboard.md)
#   - shishi-demo.srt             双语字幕;此处取其中文行 #6/#9/#10/#11/#12/#13/#15/#17,
#                               按动图内部分镜时码重排;另加 1 条过场行(事实源:storyboard S3)
#   - GitHub 地址               https://github.com/xinzhuzi/shishi(根 README)
#
# 依赖:仅 ffmpeg;中间文件全部写入 /tmp,仓库内只落成片一个文件。
# 实现注意(实测踩坑):
#   - drawtext 文本一律走 textfile —— 半角冒号/逗号会破坏 filtergraph 解析;
#   - 表达式内的逗号写作 `\,`;
#   - 中文字体显式指定(Hiragino Sans GB / STHeiti Medium),不依赖 fontconfig。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
GIF="$ROOT/docs/demo/assets/shishi-demo.gif"
OUT="$ROOT/docs/demo/video/shishi-demo.mp4"
WORK="$(mktemp -d /tmp/shishi-video-build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

F_CJK='/System/Library/Fonts/Hiragino Sans GB.ttc'   # 中文正文/字幕/◎ 字形
F_HEI='/System/Library/Fonts/STHeiti Medium.ttc'     # 稍重的黑体(片头/能力卡标题)
command -v ffmpeg >/dev/null || { echo "需要 ffmpeg" >&2; exit 1; }
[ -f "$F_CJK" ] || { echo "缺字体 $F_CJK" >&2; exit 1; }
[ -f "$F_HEI" ] || { echo "缺字体 $F_HEI" >&2; exit 1; }
[ -f "$GIF" ]   || { echo "缺素材 $GIF" >&2; exit 1; }

t() { printf '%s' "$2" > "$WORK/$1.txt"; }

# ---- 片头卡 -----------------------------------------------------------------
t motif   '(( ◎ ))'                                  # 眼睛雷达意象(◎ 经实测 Hiragino 可渲染)
t title   '世事'
t tagline '眼睛雷达 · 把信息源交给 AI'
t small   'AI-NATIVE 信息情报管线 · 本演示零凭据零外网'

# ---- 能力点文字卡(采集/分类/去重/推送/反馈) --------------------------------
t cap_h '世事 五步能力'
t cap_1 '01 采集 —— 礼貌抓取,engine 可自动降级'
t cap_2 '02 分类 —— 内置规则先行,跳过原因可见'
t cap_3 '03 去重 —— 指纹跳过,不打扰'
t cap_4 '04 推送 —— 模板不变,换 feishu_card 即发飞书'
t cap_5 '05 反馈 —— shishi list 健康度,shishi doctor 诊断'

# ---- 片尾卡 -----------------------------------------------------------------
t end_main '世事 · 把信息源交给 AI'
t end_repo 'github.com/xinzhuzi/shishi'
t end_docs '完整上手:docs/demo/README.md'

# ---- GIF 段中文字幕(时码相对 GIF 段起点;行文取自 shishi-demo.srt 中文行) ------
t sub1 'agent 据此生成 12 节 YAML:从哪抓、怎么礼貌地抓、怎么分类去重、推到哪里。'  # srt #6
t sub2 '起一个本机演示源:仓库自带虚构数据,仅监听 127.0.0.1。'                      # 过场(storyboard S3)
t sub3 '然后整链跑一遍:抓到 7 条,推送一张卡。'                                     # srt #10
t sub4 '1 条与 AI 无关,被分类跳过 —— 跳过原因可见,不是黑盒。'                       # srt #11
t sub5 '这就是推出去的卡片:标题、链接,整整齐齐。'                                   # srt #12
t sub6 '想发进飞书或 Telegram:模板不动,通道换一行配置。'                            # srt #13
t sub7 '再跑一次:内容没变,指纹直接跳过,不发打扰消息。'                              # srt #15
t sub8 '先单源试抓:提取字段、去重键,不推送、不入库。'                               # srt #9
t sub9 '世事:把信息源交给 AI。一个 YAML,一条管线,推送随取。'                        # srt #17

# ---- 滤镜图 -----------------------------------------------------------------
# GIF 段字幕窗口(相对 GIF 起点,依据 ../gif/storyboard.md 逐帧分镜):
#   sub1 YAML 3-9 | sub2 起源 9-12 | sub3/4 首轮 run 12-16.5/16.5-21
#   sub5/6 卡片 21-24.5/24.5-27.5 | sub7 增量 27.5-33 | sub8 试抓 33-38.5 | sub9 片尾 38.5-42.5
cat > "$WORK/graph.txt" <<GRAPH
[0:v]scale=1670:972:flags=lanczos,pad=1920:1080:125:20:color=0x0d1117,fps=30,format=yuv420p,setsar=1
[base];
[base]drawtext=fontfile='$F_CJK':textfile=$WORK/sub1.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,3)*lt(t\,9),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub2.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,9)*lt(t\,12),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub3.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,12)*lt(t\,16.5),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub4.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,16.5)*lt(t\,21),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub5.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,21)*lt(t\,24.5),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub6.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,24.5)*lt(t\,27.5),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub7.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,27.5)*lt(t\,33),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub8.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,33)*lt(t\,38.5),
drawtext=fontfile='$F_CJK':textfile=$WORK/sub9.txt:fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.85:x=(w-tw)/2:y=h-100:enable=gte(t\,38.5),
settb=AVTB,setpts=PTS-STARTPTS[gifseg];
color=c=0x0d1117:s=1920x1080:d=5:r=30,format=yuv420p,setsar=1,
drawtext=fontfile='$F_HEI':textfile=$WORK/motif.txt:fontsize=130:fontcolor=0x58a6ff:x=(w-tw)/2:y=270,
drawtext=fontfile='$F_HEI':textfile=$WORK/title.txt:fontsize=210:fontcolor=white:x=(w-tw)/2:y=440,
drawbox=x=(iw-160)/2:y=705:w=160:h=6:color=0x58a6ff:t=fill,
drawtext=fontfile='$F_CJK':textfile=$WORK/tagline.txt:fontsize=54:fontcolor=0x8b949e:x=(w-tw)/2:y=755,
drawtext=fontfile='$F_CJK':textfile=$WORK/small.txt:fontsize=32:fontcolor=0x6e7681:x=(w-tw)/2:y=860,
settb=AVTB,setpts=PTS-STARTPTS[card1];
color=c=0x0d1117:s=1920x1080:d=18:r=30,format=yuv420p,setsar=1,
drawtext=fontfile='$F_HEI':textfile=$WORK/cap_h.txt:fontsize=72:fontcolor=white:x=(w-tw)/2:y=130,
drawbox=x=(iw-140)/2:y=235:w=140:h=5:color=0x58a6ff:t=fill,
drawtext=fontfile='$F_CJK':textfile=$WORK/cap_1.txt:fontsize=46:fontcolor=0xc9d1d9:x=460:y=345:enable=gte(t\,1.5),
drawtext=fontfile='$F_CJK':textfile=$WORK/cap_2.txt:fontsize=46:fontcolor=0xc9d1d9:x=460:y=470:enable=gte(t\,4.7),
drawtext=fontfile='$F_CJK':textfile=$WORK/cap_3.txt:fontsize=46:fontcolor=0xc9d1d9:x=460:y=595:enable=gte(t\,7.9),
drawtext=fontfile='$F_CJK':textfile=$WORK/cap_4.txt:fontsize=46:fontcolor=0xc9d1d9:x=460:y=720:enable=gte(t\,11.1),
drawtext=fontfile='$F_CJK':textfile=$WORK/cap_5.txt:fontsize=46:fontcolor=0xc9d1d9:x=460:y=845:enable=gte(t\,14.3),
settb=AVTB,setpts=PTS-STARTPTS[card2];
color=c=0x0d1117:s=1920x1080:d=6.5:r=30,format=yuv420p,setsar=1,
drawtext=fontfile='$F_HEI':textfile=$WORK/end_main.txt:fontsize=88:fontcolor=white:x=(w-tw)/2:y=430,
drawbox=x=(iw-140)/2:y=565:w=140:h=5:color=0x58a6ff:t=fill,
drawtext=fontfile='$F_CJK':textfile=$WORK/end_repo.txt:fontsize=60:fontcolor=0x58a6ff:x=(w-tw)/2:y=620,
drawtext=fontfile='$F_CJK':textfile=$WORK/end_docs.txt:fontsize=36:fontcolor=0x6e7681:x=(w-tw)/2:y=760,
settb=AVTB,setpts=PTS-STARTPTS[card3];
[card1][gifseg][card2][card3]concat=n=4:v=1:a=0[v]
GRAPH

# ---- 装配(单次编码;音轨为静音 AAC —— 旁白配音留待真人版) -------------------
# -ignore_loop 1:忽略 GIF 的循环标志,只解码一遍(实测 0 会无限循环解码);
# -t 72:与时间线总长一致,双保险防失控。
ffmpeg -y -v error \
  -ignore_loop 1 -i "$GIF" \
  -f lavfi -i anullsrc=r=44100:cl=stereo \
  -filter_complex_script "$WORK/graph.txt" \
  -map '[v]' -map 1:a \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  -t 72 -shortest -movflags +faststart \
  "$OUT"

echo ">> 产物:$OUT"
