"""管线图片处理环:fetch 尾部对条目配图做 下载 → OCR → 可选 VL 情报向描述。

(task 10-03-vision-pipeline,拍板②:挂点 = ``_stage_fetch`` 条目收集后、
入 checkpoint 队列前——:func:`shishi.pipeline._item_checkpoint` 本就含
metadata,图析产物续跑自然可见。)

数据流:品类 ``images:`` sidecar 节(:class:`shishi.schema.ImagesConfig`)开开关
与限额;图 URL 来自条目 metadata——extract ``fields`` 配 ``image: img@src``
落入 ``metadata["image"]``(单值,str)或 L3 crawl4ai 无 extract 时的 markdown
同域收集落入 ``metadata["images"]``(list[str]);本环就地更新
``item.metadata``:

- ``image_ocr``:各图 OCR 行文本去置信度拼接(仅在有产物时写入);
- ``image_caption``:VL 情报向描述(仅 VL 产出非空时写入);
- ``image_status``:降级标记(环真正跑了才写,见下表)。

可选落图(10-03-vision-v2,品类 ``images.persist`` 开):通过全部下载关的
图**内容寻址持久化**到 ``<images_dir>/<sha16>.<ext>``(调用方传入,管线
侧 = 数据根 ``images/``;同图同文件不重复落盘),metadata 另增
``image_files``(绝对路径 list)与 ``image_ocr_lines``(逐行
``{text, conf}``,供 feed 详情展开);缺省关 = 图文件即弃,行为与落图
能力引入前逐字段一致。落盘失败(磁盘满等)只告警跳过该图,绝不阻管线。

详情页追抓(10-03-detail-images):``images.detail_fetch`` 开时,同一挂点在
识图环**之前**对本轮无图条目追抓其详情页——带**源级请求头**的静态 GET
(:func:`detail_request_headers` 与引擎链同一装配语义:源 ``headers`` 的
UA/登录 Cookie 凭据引用随行,JS 渲染链刻意不做,SPA 页自然 ``no_images``;
SSRF 前置校验 + 连接层远端 IP 复核 + 逐跳重定向复核,纪律与图片下载同源),
HTML 同域收 ``<img>``(regex,语义照抄:func:`markdown_image_urls`)写回
``metadata.images`` 后进同一环;串行 + 每请求 ≥1s 间隔(礼貌自管,不用源
qps 语义)+ 10s/页超时 + 每 run ``detail_max_items`` 条上限(源级
``images_detail_max_items`` 覆写 = 该源**独立预算**,不吃也不占共享池)。
降级只写 ``metadata.detail_status``(``ok:n=N`` / ``no_images`` /
``failed:<原因>``),绝不阻管线。

降级矩阵(全部只写 ``image_status``,**绝不抛出阻管线**):

=========================  =============================================
故障                        行为
=========================  =============================================
下载失败/网络/HTTP 错        该图跳过;全部候选图失败 → ``none``
SSRF 拒私网                  该图跳过(计入聚合,同上)
DNS rebinding(连接层私网)  该图跳过(reason=ssrf_rebind,计入聚合,同上)
格式拒(魔法字节白名单外)    该图跳过(计入聚合,同上)
< min_bytes(图标/像素)      该图跳过(计入聚合,同上)
> 10MB 流式截断              该图跳过(计入聚合,同上)
重定向超 3 跳               该图跳过(计入聚合,同上)
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
一律拒(SSRF);**连接建立后复核实际远端 IP**(DNS rebinding TOCTOU:
前置校验与连接是两次独立解析,两次之间换 IP 即拒,reason=``ssrf_rebind``;
MockTransport 无网络层信息时跳过复核);每请求显式
``follow_redirects=False``——重定向由本环逐跳复核,**注入 client 的
follow_redirects 默认值不再相关**(10-03-image-fix-followups 小修⑤);
png/jpg/webp/gif 魔法字节白名单;流式 10MB 截断。图文件落条目级临时目录,
处理完即弃,绝不持久化。

依赖红线:OCR/VL 重依赖全部惰性(ocrmac / rapidocr-onnxruntime / openai,
extras ``shishi[vision]``)——本模块 import 零重依赖,未装 extras 时 OCR 走
``ocr_failed`` 降级,不炸管线。VL 通道与端点配置复用 ``vision.yaml``
(:class:`shishi.vision.settings.VisionConfig`);token 用量经
``BudgetTracker.can_spend/spend``(与 enrich/aggregate 同一共享池,token
单位零换算)。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from shishi.engines.fetch_base import (
    DEFAULT_USER_AGENT,
    decode_response,
    resolve_headers,
)
from shishi.schema import ImagesConfig
from shishi.vision.client import VisionClient
from shishi.vision.ocr import OCRError, run_ocr
from shishi.vision.settings import VisionConfig, resolve_cloud_api_key

__all__ = [
    "DESCRIBE_PROMPT",
    "DETAIL_FETCH_INTERVAL_SECONDS",
    "DETAIL_FETCH_TIMEOUT_SECONDS",
    "DOWNLOAD_TIMEOUT_SECONDS",
    "IMAGE_OCR_JOIN",
    "ImageRunState",
    "MAX_IMAGE_BYTES",
    "MAX_REDIRECT_HOPS",
    "OCR_CONCURRENCY",
    "PERSIST_DIR_NAME",
    "VL_CONCURRENCY",
    "VL_TIMEOUT_SECONDS",
    "detail_fetch_images",
    "detail_request_headers",
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
#: VL 每图超时(秒;管线收紧——交互模式 180s 是裕量,不进管线)。2026-10-03
#: 真网实测 45s 误杀 describe 长输出(本地 GPU 30-60s 边缘,asyncio.TimeoutError
#: 的 str 为空导致日志无信息),收紧到 90s;run 级护栏仍由 max_per_run 扛。
VL_TIMEOUT_SECONDS = 90.0
#: 详情页追抓每页超时(秒;含重定向跳数,10-03-detail-images 拍板:10s/页)。
DETAIL_FETCH_TIMEOUT_SECONDS = 10.0
#: 详情页追抓相邻请求最小间隔(秒;串行 + ≥1s 礼貌自管——不用源 qps 语义,
#: 覆写单源时也不会比列表抓取更密)。测试 monkeypatch 本常量归零加速。
DETAIL_FETCH_INTERVAL_SECONDS = 1.0
#: 多图 OCR 文本拼接分隔符。
IMAGE_OCR_JOIN = "\n"
#: 落图目录名(10-03-vision-v2:``images.persist`` 开时图存数据根下该目录,
#: 内容寻址 ``<sha16>.<ext>``;管线侧由调用方拼根,本模块只收绝对目录)。
PERSIST_DIR_NAME = "images"

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

#: HTML ``<img ...>`` 标签(详情页追抓的收集目标;大小写不敏感)。
_HTML_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
#: ``<img>`` 标签内的 ``src`` 属性(单/双/无引号三形态;负向后顾排除
#: ``data-src`` 等 ``-src`` 变体——懒加载深挖刻意不做,min_bytes 兜底)。
_HTML_SRC_ATTR_RE = re.compile(
    r"""(?<![-\w])src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE
)


def _sniff_format(head: bytes) -> str | None:
    """前 12 字节嗅探图片格式(png/jpg/webp/gif);白名单外返回 None。"""
    for signature, ext in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _collect_same_domain_urls(raw_values: Iterable[str], base_url: str) -> list[str]:
    """把(可能相对的)URL 候选 resolve 到 ``base_url`` 后过滤**同域**(公共小函数,
    markdown 收集与详情页 HTML 收集共用同一语义)。

    同域判定(hostname 全等,大小写不敏感;www 前缀差异视为跨域,从严);
    scheme 限 http/https;去重保序;空值跳过(空 ``src=""`` resolve 成页面
    自身,不是图)。
    """
    seen: set[str] = set()
    collected: list[str] = []
    base_host = (urlparse(base_url).hostname or "").lower()
    for raw in raw_values:
        if not raw or not raw.strip():
            continue
        absolute = urljoin(base_url, raw.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if (parsed.hostname or "").lower() != base_host:
            continue
        if absolute not in seen:
            seen.add(absolute)
            collected.append(absolute)
    return collected


def markdown_image_urls(markdown: str, base_url: str) -> list[str]:
    """从渲染 markdown 收集**同域**图片 URL(拍板⑥:跨域广告/追踪像素不收)。

    L3 crawl4ai 无 extract 时结构化为 markdown 纯文本,图片链接本来会被丢弃
    (原始 HTML 不落库)——此函数把 ``![alt](url)`` 形态的图片链接按页 URL
    resolve 后过滤同域、去重保序。品类 ``images:`` 节开启时,这些 URL 由
    :func:`process_item_images` 消费。
    """
    return _collect_same_domain_urls(
        (match.group(1) for match in _MARKDOWN_IMAGE_RE.finditer(markdown)), base_url
    )


def _html_image_urls(html: str, base_url: str) -> list[str]:
    """从详情页 HTML 收集**同域** ``<img src>`` URL(10-03-detail-images)。

    轻量 regex(DOM 解析刻意不做,坏 HTML 容忍):逐 ``<img>`` 标签取首个
    ``src``;同域/去重保序语义与 :func:`markdown_image_urls` 完全同源(共用
    :func:`_collect_same_domain_urls`)。avatar/emoji 类路径启发式不做——
    小图交给 ``min_bytes`` 下限兜底。懒加载 ``data-src`` 深挖刻意不做。
    """
    raw_values: list[str] = []
    for tag in _HTML_IMG_TAG_RE.finditer(html):
        match = _HTML_SRC_ATTR_RE.search(tag.group(0))
        if match is None:
            continue
        value = next((group for group in match.groups() if group is not None), "")
        raw_values.append(value)
    return _collect_same_domain_urls(raw_values, base_url)


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
    # fake-ip 代理段(RFC 2544 基准 198.18.0.0/15)豁免:Clash/Surge 等 fake-ip
    # 模式把一切域名 DNS 应答进该段,连接经系统代理回到真实目标,并非内网
    # 直达;不豁免则代理环境下所有域名的图片全被误杀(2026-10-03 真网探针
    # 实证:imgs.xkcd.com 解析 198.18.0.213 被 reason=ssrf 拒)。
    if ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15"):
        return False
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


def _connected_server_ip(response: httpx.Response) -> str | None:
    """响应底层连接的**实际远端 IP**(DNS rebinding 复核;取不到返回 None)。

    前置 :func:`_host_is_public` 与 httpx 建连是两次独立解析——两次之间
    DNS 应答可被切到私网地址(TOCTOU)。连接建立后从响应 extensions 的
    network_stream 读实际连上的地址再判一次(httpx 0.28 实测:
    ``get_extra_info("server_addr")`` 返回 ``(host, port)``;兼容旧形态
    ``get_conn_info().server_addr``)。MockTransport 无网络层信息 → None,
    调用方跳过复核(前置校验已做)。
    """
    stream = response.extensions.get("network_stream") if response.extensions else None
    if stream is None:
        return None
    address: object = None
    get_conn_info = getattr(stream, "get_conn_info", None)
    if callable(get_conn_info):
        try:
            info = get_conn_info()
        except Exception:  # noqa: BLE001 - 复核失败按无信息处理,不阻下载
            return None
        address = getattr(info, "server_addr", None)
    else:
        get_extra_info = getattr(stream, "get_extra_info", None)
        if callable(get_extra_info):
            try:
                address = get_extra_info("server_addr")
            except Exception:  # noqa: BLE001 - 同上
                return None
    if isinstance(address, tuple) and address:
        return str(address[0])
    if isinstance(address, str) and address:
        return address
    return None


def _conn_is_public(response: httpx.Response) -> bool:
    """连接层复核:实际远端 IP 落私网段(或不可解析)即拒(rebinding 变体)。"""
    ip_text = _connected_server_ip(response)
    if ip_text is None:
        return True
    return not _is_private_ip(ip_text)


def _client_egress_is_proxied(client: httpx.AsyncClient) -> bool:
    """client 出网是否经代理(任一 mount 或默认 transport 的底层池带代理 URL)。

    httpx 0.28 无公开 API 暴露代理配置,``_mounts``/``_transport`` 底层池的
    ``_proxy_url`` 是唯一观测面,三种来源同盖:显式 ``proxy=``、trust_env
    吃 ``HTTP(S)_PROXY`` 环境变量、以及 macOS/Windows **系统代理**(实测
    httpx 0.28.1:进程 proxy 环境变量为空时 ``AsyncClient()`` 仍挂上系统
    代理 127.0.0.1:7897,池 ``_proxy_url`` 非 None)。socks 上游同样以
    ``AsyncSOCKSProxy._proxy_url`` 暴露。测试注入的 MockTransport 无
    ``_pool`` → 按直连处理(其响应本就无 network_stream,复核自然跳过)。
    """
    transports = list(getattr(client, "_mounts", {}).values())
    default_transport = getattr(client, "_transport", None)
    if default_transport is not None:
        transports.append(default_transport)
    return any(
        getattr(getattr(transport, "_pool", None), "_proxy_url", None) is not None
        for transport in transports
    )


# ---------------------------------------------------------------------------
# 每 run 配额
# ---------------------------------------------------------------------------


@dataclass
class ImageRunState:
    """每 run 图处理配额(``max_per_run`` 硬闸;fetch 阶段持有,逐条扣减)。

    ``detail_remaining`` 是详情页追抓的姊妹配额(``detail_max_items``,按
    **条目**计——一次追抓 = 一页,与图片张数配额互相独立);源级
    ``images_detail_max_items`` 覆写的源不吃这张共享池,改吃
    ``detail_remaining_by_source`` 里自己的独立预算(见字段注释);
    ``detail_fetches`` 计数已发起的追抓,串行 ≥1s 间隔的节拍依据
    (首个请求前不等待,跨源共享——礼貌是全 run 一份)。
    """

    remaining: int = 0
    #: 观测:本 run 因配额耗尽被 ``skipped:run_limit`` 的条目数。
    throttled_items: int = field(default=0)
    #: 详情页追抓剩余配额(条目数;耗尽后其余无图条目零进入零标记)。
    detail_remaining: int = 0
    #: 源级 ``images_detail_max_items`` 覆写的独立预算(源名 → 剩余条数):
    #: 覆写源的条目吃自己的池,不吃也不占共享 ``detail_remaining``——覆写
    # 因此可低于(也可高于)品类共享池而互不侵占;无覆写源照旧吃共享池。
    detail_remaining_by_source: dict[str, int] = field(default_factory=dict)
    #: 观测:本 run 已追抓的详情页数。
    detail_fetches: int = field(default=0)

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
    # 经代理出网时跳过连接层复核:network_stream.server_addr 是**代理**地址
    # 而非目标站 IP(pool 上游/系统代理多为 127.0.0.1 或私网段),复核必误杀
    # (实测:经本地代理的源全部图片被拒 ssrf_rebind 静默全灭);目标域私网
    # 判定由前置 ``_host_is_public`` 的本地 DNS 校验继续兜底。与 fake-ip 段
    # 豁免(_is_private_ip 注释)同宗:代理形态下连接层信号无意义。
    conn_recheck_enabled = not _client_egress_is_proxied(client)
    current = url
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        if not await _host_is_public(current):
            return "ssrf"
        try:
            # 每请求显式 10s 帽 + follow_redirects=False:调用方注入的 client
            # (如管线主 client)默认超时/重定向策略可能更宽,图片下载的预算
            # 与逐跳 SSRF 复核不随它膨胀(注入 client 的 redirect 默认值因此
            # 不再相关——10-03-image-fix-followups 小修⑤)。
            async with client.stream(
                "GET", current, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False
            ) as response:
                # 连接层复核(DNS rebinding):前置校验与建连是两次独立解析,
                # 实际连上的 IP 落私网段即拒(经代理时已在上文跳过)。
                if conn_recheck_enabled and not _conn_is_public(response):
                    return "ssrf_rebind"
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
# 详情页追抓(10-03-detail-images:fetch 尾部、识图环之前)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DetailPage:
    """一页通过全部关卡的详情页 HTML(``failed:<原因>`` 用裸 str 表达)。"""

    html: str


def detail_request_headers(
    source_headers: Mapping[str, str] | None,
    *,
    backend: Any | None = None,
) -> dict[str, str]:
    """详情页追抓的源级请求头装配(契约「引擎链抓 item.url」的 headers 面)。

    与 :class:`shishi.engines.fetch_base.BaseEngine` 构造期同一规则:源
    ``headers`` 里的 ``env:``/``keychain:`` 凭据引用经
    :func:`shishi.engines.fetch_base.resolve_headers` 解析(登录态
    Cookie/Authorization 随行),未配 User-Agent 时补引擎缺省 UA——源配的
    Chrome UA 不再被 httpx 默认 UA 顶掉(反爬 403 风险)。凭据解析失败只
    告警并裸头降级(追抓绝不阻管线;列表抓取在同一凭据上早已结构化失败,
    条目本就不该在场)。

    Args:
        source_headers: 源配置的 ``headers`` 节(``None``/空 = 只补缺省 UA)。
        backend: 钥匙串后端(``FetchContext.keychain_backend``;``None`` =
            系统钥匙串发现)。
    """
    try:
        resolved = resolve_headers(source_headers or {}, backend=backend)
    except Exception as exc:  # noqa: BLE001 - 凭据解析失败:裸头降级,不阻管线
        logger.warning("详情页追抓请求头凭据解析失败(裸头降级): %s", exc)
        return {}
    if "user-agent" not in {key.lower() for key in resolved}:
        resolved["User-Agent"] = DEFAULT_USER_AGENT
    return resolved


async def _fetch_detail_html(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str] | None = None,
) -> _DetailPage | str:
    """抓一页详情 HTML;返回 ``_DetailPage`` 或失败原因(绝不抛出)。

    源级请求头(``headers``,:func:`detail_request_headers` 装配)逐跳随行
    ——重定向后的每跳都带源 UA/登录头,与引擎链抓列表页同一出网身份。
    出网纪律与图片下载同源:SSRF 前置校验 + 连接层远端 IP 复核(rebinding)
    + 重定向逐跳复核 + 每请求显式 ``follow_redirects=False``;10MB 体量截断。
    失败/重定向超限不重试(SPA/死链单次成本即止)。整页 10s 帽由调用方的
    ``asyncio.wait_for`` 兜(含重定向跳数)。
    """
    # 经代理出网时跳过连接层复核( rationale 同 _download_image:server_addr
    # 是代理地址而非目标站 IP,复核必误杀;目标域私网由前置 DNS 校验兜底)。
    conn_recheck_enabled = not _client_egress_is_proxied(client)
    current = url
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        if not await _host_is_public(current):
            return "ssrf"
        try:
            async with client.stream(
                "GET",
                current,
                timeout=DETAIL_FETCH_TIMEOUT_SECONDS,
                follow_redirects=False,
                headers=headers,
            ) as response:
                if conn_recheck_enabled and not _conn_is_public(response):
                    return "ssrf_rebind"
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "")
                    if not location:
                        return "redirect_without_location"
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    return f"http_{response.status_code}"
                try:
                    # 非流式整读(与引擎层列表页抓取同一暴露面);截断只防
                    # 下游 regex 成本,体量护栏由 10s 帽先兜。
                    await response.aread()
                except httpx.HTTPError:
                    return "network"
                if len(response.content) > MAX_IMAGE_BYTES:
                    return "too_large"
                return _DetailPage(html=decode_response(response))
        except httpx.HTTPError:
            return "network"
    return "too_many_redirects"


async def detail_fetch_images(
    item: Any,
    *,
    images_cfg: ImagesConfig,
    source_extra: Mapping[str, Any] | None = None,
    source_name: str | None = None,
    headers: Mapping[str, str] | None = None,
    proxy_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    run_state: ImageRunState | None = None,
) -> str | None:
    """对一个无图条目追抓详情页,同域收 ``<img>`` 就地写 ``item.metadata``。

    (10-03-detail-images;在识图环**之前**调用——写回的 ``metadata.images``
    随即被 :func:`process_item_images` 消费,进同一个下载→OCR→VL 环。)

    进入条件(缺一即返回 ``None``、metadata 零写入,零影响默认):
    ``images.enabled`` 且 ``detail_fetch`` 开(品类节或源级
    ``images_detail_fetch`` 平铺覆写);条目 metadata 无可用图 URL
    (``image``/``images`` 键缺省或全不可用——「列表页已带图的条目不追抓」);
    追抓配额未尽——无源级覆写时吃 run 级共享 ``detail_remaining``
    (按管线顺序先到先得,截断后的条目照常入库零标记);源级
    ``images_detail_max_items`` 覆写且带 ``source_name`` 时改吃该源
    **独立预算**(:attr:`ImageRunState.detail_remaining_by_source`,不吃
    也不占共享池)。

    串行 + 每请求 ≥1s 间隔(:data:`DETAIL_FETCH_INTERVAL_SECONDS`,首个请求
    前不等待;调用方逐条串行 await,礼貌自管——不用源 qps 语义)。

    写入(``metadata["detail_status"]``,任何失败只标记不抛,绝不阻管线):

    - 成功收图:``metadata["images"] = [url, ...]`` + ``"ok:n=<张数>"``;
    - 页面无可收图(含 JS-SPA 静态空页):``"no_images"``;
    - 追抓失败(SSRF/超时/HTTP 错/网络):``"failed:<原因>"``。

    Args:
        item: 管线条目(鸭子类型:``url``/``metadata`` dict)。
        images_cfg: 品类 ``images:`` 节(schema sidecar)。
        source_extra: 源级 ``extra_params``(``images_detail_fetch`` /
            ``images_detail_max_items`` 平铺覆写)。
        source_name: 条目所属源名(管线侧 = ``item.source``);源级
            ``images_detail_max_items`` 覆写的独立预算以它为键。``None``
            (缺省)时覆写值只在 ``run_state`` 缺省自建形态下生效。
        headers: 源级请求头(:func:`detail_request_headers` 装配;引擎链
            同款 UA/登录凭据语义),逐跳随行;``None`` = 不注入。
        proxy_url: 可选出网代理(``client`` 缺省时自建 client 挂它;
            注入 client 时出口由 client 自带)。
        client: 可注入 ``httpx.AsyncClient``(测试 MockTransport);逐请求
            强制 ``follow_redirects=False``,注入 client 的默认值不参与。
        run_state: 每 run 配额(``detail_remaining``);``None`` = 单条调用
            自建(管线侧应传入共享实例)。

    Returns:
        写入 ``metadata["detail_status"]`` 的标记;未进入返回 ``None``。
    """
    try:
        overrides = _typed_overrides(source_extra)
        effective, accepted_overrides = _merge_overrides(images_cfg, overrides)
        if effective is None or not effective.enabled or not effective.detail_fetch:
            return None
        if _candidate_urls(item.metadata):
            return None  # 列表页已带图的条目不追抓
        item_url = str(getattr(item, "url", "") or "")
        if urlparse(item_url).scheme not in ("http", "https"):
            return None
        if run_state is None:
            run_state = ImageRunState(detail_remaining=effective.detail_max_items)
        # 源级 detail_max_items 覆写 = 该源独立预算(源名键):不吃也不占
        # 共享 detail_remaining;无覆写源照旧吃共享池(先到先得)。
        if "detail_max_items" in accepted_overrides and source_name is not None:
            source_left = run_state.detail_remaining_by_source.get(source_name)
            if source_left is None:
                source_left = effective.detail_max_items
                run_state.detail_remaining_by_source[source_name] = source_left
            if source_left <= 0:
                return None
            run_state.detail_remaining_by_source[source_name] = source_left - 1
        else:
            if run_state.detail_remaining <= 0:
                return None
            run_state.detail_remaining -= 1
        run_state.detail_fetches += 1
        if run_state.detail_fetches > 1:
            await asyncio.sleep(DETAIL_FETCH_INTERVAL_SECONDS)

        own_client = client is None
        http_client = client or httpx.AsyncClient(
            timeout=DETAIL_FETCH_TIMEOUT_SECONDS, follow_redirects=False, proxy=proxy_url
        )
        try:
            try:
                outcome = await asyncio.wait_for(
                    _fetch_detail_html(http_client, item_url, headers=headers),
                    timeout=DETAIL_FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                item.metadata["detail_status"] = "failed:timeout"
                logger.info(
                    "详情页追抓超时(%.0fs/页帽) url=%s", DETAIL_FETCH_TIMEOUT_SECONDS, item_url
                )
                return "failed:timeout"
        finally:
            if own_client:
                await http_client.aclose()
        if isinstance(outcome, _DetailPage):
            urls = _html_image_urls(outcome.html, item_url)
            if not urls:
                item.metadata["detail_status"] = "no_images"
                logger.debug("详情页无可收同域图 url=%s", item_url)
                return "no_images"
            item.metadata["images"] = urls
            status = f"ok:n={len(urls)}"
            item.metadata["detail_status"] = status
            logger.info(
                "详情页追抓收图 url=%s count=%s", item_url, len(urls)
            )
            return status
        item.metadata["detail_status"] = f"failed:{outcome}"
        logger.info("详情页追抓失败(不重试) url=%s reason=%s", item_url, outcome)
        return item.metadata["detail_status"]
    except Exception as exc:  # noqa: BLE001 - 追抓绝不阻管线
        logger.warning(
            "详情页追抓未预期异常(只写标记) url=%s: %s", getattr(item, "url", "?"), exc
        )
        try:
            item.metadata["detail_status"] = "failed:internal_error"
        except Exception:  # noqa: BLE001 - metadata 不可写:除日志外无能为力
            pass
        return "failed:internal_error"


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
_SOURCE_BOOL_OVERRIDES = {
    "images_enabled": "enabled",
    "images_detail_fetch": "detail_fetch",
}
_SOURCE_INT_OVERRIDES = {
    "images_max_images": "max_images",
    "images_min_bytes": "min_bytes",
    "images_detail_max_items": "detail_max_items",
}
_SOURCE_STR_OVERRIDES = {"images_vl": "vl", "images_ocr_engine": "ocr_engine"}


def _typed_overrides(source_extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """源级平铺覆写键的类型规整(装载期不强校验,这里手工规整,非法类型告警忽略)。"""
    if not source_extra:
        return {}
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
    return overrides


def _merge_overrides(
    images_cfg: ImagesConfig | None, overrides: dict[str, Any]
) -> tuple[ImagesConfig | None, set[str]]:
    """覆写合并进品类节;返回 ``(合并后配置, 被采纳的覆写字段名集)``。

    值域越界的覆写(如 ``images_detail_max_items: 100`` 超 le=50——类型
    预过滤只拦 ``>0``,拦不住上界)只弃**本键**(warning),不连坐整批:
    否则同批的 ``images_detail_fetch: true`` 会被一起弃掉,源级 detail
    静默关闭。定位不到具体键、或弃键后二次合并仍失败,才整体回退品类节。
    """
    if images_cfg is None:
        return (None, set())
    if not overrides:
        return (images_cfg, set())
    merged = {**images_cfg.model_dump(), **overrides}
    try:
        return (ImagesConfig.model_validate(merged), set(overrides))
    except Exception as first_exc:  # noqa: BLE001 - 逐键定位越界覆写
        bad_fields: set[str] = set()
        for error in getattr(first_exc, "errors", lambda: [])():
            loc = tuple(error.get("loc", ()))
            if loc:
                bad_fields.add(str(loc[0]))
        dropped = {key for key in overrides if key in bad_fields}
        if not dropped:
            logger.warning(
                "源级 images_* 覆写合并失败(%s),回退品类节: %s", overrides, first_exc
            )
            return (images_cfg, set())
        for key in dropped:
            merged.pop(key, None)
        logger.warning(
            "源级 images_* 覆写值域越界被弃(%s,余键照常生效): %s",
            sorted(dropped),
            first_exc,
        )
        try:
            return (ImagesConfig.model_validate(merged), set(overrides) - dropped)
        except Exception as second_exc:  # noqa: BLE001 - 兜底回退,不阻管线
            logger.warning(
                "源级 images_* 覆写合并失败(%s),回退品类节: %s", overrides, second_exc
            )
            return (images_cfg, set())


def _effective_config(
    images_cfg: ImagesConfig, source_extra: Mapping[str, Any] | None
) -> ImagesConfig | None:
    """品类 images 节 × 源级平铺覆写(``images_*``,``extra="allow"`` 通道)。

    装载期对覆写值不做 schema 强校验(与引擎扩展参数同宽容度):这里手工
    规整类型,非法值告警忽略;值域越界逐键弃(见 :func:`_merge_overrides`);
    ``images_enabled: false`` 整源关闭由调用方按 ``effective.enabled`` 判。
    ``max_per_run`` 是 run 级硬闸,不开放源级覆写。
    """
    return _merge_overrides(images_cfg, _typed_overrides(source_extra))[0]


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


def _persist_images(downloads: list[_Downloaded], images_dir: Path | None) -> list[Path]:
    """把通过全部下载关的图内容寻址落盘(10-03-vision-v2 ``images.persist``)。

    文件名 = 内容 sha256 前 16 hex + 原后缀(下载关已按魔法字节白名单归一
    png/jpg/gif/webp):同图(跨条目/跨源)同文件,天然去重。已存在即跳过
    写(内容寻址,存在即一致);单图落盘失败(OSError:磁盘满/权限)只告警
    跳过,**绝不阻管线**。返回成功落盘(或本就在场)的绝对路径列表。
    """
    if images_dir is None:
        logger.warning("images.persist 开但未提供落图目录,本轮跳过落盘")
        return []
    persisted: list[Path] = []
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("落图目录创建失败(本轮跳过落盘) dir=%s: %s", images_dir, exc)
        return []
    for downloaded in downloads:
        try:
            digest = hashlib.sha256(downloaded.path.read_bytes()).hexdigest()[:16]
            target = images_dir / f"{digest}{downloaded.path.suffix}"
            if not target.exists():
                shutil.copyfile(downloaded.path, target)
            persisted.append(target.resolve())
        except OSError as exc:
            logger.warning(
                "图片落盘失败(跳过该图) src=%s: %s", downloaded.path, exc
            )
    return persisted


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
    images_dir: Path | None = None,
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
        client: 可注入 ``httpx.AsyncClient``(测试 MockTransport);重定向由
            本环逐跳复核,每请求显式 ``follow_redirects=False``——注入
            client 自身的重定向默认值不参与(小修⑤:不受控注入不再能
            绕过逐跳 SSRF 复核)。
        run_state: 每 run 图配额(``max_per_run``);``None`` = 单条调用自建
            (管线侧应传入共享实例)。
        source_extra: 源级 ``extra_params``(``images_*`` 平铺覆写)。
        images_dir: 落图目录(仅 ``persist`` 开时消费;``None`` = 落盘跳过
            并告警)。管线侧传数据根 ``images/``。

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

                # 落图(可选,10-03-vision-v2):内容寻址持久化,产物路径挂
                # metadata.image_files(失败只告警,逐图尽力而为)。
                if effective.persist:
                    persisted = _persist_images(usable, images_dir)
                    if persisted:
                        item.metadata["image_files"] = [str(p) for p in persisted]

                ocr_sem = asyncio.Semaphore(OCR_CONCURRENCY)

                async def _ocr(path: Path) -> list[Any] | None:
                    """一张图的 OCR(to_thread + 信号量;OCRError → None)。

                    返回逐行 :class:`shishi.vision.ocr.OcrLine`(带置信度)
                    —— ``image_ocr`` 拼接与 ``image_ocr_lines`` 投影同源。
                    """
                    async with ocr_sem:
                        try:
                            return await asyncio.to_thread(run_ocr, path, engine)
                        except OCRError as exc:
                            logger.warning("图片 OCR 失败(降级): %s", exc)
                            return None

                per_image_lines = await asyncio.gather(*(_ocr(d.path) for d in usable))
                line_lists = [lines for lines in per_image_lines if lines]
                if not line_lists:
                    if all(lines is None for lines in per_image_lines):
                        item.metadata["image_status"] = "ocr_failed"
                        return "ocr_failed"
                    # 图内确实无字(逐图 OCR 成功但零行):环成功,零 OCR 产物。
                    item.metadata["image_status"] = "ok"
                    return "ok"
                item.metadata["image_ocr"] = IMAGE_OCR_JOIN.join(
                    IMAGE_OCR_JOIN.join(line.text for line in lines) for lines in line_lists
                )
                if effective.persist:
                    item.metadata["image_ocr_lines"] = [
                        {"text": line.text, "conf": line.conf}
                        for lines in line_lists
                        for line in lines
                    ]

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
                                "VL 描述失败(vl_skipped_error,不重试) url=%s: %s: %s",
                                item.url, type(exc).__name__, exc,
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
