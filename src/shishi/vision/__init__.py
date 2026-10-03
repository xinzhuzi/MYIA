"""看图能力包:双引擎 OCR + OpenAI 兼容视觉客户端 + ``vision.yaml`` 配置。

分层(task 10-03-image-input design):协议方法与钥匙串前缀用 ``image.*`` /
``myia/image/*``(desktop/entry.py 注册);本包 ``shishi.vision`` 是 Python 能力
实现名,两层不冲突,勿再发明第三种前缀。

全部重依赖惰性 import(ocrmac / rapidocr-onnxruntime / openai,extras
``shishi[vision]``)—— 核心流水线零重依赖红线不破;未装 extras 时结构化报错
并附安装命令。测试注入假引擎模块与假 AsyncOpenAI,零外网。
"""

from __future__ import annotations

from shishi.vision.client import (
    INSTALL_COMMAND,
    MAX_LONG_EDGE,
    VisionClient,
    VisionResult,
)
from shishi.vision.ocr import (
    OCR_ENGINES,
    OCRError,
    OcrLine,
    VISION_MIN_WIDTH,
    VISION_UPSCALE_TARGET,
    image_dimensions,
    run_ocr,
    sips_resize,
)
from shishi.vision.settings import (
    CHANNELS,
    DEFAULT_CLOUD_BASE_URL,
    DEFAULT_CLOUD_MODEL,
    DEFAULT_LOCAL_BASE_URL,
    KEYCHAIN_API_KEY,
    KEYCHAIN_LLM_API_KEY,
    VISION_FILE_NAME,
    VisionConfig,
    VisionConfigError,
    load_vision_config,
    resolve_cloud_api_key,
    save_vision_config,
)

__all__ = [
    "CHANNELS",
    "DEFAULT_CLOUD_BASE_URL",
    "DEFAULT_CLOUD_MODEL",
    "DEFAULT_LOCAL_BASE_URL",
    "INSTALL_COMMAND",
    "KEYCHAIN_API_KEY",
    "KEYCHAIN_LLM_API_KEY",
    "MAX_LONG_EDGE",
    "OCR_ENGINES",
    "OCRError",
    "OcrLine",
    "VISION_FILE_NAME",
    "VISION_MIN_WIDTH",
    "VISION_UPSCALE_TARGET",
    "VisionClient",
    "VisionConfig",
    "VisionConfigError",
    "VisionResult",
    "image_dimensions",
    "load_vision_config",
    "resolve_cloud_api_key",
    "run_ocr",
    "save_vision_config",
    "sips_resize",
]
