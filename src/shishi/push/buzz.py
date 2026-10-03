"""Buzz 通道:extras 结构化报错壳(无 one-shot HTTP 出站)。

事实探查(10-03-messaging-w3-longtail 组三,蓝本 = Hermes
``plugins/platforms/buzz/adapter.py``,NousResearch/Hermes-Agent,MIT):
Buzz(Block 开源、Nostr 协作平台)出站**没有**官方 one-shot HTTP API——
蓝本的中继读写全部经 ``buzz`` CLI 二进制子进程(JSON in/out),身份 =
Nostr 私钥(``BUZZ_PRIVATE_KEY``,nsec/hex),中继地址 ``BUZZ_RELAY_URL``。
MYIA 推送层不 vendor CLI、不实现 Nostr 中继协议(签名/广播属常驻客户端
职责),按 PRD 决策树落 **extras 结构化报错壳**:配置可加载、发送期
``dependency_missing`` 如实披露,绝不硬造出站。

修复路径(文案随错误携带):安装 buzz CLI(蓝本 install_hint:
https://github.com/block/buzz,PATH 或 ``BUZZ_CLI_PATH``),配置中继 URL
与 Nostr 私钥;CLI 桥接(weixin → Hermes CLI 同款 runner 形态)随后续
任务评估。

寻址:``supports_targeting=True``;直达 = 频道 UUID(蓝本
``BUZZ_CHANNELS``/``BUZZ_HOME_CHANNEL`` 同一形态);无目录发现
(:class:`DirectoryDiscoverUnsupported`)。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget

__all__ = [
    "INSTALL_HINT",
    "BUZZ_CHANNEL_RE",
    "BuzzChannel",
]

logger = logging.getLogger(__name__)

#: 依赖位:buzz CLI 二进制(蓝本 install_hint 原文)+ Nostr 私钥。
INSTALL_HINT = (
    "安装 buzz CLI(https://github.com/block/buzz,置于 PATH 或配置 BUZZ_CLI_PATH)"
    "并配置 BUZZ_RELAY_URL/BUZZ_PRIVATE_KEY(蓝本 plugins/platforms/buzz)"
)

#: 直达频道形态:UUID(蓝本 ``BUZZ_CHANNELS`` 的 channel UUID 同一形态)。
BUZZ_CHANNEL_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_UNAVAILABLE = (
    "buzz 无 one-shot HTTP 出站(蓝本事实:中继读写经 buzz CLI 子进程 + Nostr 签名,"
    "无 POST 即达 API)。MYIA 核心不含该 CLI 桥接,不硬造出站;"
    f"修复:{INSTALL_HINT}"
)


class BuzzChannel(TrendAwareChannel):
    """``buzz`` channel:发送恒报 ``dependency_missing``(结构化壳)。

    构造契约与其余通道对齐:``pipeline._build_channel`` 对所有非 stdout
    通道下传 ``target``/``template``;壳通道收下备档(legacy target 引用
    与用户模板均不参与发送——发送恒 ``dependency_missing``,不渲染)。
    """

    name = "buzz"
    #: 目录寻址已开(可解析寻址;发送能力受 CLI 桥接制约)。
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
        """buzz 无目录发现:频道列表只在 buzz CLI 侧(``buzz`` 自查),MYIA 无 CLI 桥。"""
        raise DirectoryDiscoverUnsupported(
            "buzz 无自动发现:频道列表依赖 buzz CLI(蓝本事实),MYIA 无 CLI 桥接;"
            "直达写 buzz:<频道 UUID>,常用频道可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``buzz:<频道 UUID>`` 不经目录;其余(中文别名等)回落目录。"""
        value = ref.strip()
        if BUZZ_CHANNEL_RE.fullmatch(value):
            return ChannelTarget(
                platform="buzz", chat_id=value.lower(), resolved_from=RESOLVED_DIRECT
            )
        return None
