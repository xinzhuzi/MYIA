# Demo:3 分钟上手动图 + 视频

## Goal

README 第一屏的说服力(规划第九节):动图展示「一个 YAML → 情报推送」,视频讲 AI-NATIVE 用法。

## Requirements

- README 顶部 3 分钟上手动图(gif/mp4,<5MB):终端 `myia run` → 飞书收卡 全程
- 视频 2-3 分钟:对 AI 说需求 → agent 写 YAML → 跑通 → 推送;脚本+录制+双语字幕
- 素材脚本进仓库,成片外链(B 站/YouTube)

## Acceptance Criteria

- [ ] 动图 ≤5MB 且清晰可读
- [ ] 视频发布可访问,README 链接有效

## Notes

- 录制用真实源;凭据与私人信息打码

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: AC1 实测通过:file+du 显示 gif 542,088B(529KB≤5MB)、1100×640,ffprobe 实测 42.5s,ffmpeg 抽 38s 帧目视核验终端文字含中文清晰可读;素材脚本/分镜/transcript/双语字幕均入库。AC2 的成片外链未发生:grep bilibili|b23|youtu 于 README+docs/demo 零命中,docs/demo/README.md 自标「成片外链占位」,README 现存链接均为有效本地路径。

遗留(需主人手动完成):
- 视频成片发布到 B站/YouTube 并把外链回填 README 与 docs/demo(发布类,需主人;docs/demo/video/shot-list.md 发布清单待执行)
- 发布后终验外链可访问且 README 链接有效(需主人)
