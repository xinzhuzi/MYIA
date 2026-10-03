"""Push layer: threshold routing + digest aggregation + channels.

Routing semantics (v1.7 production 定案 + grill Q1, PRD v01-push-feishu-route):

- ``push.route[]`` ``when`` expressions share the classify whitelist-AST
  evaluator (never ``eval``); rules apply in declared order, first match wins.
- v0.1 has no LLM score yet: the category default mapping decides
  (羊毛/节点/代买 → immediate;AI资讯/服务器/token/信用卡/渠道 → digest;未命中
  → 保守 digest,不打扰)。YAML overrides via non-score route rules.
- v0.2 interface: once enrich backfills a scalar ``score``, score-threshold
  rules wake up and take precedence over the category mapping (score >= 8 →
  immediate,>= 5 → digest,< 5 → archive);无 route 且有 score → immediate
  (兼容简单用法)。
- digest items aggregate per AM/PM slot into one card per channel; slot
  suppression shares the AM/PM dedup registry (local 12:00 boundary, grill
  Q3) — routing answers 「推不推」, the registry answers 「发没发过」.

Channels: ``feishu_card`` and ``stdout`` are v0.1; v0.2 adds ``telegram``
(Bot API sendMessage, 4096-char auto-split) and ``webhook`` (JSON POST with
credential-ref endpoint + configurable timeout/retry). The :class:`Channel`
protocol in :mod:`shishi.push.base` is the extension point.

Feedback receiving (v0.3, PRD 10-01-v03-feedback-loop, grill Q7 分形态):
:class:`~shishi.push.telegram_feedback.TelegramFeedbackPoller` (desktop —
``getUpdates`` polling, no public endpoint needed) and
:mod:`shishi.push.feishu_callback` (server/compose — card callback endpoint,
default off, token 鉴权 + 仅内网).

 Messaging-platform targeting (v1.2, PRD 10-03-hermes-messaging /
 10-03-messaging-core, 蓝图移植自 NousResearch/Hermes-Agent,MIT):通道目录
 (:mod:`shishi.push.directory`)、对象解析(:mod:`shishi.push.targets`)、定向
 派发 + 死信账本(:mod:`shishi.push.delivery`)。:data:`PLATFORMS` 是平台名 →
 支持寻址的通道类的 dict 注册表(core 交付空表契约;feishu 已于
 10-03-messaging-feishu 登记,telegram 于 10-03-messaging-telegram 登记
 ——直达解析 + 定向发送,目录为被动积累)。W3 长尾伞
 (10-03-messaging-w3-longtail)一次性登记 22 家:CHANNELS 30 条、
 PLATFORMS 28 条(仅 simplex 有目录发现;**6 家 extras/结构化壳**——
 yuanbao/buzz/photon/raft 无 one-shot HTTP 出站,signal 需 signal-cli
 守护进程、bluebubbles 需服务端——发送期如实 ``dependency_missing``,
 其余 16 家均有 one-shot HTTP 出站)。蓝本对照表见任务档 prd。
"""

from __future__ import annotations

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    Channel,
    PushSendError,
    SendContext,
    SendReport,
    also_seen_list,
    item_view,
)
from shishi.push.delivery import (
    DeliveryLedger,
    classify_dead_error,
    send_batch_to_targets,
)
from shishi.push.digest import DigestAggregator, PendingDigestItem, send_immediate
from shishi.push.directory import (
    ChannelDirectory,
    ChannelEntry,
    DirectoryDiscoverUnsupported,
)
from shishi.push.dingtalk import DingTalkChannel
from shishi.push.ntfy import NtfyChannel
from shishi.push.feishu_card import (
    API_URL,
    DEFAULT_TOKEN_ENV_REF,
    FeishuCardChannel,
    build_card,
    build_markdown_card,
    card_title,
)
from shishi.push.route import (
    CATEGORY_DEFAULT_ROUTES,
    DEFAULT_ROUTE_NO_RULES,
    DEFAULT_ROUTE_UNMATCHED,
    SCORE_FIELD,
    RouteBuckets,
    RouteConfigError,
    RouteDecision,
    RouteRule,
    resolve_route,
    route,
    routes_from_config,
)
from shishi.push.stdout import StdoutChannel
from shishi.push.targets import (
    ChannelTarget,
    TargetResolveError,
    parse_spec,
    resolve_all,
    resolve_target,
)
from shishi.push.telegram import TelegramChannel
from shishi.push.telegram_feedback import (
    CALLBACK_PREFIX,
    DEFAULT_POLL_INTERVAL_SECONDS,
    PollResult,
    TelegramCallback,
    TelegramFeedbackError,
    TelegramFeedbackPoller,
    parse_callback_data,
)
from shishi.push.wecom import WecomChannel
from shishi.push.weixin import WeixinChannel
# W3 长尾三组(10-03-messaging-w3-longtail 终局接线:组一 Slack 系 8 家、
# 组二 Matrix 系 8 家、组三长尾 6 家;集成会话一处收口注册)。
from shishi.push.slack import SlackChannel
from shishi.push.discord import DiscordChannel
from shishi.push.whatsapp_cloud import WhatsAppCloudChannel
from shishi.push.line import LineChannel
from shishi.push.qqbot import QQBotChannel
from shishi.push.google_chat import GoogleChatChannel
from shishi.push.teams import TeamsChannel
from shishi.push.msgraph_webhook import MSGraphWebhookChannel
from shishi.push.matrix import MatrixChannel
from shishi.push.mattermost import MattermostChannel
from shishi.push.irc import IrcChannel
from shishi.push.simplex import SimplexChannel
from shishi.push.signal import SignalChannel
from shishi.push.bluebubbles import BlueBubblesChannel
from shishi.push.email import EmailChannel
from shishi.push.sms import SmsChannel
from shishi.push.homeassistant import HomeAssistantChannel
from shishi.push.a2a import A2aChannel
from shishi.push.yuanbao import YuanbaoChannel
from shishi.push.buzz import BuzzChannel
from shishi.push.photon import PhotonChannel
from shishi.push.raft import RaftChannel
from shishi.push.templates import (
    STOCKS_EXAMPLE_TEMPLATE,
    TemplateRenderer,
    TemplateRenderError,
)
from shishi.push.webhook import WebhookChannel

#: Channel-name → implementation registry (dict registry + constructor
#: injection; no factory inheritance). All four channels are fully
#: implemented (telegram/webhook landed in v0.2); ntfy/dingtalk/wecom land
#: with 10-03-messaging-w2-platforms(蓝本形态:one-shot POST / 静态 webhook /
#: 自建应用 token)。
#: W3 长尾 22 家(10-03-messaging-w3-longtail):组一 Slack 系 8 家 + 组二
#: Matrix 系 8 家 + 组三长尾 6 家。其中 **6 家为壳通道**(配置可加载、
#: 寻址可解析,发送期如实 ``dependency_missing``,不硬造出站):
#: yuanbao/buzz/photon/raft 无 one-shot HTTP 出站;signal 依赖 signal-cli
#: 守护进程、bluebubbles 依赖 BlueBubbles 服务端(PRD R3 extras 门)。
#: 其余 16 家均有 one-shot HTTP 出站(逐家专测见
#: tests/test_messaging_<平台>.py)。
_W3_LONGTAIL_CHANNELS: dict[str, type] = {
    "slack": SlackChannel,
    "discord": DiscordChannel,
    "whatsapp_cloud": WhatsAppCloudChannel,
    "line": LineChannel,
    "qqbot": QQBotChannel,
    "google_chat": GoogleChatChannel,
    "teams": TeamsChannel,
    "msgraph_webhook": MSGraphWebhookChannel,
    "matrix": MatrixChannel,
    "mattermost": MattermostChannel,
    "irc": IrcChannel,
    "simplex": SimplexChannel,
    "signal": SignalChannel,
    "bluebubbles": BlueBubblesChannel,
    "email": EmailChannel,
    "sms": SmsChannel,
    "homeassistant": HomeAssistantChannel,
    "a2a": A2aChannel,
    "yuanbao": YuanbaoChannel,
    "buzz": BuzzChannel,
    "photon": PhotonChannel,
    "raft": RaftChannel,
}

#: Channel-name → implementation registry (dict registry + constructor
#: injection; no factory inheritance). All four channels are fully
#: implemented (telegram/webhook landed in v0.2); ntfy/dingtalk/wecom land
#: with 10-03-messaging-w2-platforms(蓝本形态:one-shot POST / 静态 webhook /
#: 自建应用 token)。
CHANNELS: dict[str, type] = {
    "feishu_card": FeishuCardChannel,
    "telegram": TelegramChannel,
    "ntfy": NtfyChannel,
    "dingtalk": DingTalkChannel,
    "wecom": WecomChannel,
    "weixin": WeixinChannel,
    "webhook": WebhookChannel,
    "stdout": StdoutChannel,
    **_W3_LONGTAIL_CHANNELS,
}

#: Platform-name → targeting-capable channel class(10-03-messaging-core
#: design D1:dict 注册表,与 :data:`CHANNELS` 并排)。
#: 登记类提供 ``parse_direct_ref``(直达解析钩子)与实例方法
#: ``discover_directory``(目录发现)——见 base.Channel 协议 docstring。
#: feishu 于 10-03-messaging-feishu 登记(目录发现 im/v1/chats + 定向发送);
#: telegram 于 10-03-messaging-telegram 登记(直达解析数字 id/@username +
#: 定向发送;Bot API 无目录发现,被动积累走 telegram_feedback 的 on_chat
#: sink,不入本表发现路径)。ntfy/dingtalk/wecom 于
#: 10-03-messaging-w2-platforms 登记(蓝本事实:三平台零目录发现,抛
#: :class:`~shishi.push.directory.DirectoryDiscoverUnsupported`;条目唯一来源
#: = 别名手工登记 + 直达 id)。weixin 于 10-03-messaging-weixin-bridge
#: 登记(可选桥接:出站经本机 Hermes CLI,直达 peer id,亦无目录发现)。
PLATFORMS: dict[str, type] = {
    "feishu": FeishuCardChannel,
    "telegram": TelegramChannel,
    "ntfy": NtfyChannel,
    "dingtalk": DingTalkChannel,
    "wecom": WecomChannel,
    "weixin": WeixinChannel,
    # W3 长尾 22 家:全部 supports_targeting=True(平台名 = 通道名,与各适配器
    # parse_direct_ref 的 platform= 字面一致)。仅 simplex 有目录发现;其余
    # 无发现(DirectoryDiscoverUnsupported),条目来源 = 直达 id + 别名手工
    # 登记。raft 有意不设 parse_direct_ref(蓝本 chat_id 形态无公开成文
    # 约束,寻址全走别名;targets._parse_direct_ref 的 getattr 守卫容忍)。
    "slack": SlackChannel,
    "discord": DiscordChannel,
    "whatsapp_cloud": WhatsAppCloudChannel,
    "line": LineChannel,
    "qqbot": QQBotChannel,
    "google_chat": GoogleChatChannel,
    "teams": TeamsChannel,
    "msgraph_webhook": MSGraphWebhookChannel,
    "matrix": MatrixChannel,
    "mattermost": MattermostChannel,
    "irc": IrcChannel,
    "simplex": SimplexChannel,
    "signal": SignalChannel,
    "bluebubbles": BlueBubblesChannel,
    "email": EmailChannel,
    "sms": SmsChannel,
    "homeassistant": HomeAssistantChannel,
    "a2a": A2aChannel,
    "yuanbao": YuanbaoChannel,
    "buzz": BuzzChannel,
    "photon": PhotonChannel,
    "raft": RaftChannel,
}

__all__ = [
    "API_URL",
    "CALLBACK_PREFIX",
    "CATEGORY_DEFAULT_ROUTES",
    "CHANNELS",
    "PLATFORMS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_ROUTE_NO_RULES",
    "DEFAULT_ROUTE_UNMATCHED",
    "DEFAULT_SEND_TIMEOUT_SECONDS",
    "DEFAULT_TOKEN_ENV_REF",
    "SCORE_FIELD",
    "ChannelDirectory",
    "ChannelEntry",
    "ChannelTarget",
    "DeliveryLedger",
    "DirectoryDiscoverUnsupported",
    "PendingDigestItem",
    "PollResult",
    "STOCKS_EXAMPLE_TEMPLATE",
    "Channel",
    "DigestAggregator",
    "DingTalkChannel",
    "FeishuCardChannel",
    "NtfyChannel",
    "PushSendError",
    "RouteBuckets",
    "RouteConfigError",
    "RouteDecision",
    "RouteRule",
    "SendContext",
    "SendReport",
    "StdoutChannel",
    "TargetResolveError",
    "TelegramCallback",
    "TelegramChannel",
    "TelegramFeedbackError",
    "TelegramFeedbackPoller",
    "TemplateRenderError",
    "TemplateRenderer",
    "WebhookChannel",
    "WecomChannel",
    "WeixinChannel",
    # W3 长尾(10-03-messaging-w3-longtail)
    "A2aChannel",
    "BlueBubblesChannel",
    "BuzzChannel",
    "DiscordChannel",
    "EmailChannel",
    "GoogleChatChannel",
    "HomeAssistantChannel",
    "IrcChannel",
    "LineChannel",
    "MatrixChannel",
    "MattermostChannel",
    "MSGraphWebhookChannel",
    "PhotonChannel",
    "QQBotChannel",
    "RaftChannel",
    "SignalChannel",
    "SimplexChannel",
    "SlackChannel",
    "SmsChannel",
    "TeamsChannel",
    "WhatsAppCloudChannel",
    "YuanbaoChannel",
    "also_seen_list",
    "build_card",
    "build_markdown_card",
    "card_title",
    "classify_dead_error",
    "item_view",
    "parse_callback_data",
    "parse_spec",
    "resolve_all",
    "resolve_route",
    "resolve_target",
    "route",
    "routes_from_config",
    "send_batch_to_targets",
    "send_immediate",
]
