"""Raft 通道:extras 结构化报错壳(无 one-shot HTTP 出站)。

事实探查(10-03-messaging-w3-longtail 组三,蓝本 = Hermes
``plugins/platforms/raft/adapter.py``,botiverse/Hermes 插件,MIT):Raft
工作区出站**没有**官方 one-shot HTTP API——蓝本架构是本地「唤醒桥」
(loopback HTTP 端点只收 content-free 唤醒提示)+ **Raft CLI 子进程**读写
消息,适配器「从不触碰消息正文与投递游标」;凭据/标识 = ``RAFT_PROFILE``
(工作区的 agent profile slug)。MYIA 推送层不 vendor Raft CLI、不实现
工作区游标语义,按 PRD 决策树落 **extras 结构化报错壳**:配置可加载、
发送期 ``dependency_missing`` 如实披露,绝不硬造出站。

修复路径(文案随错误携带):安装 Raft CLI(蓝本 install_hint:
https://raft.build)并在工作区登记 agent profile(RAFT_PROFILE);CLI
桥接随后续任务评估。

寻址:``supports_targeting=True``;直达解析**不设**——蓝本 chat_id 是
运行期会话标识(runtime session,形态无公开成文约束),MYIA 不猜形态,
寻址一律走目录别名文件登记(名称即别名,目录四路径精确名/唯一前缀可用)。
无目录发现(:class:`DirectoryDiscoverUnsupported`)。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import DirectoryDiscoverUnsupported

__all__ = [
    "INSTALL_HINT",
    "RaftChannel",
]

logger = logging.getLogger(__name__)

#: 依赖位:Raft CLI(蓝本 install_hint 原文:https://raft.build)+ 工作区 profile。
INSTALL_HINT = (
    "安装 Raft CLI(https://raft.build)并在 Raft 工作区登记 agent profile"
    "(RAFT_PROFILE;蓝本 plugins/platforms/raft 的本地唤醒桥 + CLI 读写)"
)

_UNAVAILABLE = (
    "raft 无 one-shot HTTP 出站(蓝本事实:读写经 Raft CLI 子进程 + 本地唤醒桥,"
    "无 POST 即达 API)。MYIA 核心不含该 CLI 桥接,不硬造出站;"
    f"修复:{INSTALL_HINT}"
)


class RaftChannel(TrendAwareChannel):
    """``raft`` channel:发送恒报 ``dependency_missing``(结构化壳)。

    直达解析钩子有意缺席:蓝本 chat_id 是运行期会话标识,形态无公开成文
    约束,MYIA 不猜——寻址一律走别名文件登记(目录精确名/唯一前缀)。

    构造契约与其余通道对齐:``pipeline._build_channel`` 对所有非 stdout
    通道下传 ``target``/``template``;壳通道收下备档(legacy target 引用
    与用户模板均不参与发送——发送恒 ``dependency_missing``,不渲染)。
    """

    name = "raft"
    #: 目录寻址已开(别名登记寻址;发送能力受 CLI 桥接制约)。
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
        """raft 无目录发现:工作区频道列表只在 Raft CLI 侧,MYIA 无 CLI 桥。"""
        raise DirectoryDiscoverUnsupported(
            "raft 无自动发现:工作区频道列表依赖 Raft CLI(蓝本事实),"
            "MYIA 无 CLI 桥接;目标一律用别名文件登记(无直达形态)"
        )
