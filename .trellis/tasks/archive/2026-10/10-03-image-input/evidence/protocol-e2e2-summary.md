# 装机冒烟总汇(10-03-image-input 验收 1-11,2026-10-03 10:36–11:15)

侧车:`desktop/dist/myia-core` 照现状重打包(build-sidecar.sh 已含 `--extra vision` +
`--collect-all ocrmac/rapidocr_onnxruntime/openai`,**无需任何补丁**);
app:`npm run tauri build` exit 0,bundle 内嵌 sidecar 与 dist 构建逐字节一致(cmp 通过)。

## 一、侧车体积增量(AC10,实测)

| 构建 | 字节数 |
|---|---|
| 基线(git HEAD 旧 spec `myia`,无 vision) | 16,867,072 B(16.1 MiB) |
| 现状(`myia-core`,含双 OCR 引擎+onnxruntime+内置模型+openai) | 116,866,096 B(111.4 MiB) |
| **增量** | **+99,999,024 B ≈ +95.4 MiB(+100 MB)** |

预估「+50MB 级」偏保守:实际约 +95 MiB(其中 onnxruntime + rapidocr 内置 onnx 模型占大头)。
基线构建方法:`git show HEAD:desktop/myia.spec` 旧 spec 用同一 .venv-build 跑 PyInstaller,产物已删。

## 二、协议级 E2E(直接拉起 dist/myia-core serve,stdio 行 JSON)

三轮:R1 冷态(15/20 过)、R2 温态、R3 终轮(ask/read 补验)。逐项转录:
`protocol-e2e2-transcript.jsonl`(secret.set 的 value 一律 <redacted>,bigmodel 令牌未落任何文件)。

### 通过项
- health / image.import(真截图 app-01-dashboard.png 276KB → id=79877bc5bd72388a;重复导入同 id=sha256 去重)
- **AC11 双引擎**:vision 55 行/1021ms + rapidocr 56 行/786ms,各自标注 engine,同图前 3 行一致;
  非法引擎 `paddle-不存在` → `image_engine_unknown`(AC1 补充:两引擎真截图 OCR 均 <2s)
- **AC4 死端口**:local.base_url→127.0.0.1:9 → `image_unreachable`,message 含启动指引(uvx mlx_vlm 配方+LM Studio 备选)
- **AC2 本地通道中文解读(终轮 ask 模式)**:ok=True elapsed=19796ms,
  答复「这是名为"MYIA"的软件的"仪表盘"界面,用于展示各类数据源的运行状态、健康度和近期采集成功率。」;
  model=模型路径全称;零出网采样三轮 173/96/32 条全 loopback、非 loopback 0 条(方法与代理披露见 zero-egress-sample2.txt)
- **AC5 事件流**:提交往返 1ms vs 任务 19796ms(上一轮冒烟同代码 describe 36.6s 亦全程事件流返回);
  同步应答毫秒级即返,长调用在 image.progress/completed 事件流完成
- **AC6 持久化**:image.config.save → SIGTERM → 重启 sidecar → image.config.read 全字段对齐;
  vision.yaml 311B,凭据仅 `keychain:myia/image/api_key` 引用,明文形状扫描 0 命中;图库跨轮持久(同图同 id)
- image.status 同进程对账:busy=False + last.job_id 对齐
- **AC8 不死头补充**:无文字图(程序生成渐变)vision OCR 0 行;read 模式 ok=True 9.9s
  「图片中没有文字,因此无需校对。」

### 失败项(均如实)
- **AC3 云端 glm-4.6v**:两把 key 均败——当前 config.json MCP key 文本探针 429/1113;
  bak-20260906 的 Z_AI_API_KEY 文本探针 HTTP 200 但**同 key 带图调用仍 429/1113 余额不足**。
  判定:账户侧无视觉调用资源(今晨「探针恢复」系文本探针),代码路径无缺陷(错误经
  image.completed 结构化返回 image_provider_error;无 key 时同步 `image_no_credentials` 亦验证通过)。
  **待主人充值/换 key 后复跑即收**。keychain 现存 config.json 当前 key。
- **AC8 describe 本体**:三轮均 `TimeoutError`(sidecar VisionClient 默认 60s 超时,client.py:88)。
  根因定量:今日主机推理速率崩塌——早间常驻报告 decode 26.7 tok/s,11:05 后干净单飞实测仅
  2.9–4.6 tok/s(内存空闲 46% 无压力;Chrome 102% CPU 等多会话 GPU 争用,非本任务可修);
  describe 结构化输出 500–800 token → 150–250s,必超 60s。机制本身已被 ask(19.8s)/read(9.9s)/
  直连 curl describe(84 token,33.3s)与上一轮冒烟 describe ok=True(10:07,36.6s)证实。
- describe 于带字真图(R1/R2)同样 TimeoutError,同根因。

### 冒烟发现(转 implement 会话,建议修)
1. **VisionClient 60s 超时余量不足**:首视觉请求 Metal JIT(~55s)+ describe 长输出 + 低速主机
   任意一项即越线;且客户端超时不取消服务端生成,产生孤儿请求在 continuous batching 里拖垮后续
   请求(R2 观测 2–5 tok/s 雪崩)。建议:超时提到 ≥180s 或可配 + 取消传导(断连即弃)。
2. **本地通道被系统代理中转**:httpx trust_env 读 macOS 系统代理(Clash 7897),剥 env 不够;
   本地链路建议 trust_env=False(详见 zero-egress-sample2.txt)。

## 三、app 级(静默纪律,无任何前台抢占)

- 并行任务 10-03-quiet-launch 已改静默启动:主窗口 visible:false,`MYIA_SHOW_ON_START=1`
  显窗路径含 set_focus(main.rs:231)必抢焦点 → **本轮不做显窗截图**;看图屏空态 app 级=manual。
- 侧栏「看图」入口证据:上一轮 app-01-dashboard.png(10:21 实窗截屏,OCR 命中全部 7 屏名)。
- 沙箱验证(app-sandbox-launch.txt):新 bundle 直跑二进制(MYIA_HOME=/tmp/myia-app-smoke-home
  独立实例域,main.rs:165-178 明文允许)——前台进程全程 ZCode 不变、AX 窗口 0、屏上 MYIA 窗口 0、
  内嵌 sidecar 207ms 拉起(沙箱 home)、SIGTERM 干净退出、沙箱零残留、数据根正常播种。
- 本轮**未覆盖安装到 /Applications**(纪律:不覆盖装机、不动存量进程;/Applications/MYIA.app
  系并行 quiet-launch 会话所装,内嵌同为 vision 侧车)。

## 四、AC7/AC9 回归(CI 同款命令)

- pytest(`uv run --no-sync python -m pytest -q --tb=short`):**1704 passed / 3 failed / 14 skipped**。
  3 败均非本任务:2× tests/test_docs.py(docs/zh/zero-cost.md 为并行 zero-cost 会话 10:50 未跟踪新文件,
  zh/en 树不对齐);1× test_sources_write_backup_stops_comment_loss(并行 yaml-toggle-comments 会话
  未提交 entry.py diff「sources.write 文本手术」令主文件保注释,测试断言的旧「抹注释」行为翻转)。
  image.*/vision 契约与单测全绿(定向跑 102 过,唯一失败即上述 sources_write 条)。
- vitest(`npm test`):**89/89 全绿**(建档基线 40)。`npm run build`(tsc -b && vite build)exit 0 零错。

## 五、环境事件与处置(如实)

- 10:41–10:47 并行会话清了 MYIA 数据根:16GB 常驻模型+vision.yaml+图库全失、模型服务被停。
  按 model-setup.md 配方一轮修复:键重映射(自检与存档逐字一致)→ 拉 HF config/tokenizer 9 文件 →
  mlx_vlm.convert 重建(16G/4 分片)→ 服务重启(现 PID 45641 常驻 LISTEN)。
- /tmp 16GB HF 中间件与基线构建产物已清;磁盘 137Gi 可用。

---

# 复测轮(r2,2026-10-03 11:41–11:55,修复已进)

修复落点:`src/myia/vision/client.py` 单请求超时 60s→**180s**(注释引用本轮定量)、
`src/myia/vision/ocr.py` ocrmac 1.x 兼容(OCR 类下钻子模块 + 去 use_language_correction);
trust_env 未动(代理披露维持)。云端账户侧恢复:bak-20260906 的 Z_AI key 带图直连 200,
config.json 现行 MCP key 仍 429/1113(另一把无余额令牌)。

侧车照现状重打包:116,877,472B(11:41)。复测转录:`protocol-e2e3-transcript.jsonl`。

## 复测结果(对上轮 failed 项与受影响项)

| 项 | 上轮 | 复测 | 证据 |
|---|---|---|---|
| AC8 无文字图 describe | failed(60s超时) | **passed** | ok=True elapsed=32766ms,结构化中文「渐变色块…无人物」,180s 窗口吃下 |
| AC3 云端 glm-4.6v | failed(1113) | **passed** | 无key→image_no_credentials ✅;bak key 经 secret.set 入钥匙串→ok=True elapsed=21103ms 中文结构化解读(channel=cloud) |
| AC9 回归 | failed(3败) | **passed** | pytest **1759 passed/0 failed**(≥1397;上轮 3 败全消——zero-cost 文档与 sources_write 测试已随并行任务收口);vitest **97/97**(≥40);tsc -b + vite build 0 错 |
| AC2 本地中文解读+零出网 | passed(带保留) | passed(全量) | describe ok=True 52,995ms(旧 60s 线必死,180s 修复直接受益);零出网 162 条采样 0 非 loopback |
| AC5 事件流 | passed | passed | 提交往返 0ms vs 任务 52,995ms |
| AC4/6/11/status | passed | passed | 死端口指引/重启持久化/双引擎+非法引擎,数字见 transcript |

## 归因更正(上轮 summary 之误)

上轮「bak key 带图 429」结论**有误**:11:03 那发 sidecar 云端调用前 secret.set(写 bak key)
实际已失败(钥匙串既有项跨进程更新被 ACL 拦,-25244),keychain 仍是 cur key,429 系 cur key 所致;
未核对该 secret.set 应答是我上轮的记录疏漏。本轮已核实:secret.set 对**新建**路径成功(stored=True,
r1 亦然),对**既有项跨进程更新**报结构化 `keychain_operation_failed`(-25244,需 GUI 授权,
后台上下文给不了)——建议 implement 会话补「更新失败引导重录」的 UX 提示,或改用可无提示更新的
keychain ACL 策略。复测时已删除 r1 自建项走新建路径完成 AC3。

## r2 遗留

- config.json 现行 MCP key 账户仍 1113(文本/带图皆拒);钥匙串 myia/image/api_key 现存
  **可用的 bak key**(Z_AI)。若主人想统一令牌来源,充值 MCP key 后重录即可。
