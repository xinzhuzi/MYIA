# CI 门禁:PR 级 cargo check + lint 论证 + pytest 跑法钉死

## Goal

普查 D 组工程门禁:**排 v1.1.1 tag 后开工**(grill 2026-10-03 Q5)。

## Requirements

1. **D1**:ci.yml 增 PR/push 级 `cargo check`(src-tauri)——现状 Rust 只有
   tag 发版才首次编译,PR 弄坏 Rust 代码 CI 依旧全绿。注意 binaries/
   被 gitignore(fresh checkout 无 sidecar 产物):check 需容忍缺失
   externalBin 或先跑 build-sidecar 脚本的轻量替代(见普查 D5 关联)。
2. **D2**:lint/类型门禁论证——ruff(可零配置起步)/ mypy(可选)写进
   pyproject 与 CI;若论证不做,在本档 PRD 记录理由,不留悬案。
3. **D3(已修,718d56c)**:tests 裸 pytest 假红已修(from conftest import);
   本档只做**口径钉死**:CONTRIBUTING/CI 注释统一写
   `uv run python -m pytest -q`(或补 tests/__init__.py 让两跑法等价,
   实测定,注意 ee2d9db 的包扫描教训)。
4. **D5 顺带评估**:desktop dev 链 fresh clone 缺 binaries/ 即挂 —— 在 ci.yml
   或 README dev 节写明「先 npm run sidecar」的最短路径(或 beforeDevCommand)。

## Acceptance Criteria

- [ ] PR 级 cargo check 进 ci.yml 且对无 binaries/ 的 checkout 不误红
- [ ] lint 门禁定了做/不做 + 理由入档;做则 CI 绿
- [ ] pytest 跑法口径一处定稿,文档与 CI 一致
- [ ] 现有 pytest/vitest 全绿
