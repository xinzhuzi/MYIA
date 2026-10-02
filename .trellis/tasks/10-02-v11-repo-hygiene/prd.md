# 仓库卫生:图标去重 + 本地夹具合并

## 来源

2026-10-02 顶层目录职责审计揪出的两处真重叠(第三处 plugins/docker 已由 10-02-v11-plugins-source-arch 覆盖)。

## 问题 1:图标三份存

- `desktop/branding/myia-icon-1024.png` — 源图(**保留,事实源**)
- `desktop/app-icon.png` — 与源图逐字节相同的复制(当时顺手 cp)→ **删除**,若有引用改指 branding
- `desktop/src-tauri/icons/` — `tauri icon` 生成的全套尺寸(构建必需,保留,生成物可随时再生成)

## 问题 2:本地测试夹具两套

同是「127.0.0.1 假源」的两套实现:

- `desktop/fixture/`(page.html + serve.py + plugin.yaml)— sidecar 往返验证
- `docs/demo/assets/demo-site/index.html` + `docs/demo/demo-news.yaml` — demo 录制/重放

**定夺方向(design 可改)**:合并为一套共享夹具(如 `fixtures-local/` 或择一为源、另一方引用);须保持 desktop 构建(tauri.conf resources 指向)与 docs/demo 的重放步骤(docs/demo/README.md 的 3 分钟上手)两边都不破。

## Acceptance Criteria

- [ ] `desktop/app-icon.png` 删除,全仓无引用残留(grep 验证);图标事实源唯一 = branding/
- [ ] 夹具合一:两边验证路径(desktop sidecar 往返、docs/demo 重放)各跑一遍通过并记录
- [ ] 全量 pytest 绿;提交信息说明变更
