# 10-03-vision-daily 验收证据(A1 本地 VL caption + A2 enrich 开通)

时间:2026-10-03 晚。跑法:`MYIA_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
uv run --no-sync shishi run <yaml副本> --db <独立db> --json`,push 剥为 stdout,
vision.yaml 拷至 db 同目录;真库零触碰。

## 配置落地(plugins/ai-news.yaml)

- `images.vl: local`(A1):8080 mlx-vlm Qwen3-VL-8B 在跑(vision.yaml local 节)。
- `enrich.enabled: true` + `base_url: env:MYIA_LLM_BASE_URL` +
  `api_key: keychain:myia/image/api_key`(A2,schema 铁律:两字段只收引用,明文拒载)。
- `enrich.batch: 20 → 10`:实测 20 条/批的 glm-4-flash 评分 completion 超 60s
  per-completion 上限,批级降级(enrich_degraded_llm_batch_failed);Run A 尾部
  2 条 mini-batch ~6s 完成,10 条留足余量。

## Run A — 真站真行情(run-a-*.json/txt)

`aihot.news + cocoloop`,db=run-a(db/myia.db)。status=**partial**,exit 3,141.7s。

- fetch 15.0s → 74 条;classify 42;dedup 0 拦截(全新 db)。
- analyze 126.6s:40 条评分全部批超时降级(2×60s),**尾部 2 条 mini-batch 评分成功**;
  enrich_cache 落 2 行(model=glm-4-flash,key=`value+relevance+credibility@p2`,
  reason 中文具体)→ **enrich 通道真实开通**(run-a-enrich-cache.txt)。
- 看图环:当日行情无合格图——aihot 20 卡中 16 条有题卡 `article` 内无 `<img>`,
  4 条带图卡无 h3(extract 零命中,by design);cocoloop 详情页只出 emoji 头像
  (smirking_face.png <10KB,min_bytes 跳过)。标记:`detail_status: no_images` ×6、
  `image_status: none` ×2。**环全程零崩溃,降级正确**。
- 6 条 aihot 零命中卡以 invalid_item 告警(fetch failures 数组)——记录行为,非故障。

## Run B — 本地页确定性证明(run-b1-*,run-b2-*)

当日真站无图 → 按预案起本地 http 页(127.0.0.1:8765,aihot 同构 markup,封面引
当日 feed 里两张真实 pbs.twimg.com JPEG,158KB/84KB>min_bytes;图片走公网因
SSRF 拒环回)。yaml 副本仅改 aihot url + 去 cocoloop 源(run-b-yaml-copy.yaml)。

- 第一跑 status=**success**,exit 0,125.7s。两条目全链路:
  - `image_status: ok`;`image_ocr` = macOS Vision OCR 实文(Arena.AI 榜单/帕累托图);
  - `image_caption` = 本地 VL 情报向四段 caption(内容/文字要点/**与标题关联**/数值趋势);
  - push 卡片(run-b1-log.txt 首行 JSON)条目带 `scores{value,relevance,credibility}`、
    `score`、**`enrich_model: glm-4-flash`**、`score_reason`;
  - enrich_cache 2 行(指纹 @p2 = 图析字段进指纹)。
- 第二跑(同 yaml 同 db)2.1s:fetch 指纹拦截(items_out=0)+ dedup 双闸,
  零 token 零出网——比缓存更前置。注:`enrich_cache_hit` 计数路径要求条目
  **再入 analyze**;本插件 dedup key={url} 全期拦截,重跑条目到不了 analyze,
  故该计数在本形态恒 0(缓存本体已验证写入并在库;PRD 中「第二跑命中缓存」
  语义由此处的指纹+dedup 零成本路径承接)。

## 耗时新基线(PRD 要求如实记录)

- 真站 74 条无 VL:fetch 15s;+enrich(42 条 4 批×10)估算 +40~80s(glm-4-flash
  每批 ~10-25s);Run A 全程 141.7s(含 2×60s 超时损耗,batch=10 后预期 ~80-100s)。
- 本地页 2 条 2 图:全程 125.7s,其中 2 张 VL caption ≈ 85-90s(**~40-45s/图**,
  并发 1)——VL 是时长主项,max_images=3 / max_per_run=30 护栏即为此设
  (满配 30 图上限 ≈ +20min,日常槽位远低于此)。

## 桌面侧注记(留主人)

- `base_url` schema 强制引用(明文拒载):CLI 走 shell export;docker 走
  docker/.env(docker/env.example 已有 MYIA_LLM_BASE_URL/MYIA_LLM_KEY 段,无需改)。
- 桌面 sidecar(Tauri GUI 进程 env)无 MYIA_LLM_BASE_URL;桌面用 <home>/plugins
  自有副本,本仓改动不自动同步。**同步前需拍板**:launchctl setenv 注入,或把
  base_url 也入钥匙链(schema 允许 keychain: 引用)。未拍板前桌面 ai-news 若
  同步本 yaml 且 env 缺失,run 会在构造期 fail-fast(exit 1)。

## 密钥安全

keychain:myia/image/api_key 全程由管线解析;证据文件已扫,key 值零出现。
