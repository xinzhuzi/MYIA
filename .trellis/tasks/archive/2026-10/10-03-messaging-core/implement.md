# Implement:messaging-core

前置:本档开工前 `task.py start`(评审门禁过了才动)。每步收尾跑该步测试,全绿才进下一步;任一步可 `git checkout -- <files>` 单步回滚。

## 步骤

1. **directory.py + 单测**
   - `ChannelEntry` dataclass + `ChannelDirectory`(load/atomic save/merge 平台分桶/别名覆盖,别名在 load 与 merge 双向生效)
   - 测试:空态、别名覆盖、重建后别名仍在、损坏 JSON 容错、并发原子写(tmp+rename)
2. **targets.py + 单测**
   - spec 解析四路径 + `TargetResolveError`(含候选列表);绝不 eval
3. **schema targets 字段 + 单测**
   - `PushConfig.targets` / `RouteRuleConfig.targets`:格式/去重/**同平台约束**(内置字面映射表 `feishu_card→feishu`、`telegram→telegram`)/不支持通道拒配/**targets 在场时 target 可省**(missing_target 放宽)
   - 黄金测试:旧 YAML(无 targets)加载结果与改动前逐字节等价
4. **base.py 扩展**
   - `SendContext.target` 字段 + `Channel.supports_targeting` 缺省 False(现有四通道不动,行为不变)
5. **delivery.py + DeliveryLedger + 单测**
   - 派发循环(fake 通道注入 `PLATFORMS`)、无 targets 短路、失败隔离、熔断进入(**单次硬失败即标 dead**,forbidden/chat 级 not_found;grill Q3 定案,无 N 连败阈值)/退出(1 成功自愈)、死信记录与结构化日志(不发告警卡,同 PRD Req5)
6. **管线接线**(装配点已核:pipeline.py:864/1959-1991/748;digest.py:128/179)
   - 构造 `ChannelDirectory(db_path.parent)` + `DeliveryLedger`,接进 immediate 与 digest 发送路径;digest 聚合升为每(通道×对象)一卡
   - run 前节流懒刷(>5 分钟且有已注册平台才 `discover_directory`,失败退回旧目录+告警)
   - 发现 prd/design 缺口先回改档再写码
7. **收尾门禁**
   - `python -m pytest tests/ -q` 全绿(CI 同款;仓库未配置 ruff/mypy,本轮不引入)
   - docstring 上游标注(蓝本文件 + MIT)逐模块过一遍

## 验证命令

```bash
python -m pytest tests/ -q
python -m pytest tests/ -q -k push    # push 层聚焦
```

## 回滚点

- 步骤 1–5 各自独立成文件/独立测试,单步 revert 无耦合
- 步骤 6 是唯一触碰现网行为的步骤:legacy 短路路径保证不配 targets 时零变化;出问题 revert 接线 hunk 即可
