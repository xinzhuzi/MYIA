# Design:messaging-core — 通道目录+对象解析+定向投递引擎

上游蓝本对照见 `prd.md`;本档只写 MYIA 侧的技术形态。

## 核心决策

### D1:「平台」= 支持目录寻址的 Channel,不建平行协议

Hermes 有独立的 PlatformRegistry/adapter 体系(为双向 gateway 服务)。MYIA 定向出站不需要第二套协议:**目录能力直接挂在现有 `Channel` 协议上**:

- `Channel` 增类属性 `supports_targeting: bool = False`(stdout/webhook 不支持,feishu_card/telegram 在各自子任务翻成 True 并实现目标覆盖)。
- `SendContext` 增可选字段 `target: ChannelTarget | None = None`;支持寻址的通道发送时 `context.target.chat_id` 优先、退回通道自带单 target(legacy 行为)。
- `PLATFORMS: dict[str, type[Channel]]` 注册表(与 `CHANNELS` 并排,dict 注册风格):平台名 → 已开 `supports_targeting` 的通道类。core 只交付注册表与 fake 测试通道,不内置真实平台。

理由:MYIA 的 Channel 协议是既有扩展点(spec:`push/` 通道+阈值分级路由),平行协议会让 feishu_card 同一发送逻辑写两遍。

### D2:targets 可跨通道,delivery 负责「解析→分组→派发」

`targets: ["feishu:AI中转站合伙人群", "telegram:12345"]` 天然跨通道。新模块 `delivery.py` 持有派发循环:

```
resolve(specs, directory)            # targets.py:全部解析成 ChannelTarget
  → group_by(platform)               # 每组绑到 PLATFORMS[platform] 的 PushConfig 凭据
  → ledger.filter_dead(group)        # dead 对象跳过 + 结构化日志
  → per-target send(带 context.target)  # 单对象失败隔离;错误分类(forbidden/chat 级 not_found = 硬失败)
  → 成功即自愈清除 dead 标记
```

**无 targets 配置时整体短路到现网单目标路径**——旧 YAML 行为逐字节不变,这是兼容承诺的实现方式。

### D3:目录/账本落数据根,不引全局状态

- `ChannelDirectory(data_root)`:`<data_root>/channel_directory.json` + `channel_aliases.json`。data_root = db 路径的父目录(CLI cwd / 桌面 `myia_home()`,与 v111-desktop-paths 收口一致,不在 push 层新增路径解析)。
- `DeliveryLedger(data_root)`:`<data_root>/delivery_ledger.json`,结构 `{target_key: {"reason": str, "marked_at": ts}}`(对齐 Hermes dead_targets.json 条目形态);投递错误先分类,`forbidden`/chat 级 `not_found` 单次即标 dead,瞬态错误不标;成功即自愈;dead 期跳过+结构化日志;损坏/不可写退化为内存态(Hermes 同款 best-effort)。无阈值计数、无配置口(Q3 定案)。
- 两文件均原子写(tmp+rename),读损坏文件按空态处理+告警(Hermes `_load_json_dict` 同语义)。

### D4:schema 验格式与同平台约束(Q2 定案)

`PushConfig.targets: list[str]`、`RouteRuleConfig.targets: list[str]` 校验:非空、去重、`^[a-z][a-z0-9_]*:.+$` 格式、**同平台约束**(元素前缀须与条目通道的平台一致;映射用 schema 内置字面表 `feishu_card→feishu`、`telegram→telegram`,不 import push 层,新平台接入时同步登记)、不支持寻址的通道(webhook/stdout)配 targets 即拒(fail-fast 于配置)。**targets 在场时 `target` 可省**:`_check_channel_target` 的 missing_target 校验放宽为「target 与 targets 至少其一」。

对象是否真在目录里留给运行期 `TargetResolveError`(结构化,带候选)——延续「schema 不反依赖 push 层」的分层约定。优先级:规则 `targets` > 通道级 `targets` > legacy 单 `target`。

### D5:digest 聚合粒度从「每通道」变「每(通道×对象)」

AM/PM 聚合槽语义不变;同一通道的不同对象各收各的卡片(对象间互不串台)。dedup/槽抑制注册表与 targeting 正交,不动。

## 模块清单

| 文件 | 内容 |
|---|---|
| `src/myia/push/directory.py`(新) | `ChannelEntry`(platform/chat_id/name/type/thread_id/last_seen)、`ChannelDirectory`(load/save/merge/aliases/原子写) |
| `src/myia/push/targets.py`(新) | spec 解析(直达 hook → 目录精确 id → 精确名 → 唯一前缀)、`ChannelTarget`、`TargetResolveError`;**直达解析钩子**:`PLATFORMS` 条目可提供 `parse_direct_ref(ref) -> ChannelTarget \| None`(telegram 数字 id/@username、feishu `oc_/ou_/on_/chat_/open_` 前缀 id),命中即跳过目录 |
| `src/myia/push/delivery.py`(新) | 派发循环、`DeliveryLedger` 死信/熔断 |
| `src/myia/push/base.py`(改) | `SendContext.target` 字段;`Channel.supports_targeting` 缺省 False;**增量(实现轮补记)**:`SendReport.skipped: bool = False`(死信跳过/未解析的终态标记,摘要池据此不做无限重试),四通道显式 `supports_targeting = False` 一行(保 `isinstance(Channel)` duck-typing 判定不破,行为零变化) |
| `src/myia/push/__init__.py`(改) | 导出新符号 + `PLATFORMS` 注册表 |
| `src/myia/schema.py`(改) | `targets` 字段两处 + 校验器(含 `CHANNEL_PLATFORMS` 字面表导出) |
| `src/myia/push/route.py`(改,实现轮补记) | `RouteRule.targets`/`RouteDecision.targets` 透传 + `routes_from_config` 接受 targets 字段(D4 优先级「规则 targets > 通道级」的载体) |

管线接线点(事实轮已核):`Pipeline._build_channel` @ pipeline.py:864、`_push_channel` @ pipeline.py:1959-1991、`self._db_path` @ pipeline.py:748 同类可得——构造 `ChannelDirectory(db_path.parent)` + `DeliveryLedger`,传入 immediate(`send_immediate` @ digest.py:179)与 digest(`DigestAggregator.flush` @ digest.py:128)两条路径;digest 从「每通道一卡」升为「每(通道×对象)一卡」(现状平铺池逐通道发,已核)。**节流懒刷(Q4 定案)**:run 前查目录 `updated_at`,>300 秒且存在已注册平台才触发发现;失败退回旧目录+告警。

## 兼容与回滚

- 全部改动叠加式;不配 `targets` 时走 legacy 短路路径,零行为变化。
- 回滚 = revert 上述文件;目录/账本 JSON 是惰性产物,删除无副作用。

## 取舍记录

- **不移植 Hermes 的 asyncio 常驻刷新**(housekeeping 每 5 分钟重建):MYIA 无常驻 gateway,等价物为 run 前节流懒刷(>5 分钟,Q4 定案)+ CLI/桌面手动;core 只提供 `ChannelDirectory` 读写与适配器 `discover_directory()` 入口。
- **不移植 multiplex 多 home、Slack 特判**等 gateway 专属逻辑。

## 与上游的偏离/增量注记(grill 事实轮核订)

- **解析错误内嵌候选列表**:Hermes 只报「Could not resolve」不给候选(靠交互式 `action='list'`);MYIA 无交互回路,错误里直接给候选。
- **目录发现走列表 API(`im/v1/chats`)**:Hermes feishu 根本不调列表 API,目录纯靠入站会话回填;MYIA 出站-only 无入站可回填,列表 API 是 MYIA 侧新增设计(见 feishu 子任务偏离注记)。
- **飞书凭据形态**:MYIA 用手工换取的 tenant_access_token(`env:` 引用,仓库无换取代码);Hermes 是 lark SDK 管 app_id/secret。token 新鲜度是现有运营约定,不新增风险。
- **`last_seen` 字段与按平台桶替换式重建**为 MYIA 增量;Hermes 是整体从零重建、无 last_seen、手工直编目录文件不保留。
