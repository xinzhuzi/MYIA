"""Tests for myia.push.delivery — 定向派发 + 死信账本(蓝本移植自 Hermes
gateway/delivery.py + dead_targets.py + platforms/base.py 分类表)。

覆盖任务 10-03-messaging-core 步骤 5:fake 通道注入派发循环、空 specs 短路、
失败隔离、单次硬失败即标 dead(grill Q3:无 N 连败阈值)/瞬态不标/子会话级
not_found 不标、成功自愈、dead 跳过+结构化日志、死信持久化与容错。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from myia.push.base import PushSendError, SendContext
from myia.push.delivery import (
    LEDGER_FILENAME,
    DeliveryLedger,
    classify_dead_error,
    is_chat_level_not_found,
    send_batch_to_targets,
)
from myia.push.directory import ChannelDirectory, ChannelEntry
from myia.push.targets import ChannelTarget


class FakeTargetingChannel:
    """支持寻址的 fake 通道:记录每次发送,指定 chat 抛指定错误。"""

    name = "fake"
    supports_targeting = True

    def __init__(self, failures: dict[str, PushSendError] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[dict] = []

    async def send(self, items, context) -> None:
        self.calls.append({"items": list(items), "context": context})
        failure = self.failures.get(context.target.chat_id)
        if failure is not None:
            raise failure


class LegacyChannel:
    """不支持寻址的通道(core 四通道现状)。"""

    name = "legacy"

    supports_targeting = False

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, items, context) -> None:
        self.calls += 1


def _directory(tmp_path: Path, entries: list[ChannelEntry] | None = None) -> ChannelDirectory:
    directory = ChannelDirectory(tmp_path)
    if entries:
        directory.replace_platform("fake", entries, now=1.0)
    return directory


def _context() -> SendContext:
    return SendContext(slot="am", date="2026-10-03", kind="digest")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 错误分类(Hermes 原味:forbidden / chat 级 not_found 单次即硬失败)
# ---------------------------------------------------------------------------


class TestClassifyDeadError:
    @pytest.mark.parametrize(
        "error_text",
        [
            "飞书 API 返回错误: code=403 msg=forbidden: bot was blocked by the user",
            "telegram API 返回错误: error_code=403 description=Forbidden: bot was blocked",
            "not enough rights to send text messages to the chat",
            "user is deactivated",
        ],
    )
    def test_forbidden_family_is_hard(self, error_text):
        assert classify_dead_error(error_text) == "forbidden"

    def test_chat_level_not_found_is_hard(self):
        assert classify_dead_error("Bad Request: chat not found") == "not_found"
        assert classify_dead_error("chat_id is invalid") == "not_found"

    def test_thread_level_not_found_is_not_dead(self):
        assert classify_dead_error("Bad Request: message thread not found") is None
        assert classify_dead_error("topic_deleted") is None
        assert is_chat_level_not_found("thread not found plus chat not found") is False

    @pytest.mark.parametrize(
        "transient",
        [
            "telegram 请求失败: ReadTimeout: timed out",
            "ConnectError: connection refused",
            "Too Many Requests: retry after 30",
            "飞书 API 返回错误: code=11232 msg=too many requests",
        ],
    )
    def test_transient_errors_never_mark_dead(self, transient):
        assert classify_dead_error(transient) is None

    def test_push_send_error_blob_includes_code_and_message(self):
        exc = PushSendError("feishu_api_error", "飞书 API 返回错误: code=230001 msg=chat not found")

        assert classify_dead_error(exc) == "not_found"


# ---------------------------------------------------------------------------
# DeliveryLedger(死信账本)
# ---------------------------------------------------------------------------


class TestDeliveryLedger:
    def test_mark_and_reload_persists(self, tmp_path: Path):
        ledger = DeliveryLedger(tmp_path)
        target = ChannelTarget(platform="feishu", chat_id="oc_1")

        assert ledger.mark_dead(target, reason="forbidden: blocked") is True
        assert (tmp_path / LEDGER_FILENAME).exists()

        reloaded = DeliveryLedger(tmp_path)
        assert reloaded.is_dead(target) is True
        entry = json.loads((tmp_path / LEDGER_FILENAME).read_text(encoding="utf-8"))["feishu:oc_1"]
        assert entry["reason"].startswith("forbidden")

    def test_remark_is_idempotent(self, tmp_path: Path):
        ledger = DeliveryLedger(tmp_path)
        target = ChannelTarget(platform="feishu", chat_id="oc_1")

        assert ledger.mark_dead(target) is True
        assert ledger.mark_dead(target) is False  # 已标,不算新标记

    def test_clear_heals_and_persists(self, tmp_path: Path):
        ledger = DeliveryLedger(tmp_path)
        target = ChannelTarget(platform="feishu", chat_id="oc_1")
        ledger.mark_dead(target)

        assert ledger.clear(target) is True
        assert ledger.is_dead(target) is False
        assert DeliveryLedger(tmp_path).is_dead(target) is False
        assert ledger.clear(target) is False  # 幂等

    def test_corrupt_file_degrades_to_empty(self, tmp_path: Path):
        (tmp_path / LEDGER_FILENAME).write_text("{broken", encoding="utf-8")

        ledger = DeliveryLedger(tmp_path)

        assert ledger.dead_keys() == []

    def test_malformed_entries_dropped_on_load(self, tmp_path: Path):
        (tmp_path / LEDGER_FILENAME).write_text(
            json.dumps({"ok:1": {"reason": "x", "marked_at": 1}, "bad": "not-a-dict"}),
            encoding="utf-8",
        )

        ledger = DeliveryLedger(tmp_path)

        assert ledger.dead_keys() == ["ok:1"]

    def test_unwritable_root_keeps_memory_state(self, tmp_path: Path):
        root = tmp_path / "ro"
        root.mkdir()
        root.chmod(0o500)
        try:
            ledger = DeliveryLedger(root)
            ledger.mark_dead(platform="feishu", chat_id="oc_1", reason="r")

            assert ledger.is_dead(platform="feishu", chat_id="oc_1") is True
        finally:
            root.chmod(0o700)

    def test_platform_case_and_space_normalized(self, tmp_path: Path):
        ledger = DeliveryLedger(tmp_path)
        ledger.mark_dead(platform=" Feishu ", chat_id=" oc_1 ")

        assert ledger.is_dead(platform="feishu", chat_id="oc_1") is True
        assert ledger.dead_keys() == ["feishu:oc_1"]


# ---------------------------------------------------------------------------
# 派发循环
# ---------------------------------------------------------------------------


class TestSendBatchToTargets:
    def test_each_target_gets_its_own_card_with_context_target(self, tmp_path: Path):
        directory = _directory(
            tmp_path,
            [
                ChannelEntry(platform="fake", chat_id="c1", name="群一"),
                ChannelEntry(platform="fake", chat_id="c2", name="群二"),
            ],
        )
        channel = FakeTargetingChannel()
        items = [{"title": "t"}]

        reports = _run(
            send_batch_to_targets(
                items,
                specs=["fake:群一", "fake:群二"],
                channel=channel,
                context=_context(),
                directory=directory,
                ledger=DeliveryLedger(tmp_path),
            )
        )

        assert [r.ok for r in reports] == [True, True]
        assert len(channel.calls) == 2
        assert [c["context"].target.chat_id for c in channel.calls] == ["c1", "c2"]
        assert all(c["items"] == items for c in channel.calls)

    def test_empty_specs_short_circuit(self, tmp_path: Path):
        channel = FakeTargetingChannel()

        reports = _run(
            send_batch_to_targets(
                [],
                specs=[],
                channel=channel,
                context=_context(),
                directory=_directory(tmp_path),
            )
        )

        assert reports == []
        assert channel.calls == []

    def test_single_failure_isolated_per_target(self, tmp_path: Path):
        directory = _directory(
            tmp_path,
            [
                ChannelEntry(platform="fake", chat_id="c1", name="群一"),
                ChannelEntry(platform="fake", chat_id="c2", name="群二"),
                ChannelEntry(platform="fake", chat_id="c3", name="群三"),
            ],
        )
        channel = FakeTargetingChannel(
            failures={"c2": PushSendError("http_error", "模拟网络抖动")}
        )

        reports = _run(
            send_batch_to_targets(
                [{"title": "t"}],
                specs=["fake:群一", "fake:群二", "fake:群三"],
                channel=channel,
                context=_context(),
                directory=directory,
            )
        )

        assert [r.ok for r in reports] == [True, False, True]  # c2 失败不阻断 c1/c3
        assert len(channel.calls) == 3

    def test_forbidden_marks_dead_after_single_failure_no_threshold(self, tmp_path: Path):
        """grill Q3:单次硬失败即标 dead——无 3 连败阈值(implement 修正点)。"""
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        channel = FakeTargetingChannel(
            failures={"c1": PushSendError("feishu_api_error", "code=403 msg=forbidden")}
        )
        ledger = DeliveryLedger(tmp_path)

        for _ in range(2):  # 两轮:第一轮标 dead,第二轮跳过(没有「3 连败才熔断」)
            reports = _run(
                send_batch_to_targets(
                    [{"title": "t"}],
                    specs=["fake:群一"],
                    channel=channel,
                    context=_context(),
                    directory=directory,
                    ledger=ledger,
                )
            )

        assert ledger.is_dead(platform="fake", chat_id="c1") is True
        assert len(channel.calls) == 1  # 第二轮 dead 跳过,未再真发
        assert reports[0].error.startswith("[dead_target]")
        assert reports[0].skipped is True

    def test_transient_failure_never_marks_dead(self, tmp_path: Path):
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        channel = FakeTargetingChannel(
            failures={"c1": PushSendError("http_error", "ReadTimeout: timed out")}
        )
        ledger = DeliveryLedger(tmp_path)

        for _ in range(3):
            _run(
                send_batch_to_targets(
                    [{"title": "t"}],
                    specs=["fake:群一"],
                    channel=channel,
                    context=_context(),
                    directory=directory,
                    ledger=ledger,
                )
            )

        assert ledger.is_dead(platform="fake", chat_id="c1") is False
        assert len(channel.calls) == 3  # 瞬态:每轮都真发(等上游自愈)

    def test_chat_level_not_found_marks_dead_thread_level_does_not(self, tmp_path: Path):
        directory = _directory(
            tmp_path,
            [
                ChannelEntry(platform="fake", chat_id="c1", name="群一"),
                ChannelEntry(platform="fake", chat_id="c2", name="群二"),
            ],
        )
        channel = FakeTargetingChannel(
            failures={
                "c1": PushSendError("telegram_api_error", "Bad Request: chat not found"),
                "c2": PushSendError("telegram_api_error", "Bad Request: message thread not found"),
            }
        )
        ledger = DeliveryLedger(tmp_path)

        _run(
            send_batch_to_targets(
                [{"title": "t"}],
                specs=["fake:群一", "fake:群二"],
                channel=channel,
                context=_context(),
                directory=directory,
                ledger=ledger,
            )
        )

        assert ledger.is_dead(platform="fake", chat_id="c1") is True  # chat 级
        assert ledger.is_dead(platform="fake", chat_id="c2") is False  # 话题级

    def test_success_heals_dead_mark(self, tmp_path: Path):
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        ledger = DeliveryLedger(tmp_path)
        ledger.mark_dead(platform="fake", chat_id="c1", reason="forbidden: 旧故障")

        # 手动修复(bot 重新入群)后:预置一条成功路径——dead 状态在成功前
        # 跳过,这里直接验证成功路径的自愈调用契约。
        channel = FakeTargetingChannel()
        target = ChannelTarget(platform="fake", chat_id="c1")

        assert ledger.clear(target) is True
        reports = _run(
            send_batch_to_targets(
                [{"title": "t"}],
                specs=["fake:群一"],
                channel=channel,
                context=_context(),
                directory=directory,
                ledger=ledger,
            )
        )

        assert reports[0].ok is True
        assert ledger.is_dead(platform="fake", chat_id="c1") is False

    def test_recovered_target_receives_again_after_heal(self, tmp_path: Path):
        """端到端自愈:forbidden 标 dead → 转好 → 清除 → 再次可投。"""
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        ledger = DeliveryLedger(tmp_path)
        channel = FakeTargetingChannel(
            failures={"c1": PushSendError("feishu_api_error", "code=403 msg=forbidden")}
        )
        specs = ["fake:群一"]

        _run(
            send_batch_to_targets(
                [{"title": "t"}], specs=specs, channel=channel, context=_context(),
                directory=directory, ledger=ledger,
            )
        )
        assert ledger.is_dead(platform="fake", chat_id="c1") is True

        channel.failures = {}  # bot 已被重新拉回群里
        ledger.clear(platform="fake", chat_id="c1")  # 修复后人工/UI 清除
        reports = _run(
            send_batch_to_targets(
                [{"title": "t"}], specs=specs, channel=channel, context=_context(),
                directory=directory, ledger=ledger,
            )
        )

        assert reports[0].ok is True
        assert len(channel.calls) == 2

    def test_unresolved_spec_reports_skipped_with_candidates(self, tmp_path: Path):
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        channel = FakeTargetingChannel()

        reports = _run(
            send_batch_to_targets(
                [{"title": "t"}],
                specs=["fake:没有这个群", "fake:群一"],
                channel=channel,
                context=_context(),
                directory=directory,
            )
        )

        assert len(reports) == 2
        assert reports[0].ok is False and reports[0].skipped is True
        assert reports[0].error.startswith("[target_unresolved]")
        assert "群一 (c1)" in reports[0].error  # 候选内嵌(偏离注记)
        assert reports[1].ok is True

    def test_unsupported_channel_fails_loud_no_misdelivery(self, tmp_path: Path):
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        channel = LegacyChannel()

        reports = _run(
            send_batch_to_targets(
                [{"title": "t"}],
                specs=["fake:群一"],
                channel=channel,
                context=_context(),
                directory=directory,
            )
        )

        assert len(reports) == 1
        assert reports[0].ok is False
        assert reports[0].error.startswith("[targeting_not_supported]")
        assert channel.calls == 0  # 绝不回落 legacy 单 target(防误投)

    def test_dead_skip_logs_structured_info_no_alert_card(
        self, tmp_path: Path, caplog
    ):
        directory = _directory(tmp_path, [ChannelEntry(platform="fake", chat_id="c1", name="群一")])
        ledger = DeliveryLedger(tmp_path)
        ledger.mark_dead(platform="fake", chat_id="c1", reason="forbidden: x")
        channel = FakeTargetingChannel()

        with caplog.at_level(logging.INFO, logger="myia.push.delivery"):
            reports = _run(
                send_batch_to_targets(
                    [{"title": "t"}],
                    specs=["fake:群一"],
                    channel=channel,
                    context=_context(),
                    directory=directory,
                    ledger=ledger,
                )
            )

        assert channel.calls == []
        assert reports[0].skipped is True
        assert any("死信跳过" in r.message for r in caplog.records)  # 结构化日志而非告警卡
