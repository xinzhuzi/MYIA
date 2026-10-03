"""Photon 通道:extras 结构化报错壳(无 one-shot HTTP 出站)。

事实探查(10-03-messaging-w3-longtail 组三,蓝本 = Hermes
``plugins/platforms/photon/adapter.py`` + README, NousResearch/Hermes-Agent,
MIT):Photon(Spectrum 平台,含 iMessage)出站**没有**官方 one-shot HTTP
API——蓝本双向都走 ``spectrum-ts`` SDK 的长连 gRPC 流,经一个受监督的
Node sidecar(loopback 端口 + 共享 token)收发;凭据是
``PHOTON_PROJECT_ID``/``PHOTON_PROJECT_SECRET``(app.photon.codes)。MYIA
推送层无 Node 常驻件、不实现 gRPC 流,按 PRD 决策树落 **extras 结构化
报错壳**:配置可加载、发送期 ``dependency_missing`` 如实披露,绝不硬造
出站。

修复路径(文案随错误携带):Node sidecar(spectrum-ts SDK 精确锁版,
``npm ci`` 安装;蓝本 ``hermes photon setup`` 的等价物)随后续 extras
评估;凭据 PHOTON_PROJECT_ID/PHOTON_PROJECT_SECRET。

寻址:``supports_targeting=True``;直达 = 蓝本 PHOTON_HOME_CHANNEL 文档
三形态——Spectrum space id / DM GUID(统一 UUID 形态)或裸 E.164 号码;
无目录发现(:class:`DirectoryDiscoverUnsupported`)。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from myia.push.base import PushSendError, SendContext, TrendAwareChannel
from myia.push.directory import DirectoryDiscoverUnsupported
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget

__all__ = [
    "INSTALL_HINT",
    "PHOTON_TARGET_RE",
    "PhotonChannel",
]

logger = logging.getLogger(__name__)

#: 依赖位:Node sidecar(spectrum-ts SDK;蓝本 npm ci 精确锁版安装)。
INSTALL_HINT = (
    "部署 Node sidecar(spectrum-ts SDK,https://photon.codes/,蓝本 sidecar/"
    "package.json 精确锁版 + npm ci)并配置 PHOTON_PROJECT_ID/PHOTON_PROJECT_SECRET"
)

#: 直达目标形态:UUID(space id/DM GUID)或 E.164 号码(蓝本
#: PHOTON_HOME_CHANNEL 文档三形态;中文别名不匹配 → 回落目录四路径)。
PHOTON_TARGET_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\+[1-9]\d{6,14})$"
)

_UNAVAILABLE = (
    "photon 无 one-shot HTTP 出站(蓝本事实:收发均走 spectrum-ts SDK 的"
    "gRPC 长流,经 Node sidecar,无 POST 即达 API)。MYIA 核心不含该常驻"
    f"sidecar,不硬造出站;修复:{INSTALL_HINT}"
)


class PhotonChannel(TrendAwareChannel):
    """``photon`` channel:发送恒报 ``dependency_missing``(结构化壳)。

    构造契约与其余通道对齐:``pipeline._build_channel`` 对所有非 stdout
    通道下传 ``target``/``template``;壳通道收下备档(legacy target 引用
    与用户模板均不参与发送——发送恒 ``dependency_missing``,不渲染)。
    """

    name = "photon"
    #: 目录寻址已开(可解析寻址;发送能力受 sidecar 制约)。
    supports_targeting = True

    def __init__(
        self, *, target: str | None = None, template: str | None = None
    ) -> None:
        self._target = target
        self._template = template

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """恒抛 ``dependency_missing``(蓝本无 one-shot HTTP 出站,不硬造)。"""
        raise PushSendError("dependency_missing", _UNAVAILABLE)

    async def discover_directory(self) -> list[Any]:
        """photon 无目录发现:会话列表只在 Spectrum SDK 流上可见,MYIA 无 sidecar。"""
        raise DirectoryDiscoverUnsupported(
            "photon 无自动发现:会话列表依赖 Spectrum SDK 长流(蓝本事实),"
            "MYIA 无 sidecar;直达写 photon:<space id/DM GUID/E.164>,"
            "常用会话可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``photon:<UUID space/GUID 或 E.164 号码>`` 不经目录。"""
        value = ref.strip()
        if PHOTON_TARGET_RE.fullmatch(value):
            chat_id = value if value.startswith("+") else value.lower()
            return ChannelTarget(
                platform="photon", chat_id=chat_id, resolved_from=RESOLVED_DIRECT
            )
        return None
