# Implement:messaging-feishu

前置:core 已收口、本任务 task.json 状态改 in_progress 后才动工。每步收尾跑该步测试,全绿进下一步。

## 步骤

1. **discover_directory() + 单测**
   - feishu_card.py 增 classmethod;httpx MockTransport 模拟翻页(has_more/page_token)、空目录、429 退避、401 结构化错误
   - 实现期对照官方文档逐字段核对 items 映射(设计 D2 核对项)
2. **定向发送 + 直达解析 + 单测**
   - `supports_targeting = True`;`send()` 内 `context.target.chat_id` 优先、退回 legacy `_resolve_target()`
   - `parse_direct_ref` 挂 oc_/ou_/on_/chat_/open_ 前缀;单测覆盖直达与目录解析两条路
3. **注册与 CLI**
   - `PLATFORMS["feishu"] = FeishuCardChannel`(core 交付的注册表)
   - `myia channels refresh feishu` / `channels list` 子命令 + 单测(命令输出、目录 merge、凭据缺失报错)
4. **收尾门禁**
   - `python -m pytest tests/ -q` 全量绿
   - 真机冒烟(Q6 定案对象):refresh → 目录含「AI中转站合伙人群」→ 别名登记私聊 oc_9a79… → `targets: ["feishu:AI中转站合伙人群"]` run 端到端收卡 → 同配置去 targets 回归旧行为

## 验证命令

```bash
python -m pytest tests/ -q
python -m pytest tests/ -q -k feishu
```

## 回滚点

- 步骤 1/2 集中在 feishu_card.py,可整文件 revert;步骤 3 在 cli.py,独立 revert
- 目录/别名 JSON 为惰性产物,回滚后删除无副作用
