# 模型常驻化记录:Qwen3-VL-8B MLX(implement.md 第 0 步)

执行时间:2026-10-03 08:29–08:40(Mac M4 Max,macOS 25.4.0 arm64)
配方来源:`~/.zcode/skills/local-ocr/SKILL.md` 第二级「从零启动」三步配方。

## 结果速览

| 项 | 值 |
|---|---|
| 模型路径 | `~/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx` |
| 服务地址 | `http://127.0.0.1:8080/v1/chat/completions`(OpenAI 兼容,无需鉴权) |
| model 字段 | 上述模型路径全称 |
| 冒烟 | HTTP 200 + 中文详细描述,✅ 通过 |
| 首请求耗时 | 56.95s(含 Metal JIT 编译) |
| 第二发(温态) | 57.40s,输出与首发逐字一致 |
| 服务进程 | uvicorn(python3.1)PID 88296,nohup 常驻,日志 `/tmp/qwen3vl_server.log` |

## 全过程(含验证)

### ① 键名重映射(~60s)

源:`~/Library/Application Support/另一私有应用/comfyui/models/text_encoders/qwen3vl_8b_bf16.safetensors`(17,534,334,616 字节,ComfyUI 导出,键缺 `language_model` 中缀)。
照 SKILL.md 脚本仅改 safetensors 头(权重字节原样拷贝),`model.layers.*`→`model.language_model.layers.*`、`embed_tokens`/`norm` 同法。自检输出:

```
KEYS: 750
HAS_LM_LAYERS: True  HAS_LM_EMBED: True  HAS_LM_NORM: True
DST_BYTES: 17534340560  SRC_BYTES: 17534334616   # 差 5,944B = 键名加长的头差
```

### ② 拉官方 config/tokenizer(~11.5MB)

`Qwen/Qwen3-VL-8B-Instruct` 9 个文件(config/generation_config/merges/preprocessor/tokenizer/tokenizer_config/vocab/chat_template/video_preprocessor)全部 OK;`config.json` 验明 `architectures: ["Qwen3VLForConditionalGeneration"]`、`model_type: qwen3_vl`、含 `vision_config`。

### ③ 转 MLX bf16(~90s,后台 nohup)

```bash
uvx --from mlx-vlm mlx_vlm.convert \
  --hf-path /tmp/qwen3vl8b_hf \
  --mlx-path "$HOME/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx"
```

产物:4 分片 + index + MLX config/tokenizer 等 16 文件。**完整性硬校验通过**:`index.json` 声明 `total_size=17,534,247,392` + 各分片 safetensors 头合计 92,029B = 磁盘实际 17,534,339,421B,分毫不差(EXACT_MATCH: True)。

### ④ 起服务

```bash
nohup uvx --from mlx-vlm mlx_vlm.server \
  --model "$HOME/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx" \
  --host 127.0.0.1 --port 8080 > /tmp/qwen3vl_server.log 2>&1 &
```

8080 起前 `lsof` 复查空闲;~5s 后监听,日志:`Model and processor loaded successfully` / `continuous batching enabled`。

### ⑤ 真图冒烟

图:`~/.zcode/cli/image-cache/sess_a8f2e5f4-…/image-037ef0822274c667761f76757d78663e.png`(最新 png,3840×2160 RGBA,875KB;魔法字节验明确为 PNG)。base64 走 `data:image/png;base64,…` + 文本「图里有什么」:

- 首发:`http_code=200 time_total=56.950926s`,content 1604 字中文详细描述(图为 Hermes 助手的 Telegram 消息平台设置界面,内容具体连贯),usage: prompt 8173 / completion 819。
- 第二发:`http_code=200 time_total=57.401471s`,输出与首发逐字一致(同种子确定性)。

耗时说明:此图 4K、视觉 token 8173,远大于技能档案(09-30)小图基线(读字 ~13s / 描述 ~33s),57s 属该图体量的正常值,非服务异常;`cached_tokens: 0` 表示两发间无前缀缓存收益,亦属预期。

## 启动指引(重启用)

```bash
# 端口占用先查:lsof -nP -iTCP:8080 -sTCP:LISTEN
# 若被旧实例占用:pkill -f mlx_vlm.server
nohup uvx --from mlx-vlm mlx_vlm.server \
  --model "$HOME/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx" \
  --host 127.0.0.1 --port 8080 > /tmp/qwen3vl_server.log 2>&1 &
# 就绪判据:日志出现 "Uvicorn running on http://127.0.0.1:8080" 且 lsof 见 LISTEN
# 调用:POST http://127.0.0.1:8080/v1/chat/completions
#   model = "~/Library/Application Support/MYIA/models/qwen3-vl-8b-mlx"
#   图片放 messages[0].content[].image_url.url = "data:image/png;base64,…"
# 停服:pkill -f mlx_vlm.server
```

## 给冒烟员的自检要点

1. **服务活着**:`lsof -nP -iTCP:8080 -sTCP:LISTEN` 有输出;`curl -s http://127.0.0.1:8080/v1/models` 能回。
2. **请求格式**:OpenAI 兼容,`model` 字段必须填模型**路径全称**(不是模型名);无 Authorization 头。
3. **图走 base64**:本地路径/localhost URL 服务端不可达,必须 `data:<mime>;base64,…`;mime 按真实魔法字节(png/jpeg),勿信扩展名。
4. **耗时预期**:首发含 Metal JIT 编译偏慢;4K 大图描述类 ~57s/发属正常,超时给 ≥120s;小图读字目标 ~13s 量级。
5. **通过判据**:HTTP 200 + content 非空中文;若挂了先看 `/tmp/qwen3vl_server.log` 再查端口。
6. **已知坑**(SKILL.md 09-30 实测):金额行会丢尾零($20.0000→$20.000),关键数字需逐位复核。

## 中间产物处置

`/tmp/qwen3vl8b_hf`(16GB HF 中间件)与重映射脚本已删;保留 `/tmp/qwen3vl_{convert,remap,server}.log` 与冒烟 req/resp JSON 备查。产物在数据根(`~/Library/Application Support/MYIA/models/`),不进 git。
