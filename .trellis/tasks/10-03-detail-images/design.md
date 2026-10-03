# Design:详情页取图(10-03-detail-images)

复用契约见 prd 探查节。engine 侧零改动;改动面:schema.py(images 节两字段)、collect.py(detail 环 + 小修两件)、pipeline.py(挂点调用一行)、secrets.py(keychain 删旧建新)、types.ts 注释、logs/api.ts、ai-news.yaml 示范、配套测试。

## 数据流(fetch 尾部,同一挂点内)

```
_stage_fetch 条目循环(每条):
  ①品类/源级 images 节归并(_effective_config 现成)
  ②detail_fetch 开 且 条目 metadata 无 image/images 键 且 run 级 detail 配额未尽
      → detail_fetch_images(item, source, cfg) :引擎链抓 item.url(10s)
      → HTML 同域 <img> regex 收集(语义照抄 markdown_image_urls:hostname 全等、
        scheme http/https、去重保序;跳过 min_bytes 由 ring 管)
      → 写 metadata.images;metadata.detail_status = ok(n=N)/failed:<原因>/no_images
      → 串行,每请求间 sleep ≥1s(礼貌,自管)
  ③process_item_images 环(现成,不动)
```

## 模块与接口(collect.py 增量)

```python
async def detail_fetch_images(item, *, source_cfg, images_cfg, proxy_url=None) -> None
    # 就地写 metadata.images / detail_status;失败只标记不抛
```

- 引擎复用:`fetch_source()` 按源 engine hint;对 SPA 返回空 HTML → 自然 no_images(不重试)
- 同域收集器:`_html_image_urls(html, base_url)`(regex `<img[^>]+src=`,协议相对 urljoin;同域判定抽公共小函数与 markdown 版共用)
- run 级配额:`ImageRunState` 增 detail_remaining 字段(构造=detail_max_items,take 同款)

## 小修落点(同场)

1. **keychain 删旧建新**(secrets.py):set 更新路径遇 -25244/errSecAuthFailed → 先 delete 旧项再 add(窗口极小,值相同无损);测试用探针名 myia/image/probe_key 真钥匙串往返两次
2. **proxy 传递**(pipeline.py 挂点):源级代理解析(既有 proxy 节)传入 `process_item_images(proxy_url=...)` 与 detail 环
3. **SSRF 加固**(collect.py):下载连接改「先 resolve 钉扎 IP 再连」(httpx transport 自定义或连接前校验+Host 头保留,取简:连接层 stream 后复核远端 IP 即拒);注入 client 统一强制 follow_redirects=False(pipeline 注释随之改实)
4. types.ts:522 注释删 env: 半句;logs/api.ts:147 ts 复原 event.ts

## 测试

- 单测:detail 环(mock 引擎 HTML:有图/无图/超时/配额;同域过滤;min_bytes 交接)、_html_image_urls、keychain 探针往返、SSRF 钉扎/redirect 强制、proxy 传抵
- E2E fixture:本地 http 列表页(条目无图)+ 两个详情页(一有字图一 404)→ image_ocr 经 detail 链落、失败页 detail_status、截断生效
- 真网:cocoloop detail_fetch 真 run best-effort(如实)
- 回归:pytest/vitest/tsc/cargo 四道;golden 同步

## 风险

| 风险 | 缓解 |
|---|---|
| 追抓放大请求量(礼貌/反爬) | run 级 N≤10 缺省 + 串行 + 1s 间隔 + 10s 超时;cocoloop robots 已允许 /t/ |
| 并行会话热点(schema.py/pipeline.py/collect.py) | 开工 rebase;追加式;news-rss 线持 schema(EXTRACT_TYPES rss)——只动 images 节区,冲突可解 |
| keychain 删旧建新窗口 | 值相同重写;测试探针名;真 key 不碰 |
| SPA 页白抓 | 引擎链结果零 img → no_images 单次成本 10s,N 上限兜底 |

## 回滚

detail_fetch 缺省 false 全量零影响;小修各自独立测试;整任务 revert 即回。
