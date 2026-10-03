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
protocol in :mod:`myia.push.base` is the extension point.

Feedback receiving (v0.3, PRD 10-01-v03-feedback-loop, grill Q7 分形态):
:class:`~myia.push.telegram_feedback.TelegramFeedbackPoller` (desktop —
``getUpdates`` polling, no public endpoint needed) and
:mod:`myia.push.feishu_callback` (server/compose — card callback endpoint,
default off, token 鉴权 + 仅内网).

Messaging-platform targeting (v1.2, PRD 10-03-hermes-messaging /
10-03-messaging-core, 蓝本移植自 NousResearch/Hermes-Agent,MIT):通道目录
(:mod:`myia.push.directory`)、对象解析(:mod:`myia.push.targets`)、定向
派发 + 死信账本(:mod:`myia.push.delivery`)。:data:`PLATFORMS` 是平台名 →
支持寻址的通道类的 dict 注册表(core 只交付空表与 fake 测试通道;feishu/
telegram 在各自子任务登记)。蓝本对照表见任务档 prd。
"""

from __future__ import annotations

from myia.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    Channel,
    PushSendError,
    SendContext,
    SendReport,
    also_seen_list,
    item_view,
)
from myia.push.delivery import (
    DeliveryLedger,
    classify_dead_error,
    send_batch_to_targets,
)
from myia.push.digest import DigestAggregator, PendingDigestItem, send_immediate
from myia.push.directory import ChannelDirectory, ChannelEntry
from myia.push.feishu_card import (
    API_URL,
    DEFAULT_TOKEN_ENV_REF,
    FeishuCardChannel,
    build_card,
    build_markdown_card,
    card_title,
)
from myia.push.route import (
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
from myia.push.stdout import StdoutChannel
from myia.push.targets import (
    ChannelTarget,
    TargetResolveError,
    parse_spec,
    resolve_all,
    resolve_target,
)
from myia.push.telegram import TelegramChannel
from myia.push.telegram_feedback import (
    CALLBACK_PREFIX,
    DEFAULT_POLL_INTERVAL_SECONDS,
    PollResult,
    TelegramCallback,
    TelegramFeedbackError,
    TelegramFeedbackPoller,
    parse_callback_data,
)
from myia.push.templates import (
    STOCKS_EXAMPLE_TEMPLATE,
    TemplateRenderer,
    TemplateRenderError,
)
from myia.push.webhook import WebhookChannel

#: Channel-name → implementation registry (dict registry + constructor
#: injection; no factory inheritance). All four channels are fully
#: implemented (telegram/webhook landed in v0.2).
CHANNELS: dict[str, type] = {
    "feishu_card": FeishuCardChannel,
    "telegram": TelegramChannel,
    "webhook": WebhookChannel,
    "stdout": StdoutChannel,
}

#: Platform-name → targeting-capable channel class(10-03-messaging-core
#: design D1:dict 注册表,与 :data:`CHANNELS` 并排;不内置真实平台)。
#: 平台子任务接入时在此登记,例如 ``{"feishu": FeishuCardChannel, ...}``;
#: 登记类可提供 ``parse_direct_ref``(直达解析钩子)与实例方法
#: ``discover_directory``(目录发现)——见 base.Channel 协议 docstring。
PLATFORMS: dict[str, type] = {}

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
    "PendingDigestItem",
    "PollResult",
    "STOCKS_EXAMPLE_TEMPLATE",
    "Channel",
    "DigestAggregator",
    "FeishuCardChannel",
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
