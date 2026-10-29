# 微信媒体解密件协议 v1(外部可执行件)

> 本文定义 MYIA 微信桥(wechat-bridge)调用**外部媒体解密件**(media decrypt
> provider)的子进程协议——聊天图片 `.dat`(V4 分段加密)与朋友圈 CDN 媒体流
> (Isaac64 keystream)的解密能力整体外包给用户自备的外部可执行件。
> MYIA 产品代码**零解密算法实现**;桥只认协议——不校验来源与签名、不内置降级。
> 第三方凭本文即可写出合规解密件,**无须阅读桥源码**。
>
> - 协议版本:v1
> - 必选动作:`decrypt_dat`、`decrypt_stream`
> - 词汇约定:配置项/UI 文案用「媒体解密件」;协议名与 JSON 字段保留标准英文
> - 真实解密算法(V4 XOR/AES 分段、Isaac64)**永不进 MYIA 仓库**(生态教训:
>   PyWxDump/chatlog 自删、wechat-dump-rs DMCA;仓内参考件 = toy 算法,见 §9)

## 1. 概述

- **触发时机**:桥执行媒体面命令(`localdb media decrypt` / `media scan --decrypt`
  / `sns export --with-media`)且已配置解密件路径(`media_decrypt_path`)时,
  按本协议 spawn 解密件子进程。
- **形态**:任意可执行文件(Python/Shell 脚本、编译二进制均可),须可直接执行
  (有可执行位;脚本须带 shebang,如 `#!/usr/bin/env python3`)。
- **职责边界**:解密件只负责「读入加密文件 → 把明文产物写到桥指定的
  `out_path`」;产物 magic/ext 校验、缓存与容量治理归桥侧(§7)。
- **无降级**:未配置解密件或解密失败时,桥直接报人话错误(exit 23/22,§8),
  绝不静默回退到任何内置解密实现。
- **无来源校验**:桥不校验解密件的签名/来源/实现方式——风险件用户自备、
  自行担责。
- **无状态**:解密件可被并发拉起,应可重入、不落锁文件、不依赖桥内文件布局。

## 2. 钥匙边界(链接者铁律)

- **`decrypt_dat` 的钥匙 = 件内自理**:V4 媒体钥(imageXorKey/imageAesKey)
  由用户供给**自己的**解密件;MYIA 全链对 V4 媒体钥**零接触、零落盘**——
  请求里没有也不会有任何钥字段。
- **件内自理通道落地(2026-10-15)**:仓根生产件
  `scripts/wechat-bridge-mediadecrypt.py` 按序取先命中——
  ① **macOS 钥匙串**条目 `myia / myia/wechat/media-keys`
  (值 = JSON `{"aes_key":"<16字>","xor_key":"E9"}`;
  供给 = `security add-generic-password -U -s myia -a
  myia/wechat/media-keys -w '<JSON>'`,短载荷内联无截断);
  ② **数据根文件** `$MYIA_HOME(缺省 ~/Library/Application
  Support/MYIA)/wechat-bridge/media-keys.json`(建议 chmod 600);
  ③ 手动联调 env `WX_MEDIA_*` / `--key-file`(显式供给最高优先;
  桥 spawn 链 argv/env 恒空,③ 在桥链上天然不可达)。
  调试旁路 env `WX_MEDIA_SKIP_COMPONENT=1` = 跳过①②(测试/复现用)。
- **MYIA 侧纪律(收窄表述,2026-10-14 修订)**:解密钥**不落盘、仅内存过手**。
  `decrypt_stream` 的朋友圈 CDN key 是 sns 库 content XML 内的明文数字串
  (库内公开字段,非秘密材料),经桥 stdin JSON **单次透传**给解密件——
  桥不落盘、不缓存、不中继给第三方;**stdin 载荷零日志**(诊断只承载
  action 与路径,永不承载请求原文)。
- 与取钥器协议(docs/wechat-keyprovider.md)同构:产品代码零取钥实现 +
  零解密实现,能力边界整体外置。

## 3. 调用模型

桥 spawn 解密件为子进程(独立进程组),三条标准流各司其职:

| 流 | 方向 | 用途 |
|---|---|---|
| stdin | 桥 → 件 | 承载**单行 JSON 请求**;桥写完该行后即关闭 stdin |
| stdout | 件 → 桥 | **唯一协议通道**:NDJSON(每行一个 JSON 对象) |
| stderr | 件 → 桥 | 调试旁路,不承载协议;仅失败场景被桥取尾部 500 字符附入错误信息 |

时序:

```text
桥                                        解密件
│  spawn(可执行文件)                       │
│── stdin:{"action":"decrypt_dat",...}\n ▶│  读一行请求
│── 关闭 stdin                            │
│◀─ stdout:{"type":"progress",...}\n ─────│  progress 行,0..N 次
│◀─ stdout:{"type":"result",...}\n ───────│  result 行,恰一次(终态)
│◀─ 子进程退出(退出码)                    │
│  判定(§6)→ 产物 magic 校验(§7)        │
│  → 原子落媒体缓存 → 回报 cached_path     │
```

协议 v1 不定义工作目录、环境变量注入等额外约定;只可假定 stdin/stdout/
stderr 三件套与 120 秒执行预算。

## 4. 请求(stdin,单行 JSON)

### action `decrypt_dat`(聊天图片 `.dat`,V4 分段加密)

```json
{"action":"decrypt_dat","dat_path":"/abs/path/to/Img/<key>.dat","out_path":"/abs/tmp/out-bin"}
```

| 字段 | 类型 | 必含 | 语义 |
|---|---|---|---|
| `action` | string | 是 | `"decrypt_dat"` |
| `dat_path` | string | 是 | 加密 `.dat` 源文件的**绝对路径**(桥已确认在场;件只读) |
| `out_path` | string | 是 | 桥私有区临时产物路径(**件把明文产物写到该路径**;桥随后做 magic 校验并原子移入缓存) |

**钥匙自理**:件自己持有 V4 媒体钥(用户把 imageXorKey/imageAesKey 配进
自己的件——生产件的具体供钥通道见 §2「件内自理通道落地」:钥匙串条目
`myia/wechat/media-keys` 或数据根 `wechat-bridge/media-keys.json`)。

### action `decrypt_stream`(朋友圈 CDN 媒体流;`--with-media` 链)

```json
{"action":"decrypt_stream","in_path":"/abs/tmp/cdn-body.bin","key":"<库内数字串>","out_path":"/abs/tmp/out-bin"}
```

| 字段 | 类型 | 必含 | 语义 |
|---|---|---|---|
| `action` | string | 是 | `"decrypt_stream"` |
| `in_path` | string | 是 | 桥已完成 CDN 下载落盘的密文临时文件(绝对路径) |
| `key` | string | 是 | sns 库 content XML `<url key="…">` 的数字串(库内明文字段;桥单次透传,零落盘零日志) |
| `out_path` | string | 是 | 明文产物落点(同上) |

收到未知 `action` 时,件应回应 `result`(`success:false` + 人话 `error`)并以
退出码 0 退出,或直接以非 0 退出码退出——两种形态桥都判失败(§6)。

## 5. 响应(stdout,NDJSON)

### progress 行(0..N 次,可选)

```json
{"type":"progress","message":"人类可读进度","level":0}
```

纯供人读;协议的机器语义只在 result 行。

### result 行(恰一次,终态)

**成功形态**:

```json
{"type":"result","success":true,"ext":"jpg","is_wxgf":false,"width":1080,"height":1920,"logs":["..."]}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `type` | string | 固定 `"result"` |
| `success` | bool | `true` |
| `ext` | string | 产物格式:`jpg`/`jpeg`/`png`/`webp`/`gif`/`mp4`/`wxgf`(小写;桥按 §7 魔数表核对,`wxgf` 免对照) |
| `is_wxgf` | bool | 产物是否微信实况格式(wxgf);缺省 `false`。`true` 时桥原样保留产物,UI 词面「实况格式,暂不支持预览」 |
| `width`/`height` | int | 可选;件解析出的像素尺寸(桥透传,缺省 null) |
| `logs` | string[] | 人话过程日志;可为空数组 |

**失败形态**:

```json
{"type":"result","success":false,"error":"人话错误","logs":["..."]}
```

行序硬约束(progress 0..N → result 恰一次且最后;stdout 不得混入非协议输出,
调试一律走 stderr)与取钥器协议 §4 完全同文,违例取舍亦同(首条 result 即
终态,其后行读空丢弃)。

## 6. 退出码、超时与 stderr

判定矩阵(与取钥器协议 §6 同构;失败族 = exit 22 `decrypt_failed`):

| 解密件终局 | 桥判定 |
|---|---|
| 输出 `result`(`success:true`),退出码 0 | 解密成功 → 产物 magic 校验(§7)→ 原子落缓存 → 回报 `cached_path` |
| 输出 `result`(`success:false`),任意退出码 | 失败(exit 22);`error` 透传进桥错误信息 |
| 未输出 `result`,退出码非 0 | 失败(exit 22);stderr 尾部 500 字符附入错误信息 |
| 退出码 0 但无 `result` | 失败(exit 22,「未返回结果」) |
| 超过 120 秒未结束 | 桥 kill 子进程组,失败(exit 22,超时) |

## 7. 桥侧校验与媒体缓存

- **产物 magic 校验**(桥只认四图 + mp4 标准魔数;与件声明的 `ext` 一致才收):

  | ext | 魔数 |
  |---|---|
  | `jpg`/`jpeg` | `FF D8 FF` |
  | `png` | `89 50 4E 47 0D 0A 1A 0A` |
  | `webp` | `RIFF` + 4 字节 + `WEBP` |
  | `gif` | `GIF87a` / `GIF89a` |
  | `mp4` | 偏移 4 处 `ftyp` |

  `ext=wxgf` 且 `is_wxgf=true` 的产物**免魔数对照**(微信实况专有容器,无
  标准魔数;桥原样保留)。声明不符/magic 不符 = 拒收(exit 22),不落缓存。
- **媒体缓存**:`<数据根>/wechat-localdb/media-cache/<sha256(源路径+源mtime+
  源size) 前 16 hex>.<ext>`,tmp+rename 原子落位,文件 600/目录 700。
  **幂等**:缓存键命中 = 零再 spawn;源文件 mtime/size 变化 = 新键重解。
  容量治理 = 配置 `media_cache_capacity_mb`(缺省 2048,LRU 按 mtime 清理)。
- **产物只写 MYIA 私有区**:解密件按约定只写桥给的 `out_path`(桥私有区
  临时路径);桥产物只落 media-cache;微信侧文件**零写入**。

### C6(HTTP 媒体端点)对齐注记

`media-cache/` 同时是 C6(task 10-14-wechat-http-api)HTTP 媒体端点的
**指定读取落点**:C6 以路径 allowlist 限定**仅** `<数据根>/wechat-localdb/
media-cache/` 内的文件可经端点暴露,媒体接线以 media-cache 为唯一真源
(C3 本档产出缓存,C6 只读,不带第二份拷贝)。

## 8. 桥侧错误与人话文案

| 情形 | 桥侧行为 | 法定文案/错误 |
|---|---|---|
| 未配置解密件(`media_decrypt_path` 为空) | 不 spawn,直接失败 | **「未配置媒体解密件,请在设置中指定可执行文件路径」**(exit 23 `media_unavailable`;文本链 query/sns feed/导出正文零影响) |
| 配置路径不可执行/spawn 失败 | 人话报错含所配路径 | exit 23 `media_unavailable` |
| `result` `success:false` | 透传件 `error` 字段 | exit 22 `decrypt_failed` |
| 产物 magic 与 `ext` 不符 | 拒收,不落缓存 | exit 22 `decrypt_failed`(「产物格式与声明不符」) |
| 退出码非 0 无 `result` / 0 无 `result` / 超时 | 附 stderr 尾部/超时说明 | exit 22 `decrypt_failed` |

exit 23 `media_unavailable` 为新码(design §6 修订 2026-10-14 增补;
codes.py 封闭表纪律:先改 design §6 再进表)。

## 9. 实现者指南:写一个最小解密件

完整流程:读 stdin 一行 → 解析请求 → 读入加密文件 →(你的真实算法)→
写 `out_path` → 输出恰一条 result 后退出码 0。

参考实现 =
`plugins/wechat-bridge/examples/mediadecrypt_ref.py`(随源码发布,本门面仓不含)
——**toy 算法(字节反转),非真实微信解密**;只示范协议形状,真实算法
(V4 XOR/AES 分段、Isaac64 keystream)用户自备。生产起步骨架(协议壳+钥匙
intake 钩,算法位留空)=
`scripts/wechat-bridge-mediadecrypt.py`(随源码发布,本门面仓不含)
——仓内工具位,不随产品包分发(app resources 白名单不含仓根 scripts/);
永不放入 plugins/wechat-bridge/ 目录(该目录经 tauri.conf.json resources
整目录映射随产品包分发);骨架的 env/--key-file 钩仅手动联调形态有效
(桥 spawn 链 argv 恒空、GUI app 不继承 shell env),钥匙进真实件走件内
自理(§2)。

手动联调(不经桥):

```bash
chmod +x mediadecrypt_ref.py
echo '{"action":"decrypt_dat","dat_path":"/abs/a.dat","out_path":"/tmp/out"}' | ./mediadecrypt_ref.py
```

预期:stdout 逐行 NDJSON(最后一条是 result),退出码 0,`/tmp/out` 出现
明文产物。实现自查清单与取钥器协议 §9 同文(stdout 只协议 NDJSON/flush/
result 恰一/失败带人话 error/≤120s/不等待 stdin 后续/UTF-8)。

## 10. 配置挂点

| 面 | 说明 |
|---|---|
| 桥配置 | `config.json` 键 `media_decrypt_path`(缺省空 = 未配置 = 媒体面结构化降级 exit 23,文本链零影响)与 `media_cache_capacity_mb`(缺省 2048,正整数) |
| 桥管理命令 | `wechat-bridge localdb media-decrypt set <path>` / `get` / `clear`;`set` 校验绝对路径(`~` 自动展开)且文件存在、有可执行位(照 key-provider 判例) |
| 桥媒体命令 | `localdb media status --json`(件配置态+缓存统计)/ `media decrypt <dat_path> --json` / `media scan <chat> --since 30d [--decrypt] --json` |
| MYIA 引擎 | 零改动:解密闭环在桥内,失败语义即 `media_unavailable`(23)/`decrypt_failed`(22) |

### 回滚注意事项

桥的 `config.json` 是**键封闭**装载(未知顶层键 = 整体拒载 exit 2)。桥
二进制回滚到不认识 `media_decrypt_path`/`media_cache_capacity_mb` 的旧版本
(0.7.x 之前)前,必须先 `localdb media-decrypt clear` 并手删配置里的
`media_cache_capacity_mb` 键;清掉后回到旧版现状行为,无其他残留态
(media-cache 目录是纯派生缓存,可整目录删除)。

## 11. 网络纪律修订注记(2026-10-14)

桥级纪律原文为「微信进程零接触、零出网」(localdb/localdb_query 模块
docstring)。本协议引入**唯一受限豁免**:朋友圈媒体回源(`sns export
--with-media`,**缺省关**)对 content XML 内 `cdn_url` 做 HTTPS GET——
只 GET、URL/token 全来自库内、零进程干预、不重试风暴;其余一切桥路径
零出网不变。缺省关 = 不带该旗标时零网络请求(测试以 mock 断言)。
