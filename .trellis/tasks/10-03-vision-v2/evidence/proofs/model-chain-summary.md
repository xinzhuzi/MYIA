# 实证2:小模型真下载全链 —— PASSED(10-03-vision-v2 proofs)

日期:2026-10-03 22:33–23:13 |驱动:`model-download-chain.py`(spawn `.venv entry.py serve`,
`MYIA_HOME=/Users/zhengbingjin/Library/Application Support/MYIA` 真实桌面根)|主日志:
`model-download-chain.log`(attempt5 终版;历史 attempt1–4 各留档,见下)

## 模型选择(ask「优先 Qwen2-VL-2B ~1.7GB 或更小」)

- 首选 `mlx-community/Qwen2-VL-2B-Instruct-4bit`:HF API(files_metadata)实测总量
  **1,261,855,962 B(~1.26GB)**,存在、safetensors 齐 —— 但本机链路实测
  **直连/Clash 代理(127.0.0.1:7897)/hf-mirror 三路单连接全部 ~325–400kB/s 同帽,
  4 路并行分片不聚合(合计 ~360kB/s,链路瓶颈非单连)**,1.26GB ≈ 60 分钟,
  超 ask 20 分钟帽 3 倍(attempt1 直连/attempt2 代理两跑实证,残件当时 80MB+
  incomplete,已在上轮 cleanup 经产品 `image.models.delete` 清掉)。
- 按 ask「**或更小**」换 `mlx-community/SmolVLM-256M-Instruct-4bit`:HF API 实测
  **150,417,043 B(~150MB)**,单 `model.safetensors`;config `model_type=idefics3`,
  本机 uv 缓存内 `mlx_vlm 0.7.4` 自带 `idefics3` 模块(加载实证见 vision-server.log)。

## 全链(attempt5,总耗时 415.2s,零失败)

| 步骤 | 命令/事件 | 结果 |
|---|---|---|
| 基线 | `image.server.status` | running+healthy,model=qwen3-vl-8b-mlx |
| 下载 | `image.models.download {repo: mlx-community/SmolVLM-256M-Instruct-4bit, name: test-vl-256m}` → job_id=1 | 384s 完成,`image.models.progress` 事件 700+ 条(日志采样 30 条,首条 done=4739 total=150417043),`image.models.completed {ok:true}` |
| 清单 | `image.models.list` | `test-vl-256m bytes=150415524 incomplete=false` |
| 激活 | `image.models.activate {name: test-vl-256m}` | `{ok:true}`(vision.yaml local.model 改写) |
| 停旧 | SIGTERM 8080(PID 记录在日志) | 端口释放 |
| 起新 | `image.server.ensure` → job_id=1 | `image.server.completed {ok:true, started:true}` 新模型 healthy |
| 真图 | `POST /v1/chat/completions`(real-test.png,桌面真截屏 sips 转 1600px PNG,base64 data-url) | **HTTP 200,caption:「Screen shows duplicate VHS install detected.」**(与 8B 同图描述同主旨:VHS 重复安装报错弹窗) |
| 收尾 | activate 回 `qwen3-vl-8b-mlx` → 停(PID 记录)→ ensure(job_id=2) | `image.server.completed {ok:true, started:true}`;status healthy;/health `loaded_model=…/qwen3-vl-8b-mlx`;**RESTORED=True** |
| 现场 | `image.models.delete` 两个测试目录 | ok;末态 models=[('qwen3-vl-8b-mlx', True)] |

## 末态(独立 curl 复核,23:13)

`GET /health` → `{"status":"healthy","loaded_model":"…/models/qwen3-vl-8b-mlx"}`;
8080 监听在(PID 40436,由产品 ensure 的 `uvx --from mlx-vlm mlx_vlm.server` 拉起);
`<home>/models/` 仅剩 `qwen3-vl-8b-mlx`;vision.yaml `local.model` 已回 8B;
无残留 serve/驱动进程。

## 过程发现(如实)

1. **422 坑**:mlx_vlm 0.7.4 `VLMRequest.model` 必填(server/schemas.py:691),payload
   缺 model 即 422(attempt3 实证);产品 client 本就传
   `model=self._model`(src/myia/vision/client.py:144)—— 驱动对齐后 200。
2. 进度计数观测:`done_bytes` 终态 221,843,257 > `total_bytes` 150,417,043
   (多文件 bar 的 close 重入账使累计超总量;不影响 completed 判定,供产品侧留意)。
3. 断点续传语义经 attempt1/2 残件间接实证:`image.models.list` 对半成品目录报
   `incomplete=true`(attempt3 基线行),`.cache/huggingface/download/*.incomplete`
   形态与 models.py 文档一致。

## 留档文件

- `model-download-chain.log` —— attempt5 全链主证据(事件+caption+收尾)
- `model-download-chain.attempt1-direct-stall.log` —— Qwen2-VL-2B 直连(63.7MB/400kB/s 量级)
- `model-download-chain.attempt2-proxy.log` —— 同模型走 Clash 代理(同帽,无改善)
- `model-download-chain.attempt3-422-caption.log` —— SmolVLM 首过(链路全通,caption 422)
- `model-download-chain.attempt4-pass-no-progress-log.log` —— 修 422 后全过(progress 事件未记录,补 attempt5)
- `real-test.png` —— 真图(桌面截屏 2026-10-03 13.13.59,sips -Z 1600 转 PNG)
