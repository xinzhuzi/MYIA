# Design:messaging-ui — 桌面「消息」屏 + sidecar 端点

事实底座(grill 事实轮已核):App.tsx 五屏路由 + screens/<name>/{*-screen.tsx, api.ts, *.test.tsx} 模式;sidecar 行分隔 JSON-RPC、`_HANDLERS` 表 @ desktop/entry.py:911-921、domain.verb 命名;`sources.write` @ entry.py:523-704 是唯一 YAML 写范式(校验门+原子写);协议测试 rpc() 夹具 @ tests/test_desktop_sidecar_protocol.py。前置:core 契约(feishu/telegram 未落地时按空目录开发)。

## D1:屏幕结构(第 6 屏「消息」)

> 实装修订(2026-10-03):App.tsx 注册的是 `screens/*` 真实实现,`routes/*` 是
> C 阶段占位骨架、明确「留作备查不再新增」(App.tsx 头注释)——故不建
> `routes/messaging.tsx`,注册点 = App.tsx 路由第 6 项(yaml-editor 之后)+
> 侧栏 NAV_ITEMS 同位次。屏幕现共 7 条路由,「消息」落在第 6 位恰合本任务编号。

```
screens/messaging/
  messaging-screen.tsx     屏主体,两区布局
  api.ts                   channelsList/Refresh/AliasSet/AliasDelete/pushWrite
  messaging-screen.test.tsx 组件测试(mock sidecar_request)
App.tsx + sidebar.tsx      注册第 6 项(命名直白用「消息」,不用行话)
```

- **上区·通道目录**:按平台分组的列表行(名称/类型徽标/最后发现时间/dead 徽标/别名行内编辑);每平台一个「刷新」按钮(转圈态+失败 toast,照现有屏交互范式);空态(empty-state 组件)给「先配平台凭据」指引。
- **下区·推送规则面板**:按品类 YAML 文件分组列出 push 条目(通道/模板摘要),每条目一个 targets 多选器(选项=上区该平台目录条目,产出 `platform:名称`);保存走 `push.write`。
- 状态管理沿用现有屏的数据拉取模式(不引新状态库)。

## D2:sidecar 端点契约(domain.verb)

| 方法 | params | 返回/行为 |
|---|---|---|
| `channels.list` | `{}` | `{platforms: {<name>: [entry]}, aliases, dead, updated_at, rules}`(目录+别名+死信三态合并视图 + **rules 推送规则视图**——实装修订:下区规则面板的数据源;api.ts 函数集锁死为 5 个,故由本端点附带:扫 plugins 目录各品类 YAML 的 push 条目 `{file, category_id, category_name, parse_ok, entries: [{index, channel, platform, targets, has_template, route_count, raw}]}`,**raw = 条目最小无损形态**(push.write 全量替换的写回 base——UI 改 targets 后整文件提交,None 字段与 webhook 专属传输字段不携带保证回传过同门),坏文件 parse_ok=false 如实入列) |
| `channels.refresh` | `{platform}` | 调该平台 `discover_directory` → merge(桶替换,`ChannelDirectory.commit_platform_refresh`)→ `{merged: n, entries}`;未知平台 `unknown_platform`、无发现 API(telegram 被动积累)`discover_not_supported`、发现失败(凭据/网络)结构化错误,旧桶不动 |
| `channels.alias` | `{platform, chat_id, name}` / `{platform, chat_id, name: null}` | set / delete,写别名文件(原子,经 `ChannelDirectory.set_alias`);写后重读落盘态复核,未生效即结构化报错 |
| `push.write` | `{file: "wool.yaml", push: [完整 push 数组]}` | **全量替换该文件 push[]**:路径围栏(同 yaml.save)→ 原文文本手术只换 push 块(注释保真,空数组=摘除 push 节)→ 手术结果反解析深等门 → `load_category` 同门校验(含同平台约束)→ `.bak` 留底 → 原子写盘;校验失败原样透传结构化错误,不落盘 |

- `push.write` 为什么全量替换而非增量 patch:与 `sources.write` 的文件作用域一致,免发明路径寻址;UI 侧「编辑一条提交整个数组」,冲突面=单文件单编辑器,可接受。
- handlers 进 `_HANDLERS` 表,实现风格随现有(无鉴权——本机 stdin/stdout 传输,维持现状)。

## D3:测试

- 协议测试(进 test_desktop_sidecar_protocol.py):四端点各一正例;`push.write` 坏 targets(跨平台前缀)拒写且文件未变;`channels.refresh` 未知平台报错。
- 组件测试:目录渲染(含 dead 徽标)、别名编辑调用 AliasSet、targets 多选产出正确 spec、保存调 pushWrite;空态/断连态。

## 兼容与回滚

- 纯新增端点+新屏,不动现有屏与既有端点;回滚 = revert 新文件(screens/messaging/ 三件 +
  协议/组件测试)+ 注册行(App.tsx 路由 / sidebar.tsx NAV_ITEMS / entry.py `_HANDLERS` 四行)+
  追加段(entry.py 消息节 + directory.py 两个 additive 方法 `commit_platform_refresh`/
  `aliases_snapshot`,无既有行为改动)+ 镜像文档(spec/desktop/sidecar-protocol.md)。
