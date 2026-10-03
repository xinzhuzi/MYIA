"""Tests for shishi.push.directory — 通道目录(蓝本移植自 Hermes channel_directory).

覆盖任务 10-03-messaging-core 步骤 1:空态 / 别名覆盖(load 与 replace 双向)/
重建后别名仍在(Hermes 回归点)/ 损坏 JSON 容错 / tmp+rename 原子写 /
按平台桶整体替换。无网络、无 asyncio 调度(asyncio.run 驱动 refresh)。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shishi.push.directory import (
    ALIASES_FILENAME,
    DIRECTORY_FILENAME,
    ChannelDirectory,
    ChannelEntry,
)


def _entry(chat_id: str, name: str, **kwargs) -> ChannelEntry:
    kwargs.setdefault("platform", "feishu")
    return ChannelEntry(chat_id=chat_id, name=name, **kwargs)


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return root


class TestEmptyState:
    def test_fresh_root_is_empty_and_lazy_paths(self, data_root: Path):
        directory = ChannelDirectory(data_root)

        assert directory.platforms() == []
        assert directory.all_entries() == []
        assert directory.updated_at is None
        assert directory.age_seconds() is None  # 从未刷新 → 调用方按需刷新处理
        # 空态构造不创建文件(惰性产物)。
        assert not (data_root / DIRECTORY_FILENAME).exists()

    def test_missing_files_survive_save_roundtrip(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "群A")], now=1000.0)

        directory.save()
        assert (data_root / DIRECTORY_FILENAME).exists()

        reloaded = ChannelDirectory(data_root)
        assert [e.name for e in reloaded.entries("feishu")] == ["群A"]
        assert reloaded.find("feishu", "oc_1") is not None
        # replace_platform 是桶替换原语,不驱动刷新时钟(updated_at 归 refresh())。
        assert reloaded.updated_at is None


class TestAliasOverlay:
    def test_alias_renames_discovered_entry_on_load(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "原始群名")], now=1.0)
        directory.save()

        # 手工直编别名文件(Hermes 同语义:覆盖层独立于目录文件)。
        aliases = {"feishu": {"oc_1": "我的别名群"}}
        (data_root / ALIASES_FILENAME).write_text(
            json.dumps(aliases, ensure_ascii=False), encoding="utf-8"
        )

        reloaded = ChannelDirectory(data_root)
        assert reloaded.find("feishu", "oc_1").name == "我的别名群"

    def test_alias_for_undiscovered_id_creates_placeholder(self, data_root: Path):
        (data_root / ALIASES_FILENAME).write_text(
            json.dumps({"feishu": {"oc_new": "还没首聊的群"}}), encoding="utf-8"
        )

        directory = ChannelDirectory(data_root)

        entry = directory.find("feishu", "oc_new")
        assert entry is not None
        assert entry.name == "还没首聊的群"  # 占位条目:新群可先命名后可达

    def test_alias_survives_rebuild_hermes_regression(self, data_root: Path):
        """Hermes 同款回归点:目录重建后别名仍生效(别名是持久覆盖层)。"""
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "旧名")], now=1.0)
        directory.save()
        directory.set_alias("feishu", "oc_1", "别名群")

        # 重建:桶整体替换,目录文件被重写。
        rebuilt = ChannelDirectory(data_root)
        rebuilt.replace_platform("feishu", [_entry("oc_1", "API 返回名")], now=2.0)
        rebuilt.save()

        final = ChannelDirectory(data_root)
        assert final.find("feishu", "oc_1").name == "别名群"

    def test_set_alias_writes_overlay_and_empty_name_deletes(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "原名")], now=1.0)
        directory.save()  # 目录先持久化,别名删除后重载走磁盘原始名

        directory.set_alias("feishu", "oc_1", "改名")
        assert directory.find("feishu", "oc_1").name == "改名"
        assert json.loads((data_root / ALIASES_FILENAME).read_text(encoding="utf-8")) == {
            "feishu": {"oc_1": "改名"}
        }

        directory.set_alias("feishu", "oc_1", "  ")  # 空名 = 删除别名
        reloaded = ChannelDirectory(data_root)
        assert reloaded.find("feishu", "oc_1").name == "原名"


class TestCorruptJsonTolerance:
    def test_corrupt_directory_file_degrades_to_empty(self, data_root: Path):
        (data_root / DIRECTORY_FILENAME).write_text("{not json", encoding="utf-8")

        directory = ChannelDirectory(data_root)

        assert directory.platforms() == []
        assert directory.updated_at is None

    def test_non_dict_directory_file_degrades_to_empty(self, data_root: Path):
        (data_root / DIRECTORY_FILENAME).write_text("[1, 2]", encoding="utf-8")

        assert ChannelDirectory(data_root).platforms() == []

    def test_corrupt_alias_file_is_ignored(self, data_root: Path):
        (data_root / ALIASES_FILENAME).write_text("!!", encoding="utf-8")

        directory = ChannelDirectory(data_root)

        assert directory.platforms() == []  # 别名损坏不产生占位条目

    def test_malformed_entries_are_dropped(self, data_root: Path):
        payload = {
            "updated_at": "2026-10-03T09:00:00",
            "platforms": {
                "feishu": [
                    {"chat_id": "oc_1", "name": "ok"},
                    {"name": "缺 chat_id"},
                    "not-a-dict",
                    {"chat_id": "", "name": "空 id"},
                ]
            },
        }
        (data_root / DIRECTORY_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        directory = ChannelDirectory(data_root)

        assert [e.chat_id for e in directory.entries("feishu")] == ["oc_1"]
        assert directory.age_seconds(now=1_000_000_000) is not None


class TestAtomicWrite:
    def test_save_leaves_no_tmp_file_and_is_utf8(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "中文群")], now=1.0)
        directory.save()

        target = data_root / DIRECTORY_FILENAME
        assert not target.with_suffix(".json.tmp").exists()
        assert "中文群" in target.read_text(encoding="utf-8")  # ensure_ascii=False

    def test_save_via_tmp_rename_keeps_old_content_on_write_failure(
        self, data_root: Path, monkeypatch
    ):
        """tmp+rename 原子性:替换失败时旧文件内容原样保留。"""
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "旧内容")], now=1.0)
        directory.save()
        target = data_root / DIRECTORY_FILENAME
        old_bytes = target.read_bytes()

        import shishi.push.directory as directory_module

        def exploding_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(directory_module, "_atomic_write_json", exploding_write)
        directory.replace_platform("feishu", [_entry("oc_2", "新内容")], now=2.0)
        directory.save()  # 只告警不抛

        assert target.read_bytes() == old_bytes

    def test_unwritable_root_degrades_to_memory_state(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        root.chmod(0o500)  # 只读目录(POSIX)
        try:
            directory = ChannelDirectory(root)
            directory.replace_platform("feishu", [_entry("oc_1", "群")], now=1.0)
            directory.save()  # best-effort:不抛

            assert directory.find("feishu", "oc_1") is not None  # 内存态仍在
        finally:
            root.chmod(0o700)


class TestPlatformBucketReplace:
    def test_replace_is_whole_bucket_not_merge(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform(
            "feishu", [_entry("oc_1", "群1"), _entry("oc_2", "群2")], now=1.0
        )

        # 重建只带回 oc_2:oc_1 消失(桶替换,不做条目合并)。
        directory.replace_platform("feishu", [_entry("oc_2", "群2")], now=2.0)

        assert [e.chat_id for e in directory.entries("feishu")] == ["oc_2"]

    def test_replace_dedupes_same_chat_id_first_wins(self, data_root: Path):
        directory = ChannelDirectory(data_root)

        directory.replace_platform(
            "feishu", [_entry("oc_1", "第一个"), _entry("oc_1", "第二个")], now=1.0
        )

        assert [e.name for e in directory.entries("feishu")] == ["第一个"]

    def test_other_platform_bucket_untouched(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform("feishu", [_entry("oc_1", "群", platform="feishu")], now=1.0)
        directory.replace_platform(
            "telegram", [_entry("12345", "私聊", platform="telegram")], now=2.0
        )

        assert [e.chat_id for e in directory.entries("feishu")] == ["oc_1"]
        assert [e.chat_id for e in directory.entries("telegram")] == ["12345"]
        assert directory.platforms() == ["feishu", "telegram"]

    def test_last_seen_defaults_to_refresh_stamp(self, data_root: Path):
        directory = ChannelDirectory(data_root)

        directory.replace_platform("feishu", [_entry("oc_1", "群")], now=1_700_000_000.0)

        assert directory.find("feishu", "oc_1").last_seen == 1_700_000_000.0


class FakeAdapter:
    """发现适配器测试替身:返回预置条目,可失败,可无发现能力。"""

    def __init__(self, entries=None, error: Exception | None = None) -> None:
        self.entries = entries or []
        self.error = error
        self.calls = 0

    async def discover_directory(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.entries)


class NoDiscoveryAdapter:
    """缺 discover_directory 的通道(core 四通道现状)。"""


class TestRefresh:
    def test_refresh_replaces_buckets_and_persists(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        adapter = FakeAdapter([_entry("oc_1", "群1"), _entry("oc_2", "群2")])

        counts = asyncio.run(directory.refresh({"feishu": adapter}, now=1_700_000_000.0))

        assert counts == {"feishu": 2}
        assert directory.updated_at is not None
        assert len(ChannelDirectory(data_root).entries("feishu")) == 2

    def test_refresh_failure_keeps_old_bucket_and_other_platforms(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        directory.replace_platform(
            "feishu", [_entry("oc_old", "旧群")], now=1.0
        )
        boom = FakeAdapter(error=RuntimeError("token 失效"))
        ok = FakeAdapter([_entry("123", "私聊", platform="telegram")])

        counts = asyncio.run(directory.refresh({"feishu": boom, "telegram": ok}, now=2.0))

        assert counts == {"telegram": 1}  # 失败平台不在结果
        assert [e.chat_id for e in directory.entries("feishu")] == ["oc_old"]  # 旧桶保留

    def test_refresh_skips_adapters_without_discovery(self, data_root: Path):
        directory = ChannelDirectory(data_root)

        counts = asyncio.run(directory.refresh({"feishu": NoDiscoveryAdapter()}, now=1.0))

        assert counts == {}
        assert "feishu" not in directory._platforms  # 未注册能力 ≠ 空桶

    def test_refresh_updated_at_drives_age(self, data_root: Path):
        directory = ChannelDirectory(data_root)
        assert directory.age_seconds(now=1_000.0) is None

        asyncio.run(
            directory.refresh({"feishu": FakeAdapter([_entry("oc_1", "群")])}, now=1_000.0)
        )

        assert directory.age_seconds(now=1_400.0) == pytest.approx(400.0)
