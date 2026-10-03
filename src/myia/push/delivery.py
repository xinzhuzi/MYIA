"""Targeted delivery dispatch + dead-target ledger.

MYIA 移植重写自 Hermes ``gateway/delivery.py``(派发循环/按对象投递/部分
失败隔离)与 ``gateway/dead_targets.py``(死信账本),错误分类表取自
``gateway/platforms/base.py`` 的 ``classify_send_error`` /
``is_chat_level_not_found``(均为 NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/gateway/``)。移植语义(grill Q3 定案,Hermes 原味
错误分类制):

- 投递错误先分类:**``forbidden`` 与 chat 级 ``not_found`` 为硬失败,单次
  即标 dead**;超时/网络抖动/限流等瞬态错误不标(下轮照常重试);
- dead 期间跳过该对象并记结构化日志(**不发告警卡**——推送通道本身可能
  就是故障源);投递成功一次即自愈清除;
- 无阈值、无配置口。

与上游的偏离/增量注记:

- Hermes 错误串来自 SendResult/异常文本;MYIA 通道一律抛
  :class:`~myia.push.base.PushSendError`(code + 中文消息),分类对
  ``(code, str(exc))`` 文本块做子串匹配——通道子任务(feishu/telegram)
  落地时如有更精确的 API 错误码,可在消息里带原厂描述即可命中;
- 死信键 ``platform:chat_id``(:class:`~myia.push.targets.ChannelTarget.key`),
  条目形态 ``{"reason": str, "marked_at": ts}``(design D3);
- 派发入口为批量形态(digest 一批一卡 / immediate 单条),Hermes 无摘要
  聚合,无对应物。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from myia.push.base import Channel, PushSendError, SendContext, SendReport
from myia.push.directory import ChannelDirectory
from myia.push.targets import ChannelTarget, resolve_all

__all__ = [
    "LEDGER_FILENAME",
    "classify_dead_error",
    "is_chat_level_not_found",
    "DeliveryLedger",
    "send_batch_to_targets",
]

logger = logging.getLogger(__name__)

#: 死信账本文件名(数据根下)。
LEDGER_FILENAME = "delivery_ledger.json"

#: 硬失败类别(Hermes ``_DEAD_ERROR_KINDS`` 同款;仅这两类可标 dead)。
DEAD_ERROR_KINDS = frozenset({"forbidden", "not_found"})

# ---- 错误分类表(Hermes gateway/platforms/base.py 的 MYIA 精简移植)----

#: forbidden 家族:bot 无法触达该会话(Hermes forbidden 分支 + HTTP 403)。
_FORBIDDEN_MARKERS = (
    "forbidden",
    "403",
    "bot was blocked",
    "blocked by the user",
    "user is deactivated",
    "not enough rights",
    "have no rights",
    "not a member",
)

#: chat 级 not_found:整个会话不可达(仅此可判 dead)。
_CHAT_LEVEL_NOT_FOUND_MARKERS = ("chat not found", "chat_id is invalid")

#: 子会话级 not_found:话题/消息没了但父会话可达(不判 dead,Hermes
#: ``_SUBCHAT_NOT_FOUND_SUBSTRINGS`` 同义;两类标记同现时子会话读法胜出)。
_SUBCHAT_NOT_FOUND_MARKERS = (
    "thread not found",
    "topic_deleted",
    "message to edit not found",
    "message to reply not found",
    "message_id_invalid",
)


def _error_blob(error: BaseException | str) -> str:
    """小写文本块(错误串 + 异常文本 + 异常类名)——两分类器共用,永不漂移。"""
    parts: list[str] = []
    if isinstance(error, str):
        parts.append(error)
    else:
        parts.extend(p for p in (str(error), type(error).__name__) if p)
        code = getattr(error, "code", None)
        if isinstance(code, str):
            parts.append(code)
    return " ".join(parts).lower()


def is_chat_level_not_found(error: BaseException | str) -> bool:
    """not_found 是否意味整个 chat 不可达(只有它可判 dead)。"""
    blob = _error_blob(error)
    return not any(m in blob for m in _SUBCHAT_NOT_FOUND_MARKERS) and any(
        m in blob for m in _CHAT_LEVEL_NOT_FOUND_MARKERS
    )


def classify_dead_error(error: BaseException | str) -> str | None:
    """投递错误的死信类别:``forbidden`` / chat 级 ``not_found`` → 该类别;
    其余(瞬态/未知/子会话级)→ None(不标 dead)。

    Hermes ``classify_dead_error`` 同语义;通道子任务如有精确 API 错误码
    判定,可在 PushSendError 消息里保留原厂描述以命中本表。
    """
    blob = _error_blob(error)
    if any(m in blob for m in _FORBIDDEN_MARKERS):
        return "forbidden"
    if any(m in blob for m in _CHAT_LEVEL_NOT_FOUND_MARKERS):
        return "not_found" if is_chat_level_not_found(error) else None
    return None


class DeliveryLedger:
    """Confirmed-dead target set keyed ``platform:chat_id``(Hermes
    ``DeadTargetRegistry`` 移植)。

    存储 ``<data_root>/delivery_ledger.json``,条目
    ``{key: {"reason": str, "marked_at": ts}}``;原子写(tmp+rename);损坏/
    不可写退化为内存态,绝不阻塞投递(best-effort,上游同款)。线程安全
    (RLock);成功投递调用 :meth:`clear` 自愈。
    """

    def __init__(self, data_root: str | Path) -> None:
        self._lock = threading.RLock()
        self._dead: dict[str, dict[str, Any]] = {}
        self._path = Path(data_root) / LEDGER_FILENAME
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            if self._path.exists():
                logger.warning("死信账本读取失败,按空态处理: path=%s error=%s", self._path, exc)
        else:
            if isinstance(raw, dict):
                # 只留形态良好的条目(reason/marked_at;上游同款防御)。
                self._dead = {
                    str(key): {
                        "reason": str(entry.get("reason") or ""),
                        "marked_at": entry.get("marked_at"),
                    }
                    for key, entry in raw.items()
                    if isinstance(entry, Mapping)
                }

    @property
    def path(self) -> Path:
        return self._path

    def _flush_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._dead, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:  # best-effort:保内存态,绝不阻塞投递
            logger.warning("死信账本写入失败(继续内存态): path=%s error=%s", self._path, exc)

    @staticmethod
    def _key(platform: str, chat_id: str) -> str:
        return f"{str(platform).strip().lower()}:{str(chat_id).strip()}"

    def is_dead(self, target: ChannelTarget | None = None, *, platform: str = "", chat_id: str = "") -> bool:
        """该对象是否已标 dead(键二选一:传 target 或 platform+chat_id)。"""
        if target is not None:
            platform, chat_id = target.platform, target.chat_id
        if not chat_id:
            return False
        with self._lock:
            return self._key(platform, chat_id) in self._dead

    def mark_dead(
        self,
        target: ChannelTarget | None = None,
        *,
        platform: str = "",
        chat_id: str = "",
        reason: str = "",
    ) -> bool:
        """标 dead;新标记返回 True(幂等重标返回 False)。"""
        if target is not None:
            platform, chat_id = target.platform, target.chat_id
        if not chat_id:
            return False
        key = self._key(platform, chat_id)
        with self._lock:
            existed = key in self._dead
            self._dead[key] = {"reason": str(reason)[:200], "marked_at": time.time()}
            self._flush_locked()
        if not existed:
            logger.info(
                "死信:已标记 %s 为不可达(%s)——后续投递将跳过该对象,直至某次成功自愈",
                key,
                reason or "未给出原因",
            )
        return not existed

    def clear(
        self,
        target: ChannelTarget | None = None,
        *,
        platform: str = "",
        chat_id: str = "",
    ) -> bool:
        """自愈清除(投递成功时调用);原本已标返回 True。"""
        if target is not None:
            platform, chat_id = target.platform, target.chat_id
        if not chat_id:
            return False
        key = self._key(platform, chat_id)
        with self._lock:
            if self._dead.pop(key, None) is None:
                return False
            self._flush_locked()
            logger.info("死信:已清除 %s(投递成功,自愈)", key)
        return True

    def dead_keys(self) -> list[str]:
        """当前 dead 键快照(UI/测试可见性)。"""
        with self._lock:
            return sorted(self._dead)


async def send_batch_to_targets(
    items: Sequence[Any],
    *,
    specs: Sequence[str],
    channel: Channel,
    context: SendContext,
    directory: ChannelDirectory,
    ledger: DeliveryLedger | None = None,
    platforms: Mapping[str, Any] | None = None,
) -> list[SendReport]:
    """Send one card carrying ``items`` to every target resolved from ``specs``.

    派发循环(design D2):解析 → 逐对象死信过滤 → 逐对象发送(带
    ``context.target``)→ 成功自愈 / 硬失败标 dead。单对象失败不阻断同批
    其余对象(partial-failure 约定);未解析 spec 与 dead 跳过产出
    ``skipped=True`` 的失败报告(摘要池据此不做无限重试)。

    Args:
        items: 本卡携带的条目(digest=整批,immediate=单条)。
        specs: 对象 spec 列表(schema 加载期已保证格式与同平台约束)。
        channel: 通道实例;**必须支持寻址**(``supports_targeting``),否则
            整批失败报告(绝不回落 legacy 单 target——那会误投)。
        context: 基础发送上下文(逐对象派生 ``target`` 副本)。
        directory: 通道目录。
        ledger: 死信账本;None = 不做死信跟踪(测试/演示)。
        platforms: 平台注册表(直达解析钩子);缺省惰性取
            ``myia.push.PLATFORMS``。

    Returns:
        每对象一份 :class:`SendReport`(未解析 spec 同样计入)。
    """
    if not getattr(channel, "supports_targeting", False):
        logger.error(
            "通道不支持目录寻址,定向批次整体失败(等待平台子任务翻开 supports_targeting):"
            " channel=%s specs=%s",
            channel.name,
            list(specs),
        )
        return [
            SendReport(
                channel=channel.name,
                ok=False,
                item_count=len(items),
                error=(
                    "[targeting_not_supported] 通道 "
                    f"{channel.name!r} 尚不支持定向推送(等平台适配子任务接入)"
                ),
            )
        ]

    targets, errors = resolve_all(specs, directory, platforms=platforms)
    reports: list[SendReport] = [
        SendReport(
            channel=channel.name,
            ok=False,
            item_count=len(items),
            error=f"[target_unresolved] {exc}",
            skipped=True,
        )
        for exc in errors
    ]

    for target in targets:
        if ledger is not None and ledger.is_dead(target):
            # Hermes 同款:dead 跳过 + 结构化日志,不发告警卡;某次成功即自愈。
            logger.info(
                "死信跳过(对象此前已确认不可达): target=%s reason=重发成功后自愈",
                target,
            )
            reports.append(
                SendReport(
                    channel=channel.name,
                    ok=False,
                    item_count=len(items),
                    error=f"[dead_target] 跳过此前确认不可达的对象 {target}",
                    skipped=True,
                )
            )
            continue
        try:
            await channel.send(items, replace(context, target=target))
        except PushSendError as exc:
            reports.append(
                SendReport(
                    channel=channel.name,
                    ok=False,
                    item_count=len(items),
                    error=f"[{exc.code}] {exc}",
                )
            )
            if ledger is not None:
                kind = classify_dead_error(exc)
                if kind is not None:
                    ledger.mark_dead(target, reason=f"{kind}: {str(exc)[:120]}")
                else:
                    logger.debug("瞬态投递错误,不标死信: target=%s error=%s", target, exc)
        except Exception as exc:  # noqa: BLE001 - 部分失败语义要求隔离未知异常
            logger.error(
                "定向发送未知异常(需要介入): channel=%s target=%s error=%s",
                channel.name,
                target,
                exc,
                exc_info=True,
            )
            reports.append(
                SendReport(
                    channel=channel.name,
                    ok=False,
                    item_count=len(items),
                    error=f"[unexpected] {type(exc).__name__}: {exc}",
                )
            )
        else:
            reports.append(SendReport(channel=channel.name, ok=True, item_count=len(items)))
            if ledger is not None:
                ledger.clear(target)  # 成功一次即自愈(Hermes 同款)
    return reports
