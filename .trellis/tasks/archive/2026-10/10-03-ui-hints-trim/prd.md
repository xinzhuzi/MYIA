# PRD:桌面端清除开发期提示文案

## 需求

主人 2026-10-03 看到已装 App 里两处开发期提示,明确"不需要":

1. 侧栏底部整行:`v1.1 骨架 · 业务接入见各屏空态`(sidebar.tsx:51-53)
2. 顶栏在线徽标文字:`sidecar v0.1.0 · 协议 v1`(top-bar.tsx:61)

处理口径:

- 侧栏:删除整块 footer(纯开发期说明,无业务功能)。
- 顶栏:在线态删掉文字,**保留绿色脉冲圆点**与悬浮 tooltip(连接状态反馈不丢,版本/协议信息退到 tooltip 供排障);connecting/error 态不动(瞬态/异常提示,未被点名)。
- 普查档 10-03-gap-census C10 的"文案失实"半边由此收口,"协议无 app 版本字段"半边仍留档。

## 验收标准

- [x] 两处文案在源码中消失;侧栏无残留空 footer;顶栏在线态仍可见状态点
- [x] `npm --prefix desktop/ui-src run test` 与 `run build`(tsc+vite)全绿
- [x] 重打包 `npm run tauri build` 成功,重装 /Applications/MYIA.app(旧版备份 /tmp/MYIA.app.bak-hints-trim),打开 App 目视两处提示已无(OCR 截屏:/tmp/myia-hints-smoke2.png,两字样零出现,情报数据正常渲染)
- [x] 单 commit 提交;journal 记一笔

## 关联

- 普查档:`.trellis/tasks/10-03-gap-census/prd.md` C10(部分收口)
