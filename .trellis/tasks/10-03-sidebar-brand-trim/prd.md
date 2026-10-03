# PRD:桌面端移除侧栏品牌头

## 需求

主人 2026-10-03 发来截图(448×110,OCR 确认内容为「MYIA」+「AI 替主人看着世界」),指示「在 UI 布局上面,这个图的内容不需要了」。截图对应侧栏顶部品牌头整块(sidebar.tsx:20-28):

1. MyiaMark 图标(`myia-icon.svg`,size-7)
2. MyiaWordmark 渐变字标(「MYIA」)
3. 标语行「AI 替主人看着世界」

## 处理口径

- sidebar.tsx:删品牌头整块及相关 import;导航区顶部补 padding(原 header 自带 pt-4 的节奏,删后导航首项不能贴窗口顶)。窗口为标准标题栏(tauri.conf 无 overlay/transparent),红绿灯不与侧栏重叠,删除无碰撞风险。
- MyiaWordmark 删头后全项目零引用 → 一并从 myia-mark.tsx 删除(死代码不留);MyiaMark 保留(empty-state.tsx 在用)。
- TopBar 与其余布局不动。

## 验收标准

- [x] 品牌头三要素(图标/字标/标语)在 desktop/ui-src 源码中消失,侧栏无残留空块;导航首项不贴顶(nav px-2 pt-4 pb-2)
- [x] grep 零残留:`替主人看着世界`、`MyiaWordmark`(源码与 vite 构建产物,GREP_EXIT=1)
- [x] `npm --prefix desktop/ui-src run test` 全绿(显式退出码:40/40,TEST_EXIT=0)
- [x] `npm --prefix desktop/ui-src run build`(tsc+vite)全绿(BUILD_EXIT=0)
- [x] 重打包 `npm run tauri build` 成功(exit 0,28.83 MiB),重装 /Applications/MYIA.app(备份 /tmp/MYIA.app.bak-brand-trim),窗口截屏 OCR 目视:侧栏品牌头已无、五项导航在序、仪表盘数据正常渲染;截屏中孤例「MYIA」为系统标题栏窗口标题(tauri.conf title),非侧栏字标
- [x] 单 commit 提交(仅本任务文件:sidebar.tsx/myia-mark.tsx/index.html/任务目录);journal 记一笔(journal 文件还压着 yaml-editor 线程两行未提交条目,本任务行随下次 docs 提交走)

## 关联

- 前例:archive/2026-10/10-03-ui-hints-trim(同日同区域裁剪,验收口径含重打包+目视)
