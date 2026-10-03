# 消息平台移植:通道目录+定向投递核心引擎(源自 Hermes)

## Goal

把 Hermes 的通道目录、对象解析、定向投递、死信四件源码蓝本移植进 `myia/push/`,形成平台无关引擎,并扩展 `PushConfig` 目标寻址。上游路径与总纲见父任务 `10-03-hermes-messaging/prd.md`。

## 上游蓝本对照表(移植时逐文件登记 diff 基准)

| MYIA 模块(新) | Hermes 源(蓝本) | 移植要点 |
|---|---|---|
| `myia/push/directory.py` | `~/.hermes/hermes-agent/gateway/channel_directory.py`(474 行) | 目录数据模型、别名覆盖、原子持久化、周期刷新;去掉 multiplex-gateway 多 home 逻辑 |
| `myia/push/targets.py` | `~/.hermes/hermes-agent/tools/send_message_targets.py` | `platform:名称或id` 解析、模糊匹配、未命中结构化报错 |
| `myia/push/base.py` + `myia/push/__init__.py`(原计划独立 `platform.py`,design D1 定案后并入) | `gateway/platform_registry.py`(405 行)+ `tools/send_message_senders.py` | 适配器接口(目录发现+定向发送)+ 注册表;MYIA 用 dict 注册表(`PLATFORMS`,与 CHANNELS 并排),目录能力直接挂现有 `Channel` 协议(`supports_targeting`/`parse_direct_ref`/`discover_directory`),不建平行协议、不引延迟加载器 |
| `myia/push/delivery.py` | `gateway/delivery.py`(334 行)+ `gateway/dead_targets.py`(104 行)+ `gateway/platforms/base.py` 错误分类表 | 按对象投递、部分失败隔离、连续失败熔断与自动恢复(实现轮按 grill Q3 修正为:单次硬失败即标 dead) |

## Requirements

1. **通道目录**:`ChannelEntry(platform, chat_id, name, type, thread_id, last_seen)`;持久化为数据根下 `channel_directory.json`(原子写,utf-8);别名覆盖文件 `channel_aliases.json`(手工可编,Hermes 同语义:重建后仍生效)。重建为**按平台桶整体替换**而非条目合并;手工直编 channel_directory.json 不保证保留(Hermes 同款),别名文件才是持久覆盖层。`last_seen` 为 MYIA 增量字段(Hermes 无,给 UI「最后发现」用)。
2. **对象解析**(对齐 Hermes `resolve_send_target` 实际顺序,grill 事实轮核订):显式 id/`@username` 直达不经目录(如 `telegram:12345`)→ 目录精确 id → 精确名(大小写不敏感)→ 唯一前缀(多义即未命中)。别名是目录改名层,不是独立解析层级。未命中报 `TargetResolveError` 结构化错误【偏离注记:Hermes 报错不带候选、靠交互式 action='list';MYIA 无交互回路,错误内嵌候选列表】。
3. **PushConfig 扩展**(schema 向后兼容,grill Q2 定案):新增可选 `targets: list[str]`,**同平台约束**——元素平台前缀必须与条目 `channel` 对应平台一致(feishu_card 条目只准 `feishu:*`),不一致在配置加载期即拒;跨平台 = 写多条 push 条目(沿用现有心智)。**targets 在场时 legacy `target` 可省**(省 = 只发 targets 列表对象);不填 targets = 现行为,零迁移。route 规则(`RouteRuleConfig`)可选 `targets` 覆盖通道级。
4. **定向投递**:immediate 与 digest(AM/PM 聚合)都按解析后的对象逐一发送;单对象失败不阻断同批其他对象(沿用 partial-failure 约定)。
5. **死信与熔断**(Hermes 原味错误分类制,grill Q3 定案):投递错误先分类——`forbidden` 与 chat 级 `not_found` 为硬失败,**单次硬失败即标 dead**;超时/网络抖动等瞬态错误不标。dead 期间跳过该对象并记结构化日志(不发告警卡);投递成功一次即自愈清除。状态存数据根 JSON(原子写;损坏/不可写退化为内存态,不阻塞投递)。无阈值、无配置口。
6. **适配器接口**:`PushPlatform` 协议 = `async discover_directory()` + `async send_to(entry, payload)`;注册表 `PLATFORMS: dict[str, type]`;core 不含任何具体平台(feishu/telegram 在各自子任务接入)。
7. **数据根对齐**:目录/别名/死信文件路径从流水线现有数据根解析(CLI 相对路径契约不动,桌面 home 模式落 `myia_home()`),与 `10-03-v111-desktop-paths` 定案一致。
8. **节流懒刷**(grill Q4 定案):pipeline 在 run 前检查目录新鲜度,距上次刷新 >5 分钟且存在已注册平台才触发 `discover_directory`(Hermes housekeeping 5 分钟节流的等价物);刷新失败退回旧目录+结构化告警,不阻塞推送。CLI `myia channels refresh <platform>` 与桌面按钮为手动入口(接线归 feishu/telegram/ui 子任务)。

## Acceptance Criteria

- [ ] `directory.py`:建/读/写/别名覆盖/原子性各有单测;重建目录后别名仍生效(Hermes 同款回归点)。
- [ ] `targets.py`:直达 id/@username、目录精确 id/精确名/唯一前缀各路径 + 多义/未命中(带候选)结构化错误单测;安全(不做任何 eval)。
- [ ] schema:`targets` 校验(空串拒、去重、元素格式 `platform:xxx`、同平台约束、targets 在场时 target 可省);旧 YAML(无 targets)加载结果逐字节等价。
- [ ] delivery:多对象投递的失败隔离、错误分类(硬失败标 dead/瞬态不标)、成功自愈、dead 跳过+日志、死信记录单测(fake platform 注入)。
- [ ] 全量 `pytest tests/` 绿;`ruff check src/` 干净(若项目启用)。
- [ ] docstring 逐一标注上游蓝本文件路径 + MIT 归属。

## 非目标

- 任何具体平台适配器(feishu/telegram 各自子任务)
- 桌面 UI(sidecar 端点随 ui 子任务评估)
- 目录的定时后台刷新调度(core 只提供 `refresh()` 入口,接线归 ui/cli 侧)
