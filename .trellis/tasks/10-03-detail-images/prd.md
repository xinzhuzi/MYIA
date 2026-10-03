# PRD:详情页取图 — 列表抓完追抓条目详情页,收内容图进识图环

## 背景

主人 2026-10-03 批准(/workflow「按照你的建议做完」)。vision-pipeline 真网补验的结构性结论:列表源页面当下无内容图(aihot titled 卡零封面、cocoloop 列表/热帖仅头像与 emoji),识图机制就位但无图可识。本任务补「详情页取图」:fetch 阶段后,对无图条目追抓其详情页,同域收集内容图,喂进**同一个** collect 识图环(OCR+可选 VL)——日常采集由此真出图。**同场执行**:image-fix-followups 五件小修、装机换新(一次工作流收口)。

## 探查与复用结论(2026-10-03 本会话实读)

- collect.py 现成公开面:`process_item_images(item, *, images_cfg, vision_cfg, budget, proxy_url, client)` 就地更新 metadata 三键;`markdown_image_urls(markdown, base_url)` 的同域判定语义(hostname 全等,从严)可直接复用于 HTML `<img>` 收集(collect.py:137-165)
- 挂点先例:`_process_item_images_ring` 在 `_stage_fetch` 条目入 checkpoint 队列前调用(pipeline.py:1571-1612);detail 追抓插在同一位置、ring 之前
- 引擎链:`fetch_source()`(registry.py)可按源配置直接复用抓详情页;礼貌间隔自管(串行 + ≥1s)
- 源级覆写通道现成:`_SOURCE_BOOL_OVERRIDES` 等平铺键机制(collect.py:358-372),detail 开关加同款映射
- 真网底:cocoloop 帖子页服务端渲染可静态抓(列表页 125 img 实测);aihot 文章页是 JS-SPA,静态链抓不到图(不浪费请求,示范不开在它身上)
- 五件小修档:`10-03-image-fix-followups/prd.md`(keychain -2524​4 删旧建新 / types.ts 注释 / logs ts 残留 / proxy 传递 / SSRF 钉扎+redirect 强制)

## Requirements(v1)

1. **schema**:品类 `images:` 节增 `detail_fetch: false`(缺省)与 `detail_max_items: 10`(每 run 追抓条目上限,1-50);源级平铺覆写 `images_detail_fetch/images_detail_max_items` 同款通道
2. **追抓逻辑**(collect.py 新函数,detail 环):fetch 尾部、ring 之前——选本轮**无图**(metadata 无 image/images 键)条目,按管线顺序取前 `detail_max_items` 个;逐条用源引擎链抓其 `url`,HTML 同域收 `<img>`(regex,同域语义与去重保序照抄 markdown_image_urls;跳过 avatar/emoji 类路径启发式不做,交给 min_bytes);**串行 + 每请求 ≥1s 间隔**(礼貌);抓到的 URL 写回 `metadata.images` 后进同一 ring
3. **降级**:追抓失败/超时(10s/页)→ `metadata.detail_status = failed:<原因>`,不阻管线;detail_fetch 未开零进入;列表页已带图的条目不追抓
4. **示范**:cocoloop 源开 `images_detail_fetch: true`(品类缺省 false 维持零影响);golden/fixture 同步(games 教训)
5. **五件小修**(同场):keychain 更新改「删旧建新」路径(-25244 绕过,探针名测试不碰真 key)、types.ts:522 注释改实、logs/api.ts:147 ts 复原、collect 环把源级 proxy 传进 `proxy_url`、SSRF 加固(IP 钉扎或连接层校验 + 注入 client 强制 follow_redirects=False + 测试)
6. **装机**:全绿后 tauri 构建新版,静默换装(不抢前台,旧版 /tmp 备份),open -g 后台拉起

## 刻意不做

- JS 渲染页取图(L3 浏览器链,待 crawl4ai 实装后自然获得);图片懒加载 data-src 深挖;8080 自启(v2 代管);feed 详情展开(v2)

## Acceptance Criteria

1. ✅ 零影响默认:detail_fetch 缺省 false,行为/产物与关前逐字段一致(2026-10-03 evidence/fixture-runs:nodefault vs head-nodefault(git worktree@关前)`ac1-compare.out` 三项全 IDENTICAL——run JSON 剥时序/items 表逐字段/stdout digest)
2. ✅ fixture E2E:本地 http 列表页(无图)+ 详情页(带字图)→ 条目经 detail 链出 image_ocr/detail 链产物;detail_max_items 截断生效(main 跑:a=ok:n=2 + image_ocr 文案仅存于 detail 页图片(溯源唯一);max1 跑:b/c/d/e 零标记,access.log 详情页 GET 恰 1 次;assertions.txt 16/16 PASS)
3. ✅ 追抓失败页 → detail_status 标记,条目照常入库;断网时 detail 环零外联崩溃(b(404)=failed:http_404、e(不可达)=failed:http_502 均照常入库 5/5;socket 审计 9 连接全 loopback、OCR 自身零连接)
4. ✅(行情性注记)cocoloop 真网 best-effort:开 detail_fetch 跑真 run,有图出产物、无图如实注明(页面行情性)——realnet/cocoloop-run.json:success,60 fetch→33 入库,detail 环真跑 10 次;**当日唯一 ok:n=3 条目收到的全是 Discourse emoji(1.0–1.3KB,<min_bytes 全拒)→ 无 OCR 产物,页面行情如实注明**(详见 realnet/products.md;页面内容随行情波动,本 AC 按约定以「真跑通过+无图如实注明」收口)
5. ✅ 五件小修各自回归测试过:keychain 探针名两次保存往返、types/logs 文案复原、proxy_url 传抵断言、SSRF 钉扎+redirect 强制用例(smallfix/:secrets-keychain-probe.txt 真钥匙串探针两次保存往返 PASSED + 全套 51 passed 1 skipped;ssrf-redirect-proxy.txt 9/9 PASSED;types-logs-diff.txt 注释改实+ts 复原)
6. ✅ golden/fixture 同步后 ai-news 相关用例全绿;门禁四道全绿(已知外来红 test_skill_doc rss 除外,如实注明)——gates/:pytest 全量 2466 passed/0 failed(19 skipped 环境条件)、vitest 15 files/153 passed、npm run build 零错、cargo check Finished;ai-news 用例 228 passed 6 skipped;PRD 预注外来红 test_skill_doc rss **本轮未复现**(并行线已修),如实注明
7. ✅ 装机:新版含 feed 图析行、无看图屏;后台拉起未抢前台;旧版备份 /tmp(install/install-summary.txt + check.jsonl AC7 passed:ui build/tauri build exit 0;换装全程 frontmost=Finder 未抢前台;MYIA pid 10119 存活>10s;无头三断言全 PASS(侧栏无看图/feed 图析行/settings 看图分区);备份 /tmp/世事.app.bak-pre-detail-1003)

## 决议(主人授权「按建议做完」,以下为自决项)

串行+1s 间隔(不用源 qps 语义,文档如实);HTML regex 收图不做 DOM 解析(轻量,坏 HTML 容忍);avatar/emoji 不做启发式(min_bytes 兜底);detail 超时 10s/页。
