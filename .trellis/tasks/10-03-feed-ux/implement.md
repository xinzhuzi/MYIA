# 执行计划:feed-ux 批次

> 前置:本任务排 v1.1.1 tag 后开工,与 v112-desktop-batch 并行;协议面改动与
> 在途 yaml-editor 线有合并协调(见 design.md §5,版本号不抢跑)。
> 每步独立 commit;验证命令一律显式退出码,禁管道判活。

## 步骤

### 1. Python:store 扩展 query/before + 协议测试

- `SQLiteStore.list_items` 增 `query`/`before`(LIKE 三列 NOCASE、first_seen 严格小于);
  `_m_store_items` 透传。
- `tests/test_desktop_sidecar_protocol.py` 增用例:query 命中/未命中/大小写、before
  推进同刻批量、query+before+category 组合、limit 边界。
- 验证:`uv run python -m pytest -q tests/test_desktop_sidecar_protocol.py; echo $?`
  → 0;再裸 `uv run pytest -q` 双跑法 → 0。

### 2. Python:feed.export

- handler + csv/jsonl 双格式写盘;err:export_path_invalid / export_write_failed。
- 协议测试:tmp_path 写入→读回验行数与表头;相对路径/不存在父目录拒写。
- 验证:同步骤 1 双跑法。

### 3. Python:push.test + schedule.preview

- push.test:合成 Item 走三通道 send;单测 monkeypatch 假 send 断言调用与结构化错误
  透传(不真发);stdout 通道真跑(无副作用风险)。
- schedule.preview:build_cron_trigger 链;用例含无排程品类(schedule null 非 err)、
  count 上限钳制(≤20)。
- 验证:同上双跑法。

### 4. UI:共享 client 扩展

- types.ts 增契约注释(与 entry.py 互指);client.ts:storeItems 可选参 + feedExport/
  pushTest/schedulePreview 三封装。
- vitest:封装方法名与参数形状快照测。
- 验证:`npm --prefix desktop/ui-src run test; echo $?` → 0。

### 5. UI:C8 品类选择器接线 + G1 搜索框

- top-bar Select 接 `health().plugins`(去重);选中品类提升 AppLayout → feed
  服务端 category 过滤;「全部品类」= 不传参;C 阶段死注释清除。
- feed 搜索框(防抖+Enter)→ query 透传;计数行双层如实。
- vitest:选品类后 storeItems 收到 category、搜索词透传、空态文案。
- 验证:vitest + `npm --prefix desktop/ui-src run build; echo $?` → 0。

### 6. UI:G2 卡片展开 + 打开原文

- `npm --prefix desktop/ui-src i @tauri-apps/plugin-shell`;capabilities/default.json
  增 `shell:allow-open`(scope 仅 `https?://`,描述注明与 v1.1 评审姿势的关系)。
- 卡片行内展开全文;「打开原文」按钮调 open(item.url);http 外 scheme 不渲染按钮。
- vitest:展开态快照、按钮 href 门控。
- 验证:vitest + build;tsc 零错。

### 7. UI:G3 导出 + G4 跑一次/排程一览 + G5 测试按钮

- feed 头部导出:dialog.save(默认文件名带日期)→ feed.export → 回显 path/count;
  错误走 ErrorBox。
- dashboard CategoryCard「跑一次」:run.start + completed 事件状态机(照 feed CTA),
  busy 期按钮禁用;失败 ErrorBox。
- sources 屏「排程一览」分区:schedule.preview 逐品类(并发发请求,Promise.allSettled,
  单品类失败不塌整区)。
- settings 推送区「发送测试」:pushTest(表单当前 channel),stdout 默认;结果行内回显。
- vitest:各交互用例(导出回显/跑一次状态机/排程空态/测试按钮错误透传)。
- 验证:vitest + build。

### 8. 全量回归 + 重打包冒烟

- `uv run python -m pytest -q`(全量)与裸跑双绿;vitest 全绿;tsc+vite build 绿
  (全部显式退出码)。
- PROTOCOL_VERSION 与 yaml-editor 线合并协调:若本批先全合,bump 至 2 并 CHANGELOG
  记两批方法;若 yaml.* 已 bump,沿用其版本号只记方法。
- `npm run tauri build`(desktop/)→ 备份重装 /Applications(备份
  /tmp/MYIA.app.bak-feed-ux)→ 窗口截屏 OCR 目视:搜索可用、卡片可展开原文外链、
  导出落盘、品类卡跑一次、排程一览、推送测试回显。
- push.test 真机冒烟通道主人点选(默认 stdout)。

## 回滚点

- 步骤 1-3(协议侧)与 4-7(UI 侧)各自独立成 commit;任一 UI 步骤可单独 revert
  恢复上一屏态(C8 退回 defaultValue 即现状)。
- capabilities 的 shell:allow-open 独立于代码 commit(与步骤 6 同 commit),
  revert 即收回权限。

## 风险与守门

- **版本号协调**:本批与 yaml-editor 在途扩展共用 PROTOCOL_VERSION,谁后合谁 bump,
  CHANGELOG 双记——开工前先看 main 上协议版本现值。
- **shell:allow-open 必须 scoped**(仅 https?://);绝不加 shell:execute 或通配 open
  (v1.1 评审红线,capabilities 文件自述)。
- **push.test 真发消息**:单测全 mock;真机冒烟默认 stdout 通道,飞书/TG 由主人选择。
- **export 写盘**:只写用户对话框选定的单个文件;不建目录、不覆盖无确认(dialog.save
  自带覆盖确认);大结果集流式写不整载内存。
- **LIKE 全表扫**:单机万级条目可接受;不建索引,性能注记留在 design(余量再议)。
- 与 v112-desktop-batch 的 C1 合参已在 design §1 定形(before 游标),v112-batch 侧
  只做壳层 respawn(C2)等,不再重复改 store.items——两批开工顺序若反转,以先合者为准。
