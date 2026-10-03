# 桌面 sidecar 协议(方法注册表与错误码)

> **单一事实源 = `desktop/entry.py` `_HANDLERS`**。本文是镜像快照,两者冲突时以代码为准;
> `method_not_found` 应答自带 `data.allowed = sorted(_HANDLERS)`(entry.py:1919),可随时机器对账。

## 帧格式(entry.py 模块 docstring:10-31)

- 传输:stdin/stdout JSON-line RPC(UTF-8,ensure_ascii=False;stderr 只作调试旁路不承载协议)。
  请求 `{"id","method","params"}`;应答 `{"id","result"}` 或
  `{"id","error":{code,path,message,data}}`;`id` 缺省 = 通知(只执行不应答);
  事件无 id,以 `type` 区分。
- 事件 5 类:`log` / `progress` / `completed`(run 族)、`image.progress` / `image.completed`(看图族)。
- 错误结构化透传(对齐 spec python/error-handling):`path` 字段路径、`message` 中文原因、`data` 原始细节。
- EOF = 干净退出 0(serve,entry.py:1941)。

## 方法注册表(23 方法,逐项核对 `_HANDLERS` entry.py:1875-1899)

| # | 方法 | 处理器(定义行) | 语义 |
|---|------|----------------|------|
| 1 | `version` | `_m_version`(:451) | `myia --version` 等价:版本 + 协议版本 |
| 2 | `health` | `_m_health`(:456) | 插件清单 + 源健康度 + 计数聚合 |
| 3 | `plugins.list` | `_m_plugins_list`(:491) | 已装市场插件 + findings |
| 4 | `doctor` | `_m_doctor`(:507) | 结构化诊断(问题全在 findings,完成即 0) |
| 5 | `run.start` | `_m_run_start`(:1339) | 启动 run 子进程,立即返回 run_id;单飞 `run_busy` |
| 6 | `run.status` | `_m_run_status`(:1378) | run 注册表查询;未知 id = 结构化 404 |
| 7 | `logs.tail` | `_m_logs_tail`(:1392) | 环形缓冲尾部日志,run_id 可选过滤 |
| 8 | `store.items` | `_m_store_items`(:551) | SQLite 单库直读情报流(新→旧) |
| 9 | `secret.set` | `_m_secret_set`(:579) | 凭据只入系统钥匙链;值零回显零落日志 |
| 10 | `secret.list` | `_m_secret_list`(:593) | 只列名字,值永不可读 |
| 11 | `sources.write` | `_m_sources_write`(:672) | 源启停写回(sources 屏私有封装) |
| 12 | `yaml.list` | `_m_yaml_list`(:995) | plugins 目录品类 YAML 清单(坏文件也入列) |
| 13 | `yaml.read` | `_m_yaml_read`(:1009) | 原文直读,注释/顺序逐字节保真(不经 dump) |
| 14 | `yaml.validate` | `_m_yaml_validate`(:1050) | 干跑校验,findings 分级,永不抛校验错 |
| 15 | `yaml.template` | `_m_yaml_template`(:1072) | 最小合法品类模板(id/name 占位) |
| 16 | `yaml.save` | `_m_yaml_save`(:1077) | 同门校验→跨文件 id 查重→`.bak`→原子写(mtime 乐观锁) |
| 17 | `yaml.delete` | `_m_yaml_delete`(:1191) | 围栏→`.bak` 留底→删主文件→连带删 `.disabled.json` |
| 18 | `image.import` | `_m_image_import`(:1530) | 图片入库:魔数嗅探→heic 转 png→sha256 去重 |
| 19 | `image.ocr` | `_m_image_ocr`(:1611) | 一级 OCR(双引擎):逐行 `{text,conf}` |
| 20 | `image.analyze` | `_m_image_analyze`(:1756) | 二级看图(read/describe/ask):提交即返 job_id,结果走事件流 |
| 21 | `image.status` | `_m_image_status`(:1826) | 看图任务对账:busy + 当前 job_id |
| 22 | `image.config.read` | `_m_image_config_read`(:1847) | vision.yaml 脱敏读取(不存在 = 全缺省) |
| 23 | `image.config.save` | `_m_image_config_save`(:1855) | vision.yaml 保存,同门校验失败零写入 |

分组:核心 10(1-10)+ 源启停 1(11)+ 品类 YAML 编辑 6(12-17,task 10-03-yaml-editor)+
看图 6(18-23,task 10-03-image-input)。

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
| run | `run_busy` / `run_not_found` | 单飞拒绝并发 / 未知 run_id |
| 源启停 | `duplicate_source` / `last_source` / `source_unknown` / `source_file_unreadable` / `source_dir_unreadable` / `source_write_failed` / `stash_unreadable` / `category_invalid` | `sources.write` 全链路 |
| 品类 YAML 编辑 | `path_outside_root` / `not_yaml_suffix` / `invalid_file_stem` / `file_too_large` / `invalid_encoding` / `file_not_found` / `mtime_conflict` / `duplicate_category_id`(另复用 `category_invalid` / `source_file_unreadable` / `source_write_failed`) | 围栏 + 乐观锁 + 跨文件查重(yaml.* 六方法) |
| 看图(请求应答) | `image_not_found` / `image_unsupported` / `image_too_large` / `image_ocr_failed` / `image_engine_unknown` / `image_no_credentials` / `image_busy` / `image_config_invalid` | `image_` 前缀统一;analyze 同步预检即时应答 |
| 看图(仅事件流) | `image_unreachable` / `image_provider_error` | analyze 后台线程失败 → `image.completed` 事件 `ok:false` + error 对象,不是请求错误(:1690-1698、:1724、:1749) |

### 透传族(`exc.code` 动态透传,不在 entry.py 静态出现)

`store.items` / `secret.*` / CLI 包装透传底层模块 code(例:`store_corrupt` /
`invalid_secret_name` / `plugins_dir`,枚举见 entry.py 模块 docstring:19-21)——追源头去
`src/myia/` 对应模块,entry.py 只加 `path`/`data` 不改 code。

## 变更纪律

1. 新增/改名方法:**只改 `_HANDLERS` 一处** + `tests/test_desktop_sidecar_protocol.py` 契约用例;
   本文注册表随同更新(行号注解允许漂移,方法名集合不许漂)。
2. 对账手法:发未知方法名,拿 `data.allowed` 与本文注册表比对;前端共享类型映射
   `SidecarProtocol`(types.ts)现盖 16 方法(核心 + image.*),`sources.write`/`yaml.*`
   刻意未入共享映射——对账时按上表分组核对,勿以 16 当全量。
3. 封装面 ≠ 协议面:`ui-src/src/lib/api/client.ts` 的 `api` 门面只盖核心 10 方法;
   `sources.write` 在 `screens/sources/api.ts`、`yaml.*` 在 `screens/yaml-editor/api.ts`、
   `image.*` 在 `screens/image/api.ts` 屏私有封装(invoke 直连,不走共享门面)。
