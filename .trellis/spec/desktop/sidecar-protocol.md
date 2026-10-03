# 桌面 sidecar 协议(方法注册表与错误码)

> **单一事实源 = `desktop/entry.py` `_HANDLERS`**。本文是镜像快照,两者冲突时以代码为准;
> `method_not_found` 应答自带 `data.allowed = sorted(_HANDLERS)`(entry.py:1919),可随时机器对账。

## 帧格式(entry.py 模块 docstring:10-31)

- 传输:stdin/stdout JSON-line RPC(UTF-8,ensure_ascii=False;stderr 只作调试旁路不承载协议)。
  请求 `{"id","method","params"}`;应答 `{"id","result"}` 或
  `{"id","error":{code,path,message,data}}`;`id` 缺省 = 通知(只执行不应答);
  事件无 id,以 `type` 区分。
- 事件 4 类:`log` / `progress` / `completed`(run 族)/ `test.completed`(试抓 job,C13)。
- 错误结构化透传(对齐 spec python/error-handling):`path` 字段路径、`message` 中文原因、`data` 原始细节。
- EOF = 干净退出 0(serve,entry.py:1941)。

## 方法注册表(31 方法,逐项核对 `_HANDLERS` entry.py)

| # | 方法 | 处理器 | 语义 |
|---|------|----------------|------|
| 1 | `version` | `_m_version` | `myia --version` 等价:版本 + 协议版本 |
| 2 | `health` | `_m_health` | 插件清单 + 源健康度 + 计数聚合 |
| 3 | `plugins.list` | `_m_plugins_list` | 已装市场插件 + findings |
| 4 | `doctor` | `_m_doctor` | 结构化诊断(问题全在 findings,完成即 0) |
| 5 | `run.start` | `_m_run_start` | 启动 run 子进程,立即返回 run_id;单飞 `run_busy` |
| 6 | `run.status` | `_m_run_status` | run 注册表查询;未知 id = 结构化 404 |
| 7 | `run.cancel` | `_m_run_cancel` | 取消进行中 run:killpg(SIGTERM→5s→SIGKILL);信号终局 status=cancelled(v112 批 C2) |
| 8 | `runs.list` | `_m_runs_list` | runs 表直读(新→旧,limit 钳制 [1,200]);重启后历史可达(v112 批 C3) |
| 9 | `logs.tail` | `_m_logs_tail` | 环形缓冲尾部日志,run_id 可选过滤 |
| 10 | `store.items` | `_m_store_items` | SQLite 单库直读情报流(新→旧);游标 `before`/`before_id` + `query`(v112 批 C1) |
| 11 | `feed.export` | `_m_feed_export` | 当前过滤视图导出 JSONL/CSV:sidecar 直写(数据不经 webview),只写对话框选定单文件;`export_path_invalid`/`export_write_failed`(feed-ux 批 G3) |
| 12 | `schedule.preview` | `_m_schedule_preview` | 品类排程 Next runs 预览(纯计算零副作用,count 钳制 [1,20];无排程明示 null 非 err;feed-ux 批 G4) |
| 13 | `secret.set` | `_m_secret_set` | 凭据只入系统钥匙链;值零回显零落日志 |
| 14 | `secret.list` | `_m_secret_list` | 只列名字,值永不可读 |
| 15 | `secret.delete` | `_m_secret_delete` | 删除凭据(误存清除口;二次删除 `secret_not_found`;v112 批 C5) |
| 16 | `sources.write` | `_m_sources_write` | 源启停写回(sources 屏私有封装) |
| 17 | `sources.test` | `_m_sources_test` | 试抓此源,异步 job(`test_busy` 单飞);结果走 `test.completed` 事件(v112 批 C13) |
| 18 | `yaml.list` | `_m_yaml_list` | plugins 目录品类 YAML 清单(坏文件也入列) |
| 19 | `yaml.read` | `_m_yaml_read` | 原文直读,注释/顺序逐字节保真(不经 dump) |
| 20 | `yaml.validate` | `_m_yaml_validate` | 干跑校验,findings 分级,永不抛校验错 |
| 21 | `yaml.template` | `_m_yaml_template` | 最小合法品类模板(id/name 占位) |
| 22 | `yaml.save` | `_m_yaml_save` | 同门校验→跨文件 id 查重→`.bak`→原子写(mtime 乐观锁) |
| 23 | `yaml.delete` | `_m_yaml_delete` | 围栏→`.bak` 留底→删主文件→连带删 `.disabled.json` |
| 24 | `image.config.read` | `_m_image_config_read` | vision.yaml 脱敏读取(不存在 = 全缺省) |
| 25 | `image.config.save` | `_m_image_config_save` | vision.yaml 保存,同门校验失败零写入 |
| 26 | `channels.list` | `_m_channels_list` | 消息屏目录四视图:platforms+aliases+dead+rules(零平台=合法空态) |
| 27 | `channels.refresh` | `_m_channels_refresh` | 单平台 `discover_directory`→桶替换+落盘;失败结构化上抛,旧桶不动 |
| 28 | `channels.alias` | `_m_channels_alias` | 别名 set/delete(name 非空/null 区分);落盘复核未生效即报错 |
| 29 | `push.write` | `_m_push_write` | push[] 全量替换:围栏→文本手术→双门→`.bak`→原子写;失败零写入 |
| 30 | `push.test` | `_m_push_test` | 合成单条测试条目真发指定通道(凭据沿用 env:/keychain: 引用链;PushSendError code 原文直传;stdout 通道卡片入应答 preview;feed-ux 批 G5 前半) |
| 31 | `bridge.status` | `_m_bridge_status` | 微信桥接探测:`probe_bridge()` 全量七键(available/reason/fix_hint/bin_found/weixin_configured/gateway_alive/bin_path);纯文件存在性探测零读取,无凭据无出网;bin 取品类 YAML 首个 weixin 条目 `weixin_hermes_bin` 覆写;永不抛业务错(10-03-messaging-weixin-bridge D4) |

分组:核心 10(1-9 + 13-14 的 logs.tail/secret.set/secret.list)+
源启停 1(16)+ 品类 YAML 编辑 6(18-23,task 10-03-yaml-editor)+
看图配置 2(24-25,task 10-03-image-input;10-03-vision-pipeline 拆四留二)+
消息 4(26-29,task 10-03-messaging-ui)+
feed-ux 3(11 `feed.export` G3 / 12 `schedule.preview` G4 / 30 `push.test` G5,task 10-03-feed-ux)。
v1.1.2 桌面对齐批(task 10-03-v112-desktop-parity)新增 4:7 `run.cancel`(C2)/
8 `runs.list`(C3)/ 15 `secret.delete`(C5)/ 17 `sources.test`(C13)。
weixin-bridge 批(task 10-03-messaging-weixin-bridge)新增 1:31 `bridge.status`。

**store.items 参数(合流形状,v112 批 C1 × feed-ux G1/G3)**:`db/category/since/limit`
之外增 `before`(ISO,first_seen 严格小于)、`before_id`(与 before 组成
`(first_seen, id)` 复合游标,成对出现)、`query`(title/content/source 三列
LIKE NOCASE,%/_ 按字面转义)。旧调用零感知。

**feed-ux 批三方法参数(task 10-03-feed-ux,契约钉死于任务档 design.md §1)**:
`feed.export {format: jsonl|csv, path: 绝对路径, category?, query?}` →
`{path, count, bytes}`(path = 前端 `dialog.save()` 选定,只写该一个文件);
`schedule.preview {file: 围栏内品类 YAML, count?=5 钳制 [1,20]}` →
`{file, schedule: string|null, timezone, runs[]}`(build_cron_trigger 纯计算);
`push.test {channel: myia.push.CHANNELS 键, target?(env:/keychain: 引用), template?}`
→ `{ok: true, channel, preview?}`(preview 仅 stdout 通道——serve stdout 是协议流,
卡片行入内存缓冲随应答回显)。协议版本随批 bump:v3(feed-ux 三方法)、
v4(weixin-bridge 批 `bridge.status`)。

## 错误码表

### 协议级(分发层 `_handle_line` entry.py:1902-1936,5 个)

| code | 触发 |
|------|------|
| `parse_error` | 请求行不是合法 JSON |
| `invalid_request` | 请求非 JSON 对象 / 缺字符串 `method` |
| `invalid_params` | `params` 非对象(参数形状错由各处理器细分抛同名 code) |
| `method_not_found` | 未知方法;`data.allowed` 自带全量注册表 |
| `internal_error` | 处理器未捕获异常兜底;协议流不裸 traceback |

### 业务级(entry.py 内静态抛出)

| 族 | code | 场景 |
|----|------|------|
| 通用参数 | `invalid_params`(25 处) | 各处理器参数形状/取值校验 |
| CLI 包装 | `config` / `cli_error` | CLI 报文带 config+errors[] / 退出码异常无可解析输出(:439-443) |
| 环境 | `myia_home_unwritable` | serve 上下文数据根不可写(:291) |
| run | `run_busy` / `run_not_found` / `run_not_active` | 单飞拒绝并发 / 未知 id 或无进行中 run / 已终态拒取消(data 带 state;v112 批 C2) |
| 源启停 | `duplicate_source` / `last_source` / `source_unknown` / `source_file_unreadable` / `source_dir_unreadable` / `source_write_failed` / `stash_unreadable` / `category_invalid` | `sources.write` 全链路 |
| 试抓 | `test_busy`(另复用 `invalid_params` / `not_yaml_suffix` / `path_outside_root` / `source_file_unreadable`) | `sources.test`:单飞拒绝 / 参数形状 / 围栏 / 品类装不上(v112 批 C13);子进程级 CLI 错不走请求错误,经 `test.completed` 事件 `ok:false` 透传 |
| 品类 YAML 编辑 | `path_outside_root` / `not_yaml_suffix` / `invalid_file_stem` / `file_too_large` / `invalid_encoding` / `file_not_found` / `mtime_conflict` / `duplicate_category_id`(另复用 `category_invalid` / `source_file_unreadable` / `source_write_failed`) | 围栏 + 乐观锁 + 跨文件查重(yaml.* 六方法) |
| 看图配置 | `image_config_invalid` | `image.config.read` 装载拒载 / `image.config.save` 未过校验零写入(拆四留二后看图族仅余此码) |
| feed-ux 导出 | `export_path_invalid` / `export_write_failed` | `feed.export`:路径空/相对/父目录不存在 / 写盘 IO 失败(task 10-03-feed-ux G3) |
| feed-ux 排程 | `invalid_cron`(防御性;另复用 `invalid_params`/`not_yaml_suffix`/`path_outside_root`/`source_file_unreadable`) | `schedule.preview`:`build_cron_trigger` 兜底 / 参数 / 围栏 / 品类装不上(task 10-03-feed-ux G4) |
| 消息 | `unknown_platform` / `discover_not_supported` / `channel_refresh_failed` / `alias_write_failed` / `push_write_unsupported`(另复用 `category_invalid` / `file_not_found` / `path_outside_root` / `source_write_failed` / `invalid_params`) | channels.* / push.write 全链路(task 10-03-messaging-ui;数据面错误码透传 push 层如 `credential_not_found` 经 `channel_refresh_failed.data.code` 携带) |

### 透传族(`exc.code` 动态透传,不在 entry.py 静态出现)

`store.items` / `secret.*` / CLI 包装透传底层模块 code(例:`store_corrupt` /
`invalid_secret_name` / `plugins_dir`,枚举见 entry.py 模块 docstring:19-21)——追源头去
`src/myia/` 对应模块,entry.py 只加 `path`/`data` 不改 code。`push.test` 同款透传
push 层 `PushSendError.code`(`missing_target` / `env_var_missing` /
`credential_resolve_failed` 等,追源头去 `src/myia/push/`;task 10-03-feed-ux G5)。

## 变更纪律

1. 新增/改名方法:**只改 `_HANDLERS` 一处** + `tests/test_desktop_sidecar_protocol.py` 契约用例;
   本文注册表随同更新(行号注解允许漂移,方法名集合不许漂)。
2. 对账手法:发未知方法名,拿 `data.allowed` 与本文注册表比对;前端共享类型映射
   `SidecarProtocol`(types.ts)现盖 19 方法(核心 + image.config.* + v1.1.2 批四方法 +
   feed-ux 批三方法),`sources.write`/`yaml.*` 刻意未入共享映射——对账时按上表分组核对,勿以映射数当全量。
3. 封装面 ≠ 协议面:`ui-src/src/lib/api/client.ts` 的 `api` 门面盖核心 10 方法 +
   v1.1.2 桌面对齐批 4 方法(`runCancel`/`runsList`/`secretDelete`/`sourcesTest`,
   10-03-v112-desktop-parity)+ feed-ux 批 3 方法(`feedExport`/`schedulePreview`/
   `pushTest`,10-03-feed-ux;两批新方法全入共享门面,注册表新增行 ↔ 门面新增行
   同源对账,屏私名单不扩);
  `sources.write` 在 `screens/sources/api.ts`、`yaml.*` 在 `screens/yaml-editor/api.ts`、
  `image.config.*` 在 `screens/settings/vision-api.ts`、`channels.*`/`push.write`/
  `bridge.status` 在 `screens/messaging/api.ts` 屏私有封装(invoke 直连,不走共享门面)。
