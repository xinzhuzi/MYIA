"""vision/collect 图片处理环单测(task 10-03-vision-pipeline)。

全量 mock、零外网:下载走 ``httpx.MockTransport``,DNS 解析 monkeypatch
``collect._resolve_host``(SSRF 判定不真解析),OCR monkeypatch
``collect.run_ocr``,VL 通道 monkeypatch ``collect.VisionClient``——与本仓
``tests/test_vision.py`` 的假引擎注入同款纪律。

覆盖面 = PRD 降级矩阵逐行 + 限额双闸(max_images/max_per_run)+ SSRF +
预算耗尽 + 管线挂点端到端(MockTransport 全链,断言 fetch 尾部产物挂
metadata、未开 images 的品类零影响)。

10-03-detail-images / 10-03-image-fix-followups 增补:详情页追抓环
(有图/无图/超时/404/配额/同域/串行间隔/源级覆写)、``_html_image_urls``
语义、SSRF 连接层 rebinding 复核、注入 client 强制 follow_redirects=False、
pool 源 proxy_url 传抵(环级 + 管线级)、detail 链 E2E(fixture 本地页)。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

import shishi.vision.collect as collect
from shishi.enrich.scoring import BudgetTracker
from shishi.schema import ImagesConfig, load_category
from shishi.vision.client import VisionResult
from shishi.vision.ocr import OcrLine
from shishi.vision.settings import VisionConfig

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
        from shishi.vision.ocr import OCRError

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
        from shishi.schema import CredentialResolveError

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
        from shishi.pipeline import Pipeline
        from shishi.store import SQLiteStore

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
        from shishi.pipeline import Pipeline
        from shishi.store import SQLiteStore

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


# ---------------------------------------------------------------------------
# _html_image_urls:HTML 同域收集(10-03-detail-images)
# ---------------------------------------------------------------------------


class TestHtmlImageUrls:
    def test_same_domain_relative_and_absolute_dedup(self):
        html = (
            '<img src="/a.png">'
            '<IMG class="x" SRC = "https://example.com/b.jpg">'
            '<img src="https://cdn.example/adv.jpg">'
            '<img src="">'
            '<img src="//evil.example/c.png">'
            '<img srcset="/d.png 1x" src="/a.png">'
            "<img>"
        )
        assert collect._html_image_urls(html, "https://example.com/t/1") == [
            "https://example.com/a.png",
            "https://example.com/b.jpg",
        ]

    def test_data_src_variants_not_matched(self):
        """懒加载 data-src/data-srcset 深挖刻意不做:不得误收为 src。"""
        html = '<img data-src="/lazy.png" data-srcset="/l2.png 1x" alt="无 src">'
        assert collect._html_image_urls(html, "https://example.com/t/1") == []

    def test_protocol_relative_cross_host_filtered_same_host_kept(self):
        html = '<img src="//example.com/p.png">'
        assert collect._html_image_urls(html, "https://example.com/t/1") == [
            "https://example.com/p.png"
        ]


# ---------------------------------------------------------------------------
# 详情页追抓环(10-03-detail-images;全 mock 零外网)
# ---------------------------------------------------------------------------


class TestDetailFetch:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, "detail_fetch": True, **overrides})

    @pytest.fixture(autouse=True)
    def _fast_interval(self, monkeypatch: pytest.MonkeyPatch):
        """串行 ≥1s 间隔测试归零加速(间隔行为另测)。"""
        monkeypatch.setattr(collect, "DETAIL_FETCH_INTERVAL_SECONDS", 0.0)

    def test_disabled_zero_entry_no_writes(self, monkeypatch):
        item = Item(url="https://example.com/t/1")
        client = serve({"/t/1": "<html></html>"})
        assert run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(detail_fetch=False), client=client,
        )) is None
        assert item.metadata == {}

    def test_item_with_images_not_refetched(self, monkeypatch):
        """列表页已带图的条目不追抓(候选 URL 判定,含 image 单值形态)。"""
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            return httpx.Response(200, text="<html></html>")

        item = Item(url="https://example.com/t/1")
        item.metadata["image"] = "https://example.com/pic.png"
        assert run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=make_client(handler),
        )) is None
        assert item.metadata == {"image": "https://example.com/pic.png"}
        assert hits == []

    def test_happy_collects_same_domain_and_feeds_ring(self, monkeypatch):
        fake_ocr(monkeypatch)
        item = Item(url="https://example.com/t/1")
        client = serve({
            "/t/1": (
                '<div><img src="/assets/a.png"><img src="/assets/b.png">'
                '<img src="https://cdn.example/x.jpg"></div>'
            ),
            "/assets/a.png": png_bytes(),
            "/assets/b.png": png_bytes(),
        })
        state = collect.ImageRunState(remaining=100, detail_remaining=10)
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=client, run_state=state,
        ))
        assert status == "ok:n=2"
        assert item.metadata["images"] == [
            "https://example.com/assets/a.png",
            "https://example.com/assets/b.png",
        ]
        assert item.metadata["detail_status"] == "ok:n=2"
        assert state.detail_fetches == 1 and state.detail_remaining == 9
        # 写回的 images 随即被同一 run_state 下的识图环消费(min_bytes 交接)
        ring_status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(),
            client=client, run_state=state,
        ))
        assert ring_status == "ok"
        assert item.metadata["image_ocr"] == "第一行\n第二行\n第一行\n第二行"

    def test_page_without_usable_images_marks_no_images(self, monkeypatch):
        item = Item(url="https://example.com/t/1")
        client = serve({"/t/1": "<html><p>纯文本 SPA 空页</p></html>"})
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=client,
        ))
        assert status == "no_images"
        assert item.metadata == {"detail_status": "no_images"}

    def test_http_error_marks_failed_not_raises(self, monkeypatch):
        item = Item(url="https://example.com/t/1")
        client = serve({})  # 一切 404
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=client,
        ))
        assert status == "failed:http_404"
        assert item.metadata["detail_status"] == "failed:http_404"

    def test_timeout_marks_failed(self, monkeypatch):
        monkeypatch.setattr(collect, "DETAIL_FETCH_TIMEOUT_SECONDS", 0.05)

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.3)
            return httpx.Response(200, text="<html></html>")

        item = Item(url="https://example.com/t/1")
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=make_client(handler),
        ))
        assert status == "failed:timeout"
        assert item.metadata["detail_status"] == "failed:timeout"

    def test_run_quota_truncates_following_items(self, monkeypatch):
        client = serve({"/t/1": '<img src="/a.png">', "/t/2": '<img src="/b.png">'})
        state = collect.ImageRunState(remaining=100, detail_remaining=1)
        first = Item(url="https://example.com/t/1")
        second = Item(url="https://example.com/t/2")
        assert run(collect.detail_fetch_images(
            first, images_cfg=self.cfg(), client=client, run_state=state,
        )) == "ok:n=1"
        assert run(collect.detail_fetch_images(
            second, images_cfg=self.cfg(), client=client, run_state=state,
        )) is None
        assert second.metadata == {}, "配额截断零标记,条目照常入库"
        assert state.detail_fetches == 1 and state.detail_remaining == 0

    def test_private_item_url_rejected_as_ssrf(self, monkeypatch):
        monkeypatch.setattr(collect, "_resolve_host", lambda host: ["10.0.0.5"])
        item = Item(url="https://internal.example/t/1")
        client = serve({"/t/1": "<html></html>"})
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=client,
        ))
        assert status == "failed:ssrf"
        assert item.metadata["detail_status"] == "failed:ssrf"

    def test_redirect_followed_within_hops_each_rechecked(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/t/1":
                return httpx.Response(301, headers={"Location": "/canonical/1"})
            if request.url.path == "/canonical/1":
                return httpx.Response(200, text='<div><img src="/assets/c.png"></div>')
            return httpx.Response(200, content=png_bytes())

        item = Item(url="https://example.com/t/1")
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=make_client(handler),
        ))
        assert status == "ok:n=1"
        assert item.metadata["images"] == ["https://example.com/assets/c.png"]

    def test_serial_interval_sleeps_before_second_request(self, monkeypatch):
        monkeypatch.setattr(collect, "DETAIL_FETCH_INTERVAL_SECONDS", 0.05)
        client = serve({"/t/1": "<html></html>", "/t/2": "<html></html>"})
        state = collect.ImageRunState(remaining=100, detail_remaining=10)
        first = Item(url="https://example.com/t/1")
        second = Item(url="https://example.com/t/2")
        t0 = time.monotonic()
        run(collect.detail_fetch_images(
            first, images_cfg=self.cfg(), client=client, run_state=state,
        ))
        assert time.monotonic() - t0 < 0.05, "首个请求前不等待"
        run(collect.detail_fetch_images(
            second, images_cfg=self.cfg(), client=client, run_state=state,
        ))
        assert time.monotonic() - t0 >= 0.05, "第二 个请求前必须 sleep ≥ 间隔"

    def test_source_override_enables_detail(self, monkeypatch):
        item = Item(url="https://example.com/t/1")
        client = serve({"/t/1": '<img src="/a.png">'})
        status = run(collect.detail_fetch_images(
            item,
            images_cfg=self.cfg(detail_fetch=False),
            source_extra={"images_detail_fetch": True},
            client=client,
        ))
        assert status == "ok:n=1"

    # -- 10-03-detail-images 复查①:源级 max_items 覆写在共享 run_state 生效 --

    def test_source_max_items_override_is_own_budget_not_shared_pool(self, monkeypatch):
        """源级 ``images_detail_max_items`` 覆写 = 该源独立预算:管线恒传共享
        run_state(其 detail_remaining 只按品类级初始化),覆写源吃自己的池
        (1 条),不吃也不占共享池;无覆写源照旧吃共享池。"""
        client = serve({
            "/t/1": '<img src="/a.png">',
            "/t/2": '<img src="/b.png">',
            "/t/3": '<img src="/c.png">',
        })
        state = collect.ImageRunState(remaining=100, detail_remaining=10)
        extra = {"images_detail_max_items": 1}
        first = Item(url="https://example.com/t/1")
        second = Item(url="https://example.com/t/2")
        assert run(collect.detail_fetch_images(
            first, images_cfg=self.cfg(), source_extra=extra, source_name="ov",
            client=client, run_state=state,
        )) == "ok:n=1"
        assert run(collect.detail_fetch_images(
            second, images_cfg=self.cfg(), source_extra=extra, source_name="ov",
            client=client, run_state=state,
        )) is None, "覆写预算耗尽:零进入零标记,条目照常入库"
        assert second.metadata == {}
        assert state.detail_remaining == 10, "覆写源不得吃共享池"
        assert state.detail_remaining_by_source == {"ov": 0}
        third = Item(url="https://example.com/t/3")
        assert run(collect.detail_fetch_images(
            third, images_cfg=self.cfg(), source_name="plain",
            client=client, run_state=state,
        )) == "ok:n=1", "无覆写源照旧吃共享池(品类级先到先得)"
        assert state.detail_remaining == 9

    def test_source_max_items_override_raises_above_category_cap(self, monkeypatch):
        """覆写值高于品类上限也生效:独立预算 2 > 品类共享池 1,第二条照抓。"""
        client = serve({"/t/1": '<img src="/a.png">', "/t/2": '<img src="/b.png">'})
        state = collect.ImageRunState(remaining=100, detail_remaining=1)
        extra = {"images_detail_max_items": 2}
        cfg = self.cfg(detail_max_items=1)
        first = Item(url="https://example.com/t/1")
        second = Item(url="https://example.com/t/2")
        assert run(collect.detail_fetch_images(
            first, images_cfg=cfg, source_extra=extra, source_name="ov",
            client=client, run_state=state,
        )) == "ok:n=1"
        assert run(collect.detail_fetch_images(
            second, images_cfg=cfg, source_extra=extra, source_name="ov",
            client=client, run_state=state,
        )) == "ok:n=1"
        assert state.detail_remaining == 1, "共享池未被覆写源吃掉"

    def test_out_of_range_override_drops_only_that_key(self, monkeypatch, caplog):
        """复查①连带:越界覆写值(100 > le=50,能过 >0 类型预过滤)只弃本键,
        不连坐整批——同批 ``images_detail_fetch: true`` 必须存活,源级 detail
        不再被静默关闭。"""
        item = Item(url="https://example.com/t/1")
        client = serve({"/t/1": '<img src="/a.png">'})
        with caplog.at_level(logging.WARNING, logger="shishi.vision.collect"):
            status = run(collect.detail_fetch_images(
                item,
                images_cfg=self.cfg(detail_fetch=False),
                source_extra={"images_detail_fetch": True, "images_detail_max_items": 100},
                client=client,
            ))
        assert status == "ok:n=1", "detail_fetch 覆写不得被越界 max_items 连坐弃掉"
        assert "越界" in caplog.text, "弃键必须告警可见"

    # -- 10-03-detail-images 复查②:追抓请求带源级 headers(引擎链装配语义)--

    def test_detail_request_carries_source_headers(self, monkeypatch):
        """详情页追抓以源级请求头出网(源配 UA/登录 Cookie),不再裸 httpx。"""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent")
            seen["cookie"] = request.headers.get("cookie")
            return httpx.Response(200, text='<img src="/a.png">')

        item = Item(url="https://example.com/t/1")
        status = run(collect.detail_fetch_images(
            item, images_cfg=self.cfg(), client=make_client(handler),
            headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/126", "Cookie": "sid=1"},
        ))
        assert status == "ok:n=1"
        assert seen["ua"] == "Mozilla/5.0 (Macintosh) Chrome/126"
        assert seen["cookie"] == "sid=1"

    def test_detail_request_headers_assembly_shapes(self, monkeypatch):
        """装配面:空头补引擎缺省 UA;自定义 UA 原样;env: 凭据引用真解析;
        凭据解析失败裸头降级(只告警不抛)。"""
        from shishi.engines.fetch_base import DEFAULT_USER_AGENT

        assert collect.detail_request_headers(None) == {"User-Agent": DEFAULT_USER_AGENT}
        assert collect.detail_request_headers({"User-Agent": "UA/1"}) == {"User-Agent": "UA/1"}
        monkeypatch.setenv("MYIA_TEST_DETAIL_SID", "s3cret")
        assert collect.detail_request_headers({"Cookie": "env:MYIA_TEST_DETAIL_SID"}) == {
            "Cookie": "s3cret",
            "User-Agent": DEFAULT_USER_AGENT,
        }

        def boom(headers, *, backend=None):  # noqa: ANN001
            raise RuntimeError("凭据拒解")

        monkeypatch.setattr(collect, "resolve_headers", boom)
        assert collect.detail_request_headers({"Cookie": "keychain:myia/x/y"}) == {}


# ---------------------------------------------------------------------------
# SSRF 加固(10-03-image-fix-followups 小修⑤:rebinding 连接层复核 +
# 注入 client 强制 follow_redirects=False)
# ---------------------------------------------------------------------------


class TestSsrfHardening:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, **overrides})

    def test_injected_client_follow_redirects_true_cannot_bypass_hop_check(self, monkeypatch):
        """注入 client 开着 follow_redirects=True 也不能绕过逐跳 SSRF 复核:
        每请求显式 False 强制,私网重定向跳在发请求前就被拒。"""
        resolve_map = {"public.example": ["93.184.216.34"], "internal.example": ["192.168.1.9"]}
        monkeypatch.setattr(collect, "_resolve_host", lambda host: resolve_map[host])
        fake_ocr(monkeypatch)
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
            if request.url.path == "/hop.png":
                return httpx.Response(302, headers={"Location": "https://internal.example/leak.png"})
            return httpx.Response(200, content=png_bytes())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        item = Item()
        item.metadata["image"] = "https://public.example/hop.png"
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        asyncio.run(client.aclose())
        assert status == "none"
        assert requested_hosts == ["public.example"], "私网跳不得被 client 自动跟进"

    def test_connection_layer_rebind_to_private_rejected(self, monkeypatch):
        """DNS rebinding 复核:前置解析公网、实际连上私网(两次解析被切)→ 拒。"""
        fake_ocr(monkeypatch)
        monkeypatch.setattr(collect, "_connected_server_ip", lambda response: "10.9.9.9")
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(), client=client,
        ))
        assert status == "none"

    def test_detail_connection_layer_rebind_rejected(self, monkeypatch):
        monkeypatch.setattr(collect, "_connected_server_ip", lambda response: "10.9.9.9")
        item = Item(url="https://example.com/t/1")
        client = serve({"/t/1": '<img src="/a.png">'})
        status = run(collect.detail_fetch_images(
            item, images_cfg=ImagesConfig(enabled=True, min_bytes=1, detail_fetch=True),
            client=client,
        ))
        assert status == "failed:ssrf_rebind"

    def test_connected_server_ip_shapes(self):
        """extensions 无 network_stream(MockTransport)→ None;两种底层形态取 IP。"""

        class _ExtraStream:
            def get_extra_info(self, info: str):
                assert info == "server_addr"
                return ("203.0.113.9", 443)

        class _ConnInfo:
            server_addr = ("203.0.113.10", 443)

        class _ConnInfoStream:
            def get_conn_info(self):
                return _ConnInfo()

        assert collect._connected_server_ip(httpx.Response(200)) is None
        response_extra = httpx.Response(200, extensions={"network_stream": _ExtraStream()})
        assert collect._connected_server_ip(response_extra) == "203.0.113.9"
        response_conn = httpx.Response(200, extensions={"network_stream": _ConnInfoStream()})
        assert collect._connected_server_ip(response_conn) == "203.0.113.10"


# ---------------------------------------------------------------------------
# proxy 传抵(10-03-image-fix-followups 小修④:pool 源配图不再直连)
# ---------------------------------------------------------------------------


class TestProxyPassthrough:
    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, **overrides})

    def test_own_download_client_mounts_proxy_url(self, monkeypatch):
        """client 未注入时,proxy_url 必须真的挂上自建下载 client。"""
        fake_ocr(monkeypatch)
        recorded: dict[str, Any] = {}
        # 先建好 mock client 再打桩:工厂替换的是全局 httpx.AsyncClient,
        # serve() 内部也要走它,现造必递归。
        mock_client = serve({"/pic.png": png_bytes()})

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            recorded.update(kwargs)
            return mock_client

        monkeypatch.setattr(collect.httpx, "AsyncClient", factory)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(),
            proxy_url="http://proxy.example:8080",
        ))
        assert status == "ok"
        assert recorded["proxy"] == "http://proxy.example:8080"
        assert recorded["follow_redirects"] is False

    CATEGORY_POOL_SOURCE = """
id: demo-pool
name: 池演示
schedule: "0 9 * * *"
sources:
  - name: pooled
    engine: static_html
    url: "https://example.com/list"
    proxy: "pool:main"
    extract:
      type: list
      item: "article"
      fields:
        title: "h3"
        url: "h3 a@href"
  - name: directsrc
    engine: static_html
    url: "https://example.com/other"
    extract:
      type: list
      item: "article"
      fields:
        title: "h3"
        url: "h3 a@href"
classify:
  builtin: false
  rules: []
images:
  enabled: true
  min_bytes: 1
"""

    def test_pool_source_ring_and_detail_ride_pool_client(self, monkeypatch, tmp_path):
        """管线环级:pool 源条目的 detail 追抓与识图环都骑该池共享代理 client,
        proxy_url 传抵 collect 层;direct 源条目走环的共享 client、零 proxy。"""
        import shishi.pipeline as pipeline_module
        from shishi.engines.fetch_base import DEFAULT_USER_AGENT, ProxyPools
        from shishi.pipeline import Item, Pipeline
        from shishi.store import SQLiteStore

        config = load_category(_yaml_to_dict(self.CATEGORY_POOL_SOURCE))
        store = SQLiteStore(tmp_path / "pool.db")
        calls: list[dict[str, Any]] = []

        async def fake_ring(item, **kwargs: Any) -> None:
            calls.append({"stage": "ring", "url": item.url, **kwargs})

        async def fake_detail(item, **kwargs: Any) -> None:
            calls.append({"stage": "detail", "url": item.url, **kwargs})

        monkeypatch.setattr(pipeline_module, "process_item_images", fake_ring)
        monkeypatch.setattr(pipeline_module, "detail_fetch_images", fake_detail)

        mock_client = make_client(lambda request: httpx.Response(404))
        pipeline = Pipeline(
            config, store=store, client=mock_client,
            proxy_pools=ProxyPools({"main": "http://proxy.example:8080"}),
        )
        context = pipeline._context_for_run(mock_client, store)
        items = [
            Item.from_extracted({"url": "https://example.com/t/1", "title": "池源"}, "pooled"),
            Item.from_extracted({"url": "https://example.com/t/2", "title": "直连"}, "directsrc"),
        ]
        try:
            asyncio.run(pipeline._process_item_images_ring(items, context))
            pool_client = context.pool_clients.get("main")
        finally:
            store.close()
            asyncio.run(context.aclose_pool_clients())

        pooled = [c for c in calls if c["url"].endswith("/t/1")]
        direct = [c for c in calls if c["url"].endswith("/t/2")]
        assert {c["stage"] for c in calls} == {"detail", "ring"}, "detail 环在识图环之前逐条先行"
        assert pooled and all(c["proxy_url"] == "http://proxy.example:8080" for c in pooled)
        assert all(c["client"] is pool_client for c in pooled)
        assert direct and all(c["proxy_url"] is None for c in direct)
        assert all(c["client"] is mock_client for c in direct)
        # 复查①/②环级传参:source_name(源级独立预算键)+ 源级请求头
        # (引擎链同款装配;两源未配 headers → 补缺省 UA)——只断言 detail
        # 阶段调用(识图环 process_item_images 无此二参,契约面不同)。
        detail_calls = [c for c in calls if c["stage"] == "detail"]
        assert detail_calls
        assert all(c["source_name"] in ("pooled", "directsrc") for c in detail_calls)
        assert all(c["headers"] == {"User-Agent": DEFAULT_USER_AGENT} for c in detail_calls), (
            "环必须给追抓传引擎链同款装配的源级请求头"
        )

    # -- 经代理出网跳过连接层复核(10-03 复查:proxy×rebind 组合此前零覆盖)--

    @staticmethod
    def _raw_response(payload: bytes, content_type: str) -> bytes:
        return (
            f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii") + payload

    @classmethod
    async def _via_local_proxy(cls, payload: bytes, content_type: str, request):
        """本地真代理 e2e(零外网):127.0.0.1 起假代理回固定响应,client 显式
        挂它发请求。经代理时 network_stream.server_addr = 代理 127.0.0.1(非
        目标站 IP),连接层复核若仍执行必判 ssrf_rebind——本组测试钉死它跳过。"""

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                pass  # 请求头没读完也照回:假代理不解析,只演示「代理在中间」
            writer.write(cls._raw_response(payload, content_type))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with httpx.AsyncClient(
                proxy=f"http://127.0.0.1:{port}", trust_env=False
            ) as client:
                return await request(client)
        finally:
            server.close()
            await server.wait_closed()

    def test_client_egress_is_proxied_shapes(self):
        """判别面:显式 http/socks 代理 → True;trust_env=False 直连与
        MockTransport 注入(无 _pool)→ False(测试注入 client 永不误判)。"""
        clients = [
            httpx.AsyncClient(proxy="http://127.0.0.1:8766", trust_env=False),
            httpx.AsyncClient(proxy="socks5://127.0.0.1:1080", trust_env=False),
            httpx.AsyncClient(trust_env=False),
            serve({"/x": b"x"}),
        ]
        try:
            assert collect._client_egress_is_proxied(clients[0])
            assert collect._client_egress_is_proxied(clients[1])
            assert not collect._client_egress_is_proxied(clients[2])
            assert not collect._client_egress_is_proxied(clients[3])
        finally:
            for client in clients:
                asyncio.run(client.aclose())

    def test_download_via_local_proxy_egress_not_killed_as_rebind(self, tmp_path):
        """经代理下载不判 ssrf_rebind:复核跳过后图片照过全关(PNG 魔法字节)。
        修复前该路径静默全灭(pool 上游/环自建 client 吃系统代理均此形态)。"""

        async def request(client: httpx.AsyncClient):
            return await collect._download_image(
                client, "http://example.com/pic.png", tmp_path, min_bytes=1
            )

        outcome = run(self._via_local_proxy(png_bytes(), "image/png", request))
        assert isinstance(outcome, collect._Downloaded), f"经代理不得判 ssrf_rebind: {outcome}"
        assert outcome.path.read_bytes()[:8] == PNG_HEAD

    def test_detail_via_local_proxy_egress_not_killed_as_rebind(self):
        """经代理的详情页追抓不判 ssrf_rebind:照常收同域图写 metadata。"""
        item = Item(url="http://example.com/t/1")

        async def request(client: httpx.AsyncClient):
            return await collect.detail_fetch_images(
                item,
                images_cfg=ImagesConfig(enabled=True, min_bytes=1, detail_fetch=True),
                client=client,
            )

        status = run(
            self._via_local_proxy(
                b"<html><body><img src='http://example.com/a.png'></body></html>",
                "text/html",
                request,
            )
        )
        assert status == "ok:n=1", f"经代理详情追抓不得判 ssrf_rebind: {status}"
        assert item.metadata["images"] == ["http://example.com/a.png"]


# ---------------------------------------------------------------------------
# detail 链管线 E2E(fixture 本地页:列表无图 + 详情页一好一 404 + 配额截断)
# ---------------------------------------------------------------------------


CATEGORY_WITH_DETAIL = """
id: demo-detail
name: 演示
schedule: "0 9 * * *"
sources:
  - name: textlist
    engine: static_html
    url: "https://example.com/list"
    extract:
      type: list
      item: "article"
      fields:
        title: "h3"
        url: "h3 a@href"
classify:
  builtin: false
  rules: []
images:
  enabled: true
  min_bytes: 1
  detail_fetch: true
  detail_max_items: 2
"""


class TestPipelineDetailHook:
    def _client(self, hits: list[str]) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(request.url.path)
            if request.url.path.endswith("/robots.txt"):
                return httpx.Response(404, text="")
            if request.url.path == "/list":
                return httpx.Response(200, text=(
                    '<article><h3><a href="/items/1">条目一</a></h3></article>'
                    '<article><h3><a href="/items/2">条目二</a></h3></article>'
                    '<article><h3><a href="/items/3">条目三</a></h3></article>'
                ))
            if request.url.path == "/items/1":
                return httpx.Response(200, text=(
                    '<div><img src="/assets/a.png"><img src="/assets/b.png">'
                    '<img src="https://cdn.example/x.jpg"></div>'
                ))
            if request.url.path in ("/assets/a.png", "/assets/b.png"):
                return httpx.Response(200, content=png_bytes())
            return httpx.Response(404, text="missing")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    def test_detail_chain_end_to_end(self, monkeypatch, tmp_path):
        """AC2/AC3 fixture 版:无图列表 → 条目一经 detail 链出 image_ocr;
        失败页 detail_status 落标记且条目照常入库;配额截断第三条零进入。"""
        from shishi.pipeline import Pipeline
        from shishi.store import SQLiteStore

        fake_ocr(monkeypatch)
        monkeypatch.setattr(collect, "DETAIL_FETCH_INTERVAL_SECONDS", 0.0)
        hits: list[str] = []
        client = self._client(hits)
        config = load_category(_yaml_to_dict(CATEGORY_WITH_DETAIL))
        store = SQLiteStore(tmp_path / "detail.db")
        pipeline = Pipeline(config, store=store, client=client)
        result = asyncio.run(pipeline.run())
        store.close()
        asyncio.run(client.aclose())
        assert result.status == "success", result.stats_dict()
        items = result.items
        assert len(items) == 3

        first, second, third = items
        # 条目一:详情页收 2 张同域图 → 同一识图环出 OCR 产物
        assert first.metadata["detail_status"] == "ok:n=2"
        assert first.metadata["images"] == [
            "https://example.com/assets/a.png",
            "https://example.com/assets/b.png",
        ]
        assert first.metadata["image_ocr"] == "第一行\n第二行\n第一行\n第二行"
        assert first.metadata["image_status"] == "ok"
        # 条目二:404 → failed 标记,条目照常入库,识图环零进入
        assert second.metadata["detail_status"] == "failed:http_404"
        assert "images" not in second.metadata
        assert "image_status" not in second.metadata
        # 条目三:detail_max_items=2 截断 → 零进入零标记,详情页从未被抓
        assert "detail_status" not in third.metadata
        assert "/items/3" not in hits, "配额截断的详情页不得发起请求"

    def test_detail_fetch_rides_source_headers(self, monkeypatch, tmp_path):
        """复查②管线级:源 ``headers`` 配的 UA 随 detail 追抓请求出网(引擎链
        同一装配语义),不再是裸 httpx 默认 UA;列表抓取同 UA(既有引擎语义),
        图片下载不在本契约面(不注入源头)。"""
        from shishi.pipeline import Pipeline
        from shishi.store import SQLiteStore

        fake_ocr(monkeypatch)
        monkeypatch.setattr(collect, "DETAIL_FETCH_INTERVAL_SECONDS", 0.0)
        category = _yaml_to_dict(CATEGORY_WITH_DETAIL.replace(
            '    url: "https://example.com/list"\n',
            '    url: "https://example.com/list"\n'
            '    headers:\n'
            '      User-Agent: "TestUA/1 (detail)"\n',
        ))
        uas: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            uas[request.url.path] = request.headers.get("user-agent", "")
            if request.url.path.endswith("/robots.txt"):
                return httpx.Response(404, text="")
            if request.url.path == "/list":
                return httpx.Response(200, text=(
                    '<article><h3><a href="/items/1">条目一</a></h3></article>'
                ))
            if request.url.path == "/items/1":
                return httpx.Response(200, text='<div><img src="/assets/a.png"></div>')
            if request.url.path == "/assets/a.png":
                return httpx.Response(200, content=png_bytes())
            return httpx.Response(404, text="")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        store = SQLiteStore(tmp_path / "detail-headers.db")
        pipeline = Pipeline(load_category(category), store=store, client=client)
        result = asyncio.run(pipeline.run())
        store.close()
        asyncio.run(client.aclose())
        assert result.status == "success", result.stats_dict()
        assert result.items[0].metadata.get("detail_status") == "ok:n=1"
        assert uas.get("/list") == "TestUA/1 (detail)", "列表抓取带源 UA(既有引擎语义)"
        assert uas.get("/items/1") == "TestUA/1 (detail)", "detail 追抓必须带源 UA 出网"
        assert uas.get("/assets/a.png") != "TestUA/1 (detail)", "图片下载不在本契约面"


# ---------------------------------------------------------------------------
# 图片落库(10-03-vision-v2:images.persist 开 → image_files / image_ocr_lines)
# ---------------------------------------------------------------------------


class TestPersistImages:
    """persist 缺省 false = 行为与今天逐字段一致;开启才落盘/写新键。"""

    def cfg(self, **overrides: Any) -> ImagesConfig:
        return ImagesConfig(**{"enabled": True, "min_bytes": 1, **overrides})

    def test_persist_off_keeps_today_behavior(self, monkeypatch, tmp_path):
        """缺省关:零落盘、零新 metadata 键(与落图能力引入前逐字段一致)。"""
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        images_dir = tmp_path / "images"
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(), vision_cfg=VisionConfig(),
            client=client, images_dir=images_dir,
        ))
        assert status == "ok"
        assert item.metadata["image_ocr"] == "第一行\n第二行"
        assert "image_files" not in item.metadata
        assert "image_ocr_lines" not in item.metadata
        assert not images_dir.exists()  # 零磁盘副作用

    def test_persist_on_writes_files_and_ocr_lines(self, monkeypatch, tmp_path):
        """开启:内容寻址落盘 + image_files 绝对路径 + 逐行置信度投影。"""
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        images_dir = tmp_path / "data" / "images"
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(persist=True), vision_cfg=VisionConfig(),
            client=client, images_dir=images_dir,
        ))
        assert status == "ok"
        files = item.metadata["image_files"]
        assert isinstance(files, list) and len(files) == 1
        saved = Path(files[0])
        assert saved.is_absolute() and saved.exists()
        assert saved.parent == images_dir.resolve()
        assert saved.suffix == ".png"
        assert saved.name == hashlib.sha256(png_bytes()).hexdigest()[:16] + ".png"
        assert saved.read_bytes() == png_bytes()
        # 逐行置信度:与 image_ocr 同源,只是不拼行
        assert item.metadata["image_ocr_lines"] == [
            {"text": "第一行", "conf": 0.99}, {"text": "第二行", "conf": 0.99},
        ]
        assert item.metadata["image_ocr"] == "第一行\n第二行"

    def test_persist_content_addressed_dedup_across_items(self, monkeypatch, tmp_path):
        """同图跨条目:同 sha16 文件只落一份(内容寻址去重)。"""
        fake_ocr(monkeypatch)
        images_dir = tmp_path / "images"
        client = serve({"/pic.png": png_bytes()})
        paths = []
        for _ in range(2):
            item = Item()
            item.metadata["image"] = "https://example.com/pic.png"
            run(collect.process_item_images(
                item, images_cfg=self.cfg(persist=True), vision_cfg=VisionConfig(),
                client=client, images_dir=images_dir,
            ))
            paths.extend(item.metadata["image_files"])
        assert paths[0] == paths[1]
        assert len(list(images_dir.iterdir())) == 1

    def test_persist_all_downloads_fail_no_files_key(self, monkeypatch, tmp_path):
        """下载全灭:走既有 none 降级,零 image_files 写入。"""
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": httpx.Response(404, text="gone")})
        status = run(collect.process_item_images(
            item, images_cfg=self.cfg(persist=True), vision_cfg=VisionConfig(),
            client=client, images_dir=tmp_path / "images",
        ))
        assert status == "none"
        assert "image_files" not in item.metadata
        assert "image_ocr_lines" not in item.metadata

    def test_persist_without_dir_warns_and_skips(self, monkeypatch, tmp_path, caplog):
        """persist 开但调用方未给目录:告警跳过落盘,环照常成功不阻管线。"""
        fake_ocr(monkeypatch)
        item = Item()
        item.metadata["image"] = "https://example.com/pic.png"
        client = serve({"/pic.png": png_bytes()})
        with caplog.at_level(logging.WARNING, logger="shishi.vision.collect"):
            status = run(collect.process_item_images(
                item, images_cfg=self.cfg(persist=True), vision_cfg=VisionConfig(),
                client=client, images_dir=None,
            ))
        assert status == "ok"
        assert "image_files" not in item.metadata  # 落盘跳过,键不写半吊子
        assert "image_ocr_lines" in item.metadata  # OCR 照常(键属环产物,不受磁盘影响)
        assert any("落图目录" in record.message for record in caplog.records)

    def test_schema_persist_default_false_and_loadable(self):
        """schema 同门:persist 缺省 false;显式 true 合法构造。"""
        assert ImagesConfig().persist is False
        assert ImagesConfig(enabled=True).persist is False
        assert ImagesConfig(enabled=True, persist=True).persist is True
