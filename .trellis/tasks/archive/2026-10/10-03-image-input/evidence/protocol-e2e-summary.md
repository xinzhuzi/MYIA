# 协议级 E2E 摘要(打包 sidecar 真进程)

```
[01] health -> OK
[02] image.import -> OK
    image_id=3bd7308fce4331a8 bytes=875230 ext=png
[03-vision] image.ocr -> OK
    vision: 75 行 / 2231ms;前3行: [('会话', 1.0), ('新建会话', 1.0), ('技能与工具', 1.0)]
[03-rapidocr] image.ocr -> OK
    rapidocr: 85 行 / 4184ms;前3行: [('0', 0.5101), ('会话机器人', 0.9993), ('全部', 0.9995)]
[04] image.ocr -> ERROR {"code": "image_engine_unknown", "path": "params.engine", "message": "未知 OCR 引擎 'paddle-不存在'(可选:vision/rapidocr)", "data": {"engine": "paddle-不存在", "allowed": ["vision", "rapidocr"]}}
    非法引擎错误码=image_engine_unknown (期望 image_engine_unknown)
[05] image.config.read -> OK
[06] image.config.save -> OK
[07] image.analyze -> ERROR {"code": "image_no_credentials", "path": "cloud.api_key", "message": "云端通道缺 api_key:先在看图设置录入(经 secret.set 入钥匙链 myia/image/api_key)"}
    无 key 云端错误码=image_no_credentials (期望 image_no_credentials)
[08] image.analyze -> OK
    本地 describe job=1 ok=True elapsed_ms=36574
    本地解读前120字: 这张图片并非真实拍摄的场景，而是一个软件界面的截图，因此无法按照“人/物+姿态神态”等传统视觉描述结构进行解读。以下是根据您提供的结构化要求，对这张软件界面截图的详细中文解读：

1. 主体（人/物+姿态神态）
   - 无真实人物或物体。
    本地通道连接采样(零出网证据,去重): ['<无 TCP 连接>', 'pid=70774 127.0.0.1:53322->127.0.0.1:7897 (ESTABLISHED)', 'pid=70774 127.0.0.1:53344->127.0.0.1:7897 (ESTABLISHED)']
[09] image.config.save -> OK
[10] image.analyze -> OK
    死端口错误码=image_unreachable (期望 image_unreachable);提示含启动指引=True
[11] secret.set -> OK
[12] image.config.save -> OK
[13] image.analyze -> OK
    云端 glm-4.6v ok=False elapsed_ms=None
    失败详情: {"code": "image_provider_error", "message": "RateLimitError: Error code: 429 - {'error': {'code': '1113', 'message': '余额不足或无可用资源包,请充值。'}}"}
[13b] image.config.save -> OK
[13c] image.analyze -> OK
    云端 glm-4v-flash ok=False elapsed_ms=None
    失败详情: {"code": "image_provider_error", "message": "BadRequestError: Error code: 400 - {'error': {'code': '1210', 'message': 'max_tokens参数非法：限制数值范围[1,1024]'}}"}
[14] image.status -> OK
    status 对账 last.job_id=4 ok=False
    vision.yaml 内容(全文,315B,凭据仅引用):
      | channel_default: cloud
      | local:
      |   base_url: http://127.0.0.1:8080/v1
      |   model: /Users/zhengbingjin/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx
      | cloud:
      |   base_url: https://open.bigmodel.cn/api/paas/v4
      |   model: glm-4v-flash
      |   api_key: keychain:myia/image/api_key
      | ocr:
      |   enabled: true
      |   engine_default: vision
    明文凭据扫描(排除 keychain: 引用后仍有长令牌形状): 无
```
