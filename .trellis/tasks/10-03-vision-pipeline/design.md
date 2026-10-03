# Design:看图入管线(10-03-vision-pipeline)

事实基础 = prd.md 探查结论(含二轮设计级补充)。前置红线:`.trellis/spec/python/index.md`(核心依赖/新模块登记)、`guides`(新增 RPC/配置字段必读 cross-layer)、`domain/yaml-schema.md`(品类 YAML 硬规则)、`security-baseline`(凭据)。引擎层(`src/myia/vision/` 现有四件)本任务**只读复用,不改动其 API**。

## 总体数据流

```
品类 YAML images: 节(开关/限额)          vision.yaml(通道/端点/key,已有)
        │                                        │
fetch(L1/L2: extract img@src → metadata.images)  │
     │  (L3: markdown ![](url) 同域收集,拍板⑥)  │
     ▼                                           │
┌─ 图片处理环(fetch 阶段尾部,逐条,拍板②)──────┴────────┐
│ 下载(限额/SSRF 拒私网/魔法字节校验/超时)               │
│ → 本地 OCR(双引擎,to_thread 池,免费)                │
│ → 可选 VL 情报向 caption(拍板⑧;通道 images.vl;进 BudgetTracker 池) │
│ → metadata.image_ocr / image_caption / image_status    │
│ (图文件临时目录即弃,产物纯文本;失败逐级降级不阻管线)   │
└──────────────┬───────────────────────────────────┘
               ▼
  dedup(不读图,零改动) → analyze(enrich:_render_batch 增 image_ocr/image_caption
  两键 + prompt.json version bump,缓存失效一次) → store(raw 列,零迁移) → push(模板可读)
  桌面 feed 屏:content 摘要下加「图析」行(拍板⑤)
```

## 品类 YAML `images:` 节(schema.py 落法)

品类级 PrivateAttr sidecar(照 `_aggregate` 先例:schema.py:1343 property + `_validate_images_section` 同门校验,错误带字段路径):

```yaml
images:
  enabled: true          # 缺省 false——不开 = 行为与今天完全一致
  max_images: 3          # 每条上限,1-10
  max_per_run: 30        # 每 run 图处理总上限(VL 时长的硬闸)
  min_bytes: 10240       # <10KB 视为图标跳过
  vl: off                # off | local | cloud(拍板③:缺省 off 零影响,启用品类默认 local,cloud 走 token 预算池)
  ocr_engine: vision     # vision | rapidocr(缺省按 vision.yaml)
```

- 源级覆写:`SourceConfig` 本就 `extra="allow"`(schema.py:632),约定同键 `images_max_images` 等平铺参数,装载时无 schema 强校验(与引擎参数同宽容度,文档写明)
- extract 收图:YAML `fields` 配 `image: img@src`(机制现成,fetch_base.py:166 urljoin);多图 `images: [img@src, img@data-src]` 由 extract 的 list 语义自然支持
- 明文凭据与此节无关(纯限额/开关);校验失败零写入同门
- **官方示范(拍板⑨)**:ai-news 活跃 list 源 fields 加 `image: img@src`(同域),品类尾加 images 节(enabled 只 OCR);`vl: local` 以注释示例出现,附本地服务启动指引一句;**改官方插件声明面必同步 golden/fixture**(games 教训在案)

## 新模块:`src/myia/vision/collect.py`(管线侧消费入口)

登记名「vision/collect——管线图片处理环」。公开面:

```python
async def process_item_images(item, *, images_cfg, vision_cfg, budget, proxy_url=None) -> None
    # 就地更新 item.metadata;图 URL 取 item.metadata["images"](list[str],extract 产物)
```

- **下载**:httpx(核心依赖,零新增)async + 超时 10s/图、大小流式截断(超 min(10MB, 配置))、重定向后复核;**SSRF**:解析后 IP 落 `ipaddress.is_private/is_loopback/is_link_local` 拒;**魔法字节** png/jpg/webp/gif 首 8 字节白名单;临时文件落 `tempfile.TemporaryDirectory`(条目级,处理完即弃)
- **OCR**:`run_ocr` 同步阻塞 → `asyncio.to_thread`(信号量 4);`<1000px` 宽的放大配方沿用 ocr.py 内建
- **VL**:`VisionClient.analyze`(async);**并发 1**(本地 GPU 单飞实测定调)、每图超时 45s(180s 是交互模式裕量,管线收紧)、预算池 `budget.can_spend/spend(VisionResult.total_tokens)`(enrich/scoring.py:118 同款,**token 单位零换算已核实**);describe 用**情报向模板**(拍板⑧,collect.py 模块级定义,与 entry.py 交互屏模板两用途两模板):「中文 caption:①图中内容 ②可见文字要点 ③与条目标题的关系 ④数据/图表则读出关键数值」——通用照片向(构图/流派/氛围)不进管线
- **降级矩阵**(全部只写 `metadata.image_status`,绝不抛出阻管线):

| 故障 | 行为 |
|---|---|
| 下载失败/SSRF 拒/格式拒 | 该图跳过,status=`skipped:<原因>`;全部失败→`none` |
| OCR 异常 | `ocr_failed`,条目照常 |
| VL 预算不足 | 只留 OCR 产物,`vl_skipped_budget` |
| VL 超时/通道不可达 | 同上,`vl_skipped_error`;不重试 |
| images 节未开 | 整环零进入,零开销 |

- **挂点**(拍板②默认 fetch 尾部):`_stage_fetch` 条目收集循环内、进 checkpoint 队列**之前**——`_item_checkpoint` 本就含 metadata(pipeline.py:577),图析产物续跑自然可见;content 不进 checkpoint,故产物只挂 metadata 不挂 content

## enrich 拼装与缓存

- `_render_batch`(enrich/__init__.py:363)每条 dict 增两键:`image_ocr`(去行置信度拼纯文本,截 800 字)、`image_caption`(截 300 字);无图条目不带键(payload 不膨胀)
- `enrich/data/prompt.json`:user 模板补一行「条目可含 image_ocr/image_caption 字段,为内容配图的 OCR 文本与视觉描述,评分时与正文同权参考」;**`version` +1**(prompt.py:66 整数即缓存指纹,version 进 enrich-cache 键)——旧缓存整体失效一次,预期内成本,AC 覆盖
- mute/cache/batch 机制零改动;`_apply_scores`/回写零改动

## 存储与呈现

- 产物全在 `metadata` → items.raw 列(pipeline.py:1601)零迁移;专用列不做(观察量级后再议)
- 桌面 feed 屏(feed-screen.tsx:131 content 摘要处):加「图析」行——`image_ocr` 截一行 + 「图」Badge;vitest 用例;无图条目零渲染变化;详情展开(全量 OCR+逐行置信度)= v2(拍板⑩)
- push:模板(pull/base.py:194 item_view 合并 metadata)即刻可读;TG sendPhoto/飞书 img 卡 v1 不做

## 拆屏 runbook(拍板①=整拆时执行;拍板=保留则跳过本节)

拆**四留二**:`image.import/ocr/analyze/status` 四方法+`image.progress/completed` 两事件+`_image_analyze_worker` 全清(entry.py:1880-2210、_HANDLERS、头注释协议表);**保留** `image.config.read/save`(settings 的 VisionForm 依赖,配置入口留在设置屏——它不是明面看图,是配置面)。UI 侧清:`screens/image/` 7 文件、App.tsx 路由、sidebar `/image`、types.ts 对应方法/事件类型;VisionForm 与 vision-form 测试保留。协议测试删四方法用例、留 config 用例。`src/myia/vision/` 与 `vision.yaml` 不动。

## 测试策略

- 单测:collect.py(mock httpx/OCR/VL:降级矩阵逐行、SSRF 拒、限额截断、预算耗尽);schema images 节合法/非法/缺省闭;enrich 拼装快照(有图/无图 payload)
- 协议回归:拆屏后 `test_desktop_sidecar_protocol.py` 四方法删除、config 两方法留
- E2E 冒烟:fixture 品类指向本地 http server(serve 两张真图:一张含文字截图、一张无字渐变),`myia run` 断言 metadata.image_ocr/image_caption/预算 spend/降级路径;断网重跑(OCR 全本地)
- 桌面:vitest feed 图析行 + 拆屏回归;tsc

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 管线时长膨胀(图×VL 13-33s) | max_per_run 硬闸 + VL 并发 1 + 每图 45s 超时 + vl 缺省 off(拍板③已定:启用品类默认 local) |
| 本地 VL 与主人 GPU 争用(实速曾崩到 2.9 tok/s) | 并发 1 + 超时降级留 OCR;vl 失败不重试不阻管线 |
| SSRF/恶意大图 | 私网拒 + 流式截断 + 魔法字节白名单 + 每条/每 run 双限额 |
| enrich 缓存全失效一次 | prompt version bump 预期成本,AC 显式断言新键生效 |
| 并行会话热点冲突(pipeline.py/enrich) | 开工先 rebase;改动追加式;pipeline 挂点单一函数 |
| checkpoint 兼容 | 已核实 metadata 在 payload 内,零改动;产物不挂 content |

## 回滚

品类不开 images 节 = 全量零影响;整任务 revert 即回今天。拆屏部分独立 commit,可单独回滚(保留引擎与 config 协议)。
