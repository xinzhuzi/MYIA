# Implement:详情页取图 + 五件小修 + 装机(10-03-detail-images)

门禁纪律同前(重定向文件+显式 $?;CI 同款命令)。

## 0. 前置

- [x] 基线核对:rebase 最新 main(news-rss 线持 schema.py 的 EXTRACT_TYPES,只动 images 区);四道门禁记录基线(已知外来红:test_skill_doc 枚举表缺 rss,勿修)

## 1. Python 侧(单实现者,collect.py 归一避免撞文件)

- [x] schema images 节 +detail_fetch/+detail_max_items + fixtures
- [x] collect.py:detail_fetch_images + _html_image_urls + ImageRunState.detail 配额;小修:SSRF 钉扎/redirect 强制、(与挂点一起)proxy 传递
- [x] pipeline.py 挂点接线 + secrets.py keychain 删旧建新
- [x] 单测全量(mock 零外网;keychain 用探针名)
- 验证:定向 pytest;回滚点:独立 commit

## 2. 桌面小修 + 示范

- [x] types.ts:522 注释、logs/api.ts:147 ts 复原 + vitest
- [x] ai-news 示范:cocoloop 源 images_detail_fetch: true + golden 同步
- 验证:npm test/build 定向;回滚点:独立 commit

## 3. 门禁四道 ≤3 轮(外来红除外)

## 4. 独立复查(只读)→ 中高危修复循环 ≤2 轮 → 复跑带尾部

## 5. spec 登记:python/index.md images 节新字段 + secrets 行为注记

## 6. 冒烟:fixture E2E(detail 链全断言)+ cocoloop 真网 best-effort + keychain 探针往返 + 无头 GUI(旧断言回归)

## 7. 装机:tauri 构建新版 → 静默换装(非前台才动;旧版 /tmp 备份)→ open -g 后台拉起验证

## 8. 终检落档:两任务(detail-images/image-fix-followups)勾档置 review、密钥扫描、本地提交(不 push)、leftovers(8080 自启/推送时机)
