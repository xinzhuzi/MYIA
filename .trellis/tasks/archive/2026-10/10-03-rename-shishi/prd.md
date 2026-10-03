# 定名「世事」:MYIA 更名 + 眼后宇宙图标

## Goal

主人定案(2026-10-03):产品名 MYIA→世事,图标改为眼睛背后是宇宙。范围=桌面显示面:productName/窗口标题/Dock 名/html title/alt;图标 branding 唯一事实源重绘(SVG→rsvg→tauri icon 全尺寸)。内部标识符不动(com.myia.app/MYIA_HOME/myia-core/mainBinaryName=MYIA——数据根/锁域/updater 身份零迁移);repo 名/PyPI/CLI 名=主人后续拍板;desktop-release.yml 产物名同步世事.app.tar.gz

## Requirements

- TBD

## Acceptance Criteria

- [x] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

## 验收记录(2026-10-03,受主人委托代验)

**verdict:rejected(留 review;一处应修缺陷,其余全过)**。注:Acceptance Criteria 为 TBD,
代验按 Goal 列明的显示面范围逐项对照。

已核实通过(均为 HEAD 已提交证据):
- productName=世事、窗口 title=世事(desktop/src-tauri/tauri.conf.json:3/16);内部标识符不动:
  mainBinaryName=MYIA(:4)、identifier=com.myia.app(:6)、MYIA_HOME/myia-core(main.rs:211-213、entry.py)
- html title=世事(desktop/ui-src/index.html:8);品牌标 src=/shishi-icon.svg、alt=世事
  (desktop/ui-src/src/components/myia-mark.tsx:10-11);public/shishi-icon.svg 在档
- 图标:branding/shishi-icon.svg 唯一事实源 + shishi-icon-1024.png + branding/README.md 管线
  (SVG→rsvg→tauri icon);src-tauri/icons/ 全尺寸经 a20882b 重生成 + v3.1 迭代 598130f;
  主人验收 v3.1 定稿(task.json notes + evidence 转录)
- desktop-release.yml 产物名世事.app.tar.gz(l.119/127/139-140)、release notes 前缀世事(l.122);
  UPDATER.md 同步;MYIA_SIDECAR_SKIP 等 env 名不动
- Dock 名:由 productName 派生 CFBundleDisplayName,transcript 记 PlistBuddy 实证
  /Applications/世事.app(旧 MYIA.app 已备份移除)

应修缺陷(rejected 依据;可修缺陷不得标 conditional):
1. **favicon 悬挂引用,系本任务提交 a20882b 自身引入**:desktop/ui-src/index.html:5
   `href="/myia-icon.svg"` —— 同提交删除了 public/myia-icon.svg(现仅存 shishi-icon.svg),
   源码与构建产物(desktop/ui/index.html:5,frontendDist)均成 404 悬挂;evidence 中
   「public 旧标已换」的声明不完整(文件已换、引用未换)。修法:href→/shishi-icon.svg,
   重建 ui。
2. (随件)事实源文档指针陈旧:desktop/ui-src/README.md:35、desktop/ui-src/src/index.css:5
   注释仍指已删除的 branding/myia-icon.svg,应随修更正。

非本任务遗留:repo 名/PyPI/CLI 拍板已由 10-03-shishi-everywhere(in_progress)承接执行
(pyproject 已名 shishi);tauri.conf.json:41 shortDescription 仍为「MYIA — AI-native
intelligence hub」——不在 Goal 列举范围,随件记录供 shishi-everywhere 收尾扫尾。

> **2026-10-03 复审(主会话)**:代验驳回项(favicon 404 悬挂 + 2 处陈旧指针)已修复——index.html href 改 shishi-icon.svg、README/index.css 指针同步,npm run build 重生成产物(ui/index.html 已含 shishi-icon,EXIT=0),全量 1771 passed。verdict 升为 accepted,归档。
