# Design:messaging-weixin-bridge — 微信可选桥接(出站经本机 Hermes)

> 证据基线:本档行号 = 2026-10-03 本机实读实跑。`<HA>` = `~/.hermes/hermes-agent`
> (NousResearch/Hermes-Agent,MIT;MYIA 只调用不 vendor,红线 R5)。PRD 事实
> 引证逐条复核过:`weixin.py:1219-1243`(`send_weixin_direct`)、
> `weixin.py:889-891`(context_token 仅由入站落盘)、`weixin.py:81-88`
> (tokenless 冷发终态 `_session_not_ready_error`)全部实命中,PRD 无需回改。

## D0:调用通道对比定案 —— **① CLI 子进程,淘汰② gateway 控制接口**

三标准(零依赖/超时可控/错误可结构化)逐项对照:

| 标准 | ① CLI 子进程 `bin/hermes send` | ② gateway 控制接口(control_socket / api_server) |
|---|---|---|
| 零依赖 | ✅ stdlib `asyncio.create_subprocess_exec`,MYIA 零新包 | ❌ **发送面不存在**:control_socket 只注册 `identify`/`status`(`control_socket.py:138`,唯一增量 verb 是 `gateway/run.py:5675` 的 `pause-for-update`);api_server 是 LLM API 面(`/v1/chat/completions`/`runs`/`sessions`,`api_server.py:82-101`;rg `weixin\|send_message` 于 `api_server*.py` 零命中)。要用它须改上游加 send verb = vendor 红线 |
| 超时可控 | ✅ `asyncio.wait_for` 包住整个子进程生命周期,kill 兜底;实测 CLI 冷启动 0.65s(2026-10-03 本机,`time` 实跑) | ⚠️ socket 有 2.0s 客户端超时(`control_socket.py:36`)但无发送语义可超时 |
| 错误结构化 | ✅ `--json` 出机器可读结果:成功 `{"success": true, "platform": "weixin", "message_id", "context_token_used"}`、失败 `{"error": "…"}`;exit code 三段 0/1/2(`send_cmd.py:14-16`,`_emit_result` `send_cmd.py:62-81`) | ❌ 同上,无发送应答可结构化 |

CLI 路径全链(读码核订):`hermes send --to weixin:<peer> --json` →
`cmd_send`(`send_cmd.py:194-240`,`_load_hermes_env` 从 `<HERMES_HOME>/.env`
+ `config.yaml` 灌凭据)→ `send_message_tool` → `_send_weixin`
(`send_message_senders.py:585-594`)→ **`send_weixin_direct`**
(`weixin.py:1219-1243`):从盘恢复 `ContextTokenStore`
(`<hermes_home>/weixin/accounts/<account_id>.context-tokens.json`,
`weixin.py:1231-1233`),gateway 在跑则借 live adapter 会话、不在跑则
throwaway aiohttp 会话(**gateway 常驻不是发送前置**,token 由其历史轮询落盘)。
iLink 重试/降级(tokenless 重发一次、限流退避)全部在 Hermes 侧完成
(`_send_text_chunk` `weixin.py:969-1027`),MYIA 只见终态。

**定案:①。** 本机实跑证据(2026-10-03):`hermes send`(缺 `--to`)→
stderr 用法提示 + **exit 2**;`hermes send --list weixin --json` → **exit 0**
+ 列出已发现 DM 会话(peer id 形如 `<opaque>@im.wechat`)。控制面唯一用途
降级为**检测参考**(gateway.sock 存在性,D2),不作发送面。

调用形状(定案):

```
argv = [hermes_bin, "send", "--to", f"weixin:{peer_id}", "--json", "--file", "-"]
body 经 stdin 传入(`--file -` → `sys.stdin.read()`,`send_cmd.py:33-34`)
```

- body 走 stdin 而非 positional:argv 不携带情报内容(日志可安全打 argv),
  且无长度/引号边界问题;`--file` 读文本正是卡片正文形态。
- 环境变量原样继承(用户自定义 `HERMES_HOME` 场景由 Hermes 自家 launcher
  处理,`bin/hermes` 脚本自行落缺省 root);MYIA 不注入任何凭据。
- 超时:`DEFAULT_BRIDGE_TIMEOUT_SECONDS = 120.0` 模块常量,**不设配置口**
  (依据:Hermes 内部重试上界 = 4 次重试 × (API 15s + 3×attempt s 退避) +
  分块间隔 1.5s,`weixin.py:55-56,714-716` ≈ 105s 级;120s 覆盖之。配置口
  只会诱导用户调出比上游重试更小的值,产生伪超时)。超时 → `proc.kill()`
  + 归 `bridge_timeout` 瞬态。

## D1:通道实现(`src/myia/push/weixin.py`,照 ntfy 蓝本形态)

```python
class WeixinChannel(TrendAwareChannel):
    name = "weixin"
    supports_targeting = True
    # __init__(target=None, template=None, renderer=None,
    #          hermes_bin=None, timeout=DEFAULT_BRIDGE_TIMEOUT_SECONDS,
    #          runner=None)   # runner = 测试注入点(async fn(argv, input, timeout)
    #                          #  → (exit_code, stdout, stderr)),缺省 asyncio 子进程
```

- **寻址(R3)**:`context.target.chat_id` 优先,退回 legacy `target` 凭据引用
  (`env:WEIXIN_PEER_ID` 风格,发送期 `resolve_credential`)——与 ntfy/wecom
  同构(ntfy.py:175-196 先例)。对象 = 微信会话 peer id(蓝本 ContextTokenStore
  的 `account:peer` 语义里的 peer 端,`weixin.py:191-193`)。
- **直达解析**:`parse_direct_ref` 用
  `WEIXIN_PEER_RE = ^[A-Za-z0-9_-]+@[A-Za-z0-9.-]+$`(命中
  `xxx@im.wechat` DM / `xxx@chatroom` 群形态;蓝本 `get_chat_info`
  `weixin.py:1194-1195` 的形态依据)。中文别名不匹配 → 落目录四路径
  (别名层,`directory.py` 既有机制),与 R3「别名手工登记 + 直达 peer id」一致。
- **目录**:`discover_directory()` 抛 `DirectoryDiscoverUnsupported`,
  文案如实说明「Hermes 侧可自助 `hermes send --list weixin` 查 peer id;
  MYIA 不做自动发现(R3 定案)」。
- **正文**:用户模板在场走 `TemplateRenderer`,缺省内置纯文本版式
  (`card_title` + 每条「▸ 标题 · URL」行,与 ntfy `build_message` 同款)。
  长文不预切——Hermes 侧 1800/2000 分块是它的本职(`weixin.py:695-698`)。
- **注册**:`CHANNELS["weixin"]` + `PLATFORMS["weixin"]`
  (`src/myia/push/__init__.py:114,135` 两表);
  pipeline `_W2_CHANNEL_FIELD_KWARGS["weixin"] = (("weixin_hermes_bin", "hermes_bin"),)`
  (`pipeline.py:183` 同款先例)。
- **schema**(`src/myia/schema.py`):`PUSH_CHANNELS`(:164)/`PushChannel`
  Literal(:181)加 `"weixin"`;`CHANNEL_PLATFORMS["weixin"] = "weixin"`
  (:874,同平台约束自动生效);新可选字段 `weixin_hermes_bin: str | None`
  (本地路径,非凭据,不入 env:/keychain: 体系)+ 登记宿主表
  `_CHANNEL_OPTIONAL_FIELD_HOSTS["weixin"] = ("weixin_hermes_bin",)`
  (:973,fail-fast 先例同 W2 三平台)。

## D2:凭据与检测(怎么判「本机 Hermes 可用」)

**MYIA 零凭据**:iLink token/QR/context_token 全在 Hermes 侧
(`<hermes_home>/.env` + `weixin/accounts/*.json`),MYIA 不读值、不存、不传。

`probe_bridge(hermes_bin=None) -> BridgeStatus`(纯文件系统探测,只看
存在性,**永不读文件内容**——他人应用的私有数据边界):

| 信号 | 判据 | 依据 |
|---|---|---|
| `bin_found` | bin 路径 `is_file()` 且 `os.access(X_OK)`;缺省 `~/.hermes/hermes-agent/.hermes/bin/hermes`,可被 `weixin_hermes_bin` 覆盖 | 本机实存(shell 脚本 launcher,内嵌绝对 python 路径,自含依赖) |
| `weixin_configured` | `<hermes_home>/weixin/accounts/` 下任一 `*.json`(QR 登录 `save_weixin_account` 必写,`weixin.py:168-173`;本机实存两套账号文件) | hermes_home = `HERMES_HOME` env(在场时)否则 `~/.hermes` |
| `gateway_alive` | `<hermes_home>/gateway.sock` 存在(本机实存,`srw-------`) | 仅咨询信号:发送不依赖 gateway,但 token 续期依赖其轮询——不在场时冷发大概率吃 session 类错误,文案如实说 |
| `available` | `bin_found and weixin_configured` | |

- 不可用两因各自带修复指引:`hermes_missing`(装 Hermes / 配
  `weixin_hermes_bin` 路径)与 `weixin_not_configured`(Hermes 侧
  `hermes gateway setup` 扫码登录微信)。
- **R2 加载零报错**:探测只在**发送期**与 **UI 探测**(D4)发生;配置
  加载/通道构造只存 bin 路径字符串,零文件系统检查——无 Hermes 环境
  配置照常加载,schema 校验与 Hermes 是否在场无关。

## D3:错误映射表(Hermes 终态 → MYIA PushSendError → core 死信语义)

调用面输出 = (exit_code, stdout JSON)。映射(core 分类器
`classify_dead_error` `delivery.py:131-143`:仅 `forbidden`/chat 级
`not_found` 标 dead,其余瞬态):

| Hermes 终态(exit / 判据) | PushSendError code | 消息要点(含修复指引) | 死信 |
|---|---|---|---|
| 0 + `{"success": true}` | —(成功) | — | — |
| 1 + `{"error": "Weixin token missing…"}` / `"Weixin account ID missing…"`(`weixin.py:1227-1230`) | `bridge_unavailable` | Hermes 在场但微信未配置;指引:Hermes 侧扫码登录 | 否(瞬态,配置态可修复) |
| 1 + requirements 未达(`"Need aiohttp + cryptography"` / `"adapter not available"`,`send_message_senders.py:585-590`) | `bridge_unavailable` | 指引:`hermes pm repair` | 否 |
| bin 不存在 / 不可执行(FileNotFoundError / probe 预检失败) | `bridge_unavailable` | 指引:装 Hermes 或配 `weixin_hermes_bin` | 否 |
| 1 + error 含 `session not ready`(`weixin.py:81-88` 终态文案 "the user must send the bot a message first") | `session_not_ready` | 「须先让对方给 bot 发条消息(或重新配对)」结构化指引 | **否(R4 明文:可修复态不标 dead)** |
| 1 + error 含 `ret=-14` / `errcode=-14`(tokenless 重发后仍会话过期,`weixin.py:988-998` 路径) | `session_not_ready` | 指引:对端先发消息,或 Hermes 侧重新扫码 | 否 |
| 1 + `rate limited`(`weixin.py:999-1011`) | `weixin_send_error` | iLink 限流,Hermes 侧退避已尽 | 否(瞬态) |
| 1 + 其余 `iLink sendmessage error: ret=…` | `weixin_send_error` | 原文带 ret/errcode/errmsg | 否 |
| 1 + stdout 非法 JSON / 空 | `weixin_send_error` | 带原始 stderr 片段 | 否 |
| 2(用法错 = MYIA argv 契约漂移,自家 bug) | `bridge_usage_error` | stderr 原文;ERROR 级日志(需要介入) | 否 |
| 子进程超时 | `bridge_timeout` | 120s 上限 + kill 记录 | 否 |

**零死信设计**:iLink 蓝本不存在 peer 级 forbidden/not_found 信号
(`weixin.py` 全文无此错误族),按 R4「仅 forbidden/对端不存在标 dead」
= 本通道**任何错误都不标 dead**。防误伤归一化:Hermes HTTP 层错误文本
形如 `HTTP 404`(`weixin.py:274`),会撞 core 分类器的 `"http 404"`
marker(`delivery.py:93`)误判 not_found→dead;桥接层抛错前对消息做
`HTTP (\d{3})` → `HTTP状态码\1` 归一(中文字符不可能撞 ASCII marker 表)。
**实现期闭口(2026-10-03)**:`\1` 若保留 ASCII 数字,`HTTP 403` 归一后
残留的 `403` 仍撞 forbidden 裸 marker(`delivery.py:70`)破零死信——归一
替换把状态码数字一并转全角(`HTTP 403` → `HTTP状态码４０３`),该 span
内无任何 ASCII 数字/空格组合,marker 表全部免疫。
单测断言:上表每一行的错误对象过 `classify_dead_error` 必得 `None`。

## D4:UI 灰卡披露接线

1. **sidecar 新方法 #28 `bridge.status`**(`desktop/entry.py` `_HANDLERS`
   注册表唯一改动点 + `tests/test_desktop_sidecar_protocol.py` 契约用例;
   `sidecar-protocol.md` 镜像随更):params `{}` → result =
   `probe_bridge()` 全量(BridgeStatus dict:available/reason/fix_hint/
   bin_found/weixin_configured/gateway_alive/bin_path)。纯文件系统探测,
   无凭据、无出网。
2. **前端状态机**(`platform-overview.tsx`):微信从 `UPCOMING_PLATFORMS`
   (:236)移入 `IMPLEMENTED_PLATFORMS`(:81 表),`wave: "W2"`、
   `discovery: "manual"`;`PlatformCardStatus` 增第四态
   **`bridge_unavailable`**(灰,tone 复用 coming_soon 的 muted token 组:
   点 `bg-muted-foreground/50`、胶囊/描边同 `:397-408` 灰值),状态文案
   **「需本机 Hermes」**。派生:微信卡不走通用
   `deriveImplementedStatus`(:324),改用 `bridge.status` 结果——
   `available → "connected"`、不可用 → `"bridge_unavailable"`
   (永黄不了:凭据不在 MYIA 侧,黄态「按凭据指南录入」是误导)。
   `buildPlatformCards` 增可选 `bridgeStatus` 入参(向后兼容缺省)。
3. **披露文案(R2「不装可用」)**:详情面板 description 与状态说明如实写
   「微信无官方出站 API;本通道是桥接实现,依赖本机常驻 Hermes 持有微信
   登录态与 context_token。无 Hermes 的环境此平台不可用——这是如实披露,
   不是缺陷」。凭据指南(steps)= 装 Hermes → `hermes gateway setup` 扫码 →
   `~/.hermes/hermes-agent/.hermes/bin/hermes send --list weixin` 自查
   peer id → 规则 targets 直达 `weixin:<peer>` 或别名登记;keys 段为空
   (MYIA 侧零凭据,文案明说)。
4. **屏私有封装**:`screens/messaging/api.ts` 加 `bridgeStatus()`
   invoke 直连(照 `channels.*` 先例,不入共享门面——sidecar-protocol.md
   变更纪律 3)。
5. 图标已在位(`platform-icons.tsx:83` weixin 条目)。**实现期闭口(2026-10-03)**:
   原条目是 generic 灰标(未实装形态);微信转实装后按 icons 测试不变量
   (已实装 → brand 精确标)升为 brand monogram「微」+ 微信官方绿
   `#07C160`(钉钉/企微同范式,一行改动)。

## D5:开源边界披露(R2/AC4)

一句话披露三处落点:根 `README.md`(推送通道叙述段,「pushes what matters
to your messaging apps」`README.md:339-340` 附近)注「Weixin outbound is a
bridge via a local Hermes-Agent install — without one, the weixin channel is
unavailable」;`docs/zh/getting-started.md:42`(推送通道段)与
`docs/en/getting-started.md` 对应段各加一句(双语对照纪律:改一侧须同步
另一侧)。

## 兼容与回滚

- 纯增量:一个新通道文件 + schema 枚举/字段扩展 + sidecar 一个新方法 +
  UI 状态位。不配 weixin 通道 = 零行为变化(golden 回测逐字节等价口径)。
- 入站零出现(红线):无轮询、无 callback、无目录发现,唯一交互方向 =
  出站子进程;`hermes send` 本身是纯出站路径(`send_cmd.py` 无 getupdates)。
- 回滚粒度见 implement.md 各步;依赖红线自检:`import` 清单 =
  stdlib(asyncio/subprocess/os/re/pathlib)+ 既有 httpx 通道基类
  (实际未用 httpx,纯 stdlib)。
