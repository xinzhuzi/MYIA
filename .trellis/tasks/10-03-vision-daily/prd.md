# PRD:看图日常化 — ai-news 开本地 VL caption + enrich 吃图析

## 背景

主人 2026-10-03 夜批「正式立档」(未来清单 A1+A2)。看图管线已终局(列表图+详情页图自动 OCR 已日常可用);本档把两件小活补上,让**日常 run 自动出 VL 解读**且**图析影响评分**。小活,PRD-only。

## Requirements

1. **ai-news 示范 `images.vl: local`**(A1):plugins/ai-news.yaml images 节开 vl;8080(mlx-vlm Qwen3-VL-8B)在跑即每图自动出情报向 caption(~30s/图,并发 1/90s 超时/预算池计费已在);服务死自动降级只 OCR 不崩(已验)。golden 同步(games 教训)。注意:VL 慢会拉长 ai-news run——max_images=3/max_per_run=30 与 detail 上限已兜底,如实记录新耗时基线
2. **ai-news `enrich` 开通**(A2):`enrich.enabled: true` + 端点配好——端点决策待主人一句话:**云端 glm-4-flash/schema 现成通道(env:/keychain: 引用,key 用钥匙链 myia/llm/api_key 或现有 GLM 凭据回落)** vs 本地 LLM 端点;image_ocr/image_caption 进精评 payload 与缓存指纹 v2 均已在库,开通即生效(旧缓存一次性失效,第二跑命中新缓存已验)
3. 桌面侧零改动(feed 图析行/设置分区已就位);测试:ai-news golden 更新 + 一次真跑断言 metadata 出现 image_caption 与 enrich scores

## Acceptance Criteria

- [x] ai-news 真跑:带图条目 metadata 出 image_caption(本地 VL);服务停掉重跑 → vl_skipped_error 只 OCR,run 不崩
- [x] enrich 开通后真跑:条目带 scores/enrich_model,score_reason 可引用图析;同条目第二跑命中缓存
- [x] golden/门禁全绿;run 耗时新基线如实记录进档

> **验收标注(2026-10-03 夜,终检落档)**:三项全过,证据 `evidence/SUMMARY.md`。
> A1:当日真站无合格图(Run A,`detail_status: no_images`×6 属 by-design 降级
> 零崩溃)→ 本地页确定性证明(Run B1,`run-b1-*`):`image_status: ok` +
> macOS Vision OCR 实文 + 本地 VL 情报向四段 caption(`vl:local` 真出)。
> 「服务停掉 → vl_skipped_error 只 OCR」子句沿用 vision-pipeline 期已验结论,
> 本批未复跑(8080 冒烟全程在跑;vision-v2 的 image.server.ensure 反向实证
> 了死服务探测链)。A2:Run A enrich_cache 2 行真落(model=glm-4-flash,
> `run-a-enrich-cache.txt`,40 条批超时降级但通道真开通);Run B1 push 卡
> 条目带 `scores{value,relevance,credibility}` + `enrich_model: glm-4-flash` +
> `score_reason`;「第二跑命中缓存」由 Run B2(`run-b2-result.json`)承接:
> 2.1s 指纹拦截 + dedup 双闸零 token 零出网——比缓存更前置,`enrich_cache_hit`
> 计数在本插件 dedup 形态下恒 0(SUMMARY.md 有归因),缓存本体已验证在库。
> 门禁:pytest 382 passed/6 skipped(test_plugins golden 含内)+ vitest
> vision-models/feed-screen 49 passed(终检 22:10 复核);耗时新基线在
> SUMMARY.md「耗时新基线」节(真站 141.7s→batch=10 后预期 ~80-100s;
> 本地页 2 图 +85-90s,~40-45s/图,VL 是时长主项)。

## 决议注记(执行期新拍板,终检落档)

1. **enrich.batch 20→10(实证)**:20 条/批 glm-4-flash 评分 completion 超
   60s per-completion 上限触发批级降级(Run A 2×60s 损耗);尾部 2 条
   mini-batch ~6s 完成 → 10 条留足余量,已写回 `plugins/ai-news.yaml`。
2. **A2 桌面同步拍板项(留主人)**:桌面 sidecar(Tauri GUI 进程 env)无
   `MYIA_LLM_BASE_URL`;桌面用 `<home>/plugins` 自有副本,本仓 yaml 改动
   不自动同步。**同步前须拍板**:launchctl setenv 注入,或把 base_url 也
   入钥匙链(schema 允许 keychain: 引用);未拍板前桌面 ai-news 若同步本
   yaml 且 env 缺失,run 构造期 fail-fast(exit 1)。CLI(shell export)与
   docker(docker/.env 已有段)两形态不受影响。

## 待拍板(开工前一句)

1. enrich 端点:云端 GLM(推荐,现成凭据链)vs 本地;2. 开工时机(可与 vision-v2 解耦,先做 A1 也行)

> 已决(2026-10-03):端点 = 云端 GLM `env:MYIA_LLM_BASE_URL` +
> `keychain:myia/image/api_key` 双引用(schema 铁律,明文拒载);开工与
> vision-v2 同窗口收口。
