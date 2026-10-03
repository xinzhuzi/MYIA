"""src/myia/vision 单测(10-03-image-input):ocr / client / settings。

引擎与 AsyncOpenAI 全 mock(ocrmac / rapidocr_onnxruntime / openai 在 CI 与
dev 环境均未安装 —— extras 未开):假引擎模块注入 ``sys.modules``,假
AsyncOpenAI 经 ``VisionClient._import_openai`` 替换;sips 探测/缩放全部
monkeypatch(CI 是 ubuntu,sips 不可用)。零外网、零真实钥匙链。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import run

from myia.vision import (
    VisionClient,
    VisionConfig,
    VisionConfigError,
    VisionResult,
    load_vision_config,
    save_vision_config,
)
from myia.vision import client as vision_client
from myia.vision import ocr as vision_ocr
from myia.vision.ocr import OCRError, OcrLine, run_ocr

# 魔数即可:嗅探/引擎/客户端全 mock,无任何组件真解析图片内容。
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"vision-fixture-body"


@pytest.fixture()
def png_file(tmp_path: Path) -> Path:
    path = tmp_path / "shot.png"
    path.write_bytes(PNG_BYTES)
    return path


# ---------------------------------------------------------------------------
# settings.py:同门校验(明文/env: 拒载、枚举、http(s)、未知字段、装载往返)
# ---------------------------------------------------------------------------


class TestVisionSettings:
    def test_defaults_construct_and_payload_shape(self):
        config = VisionConfig()
        assert config.to_payload() == {
            "channel_default": "local",
            "local": {"base_url": "http://127.0.0.1:8080/v1", "model": ""},
            "cloud": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "model": "glm-4.6v",
                "api_key": None,
            },
            "ocr": {"enabled": True, "engine_default": "vision"},
        }

    def test_plaintext_api_key_refused(self):
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig(cloud_api_key_ref="sk-123456")
        assert excinfo.value.code == "credential_plaintext"
        assert excinfo.value.to_dict()["field"] == "cloud.api_key"

    def test_env_api_key_refused_not_keychain(self):
        # 只收 keychain: 引用:env: 同样拒(security-baseline:桌面凭据走钥匙串)。
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig(cloud_api_key_ref="env:MY_KEY")
        assert excinfo.value.code == "invalid_credential_ref"

    def test_keychain_api_key_accepted(self):
        ref = "keychain:myia/image/api_key"
        assert VisionConfig(cloud_api_key_ref=ref).cloud_api_key_ref == ref

    def test_invalid_engine_default(self):
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig(ocr_engine_default="tesseract")
        assert excinfo.value.code == "invalid_engine"

    def test_invalid_channel(self):
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig(channel_default="edge")
        assert excinfo.value.code == "invalid_channel"

    def test_non_http_base_url(self):
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig(local_base_url="127.0.0.1:8080")
        assert excinfo.value.code == "invalid_base_url"
        assert excinfo.value.details["field"] == "local_base_url"

    def test_unknown_field_refused(self):
        with pytest.raises(VisionConfigError) as excinfo:
            VisionConfig.from_payload({"chanel_default": "local"})  # 手滑字段名
        assert excinfo.value.code == "unknown_field"

    def test_load_missing_file_returns_defaults(self, tmp_path):
        config = load_vision_config(tmp_path / "vision.yaml")
        assert config == VisionConfig()

    def test_load_empty_file_returns_defaults(self, tmp_path):
        path = tmp_path / "vision.yaml"
        path.write_text("", encoding="utf-8")
        assert load_vision_config(path) == VisionConfig()

    def test_load_corrupt_yaml_structured(self, tmp_path):
        path = tmp_path / "vision.yaml"
        path.write_text("channel_default: [unclosed", encoding="utf-8")
        with pytest.raises(VisionConfigError) as excinfo:
            load_vision_config(path)
        assert excinfo.value.code == "vision_unreadable"

    def test_load_invalid_content_structured(self, tmp_path):
        path = tmp_path / "vision.yaml"
        path.write_text("ocr:\n  engine_default: tesseract\n", encoding="utf-8")
        with pytest.raises(VisionConfigError) as excinfo:
            load_vision_config(path)
        assert excinfo.value.code == "invalid_engine"

    def test_save_then_load_roundtrip_no_plaintext(self, tmp_path):
        path = tmp_path / "vision.yaml"
        original = VisionConfig(
            channel_default="cloud",
            local_model="/models/qwen3-vl-8b-mlx",
            cloud_api_key_ref="keychain:myia/image/api_key",
            ocr_engine_default="rapidocr",
        )
        save_vision_config(path, original)
        text = path.read_text(encoding="utf-8")
        assert "keychain:myia/image/api_key" in text  # 引用原样落盘
        assert "myia/image" in text
        assert load_vision_config(path) == original  # 往返一致

    def test_from_payload_invalid_never_touches_disk(self, tmp_path):
        path = tmp_path / "vision.yaml"
        with pytest.raises(VisionConfigError):
            VisionConfig.from_payload({"cloud": {"api_key": "sk-plaintext"}})
        assert not path.exists()  # 拒载 = 零写入


# ---------------------------------------------------------------------------
# ocr.py:引擎路由 / 依赖缺失 / 小图放大 / 输出形状(双引擎全 mock)
# ---------------------------------------------------------------------------


class TestOcr:
    @staticmethod
    def _install_ocrmac(monkeypatch, observations, record, recognize_error=None):
        class FakeOCR:
            def __init__(self, path: str, **kwargs: Any) -> None:
                record["path"] = path
                record["kwargs"] = kwargs

            def recognize(self):
                if recognize_error is not None:
                    raise recognize_error
                return observations

        monkeypatch.setitem(sys.modules, "ocrmac", SimpleNamespace(OCR=FakeOCR))

    def test_vision_engine_roundtrip(self, monkeypatch, png_file):
        record: dict[str, Any] = {}
        self._install_ocrmac(
            monkeypatch, [("中文行一", 0.98), ("IP 2403:27c0:c03:1::71", 0.50)], record
        )
        lines = run_ocr(png_file, "vision")
        assert lines == [OcrLine("中文行一", 0.98), OcrLine("IP 2403:27c0:c03:1::71", 0.50)]
        # 关键 kwargs 钉死:accurate + 中英语言(ocrmac 1.x 无 use_language_correction,
        # VNRecognizeTextRequest 缺省即关语言纠正;装机冒烟 10-03 实测签名)。
        assert record["kwargs"]["recognition_level"] == "accurate"
        assert record["kwargs"]["language_preference"] == ["zh-Hans", "en-US"]
        assert "use_language_correction" not in record["kwargs"]
        assert record["path"] == str(png_file)

    def test_vision_low_confidence_lines_pass_through_unchanged(self, monkeypatch, png_file):
        """置信度阈值分支:低置信行原样透传,不滤不改(证据保真;阈值判定在 UI)。"""
        record: dict[str, Any] = {}
        self._install_ocrmac(monkeypatch, [("疑似乱码行", 0.30)], record)
        assert run_ocr(png_file, "vision") == [OcrLine("疑似乱码行", 0.30)]

    def test_vision_upscales_narrow_images(self, monkeypatch, png_file, tmp_path):
        record: dict[str, Any] = {}
        self._install_ocrmac(monkeypatch, [("放大后行", 0.9)], record)
        monkeypatch.setattr(vision_ocr, "image_dimensions", lambda _p: (640, 480))
        upscaled = tmp_path / "upscaled.png"
        upscaled.write_bytes(PNG_BYTES)
        resize_calls: list[tuple[Any, int]] = []

        def fake_resize(image_path, target_long_edge, *, suffix=".png"):
            resize_calls.append((image_path, target_long_edge))
            return upscaled

        monkeypatch.setattr(vision_ocr, "sips_resize", fake_resize)
        lines = run_ocr(png_file, "vision")
        assert lines == [OcrLine("放大后行", 0.9)]
        assert record["path"] == str(upscaled)  # 引擎吃的是放大产物
        assert resize_calls == [(png_file, vision_ocr.VISION_UPSCALE_TARGET)]
        assert not upscaled.exists()  # 临时文件用完即弃

    def test_vision_wide_image_not_upscaled(self, monkeypatch, png_file):
        record: dict[str, Any] = {}
        self._install_ocrmac(monkeypatch, [("全尺寸行", 0.9)], record)
        monkeypatch.setattr(vision_ocr, "image_dimensions", lambda _p: (3840, 2160))
        run_ocr(png_file, "vision")
        assert record["path"] == str(png_file)

    def test_vision_dependency_missing(self, monkeypatch, png_file):
        monkeypatch.setitem(sys.modules, "ocrmac", None)
        with pytest.raises(OCRError) as excinfo:
            run_ocr(png_file, "vision")
        assert excinfo.value.code == "dependency_missing"
        assert "myia[vision]" in str(excinfo.value)

    def test_vision_engine_failure_wrapped(self, monkeypatch, png_file):
        self._install_ocrmac(monkeypatch, [], {}, recognize_error=RuntimeError("boom"))
        with pytest.raises(OCRError) as excinfo:
            run_ocr(png_file, "vision")
        assert excinfo.value.code == "engine_failed"
        assert "boom" in str(excinfo.value)

    @staticmethod
    def _install_rapidocr(monkeypatch, result):
        class FakeRapidOCR:
            def __call__(self, path: str):
                return result, [0.12]

        monkeypatch.setitem(
            sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR)
        )

    def test_rapidocr_roundtrip(self, monkeypatch, png_file):
        # RapidOCR 返回 (lines, elapse);每行 = [box, text, score]。
        line = [[[0, 0], [10, 0], [10, 10], [0, 10]], "羊毛行RapidOCR", 0.95]
        self._install_rapidocr(monkeypatch, [line])
        assert run_ocr(png_file, "rapidocr") == [OcrLine("羊毛行RapidOCR", 0.95)]

    def test_rapidocr_no_text_returns_empty(self, monkeypatch, png_file):
        self._install_rapidocr(monkeypatch, None)
        assert run_ocr(png_file, "rapidocr") == []

    def test_rapidocr_dependency_missing(self, monkeypatch, png_file):
        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
        with pytest.raises(OCRError) as excinfo:
            run_ocr(png_file, "rapidocr")
        assert excinfo.value.code == "dependency_missing"

    def test_unknown_engine_structured(self, png_file):
        with pytest.raises(OCRError) as excinfo:
            run_ocr(png_file, "tesseract")
        assert excinfo.value.code == "engine_unknown"
        assert excinfo.value.details["allowed"] == ["vision", "rapidocr"]


# ---------------------------------------------------------------------------
# client.py:image part 组装 / 本地无鉴权头 / 云端 Bearer / 长边压缩 / 懒加载
# ---------------------------------------------------------------------------


class TestVisionClient:
    @staticmethod
    def _install_fake_openai(monkeypatch, reply="解读文本", tokens=17):
        created: list[Any] = []

        class FakeAsyncOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                self.init_kwargs = kwargs
                self.create_calls: list[dict[str, Any]] = []
                self.closed = False
                created.append(self)
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=self._create)
                )

            async def _create(self, **kwargs: Any):
                self.create_calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                    usage=SimpleNamespace(total_tokens=tokens),
                )

            async def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(
            VisionClient, "_import_openai", staticmethod(lambda: SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
        )
        return created

    def test_local_channel_no_auth_header_and_part_assembly(self, monkeypatch, png_file):
        created = self._install_fake_openai(monkeypatch)
        client = VisionClient("http://127.0.0.1:8080/v1", "/models/qwen3-vl-8b-mlx")
        result = run(client.analyze(image_path=png_file, prompt="读出图中全部文字"))
        assert isinstance(result, VisionResult)
        assert result.text == "解读文本" and result.total_tokens == 17
        underlying = created[0]
        # 本地通道:占位 key 过 SDK 构造,Authorization 头以空串覆写(10-03 装机
        # 冒烟:httpx 无 None=删头语义,None 头值运行期 TypeError;空串零凭据)。
        assert underlying.init_kwargs["api_key"] == vision_client.LOCAL_PLACEHOLDER_KEY
        assert underlying.init_kwargs["default_headers"] == {"Authorization": ""}
        assert underlying.init_kwargs["base_url"] == "http://127.0.0.1:8080/v1"
        # image part 组装:image_url(data URL)在前、text part 在后(local-ocr 配方)。
        (call,) = underlying.create_calls
        assert call["model"] == "/models/qwen3-vl-8b-mlx"
        assert call["temperature"] == 0
        parts = call["messages"][0]["content"]
        assert parts[0]["type"] == "image_url"
        assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert parts[1] == {"type": "text", "text": "读出图中全部文字"}
        run(client.aclose())
        assert underlying.closed

    def test_cloud_channel_passes_bearer_key(self, monkeypatch, png_file):
        created = self._install_fake_openai(monkeypatch)
        run(
            VisionClient("https://open.bigmodel.cn/api/paas/v4", "glm-4.6v", api_key="sk-cloud").analyze(
                image_path=png_file, prompt="描述"
            )
        )
        init_kwargs = created[0].init_kwargs
        assert init_kwargs["api_key"] == "sk-cloud"
        assert "default_headers" not in init_kwargs  # 云端不剥离鉴权头

    def test_long_edge_compressed_to_jpeg(self, monkeypatch, png_file, tmp_path):
        created = self._install_fake_openai(monkeypatch)
        monkeypatch.setattr(vision_client, "image_dimensions", lambda _p: (4096, 2304))
        compressed = tmp_path / "resized.jpg"
        compressed.write_bytes(b"\xff\xd8\xffcompressed")
        resize_calls: list[tuple[Any, int, str]] = []

        def fake_resize(image_path, target_long_edge, *, suffix=".png"):
            resize_calls.append((image_path, target_long_edge, suffix))
            return compressed

        monkeypatch.setattr(vision_client, "sips_resize", fake_resize)
        run(VisionClient("http://127.0.0.1:8080/v1", "m").analyze(image_path=png_file, prompt="p"))
        assert resize_calls == [(png_file, vision_client.MAX_LONG_EDGE, ".jpg")]
        data_url = created[0].create_calls[0]["messages"][0]["content"][0]["image_url"]["url"]
        assert data_url.startswith("data:image/jpeg;base64,")
        assert not compressed.exists()  # 压缩产物临时文件即弃

    def test_small_image_sent_verbatim(self, monkeypatch, png_file):
        created = self._install_fake_openai(monkeypatch)
        monkeypatch.setattr(vision_client, "image_dimensions", lambda _p: (1024, 768))
        run(VisionClient("http://127.0.0.1:8080/v1", "m").analyze(image_path=png_file, prompt="p"))
        data_url = created[0].create_calls[0]["messages"][0]["content"][0]["image_url"]["url"]
        assert data_url.startswith("data:image/png;base64,")
        assert data_url.endswith(__import__("base64").b64encode(PNG_BYTES).decode("ascii"))

    def test_dimensions_probe_failure_degrades_to_original(self, monkeypatch, png_file):
        created = self._install_fake_openai(monkeypatch)
        monkeypatch.setattr(vision_client, "image_dimensions", lambda _p: None)  # 非 macOS/无 sips
        run(VisionClient("http://127.0.0.1:8080/v1", "m").analyze(image_path=png_file, prompt="p"))
        data_url = created[0].create_calls[0]["messages"][0]["content"][0]["image_url"]["url"]
        assert data_url.startswith("data:image/png;base64,")

    def test_openai_missing_raises_dependency_missing(self, monkeypatch, png_file):
        monkeypatch.setitem(sys.modules, "openai", None)
        client = VisionClient("http://127.0.0.1:8080/v1", "m")  # 构造成功(懒加载契约)
        with pytest.raises(VisionConfigError) as excinfo:
            run(client.analyze(image_path=png_file, prompt="p"))
        assert excinfo.value.code == "dependency_missing"
        assert "myia[vision]" in str(excinfo.value)
