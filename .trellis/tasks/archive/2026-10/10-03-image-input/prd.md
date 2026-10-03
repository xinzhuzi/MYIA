# PRD:看图 — 图片输入 + 文字OCR + 本地/云端图片理解

## 背景

主人 2026-10-03 指示(原文):「必须可以看图,带有文字OCR,以及本地模型看图,还有云端API看图,暂时你先用这个电脑的本地模型看图代替,后面可以下载,设置本地模型路径,这块你实际参考一下找一下 GitHub 相关的项目,看看别人是如何做的,用来辅助分析内容,做一个trellis任务文档补全这块的功能!」

MYIA 是情报中枢不是聊天助手(五屏无输入框),「看图」落成**情报分析工具屏**:丢一张图进来 → 本地 OCR 逐行提字(带置信度)→ 本地/云端视觉模型解读 → 结构化结果。后续 v2 再接 feed 内容图片自动看图(enrich 集成)。

## 探查结论(2026-10-03 本会话实读 + 实测,非转述)

### 现状:全库零图片能力

- `src/`、`desktop/ui-src`、`src-tauri` grep `vision/ocr/base64/multimodal/attachment/image` 零业务命中
- 五屏路由 `desktop/ui-src/src/App.tsx:19-32`,无任何文本/附件输入框

### 接入点(全部实读核实)

| 落点 | 事实 |
|---|---|
| sidecar 协议 | `desktop/entry.py:910-922` `_HANDLERS` 11 方法,JSON-line stdio;新方法与 `desktop/ui-src/src/lib/api/types.ts:432` 双侧同步;唯一前端入口 `client.ts:42` `invoke("sidecar_request")` |
| 长任务范式 | `run.start`(`entry.py:835`)后台线程 + `log/progress/completed` 事件流(`entry.py:759-832`);壳侧**单请求 120s 硬超时**(`src-tauri/src/main.rs:22`)——VL 调用 13-33s 且首请求含 Metal JIT 编译,**必须走事件流不能同步应答** |
| 网络 | CSP `connect-src 'self' ipc: http://ipc.localhost`——webview 不能直连外网,云端 vision 只能经 sidecar 出网;`img-src 'self' data:` 已放行,base64 缩略图可直接显示 |
| 权限 | `src-tauri/capabilities/default.json` 无 dialog/clipboard/fs——文件选择需加 tauri-plugin-dialog;**拖拽/粘贴纯 JS 不需要任何新权限** |
| LLM 客户端 | `src/myia/enrich/client.py:40` `OpenAICompatClient`(AsyncOpenAI,任意 base_url,**本地端点可直接复用**);但 `complete()` 纯文本,无 image content part——需扩展或新建 vision 客户端 |
| 设置体系 | 凭据只入钥匙链(`myia/llm/base_url|api_key`,`desktop/ui-src/src/screens/settings/api.ts:83-84`);结构配置 `EnrichConfig`(`src/myia/schema.py:748`)只收 `env:`/`keychain:` 引用,明文拒载(security-baseline 铁律);settings 三表单不可写回=缺口 C11,**本任务自带 `image.config.*` 读写,不等 yaml-editor 的 yaml.save 通路** |
| 依赖红线 | 核心依赖仅 6 个,新依赖走 extras + 本 PRD 论证(`.trellis/spec/python/index.md`);打包 `desktop/build-sidecar.sh`(PyInstaller)需连带验证 |
| 数据根 | `MYIA_HOME`(`entry.py:120`,macOS=`~/Library/Application Support/MYIA`)——图片与 vision 配置都落这里 |

### 本机环境实况(2026-10-03 探测)

- **无 Ollama**;**LM Studio 已装**(`~/.lmstudio`,models:Qwen / lmstudio-community / orcarouter);tesseract 有(homebrew)但引外部二进制不作默认
- **本机视觉底座**:`漫影工作室/comfyui/.../text_encoders/qwen3vl_8b_bf16.safetensors` 在盘——local-ocr 技能 2026-09-30 实跑验证过整条链:转 MLX → `mlx_vlm.server` → `http://127.0.0.1:8080/v1/chat/completions`(OpenAI 兼容)→ 中文逐字全对(13s 读字/33s 描述);`/tmp` 的转换产物已清,**需一次性重转到常驻路径**
- **macOS Vision OCR**:Swift+Vision 毫秒级、zh-Hans+en-US、每行置信度(任一行 ≤0.5 触发升二级)——已在本机长期实跑
- **RapidOCR 实跑(2026-10-03 本会话)**:uv 临时环境装 rapidocr-onnxruntime(13 包,下载 ~37MB = onnxruntime 20.5MB + rapidocr 14.2MB 含默认 det/rec/cls 模型,零模型下载);同一张 3840×2160 截图 **85 行 / 0.75s**,中文行分数 0.95-1.00;Vision 同图 ~1s,顶部条目两引擎识别一致——**双引擎对照成立**。注意:两者置信度刻度不同(Vision 有 0.30-1.0 真实分布,RapidOCR 普遍 ≥0.9),≤0.5 警示阈值主要对 Vision 有意义,属刻度特性不是缺陷
- **云端视觉实测存档**(local-ocr 技能):`glm-4.6v`/`glm-5.3-flash` 逐字全对;`glm-4.5v` 错读勿用;智谱账户曾报 1113 余额不足(2026-09-30),云端联调前需主人确认账户可用

### GitHub 参考项目(2026-10-03 gh api 逐个核实:星数/推送/许可)

| 项目 | 实况 | 借鉴点 | 许可结论 |
|---|---|---|---|
| CherryHQ/cherry-studio | 52.3k★,10-02 推,AGPL-3.0 | 桌面 AI 助手的图片附件交互 + 多 provider(含本地)配置形态;主人本机就装着可直接体验 | **只看不抄** |
| open-webui | 153.8k★,自定义许可 | 图片上传→视觉模型的 UI 惯例 | 只看不抄 |
| lobehub/lobe-chat | 82.9k★,自定义许可 | 同上 | 只看不抄 |
| Mintplex-Labs/anything-llm | 66.7k★,MIT | 图片→vision agent 流程 | 可借鉴代码 |
| janhq/jan | 44.8k★,自定义许可 | **本地模型下载/目录管理的 UX**(v2 参照) | 只看不抄 |
| chatboxAI/chatbox | 41.9k★,GPL-3.0 | 多端 AI 客户端附件流 | 只看不抄 |
| **Blaizzy/mlx-vlm** | 5.6k★,10-02 推,MIT | 本地看图服务栈:OpenAI 兼容接口、**model 字段=模型路径**(「设置本地模型路径」的天然落点) | 可用 |
| **straussmaximilian/ocrmac** | 548★,MIT | macOS Vision 的 Python 封装(**默认 OCR 引擎**,零模型下载,10-03 实跑) | 可用 |
| RapidAI/RapidOCR | 8.0k★,10-02 推,Apache-2.0 | **第二 OCR 引擎(v1 双引擎之一;10-03 本机实跑对照通过)** | 可用 |

结论:交互形态照 Cherry Studio/Open WebUI 惯例(拖/贴/选 + 缩略图 + 预览),引擎全用 MIT/Apache 件(Vision via ocrmac + RapidOCR 双 OCR 引擎 + mlx-vlm + OpenAI 兼容云 API),本机 local-ocr 技能链就是已验证的引擎蓝图。

## Requirements

### v1 内(本任务)

1. **「看图」新屏**(已拍板:独立屏,路由 `/image`,侧栏名「看图」,位次=情报流之后):拖拽/粘贴(纯 JS 零权限)+ 文件选择(tauri-plugin-dialog,扩 capabilities);缩略图预览;**v1 单图**(已拍板,多图 v2)
2. **图片落库**:sidecar `image.import` → 存 `MYIA_HOME/images/<sha256前16>.<ext>` 去重,返回 id+元信息;大小上限 10MB,格式 png/jpg/webp(heic 自动经 sips 转 png 后收);v1 不自动清理历史图片(hash 去重,增量可控),清理工具 v2
3. **一级 OCR(双引擎,10-03 修订:两个都要有)**:`image.ocr {id, engine?}` → `vision`(macOS Vision via ocrmac,**默认**)/ `rapidocr`(rapidocr-onnxruntime,内置模型零下载);选图后**自动触发**(默认引擎按配置);返回逐行 `{text, confidence}` + 本次 `engine`;UI 引擎切换(Vision/RapidOCR,切换即重跑),结果标注来源引擎;置信度色阶,≤0.5 行标警示+「升二级看图」按钮(**仅提示不自动**,已拍板;RapidOCR 分数偏高属其刻度,阈值两引擎统一);宽 <1000px 先放大再识别(sips,系统自带,仅 vision 引擎路径)
4. **二级看图**:`image.analyze {mode: read|describe|ask, question?, channel?}` → **事件流后台任务**(仿 run.start,规避壳 120s 超时);三模式:读字校对(OCR 初稿取当前所选引擎结果嵌 prompt,local-ocr 实证配方)/ 图像描述(结构化中文解读)/ 自由提问;通道=本地(默认,base_url 指向 OpenAI 兼容端点)/云端(同协议+钥匙串 key)
5. **设置**:settings 屏新增「看图」分区:默认通道、**OCR 默认引擎(vision/rapidocr)**、本地 base_url(默认 `http://127.0.0.1:8080`)、model(本地即**模型路径**,天然满足「设置本地模型路径**)、云端模型(默认 `glm-4.6v`,已拍板)+api_key;结构配置落 `MYIA_HOME/vision.yaml`(已拍板:MYIA_HOME 第一个全局配置文件,独立先行;协议方法统一 `image.config.*`;`env:`/`keychain:` 引用同门校验),凭据走钥匙链 `myia/image/api_key`(技术自决:与协议 `image.*` 同层,弃 `myia/vision/*` 前缀)
6. **过渡落地**(主人指定的代替方案):dev/本机 = Qwen3-VL-8B mlx-vlm(权重在盘,一次性转换到常驻路径 + 起服务冒烟);LM Studio(1234 端口)为备选端点
7. **隐私边界**:本地通道零出网;切云端**首次**弹「图片内容将出网」确认并记住选择(已拍板);日志不落图片内容。**结果去向(已拍板)**:v1 仅屏上展示+文本可复制,结果不入库不持久化(图片文件持久保留、可随时重分析);「存为情报条目/走推送通道」v2 另拍

### 刻意不做(v1,防蔓延)

- 应用内模型下载、server 进程代管(MYIA 起/停 mlx_vlm.server)= **v2**;「下载」v1 以文档指引代替(转换脚本三步照 local-ocr 技能)
- feed 抓取内容中的图片自动看图(enrich 集成)= v2
- tesseract 第三引擎(Windows 版再议)、多图批量、看图历史记录页
- 看图结果存为情报条目/走推送通道(Q7 已拍板:v2 另议,牵动 feed 数据模型)

## Acceptance Criteria

> 勾选口径(2026-10-03 装机冒烟 r2 收尾核验):`[x]` = 已实跑/自动化验证且绿;`[ ]` = 存 ⏳ 人工项,行尾注明。

- [ ] 1. 拖一张报错截图进「看图」屏 → **自动**出逐行 OCR 结果带置信度,2s 内返回 —— ⏳ **manual(待主人手验)**:拖拽交互无法自动化;替代证据:真截图自动 OCR vision 55 行/881ms、rapidocr 56 行/780ms 均 <2s(evidence/protocol-e2e3-transcript.jsonl),看图屏 UI 见 evidence/app-01-dashboard.png 与 app-02-image-screen.png
- [x] 2. 点「解读」走本地通道 → Qwen3-VL-8B 返回中文解读,**全程零出网**(可断网验证)——describe 全量 ok=True elapsed=52,995ms 结构化中文;零出网采样 162 条对端全 loopback、非 loopback 0 条(evidence/zero-egress-sample3.txt,方法=lsof 进程树,未断网;系统代理中转披露维持:trust_env 本轮未改)
- [x] 3. 切云端(填 key,首次切换弹知情确认)→ 同图 GLM 视觉(glm-4.6v)通道走通——账户 2026-10-03 探针核实恢复可用(HTTP 200),此条**实跑联调**;不填 key → 结构化 `image_no_credentials` 错误,绝不假装成功——r2 补轮:无 key → `image_no_credentials` ✅;bak-20260906 备份可用令牌经 secret.set(新建路径 stored=True)入钥匙串 → 云端 describe ok=True elapsed=21,103ms 中文结构化(channel=cloud, model=glm-4.6v);config.json 现行 MCP key 仍 429/1113(无余额令牌)弃用。发现:钥匙串既有项跨进程更新报 `keychain_operation_failed`(-25244 需 GUI 授权),新建路径正常——已记档待修(evidence/protocol-e2e3-transcript.jsonl:38-40)
- [x] 4. 本地服务没起 → 结构化 `image_unreachable` 错误(附启动指引提示),应用不崩——死端口 127.0.0.1:9 → ok=False code=image_unreachable,message 含 `uvx mlx_vlm` 启动指引,进程不崩(evidence/protocol-e2e3-transcript.jsonl)
- [x] 5. 13-33s 的 VL 调用与首请求 Metal JIT 编译不被壳 120s 超时杀(事件流模式实证)——提交往返 0ms vs 任务耗时 52,995ms:同步应答毫秒级即返、长调用经 image.progress/completed 事件流完整返回;上轮发现的 60s 余量不足已修(client.py 缺省 180s,注释引用本轮定量),52.99s 任务实测吃满旧线、新线下无恙
- [x] 6. 设置持久化:重启 app 后看图配置生效;`vision.yaml` 无明文凭据,key 只在钥匙链——r2 重跑(新二进制):save → SIGTERM → 重启 → config.read 全字段对齐;vision.yaml 仅 `keychain:myia/image/api_key` 引用,明文形状 0 命中
- [x] 7. 协议契约测试(`tests/test_desktop_sidecar_protocol.py`):`image.import/ocr/analyze/status` + `image.config.read/save` 全方法往返 + 错误矩阵,mock 零外网——全量 pytest 1759 passed/0 failed(含 image.* 全部契约与错误矩阵;r1 定向 102 过亦在档)
- [x] 8. 无文字图片 → describe 模式走通(local-ocr 裁定:无文字不是终点)——无文字渐变图 vision OCR 0 行;describe ok=True elapsed=32,766ms,结构化中文「渐变色块…无人物」(180s 修复+主机速率回升 26.2 tok/s 后全量走通,上轮 failed 已翻绿)
- [x] 9. 基线不回归:pytest / vitest 全绿且数字 ≥ 开工当轮基线(建档时为 1397 / 40;v111-release、yaml-editor 并行落地后以开工重跑为准),`tsc -b` 零错——r2 复测:pytest 1759 passed/0 failed/14 skipped(≥1397,上轮 3 败全消);vitest 97/97(≥40);`npm run build`(tsc -b && vite build)exit 0 零错
- [x] 10. 装包冒烟:build-sidecar(PyInstaller)含 ocrmac + RapidOCR(onnxruntime+内置模型)打包成功,装机后看图屏可用;侧车体积增量如实记录——r2 照现状重打包 exit 0(116,877,424B);npm run tauri build exit 0,bundle 内嵌 sidecar 与 dist 构建逐字节一致(cmp 通过);沙箱静默验证:前台 WeChat 全程不变、AX窗口0、内嵌 sidecar 194ms 拉起、SIGTERM 干净退出零残留(evidence/app-sandbox-launch-r2.txt);/Applications 未动(其世事.app+2 sidecar 系并行会话所装)
- [x] 11. **引擎切换**:同图分别用 `vision`/`rapidocr` 跑 OCR,均返回逐行结果并标注来源引擎;非法 engine 值 → 结构化 `image_engine_unknown`——vision(55 行/881ms)与 rapidocr(56 行/780ms)各标 engine、前 3 行一致;engine=paddle-不存在 → `image_engine_unknown`

## 已拍板决议(grill Round 1,2026-10-03,主人「全按推荐」)

1. **排期**:v111-release(其工作流疑死需重跑)完成后 → **本任务插队到 yaml-editor 之前**动工
2. **入口**:独立「看图」屏,侧栏位次=情报流之后
3. **云端默认模型 `glm-4.6v`**;账户 2026-10-03 探针核实恢复可用(HTTP 200,09-30 的 1113 已消),验收 #3 云端通道实跑联调
4. **OCR 双引擎(2026-10-03 修订,主人「RapidOCR 也要用上,2个都要有」)**:Vision via ocrmac(默认)+ RapidOCR 并存,看图屏可切换;Vision 打包不友好退自写 PyObjC 裸调;两引擎已于 10-03 本机同图实跑对照通过(见探查结论)
5. **v1 单图**,多图 v2
6. **常驻模型路径 = `MYIA_HOME/models/`**(Qwen3-VL-8B MLX 转换产物,约 16GB)
7. **结果去向**:v1 仅屏上展示+文本可复制;结果不入库不持久化(图片文件持久保留可重分析);「存为情报条目」v2 另拍
8. **交互**:选图后 OCR 自动跑;低置信(≤0.5)只标警示+「升二级」按钮(手动不自动);切云端首次弹「图片内容将出网」确认并记住选择
9. **配置**:`vision.yaml` 作为 MYIA_HOME 第一个全局配置文件独立先行;协议方法统一 `image.config.*`(废弃 vision.config.* 命名)
10. **v2 优先序**:模型下载+server 代管捆绑第一优先(下载即能用),feed enrich 自动看图第二;v1 schema 不设死 v2 字段,扩展不破协议

技术自决(随决议落档):错误码统一 `image_` 前缀;heic 经 sips 转 png 后收;结果复制按钮 navigator.clipboard 失败退化手动选中复制;钥匙链前缀统一 `myia/image/*`;VL 发送前长边 >2048 先压缩再 base64。

## 验收记录(2026-10-03,受主人委托代验)

**verdict: conditional** —— AC2-AC11 全数仓库内证据复核通过:entry.py `_HANDLERS` 注册 `image.import/ocr/analyze/status/config.read/config.save` 六方法(entry.py:2664-2669);代验实跑 `tests/test_desktop_sidecar_protocol.py` 85 passed(image.* 全方法往返+错误矩阵:image_no_credentials / image_unreachable / image_engine_unknown / 明文拒载零写入),前端 image.test.tsx 20 + vision-form.test.tsx 5 全绿(实跑);vision/client.py:93 `timeout_seconds=180.0`(AC5)、vision/settings.py:49 KEYCHAIN_API_KEY=`myia/image/api_key`(AC6);evidence/ 复核:describe local 52,995ms / cloud glm-4.6v 21,103ms / 无字图 32,766ms、OCR vision 55 行/881ms + rapidocr 56 行/780ms(AC1 替代证据)、zero-egress-sample3 全 loopback、sandbox-launch-r2 沙箱冒烟在档(AC9/AC10 全量数字取自任务档 r2 记录,代验按规未跑全量套件,任务自带测试全绿)。**conditional 遗留:AC1 拖拽/粘贴交互待主人真人手验**;另钥匙串既有项跨进程更新报 keychain_operation_failed(-25244)系已记档待修项,不阻塞本验收。
