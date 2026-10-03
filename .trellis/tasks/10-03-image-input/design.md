# Design:看图 — sidecar 视觉通路 + 「看图」屏

前置:prd.md 探查结论的接入点表是本文的事实基础;spec 红线见 `.trellis/spec/python/index.md`(新模块本档即登记)、`.trellis/spec/domain/security-baseline.md`(凭据)、`.trellis/spec/python/error-handling.md`(结构化错误)。

## 总体数据流

```
看图屏(React)
  ├─ 拖/贴:JS 原生读文件 → base64          ┐
  ├─ 选图:tauri-plugin-dialog → 路径/base64 ├→ image.import ─→ MYIA_HOME/images/<hash>.<ext>
  │                                          ┘      (sidecar,同步,毫秒级)
  ├─ image.ocr {id, engine?} ──同步──→ 双引擎(macOS Vision / RapidOCR)→ 逐行 {text, confidence}
  └─ image.analyze {id, mode, channel} ──异步 job──→ 事件流 image.progress / image.completed
                                                    │
                                        ┌───────────┴───────────┐
                                  本地通道(默认)           云端通道(显式切)
                                  OpenAI 兼容端点           同协议 + Bearer key
                                  mlx-vlm :8080 / LM Studio  bigmodel GLM 视觉系
                                  (model 字段=模型路径)      (key 钥匙链 myia/image/api_key)
```

webview 全程不出网(CSP 已锁);出网只发生在 sidecar 进程。缩略图走 `img-src data:`(已放行)。

## sidecar 协议(6 新方法,与 `sources.write` 同风格双侧同步)

| 方法 | 参数 | 返回 | 同步性 |
|---|---|---|---|
| `image.import` | `{kind: path\|base64, value, mime?}` | `{id, path, bytes, ext}` | 同步;>10MB/坏格式 → `image_too_large`/`image_unsupported`;heic 先 sips 转 png 再收 |
| `image.ocr` | `{id, engine?: vision\|rapidocr}` | `{lines: [{text, conf}], engine, ms}` | 同步(两引擎实跑均 ~1s);缺省 engine 取配置默认;失败 → `image_ocr_failed`;非法 engine → `image_engine_unknown` |
| `image.analyze` | `{id, mode: read\|describe\|ask, question?, channel?}` | `{job_id}` 即返,**结果走事件** | 异步;已有 job 在跑 → `image_busy`(仿 `run_busy` 单飞守卫) |
| `image.status` | `{job_id?}` | `{busy, job_id?}`;带 `job_id` 查询时附 `last` = 最近一次终态的 `image.completed` 原文载荷(自带 job_id) | 同步(UI 重连/订阅竞态对账:瞬时失败任务的 completed 可能在 webview 订阅建立前写出而被丢,UI 订阅就绪后按 job_id 拉一次恢复) |
| `image.config.read` | `{}` | 脱敏配置(keychain 引用不回明文) | 同步 |
| `image.config.save` | `{config}` | `{ok}` | 同步;同门校验失败零写入 |

**事件**(走既有 stdout 无 id 行 → `sidecar://event` 泵,`entry.py:759-832` 惯例,前端 `client.ts:138` 已有订阅):
- `image.progress {job_id, stage: ocr|model, pct?}`
- `image.completed {job_id, ok, result | error}`——`result = {text, model, channel, elapsed_ms, ocr_used}`

**错误码**(grill 拍板:统一 `image_` 前缀):`image_not_found / image_unsupported / image_too_large / image_ocr_failed / image_engine_unknown / image_unreachable(附启动指引文案)/ image_no_credentials / image_provider_error / image_busy / image_config_invalid`。全部走 ProtocolError `{code, message, path, data}` 既有契约。

**模式与 prompt**(引擎蓝图=本机 local-ocr 技能,已实跑验证):
- `read`:先跑一级 OCR,初稿嵌校对 prompt「对照图片逐行校对此 OCR 初稿,只修正确有出入的字:<初稿>」(09-30 实证三条已知错行全对;金额/ID 逐位复核提示由 UI 呈现)
- `describe`:固定结构化中文 prompt(主体/构图/风格色彩/氛围/是否疑似 AI 生成)
- `ask`:用户 question 直传

## Python 模块(新登记:`src/myia/vision/`)

| 文件 | 职责 |
|---|---|
| `__init__.py` | 导出 |
| `ocr.py` | **双引擎统一接口**(10-03 修订:两个都要)`run_ocr(path, engine) -> lines[{text, conf}]`:`vision` = ocrmac 封装(zh-Hans+en-US、accurate、逐行置信度;宽<1000px 先 `sips -Z 2000` 放大,临时文件 /tmp 即弃;`usesLanguageCorrection` 保持关——报错码/ID 场景开了会毁证据);`rapidocr` = rapidocr-onnxruntime(内置默认 det/rec/cls 模型零下载;置信度刻度普遍 ≥0.9,与 Vision 不可直接互比);两引擎输出统一 `{text, conf}` 形状 |
| `client.py` | `VisionClient`:复用 `enrich/client.py:40` 的 AsyncOpenAI 模式,新增 image content part 消息(`{type:"image_url", image_url:{url:"data:<mime>;base64,..."}}` + text part,local-ocr 实证配方);发送前长边 >2048 先 sips 压缩再 base64(VL 输入提速);本地通道无 Authorization,云端带 Bearer;读图侧写超时(60s 请求级 + 整体交给事件流不受壳 120s 限制) |
| `settings.py` | `VisionConfig` dataclass + load/save:结构落 `MYIA_HOME/vision.yaml`,门校验照 `EnrichSettings`(`enrich/settings.py:41` 模式)——`api_key` 只收 `keychain:` 引用;缺省 `{channel_default: local, local: {base_url: "http://127.0.0.1:8080/v1", model: ""}, cloud: {base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.6v"(grill 已拍板), api_key: null}, ocr: {enabled: true, engine_default: "vision"}}` |

entry.py 侧 handler 薄封装注册进 `_HANDLERS`;`image.analyze` 后台线程仿 `run.start`(`entry.py:835`);`image_busy` 单飞守卫照抄 `run_busy`。

**命名分层**:`image.*` 管 协议方法与钥匙链前缀(`myia/image/api_key`);Python 模块名 `src/myia/vision/` 是能力实现名——两层不冲突,勿再发明第三种前缀。

**依赖**:pyproject extras `myia[vision] = ["ocrmac>=1.0", "rapidocr-onnxruntime>=1.3"]`(MIT + Apache-2.0;核心 6 依赖红线不破)。**体积影响(10-03 实测)**:uv 装 13 包下载 ~37MB(onnxruntime 20.5MB + rapidocr 14.2MB 含内置模型),PyInstaller 侧车预计 +50MB 级,打包装机冒烟如实记录体积。openai 已在 `myia[llm]`,vision extras 显式含 llm。`build-sidecar.sh` 改用 `uv sync --frozen --extra vision`(沿用 UV_PROJECT_ENVIRONMENT 教训)+ PyInstaller hiddenimports 核对 ocrmac/pyobjc 与 rapidocr(onnxruntime 动态库 + 内置模型 data files);**打包验证看 `desktop/ui` 构建产物**(Tauri 压缩嵌入,grep 二进制验不了的教训)。

## 桌面端

- **壳**:`src-tauri` 加 `tauri-plugin-dialog`;`capabilities/default.json` 增 dialog 权限。CSP 不动。
- **UI 新屏** `screens/image/`(自带 api.ts + vitest,照 sources 惯例):
  - `DropZone`:drag/drop + paste 事件(零权限)+ 「选择图片」按钮(dialog)
  - `Thumb`:缩略图 + 重新选/清空
  - `OcrPanel`:**选图后自动触发**(已拍板);**引擎切换**(Vision/RapidOCR 分段控件,默认按配置,切换即重跑,10-03 修订);行列表,置信度色阶(≤0.5 标警示,附「升二级看图」按钮——仅提示不自动,已拍板);结果标注来源引擎
  - `VisionPanel`:mode 三选(read/describe/ask+输入框)、channel 切换(切云端**首次**弹「图片内容将出网」确认并记住选择,已拍板)、结果 markdown 渲染 + 文本可复制(navigator.clipboard,失败退化选中手动复制)、进行中态(事件流驱动)
  - 路由 `/image`,侧栏「看图」(lucide image 图标,**位次=情报流之后**,已拍板;v1 单图);`types.ts` SidecarProtocol 增 6 方法映射
- **settings 屏**新增「看图」分区 `VisionForm`(复刻三表单风格):默认通道/OCR 默认引擎(vision/rapidocr)/本地 base_url/模型路径/云端模型;api_key 经 `secret.set` 入钥匙链 `myia/image/api_key`(复用 `settings/api.ts` 惯例),save 只落引用

## 隐私与安全

- 本地通道零出网(断网可用是验收项);云端必须用户显式切且知情
- 图片与解读结果只落本地(`MYIA_HOME/images`);日志不记图片内容与解读正文
- 明文凭据拒载铁律照旧;`vision.yaml` 进 `secret.list` 扫描范围

## 测试策略

- 协议契约测试:`tests/test_desktop_sidecar_protocol.py` 增 6 方法往返 + 错误矩阵(mock stdin/stdout,零外网;ocrmac/AsyncOpenAI 全 mock)
- 单测:vision/ocr.py(mock 双引擎 ocrmac 与 rapidocr:引擎路由/非法 engine 分支/置信度阈值分支)、client.py(mock AsyncOpenAI,验 image part 组装/本地无鉴权头)、settings.py(明文 key 拒载/引用解析/engine_default 校验)
- vitest:DropZone 拖贴交互、api.ts 方法映射、VisionPanel 事件流状态机
- 真机冒烟手册(不入 CI):本机 mlx-vlm 起服务 → 真报错截图全链路 → 断网重跑本地通道

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| PyInstaller × onnxruntime(动态库/体积 +50MB 级) | 装机冒烟是门禁(AC10),体积增量如实记录;hiddenimports 清单核对 |
| 本地 VL 服务由用户手起,状态不可控 | `image_unreachable` 结构化错误 + 启动指引文案;analyze 前轻探活(/models) |
| 壳单请求 120s 硬超时 + 首请求 Metal JIT 冷启动 | 全走事件流后台任务(AC5 实证) |
| 并行任务撞同文件:yaml-editor(进行中)同改 entry.py `_HANDLERS`、types.ts、settings 屏 | 按决议①序在看图之前落地;开工先 rebase 最新 main;方法名不重叠,解合并即可 |
| 置信度刻度差(Vision 0.30-1.0 / RapidOCR ≥0.9) | UI 注记来源引擎,阈值统一、不互比 |
| 大图内存/耗时 | 10MB 上限 + VL 前长边压 2048 |

## 回滚

新屏+新模块+extras,零改动既有链路:回滚=revert 提交。`vision.yaml` 不存在时看图屏显示「未配置」引导去设置,不崩。

**v2 扩展原则(grill 已拍板)**:vision.yaml 不设死下载/代管字段——v2「模型下载+server 代管」(第一优先)直接扩 `image.config` schema 与新方法,不破 v1 协议;feed enrich 自动看图(第二优先)独立成档。
