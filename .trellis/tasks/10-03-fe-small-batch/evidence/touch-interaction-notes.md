# AC8 触屏交互存证(after-only)——2026-10-03 主人裁决口径

裁决原文要点:①本批为功能件批次,AC8 价值在「交互可用」;before 态已不可复原
(改动全在工作树、并行流在途文件禁 stash),**不做任何 stash/还原**;②无头法
存证交互,文件名 `after-g*` 明示 after-only,不伪称 before/after 对;③P1/P2
对比度类以 WCAG 公式复算数值入档;严格 before/after 与装机目验归主人装机冒烟
一并补(leftovers 在案)。

## 链路(全无头,零前台;进程用后清)

```
seed.py(沙箱 MYIA_HOME 造数:6 items + 3 历史 run + 自拷 3 官方插件*)
  → desktop/entry.py serve(真 sidecar,stdio JSON-RPC,uv run)
  → bridge.mjs(127.0.0.1:8797:HTTP /rpc + SSE /events;FORCE_DRY=1)
  → vite dev(localhost:5173)
  → shoot.py(Playwright chromium:1180×820 @2x,hasTouch,locale zh-CN,dark)
     └ addInitScript(shim.js)= window.__TAURI_INTERNALS__ 浏览器直开垫片
```

- **视口径**:1180×820 触屏笔电/平板形态(390px 手机视口下桌面侧栏 `w-56`
  会盖住卡片操作簇,非本产品形态);触屏语义 = hasTouch + 全程 `tap()`。
- **FORCE_DRY 披露**:桥层把 `run.start` params.dry 强制 true——run 子进程/
  管线/日志环/run 注册表全真,仅采集与推送走 dry,存证零外网。
- \*dev 态 sidecar `_bundle_plugins_dir()` 恒 None(entry.py:378,仅 frozen 包
  有种),沙箱自拷 myia-demo/news/stocks 三件(health 实测 3 插件 loaded)。

## 截图与拍摄门禁(每张图在拍前均有 Playwright 选择器断言,断言不过不拍)

| 文件 | 内容 | 拍前门禁断言 |
|---|---|---|
| after-g8-enrich-loading.png | G8 摘要 loading 态(桥延迟 feed.enrich 应答 1.2s 捕帧,传输层延迟不改前端) | `[data-testid^="feed-enrich-loading-"]` 可见 |
| after-g8-enrich-result.png | G8 无配置 graceful 明示 | `[data-testid^="feed-enrich-error-"]` 可见 |
| after-g9-before.png | G9 全部标已读前(未读卡片 + 计数) | `[data-unread="true"]` 可见 |
| after-g9-after.png | G9 一键后(未读清零) | `document.querySelectorAll('[data-unread="true"]').length === 0` |
| after-g12-watchlist-panel.png | G12 沉淀面板(关键词/目标 YAML/写入) | `[aria-label="目标品类 YAML"]` 可见 |
| after-g12-watchlist-written.png | G12 写入回执 | `[data-testid^="feed-keyword-note-"]` 可见 |
| after-g7-rerun-triggered.png | G7 重跑已触发(骑 run.start 回放 yaml/dry/db) | `[data-testid^="run-rerun-ok-"]` 可见 |
| after-g7-search.png | G7 日志搜索命中高亮(`<mark>`) | `[data-testid="log-search-hit"]` 可见 |
| after-g7-filter.png | G7 品类过滤(combobox 选 myia-demo) | 过滤器选中态后拍 |

### 独立硬证据(非像素,可复查)

- G12 真落盘:沙箱 `plugins/myia-demo.yaml` 出现 `watchlist: … - 触屏存证词`
  (yaml.read→原文手术→yaml.save 全往返)。
- G8 graceful:`feed.enrich {item:"touch-0"}` →
  `{"code":"enrich_not_configured","message":"无法精评:品类 'news' 未启用 enrich…","data":{"reason":"enrich_disabled"}}`。
- G7 真触发:run.status 见 run_id=5(`dry:true, state:done, exit_code:0,
  status:success, duration 2.9s`,yaml=沙箱 myia-demo.yaml)——重跑真的跑了一次。

注:本轮存证会话的模型端不支持图像输入,PNG 像素内容未目验;以拍前选择器
断言 + 上表硬证据 + 下节复现命令担保,harness 全件留档可复跑。

## P1/P2 对比度复算(WCAG 相对亮度公式;底色=前景@α叠底实测合成)

过线面(修复值复算,全部 ≥4.5):

| 项 | 前景 | 底 | 比率 |
|---|---|---|---|
| P1 destructive 徽章(badge.tsx:17) | #ff6b70 | destructive/15@card #2e1a27 | **5.86** |
| 同上(popover 面) | #ff6b70 | destructive/15@popover #332130 | **5.41** |
| P2③ destructive 按钮(button.tsx:18) | #fdebec | #c53136 | **4.73** |
| P2① 侧栏小字整值(sidebar.tsx:113) | #8b94a7 | sidebar #080b13 | **6.46**(改前 /70 3.67、/80 4.48) |
| P2② 日志错误行 | #e5484d(text-dead) | dead/10@sidebar #1e1119 | **4.67** |

旁核三处(<4.5,文件脏/未跟踪本批未修,如实在案):

| 项 | 前景 | 底 | 比率 |
|---|---|---|---|
| 组头 hover 错误计数(logs-screen.tsx:175) | #e5484d | accent/60@card #151c2d | **4.35**(非 hover 4.77 过线) |
| 搜索 mark 落错误行(logs-screen.tsx:83) | #e5484d(继承) | warning/30@错误行 #5f4226 | **2.34**(正常行命中 7.66 过线) |
| 差评图标/错误文案(feed-card-feedback.tsx:74/80) | #e5484d | popover/95@card #141a2a | **4.44** |

(质检原报对应值 4.34/2.62/4.43;mark 项绝对值差 0.3 来自叠层合成模型取法
差异,两算均 <4.5,结论同向。)

## 复现

```bash
SANDBOX=$(mktemp -d /tmp/myia-touch-evidence.XXXXXX)
uv run python .trellis/tasks/10-03-fe-small-batch/evidence/touch-harness/seed.py "$SANDBOX"
node    .trellis/tasks/10-03-fe-small-batch/evidence/touch-harness/bridge.mjs "$SANDBOX" 8797 &
(cd desktop/ui-src && npm run dev) &
/opt/homebrew/opt/python@3.14/bin/python3.14 \
  .trellis/tasks/10-03-fe-small-batch/evidence/touch-harness/shoot.py
# 用毕:停 bridge/vite(本轮已验收 5173/8797 双关、无残留进程)
```
