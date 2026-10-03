# 多会话共享机构建竞态:PyInstaller 缓存互踩 + e2e 清场互踩

## Goal

同一工作区多 AI 会话并行施工暴露的两笔共享设施竞态:①build-sidecar.sh 带 --clean 会先删 ~/Library/Application Support/pyinstaller 共享缓存,两会话同时打包互删对方缓存(onnxruntime/cv2/numpy 三次悬空实锤);候选修法=打包文件锁串行化或每次构建独立 PYINSTALLER_CONFIG_DIR。②e2e/冒烟清场用 git checkout -- plugins/ 全目录还原,会吞掉其他会话的在途冒烟改动(yaml-editor 冒烟 diff 被并行 e2e 还原实锤);候选修法=清场只动自己创建/修改的显式文件清单。本档只落档与方案,修复排期归 release 工程/ci-gates 侧裁量

## Requirements(2026-10-03 方案定案,主会话执行)

1. **PyInstaller 缓存竞态 → 独立 CONFIG_DIR(选型:弃文件锁)**:build-sidecar.sh 导出 `PYINSTALLER_CONFIG_DIR=<检出内 .pyinstaller-cache>`,--clean 只清自己缓存,跨会话零互删。理由:锁需超时/死锁协议且 --clean 仍全删共享目录,隔离彻底且实现一行;代价=每检出约 100-300MB 重复缓存(可接受,gitignore)。
2. **e2e/冒烟清场纪律 → 显式文件清单**:禁 `git checkout -- <目录>/` 全目录还原;只还原自己创建/修改的显式文件(写入 spec 纪律+分发模板)。无固化脚本(实锤来自会话临时命令,治本=纪律)。
3. 验证:实跑一次 sidecar 构建,缓存落在新位置、构建成功、共享目录不再被触碰。

## Acceptance Criteria

- [x] build-sidecar.sh 独立 PYINSTALLER_CONFIG_DIR + gitignore(实跑验证)
- [x] 清场纪律入 .trellis/spec/domain/os-etiquette.md(工作区礼仪并档)与分发模板
- [x] PRD 落实方案与选型理由(本节)

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

## 执行记录(2026-10-03 主会话,方案当班落地)

- build-sidecar.sh 导出私有 PYINSTALLER_CONFIG_DIR(desktop/.pyinstaller-cache,已 gitignore);实跑验证 EXIT=0、私有缓存 238M 落位、共享目录零触碰、产物 myia-core-aarch64-apple-darwin 正常
- 清场纪律(禁全目录 checkout 还原)入 spec/domain/os-etiquette.md「工作区共享礼仪」+ 分发模板;选型理由(弃锁取隔离)记录于 Requirements
