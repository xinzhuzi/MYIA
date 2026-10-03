"""vision/collect 图片处理环单测(task 10-03-vision-pipeline)。

全量 mock、零外网:下载走 ``httpx.MockTransport``,DNS 解析 monkeypatch
``collect._resolve_host``(SSRF 判定不真解析),OCR monkeypatch
``collect.run_ocr``,VL 通道 monkeypatch ``collect.VisionClient``——与本仓
``tests/test_vision.py`` 的假引擎注入同款纪律。

覆盖面 = PRD 降级矩阵逐行 + 限额双闸(max_images/max_per_run)+ SSRF +
预算耗尽 + 管线挂点端到端(MockTransport 全链,断言 fetch 尾部产物挂
metadata、未开 images 的品类零影响)。
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

import myia.vision.collect as collect
from myia.enrich.scoring import BudgetTracker
from myia.schema import ImagesConfig, load_category
from myia.vision.client import VisionResult
from myia.vision.ocr import OcrLine
from myia.vision.settings import VisionConfig

PUBLIC_IPS = ["93.184.216.34"]
PNG_HEAD = b"\x89PNG\r\n\x1a\n"


class Item:
    """Duck-typed pipeline item(与 pipeline.Item 同形:url/title/metadata)。"""

    def __init__(self, url: str = "https://example.com/item", title: str = "示例条目") -> None:
        self.url = url
        self.title = title
        self.metadata: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch):
    """DNS 桩(零外网):字面 IP 原样回(真实 getaddrinfo 语义),主机名给公网。"""

    def _resolve(host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return list(PUBLIC_IPS)
        return [host]

    monkeypatch.setattr(collect, "_resolve_host", _resolve)


def png_bytes(size: int = 4096) -> bytes:
    return PNG_HEAD + b"0" * (size - len(PNG_HEAD))


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def serve(routes: dict[str, Any]) -> httpx.AsyncClient:
    """path → bytes | httpx.Response;缺省 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, text="missing")
        if isinstance(target, httpx.Response):
            return target
        return httpx.Response(200, content=target)

    return make_client(handler)


def fake_ocr(monkeypatch: pytest.MonkeyPatch, lines_by_path: dict[str, list[str]] | None = None,
             error: Exception | None = None):
    """run_ocr 桩:按文件内容寻址(同图同产物),或统一抛错。"""

    def _run_ocr(path, engine):  # noqa: ANN001 — 与真签名一致(path, engine)
        if error is not None:
            raise error
        key = Path(path).read_bytes()[:64].decode("latin1")
        lines = (lines_by_path or {}).get(key, ["第一行", "第二行"])
        return [OcrLine(text=text, conf=0.99) for text in lines]

    monkeypatch.setattr(collect, "run_ocr", _run_ocr)


@dataclass
class FakeVlClient:
    """VisionClient 桩:每图返回固定文本/用量;可注入异常与延迟计数。"""

    results: list[VisionResult] = field(default_factory=lambda: [VisionResult(text="图析描述", total_tokens=120)])
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def analyze(self, *, image_path, prompt):  # noqa: ANN001
        self.calls.append({"image_path": str(image_path), "prompt": prompt})
        if self.error is not None:
            raise self.error
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]

    async def aclose(self) -> None:
        return None


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 基础:候选 URL / markdown 同域收集 / run 配额
# ---------------------------------------------------------------------------


class TestCandidatesAndHelpers:
    def test_candidate_urls_merges_images_list_and_image_str_dedup(self):
        metadata = {
            "images": ["https://a.example/1.png", "https://a.example/1.png", "not-a-url"],
            "image": "https://a.example/2.png",
        }
        assert collect._candidate_urls(metadata) == [
            "https://a.example/1.png",
            "https://a.example/2.png",
        ]

    def test_candidate_urls_empty_when_nothing_usable(self):
        assert collect._candidate_urls({}) == []
        assert collect._candidate_urls({"image": "javascript:alert(1)"}) == []
        assert collect._candidate_urls({"images": "https://a.example/1.png"})
        assert collect._candidate_urls({"images": []}) == []

    def test_markdown_image_urls_same_domain_only_relative_resolved(self):
        markdown = (
            "前文 ![图一](/a.png) 中段 ![跨域广告](https://evil.example/x.jpg) "
            '![标题图](https://example.com/b.jpg "hint") '
            "[普通链接](https://example.com/c.png) 尾部 ![重复](/a.png)"
        )
        assert collect.markdown_image_urls(markdown, "https://example.com/page") == [
            "https://example.com/a.png",
            "https://example.com/b.jpg",
        ]

    def test_run_state_take_grants_then_zero(self):
        state = collect.ImageRunState(remaining=2)
        assert state.take(5) == 2
        assert state.take(1) == 0
        assert state.remaining == 0


# ---------------------------------------------------------------------------
# 降级矩阵逐行(PRD Req 4 / AC 4)
# ---------------------------------------------------------------------------


class TestDegradationMatrix:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, **overrides})

    def test_download_http_error_all_fail_marks_none(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["images"] = ["https://example.com/gone.png"]
        client = serve({})  # 一切 404
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"
        assert item.metadata == {"images": ["https://example.com/gone.png"], "image_status": "none"}

    def test_ssrf_private_host_rejected(self, monkeypatch):
        monkeypatch.setattr(collect, "_resolve_host", lambda host: ["10.0.0.5"])
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://internal.example/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_fake_ip_proxy_range_allowed(self, monkeypatch):
        """fake-ip 代理段(198.18.0.0/15)豁免:DNS 应答全进该段时照常下载+OCR。

        回归钉:2026-10-03 真网探针实证,不豁免则 fake-ip 代理环境(Clash 等)
        下所有域名的图片都被 reason=ssrf 误杀,自动识图全灭。
        """
        monkeypatch.setattr(collect, "_resolve_host", lambda host: ["198.18.0.213"])
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://cdn.example/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "ok"
        assert item.metadata.get("image_ocr")

    def test_ssrf_private_literal_loopback_rejected(self, monkeypatch):
        """127.0.0.1 直写主机同样拒(字面 IP 走同一条私网判定,public_dns 桩原样回)。"""
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "http://127.0.0.1:8080/v1/leak.png"
        client = serve({"/v1/leak.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_redirect_to_private_host_rejected_per_hop(self, monkeypatch):
        """重定向逐跳复核:公网 → 跳私网 = 拒(SSRF 经重定向绕过不通)。"""
        resolve_map = {"public.example": ["93.184.216.34"], "internal.example": ["192.168.1.9"]}
        monkeypatch.setattr(collect, "_resolve_host", lambda host: resolve_map[host])
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://public.example/hop.png"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/hop.png":
                return httpx.Response(302, headers={"Location": "https://internal.example/leak.png"})
            return httpx.Response(200, content=png_bytes())

        client = make_client(handler)
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_redirect_loop_over_hops_marks_none(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/loop.png"
        client = serve({"/loop.png": httpx.Response(302, headers={"Location": "/loop.png"})})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_format_rejected_outside_magic_whitelist(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/trojan.png"
        client = serve({"/trojan.png": b"<html><body>not an image</body></html>"})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_min_bytes_skip_marks_none(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/icon.png"
        client = serve({"/icon.png": png_bytes(size=64)})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(min_bytes=10_240), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_stream_cap_over_max_bytes_marks_none(self, monkeypatch):
        monkeypatch.setattr(collect, "MAX_IMAGE_BYTES", 1024)
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/huge.png"
        client = serve({"/huge.png": png_bytes(size=4096)})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_ocr_engine_failure_marks_ocr_failed(self, monkeypatch):
        from myia.vision.ocr import OCRError

        fake_ocr(monkeypatch, error=OCRError("dependency_missing", "ocrmac 未安装"))
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "ocr_failed"
        assert "image_ocr" not in item.metadata

    def test_ocr_empty_success_is_ok_without_ocr_key(self, monkeypatch):
        monkeypatch.setattr(collect, "run_ocr", lambda path, engine: [])
        item = Item()
        item.metadata["image"] = "https://example.com/plain.png"
        client = serve({"/plain.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "ok"  # 图内确实无字:环成功,零 OCR 产物
        assert "image_ocr" not in item.metadata

    def test_happy_path_ocr_text_lands_in_metadata(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "ok"
        assert item.metadata["image_ocr"] == "第一行\n第二行"

    def test_disabled_or_absent_urls_zero_entry(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(enabled=False), vision_cfg=VisionConfig(),
        ))
        assert status is None
        assert item.metadata == {}
        item2 = Item()
        item2.metadata["image"] = "https://example.com/pic.png"
        status2 = run(collect.process_item_images(
            item2, images_cfg=self.cfg(enabled=False), vision_cfg=VisionConfig(),
        ))
        assert status2 is None
        assert "image_status" not in item2.metadata


# ---------------------------------------------------------------------------
# 限额双闸:max_images(每条静默截断)/ max_per_run(每 run 硬闸)
# ---------------------------------------------------------------------------


class TestLimits:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, **overrides})

    def test_max_images_truncates_per_item(self, monkeypatch):
        fake_ocr(monkeypatch)
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            return httpx.Response(200, content=png_bytes())

        item = Item()
        item.metadata["images"] = [f"https://example.com/p{i}.png" for i in range(5)]
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(max_images=2), vision_cfg=VisionConfig(),
            client=make_client(handler),
        ))
        assert status == "ok"
        assert hits == ["/p0.png", "/p1.png"], "超出 max_images 的图必须静默截断"

    def test_max_per_run_throttles_following_items(self, monkeypatch):
        fake_ocr(monkeypatch)
        client = serve({f"/p{i}.png": png_bytes() for i in range(2)})
        state = collect.ImageRunState(remaining=1)
        first, second = Item(), Item()
        first.metadata["image"] = "https://example.com/p0.png"
        second.metadata["image"] = "https://example.com/p1.png"
        assert run(collect.process_item_images(
            first, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client, run_state=state,
        )) == "ok"
        assert run(collect.process_item_images(
            second, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client, run_state=state,
        )) == "skipped:run_limit"
        assert second.metadata["image_status"] == "skipped:run_limit"
        assert "image_ocr" not in second.metadata
        assert state.throttled_items == 1

    def test_partial_run_quota_processes_prefix(self, monkeypatch):
        """配额 2、条目要 3 张:处理前 2 张,第 3 张留待,状态仍是 ok。"""
        fake_ocr(monkeypatch)
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            return httpx.Response(200, content=png_bytes())

        item = Item()
        item.metadata["images"] = [f"https://example.com/p{i}.png" for i in range(3)]
        state = collect.ImageRunState(remaining=2)
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(max_images=10), vision_cfg=VisionConfig(),
            client=make_client(handler), run_state=state,
        ))
        assert status == "ok"
        assert hits == ["/p0.png", "/p1.png"]
        assert state.remaining == 0


# ---------------------------------------------------------------------------
# VL:预算耗尽 / 通道死 / 成功 spend(拍板③ + AC3)
# ---------------------------------------------------------------------------


class TestVlPaths:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, "vl": "local", **overrides})

    def vision(self) -> VisionConfig:
        return VisionConfig(local_model="/tmp/qwen3vl_mlx")

    def install_vl(self, monkeypatch, fake: FakeVlClient) -> FakeVlClient:
        monkeypatch.setattr(collect, "VisionClient", lambda *args, **kwargs: fake)
        return fake

    def test_vl_budget_exhausted_keeps_ocr_only(self, monkeypatch):
        fake_ocr(monkeypatch)
        vl = self.install_vl(monkeypatch, FakeVlClient())
        budget = BudgetTracker(limit=100)
        budget.spend(100)  # 预算耗尽
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=self.vision(), budget=budget, client=client,
        ))
        assert status == "vl_skipped_budget"
        assert item.metadata["image_ocr"] == "第一行\n第二行"
        assert "image_caption" not in item.metadata
        assert vl.calls == [], "预算不足时 VL 一次都不该发"

    def test_vl_without_budget_pool_marks_budget_skip(self, monkeypatch):
        fake_ocr(monkeypatch)
        self.install_vl(monkeypatch, FakeVlClient())
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=self.vision(), budget=None, client=client,
        ))
        assert status == "vl_skipped_budget"

    def test_vl_error_degrades_no_retry(self, monkeypatch):
        fake_ocr(monkeypatch)
        vl = self.install_vl(monkeypatch, FakeVlClient(error=TimeoutError("45s 超时")))
        budget = BudgetTracker(limit=1000)
        item = Item()
        item.metadata["images"] = ["https://example.com/a.png", "https://example.com/b.png"]
        client = serve({"/a.png": png_bytes(), "/b.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=self.vision(), budget=budget, client=client,
        ))
        assert status == "vl_skipped_error"
        assert item.metadata["image_ocr"]
        assert len(vl.calls) == 1, "VL 失败不重试"

    def test_vl_local_channel_unconfigured_marks_error(self, monkeypatch):
        fake_ocr(monkeypatch)
        self.install_vl(monkeypatch, FakeVlClient())
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(),  # local_model 空
            budget=BudgetTracker(limit=1000), client=client,
        ))
        assert status == "vl_skipped_error"

    def test_vl_success_spends_tokens_and_captions(self, monkeypatch):
        fake_ocr(monkeypatch)
        vl = self.install_vl(monkeypatch, FakeVlClient())
        budget = BudgetTracker(limit=1000)
        item = Item(title="开源模型刷新榜单")
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=self.vision(), budget=budget, client=client,
        ))
        assert status == "ok"
        assert item.metadata["image_caption"] == "图析描述"
        assert budget.used == 120, "VL total_tokens 必须实际计入共享预算池(AC3)"
        assert "开源模型刷新榜单" in vl.calls[0]["prompt"], "情报向模板必须带条目标题(拍板⑧)"
        assert "条目标题" in vl.calls[0]["prompt"]

    def test_vl_budget_runs_out_mid_item_stops_cleanly(self, monkeypatch):
        """两图、预算只够一张:第一张 spend 后第二张不再发,标记 budget。"""
        fake_ocr(monkeypatch)
        budget = BudgetTracker(limit=120)  # 恰好一张的用量
        self.install_vl(monkeypatch, FakeVlClient(results=[VisionResult(text="一", total_tokens=120)]))
        item = Item()
        item.metadata["images"] = ["https://example.com/a.png", "https://example.com/b.png"]
        client = serve({"/a.png": png_bytes(), "/b.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=self.vision(), budget=budget, client=client,
        ))
        assert status == "vl_skipped_budget"
        assert budget.used == 120
        assert item.metadata.get("image_caption") == "一", "首图 caption 必须保留"


class TestVlCloudChannel:
    """vl: cloud 凭据解析序(显式 image key 优先 → 回落既有 GLM 链路)。

    解析器本体 monkeypatch 在 ``collect.resolve_cloud_api_key`` 接缝(真实
    钥匙串零触碰;解析序自身的单测在 ``tests/test_vision.py``
    ``TestResolveCloudApiKey``,注入 InMemoryKeychainBackend)。
    """

    def install_vl_capturing(
        self, monkeypatch, fake: FakeVlClient
    ) -> tuple[dict[str, Any], FakeVlClient]:
        """替身 VisionClient 并捕获构造入参(断言 api_key/base_url/model 通路)。"""
        captured: dict[str, Any] = {}

        def factory(*args: Any, **kwargs: Any) -> FakeVlClient:
            captured["args"] = args
            captured.update(kwargs)
            return fake

        monkeypatch.setattr(collect, "VisionClient", factory)
        return captured, fake

    def test_cloud_falls_back_to_llm_credential_chain(self, monkeypatch):
        """未录专用 image key:回落 myia/llm/api_key 后云端 VL 照常出 caption。"""
        fake_ocr(monkeypatch)
        monkeypatch.setattr(collect, "resolve_cloud_api_key", lambda cfg: "sk-llm-fallback")
        captured, vl = self.install_vl_capturing(monkeypatch, FakeVlClient())
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=ImagesConfig(enabled=True, min_bytes=1, vl="cloud"),
            vision_cfg=VisionConfig(),  # 无显式 cloud.api_key 引用
            budget=BudgetTracker(limit=1000), client=client,
        ))
        assert status == "ok"
        assert item.metadata["image_caption"] == "图析描述"
        assert captured["args"][0] == VisionConfig().cloud_base_url
        assert captured["args"][1] == "glm-4.6v", "云端缺省模型必须 glm-4.6v"
        assert captured["api_key"] == "sk-llm-fallback"
        assert len(vl.calls) == 1

    def test_cloud_no_credentials_any_chain_degrades(self, monkeypatch):
        fake_ocr(monkeypatch)
        monkeypatch.setattr(collect, "resolve_cloud_api_key", lambda cfg: None)
        _, vl = self.install_vl_capturing(monkeypatch, FakeVlClient())
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=ImagesConfig(enabled=True, min_bytes=1, vl="cloud"),
            vision_cfg=VisionConfig(), budget=BudgetTracker(limit=1000), client=client,
        ))
        assert status == "vl_skipped_error"
        assert item.metadata["image_ocr"], "OCR 产物必须保留"
        assert vl.calls == [], "无凭据时 VL 一次都不该发"

    def test_cloud_explicit_ref_resolve_failure_degrades(self, monkeypatch):
        """显式引用解析失败(配置坏了)→ 降级只 OCR,不静默回落、不阻管线。"""
        from myia.schema import CredentialResolveError

        fake_ocr(monkeypatch)

        def _broken(cfg: VisionConfig) -> str:
            raise CredentialResolveError("secret_not_found", "钥匙串项被删")

        monkeypatch.setattr(collect, "resolve_cloud_api_key", _broken)
        _, vl = self.install_vl_capturing(monkeypatch, FakeVlClient())
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=ImagesConfig(enabled=True, min_bytes=1, vl="cloud"),
            vision_cfg=VisionConfig(), budget=BudgetTracker(limit=1000), client=client,
        ))
        assert status == "vl_skipped_error"
        assert vl.calls == []


# ---------------------------------------------------------------------------
# 源级平铺覆写(images_*,extra="allow" 通道)
# ---------------------------------------------------------------------------


class TestSourceOverrides:
    base = {"enabled": True, "min_bytes": 1}

    def test_images_enabled_false_disables_source(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=ImagesConfig(**self.base), vision_cfg=VisionConfig(),
            client=client, source_extra={"images_enabled": False},
        ))
        assert status is None
        assert item.metadata == {"image": "https://example.com/pic.png"}

    def test_images_max_images_override_applies(self, monkeypatch):
        fake_ocr(monkeypatch)
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            return httpx.Response(200, content=png_bytes())

        item = Item()
        item.metadata["images"] = [f"https://example.com/p{i}.png" for i in range(3)]
        run(collect.process_item_images(
            item, images_cfg=ImagesConfig(**self.base), vision_cfg=VisionConfig(),
            client=make_client(handler), source_extra={"images_max_images": 1},
        ))
        assert hits == ["/p0.png"]

    def test_invalid_override_values_ignored_with_category_fallback(self, monkeypatch, caplog):
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=ImagesConfig(**self.base), vision_cfg=VisionConfig(),
            client=client,
            source_extra={"images_max_images": "两", "images_vl": 3, "images_enabled": "yes"},
        ))
        assert status == "ok", "非法覆写值忽略回退品类节,不阻管线"


# ---------------------------------------------------------------------------
# 管线挂点端到端(MockTransport 全链;零外网)
# ---------------------------------------------------------------------------


CATEGORY_WITH_IMAGES = """
id: demo-images
name: 演示
schedule: "0 9 * * *"
sources:
  - name: pics
    engine: static_html
    url: "https://example.com/list"
    extract:
      type: list
      item: "article"
      fields:
        title: "h3"
        url: "h3 a@href"
        image: "img@src"
classify:
  builtin: false
  rules: []
images:
  enabled: true
  min_bytes: 1
"""

CATEGORY_WITHOUT_IMAGES = CATEGORY_WITH_IMAGES.split("images:")[0].rstrip() + "\n"


class TestPipelineHook:
    def _client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/robots.txt"):
                return httpx.Response(404, text="")
            if request.url.path == "/list":
                return httpx.Response(200, text=(
                    '<article><h3><a href="/items/1">配图条目</a></h3>'
                    '<img src="/assets/pic.png"></article>'
                ))
            if request.url.path == "/assets/pic.png":
                return httpx.Response(200, content=png_bytes())
            return httpx.Response(404, text="")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    def test_fetch_tail_ring_products_reach_item_metadata(self, monkeypatch, tmp_path):
        from myia.pipeline import Pipeline
        from myia.store import SQLiteStore

        fake_ocr(monkeypatch)
        client = self._client()
        config = load_category(_yaml_to_dict(CATEGORY_WITH_IMAGES))
        store = SQLiteStore(tmp_path / "ring.db")
        pipeline = Pipeline(config, store=store, client=client)
        result = asyncio.run(pipeline.run())
        store.close()
        asyncio.run(client.aclose())
        assert result.status == "success", result.stats_dict()
        items = result.items
        assert items, "条目必须走完全链"
        assert items[0].metadata.get("image") == "https://example.com/assets/pic.png"
        assert items[0].metadata.get("image_ocr") == "第一行\n第二行"
        assert items[0].metadata.get("image_status") == "ok"

    def test_category_without_images_section_is_zero_impact(self, monkeypatch, tmp_path):
        from myia.pipeline import Pipeline
        from myia.store import SQLiteStore

        fake_ocr(monkeypatch)  # 若环误进,OCR 桩会写 image_ocr —— 断言它没有
        client = self._client()
        config = load_category(_yaml_to_dict(CATEGORY_WITHOUT_IMAGES))
        store = SQLiteStore(tmp_path / "bare.db")
        pipeline = Pipeline(config, store=store, client=client)
        result = asyncio.run(pipeline.run())
        store.close()
        asyncio.run(client.aclose())
        assert result.status == "success", result.stats_dict()
        assert result.items[0].metadata.get("image") == "https://example.com/assets/pic.png"
        assert "image_ocr" not in result.items[0].metadata
        assert "image_status" not in result.items[0].metadata


def _yaml_to_dict(text: str) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data
