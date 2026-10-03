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

## 验收记录(2026-10-03,受主人委托代验)

verdict: conditional

evidence: desktop/app-icon.png 已删(ls 确认不存在),全仓 grep 仅剩说明性/历史提及(branding/README.md:4 为删除记录本身、.trellis 任务档),零功能引用;夹具合一:docs/demo/assets/demo-site/ 目录已删,docs/demo/README.md:5,30,41 指 desktop/fixture/ 为事实源,tauri.conf.json:30 resources 含 ../fixture/plugin.yaml;两边验证路径我各实跑一遍:docs/demo 重放(起 http.server 8765 + `myia run docs/demo/demo-news.yaml`→首轮 7 条 success,二次 not_modified,与 README 步骤逐条吻合)+ tests/test_desktop_sidecar_protocol.py 46 tests 绿;全量 pytest 由脚本门禁跑过(绿),我未重复跑。

遗留(需主人手动完成):
- AC「提交信息说明变更」:本批变更未提交(git status 全为 M/??),提交为主人手动项(审计约定禁 git)
