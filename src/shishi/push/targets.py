"""Target spec parsing + directory resolution for targeted delivery.

MYIA 移植重写自 Hermes ``tools/send_message_targets.py``(NousResearch/
Hermes-Agent,MIT;上游路径 ``~/.hermes/hermes-agent/tools/send_message_targets.py``,
``resolve_send_target``/``resolve_channel_name`` 的解析顺序)。解析顺序对齐
Hermes 实际顺序(grill 事实轮核订):

1. **显式 id / ``@username`` 直达**(平台解析钩子,不经目录):如
   ``telegram:12345``、``telegram:@user``、``feishu:oc_xxx``;
2. **目录精确 chat_id**(大小写敏感原样匹配,raw platform id 可用);
3. **精确名**(大小写不敏感,忽略首部 ``#``,Hermes 同款规范化);
4. **唯一前缀**(多义即未命中)。

别名是目录改名层(:mod:`shishi.push.directory` 加载期已套用),不是独立
解析层级。未命中抛 :class:`TargetResolveError` 结构化错误——【偏离注记】
Hermes 只报「Could not resolve」不带候选(靠交互式 ``action='list'``);
MYIA 无交互回路,错误内嵌候选列表】。

安全:全部为纯字符串/前缀匹配,无任何 ``eval``/动态执行。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from shishi.push.directory import ChannelDirectory, ChannelEntry

__all__ = [
    "SPEC_RE",
    "ChannelTarget",
    "TargetResolveError",
    "parse_spec",
    "resolve_target",
    "resolve_all",
]

logger = logging.getLogger(__name__)

#: 合法 spec 形态 ``platform:名称或id``(platform 小写字母开头,后接小写
#: 字母/数字/下划线;冒号后非空,允许 ``@``/负号/中文等原生 id 字符)。
SPEC_RE = re.compile(r"^([a-z][a-z0-9_]*):(.+)$")

#: 解析来源标签(报告/日志用)。
RESOLVED_DIRECT = "direct"
RESOLVED_DIRECTORY_ID = "directory_id"
RESOLVED_DIRECTORY_NAME = "directory_name"
RESOLVED_DIRECTORY_PREFIX = "directory_prefix"


class TargetResolveError(ValueError):
    """One target spec could not be resolved(结构化,内嵌候选列表).

    Attributes:
        spec: 原始 spec(如 ``feishu:不存在的群``)。
        platform: spec 里的平台前缀(格式错时可能为 None)。
        ref: 冒号后的名称/id 部分。
        candidates: 目录内该平台的候选描述(``名称 (chat_id)`` 列表,可能为空)。
        reason: 机器可读失败原因——``bad_format`` / ``unresolvable`` /
            ``ambiguous_prefix`` / ``unknown_platform``。
    """

    def __init__(
        self,
        spec: str,
        *,
        platform: str | None,
        ref: str | None,
        candidates: list[str] | None = None,
        reason: str = "unresolvable",
        message: str | None = None,
    ) -> None:
        self.spec = spec
        self.platform = platform
        self.ref = ref
        self.candidates = candidates or []
        self.reason = reason
        hint = ""
        if reason == "ambiguous_prefix":
            hint = f"前缀多义(命中 {len(self.candidates)} 个候选)"
        elif reason == "unknown_platform":
            hint = "目录中该平台尚无任何已发现对象"
        elif reason == "bad_format":
            hint = "spec 必须形如 platform:名称或id"
        text = message or (
            f"无法解析推送对象 {spec!r}"
            + (f":{hint}" if hint else "")
            + (f";候选: {', '.join(self.candidates)}" if self.candidates else "")
        )
        super().__init__(text)


@dataclass(frozen=True)
class ChannelTarget:
    """One resolved push target(投递与死信记账的原子单位).

    ``resolved_from`` 记录命中路径(四路径之一),报告/测试断言用。
    """

    platform: str
    chat_id: str
    thread_id: str | None = None
    name: str | None = None
    resolved_from: str = RESOLVED_DIRECT

    @property
    def key(self) -> str:
        """死信账本键(与 :class:`shishi.push.delivery.DeliveryLedger` 一致)。"""
        return f"{self.platform.strip().lower()}:{self.chat_id.strip()}"

    def __str__(self) -> str:
        label = self.name or ""
        return f"{self.platform}:{self.chat_id}" + (f"({label})" if label else "")


def parse_spec(spec: str) -> tuple[str, str]:
    """``platform:名称或id`` → ``(platform, ref)``;格式错抛 TargetResolveError。"""
    match = SPEC_RE.fullmatch(spec.strip())
    if match is None:
        raise TargetResolveError(
            spec, platform=None, ref=None, reason="bad_format"
        )
    platform, ref = match.group(1), match.group(2).strip()
    if not ref:
        raise TargetResolveError(spec, platform=platform, ref=ref, reason="bad_format")
    return platform, ref


def _normalize_query(value: str) -> str:
    """Hermes ``_normalize_channel_query`` 同款:去首部 ``#`` + 空白 + 小写。"""
    return value.lstrip("#").strip().lower()


def _candidate_labels(entries: Iterable[ChannelEntry]) -> list[str]:
    """候选描述列表(``名称 (chat_id)``),按名称稳定排序,供错误内嵌。"""
    return sorted(
        f"{entry.name} ({entry.chat_id})" for entry in entries if entry.name
    )


def resolve_target(
    spec: str,
    directory: ChannelDirectory,
    *,
    platforms: Mapping[str, Any] | None = None,
) -> ChannelTarget:
    """Resolve one ``platform:名称或id`` spec to a :class:`ChannelTarget`.

    Args:
        spec: 待解析 spec。
        directory: 通道目录(别名已在加载期套用)。
        platforms: 平台注册表(``PLATFORMS``,platform → 通道类);条目可提供
            ``parse_direct_ref(ref) -> ChannelTarget | None`` 直达解析钩子
            (telegram 数字 id/``@username``、feishu ``oc_/ou_/on_/chat_/open_``
            前缀 id 等),命中即跳过目录。缺省惰性取 ``shishi.push.PLATFORMS``。

    Raises:
        TargetResolveError: spec 格式错 / 直达与目录全部未命中 / 前缀多义。
    """
    platform, ref = parse_spec(spec)
    bucket = directory.entries(platform)

    # 1. 直达钩子:显式 id/@username 不经目录(Hermes 平台 parser 的等价物)。
    direct = _parse_direct_ref(platform, ref, platforms)
    if direct is not None:
        return direct

    # 2. 目录精确 chat_id(大小写敏感,原样匹配——raw platform id 必须可用)。
    raw = ref.strip()
    for entry in bucket:
        if entry.chat_id == raw:
            return _from_entry(entry, RESOLVED_DIRECTORY_ID)

    # 3. 精确名(大小写不敏感,# 前缀忽略)。
    query = _normalize_query(ref)
    for entry in bucket:
        if _normalize_query(entry.name) == query:
            return _from_entry(entry, RESOLVED_DIRECTORY_NAME)

    # 4. 唯一前缀;多义即未命中(带候选)。
    prefix_matches = [e for e in bucket if _normalize_query(e.name).startswith(query)]
    if len(prefix_matches) == 1:
        return _from_entry(prefix_matches[0], RESOLVED_DIRECTORY_PREFIX)
    if len(prefix_matches) > 1:
        raise TargetResolveError(
            spec,
            platform=platform,
            ref=ref,
            candidates=_candidate_labels(prefix_matches),
            reason="ambiguous_prefix",
        )

    reason = "unknown_platform" if not bucket else "unresolvable"
    raise TargetResolveError(
        spec,
        platform=platform,
        ref=ref,
        candidates=_candidate_labels(bucket),
        reason=reason,
    )


def resolve_all(
    specs: Iterable[str],
    directory: ChannelDirectory,
    *,
    platforms: Mapping[str, Any] | None = None,
) -> tuple[list[ChannelTarget], list[TargetResolveError]]:
    """批量解析(partial-failure 约定): ``(targets, errors)``.

    单个 spec 未命中不阻断其余 spec——错误收集返回,由派发层转结构化
    日志/失败报告(schema 加载期已保证格式与同平台约束)。
    """
    targets: list[ChannelTarget] = []
    errors: list[TargetResolveError] = []
    for spec in specs:
        try:
            targets.append(resolve_target(spec, directory, platforms=platforms))
        except TargetResolveError as exc:
            logger.warning("推送对象解析失败: spec=%s reason=%s", exc.spec, exc.reason)
            errors.append(exc)
    return targets, errors


def _parse_direct_ref(
    platform: str, ref: str, platforms: Mapping[str, Any] | None
) -> ChannelTarget | None:
    """平台直达钩子分发;无注册表或无钩子/未命中 → None(走目录)。"""
    if platforms is None:
        from shishi.push import PLATFORMS  # deferred:避免包初始化环

        platforms = PLATFORMS
    entry_cls = platforms.get(platform)
    if entry_cls is None:
        return None
    parser = getattr(entry_cls, "parse_direct_ref", None)
    if not callable(parser):
        return None
    target = parser(ref)
    if target is None:
        return None
    # 钩子可能返回裸 (platform, chat_id[, thread]);统一升格为 ChannelTarget。
    if isinstance(target, ChannelTarget):
        return target
    if isinstance(target, tuple) and target:
        chat_id = str(target[0])
        thread = str(target[1]) if len(target) > 1 and target[1] else None
        return ChannelTarget(
            platform=platform, chat_id=chat_id, thread_id=thread, resolved_from=RESOLVED_DIRECT
        )
    logger.debug("parse_direct_ref 返回非常规形态,忽略: platform=%s value=%r", platform, target)
    return None


def _from_entry(entry: ChannelEntry, resolved_from: str) -> ChannelTarget:
    return ChannelTarget(
        platform=entry.platform,
        chat_id=entry.chat_id,
        thread_id=entry.thread_id,
        name=entry.name,
        resolved_from=resolved_from,
    )
