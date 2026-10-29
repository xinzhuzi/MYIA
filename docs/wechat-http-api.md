# 微信本地库 HTTP API(wechat-bridge localdb serve)

> 协议文档 v1(组件 0.8.0;task 10-14-wechat-http-api)。把桥 localdb 只读能力
> 映射为本机 HTTP 常驻服务,供外部系统/自动化脚本/LLM 工具对接。**纯只读**:
> 一切端点读解密副本,零微信进程干预、零写微信侧数据。
>
> 接口形态锚 WeFlow `docs/HTTP-API.md` 的**语义**(端点族谱/SSE 契约/ChatLab
> 水位协议/GET+POST 统一),代码与文案零拷贝(其许可 CC BY-NC-SA 4.0,只借
> 设计思想,不可沾其码)。写/装卸类端点整族排除——本 API 永无 DELETE/PUT
> 处理器与 hook 端点(组件测试 grep 守卫钉死)。

## 快上手

```console
# ① 生成访问 token(明文仅本次应答显示;再生成=轮换热生效)
python -m wechat_bridge localdb http-token generate --json
# {"http_token": {"result": "generated", "token": "<43 字>", "length": 43}}

# ② 起服(前台常驻;缺省 127.0.0.1:5032;Ctrl-C / SIGTERM 干净退)
python -m wechat_bridge localdb serve --json
# {"http_api": {"host": "127.0.0.1", "port": 5032, "token_configured": true, "version": "0.8.0"}}

# ③ 调用(token 三通道,主推 Bearer——不进 shell history/进程列表)
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:5032/api/v1/health
curl -s -H "Authorization: Bearer <token>" "http://127.0.0.1:5032/api/v1/messages?chat_id=<会话id>&since_days=7"

# ④ 事件流(SSE;EventSource 不能设头 → 本端点另收 URL access_token)
curl -N "http://127.0.0.1:5032/api/v1/push/messages?access_token=<token>"
```

CLI 形态:`localdb serve --json [--host H] [--port P]`(flags 覆写配置键)与
`localdb http-token generate|show|clear --json`。`show` 为本机主人面(配置
600 私有区);`clear` 吊销 token(此后一切非 health 请求 401)。

## 鉴权(token 三通道;未配置即全拒)

| 通道 | 适用端点 | 说明 |
|---|---|---|
| `Authorization: Bearer <token>` 头 | 全端点 | **主推**:token 不进 URL/进程列表/访问日志 |
| POST JSON body `{"token": …}` | 全端点 | 与查询参数同请求并用(参数并入语义) |
| URL `?access_token=<token>` | **仅 SSE 端点** | EventSource 不能设头;其余端点 query 通道=401 |

- token 未配置(空/键缺):一切非 `/api/v1/health` 请求 **401**(防裸奔),
  错误体 `fix_hint` 指向 `localdb http-token generate`。
- 比较为常时实现(`hmac.compare_digest`);token 每请求热重读配置文件——
  `generate`(轮换)/`clear`(吊销)无须重启服务即刻生效。
- 配置文件损坏:沿上次有效 token 继续服务,`/health` 如实加 `config_error`
  注记(不炸服务不静默)。
- CORS:仅当 `Origin` 的 host 属 `localhost|127.0.0.1|[::1]`(任意端口)时回
  `Access-Control-Allow-Origin: <origin>` + `Vary: Origin`;其余 Origin 不回
  任何 CORS 头。OPTIONS 预检同规(204)。

## 端点全表(全只读;GET/POST 统一,POST JSON body 参数并入查询参数)

| 端点 | 参数 | 行为 |
|---|---|---|
| `GET/POST /api/v1/health` | — | **token 豁免,恒 200**。`{"health":{"state","key_status","bridge_version","uptime_seconds"}}`;state ∈ `ok\|key_missing\|key_stale\|db_missing` |
| `GET/POST /api/v1/sessions` | `limit?`(1..200,缺省 20) | 会话列表(session+contact 域副本编排);应答 `{"sessions":[{"id","name","unread"}],"domains","copy"}` |
| `GET/POST /api/v1/messages` | `chat_id`(必)、`since_days?`(1..3650,缺省 30)、`limit?`(1..1000,缺省 50) | 与 `localdb query` 同源编排,应答=CLI 载荷原样(消息按时间倒序,最近在前) |
| `GET/POST /api/v1/sessions/{chat_id}/messages` | `since`(epoch 秒,必)、`end?`(epoch 秒,不含)、`limit?`(1..5000,缺省 500)、`offset?`(≥0,缺省 0) | **ChatLab Pull 水位协议**(见下节) |
| `GET/POST /api/v1/group-info` | `chat_id`(必) | 与 `localdb group-info` 同源;缺表降级空结构恒 200 |
| `GET/POST /api/v1/sns` | `mode?`(feed\|notices,缺省 feed)、`since_days?`(1..3650,缺省 3) | 与 `localdb sns` 同源编排 |
| `GET /api/v1/media/<rel>` | — | 只服务 `<数据根>/wechat-localdb/http-media/` 白名单目录内文件;目录外/不存在=404(媒体导出缓存由朋友圈/媒体导出集成注入,未接线前目录为空=如实 404) |
| `GET/POST /api/v1/push/messages` | `access_token`(SSE 专用 token 通道) | SSE 事件流(见 SSE 契约) |

示例:

```console
$ curl -s -H "Authorization: Bearer T" \
    "http://127.0.0.1:5032/api/v1/sessions/50438708676@chatroom/messages?since=1760000000&limit=2"
{"chat_id": "50438708676@chatroom", "since": 1760000000,
 "messages": [
   {"ts": 1760000123, "sender": "wxid_zhang", "type": "text", "text": "早上好"},
   {"ts": 1760000456, "sender": "me", "type": "image", "media_placeholder": "非文本消息,内容未导出(占位)"}],
 "sync": {"hasMore": false, "nextSince": 1760000456, "nextOffset": 0, "watermark": 1760000456}}
```

### ChatLab Pull 水位协议(`/api/v1/sessions/{chat_id}/messages`)

- 请求带 `since`(epoch 秒)增量拉取:`create_time > since`(且 `<= end` 若给)
  升序 + `LIMIT/OFFSET` 翻页。
- `sync` 块:取满 `limit` → `hasMore=true, nextOffset=offset+limit,
  nextSince=since`(翻页期 since 不动);未取满 → `hasMore=false,
  nextOffset=0, nextSince=watermark`(一轮拉毕跳水位)。
- `watermark` = 该会话表当前 `max(create_time)`;消费方把 `nextSince` 作为
  下轮 `since` 即为稳态增量环。
- 全量历史/深分页走本端点;**深分页(OFFSET 数十万)依赖副本索引自举**
  (C1),无索引时大偏移翻页性能按真库实测口径评估(门控基准落档)。

## SSE 事件契约(`/api/v1/push/messages`)

- 帧形态:`id: <单调整数>` + `event: message` + `data: {"type":
  "message.new"|"message.revoke","chat_id","ts","sender",…}`;
  `message.new` 载 `text?`(文本)或 `media_placeholder`;`message.revoke`
  由 `local_type=10002` 记录+残留原消息合成,携 `orig_preview?`(≤30 字;
  原消息缺失=仅撤回事实,不臆造内容)。
- **心跳**:每 25s 注释帧 `: ping`。
- **重放**:环形缓冲 ≤1000 事件 / 10 分钟 TTL;断线带 `Last-Event-ID`
  重连 → 补发该 id 之后仍在缓冲内的事件;超量/超期淘汰如实(重连过晚 =
  经 Pull 端点补齐)。
- **去重**:交付为 at-least-once——扫描边界秒(与水位同秒)的行可能重发,
  消费端按内容锚 `(chat_id, ts, sender, sha1(text)[:16])` 去重(与 MYIA
  管线引擎 `wechat_local` 的源级水位内容锚同族)。
- **延迟口径(如实)**:事件延迟 = 扫描间隔量级(**分钟级**,缺省 60s 一轮;
  单轮覆盖 session 表前 50 会话)。本 API 不提供亚分钟级事件——需要更细
  粒度请用 Pull 端点按 watermark 轮询。扫描不可得(密钥缺位等)时水位不
  推进,恢复后断档间隙如实补发。

## 错误码映射

| HTTP | 场景 | 载荷 |
|---|---|---|
| 400 | 参数形状错(缺必填/越界/坏 JSON body) | `{"error":{"code":"usage",…}}` |
| 401 | 未配置 token 全拒 / token 不符 / query 通道用于非 SSE | `{"error":{"code":"unauthorized","fix_hint":…}}` |
| 404 | 未知路由 / 媒体文件不在白名单目录内 | `{"error":{"code":"not_found",…}}` |
| 405 | 非 GET/POST/OPTIONS 方法(媒体下发仅 GET) | `{"error":{"code":"method_not_allowed",…}}` + `Allow` 头 |
| 500 | 未预期异常(日志只载形状) | `{"error":{"code":"internal",…}}` |
| 503 | 桥错误族:密钥缺位(21)/库缺位(20)/解密失败(22) | `{"error":{"code":"key_unavailable\|db_not_found\|decrypt_failed","message","fix_hint"}}`(码名/文案与 CLI 错误载荷同源) |

## 安全边界

- **回环强制(Red Line)**:服务只绑 `127.0.0.1|localhost|::1`;配置了
  `0.0.0.0` 等非回环值 → 启动 exit 2 结构化拒绝。对外暴露解密聊天数据是
  红线(chatlog 族收函/下架判例在案)。外部系统经反向代理/SSH 隧道转发
  回环地址,由转发方自担其责。
- **token**:见鉴权节;未配置即全拒,常时比较,热轮换。
- **媒体 allowlist + 路径穿越守卫(Red Line)**:`/api/v1/media/` 只服务
  `http-media/` 白名单目录;`../`、`%2e%2e%2f`、`..%2f`、绝对路径、符号
  链接出根等变体一律 404——`decrypted/` 副本、`keys.json`、配置文件结构
  性不可达。
- **端口**:缺省 5032(刻意避开 5030/5031,防与 chatlog 族工具同机相撞)。
- **日志红线**:访问日志只载形状(method/path(去 query)/status/耗时),
  零聊天内容、零密钥;SSE 的 `access_token` 在 query 里,永不落日志。

## 配置键(数据根私有区 `wechat-bridge/config.json`,封闭清单)

| 键 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `http_api_enabled` | bool | `false` | 仅桌面托管消费(显式 CLI 起服不读它) |
| `http_api_host` | str | `"127.0.0.1"` | 仅回环值可用(serve 面强制) |
| `http_api_port` | int | `5032` | 1..65535 |
| `http_api_token` | str | `""` | 空=未配置=全拒;由 `localdb http-token` 命令代写 |

服务自有私有产物:`<数据根>/wechat-localdb/http-watermark.json`(SSE 水位,
600,仅时间戳)与 `<数据根>/wechat-localdb/http-media/`(媒体下发白名单
目录,700)。

## 公开分发风险知情注记(记录在案)

本能力面(解密聊天数据 → 可编程 HTTP)与下列被打击件同面:sjzar/chatlog
(2025-10-20 收微信官方函件清库)、0xlane/wechat-dump-rs(2026-01-08 GitHub
DMCA,Tencent 申诉)、MemoTrace(清库)、PyWxDump(删库)。缓解 = 只读解密
副本 + 密钥不出数据根私有区 + 本机回环 + token + 不对外分发密钥获取能力;
**若 MYIA 公开仓库发布,收函/DMCA 风险只能降低不能归零**(主人 2026-10-13
已圈定 HTTP API 功能令,风险知悉后按令推进)。许可纪律:WeFlow 为
CC BY-NC-SA 4.0,本 API 只借其设计思想,代码/JSON 结构/文案零拷贝。
