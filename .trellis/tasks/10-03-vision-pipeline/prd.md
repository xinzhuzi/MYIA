# PRD:看图入管线 — 采集图片自动 OCR + 视觉分析并入 analyze/enrich

## 背景

主人 2026-10-03 纠偏(原文):「看图 这个功能不是让你在明面上面做的,是让你集合到情报收集,分析这个模块去做的,做到工作流里面,而不是单独一个UI界面放到前台去做呀」

正解:图片理解是**情报管线的一个内部环节**——fetch 抓到的内容里的图片自动 OCR(+可选视觉描述),产物进入条目数据、喂给 analyze/enrich 评分与推送,而不是让用户手动拖图的前台屏。前档 `10-03-image-input`(review)交付的引擎层**全部复用**:双引擎 OCR(`run_ocr`)、本地/云端 VisionClient、vision.yaml/钥匙链配置;本任务改的是**入口与消费方**。

## 探查结论(2026-10-03 本会话实读,file:line 证据)

### 管线骨架与数据形状

- DAG 阶段 `fetch → classify → dedup → analyze → push`(+aggregate 可选):`src/myia/pipeline.py:198`;item 字段 `url/title/source/category/scores/dedup_key/content/metadata`,**无图片字段**;extract 的非保留键全部落 `metadata`(`Item.from_extract`,pipeline.py:403-430)
- `myia run` 是独立子进程(cli.py:721 进程内建 Pipeline),与 sidecar 无关;vision 包可被它直接 import:重依赖全惰性、extras 隔离,sidecar 内已有同款先例(entry.py:188-198)。唯一形态差异:`run_ocr` 同步阻塞(ms~秒级),事件循环内需 to_thread;`VisionClient.analyze` 是协程

### 采集侧(图片 URL 从哪来)

- **机制现成**:`_URL_ATTRIBUTES = {"href","src"}`(fetch_base.py:166)对属性值做 urljoin——品类 YAML 里配 `image: img@src` **今天就能把图 URL 抽进 metadata**,零代码改动;但现有 6 个官方插件无一使用
- L3 crawl4ai 无 extract 时结构化为 markdown 纯文本,图片链接被丢弃(crawl4ai.py:227-249);原始 HTML 不落库
- 无图片型源先例(监控截图/图表页)

### 分析侧(产物给谁吃)

- classify 关键词粗筛**只读 title**(myia_classifier/__init__.py:93)——不动
- enrich 精评拼给模型的是 `{url, title, content[:摘要上限]}` 三键(`_render_batch`,enrich/__init__.py:363-378);prompt 模板 `enrich/data/prompt.json`,**版本进缓存键**——改拼装要动版本指纹
- **预算池现成**:`BudgetTracker` 共享池按 `enrich.budget_per_run` 建立(pipeline.py:949-953),analyze/aggregate 同池;`VisionResult.total_tokens` 可直接 `spend`(scoring.py:118)——VL 调用进预算零新机制

### 存储与推送

- items 表 `raw`(JSON)列承载全部 metadata(pipeline.py:1601-1611),**图析产物进 metadata = 零迁移**;专用列才需迁移(schema v6→v7 机制,sqlite.py:320)
- push 模板(Jinja2 沙箱)可读 item 任意 metadata 字段(push/base.py:194-211)——图析摘要进 metadata 后模板即刻可渲染;TG 现仅 sendMessage、飞书卡无 img 元素(协议可加,v1 不做)

### 品类 YAML 落点

- 品类级新节先例:PrivateAttr sidecar(如 `aggregate`,schema.py:1343/1633 同门校验)——「图片处理」节照此挂;源级可走 `SourceConfig` 的 `extra="allow"`(schema.py:632-658)

### 看图屏与 image.* 协议拆除面(清单,去留待拍板)

- UI:`screens/image/` 7 文件、settings 的 `vision-form.tsx`+引用、`App.tsx` 路由、sidebar `/image` 项、types.ts image.* 六方法+两事件、logs 屏过滤注释
- sidecar:entry.py 六方法族(:1880-2210)+`_HANDLERS` 注册+头注释协议表;`tests/test_vision.py` 对应部分
- **引擎与 vision.yaml 独立于 sidecar,拆屏不牵动**(vision/settings.py 自成一体)

### 设计级补充事实(2026-10-03 二轮实读)

- **checkpoint 本就含 metadata**(`_item_checkpoint`,pipeline.py:577-587,仅 content 不进)——图析产物挂 metadata 即续跑可见,零改动;dedup_key 模板可引用 metadata 字段(schema.py:1397 校验),images 字段与其互不干扰
- **feed 屏现状只渲染 title/url/category/content 两行截断**(feed-screen.tsx:75-132)——「图析」行是纯增量渲染点
- **prompt.json 的 `version` 整数即 enrich 缓存指纹**(prompt.py:25-27/66-69,version 进缓存键)——图析进评分 = version +1,旧缓存整体失效一次,机制现成无需改缓存代码
- enrich 拼装点 `_render_batch` 三键 JSON(enrich/__init__.py:363-378)、mute/cache 零改动即可带新键

## Requirements(v1 定稿级;分支项标注拍板号)

1. **采集**:品类级 `images:` 节(`enabled` 缺省 **false**/`max_images` 3/`max_per_run` 30/`min_bytes` 10KB/`vl: off|local|cloud` 缺省 off/`ocr_engine`),PrivateAttr sidecar 照 `aggregate` 先例同门校验;源级平铺参数覆写(`SourceConfig` extra="allow");extract 配 `img@src` 收图 URL 机制现成;L3 crawl4ai 补收 markdown 图片链接(同域,拍板⑥)
2. **图片处理环**(新模块 `src/myia/vision/collect.py`,fetch 阶段尾部、checkpoint 队列前,拍板②):下载(httpx 流式 10MB 截断、SSRF 拒私网、png/jpg/webp/gif 魔法字节白名单、10s/图)→ 本地 OCR(to_thread 信号量 4,免费)→ 可选 VL(并发 1、45s/图、走 BudgetTracker 共享池 spend total_tokens)→ 产物 `metadata.image_ocr`(去置信度纯文本)/`image_caption`(VL 描述)/`image_status`(降级标记);图文件临时目录即弃
3. **消费**:enrich `_render_batch` 每条增 `image_ocr`(截 800 字)/`image_caption`(截 300 字)两键(无图不带,payload 不膨胀),prompt.json 补图析说明一行 + **version +1**(缓存指纹机制现成,旧缓存失效一次,拍板⑤);classify 粗筛仍 title-only 不动;推送模板经 item_view 即刻可读新字段
4. **降级矩阵**(全部写 image_status 不阻管线):下载失败/SSRF/格式拒→`skipped:<原因>`;OCR 失败→`ocr_failed`;VL 预算不足→`vl_skipped_budget`;VL 超时/通道死→`vl_skipped_error` 不重试;images 未开→整环零进入
5. **呈现**:桌面 feed 屏 content 摘要下加「图析」行 + 「图」Badge(无图条目零渲染变化);TG/飞书原生带图不做
6. **看图屏处置**(拍板①,推荐整拆):**拆四留二**——拆 `image.import/ocr/analyze/status` + 两事件 + worker,留 `image.config.read/save`(settings 的 VisionForm 配置入口保留);UI 清 `screens/image/`、路由、侧栏项;`src/myia/vision/` 与 vision.yaml 引擎层不动
7. **续跑兼容**:`_item_checkpoint` 本就含 metadata(pipeline.py:577)——图析产物续跑可见零改动;产物不挂 content
8. **官方示范(拍板⑨)**:ai-news 活跃 list 源 fields 加 `image: img@src` + 品类 `images:` 节(enabled 只 OCR;`vl: local` 注释示例含本地服务启动指引一句);golden/fixture 同步

## 刻意不做(v1)

- TG sendPhoto / 飞书 img 卡片(推送带原图)
- 图片文件持久化/看图历史;items 专用列迁移(先 metadata)
- classify 阶段读图(粗筛仍 title-only);AGI 式「看图改写标题」
- 图片型源(整页截图监控)= 观察需求后另档
- feed 详情展开(全量 OCR 文本+逐行置信度)= v2(拍板⑩)

## Acceptance Criteria

> 验收标注(2026-10-03 E2E 冒烟,证据索引 `evidence/README.md` 逐 AC 对档):AC1-10 passed;AC11 manual(环路径全量证完,真网当日零 titled 封面如实记录)。

1. ✅ **零影响默认**:品类不开 `images:` 节跑 `myia run`,行为/产物/耗时与今天逐字段一致(ac1-compare.out:HEAD worktree vs 当前树 items 逐字段 IDENTICAL 8v8)
2. ✅ 开启 images 的品类(fixture 本地 http 双图:含字截图+无字渐变):条目 metadata 带 image_ocr(纯文本)与 image_status;feed 屏出现「图析」行与「图」Badge(main-run.json + ac8-04-feed-image-row.png)
3. ✅ `vl: local` 且 8080 在跑:image_caption 生成,total_tokens 实际计入 BudgetTracker(预算耗尽自动降级只 OCR,标记 vl_skipped_budget)(vllocal2-run.json + vlbudget-run.json 行为学证明)
4. ✅ SSRF 与限额:私网图 URL 拒(skipped)、>10MB 流式截断拒、超 max_images/max_per_run 截断——四路径全部只写标记不阻管线(main-info-run.stderr.log + access.log + runlim-run.json)
5. ✅ enrich:payload 快照含 image_ocr/image_caption 两键(无图条目不带);version+1 后旧缓存不复用、新评分生效(llm_requests.jsonl + enrich-run1/3)
6. ✅ 断网重跑:OCR 全本地照常出产物;VL 通道死→vl_skipped_error,条目照常入库推送(socket_connects.jsonl 11/11 loopback + vldead-run.json;替代方法 socket 审计钩子如实注明)
7. ✅ 续跑:fetch 后中断续跑,已处理条目的图析产物在(checkpoint 含 metadata 实证)(resume-run1/2.json:SIGKILL 后续跑 8 条全产物)
8. ✅ 拆屏(拍板①整拆):`/image` 路由/侧栏/screens/image/ 清空,image.import/ocr/analyze/status 与两事件从协议测试删除,image.config.read/save 保留且 VisionForm 正常;tsc/vitest/pytest/cargo 四道全绿(app-headless 4/4 PASS + protocol-image-methods-probe.txt + gates-summary.txt)
9. ✅ prompt 版本 bump 的缓存失效一次性:同条目第二跑命中新缓存(不再重复计费)(enrich-run2.json:enrich_cache_hit=8、mock 0 新请求)
10. ✅ 基线不回归:pytest/vitest/tsc 以开工基线为准(第 0 步记录)(gates-summary.txt:波动 3 起全归因并行会话在途编辑)
11. ⚠️ manual **官方示范生效**:ai-news 示范配置跑通抽图→OCR;golden/fixture 已同步(改官方插件声明面的既定教训)(ainews-run.json:装载+fetch 74+classify 35+push ok、抽图字段命中;当日页面 titled 卡片零封面→真网无 OCR 产物,环路径由本地 fixture 同代码路径全量证完见 AC2/3;golden 面 ai-news 相关 16 passed)

## 已拍板决议(grill Round 1,2026-10-03,主人「按照你的推荐落实」)

1. **看图屏整拆**:UI(屏/路由/侧栏)+ sidecar `image.import/ocr/analyze/status` 四方法与两事件全清;**留** `image.config.read/save` 与设置屏 VisionForm;引擎层与 vision.yaml 不动
2. **挂点 = fetch 阶段尾部**(条目入 checkpoint 队列前,续跑可见已实证)
3. **VL 深度**:schema 缺省 `off`(不开零影响)→ 启用品类默认 `local`(并发 1、45s/图超时降级)→ `cloud` 可选(走 token 预算池,BudgetTracker 已核实为 token 单位零换算)
4. **限额**:3 张/条、30 张/run、<10KB 跳过、10MB 流式截断;域名黑名单不做,观察后再议
5. **enrich v1 就吃图析**:payload 增 image_ocr/image_caption 两键,prompt version+1,旧缓存一次性失效(第二跑起新缓存)
6. **L3 收图同域 only**(跨域广告/追踪像素不收)
7. **排期**:P1,大工作流 dwfrun-0e1749cb 收尾后立即动工(避开 pipeline.py/enrich 热点冲突)
8. **VL describe 模板 = 情报向**(非通用照片向):图中内容、可见文字要点、与条目标题的关系、数据/图表则读关键数值;collect.py 为事实源,与交互屏模板两用途两模板
9. **官方示范 = ai-news 开图**:活跃源 fields 加 `image: img@src`,品类 `images: enabled`(只 OCR);`vl: local` 以注释示例带本地服务启动指引;**改官方插件声明面必同步 golden/fixture**(games 教训)
10. **feed 呈现 v1 = 一行摘要 + 「图」Badge**;详情展开(全量 OCR+逐行置信度)v2 再议
