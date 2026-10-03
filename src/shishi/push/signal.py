"""Signal 通道:extras 壳(PRD 10-03-messaging-w3-longtail Requirements 3)。

硬约束(蓝本事实):Signal 出站唯一合规路径是 **signal-cli 守护进程**
(``signal-cli daemon --http …`` HTTP 模式;蓝本 Hermes
``gateway/platforms/signal.py``,NousResearch/Hermes-Agent,MIT——出站经
``POST {SIGNAL_HTTP_URL}/api/v1/rpc`` 的 JSON-RPC 2.0,``send`` 方法参数
``{"account", "message", "recipient"/"groupId"}``)。守护进程是外部系统级
依赖,不是 pip 包——按 PRD R3 「需要外部守护进程的进 extras 并结构化
报错,不进核心」,本模块是**extras 壳**:

- ``send()`` 立即抛 ``PushSendError("dependency_missing", …)`` 并附安装
  命令(vision/ocr.py ``dependency_missing`` 同款范式):装 extras
  (``shishi[signal]``,集成步在 pyproject 定义) + 部署 signal-cli 守护进程;
  依赖门先于凭据解析(装不出的通道没有解析凭据的意义);
- 寻址面照常接线,extras 落地即可用:``supports_targeting=True``、
  ``context.target`` 优先 / legacy ``target`` 引用、直达解析 ``+手机号``
  (蓝本 recipient 形态;群 id 是 base64,与别名撞形,经目录登记)、目录
  无自动发现(signal-cli 无列表命令,蓝本事实)抛
  :class:`~shishi.push.directory.DirectoryDiscoverUnsupported`;
- 真实发送路(JSON-RPC over HTTP,纯 ``httpx`` 可达)待 extras 落地批次
  实装——壳先钉住注册表/UI 卡/凭据指南的契约面。

凭据安全基线同其余通道:壳只声明 ``target`` 引用,不解析不落值。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget

__all__ = [
    "DEFAULT_TARGET_ENV_REF",
    "INSTALL_COMMAND",
    "PHONE_RE",
    "SignalChannel",
]

logger = logging.getLogger(__name__)

#: extras 安装命令(集成步在 pyproject 定义 ``signal`` extras 组)+ 守护进程指引。
INSTALL_COMMAND = (
    "pip install 'shishi[signal]'  # 或 uv add 'shishi[signal]';"
    "并需部署 signal-cli 守护进程(signal-cli daemon --http 模式,"
    "https://github.com/AsamK/signal-cli)"
)
#: legacy target 引用(``+8613…`` 手机号;群 id 走目录别名登记)。
DEFAULT_TARGET_ENV_REF = "env:SIGNAL_CHAT"
#: 直达 recipient 形态:E.164 手机号(可选 ``+`` 前缀,7-15 位数字;蓝本
#: ``recipient`` 参数即手机号/groupId,groupId 为 base64 不设直达)。
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class SignalChannel(TrendAwareChannel):
    """``signal`` channel:extras 壳——发送即结构化报错(PRD R3)。

    Args:
        target: legacy target 引用(存储不解析;extras 实装后生效)。
        template: 可选用户模板(存储不渲染;extras 实装后生效)。

    Raises:
        PushSendError: ``send()`` 恒抛 ``dependency_missing``(附
            :data:`INSTALL_COMMAND`)——本批次无真实发送路,绝不假装可用。
    """

    name = "signal"
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
            f"signal 通道需 extras 未装(PRD R3:出站依赖 signal-cli 守护进程,"
            f"不进核心依赖):请先执行 {INSTALL_COMMAND}",
        )

    async def discover_directory(self) -> list[Any]:
        """signal-cli 无列表命令(蓝本事实):结构化说明,而非假装刷新。"""
        raise DirectoryDiscoverUnsupported(
            "signal 无自动发现(蓝本事实:signal-cli 无列表命令;且通道本体"
            "为 extras 壳):直达写 signal:+手机号,群可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``+8613…`` 手机号(E.164,可选 ``+``)不经目录。

        群 id(base64)与显示名不设直达——与目录别名撞形,经别名登记。
        其余形态返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if PHONE_RE.fullmatch(value):
            return ChannelTarget(
                platform="signal", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
