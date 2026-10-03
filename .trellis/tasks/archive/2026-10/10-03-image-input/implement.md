# Implement:看图(10-03-image-input)

执行序(每步含验证与回滚点);门禁纪律:验证命令重定向到文件 + 显式 `$?`,禁 `| tail` 判活(管道吞退出码教训)。

## 0. 前置:基线核对 + 本机视觉底座常驻化(不动仓库)

- [x] **开工基线核对**:rebase 最新 main(v111-release / yaml-editor 并行改动会动 entry.py、types.ts、settings 屏),按 CI 同款命令重跑并记录当轮基线数字(本档建档时 pytest 1397 / vitest 40)——r2 终值见 prd AC9:pytest 1759 passed/0 failed/14 skipped + vitest 97/97

- [x] 按 local-ocr 技能三步配方,把 ComfyUI 的 `qwen3vl_8b_bf16.safetensors` 重映射+转 MLX,落 `MYIA_HOME/models/qwen3-vl-8b-mlx`(路径已拍板:MYIA_HOME/models/,约 16GB)——证据 evidence/model-setup.md
- [x] `mlx_vlm.server` 起在 127.0.0.1:8080,用一张真截图 curl 冒烟(HTTP 200 + 中文转写),记录耗时基线(预期读字 ~13s / 描述 ~33s,首请求含 JIT 编译)——装机 r2 实测:OCR vision 881ms/rapidocr 780ms、describe 52,995ms(evidence/protocol-e2e3-transcript.jsonl)
- 验证:curl 返回 200 且 content 非空;回滚:无需(产物在数据根,不进 git)

## 1. Python 侧:`src/myia/vision/` 三模块 + extras

- [x] `settings.py`(VisionConfig + 同门校验)→ `client.py`(VisionClient,image part 组装)→ `ocr.py`(**双引擎**:vision=ocrmac+sips 放大;rapidocr=rapidocr-onnxruntime;统一 `{text, conf}` 接口与 engine 路由)——r2 装机冒烟三修入档:client.py 单请求超时缺省 180s + 本地通道 Authorization 空串覆写(httpx 无 None=删头语义)、ocr.py ocrmac 1.x 兼容(OCR 类下钻子模块 + 去 use_language_correction)
- [x] pyproject:`myia[vision]` extras(ocrmac + rapidocr-onnxruntime,体积影响见 design 依赖节);核心依赖不动
- [x] 单测(mock 全量,零外网)——test_vision.py 随 r2 签名修订(kwargs 钉死同步 ocrmac 1.x)
- 验证(CI 同款):`uv run --no-sync python -m pytest -q --tb=short` 全绿(含新增 vision 单测;此即 718d56c 的 prepend 模式命令);回滚点:独立 commit,可单独 revert

## 2. sidecar 协议:6 方法 + 事件流

- [x] entry.py handler + `_HANDLERS` 注册;`image.ocr` 带引擎参数(缺省取配置);`image.analyze` 后台线程 + `image.progress/completed` 事件(仿 run.start;`image_busy` 单飞守卫)
- [x] `tests/test_desktop_sidecar_protocol.py` 增往返 + 错误矩阵(含 `image_engine_unknown` 非法引擎分支)——AC7:全量 pytest 1759 passed/0 failed 含 image.* 全方法契约
- 验证(CI 同款):`uv run --no-sync python -m pytest -q --tb=short`;回滚点:协议与 UI 分两个 commit

## 3. 壳:dialog 插件

- [x] `tauri-plugin-dialog` + capabilities 增权限;CSP 不动——装机包选图路径实跑可用(AC10 bundle 冒烟)
- 验证:`cargo check` 绿 + dev 模式选图可用;回滚:revert 壳 commit

## 4. UI:看图屏 + settings 分区

- [x] `screens/image/`(DropZone/Thumb/OcrPanel/VisionPanel + api.ts);`types.ts` 增 6 方法;App.tsx 路由 `/image` + 侧栏「看图」(位次=情报流后)
- [x] 交互落地(grill 已拍板):选图自动 OCR;低置信只标警示、升二级手动;切云端首次弹出网确认+记住;结果文本可复制;**引擎切换即切即重跑(10-03 修订)**——r2 补 completed 早丢经 image.status(job_id) 对账恢复 + config.read {file,exists,config} 包装解包用例
- [x] settings 屏 VisionForm(api_key 走 secret.set → `myia/image/api_key`)
- [x] vitest 用例 + `tsc -b` 零错——AC9:vitest 97/97;npm run build(tsc -b && vite build)exit 0
- 验证(CI 同款):desktop/ui-src 下 `npm test`(=vitest run)+ `npm run build`(=tsc -b && vite build)全绿;回滚点:独立 commit

## 5. 联调冒烟(dev → 装包)

- [x] dev 模式全链路:拖图 → **双引擎 OCR 对照(vision/rapidocr 各跑一遍)** → 本地通道解读(断网重跑一遍证零出网)→ 切云端无 key 报 `image_no_credentials`——装机 r2 补轮 AC2/3/4/8/11 全过(逐条证据见 prd 勾档);零出网采样 162 条对端全 loopback、非 loopback 0 条(evidence/zero-egress-sample3.txt,lsof 进程树法,未断网;系统代理中转披露维持:trust_env 本轮未改)
- [x] build-sidecar(`--extra vision`)重打包(含 onnxruntime 动态库与 rapidocr 内置模型,记录侧车体积增量),装机冒烟看图屏可用;打包验证看构建产物非二进制 grep——r2 照现状重打包 exit 0(116,877,424B);bundle 内嵌 sidecar 与 dist 构建逐字节一致(cmp 通过);沙箱静默验证 evidence/app-sandbox-launch-r2.txt
- 验证:冒烟截图存任务 evidence/;回滚:整任务 revert

## 6. 回归与收尾

- [x] 全量回归三件套(CI 同款:pytest / `npm test` / `npm run build`),数字与开工基线核对(第 0 步记录值)——r2 复测:pytest 1759 passed/0 failed/14 skipped(≥1397,上轮 3 败全消);vitest 97/97(≥40);npm run build exit 0 零错
- [x] spec 更新:python/index.md 登记 `src/myia/vision/` 与 vision extras;error-handling 增 image_* 错误码段(若约定要求)——python/index.md 本轮入档;error-handling.md 只讲原则与 CLI 退出码、无错误码登记约定(N/A),image_* 全码已载 desktop/sidecar-protocol.md:73-74(请求应答/事件流两档)
- [x] 验收标准 1-11 逐条勾(grill Round 1 + 10-03 双引擎修订均已拍定,见 prd「已拍板决议」节)——2026-10-03 装机冒烟 r2:10 passed + AC1 manual(拖拽交互无法自动化,待主人手验;替代证据见 prd AC1 行)

## 显式不做(与 prd 一致)

模型下载/server 代管/feed enrich 自动看图/tesseract/多图/看图历史——全排 v2,勿顺手实现。
