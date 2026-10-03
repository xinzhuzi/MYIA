# 多会话共享机构建竞态:PyInstaller 缓存互踩 + e2e 清场互踩

## Goal

同一工作区多 AI 会话并行施工暴露的两笔共享设施竞态:①build-sidecar.sh 带 --clean 会先删 ~/Library/Application Support/pyinstaller 共享缓存,两会话同时打包互删对方缓存(onnxruntime/cv2/numpy 三次悬空实锤);候选修法=打包文件锁串行化或每次构建独立 PYINSTALLER_CONFIG_DIR。②e2e/冒烟清场用 git checkout -- plugins/ 全目录还原,会吞掉其他会话的在途冒烟改动(yaml-editor 冒烟 diff 被并行 e2e 还原实锤);候选修法=清场只动自己创建/修改的显式文件清单。本档只落档与方案,修复排期归 release 工程/ci-gates 侧裁量

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
