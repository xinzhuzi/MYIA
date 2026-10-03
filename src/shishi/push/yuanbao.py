"""元宝(yuanbao)通道:extras 结构化报错壳(无 one-shot HTTP 出站)。

事实探查(10-03-messaging-w3-longtail 组三,蓝本 = Hermes
``gateway/platforms/yuanbao.py``,NousResearch/Hermes-Agent,MIT):元宝出站
**没有**官方 one-shot HTTP API——蓝本是常驻 WebSocket 网关客户端
(sign-token → WS 连接 → AUTH_BIND → ping/pong 心跳 → 出站帧),依赖
``websockets`` 包与元宝开放平台凭据(``YUANBAO_APP_ID``/``YUANBAO_APP_SECRET``,
可选 ``YUANBAO_BOT_ID``/``YUANBAO_WS_URL``)。MYIA 推送层是单发 one-shot
契约(无常驻连接、无心跳、无重连),按 PRD 决策树落 **extras 结构化报错
壳**:配置可加载、发送期 ``dependency_missing`` 如实披露,绝不硬造一个
「POST 即达」的假出站(元宝网关不认)。

修复路径(文案随错误携带):
1. 常驻 WS 客户端随后续 extras 提供(蓝本 ``gateway/platforms/yuanbao.py``
   的 AUTH_BIND 会话语义),安装依赖位 ``uv add websockets``;
2. 或改用本组其余 one-shot 通道(email/sms/homeassistant/ntfy/webhook)。

寻址:``supports_targeting=True``(可配置、可解析,只是发送不可用);
直达 = 蓝本 chat_id 三形态 ``direct:{account_id}`` / ``group:{group_code}``
/ 裸 account_id(yuanbao_tools 文档同款);无目录发现
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
    "YUANBAO_REF_RE",
    "YuanbaoChannel",
]

logger = logging.getLogger(__name__)

#: 依赖位:常驻 WS 客户端的 Python 依赖(蓝本 ``websockets``);开放平台
#: 凭据在蓝本侧配置(YUANBAO_APP_ID/APP_SECRET)。
INSTALL_HINT = (
    "uv add websockets  # 常驻 WS 网关客户端依赖;凭据 YUANBAO_APP_ID/YUANBAO_APP_SECRET"
    "(蓝本 gateway/platforms/yuanbao.py 的 AUTH_BIND 会话,extras 待提供)"
)

#: 直达 chat_id 形态(蓝本 yuanbao_tools 文档:``direct:{account_id}`` /
#: ``group:{group_code}`` / 裸 account_id;ASCII 段保守字符集,中文别名
#: 不匹配 → 回落目录四路径)。
YUANBAO_REF_RE = re.compile(r"^(?:direct:|group:)?[A-Za-z0-9_-]{1,64}$")

_UNAVAILABLE = (
    "yuanbao 无 one-shot HTTP 出站(蓝本事实:出站走常驻 WebSocket 网关,"
    "sign-token → AUTH_BIND → 心跳,无 POST 即达 API)。MYIA 核心不含该常驻"
    f"客户端,不硬造出站;修复:安装/接入见 {INSTALL_HINT}"
)


class YuanbaoChannel(TrendAwareChannel):
    """``yuanbao`` channel:发送恒报 ``dependency_missing``(结构化壳)。

    配置加载/寻址解析照常(别名+直达),发送期如实披露出站不可用——
    UI 灰态可探测,不假装可用。构造契约与其余通道对齐:
    ``pipeline._build_channel`` 对所有非 stdout 通道下传 ``target``/
    ``template``;壳通道收下备档(均不参与发送——发送恒
    ``dependency_missing``,不渲染)。
    """

    name = "yuanbao"
    #: 目录寻址已开(可解析寻址;发送能力受 extras 制约)。
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
        """yuanbao 无目录发现:会话列表只随常驻 WS 在线可查,MYIA 无常驻端。"""
        raise DirectoryDiscoverUnsupported(
            "yuanbao 无自动发现:会话列表依赖常驻 WS 在线(蓝本事实),"
            "MYIA 无常驻端;直达写 yuanbao:<direct:xx/group:xx/裸 id>,"
            "常用会话可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``direct:{account_id}`` / ``group:{group_code}`` / 裸 id 不经目录。"""
        value = ref.strip()
        if YUANBAO_REF_RE.fullmatch(value):
            return ChannelTarget(
                platform="yuanbao", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
