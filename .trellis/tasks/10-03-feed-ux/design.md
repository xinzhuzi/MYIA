# 技术设计:feed-ux 批次

> 事实底座(2026-10-03 实查,写定不再摸底):
> - capabilities = `core:default` + `updater:default` + `process:allow-restart` +
>   `dialog:default`(看图屏加的,含保存对话框);**安全姿势:webview 零 shell 执行授权**
>   (v1.1 评审定案,_capability 文件自述)。
> - `store.items` 现参 `db/category/since/limit`——**category 已在协议里**,C8 是纯接线。
> - `tauri_plugin_shell` 已注册 Rust 侧(sidecar 拉起用),JS 包 `@tauri-apps/plugin-shell`
>   未装;dialog 前后端全装。
> - sidecar **不驻留调度器**(entry.py 无 APScheduler)——「排程管理」= 可视化+预览+
>   手动触发,排程执行本体属 CLI/常驻形态,启停调度无对象(编辑 schedule 节归 yaml-editor)。
> - `src/myia/pipeline.py:640 build_cron_trigger(schedule, timezone)` 现成可复用。
> - push 各通道 `async send(items, context)` 齐备(feishu_card/telegram/stdout/webhook)。
> - `PROTOCOL_VERSION = 1`(entry.py:131);在途 yaml-editor 线的 yaml.* 六方法同未定版,
>  **版本号两线合并时统一 +1,本批不抢跑**。
> - items 表列:`source TEXT / title TEXT NOT NULL / content TEXT`(LIKE 目标成立)。

## 1. 协议契约(钉死;Python/TS 两侧同源,契约注释两侧互指)

### store.items 扩展(与 v112-batch C1 游标合参,一次定形)

```
params 增(全部可选,旧调用零感知):
  query?:  string   # 非空 → title/content/source 三列 LIKE %q%(大小写不敏感,SQLite LIKE 原生);空串/缺省 = 不过滤
  before?: ISO-8601 # first_seen 严格小于该时刻(C1:同刻批量 > limit 时游标可推进);与 since 可并存
应答不变(items[] 新→旧)
err 不变
```

- feed/api.ts 分页改用 `before = 上页最旧 first_seen`(替换现 since 复用),客户端
  dedup 保留(边界条目仍会重复返回)、「追加 0 条判停」逻辑保留为防御。
- 搜索与游标组合:query 全程随游标透传;LIKE 无索引单机万级条目可接受(如实注记,
  不建索引)。

### feed.export(新方法)

```
params: { format: "jsonl" | "csv", path: string, category?: string, query?: string }
  # path = 前端 dialog.save() 用户选定(绝对路径);category/query = 当前过滤视图
应答: { path, count, bytes }
err: export_path_invalid(空/相对路径/父目录不存在) / export_write_failed(IO 原文)
```

- sidecar 直写(stdlib csv / 逐行 json.dumps ensure_ascii=False),数据不经 webview;
  只写该一个文件,不建目录不删除任何东西。
- 导出内容 = 复用 list_items 同一查询面(与屏上所见一致)。

### push.test(新方法)

```
params: { channel: "feishu" | "telegram" | "stdout" }
应答: { ok: true, channel }
err: 凭据缺失/发送失败沿用通道既有错误分类原文(push 侧结构化错误直传)
```

- 合成单条测试 Item(标题「MYIA 推送测试」+ 时间戳)走既有 `send(items, context)`,
  凭据解析复用现有链路——**真发消息**;单测 monkeypatch 假 send,真机冒烟通道由
  主人点选(默认 stdout,不扰生产群)。

### schedule.preview(新方法)

```
params: { file: string, count?: number = 5 }
应答: { schedule: string | null, timezone: string | null, runs: ISO[] }
  # 无排程品类明示 schedule: null + runs: [](不是错误)
err: 品类文件加载失败沿用 doctor/health 同款错误
```

- `build_cron_trigger` → `get_next_fire_time` 链推进 count 次;纯计算零副作用。

## 2. Python 侧结构(entry.py / store/sqlite.py,全在既有文件内)

- `SQLiteStore.list_items` 增 `query`/`before` 形参,进既有 conditions 拼装
  (`title LIKE ? COLLATE NOCASE` 三列 OR,参数转义照旧)。
- `_m_store_items` 透传两参;`_m_feed_export`/`_m_push_test`/`_m_schedule_preview`
  新 handler 进 `_HANDLERS`;错误码风格与现有一致(小写下划线)。
- 零新 Python 依赖。

## 3. UI 侧结构(desktop/ui-src/src/)

- **共享 client(types.ts 契约 + client.ts 封装)**:storeItems 增可选参;新增
  feedExport / pushTest / schedulePreview 三封装(与 _HANDLERS 一一对应)。
- **C8 接线**:top-bar Select 选项 = `health().plugins`(id 去重);选中值提升到
  AppLayout 级状态(简单 prop drilling 即可,不引状态库)→ feed 屏
  `fetchFeedPage({ category })` 服务端过滤;「全部品类」= 不传参。
- **G1 搜索**:feed 屏搜索框(防抖 300ms + Enter 提交)→ `query` 随分页透传;
  计数行如实显示「服务端搜索 × 本地未读过滤」两层。
- **G2 详情/打开原文**:卡片行内展开(全文 content + 元信息)不用路由跳转;
  「打开原文」= plugin-shell `open(item.url)`。**npm 增 `@tauri-apps/plugin-shell`**
  (JS 壳包,Rust 已注册);**capabilities 增 `shell:allow-open` 且 scope 校验器仅放行
  `https?://`**——尊重「webview 零执行授权」姿势,只开受控 open,不开 execute。
- **G3 导出**:feed 头部「导出当前视图」→ `dialog.save()`(默认名
  `myia-feed-YYYYMMDD.jsonl|csv`)→ feed.export(path)→ 回显 path/count。
- **G4 跑一次 + 排程一览**:dashboard CategoryCard 增「跑一次」(run.start{yaml},
  状态机照抄 feed 空态 CTA:starting→collecting→done/error,completed 事件刷新);
  源管理屏底部新分区「排程一览」= 每品类 schedule/timezone 原文 + 未来 5 次
  (schedule.preview)。
- **G5 测试按钮**:设置屏推送表单区「发送测试」(channel 取表单当前选中),
  结果行内回显(成功 ok 徽标 / ErrorBox)。

## 4. 隔离与刻意不做(本批边界)

- 不做:run 重跑、批量已读、快捷键(G9)、告警规则(G5 主体,池)、AI 摘要按钮(G8,池)。
- 排程**启停**不做:sidecar 无调度器,启停无对象;改 schedule 节归 yaml-editor。
- 搜索高亮/正则/分词不做:首版 LIKE 子串,够用即止。
- C7(client.ts 封装与 _HANDLERS 注释失实修正)是 v112-batch 的活,本批只保证
  **自己新增**的封装一一对应,不顺手改旧注释(防两批打架)。

## 5. 兼容与回滚

- store.items 只加可选参,旧 UI/旧测试零感知;协议测试新增用例不改动既有断言。
- PROTOCOL_VERSION:与在途 yaml-editor 线**合并时统一 +1 一次**(两个 PR 都先不 bump,
  谁后合谁 bump 并在 CHANGELOG 记两批方法)——避免版本号抢跑打架。
- 回滚:UI 各步独立 commit 可单退(C8 接线退回 defaultValue 即恢复现状);Python 新
  handler 是增量注册,回退即摘除。
- 重打包冒烟口径照 brand-trim:tauri build + 备份重装 /Applications + 窗口截屏 OCR 目视。
