# Implement:看图入管线(10-03-vision-pipeline)

门禁纪律:验证重定向到文件+显式 `$?`,禁 `| tail` 判活;CI 同款命令 = `uv run --no-sync python -m pytest -q --tb=short` / desktop/ui-src 下 `npm test`、`npm run build`。待拍板项未决时按推荐分支施工,拍板不同只改对应步。

## 0. 前置

- [x] 基线核对:rebase 最新 main(并行会话多,重点 pipeline.py/enrich 冲突面),重跑四道门禁记录当轮基线数字(baseline-head/current.json + gates-summary.txt 在档;AC10 波动 3 起全归因并行会话在途编辑)
- [x] 拍板已完成(2026-10-03 grill Round 1 十条全按推荐,见 prd「已拍板决议」)

## 1. schema:品类 images 节

- [x] `schema.py` PrivateAttr sidecar + `_validate_images_section`(字段路径错误、范围校验、缺省 false)+ fixtures 合法/非法(test_schema.py TestImagesSection 12 用例)
- 验证:定向 pytest tests/test_schema*;回滚点:独立 commit

## 2. vision/collect.py 图片处理环

- [x] 下载(SSRF/魔法字节/流式限额/超时)→ OCR(to_thread 池,信号量 4)→ VL(并发 1、45s、BudgetTracker spend)→ metadata 三键 + 降级矩阵;describe 模板常量落 collect.py
- [x] 单测:降级矩阵逐行、私网拒、限额截断、预算耗尽(mock 全量零外网)(tests/test_vision_collect.py 33 用例)
- 验证:定向 pytest tests/test_vision*;回滚点:独立 commit

## 3. pipeline 挂点(fetch 尾部)

- [x] `_stage_fetch` 条目循环内、checkpoint 队列前调 `process_item_images`(images 节未开零进入);L3 crawl4ai 补 markdown 同域图 URL 收集
- [x] CLI `myia run` 冒烟 fixture:本地 http server 双图(含字截图+无字渐变),断言 metadata 产物与降级路径;断网重跑证 OCR 全本地(E2E:main-run + socket 审计钩子 11/11 loopback)
- 验证:定向 pytest + 手跑 fixture;回滚点:独立 commit

## 4. enrich 吃图析

- [x] `_render_batch` 增 image_ocr(800 字)/image_caption(300 字)两键(无图不带);prompt.json 补一行说明 + version +1(1→2)
- [x] 缓存断言:同条目 bump 后重评(旧缓存不复用)、新键出现在 payload 快照(E2E:enrich-run1/2/3 + llm_requests.jsonl,@p2/@p3 两代缓存键并存)
- 验证:定向 pytest tests/enrich*;回滚点:独立 commit

## 5. 官方示范:ai-news 开图(拍板⑨)

- [x] ai-news 活跃 list 源 fields 加 `image: img@src` + 品类 `images:` 节(enabled 只 OCR;`vl: local` 注释示例含本地服务启动指引一句)
- [x] golden/fixture 同步(games 教训:改官方插件声明面必同步 golden 基件)(E2E AC11:ai-news 相关 16 passed;真网 dry-run 装载/抽图链路通,当日页面零 titled 封面=零 OCR 产物,如实 manual)
- 验证:定向 pytest 该插件 fixture 用例;回滚点:独立 commit

## 6. 桌面:feed 图析行 + 拆屏(拍板①=整拆)

- [x] feed 屏 content 摘要下加「图析」行 + 「图」Badge(image_ocr 截断,无图零变化)+ vitest(feed-screen 2 新用例)
- [x] 拆四留二:entry.py 删 image.import/ocr/analyze/status+两事件+worker,留 image.config.*;协议测试同步;UI 清 screens/image/、路由、侧栏、types.ts 对应段;VisionForm 保留(配置封装迁 settings/vision-api.ts)
- 验证:`npm test` + `npm run build`(tsc 零错)+ 协议 pytest;回滚点:拆屏独立 commit 可单独回滚

## 7. 回归与收尾

- [x] 四道门禁全绿,数字与第 0 步基线核对;`cargo check`(types.ts 变更牵动)(gates-summary.txt:tsc/vitest 113/113/build/cargo 绿,pytest 全套观测过 1930 passed 0 failed)
- [x] spec 登记:python/index.md 增 vision/collect 与 images 节;desktop/sidecar-protocol.md 删四方法留二(该文件系并行会话事实源,动前重读)
- [x] prd AC 1-11 逐条勾(决议已回写 prd「已拍板决议」节)(E2E 10 passed + AC11 manual,证据索引 evidence/README.md)

## 显式不做(与 prd 一致)

push 带原图/图片落库/专用列/classify 读图/图片型源/看图历史——另档另议。
