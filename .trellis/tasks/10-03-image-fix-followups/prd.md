# PRD:看图遗留小修(keychain 更新 + proxy/SSRF 加固 + 零碎)

## 背景

两轮看图交付(10-03-image-input 引擎与交互、10-03-vision-pipeline 管线集成与拆屏)后的遗留清单。2026-10-03 vision-pipeline 终检时刷新:旧条目 2(heic TimeoutExpired)已随拆屏**删除代码而过期**;旧条目 3 的 ocr_engine 半条已随 ImageAnalyzeOutcome 删除而过期;新增独立复查的 3 条 low。

## 缺陷清单(全部带证据)

1. **钥匙串既有项跨进程更新失败(P2,冒烟实测)**:sidecar 对**已存在**的钥匙串项写回(myia/image/api_key)报 `keychain_operation_failed`(-25244 需 GUI 授权);新建路径正常。影响:VisionForm 换 key 第二次保存失败。证据:`10-03-image-input/evidence/protocol-e2e2-summary.md`。方向:删旧建新绕过或 security -U 路径核实;守 `src/myia/secrets.py` 既有约定。(✅ 2026-10-03 已修:set_secret 既有项写入失败回落删旧建新+双 mock 回归入 TestSecretCrud;跨进程真机往返验收留装机档)
2. **types.ts:522 注释漂移(low)**:VisionConfig 注释称「api_key 只收 keychain:/env: 引用」,Python 侧 `env:` 同样拒载(vision/settings.py 只收 keychain:)——注释改准确。(旧 ocr_engine 字段半条已随拆屏删除,过期)(✅ 2026-10-03 已修:注释改实「api_key 只收 keychain: 引用(env: 同拒)」,见 detail-images evidence/smallfix/types-logs-diff.txt)
3. **logs/api.ts:147 completed 行 ts 空串(low)**:`ts: event.ts` 被改为 `ts: ""` 编辑残留;类型仍声明 `ts: string`,无 UI 可见影响但数据不一致。证据:vision-pipeline 复查发现,git diff HEAD 在案。(✅ 2026-10-03 已修:completed 行 `ts: event.ts` 复原,见 detail-images evidence/smallfix/types-logs-diff.txt)
4. **图片下载绕过 proxy pools(low)**:`collect.process_item_images` 有 `proxy_url` 参数但 `_process_item_images_ring` 从不传(pipeline.py:1571-1612)——`pool:` 源的配图直连下载,代理出网环境全降级为优雅跳过;ai-news 无 pool 不受影响。方向:把源级代理解析传下去。(✅ 2026-10-03 已修:pipeline.py `_ring_proxy_for_source` 把 `pool:` 源解析为池共享代理 client,下载与 detail 追抓都骑池出网;evidence/smallfix/ssrf-redirect-proxy.txt 9/9 含 pool 源环+追抓骑池 client 用例)
5. **SSRF 两处加固面(low,实现符合设计文本属加固)**:①DNS rebinding TOCTOU——`_host_is_public` 先校验、httpx 连接时独立再解析,可两次解析间切 IP 绕私网拒(collect.py:195-205);②注入 client 的 `follow_redirects` 不受控,pipeline.py:1585-1589 注释「由 collect 层保证」不实——生产 CLI 不注入 client(cli.py:721-731),当前仅测试路径可达。方向:IP 钉扎或连接层校验;注释改实。(✅ 2026-10-03 已修:下载/detail 两路连接层 rebinding 拒绝 + 注入 client `follow_redirects=True` 不能绕逐跳复核(强制 False);evidence/smallfix/ssrf-redirect-proxy.txt 9/9)

## Requirements

- 修 1-5,各带回归测试(1 用 mock 钥匙串锁「更新失败→删旧建新」;4 补 pool 传递用例;5 补 rebinding 拒绝与 follow_redirects 强制用例);不动已交付协议面。

## Acceptance Criteria

- [x] 换 key 两次保存往返成功(不再 -25244;钥匙链外无明文)——2026-10-03 真钥匙串探针 `myia/image/probe_key` 两次保存往返 PASSED(第二次=既有项更新路径;detail-images evidence/smallfix/secrets-keychain-probe.txt);终检密钥扫描:改动文件 diff 与 evidence 目录 Bearer/api_key/sk- 样式零明文凭据命中
- [x] types.ts 注释与 Python 门校验一致;logs completed 行 ts 恢复 event.ts(注释改实「api_key 只收 keychain: 引用(env: 同拒)」;ts=event.ts 复原;detail-images evidence/smallfix/types-logs-diff.txt)
- [x] pool: 源条目配图经代理下载(mock 断言 proxy_url 传抵)——ssrf-redirect-proxy.txt 9/9(pool 源环+追抓骑池 client)
- [x] rebinding 变体(两次解析不同 IP)被拒;注入 client 强制 follow_redirects=False(ssrf-redirect-proxy.txt:下载/detail 两路连接层 rebinding 拒 + follow_redirects=True 不能绕逐跳复核)
- [x] 基线全绿(pytest/vitest/tsc 以开工基线为准)——detail-images evidence/gates/:pytest 2466 passed/0 failed、vitest 153 passed、npm run build(tsc -b + vite)零错、cargo check Finished

## 过期注记

- ~~heic TimeoutExpired 裸穿~~:`_convert_heic_to_png` 已随 vision-pipeline 拆屏删除(2026-10-03 grep 零命中);若 v2 交互看图回归再立。
