"""Vision LLM 客户端:任意 OpenAI 兼容 ``chat.completions`` 端点 + 图片输入。

复用 :class:`shishi.enrich.client.OpenAICompatClient` 的 AsyncOpenAI 模式
(惰性 import、每调用一次 create、60s 请求级超时;整体跑在 sidecar 后台
线程的事件流里,不受壳 120s 单请求硬超时限制),新增 image content part
消息 —— local-ocr 技能实证配方:image_url part 在前、text part 在后,图片
以 ``data:<mime>;base64,...`` 直发(本地路径对云端不可达,base64 是唯一通形)。

通道语义(task 10-03-image-input design):本地端点(``api_key=None``)零
``Authorization`` 头 —— OpenAI SDK 构造必须给非空 api_key,以占位符构造后
用 ``default_headers={"Authorization": None}`` 显式剥离(httpx 对 None 头值
的语义即删除);云端携带真实 Bearer key(key 只经构造参数传递,不落日志)。

发送前长边 >2048 先 :func:`shishi.vision.ocr.sips_resize` 压缩再 base64
(VL 输入提速,jpeg 产物;库内 10MB 原件不动,压缩临时文件即弃)。sips 探测
失败(非 macOS)时跳过压缩原图直发,不硬失败。
"""

from __future__ import annotations

import asyncio
import base64
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 私有符号受控复用(与 desktop/entry.py 复用 schema._SECRET_REF_RE 同一先例):
# 响应文本/usage 提取只此一处定义,防两处漂移。
from shishi.enrich.client import _message_text, _usage_tokens
from shishi.vision.ocr import image_dimensions, sips_resize
from shishi.vision.settings import VisionConfigError

__all__ = [
    "INSTALL_COMMAND",
    "LOCAL_PLACEHOLDER_KEY",
    "MAX_LONG_EDGE",
    "VisionClient",
    "VisionResult",
]

INSTALL_COMMAND = "pip install 'shishi[vision]'  # 或 uv add 'shishi[vision]'"
#: VL 输入长边上限(超过先 sips 等比压缩再 base64)。
MAX_LONG_EDGE = 2048
#: 本地通道占位 key:OpenAI SDK 构造要求非空 api_key;占位后以 default_headers 剥离鉴权头。
LOCAL_PLACEHOLDER_KEY = "myia-local-no-auth"

#: 后缀 → MIME(入库白名单 png/jpg/webp 保证未知后缀不可达;兜底 png)。
_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@dataclass(frozen=True)
class VisionResult:
    """一次看图调用:解读文本 + 观测 token 用量(缺 usage 报 0,不臆测)。"""

    text: str
    total_tokens: int


class VisionClient:
    """OpenAI 兼容视觉端点的最小异步封装(每调用一次 ``chat.completions.create``)。

    构造永不触碰可选依赖:``openai`` 惰性加载于首次 :meth:`analyze`
    (:meth:`_ensure_async_client`),与 enrich 同一懒加载契约 —— 没装 extras
    也能构造,首次调用才结构化报 ``dependency_missing``。

    Args:
        base_url: OpenAI 兼容端点 base(本地含 ``/v1`` 路径,如
            ``http://127.0.0.1:8080/v1``)。
        model: 模型名;本地 mlx-vlm 场景即模型目录路径(「设置本地模型路径」落点)。
        api_key: 已解析的云端 key;``None`` = 本地通道(占位构造 + 剥离
            Authorization 头,见 :data:`LOCAL_PLACEHOLDER_KEY`)。
        timeout_seconds: 单请求硬上限(asyncio.wait_for + SDK timeout 双保险;
            缺省 180s —— 60s 在 Metal JIT 首请求/长 describe/低速主机下实测越线,
            见 10-03 装机冒烟 protocol-e2e2-summary.md)。
        max_output_tokens: 端点侧输出上限。

    Raises:
        VisionConfigError: 首次 :meth:`analyze` 时 ``dependency_missing``
            (``openai`` 未安装;vision extras 已含 openai,见 INSTALL_COMMAND)。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        # 10-03 装机冒烟定量:60s 余量不足 —— 首视觉请求 Metal JIT ~55s、
        # describe 结构化输出 500-800 token、争用主机 4-5 tok/s 三者任一即越线
        # (实测 describe 448 token / 110.5s 全程跑通,机制无恙,唯超时线太紧)。
        # 事件流本就不受壳 120s 限制,180s 只放宽单请求硬上限。
        timeout_seconds: float = 180.0,
        max_output_tokens: int = 2048,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._async_client: Any | None = None

    def _ensure_async_client(self) -> Any:
        """Build (once) the lazily-imported ``openai.AsyncOpenAI``."""
        if self._async_client is None:
            openai = self._import_openai()
            init_kwargs: dict[str, Any] = {
                "base_url": self._base_url,
                "api_key": self._api_key if self._api_key is not None else LOCAL_PLACEHOLDER_KEY,
                "timeout": self._timeout_seconds,
            }
            if self._api_key is None:
                # 本地端点零鉴权头:httpx 不支持 None 头值(10-03 装机冒烟实测
                # TypeError: Header value must be str or bytes, not NoneType;
                # None=删头是 requests 的语义,httpx 没有),以空串覆写占位 Bearer ——
                # ``Authorization:``(空)不含任何凭据,本地端点(mlx-vlm)不受影响。
                init_kwargs["default_headers"] = {"Authorization": ""}
            self._async_client = openai.AsyncOpenAI(**init_kwargs)
        return self._async_client

    @staticmethod
    def _import_openai() -> Any:
        try:
            return importlib.import_module("openai")
        except ImportError as exc:
            raise VisionConfigError(
                "dependency_missing",
                f"vision 依赖 openai 未安装:请先执行 {INSTALL_COMMAND}"
                "(vision extras 已含 openai)",
                details={"package": "openai", "install": INSTALL_COMMAND},
            ) from exc

    async def analyze(self, *, image_path: Path | str, prompt: str) -> VisionResult:
        """一次看图调用(image_url part + text part;长边超限先压缩)。

        Raises:
            TimeoutError: 调用超过 ``timeout_seconds``。
            Exception: 端点/网络错误原样上抛(调用方按通道语义翻译结构化错误)。
        """
        client = self._ensure_async_client()
        data_url = self._image_data_url(Path(image_path))
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                temperature=0,
                max_tokens=self._max_output_tokens,
            ),
            timeout=self._timeout_seconds,
        )
        return VisionResult(text=_message_text(response), total_tokens=_usage_tokens(response))

    async def aclose(self) -> None:
        """Release the underlying httpx-backed client, when built."""
        if self._async_client is not None:
            close = getattr(self._async_client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            self._async_client = None

    def _image_data_url(self, image_path: Path) -> str:
        """图片 → ``data:<mime>;base64,...``;压缩产物临时文件即弃。"""
        feed, mime = self._prepare_image(image_path)
        try:
            payload = feed.read_bytes()
            return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
        finally:
            if feed != image_path:
                feed.unlink(missing_ok=True)

    def _prepare_image(self, image_path: Path) -> tuple[Path, str]:
        """发送前预处理:长边 >2048 先 sips 压缩(jpeg);探测失败按原图直发。"""
        dimensions = image_dimensions(image_path)
        if dimensions is None or max(dimensions) <= MAX_LONG_EDGE:
            return image_path, _MIME_BY_EXT.get(image_path.suffix.lower(), "image/png")
        return sips_resize(image_path, MAX_LONG_EDGE, suffix=".jpg"), "image/jpeg"
