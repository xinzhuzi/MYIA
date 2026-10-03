"""双引擎 OCR 统一接口:macOS Vision(ocrmac)+ RapidOCR(rapidocr-onnxruntime)。

``run_ocr(path, engine)`` 返回统一形状的逐行结果 ``[OcrLine{text, conf}]``
(task 10-03-image-input,10-03 修订:两个引擎都要有,可切换):

- ``vision``(默认):ocrmac 封装 macOS Vision(zh-Hans+en-US、accurate、逐行
  置信度),零模型下载;宽 <1000px 先 ``sips -Z 2000`` 放大再识别(local-ocr
  实证:小图不放大,行识别质量塌);ocrmac 1.x 无 use_language_correction 参
  (VNRecognizeTextRequest 缺省即关语言纠正,报错码/ID 保真语义保持)。
  置信度刻度 0.30-1.0 真实分布,≤0.5 警示阈值主要对此引擎有意义。
- ``rapidocr``:rapidocr-onnxruntime 内置默认 det/rec/cls 模型,零下载;
  置信度刻度普遍 ≥0.9(与 Vision 不可直接互比,UI 注记来源引擎,阈值统一)。

两个引擎都是可选依赖(extras ``shishi[vision]``),惰性 import —— 未装时结构化
报错并附安装命令,核心流水线零重依赖红线不破。置信度原样透传,不截断不过滤:
低置信行是「升二级看图」的触发证据,不是噪音。

本模块同时导出 sips 图片工具(:func:`image_dimensions` / :func:`sips_resize`),
供 vision 包内复用(client.py 发送前长边压缩);macOS 之外 sips 不可用时探测
返回 None,调用方按「不缩放/原图直发」降级,不硬失败。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shishi.vision.settings import OCR_ENGINES

__all__ = [
    "INSTALL_COMMAND",
    "OCR_ENGINES",
    "OCRError",
    "OcrLine",
    "VISION_MIN_WIDTH",
    "VISION_UPSCALE_TARGET",
    "image_dimensions",
    "run_ocr",
    "sips_resize",
]

INSTALL_COMMAND = "pip install 'shishi[vision]'  # 或 uv add 'shishi[vision]'"

#: 低于此宽度先 sips 放大再识别(local-ocr 实证阈值;仅 vision 引擎路径)。
VISION_MIN_WIDTH = 1000
#: 放大目标长边像素(sips -Z 等比)。
VISION_UPSCALE_TARGET = 2000
#: Vision OCR 语言偏好(中英混排报错截图的主场景)。
_VISION_LANGUAGES = ("zh-Hans", "en-US")


class OCRError(RuntimeError):
    """OCR 结构化失败(engine 路由/依赖/引擎执行;协议层翻译为 image_* 码)。

    Attributes:
        code: ``engine_unknown``(非法引擎名,协议层译 ``image_engine_unknown``) /
            ``dependency_missing``(extras 未装,附 :data:`INSTALL_COMMAND`) /
            ``engine_failed``(引擎执行失败,原文原因入 message)。
        details: 结构化上下文(engine / package / install)。
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form(协议错误 data 消费)。"""
        return {"error_type": self.code, "message": str(self), **self.details}


@dataclass(frozen=True)
class OcrLine:
    """一行识别结果:文本 + 置信度(0-1,两引擎刻度不可互比,原样透传)。"""

    text: str
    conf: float


def run_ocr(image_path: Path | str, engine: str) -> list[OcrLine]:
    """按引擎路由执行 OCR,返回逐行 ``{text, conf}``(空图合法返回空表)。

    Raises:
        OCRError: ``engine_unknown`` / ``dependency_missing`` / ``engine_failed``。
    """
    path = Path(image_path)
    if engine == "vision":
        return _run_vision(path)
    if engine == "rapidocr":
        return _run_rapidocr(path)
    raise OCRError(
        "engine_unknown",
        f"未知 OCR 引擎 {engine!r}(可选:{'/'.join(OCR_ENGINES)})",
        details={"engine": engine, "allowed": list(OCR_ENGINES)},
    )


def _run_vision(path: Path) -> list[OcrLine]:
    """macOS Vision via ocrmac;小图先放大,识别后临时文件即弃。"""
    try:
        ocrmac = importlib.import_module("ocrmac")
        # ocrmac 1.x(1.0.1 实测,10-03 装机冒烟):OCR 类在 ocrmac.ocrmac 子模块,
        # 顶层包不再导出;旧版/测试桩顶层带 OCR —— 先取顶层,缺失再下钻子模块。
        ocr_cls = getattr(ocrmac, "OCR", None)
        if ocr_cls is None:
            ocr_cls = importlib.import_module("ocrmac.ocrmac").OCR
    except ImportError as exc:
        raise OCRError(
            "dependency_missing",
            f"vision 引擎依赖 ocrmac 未安装(macOS Vision 封装):请先执行 {INSTALL_COMMAND}",
            details={"engine": "vision", "package": "ocrmac", "install": INSTALL_COMMAND},
        ) from exc
    feed = path
    upscaled: Path | None = None
    dimensions = image_dimensions(path)
    if dimensions is not None and dimensions[0] < VISION_MIN_WIDTH:
        try:
            upscaled = sips_resize(path, VISION_UPSCALE_TARGET)
        except OSError as exc:
            raise OCRError(
                "engine_failed", f"vision 引擎预放大失败: {exc}", details={"engine": "vision"}
            ) from exc
        feed = upscaled
    try:
        # ocrmac 1.x(装机冒烟实测)不收 use_language_correction(0.x 参数);
        # VNRecognizeTextRequest 的 usesLanguageCorrection 缺省即关,语义保持。
        # unit 保持缺省 token:Vision 框架 observation 即行级、置信度真实;
        # unit="line" 反而把置信度硬编码 1.0(ocrmac 源码 253-260 行),不可用。
        request = ocr_cls(
            str(feed),
            language_preference=list(_VISION_LANGUAGES),
            recognition_level="accurate",
        )
        observations = request.recognize()
    except Exception as exc:  # noqa: BLE001 — 引擎内部任何失败统一结构化包装
        raise OCRError(
            "engine_failed",
            f"vision 引擎识别失败: {type(exc).__name__}: {exc}",
            details={"engine": "vision"},
        ) from exc
    finally:
        if upscaled is not None:
            upscaled.unlink(missing_ok=True)  # /tmp 即弃
    # ocrmac recognize() 逐行产出 (text, confidence, bounding_box) 三元组。
    return [OcrLine(text=str(text), conf=float(conf)) for text, conf, *_ in observations]


def _run_rapidocr(path: Path) -> list[OcrLine]:
    """RapidOCR(onnxruntime);内置默认模型,首次调用才加载。"""
    try:
        rapidocr = importlib.import_module("rapidocr_onnxruntime")
    except ImportError as exc:
        raise OCRError(
            "dependency_missing",
            f"rapidocr 引擎依赖 rapidocr-onnxruntime 未安装:请先执行 {INSTALL_COMMAND}",
            details={"engine": "rapidocr", "package": "rapidocr-onnxruntime", "install": INSTALL_COMMAND},
        ) from exc
    try:
        engine = rapidocr.RapidOCR()
        result, _elapse = engine(str(path))
    except Exception as exc:  # noqa: BLE001 — 引擎内部任何失败统一结构化包装
        raise OCRError(
            "engine_failed",
            f"rapidocr 引擎识别失败: {type(exc).__name__}: {exc}",
            details={"engine": "rapidocr"},
        ) from exc
    if not result:  # 无文字图片:合法空结果(不是错误;describe 模式的事)
        return []
    # RapidOCR 逐行产出 [box, text, score];score 刻度普遍 ≥0.9(见模块头)。
    return [OcrLine(text=str(item[1]), conf=float(item[2])) for item in result]


# ---------------------------------------------------------------------------
# sips 图片工具(macOS 系统自带,零新依赖;vision 包内共享)
# ---------------------------------------------------------------------------


def image_dimensions(image_path: Path | str) -> tuple[int, int] | None:
    """sips 读像素 (宽, 高);任何失败返回 None(调用方按不缩放降级,不硬失败)。"""
    try:
        proc = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image_path)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    dims: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        key, sep, raw = line.partition(":")
        key_name = key.strip()
        if sep and key_name in ("pixelWidth", "pixelHeight"):
            try:
                dims[key_name] = int(raw.strip())
            except ValueError:
                return None
    if "pixelWidth" in dims and "pixelHeight" in dims:
        return dims["pixelWidth"], dims["pixelHeight"]
    return None


def sips_resize(image_path: Path | str, target_long_edge: int, *, suffix: str = ".png") -> Path:
    """sips 等比缩放(``-Z`` 长边上限;产物格式由 ``suffix`` 决定)到临时文件。

    产物是调用方责任:用完即删(临时文件落系统临时目录)。失败抛 :class:`OSError`
    (调用方按各自语义包装:OCR 路径 → engine_failed;VL 发送路径 → 原样上抛)。
    """
    handle, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        proc = subprocess.run(
            ["sips", "-Z", str(target_long_edge), str(image_path), "--out", tmp_name],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp.unlink(missing_ok=True)
        raise OSError(f"sips 缩放失败: {type(exc).__name__}: {exc}") from exc
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise OSError(f"sips 缩放失败(退出码 {proc.returncode}): {proc.stderr.strip()[:200]}")
    return tmp
