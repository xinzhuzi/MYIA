"""Channel directory: cached map of addressable push targets per platform.

MYIA 移植重写自 Hermes ``gateway/channel_directory.py``(NousResearch/
Hermes-Agent,MIT;上游路径 ``~/.hermes/hermes-agent/gateway/channel_directory.py``,
474 行)。移植要点:目录数据模型、别名覆盖、原子持久化;去掉了 multiplex
多 home、Slack 特判、session 回填等 gateway 专属逻辑(取舍见任务档
10-03-messaging-core design「取舍记录」)。与上游的增量:

- 重建为**按平台桶整体替换**(:meth:`ChannelDirectory.replace_platform`)而
  非整体从零重建——MYIA 无常驻 gateway,发现按平台单独触发;
- :meth:`ChannelDirectory.merge_entries` 被动增量合并(10-03-messaging-telegram
  D2):无目录发现 API 的平台(如 Telegram)靠入站观测逐条积累,Hermes 的
  等价物是入站消息回填目录;
- :meth:`ChannelDirectory.commit_platform_refresh` 单平台已发现的合并提交
  (10-03-messaging-ui):sidecar 刷新按钮要把发现失败结构化上抛(不吞进
  warning),发现与合并拆开,合并口单列;
- ``last_seen`` 字段(Hermes 无):给桌面 UI「最后发现」列用;
- 手工直编 ``channel_directory.json`` 不保证保留(Hermes 同款);别名文件
  ``channel_aliases.json`` 才是持久覆盖层,在 load 与 replace 双向生效
  (重建后仍生效,Hermes 回归点)。

存储形态(对齐 Hermes JSON 结构)::

    channel_directory.json  -> {"updated_at": iso|None, "platforms": {"feishu": [entry, ...]}}
    channel_aliases.json    -> {"feishu": {"<chat_id>": "别名"}}   # 手工可编

别名 id 未被目录发现时生成占位条目(上游同语义:新群可在第一条消息前
先命名寻址)。读写全部 best-effort:损坏/不可写退化为内存态,绝不阻塞推送。
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "ALIASES_FILENAME",
    "DIRECTORY_FILENAME",
    "DirectoryDiscoverUnsupported",
    "REFRESH_STALE_SECONDS",
    "ChannelEntry",
    "ChannelDirectory",
]

logger = logging.getLogger(__name__)

#: 目录文件名(数据根下)。
DIRECTORY_FILENAME = "channel_directory.json"
#: 别名覆盖文件名(手工可编,重建后仍生效)。
ALIASES_FILENAME = "channel_aliases.json"
#: 目录节流懒刷阈值(Q4 定案:距上次刷新超过该秒数才重新发现;Hermes
#: housekeeping 5 分钟节流的等价物)。调用方(pipeline)使用。
REFRESH_STALE_SECONDS = 300.0

#: entry ``type`` 取值约定(与通道实现共享;对齐 Hermes 的 channel/dm/forum 语汇)。
ENTRY_TYPES = ("group", "dm", "channel", "topic")


class DirectoryDiscoverUnsupported(RuntimeError):
    """该平台**没有**目录发现能力(蓝本事实),不是刷新失败。

    10-03-messaging-w2-platforms design D4:ntfy/dingtalk/wecom 三家在蓝本里
    均无「列出可达对象」的 API(MYIA 侧也无入站可回填),目录条目唯一来源是
    别名文件手工登记 + 直达 id。发现路径遇到本异常按「该平台无自动发现」
    处理——静默跳过/如实说明,绝不计为失败、不假装刷新出空目录:

    - :meth:`ChannelDirectory.refresh` 捕获后 debug 日志跳过(保留旧桶);
    - CLI ``channels refresh`` 打印说明并以 0 退出(payload 记 ``no_discovery``);
    - 桌面 sidecar ``channels.refresh`` 转 ``discover_not_supported`` 结构化错误。

    与 telegram 的被动积累(``discover_directory`` 缺席)语义不同:telegram
    条目会随入站观测自动进目录,这三家连隐式积累都没有——所以走显式
    异常而非「无钩子」约定。
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(
            message
            or "该平台无自动发现(蓝本事实):请用别名登记或直达 id 寻址"
        )


@dataclass
class ChannelEntry:
    """One addressable target inside a platform's bucket.

    ``last_seen`` 是 MYIA 增量字段(Hermes 无):该条目最近一次被目录发现
    的 Unix 时间戳,给 UI「最后发现」列用。
    """

    platform: str
    chat_id: str
    name: str
    type: str = "dm"
    thread_id: str | None = None
    last_seen: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 持久化形态(``platform`` 冗余存储,单文件可独立消费)。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, platform: str | None = None) -> "ChannelEntry | None":
        """Best-effort 反序列化:缺 chat_id/name 或非 dict → None(静默丢弃)。"""
        if not isinstance(raw, Mapping):
            return None
        chat_id = str(raw.get("chat_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not chat_id or not name:
            return None
        thread_id = raw.get("thread_id")
        last_seen = raw.get("last_seen")
        return cls(
            platform=str(raw.get("platform") or platform or "").strip(),
            chat_id=chat_id,
            name=name,
            type=str(raw.get("type") or "dm"),
            thread_id=str(thread_id) if thread_id else None,
            last_seen=float(last_seen) if isinstance(last_seen, (int, float)) else None,
        )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """tmp+rename 原子写(UTF-8,ensure_ascii=False);失败抛 OSError。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(path)


def _load_json_dict(path: Path) -> dict[str, Any]:
    """读 JSON 对象;缺失/损坏/非 dict → {}(Hermes ``_load_json_dict`` 同语义)。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("目录文件读取失败,按空态处理: path=%s error=%s", path, exc)
        return {}


class ChannelDirectory:
    """Per-data-root channel directory with alias overlay + atomic persistence.

    Args:
        data_root: 数据根目录(db 路径的父目录;CLI cwd / 桌面 ``myia_home()``,
            与任务 10-03-v111-desktop-paths 收口一致,push 层不自建路径解析)。
        now: 时间源注入(测试钉死 ``updated_at``/``last_seen``);缺省墙钟。
    """

    def __init__(self, data_root: str | Path, *, now: float | None = None) -> None:
        self._root = Path(data_root)
        self._now = now
        self._platforms: dict[str, list[ChannelEntry]] = {}
        self._updated_at: str | None = None
        self.load()

    # ------------------------------------------------------------ paths/state

    @property
    def path(self) -> Path:
        """目录文件路径(``<data_root>/channel_directory.json``)。"""
        return self._root / DIRECTORY_FILENAME

    @property
    def aliases_path(self) -> Path:
        """别名文件路径(``<data_root>/channel_aliases.json``,手工可编)。"""
        return self._root / ALIASES_FILENAME

    @property
    def updated_at(self) -> str | None:
        """最近一次成功刷新的 ISO 时间戳;None = 从未刷新(视为不新鲜)。"""
        return self._updated_at

    def age_seconds(self, *, now: float | None = None) -> float | None:
        """距上次刷新的秒数;从未刷新返回 None(调用方按「需要刷新」处理)。"""
        if self._updated_at is None:
            return None
        try:
            updated = datetime.fromisoformat(self._updated_at).timestamp()
        except ValueError:
            return None
        return float(now if now is not None else self._wall_clock()) - updated

    def _wall_clock(self) -> float:
        return self._now if self._now is not None else time.time()

    def entries(self, platform: str) -> list[ChannelEntry]:
        """One platform's bucket (copy; empty when the platform is unknown)."""
        return list(self._platforms.get(platform, ()))

    def platforms(self) -> list[str]:
        """Known platform names (sorted for stable display)."""
        return sorted(self._platforms)

    def all_entries(self) -> list[ChannelEntry]:
        """Every entry across platforms, platform name then chat_id order."""
        return [
            entry
            for platform in self.platforms()
            for entry in self._platforms[platform]
        ]

    def find(self, platform: str, chat_id: str) -> ChannelEntry | None:
        """Exact chat_id lookup inside one platform bucket."""
        return next(
            (e for e in self._platforms.get(platform, ()) if e.chat_id == chat_id),
            None,
        )

    # ------------------------------------------------------------ load/save

    def load(self) -> None:
        """(Re)load from disk + re-apply aliases(编辑别名立即生效,不必等刷新)。"""
        data = _load_json_dict(self.path)
        platforms: dict[str, list[ChannelEntry]] = {}
        raw_platforms = data.get("platforms")
        if isinstance(raw_platforms, Mapping):
            for plat_name, raw_entries in raw_platforms.items():
                bucket = (
                    [
                        entry
                        for raw in raw_entries
                        if (entry := ChannelEntry.from_dict(raw, platform=str(plat_name))) is not None
                    ]
                    if isinstance(raw_entries, list)
                    else []
                )
                # 空桶也保留:平台已注册但尚无发现是合法态(UI 提示用)。
                platforms[str(plat_name)] = bucket
        self._platforms = platforms
        self._updated_at = (
            data["updated_at"] if isinstance(data.get("updated_at"), str) else None
        )
        self._apply_aliases()

    def save(self) -> None:
        """Atomic persist(tmp+rename);失败仅告警(Hermes 同款 best-effort)。"""
        payload: dict[str, Any] = {
            "updated_at": self._updated_at,
            "platforms": {
                plat: [entry.to_dict() for entry in bucket]
                for plat, bucket in sorted(self._platforms.items())
            },
        }
        try:
            _atomic_write_json(self.path, payload)
        except OSError as exc:
            logger.warning("目录写入失败(继续内存态): path=%s error=%s", self.path, exc)

    # ------------------------------------------------------------ aliases

    def _aliases_raw(self) -> dict[str, dict[str, str]]:
        """别名文件原始形态 ``{platform: {chat_id: name}}``;损坏按空。"""
        raw = _load_json_dict(self.aliases_path)
        aliases: dict[str, dict[str, str]] = {}
        for plat_name, id_map in raw.items():
            if not isinstance(id_map, Mapping):
                continue
            cleaned = {
                str(chat_id): str(name).strip()
                for chat_id, name in id_map.items()
                if isinstance(name, str) and name.strip()
            }
            if cleaned:
                aliases[str(plat_name)] = cleaned
        return aliases

    def _apply_aliases(self) -> None:
        """别名覆盖 + 占位条目生成(Hermes ``_apply_channel_aliases`` 语义)。

        对已发现 id 改名;未发现的 id 生成占位条目(新群可先命名后首聊)。
        """
        for plat_name, id_map in self._aliases_raw().items():
            bucket = self._platforms.setdefault(plat_name, [])
            for chat_id, friendly in id_map.items():
                matches = [e for e in bucket if e.chat_id == chat_id]
                for entry in matches:
                    entry.name = friendly
                if not matches:
                    bucket.append(
                        ChannelEntry(
                            platform=plat_name,
                            chat_id=chat_id,
                            name=friendly,
                            type="dm",
                        )
                    )

    def aliases_snapshot(self) -> dict[str, dict[str, str]]:
        """Raw alias overlay view(read-only fresh copy;呈现层/sidecar 用).

        形态 ``{platform: {chat_id: name}}``,与别名文件的持久形态一致
        (手工可编);损坏/缺失按空(与 :meth:`_aliases_raw` 同一解析)。
        """
        return self._aliases_raw()

    def set_alias(self, platform: str, chat_id: str, name: str) -> None:
        """Write one alias to the overlay file + apply it to the live directory.

        别名文件是持久覆盖层:目录重建后仍生效。空名删除该别名。
        """
        aliases = self._aliases_raw()
        plat_map = aliases.setdefault(platform, {})
        if name.strip():
            plat_map[chat_id] = name.strip()
        else:
            plat_map.pop(chat_id, None)
            if not plat_map:
                aliases.pop(platform, None)
        try:
            _atomic_write_json(self.aliases_path, aliases)
        except OSError as exc:
            logger.warning("别名写入失败(继续内存态): path=%s error=%s", self.aliases_path, exc)
        self._apply_aliases()

    # ------------------------------------------------------------ rebuild

    def replace_platform(
        self, platform: str, entries: Iterable[ChannelEntry], *, now: float | None = None
    ) -> None:
        """整体替换一个平台的桶(MYIA 增量:Hermes 是全量从零重建)。

        同 chat_id 的旧条目不合并(桶替换);``last_seen`` 缺省填刷新时刻。
        手工直编目录文件在本方法后不保证保留——别名文件才是持久覆盖层。
        """
        stamp = now if now is not None else self._wall_clock()
        bucket: list[ChannelEntry] = []
        seen: set[str] = set()
        for entry in entries:
            if entry.chat_id in seen:
                continue  # 同 id 去重,首个胜出(Hermes _normalize_adapter_channels 同款)
            seen.add(entry.chat_id)
            entry.platform = platform
            if entry.last_seen is None:
                entry.last_seen = stamp
            bucket.append(entry)
        self._platforms[platform] = bucket
        self._apply_aliases()

    async def refresh(
        self,
        adapters: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        """对每个平台调用适配器 ``discover_directory()`` 并桶替换 + 持久化。

        Hermes housekeeping 周期重建的 MYIA 等价物(节流由调用方负责,
        见 pipeline run 前懒刷)。单平台失败隔离:告警 + 保留旧桶,其余平台
        照常(Hermes ``build_channel_directory`` 同语义)。

        Args:
            adapters: platform → 通道实例;缺 ``discover_directory`` 的跳过;
                抛 :class:`DirectoryDiscoverUnsupported` 的(无自动发现平台,
                蓝本事实)同样跳过——debug 级日志,不算失败。
            now: 时间源注入(测试)。

        Returns:
            每平台条目数 ``{platform: count}``(失败/无发现平台不在结果里)。
        """
        stamp = now if now is not None else self._wall_clock()
        counts: dict[str, int] = {}
        for platform in sorted(adapters):
            adapter = adapters[platform]
            discover = getattr(adapter, "discover_directory", None)
            if not callable(discover):
                continue
            try:
                entries = await discover()
            except DirectoryDiscoverUnsupported as exc:
                # 无自动发现是平台事实(prd R4):不告警不计数,保留旧桶。
                logger.debug("平台无自动发现,跳过目录刷新: platform=%s note=%s", platform, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 刷新失败退回旧目录,不阻塞推送
                logger.warning(
                    "目录刷新失败,保留该平台旧桶: platform=%s error=%s", platform, exc
                )
                continue
            self.replace_platform(platform, entries or (), now=stamp)
            counts[platform] = len(self._platforms[platform])
        self._updated_at = datetime.fromtimestamp(stamp).isoformat()
        self.save()
        if counts:
            logger.info("目录刷新完成: %s", counts)
        return counts

    def commit_platform_refresh(
        self,
        platform: str,
        entries: Iterable[ChannelEntry],
        *,
        now: float | None = None,
    ) -> list[ChannelEntry]:
        """Commit an already-discovered bucket: replace + ``updated_at`` + persist.

        与 :meth:`refresh` 的分工(10-03-messaging-ui):那边发现与合并一体、
        单平台失败只告警隔离(推送路径绝不因目录停摆);这边发现已由调用方
        完成 —— sidecar ``channels.refresh`` 要把发现失败**结构化上抛**给
        UI,不能吞进 warning,故合并+落盘单列此口。替换/别名重套/持久化
        语义与 refresh 完全同一实现路径。

        Returns:
            替换后的平台桶(拷贝;调用方直接作应答载荷)。
        """
        stamp = now if now is not None else self._wall_clock()
        self.replace_platform(platform, entries, now=stamp)
        self._updated_at = datetime.fromtimestamp(stamp).isoformat()
        self.save()
        return self.entries(platform)

    def merge_entries(
        self,
        platform: str,
        entries: Iterable[ChannelEntry],
        *,
        now: float | None = None,
    ) -> int:
        """被动合并增量条目(10-03-messaging-telegram D2;与 :meth:`replace_platform`
        的整桶替换互补——Telegram 无目录发现 API,条目靠入站观测逐个积累)。

        同 ``chat_id`` 用观测值刷新 ``name``/``type`` 并前移 ``last_seen``;
        新 id 追加。别名覆盖在合并后重套(手工别名始终赢)。**有实际变化才
        落盘**:空轮次/重复观测零写盘(getUpdates 带 offset 书签,大多数
        轮次本就无新 update)。

        Args:
            platform: 平台桶名(如 ``telegram``)。
            entries: 本轮观测到的条目(同 id 重复无妨,合并幂等)。
            now: 时间源注入(测试钉死 ``last_seen``)。

        Returns:
            发生变化的条目数(新增 + 字段刷新;仅 ``last_seen`` 前移也计)。
        """
        stamp = now if now is not None else self._wall_clock()
        bucket = self._platforms.setdefault(platform, [])
        index = {entry.chat_id: entry for entry in bucket}
        changed = 0
        for entry in entries:
            existing = index.get(entry.chat_id)
            if existing is None:
                entry.platform = platform
                if entry.last_seen is None:
                    entry.last_seen = stamp
                bucket.append(entry)
                index[entry.chat_id] = entry
                changed += 1
                continue
            if (existing.name, existing.type) != (entry.name, entry.type):
                existing.name = entry.name
                existing.type = entry.type
                changed += 1
            if existing.last_seen != stamp:
                existing.last_seen = stamp
                changed += 1
        if changed:
            self._apply_aliases()
            self.save()
        return changed
