# Design:messaging-feishu — 目录发现 + 定向发送

上游蓝本:`~/.hermes/hermes-agent/plugins/platforms/feishu/`(偏离注记见 prd:列表 API 是 MYIA 侧新增)。前置:messaging-core 契约。

## D1:发现与发送同居 feishu_card.py,不加新文件

「平台 = 通道类」(core design D1)的直接推论:目录发现是 `FeishuCardChannel` 的 classmethod,与 send 共用 token 解析(`DEFAULT_TOKEN_ENV_REF` @ feishu_card.py:54)。feishu_card.py 现 260 行,加发现 ~80 行仍在 cohesive 范围;不为一个平台开新文件。

```python
@classmethod
async def discover_directory(cls) -> list[ChannelEntry]:
    # token 解析复用 send 同款 _resolve_token;失败 → DirectoryDiscoverError(结构化)
```

## D2:列表 API 契约(im/v1/chats)

- `GET https://open.feishu.cn/open-apis/im/v1/chats`,headers `Authorization: Bearer <tenant_access_token>`,params `user_id_type=open_id&page_size=100&page_token=<cursor>`;`has_more=true` 时循环取 `data.page_token`。
- 响应映射:`data.items[].{chat_id, name, ...}` → `ChannelEntry(platform="feishu", type="group", thread_id=None)`。
- **实现期核对项**:items 的字段名以官方文档为准实现时逐字段核对(档内不预写全字段,防凭记忆造假);翻页上限保底(如 20 页)防死循环。
- 429/限频:退避一次再试,再失败按发现失败处理。

## D3:错误语义分层

- **发现失败 ≠ 投递死信**:目录发现失败(401/403/网络)只记结构化日志并保留该平台旧桶,不触碰 `DeliveryLedger`;死信只由投递路径按 core Q3 语义标记。
- **投递侧**:403(forbidden)与 chat 级 not_found(机器人被移出群)按 core 分类标 dead;其余瞬态不标。

## D4:直达解析

`feishu:oc_xxxxx` / `ou_` / `on_` / `chat_` / `open_` 前缀 id 不经目录直达(Hermes feishu 解析器同款形态),挂在 core `parse_direct_ref` 钩子上。

## D5:CLI 家族

`myia channels refresh feishu`(解析凭据 → discover → `directory.merge` → 打印对 AI/人类双友好的目录表,照 cli.py 现有输出风格)与 `myia channels list`(纯读目录)。与 secret/plugin/feedback 的「名词+子动词」家族同构(cli.py:361-458 先例);命令实现放 cli.py,不加新入口文件。

## 兼容与回滚

- 无 targets 配置的 YAML 零行为变化;`supports_targeting` 翻 True 只增加能力不改变缺省路径。
- 回滚 = revert feishu_card.py 与 cli.py 增量;目录 JSON 是惰性产物。
