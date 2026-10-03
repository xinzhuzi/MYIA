# E2E 冒烟证据索引 — 10-03-vision-pipeline(2026-10-03)

验收人:E2E 冒烟员(真机)。方法与结论逐条对应 PRD 验收标准 1-11。

## 环境

- 机:macOS(darwin 25.4.0 arm64);CLI:`desktop/.venv-build`(editable 安装 → 当前工作树,含 vision extras:ocrmac 1.0.1 / rapidocr-onnxruntime / PIL);pytest 用工程 `.venv`
- VL 服:`mlx_vlm.server --model ~/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx --host 127.0.0.1 --port 8080`(E2E 前已在跑,未动;日志 `/private/tmp/qwen3vl_server.log`)
- fixture 服:自写 `server.py`(ThreadingHTTPServer @127.0.0.1:8765:静态双图 + >10MB + <10KB 图 + 私网 URL 对照 + POST /v1/chat/completions mock 精评 LLM,请求体全录 `llm_requests.jsonl`)
- 测试缝(如实声明):`myia.vision.collect._resolve_host` 的 docstring 自述「测试的 monkeypatch 点,零外网」——经 `sitecustomize`(PYTHONPATH 注入)仅对字面量 `127.0.0.1` 返回公网替身 IP 使本地 fixture 图过 SSRF 门;**192.168.0.1 等其余主机走真实解析,SSRF 拒私网路径是真实未打补丁的**(instrument/sitecustomize.py 一并入档)
- 零外联审计:同 sitecustomize 装 `sys.addaudithook`,全 run 记录 socket.connect 事件

## pipeline-runs/(逐 AC)

| 文件 | AC | 断言结果 |
|---|---|---|
| main-run.json + main-info-run.stderr.log | AC2/AC4 | 8 条目:a=ok+image_ocr(3 行纯文本)、b=ok(无字)、c=none(reason=ssrf,192.168.0.1 真实解析被拒)、d=none(reason=too_large,11MB 流式截断)、e=none(reason=too_small)、f/m2 无 image_status 键(零进入)、m1=ok;run status=success |
| access.log | AC4 | m1 五图只请求 i=1/2/3(max_images=3 截断);11 次连接=11 个 HTTP 请求,OCR 自身零连接 |
| socket_connects.jsonl | AC6 | vl-off 全 run 11/11 出站连接全 loopback(127.0.0.1),零外联 |
| vllocal2-run.json + qwen3vl-server-requests.log | AC3 | a/m1 image_caption 生成(①图中内容②可见文字要点…情报向结构,qwen 日志 +122 行实跑 8080);b 无字渐变 qwen3 返回空 content → caption 空、status ok(模型行为,如实记录);首轮 vllocal-run.json:首请求冷启 57s>45s → a=vl_skipped_error(超时降级真实触发) |
| vlbudget-run.json | AC3 | budget_per_run=1:a 出 caption(qwen +32 行,唯一 VL 调用)→ spend 入池;m1=vl_skipped_budget 且 ocr 167 字在(预算耗尽自动降级只 OCR;used 增长的行为学证明——limit=1 下若未记账 can_spend 仍为真) |
| vldead-run.json + vldead-run.stderr.log | AC6 | 通道指 8081(死):a/m1=vl_skipped_error(VL 描述失败 Connection error 警告在 stderr),OCR 产物在,8 条照常入库+stdout digest 推送(count=8,含 image_ocr) |
| runlim-run.json | AC4 | max_per_run=2:a/b 正常,c/d/e/m1=skipped:run_limit,run success |
| enrich-run1.json + llm_requests.jsonl | AC5 | mock LLM 收到的 payload:a/m1 带 image_ocr+image_caption 两键,b/c/d/e/f/m2 无键(payload 不膨胀);评分回填 items 表(scores JSON 在档);git diff:prompt.json version 1→2(HEAD=1 实证) |
| enrich-run2.json | AC9 | 同条目第二跑:analyze skips enrich_cache_hit=8、mock 0 新请求(不再计费),scores 从缓存回填 |
| enrich-run3.json | AC9/AC5 | 副本树(src-v3,PYTHONPATH 优先)prompt version 2→3:cache 全失效,1 新 LLM 请求;enrich_cache 表并存 @p2/@p3 两代键(version 在缓存指纹内的行为学证明) |
| resume-run1/2.json | AC7 | 慢 LLM(60s)中 SIGKILL(runs 表 status=running 可接管)→ 续跑 resumed_from_run_id=1,fetch/classify/dedup skipped(resumed),8 条 checkpoint 条目带全部图析产物过 analyze/push,入库含 scores |
| baseline-head/current.json + ac1-compare.py/.out | AC1 | git worktree@HEAD(无 collect.py)vs 当前树,同品类(e2e-noimg,无 images 节):run JSON 剥时序字段 IDENTICAL、items 表逐字段 IDENTICAL(8v8)、stdout digest 条目集 IDENTICAL |
| ainews-run.json | AC11 | 官方示范真网 dry-run:装载+2 源 fetch 74 条+classify 35+push ok;aihot 20 条抽出(image: img@src 已声明;当日页面 titled 卡片零封面,6 个 img@src 命中均为无 title 卡的头像,被既定零命中规则丢弃 → 当日无 OCR 产物;环路径本身由 fixture 全量证完) |

## app-headless/(AC8,无头法,零开窗零焦点)

法 = yaml-editor 交付的无头 GUI 冒烟法复用:自起 vite(127.0.0.1:5211)+ `.zcode/smoke/bridge.mjs`(sidecar 以**当前源码** `desktop/entry.py serve` 起,冻结二进制早于本任务无 image_ocr 投影故弃用)+ Playwright headless chromium 注入 `__TAURI_INTERNALS__` shim。唯一 shim 注入:store.items 补 `params.db`=fixture 库(真壳由 MYIA_HOME 决定;如实在驱动脚本头注释)。

- ac8-01-sidebar.png:导航 7 项(仪表盘/情报流/源管理/配置编辑/消息/采集日志/设置),无「看图」
- ac8-02-image-route.png:#/image → location.hash 归 `#/`(兜底 Navigate to /),仪表盘渲染
- ac8-03-settings-vision.png:看图配置分区在(VisionForm,通道/引擎字段;image.config.read 经桥实读 200)
- ac8-04-feed-image-row.png:「图」Badge ×2(a+m1)+ 图析单行摘要(MYIA VISION PIPELINE E2E… 全文 title 悬浮)
- protocol-image-methods-probe.txt:拆四留二——image.import/ocr/analyze/status → method_not_found(附 allowed 名单),image.config.read → 正常应答
- ac8-headless-transcript.json:4/4 PASS + console 零错误

## gates-summary.txt(AC10)

tsc/vitest/build/cargo 全绿;pytest 全套在本窗口观测过 1930 passed 0 failed;其余波动(2 处 SKILL.md、1 处 messaging 图标、4 处 games 用例)全部归因并行会话在途编辑(push 三通道/games.yaml),非本任务触点,时间线在档。
