"""BlueBubbles 通道:extras 壳(PRD 10-03-messaging-w3-longtail Requirements 3)。

硬约束(蓝本事实):BlueBubbles 出站依赖 **BlueBubbles 服务端**(自建
iMessage 桥,常驻 macOS 并接管 Messages.app;蓝本 Hermes
``gateway/platforms/bluebubbles.py``,NousResearch/Hermes-Agent,MIT——出站经
``POST {server}/api/v1/message/text``,鉴权 ``?password=`` 查询参数,body
``{"chatGuid", "tempGuid", "message"}``,chat_id 先经 chat 列表 API 解析成
GUID)。服务端是外部系统级依赖,不是 pip 包——按 PRD R3「需要服务端的进
extras 并结构化报错,不进核心」,本模块是**extras 壳**:

- ``send()`` 立即抛 ``PushSendError("dependency_missing", …)`` 并附安装
  命令(vision/ocr.py ``dependency_missing`` 同款范式):装 extras
  (``myia[bluebubbles]``,集成步在 pyproject 定义)+ 部署 BlueBubbles
  服务端;依赖门先于凭据解析(装不出的通道没有解析凭据的意义);
- 寻址面照常接线,extras 落地即可用:``supports_targeting=True``、
  ``context.target`` 优先 / legacy ``target`` 引用、直达解析
  ``iMessage;…``/``SMS;…`` chat GUID 形态与 ``+`` 手机号(蓝本
  ``chatGuid``/``_ADDRESS_RE`` 形态)、目录无自动发现(常驻服务端才有
  chat 列表,extras 壳不含)抛
  :class:`~myia.push.directory.DirectoryDiscoverUnsupported`;
- 真实发送路(REST + password,纯 ``httpx`` 可达)待 extras 落地批次实装
  ——壳先钉住注册表/UI 卡/凭据指南的契约面。

凭据安全基线同其余通道:壳只声明 ``target`` 引用,不解析不落值。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from myia.push.base import PushSendError, SendContext, TrendAwareChannel
from myia.push.directory import DirectoryDiscoverUnsupported
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget

__all__ = [
    "CHAT_GUID_RE",
    "DEFAULT_TARGET_ENV_REF",
    "INSTALL_COMMAND",
    "PHONE_RE",
    "BlueBubblesChannel",
]

logger = logging.getLogger(__name__)

#: extras 安装命令(集成步在 pyproject 定义 ``bluebubbles`` extras 组)+
#: 服务端指引。
INSTALL_COMMAND = (
    "pip install 'myia[bluebubbles]'  # 或 uv add 'myia[bluebubbles]';"
    "并需部署 BlueBubbles 服务端(https://bluebubbles.app,常驻 macOS)"
)
#: legacy target 引用(chat GUID 或手机号;群/邮箱走目录别名登记)。
DEFAULT_TARGET_ENV_REF = "env:BLUEBUBBLES_CHAT"
#: 直达 chat GUID 形态(蓝本 chatGuid:``iMessage;…``/``SMS;…`` sigil 起头,
#: 分号分段;群 GUID 含 ``chat…``、DM 为服务格式 + 地址段)。
CHAT_GUID_RE = re.compile(r"^(iMessage|SMS);[-A-Za-z0-9.;+]+$")
#: 直达手机号形态(蓝本 ``_ADDRESS_RE = ^\+\d+``;新会话可按地址直开)。
PHONE_RE = re.compile(r"^\+[0-9]{7,15}$")


class BlueBubblesChannel(TrendAwareChannel):
    """``bluebubbles`` channel:extras 壳——发送即结构化报错(PRD R3)。

    Args:
        target: legacy target 引用(存储不解析;extras 实装后生效)。
        template: 可选用户模板(存储不渲染;extras 实装后生效)。

    Raises:
        PushSendError: ``send()`` 恒抛 ``dependency_missing``(附
            :data:`INSTALL_COMMAND`)——本批次无真实发送路,绝不假装可用。
    """

    name = "bluebubbles"
    #: 目录寻址面已接线(直达/别名字典语义完整);发送待 extras 实装。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
    ) -> None:
        self._target = target
        self._template = template

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Extras 壳:依赖门先于一切(send 即报,绝不发任何请求)。"""
        raise PushSendError(
            "dependency_missing",
            f"bluebubbles 通道需 extras 未装(PRD R3:出站依赖 BlueBubbles "
            f"服务端,不进核心依赖):请先执行 {INSTALL_COMMAND}",
        )

    async def discover_directory(self) -> list[Any]:
        """BlueBubbles 目录需常驻服务端的 chat 列表 API:结构化说明。"""
        raise DirectoryDiscoverUnsupported(
            "bluebubbles 无自动发现(需常驻服务端的 chat 列表 API,extras 壳"
            "不含):直达写 bluebubbles:iMessage;… / SMS;… 或 +手机号,"
            "常用地名可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:chat GUID(``iMessage;…``/``SMS;…``)与 ``+`` 手机号不经目录。

        邮箱/显示名不设直达——与目录别名撞形,经别名登记;其余形态返回
        None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if CHAT_GUID_RE.fullmatch(value) or PHONE_RE.fullmatch(value):
            return ChannelTarget(
                platform="bluebubbles", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
