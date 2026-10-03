# cocoloop 真网 best-effort 产物(10-03-detail-images AC4,2026-10-03 晚)

## run 概况(cocoloop-run.json)

- 命令:`shishi run --json --db myia.db --once ai-news-cocoloop.yaml`(build venv,当前源码)
- status=**success**,duration 18.6s,零 failures
- fetch 60 条(2 页 /latest)→ classify 留 33(27 条 classify_unmatched)→ 33 条全量入库,push=stdout
- 副本摆位:`/tmp/myia-di-realnet/{myia.db, vision.yaml(ocr.engine_default=vision)}` 同目录;
  push 剥离 feishu_card → stdout;仅留 cocoloop 源(aihot 与本 AC 无关,砍掉控时长)
- 源级 `images_detail_fetch: true`(品类 images 节未写 detail 键,缺省 false 零影响其余)

## detail 环实跑结果(页面行情,如实)

- **7 条**可见 detail_status 标记(1× ok:n=3,6× no_images)。detail 配额缺省 10:
  追抓挂点在 fetch 尾部、classify **之前**,60 条里按序取前 10 无图条目——其中 3 条
  被 classify 丢弃,其标记随条目一起出列(设计行为,故终局可见 7 个)。
- 唯一 ok:n=3 的条目(「围观新模型评测…」/t/topic/20916)收到的 3 张同域图全是
  **Discourse emoji**(`/images/emoji/twitter/{rofl,joy,smirking_face}.png?v=15`,
  实测 1095–1294 字节)——全部低于 min_bytes=10240 被环拒(too_small)→
  image_status=none,**当日无 OCR 产物**。
- 结论(页面行情性):当日 cocoloop 热帖页静态 HTML 内可收的同域 <img> 仅 emoji
  (正文配图走 JS 懒加载/上传组件,静态链不可见,与 PRD「刻意不做」的 L3 边界一致);
  无内容图如实注明,detail 环本身的行为(选条目、同域收集、礼貌、降级)全链实证。
- 佐证:run duration 18.6s(含 10 次追抓的串行 ≥1s 间隔);emoji 尺寸 curl 实测在档。

## 复现

```
cd /tmp/myia-di-realnet && <build-venv>/bin/shishi run --json --db myia.db --once ai-news-cocoloop.yaml
sqlite3 myia.db "SELECT json_extract(raw,'$.detail_status'), count(*) FROM items GROUP BY 1"
# no_images×6, ok:n=3×1, NULL×26
```
