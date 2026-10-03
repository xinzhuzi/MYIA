# Implement:看图(10-03-image-input)

执行序(每步含验证与回滚点);门禁纪律:验证命令重定向到文件 + 显式 `$?`,禁 `| tail` 判活(管道吞退出码教训)。

## 0. 前置:基线核对 + 本机视觉底座常驻化(不动仓库)

- [ ] **开工基线核对**:rebase 最新 main(v111-release / yaml-editor 并行改动会动 entry.py、types.ts、settings 屏),按 CI 同款命令重跑并记录当轮基线数字(本档建档时 pytest 1397 / vitest 40)

- [ ] 按 local-ocr 技能三步配方,把 ComfyUI 的 `qwen3vl_8b_bf16.safetensors` 重映射+转 MLX,落 `MYIA_HOME/models/qwen3-vl-8b-mlx`(路径已拍板:MYIA_HOME/models/,约 16GB)
- [ ] `mlx_vlm.server` 起在 127.0.0.1:8080,用一张真截图 curl 冒烟(HTTP 200 + 中文转写),记录耗时基线(预期读字 ~13s / 描述 ~33s,首请求含 JIT 编译)
- 验证:curl 返回 200 且 content 非空;回滚:无需(产物在数据根,不进 git)

## 1. Python 侧:`src/myia/vision/` 三模块 + extras

- [ ] `settings.py`(VisionConfig + 同门校验)→ `client.py`(VisionClient,image part 组装)→ `ocr.py`(**双引擎**:vision=ocrmac+sips 放大;rapidocr=rapidocr-onnxruntime;统一 `{text, conf}` 接口与 engine 路由)
- [ ] pyproject:`myia[vision]` extras(ocrmac + rapidocr-onnxruntime,体积影响见 design 依赖节);核心依赖不动
- [ ] 单测(mock 全量,零外网)
- 验证(CI 同款):`uv run --no-sync python -m pytest -q --tb=short` 全绿(含新增 vision 单测;此即 718d56c 的 prepend 模式命令);回滚点:独立 commit,可单独 revert

## 2. sidecar 协议:6 方法 + 事件流

- [ ] entry.py handler + `_HANDLERS` 注册;`image.ocr` 带引擎参数(缺省取配置);`image.analyze` 后台线程 + `image.progress/completed` 事件(仿 run.start;`image_busy` 单飞守卫)
- [ ] `tests/test_desktop_sidecar_protocol.py` 增往返 + 错误矩阵(含 `image_engine_unknown` 非法引擎分支)
- 验证(CI 同款):`uv run --no-sync python -m pytest -q --tb=short`;回滚点:协议与 UI 分两个 commit

## 3. 壳:dialog 插件

- [ ] `tauri-plugin-dialog` + capabilities 增权限;CSP 不动
- 验证:`cargo check` 绿 + dev 模式选图可用;回滚:revert 壳 commit

## 4. UI:看图屏 + settings 分区

- [ ] `screens/image/`(DropZone/Thumb/OcrPanel/VisionPanel + api.ts);`types.ts` 增 6 方法;App.tsx 路由 `/image` + 侧栏「看图」(位次=情报流后)
- [ ] 交互落地(grill 已拍板):选图自动 OCR;低置信只标警示、升二级手动;切云端首次弹出网确认+记住;结果文本可复制;**引擎切换即切即重跑(10-03 修订)**
- [ ] settings 屏 VisionForm(api_key 走 secret.set → `myia/image/api_key`)
- [ ] vitest 用例 + `tsc -b` 零错
- 验证(CI 同款):desktop/ui-src 下 `npm test`(=vitest run)+ `npm run build`(=tsc -b && vite build)全绿;回滚点:独立 commit

## 5. 联调冒烟(dev → 装包)

- [ ] dev 模式全链路:拖图 → **双引擎 OCR 对照(vision/rapidocr 各跑一遍)** → 本地通道解读(断网重跑一遍证零出网)→ 切云端无 key 报 `image_no_credentials`
- [ ] build-sidecar(`--extra vision`)重打包(含 onnxruntime 动态库与 rapidocr 内置模型,记录侧车体积增量),装机冒烟看图屏可用;打包验证看构建产物非二进制 grep
- 验证:冒烟截图存任务 evidence/;回滚:整任务 revert

## 6. 回归与收尾

- [ ] 全量回归三件套(CI 同款:pytest / `npm test` / `npm run build`),数字与开工基线核对(第 0 步记录值)
- [ ] spec 更新:python/index.md 登记 `src/myia/vision/` 与 vision extras;error-handling 增 image_* 错误码段(若约定要求)
- [ ] 验收标准 1-11 逐条勾(grill Round 1 + 10-03 双引擎修订均已拍定,见 prd「已拍板决议」节)

## 显式不做(与 prd 一致)

模型下载/server 代管/feed enrich 自动看图/tesseract/多图/看图历史——全排 v2,勿顺手实现。
