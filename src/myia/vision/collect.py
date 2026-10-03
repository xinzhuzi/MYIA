"""管线图片处理环:fetch 尾部对条目配图做 下载 → OCR → 可选 VL 情报向描述。

(task 10-03-vision-pipeline,拍板②:挂点 = ``_stage_fetch`` 条目收集后、
入 checkpoint 队列前——:func:`myia.pipeline._item_checkpoint` 本就含
metadata,图析产物续跑自然可见。)

数据流:品类 ``images:`` sidecar 节(:class:`myia.schema.ImagesConfig`)开开关
与限额;图 URL 来自条目 metadata——extract ``fields`` 配 ``image: img@src``
落入 ``metadata["image"]``(单值,str)或 L3 crawl4ai 无 extract 时的 markdown
同域收集落入 ``metadata["images"]``(list[str]);本环就地更新
``item.metadata``:

- ``image_ocr``:各图 OCR 行文本去置信度拼接(仅在有产物时写入);
- ``image_caption``:VL 情报向描述(仅 VL 产出非空时写入);
- ``image_status``:降级标记(环真正跑了才写,见下表)。

降级矩阵(全部只写 ``image_status``,**绝不抛出阻管线**):

=========================  =============================================
故障                        行为
=========================  =============================================
下载失败/网络/HTTP 错        该图跳过;全部候选图失败 → ``none``
SSRF 拒私网                  该图跳过(计入聚合,同上)
格式拒(魔法字节白名单外)    该图跳过(计入聚合,同上)
< min_bytes(图标/像素)      该图跳过(计入聚合,同上)
> 10MB 流式截断              该图跳过(计入聚合,同上)
重定向超 3 跳                该图跳过(计入聚合,同上)
超 max_images(每条)         静默截断(首张优先,extract 顺序)
超 max_per_run(每 run)      ``skipped:run_limit``,本条不再处理
OCR 异常(逐图)              有图过下载关且全部 OCR 失败 → ``ocr_failed``
VL 预算不足                  只留 OCR 产物 → ``vl_skipped_budget``
VL 超时/通道死/未配置        只留 OCR 产物 → ``vl_skipped_error``(不重试)
images 节未开/无图 URL       整环零进入,metadata 零写入
=========================  =============================================

``image_status = "ok"`` 覆盖两种成功形态:有 OCR 文本,或图内确实无字
(逐图 OCR 成功但零行——与 ``ocr_failed`` 的区分:后者每图都抛异常)。

安全(security-baseline):下载前解析主机名,私网/回环/链路本地/保留地址
一律拒(SSRF);重定向逐跳复核;png/jpg/webp/gif 魔法字节白名单;流式
10MB 截断。图文件落条目级临时目录,处理完即弃,绝不持久化。

依赖红线:OCR/VL 重依赖全部惰性(ocrmac / rapidocr-onnxruntime / openai,
extras ``myia[vision]``)——本模块 import 零重依赖,未装 extras 时 OCR 走
``ocr_failed`` 降级,不炸管线。VL 通道与端点配置复用 ``vision.yaml``
(:class:`myia.vision.settings.VisionConfig`);token 用量经
``BudgetTracker.can_spend/spend``(与 enrich/aggregate 同一共享池,token
单位零换算)。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from myia.schema import ImagesConfig
from myia.vision.client import VisionClient
from myia.vision.ocr import OCRError, run_ocr
from myia.vision.settings import VisionConfig, resolve_cloud_api_key

__all__ = [
    "DESCRIBE_PROMPT",
    "DOWNLOAD_TIMEOUT_SECONDS",
    "IMAGE_OCR_JOIN",
    "ImageRunState",
    "MAX_IMAGE_BYTES",
    "MAX_REDIRECT_HOPS",
    "OCR_CONCURRENCY",
    "VL_CONCURRENCY",
    "VL_TIMEOUT_SECONDS",
    "markdown_image_urls",
    "process_item_images",
]

logger = logging.getLogger(__name__)

#: 单图下载超时(秒;拍板④)。
DOWNLOAD_TIMEOUT_SECONDS = 10.0
#: 单图流式截断上限(字节,10MB;超过即弃,防恶意大图撑爆内存/磁盘)。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
#: 手动重定向跟进上限(逐跳 SSRF 复核;超过按跳过降级)。
MAX_REDIRECT_HOPS = 3
#: 本地 OCR 线程并发上限(``run_ocr`` 同步阻塞 → to_thread 池)。
OCR_CONCURRENCY = 4
#: VL 并发上限(本地 GPU 单飞实测定调,拍板③)。
VL_CONCURRENCY = 1
#: VL 每图超时(秒;管线收紧——交互模式 180s 是裕量,不进管线)。
VL_TIMEOUT_SECONDS = 45.0
#: 多图 OCR 文本拼接分隔符。
IMAGE_OCR_JOIN = "\n"

#: 情报向 describe 模板(拍板⑧:**collect.py 是管线侧事实源**,与 desktop
#: 交互屏的通用照片向模板两用途两模板——构图/流派/氛围不进管线)。
DESCRIBE_PROMPT = (
    "用中文对这张新闻配图做情报式解读,按以下结构输出:"
    "①图中内容(人/物/场景);"
    "②可见文字要点(标题/水印/界面文字逐条列出);"
    "③与条目标题的关系(配图如何支撑或补充标题);"
    "④若是数据/图表,读出关键数值与趋势。"
    "不要泛泛描述构图与审美。条目标题:{title}"
)

#: 魔法字节白名单:文件头签名 → 扩展名(png/jpg/webp/gif)。
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

#: markdown 图片语法 ``![alt](url "title")``(title 可选)。
_MARKDOWN_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def _sniff_format(head: bytes) -> str | None:
    """前 12 字节嗅探图片格式(png/jpg/webp/gif);白名单外返回 None。"""
    for signature, ext in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def markdown_image_urls(markdown: str, base_url: str) -> list[str]:
    """从渲染 markdown 收集**同域**图片 URL(拍板⑥:跨域广告/追踪像素不收)。

    L3 crawl4ai 无 extract 时结构化为 markdown 纯文本,图片链接本来会被丢弃
    (原始 HTML 不落库)——此函数把 ``![alt](url)`` 形态的图片链接按页 URL
    resolve 后过滤同域、去重保序。品类 ``images:`` 节开启时,这些 URL 由
    :func:`process_item_images` 消费。
    """
    seen: set[str] = set()
    collected: list[str] = []
    base_host = (urlparse(base_url).hostname or "").lower()
    for match in _MARKDOWN_IMAGE_RE.finditer(markdown):
        absolute = urljoin(base_url, match.group(1))
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        # 同域判定(hostname 全等,大小写不敏感);www 前缀差异视为跨域,从严。
        if (parsed.hostname or "").lower() != base_host:
            continue
        if absolute not in seen:
            seen.add(absolute)
            collected.append(absolute)
    return collected


# ---------------------------------------------------------------------------
# SSRF 防护(security-baseline:图片下载是管线里「URL 直接来自页面内容」的
# 出网点——extract 抽到的图地址可能是内网诱导链接)
# ---------------------------------------------------------------------------


def _resolve_host(host: str) -> list[str]:
    """解析主机名 → IP 文本列表(测试的 monkeypatch 点,零外网)。"""
    addr_infos = socket.getaddrinfo(host, None)
    return sorted({info[4][0] for info in addr_infos})


def _is_private_ip(ip_text: str) -> bool:
    """私网/回环/链路本地/保留/组播/未指定判定(不可解析从严按私网拒)。"""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    # IPv4-mapped IPv6(::ffff:10.0.0.1)按映射后的 v4 判定,防绕过。
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _host_is_public(url: str) -> bool:
    """URL 主机是否解析到全公网地址(任一 IP 落私网段即拒)。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        # getaddrinfo 是阻塞调用:丢线程池,别卡事件循环。
        ips = await asyncio.to_thread(_resolve_host, parsed.hostname)
    except (OSError, UnicodeError):
        return False
    return bool(ips) and not any(_is_private_ip(ip) for ip in ips)


# ---------------------------------------------------------------------------
# 每 run 配额
# ---------------------------------------------------------------------------


@dataclass
class ImageRunState:
    """每 run 图处理配额(``max_per_run`` 硬闸;fetch 阶段持有,逐条扣减)。"""

    remaining: int = 0
    #: 观测:本 run 因配额耗尽被 ``skipped:run_limit`` 的条目数。
    throttled_items: int = field(default=0)

    def take(self, wanted: int) -> int:
        """扣减配额,返回实际 granted 数(0 = 配额已尽)。"""
        granted = max(0, min(wanted, self.remaining))
        self.remaining -= granted
        return granted


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Downloaded:
    """一张通过全部下载关的图(临时文件,随条目级临时目录清理)。"""

    path: Path
    size: int


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


async def _download_image(
    client: httpx.AsyncClient,
    url: str,
    dest_dir: Path,
    *,
    min_bytes: int,
) -> _Downloaded | str:
    """下载一张图到临时文件;返回 ``_Downloaded`` 或跳过原因(绝不抛出)。

    关卡顺序:SSRF 拒私网(重定向逐跳复核)→ HTTP 状态 → 魔法字节白名单 →
    流式 10MB 截断 → ``min_bytes`` 下限(图标/追踪像素)。
    """
    current = url
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        if not await _host_is_public(current):
            return "ssrf"
        try:
            # 每请求显式 10s 帽:调用方注入的 client(如管线主 client)默认
            # 超时可能更宽,图片下载的预算不随它膨胀。
            async with client.stream("GET", current, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "")
                    if not location:
                        return "redirect_without_location"
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    return f"http_{response.status_code}"
                head = b""
                size = 0
                handle, tmp_name = tempfile.mkstemp(dir=dest_dir, suffix=".part")
                os.close(handle)
                tmp = Path(tmp_name)
                try:
                    with open(tmp, "wb") as sink:
                        async for chunk in response.aiter_bytes():
                            if len(head) < 12:
                                head = (head + chunk)[:12]
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                return "too_large"
                            sink.write(chunk)
                except httpx.HTTPError:
                    return "network"
                fmt = _sniff_format(head)
                if fmt is None:
                    return "format"
                if size < min_bytes:
                    return "too_small"
                final = tmp.with_suffix(f".{fmt}")
                tmp.replace(final)
                return _Downloaded(path=final, size=size)
        except httpx.HTTPError:
            return "network"
    return "too_many_redirects"


# ---------------------------------------------------------------------------
# 主环
# ---------------------------------------------------------------------------


def _candidate_urls(metadata: Mapping[str, Any]) -> list[str]:
    """条目 metadata 里的图 URL 候选(``images`` list + ``image`` str,去重保序)。"""
    raw: list[Any] = []
    images_value = metadata.get("images")
    if isinstance(images_value, list):
        raw.extend(images_value)
    elif isinstance(images_value, str):
        raw.append(images_value)
    image_value = metadata.get("image")
    if isinstance(image_value, str):
        raw.append(image_value)
    seen: set[str] = set()
    urls: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if value not in seen:
            seen.add(value)
            urls.append(value)
    return urls


#: 源级平铺覆写键 → ImagesConfig 字段(装载期不强校验,这里手工规整)。
_SOURCE_BOOL_OVERRIDES = {"images_enabled": "enabled"}
_SOURCE_INT_OVERRIDES = {"images_max_images": "max_images", "images_min_bytes": "min_bytes"}
_SOURCE_STR_OVERRIDES = {"images_vl": "vl", "images_ocr_engine": "ocr_engine"}


def _effective_config(
    images_cfg: ImagesConfig, source_extra: Mapping[str, Any] | None
) -> ImagesConfig | None:
    """品类 images 节 × 源级平铺覆写(``images_*``,``extra="allow"`` 通道)。

    装载期对覆写值不做 schema 强校验(与引擎扩展参数同宽容度):这里手工
    规整类型,非法值告警忽略;``images_enabled: false`` 整源关闭返回 None。
    ``max_per_run`` 是 run 级硬闸,不开放源级覆写。
    """
    if not source_extra:
        return images_cfg
    overrides: dict[str, Any] = {}
    for flat_key, cfg_key in _SOURCE_BOOL_OVERRIDES.items():
        value = source_extra.get(flat_key)
        if isinstance(value, bool):
            overrides[cfg_key] = value
        elif value is not None:
            logger.warning("源级覆写 %s=%r 应为布尔,忽略", flat_key, value)
    for flat_key, cfg_key in _SOURCE_INT_OVERRIDES.items():
        value = source_extra.get(flat_key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            overrides[cfg_key] = value
        elif value is not None:
            logger.warning("源级覆写 %s=%r 应为正整数,忽略", flat_key, value)
    for flat_key, cfg_key in _SOURCE_STR_OVERRIDES.items():
        value = source_extra.get(flat_key)
        if isinstance(value, str) and value:
            overrides[cfg_key] = value
        elif value is not None:
            logger.warning("源级覆写 %s=%r 应为字符串,忽略", flat_key, value)
    if not overrides:
        return images_cfg
    merged = {**images_cfg.model_dump(), **overrides}
    try:
        return ImagesConfig.model_validate(merged)
    except Exception as exc:  # noqa: BLE001 - 覆写值非法:回退品类节,不阻管线
        logger.warning("源级 images_* 覆写合并失败(%s),回退品类节: %s", overrides, exc)
        return images_cfg


class _VlChannel:
    """按 ``vl`` 取值装配的 VL 通道(不可用时带降级原因,归 ``vl_skipped_error``)。"""

    def __init__(self, channel: str, vision_cfg: VisionConfig) -> None:
        self.available = False
        self.reason = ""
        self.base_url = ""
        self.model = ""
        self.api_key: str | None = None
        if channel == "local":
            if not vision_cfg.local_model:
                self.reason = "no_local_model"
                return
            self.base_url = vision_cfg.local_base_url
            self.model = vision_cfg.local_model
            self.available = True
        elif channel == "cloud":
            try:
                # 显式 myia/image/api_key 优先;缺省回落既有 GLM 链路
                # (myia/llm/api_key,settings.resolve_cloud_api_key 解析序)——
                # 已配 GLM 的主机 `vl: cloud` 开箱即用,免二次录 key。
                self.api_key = resolve_cloud_api_key(vision_cfg)
            except Exception as exc:  # noqa: BLE001 - 凭据解析失败 = VL 降级,不阻管线
                self.reason = f"credential_resolve_failed:{type(exc).__name__}"
                logger.warning("VL 云端凭据解析失败(降级只 OCR): %s", exc)
                return
            if not self.api_key:
                self.reason = "no_cloud_credentials"
                return
            self.base_url = vision_cfg.cloud_base_url
            self.model = vision_cfg.cloud_model
            self.available = True


async def process_item_images(
    item: Any,
    *,
    images_cfg: ImagesConfig,
    vision_cfg: VisionConfig,
    budget: Any | None = None,
    proxy_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    run_state: ImageRunState | None = None,
    source_extra: Mapping[str, Any] | None = None,
) -> str | None:
    """对一个条目跑图片处理环,就地更新 ``item.metadata``。

    Args:
        item: 管线条目(鸭子类型:``url``/``title``/``metadata`` dict)。
        images_cfg: 品类 ``images:`` 节(schema sidecar)。
        vision_cfg: ``vision.yaml`` 内存形态(OCR 缺省引擎 / VL 通道端点)。
        budget: 单轮共享 token 预算池(``BudgetTracker``);VL 每图先
            ``can_spend`` 再 ``spend(total_tokens)``——``None`` 视为预算
            不可用,VL 直接 ``vl_skipped_budget``(OCR 不受影响)。
        proxy_url: 可选出网代理(图片下载 rides it;缺省直连)。
        client: 可注入 ``httpx.AsyncClient``(测试 MockTransport);注入的
            客户端应 ``follow_redirects=False``——重定向由本环逐跳复核。
        run_state: 每 run 图配额(``max_per_run``);``None`` = 单条调用自建
            (管线侧应传入共享实例)。
        source_extra: 源级 ``extra_params``(``images_*`` 平铺覆写)。

    Returns:
        写入 ``metadata["image_status"]`` 的标记;环未进入(节未开/无图/
        源级关闭)返回 ``None``,metadata 零写入。

    绝不抛出:任何未预期异常兜底为 ``skipped:internal_error``,只写标记。
    """
    try:
        effective = _effective_config(images_cfg, source_extra)
        if effective is None or not effective.enabled:
            return None
        urls = _candidate_urls(item.metadata)
        if not urls:
            return None
        if run_state is None:
            run_state = ImageRunState(remaining=effective.max_per_run)
        if run_state.remaining <= 0:
            run_state.throttled_items += 1
            item.metadata["image_status"] = "skipped:run_limit"
            return "skipped:run_limit"
        selected = urls[: effective.max_images]
        granted = run_state.take(len(selected))
        if granted <= 0:
            run_state.throttled_items += 1
            item.metadata["image_status"] = "skipped:run_limit"
            return "skipped:run_limit"
        taken = selected[:granted]
        engine = effective.ocr_engine or vision_cfg.ocr_engine_default

        own_client = client is None
        http_client = client or httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False, proxy=proxy_url
        )
        try:
            with tempfile.TemporaryDirectory(prefix="myia-images-") as tmp:
                dest_dir = Path(tmp)
                downloads = await asyncio.gather(
                    *(
                        _download_image(
                            http_client, url, dest_dir, min_bytes=effective.min_bytes
                        )
                        for url in taken
                    )
                )
                for url, outcome in zip(taken, downloads):
                    if isinstance(outcome, str):
                        logger.info("图片跳过 url=%s reason=%s item=%s", url, outcome, item.url)
                usable = [d for d in downloads if isinstance(d, _Downloaded)]
                if not usable:
                    item.metadata["image_status"] = "none"
                    return "none"

                ocr_sem = asyncio.Semaphore(OCR_CONCURRENCY)

                async def _ocr(path: Path) -> list[str] | None:
                    """一张图的 OCR(to_thread + 信号量;OCRError → None)。"""
                    async with ocr_sem:
                        try:
                            lines = await asyncio.to_thread(run_ocr, path, engine)
                        except OCRError as exc:
                            logger.warning("图片 OCR 失败(降级): %s", exc)
                            return None
                        return [line.text for line in lines]

                per_image_texts = await asyncio.gather(*(_ocr(d.path) for d in usable))
                texts = [t for t in per_image_texts if t]
                if not texts:
                    if all(t is None for t in per_image_texts):
                        item.metadata["image_status"] = "ocr_failed"
                        return "ocr_failed"
                    # 图内确实无字(逐图 OCR 成功但零行):环成功,零 OCR 产物。
                    item.metadata["image_status"] = "ok"
                    return "ok"
                item.metadata["image_ocr"] = IMAGE_OCR_JOIN.join(
                    IMAGE_OCR_JOIN.join(t) for t in texts
                )

                # ---- VL 情报向 caption(可选;并发 1、45s/图、走预算池)----
                if effective.vl == "off":
                    item.metadata["image_status"] = "ok"
                    return "ok"
                vl = _VlChannel(effective.vl, vision_cfg)
                if not vl.available:
                    logger.info("VL 通道不可用(只留 OCR 产物) reason=%s", vl.reason)
                    item.metadata["image_status"] = "vl_skipped_error"
                    return "vl_skipped_error"
                if budget is None or not budget.can_spend():
                    item.metadata["image_status"] = "vl_skipped_budget"
                    return "vl_skipped_budget"
                caption_parts: list[str] = []
                vl_failed = False
                budget_exhausted = False
                vl_client = VisionClient(
                    vl.base_url, vl.model, api_key=vl.api_key, timeout_seconds=VL_TIMEOUT_SECONDS
                )
                vl_sem = asyncio.Semaphore(VL_CONCURRENCY)
                prompt = DESCRIBE_PROMPT.format(title=getattr(item, "title", "") or "")
                try:
                    for downloaded in usable:
                        if budget is not None and not budget.can_spend():
                            # 中途耗尽:已产出的 caption 照常落,后续图不再发。
                            budget_exhausted = True
                            break

                        async def _analyze(path: Path = downloaded.path) -> Any:
                            async with vl_sem:
                                return await asyncio.wait_for(
                                    vl_client.analyze(image_path=path, prompt=prompt),
                                    timeout=VL_TIMEOUT_SECONDS,
                                )

                        try:
                            result = await _analyze()
                        except Exception as exc:  # noqa: BLE001 - VL 失败不重试
                            logger.warning(
                                "VL 描述失败(vl_skipped_error,不重试) url=%s: %s", item.url, exc
                            )
                            vl_failed = True
                            break
                        if budget is not None:
                            budget.spend(result.total_tokens)
                        if result.text.strip():
                            caption_parts.append(result.text.strip())
                finally:
                    await vl_client.aclose()
                if caption_parts:
                    item.metadata["image_caption"] = IMAGE_OCR_JOIN.join(caption_parts)
                if budget_exhausted:
                    item.metadata["image_status"] = "vl_skipped_budget"
                    return "vl_skipped_budget"
                item.metadata["image_status"] = "vl_skipped_error" if vl_failed else "ok"
                return item.metadata["image_status"]
        finally:
            if own_client:
                await http_client.aclose()
    except Exception as exc:  # noqa: BLE001 - 降级矩阵兜底:图析绝不阻管线
        logger.warning("图片处理环未预期异常(只写标记) url=%s: %s", getattr(item, "url", "?"), exc)
        try:
            item.metadata["image_status"] = "skipped:internal_error"
        except Exception:  # noqa: BLE001 - metadata 不可写:除日志外无能为力
            pass
        return "skipped:internal_error"
