# E2E 冒烟证据索引 — 10-03-detail-images(2026-10-03 晚)

验收人:E2E 冒烟员(真机,全程后台无头,零开窗零焦点)。逐条对应 PRD 验收 1-6(AC7 装机留下一阶段;AC8=无头 GUI 回归,本档 8 条口径)。

## 环境

- 机:macOS(darwin 25.4.0 arm64);CLI:`desktop/.venv-build/bin/shishi`(editable → 当前工作树,含 vision extras:ocrmac 1.0.1 / PIL);pytest 用工程 `.venv`
- fixture 服:自写 `server.py`(ThreadingHTTPServer @127.0.0.1:8765;列表页 5 card 零 `<img>` + 详情页 a/c/d 静态档 + b 缺席 404;逐请求 `access.log`)
- 出网形态(如实):本机 macOS 系统代理(Clash @127.0.0.1:7897)被 httpx trust_env 拾取——全部 HTTP(含 fixture 拨号)经它;**socket 审计 9/9 连接仍全 loopback**;条目 e 的 connection-refused 因此呈现为代理应答 `failed:http_502`(失败标记语义不变)
- 测试缝(如实声明):`sitecustomize.py`(PYTHONPATH 注入)仅对字面量 `127.0.0.1` 在 `_resolve_host`(docstring 自述测试缝)返回公网替身 IP,使本地 fixture 过 SSRF 门;其余主机真实解析。同 hook 装 `sys.addaudithook` 全程记录 `socket.connect`
- VL:品类 `vl: "off"`(本任务 OCR 链;8080 未动,装机阶段如需 VL 再验)

## fixture-runs/(AC1/AC2/AC3;断言汇总 `assertions.txt` 16/16 PASS)

四跑独立 db(`runs/{main,max1,nodefault,head-nodefault}`,vision.yaml 同目录):

| 跑 | yaml | 结果 |
|---|---|---|
| main | e2e-detail.yaml(detail_fetch:true,max 10) | a=**ok:n=2**+`image_ocr`("MYIA DETAIL IMAGES E2E
DETAIL PAGE OCR 2026
详情页取图链测试"——文案仅存于 detail 页图片,溯源唯一)+image_status=ok;b(404)=failed:http_404;c(零img)=no_images;d(仅跨域img)=no_images(同域过滤);e(不可达)=failed:http_502;run success,5/5 入库(`main-myia.db`,失败页带标记照常入库) |
| max1 | e2e-detail-max1.yaml(max_items:**1**) | 仅 a 追抓(ok:n=2),b/c/d/e **零标记**;access.log 该波次详情页 GET 恰 1 次(截断生效) |
| nodefault | e2e-nodefault.yaml(**不写 detail 键**) | 当前树:零 detail_status、零详情页 GET(缺省 false 零进入) |
| head-nodefault | 同上,`git worktree`@关前(PYTHONPATH 指向,PID 内实证 `has detail_fetch_images: False`) | 与 nodefault 逐字段对比:`ac1-compare.out` 三项全 **IDENTICAL**(run JSON 剥时序/items 表逐字段/stdout digest) |

旁证:`main-access.log` 追抓串行间隔 b→c 1.381s、c→d 1.004s(≥1s 礼貌);`socket_connects.jsonl` 9 连接 ↔ 恰 9 个 HTTP 请求(robots+index+5 详情+2 图),**OCR 自身零连接**(AC3 断网替代证据:OCR 路径零外联);跨域/data: URL 零请求(收集期即滤)。

## realnet/(AC4 cocoloop 真网 best-effort)

`ai-news-cocoloop.yaml`(ai-news 副本:仅 cocoloop 源、push 剥离→stdout、db+vision.yaml 同目录)+ `cocoloop-run.json`:success,60 fetch→33 入库,detail 环真跑 10 次(7 标记可见,3 随 classify 丢弃条目出列);当日唯一 ok:n=3 条目收到的全是 Discourse emoji(1.0–1.3KB,<min_bytes 全拒)→ **无 OCR 产物,页面行情如实注明**(详见 `products.md`)。

## smallfix/(AC5 五件小修实证)

- `secrets-keychain-probe.txt`:真钥匙串探针 `myia/image/probe_key` **两次保存往返 PASSED**(第二次=既有项更新路径,-25244 场景);全套 51 passed 1 skipped;mock 删旧建新回归(TestSecretCrud)在内
- `ssrf-redirect-proxy.txt`:9/9 PASSED——注入 client `follow_redirects=True` 不能绕逐跳复核、下载/detail 两路连接层 rebinding 拒、`proxy_url` 挂载、pool 源环+追抓骑池 client、本地代理出口不误杀
- `types-logs-diff.txt`:types.ts 注释改实(`api_key 只收 keychain: 引用(env: 同拒)`,现值 :582);logs/api.ts completed 行 `ts: event.ts` 复原(现值 :147)

## gates/(AC6)

四道全绿(命令与输出在档):pytest 全量 **2466 passed / 0 failed**(19 skipped 环境条件);vitest 15 files/**153 passed**;`npm run build`(tsc -b + vite)零错;`cargo check --locked` Finished。ai-news 引用用例(test_plugins/test_classify/test_store_dedup)228 passed 6 skipped。PRD 预注外来红 `test_skill_doc rss` **本轮未复现**(并行线已修),如实注明。

## app-headless/(AC8 无头 GUI 回归,零开窗)

法 = yaml-editor 无头法复用:vite dev(127.0.0.1:5221)+ `bridge-di.mjs`(sidecar=**当前源码** `desktop/entry.py serve`,desktop/.venv-build)+ Playwright headless chromium + `__TAURI_INTERNALS__` shim;唯一注入:store.items 补 `params.db`=fixture 库(见驱动脚本头注;脚本在 `/tmp/myia-di-e2e/di-smoke-drive.cjs`,与 vision-pipeline 档同款)。

- `ac8-01-sidebar.png`:导航 7 项,无「看图」(拆屏旧断言)
- `ac8-02-image-route.png`:#/image → `#/` 兜底重定向(旧断言)
- `ac8-03-feed-image-row.png`:「图」Badge ×1 + 图析行渲染,OCR 文本「MYIA DETAIL IMAGES E2E…」来自 **detail 链产物库**(runs/main/myia.db)
- `ac8-headless-transcript.json`:3/3 PASS + console 零错误
