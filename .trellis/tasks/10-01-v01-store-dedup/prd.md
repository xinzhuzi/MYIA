# 存储 + 去重:SQLite 注册表 + AM/PM 槽位

## Goal

SQLite 默认存储层(可插拔接口留 PG 位置)与生产验证过的去重语义:URL/组合键注册表 + AM/PM 槽位防重发。

## Requirements

- SQLite(单文件,`myia.db`;路径可配):
  - `items` 表:url、dedup_key、source、title、content(摘要)、content_hash、tags、category、scores(v0.1 预留列)、pushed_at、push_slot、first_seen、raw(可选 JSON)
  - `dedup_registry` 表:key → first_seen / last_pushed_at / last_push_slot
  - `change_baseline` 表:url → etag / last_modified / content_hash / last_changed(供 fetch_base 指纹比对)
  - `engine_hints` 表(供 registry 降级回写)、`runs` 表(run 记录,断点续跑 v0.2)
- 去重:`dedup.key` 组合键模板(如 `{symbol}-{date}`);无 key 时默认 URL 全等;**永不标题指纹**(规划铁律)
- AM/PM 槽位:同一 key 在同一槽位(am/pm)不重发;跨槽位可发;digest 与 immediate 共享同一注册表——防重发语义,分级路由(v01-push)只管"推不推",本层管"发没发过"。**槽位定义(grill Q3 定案):本地时区 12:00 分界,AM=00:00–11:59,PM=12:00–23:59**
- 线程/协程安全:单进程 asyncio 场景 WAL 模式;接口按可插拔设计(Protocol/ABC,PG 实现留 v0.2+)

## Acceptance Criteria

- [ ] 单测:同 key 同槽位拦截、跨槽位放行、12:00 分界正确性、组合键生效、基线读写、WAL 并发写
- [ ] `myia run` 连跑两次:第二次 dedup 拦截数与首次条目数一致(端到端联测时验证)
- [ ] 无 Redis/PG 依赖

## Notes

- 填充 `src/myia/store/__init__.py`(47 行壳)与 `src/myia/dedup.py`(44 行壳)
- retention 清理与 VACUUM 是 v0.2(v02-storage-hardening),本任务只留 storage 节解析位置
