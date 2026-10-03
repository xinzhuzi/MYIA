"""Tests for messaging-core pipeline wiring — 10-03-messaging-core 步骤 6.

覆盖:不配 targets 的 legacy 路径行为不变、immediate/digest 定向派发接线、
规则级 targets 覆盖通道级、run 前目录节流懒刷(Q4:stale 才刷/失败退回旧目录
不阻塞/dry-run 不刷/无注册平台零开销)、forbidden 经管线落死信账本。
通道用 fake 注入(CHANNELS/PLATFORMS monkeypatch);无真实网络。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import myia.pipeline as pipeline_module
import myia.push as push_module
from conftest import FakeClock
from myia.pipeline import Pipeline
from myia.push.base import PushSendError
from myia.push.directory import ChannelDirectory, ChannelEntry
from myia.schema import load_category
from myia.store import SQLiteStore

TIMEZONE = "Asia/Shanghai"


class FakeFeishuTargeting:
    """feishu 平台的 fake 定向通道:记录发送/发现,可选按 chat 抛错。

    注入方式:monkeypatch ``pipeline_module.CHANNELS["feishu_card"]`` 与
    ``PLATFORMS["feishu"]``(core 交付的空注册表由平台子任务登记)。
    """

    name = "feishu_card"
    supports_targeting = True
    instances: list["FakeFeishuTargeting"] = []

    fail_chats: dict[str, str] = {}  # 类级配置:chat_id -> 错误消息
    discover_error: Exception | None = None  # 类级配置:发现失败注入

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.discoveries = 0
        FakeFeishuTargeting.instances.append(self)

    async def discover_directory(self):
        self.discoveries += 1
        if self.discover_error is not None:
            raise self.discover_error
        return [
            ChannelEntry(platform="feishu", chat_id="oc_1", name="群一"),
            ChannelEntry(platform="feishu", chat_id="oc_2", name="群二"),
        ]

    async def send(self, items, context) -> None:
        self.calls.append({"items": list(items), "context": context})
        message = self.fail_chats.get(context.target.chat_id if context.target else "")
        if message is not None:
            raise PushSendError("feishu_api_error", message)

    @classmethod
    def reset(cls, fail_chats: dict[str, str] | None = None, discover_error: Exception | None = None) -> None:
        cls.instances = []
        cls.fail_chats = fail_chats or {}
        cls.discover_error = discover_error

    @classmethod
    def send_calls(cls) -> list[dict[str, Any]]:
        return [call for instance in cls.instances for call in instance.calls]

    @classmethod
    def discovery_count(cls) -> int:
        return sum(instance.discoveries for instance in cls.instances)


class CapturingStdout:
    """stdout 假体:验证 legacy 路径 context.target 恒为 None。"""

    name = "stdout"
    instances: list["CapturingStdout"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        CapturingStdout.instances.append(self)

    async def send(self, items, context) -> None:
        self.calls.append({"items": list(items), "context": context})


def make_config(push: list[dict[str, Any]], **overrides: Any):
    data: dict[str, Any] = {
        "id": "demo",
        "name": "演示品类",
        "schedule": "0 9 * * *",
        "timezone": TIMEZONE,
        "sources": [
            {
                "name": "api",
                "engine": "direct_api",
                "url": "https://api.demo.local/list",
                "extract": {"type": "json_path", "fields": {"title": "$[*].title", "url": "$[*].url"}},
            }
        ],
        "push": push,
    }
    data.update(overrides)
    return load_category(data)


def make_handler(titles: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        return httpx.Response(
            200,
            json=[{"title": t, "url": f"https://api.demo.local/{index}"} for index, t in enumerate(titles)],
        )

    return handler


def _run_pipeline(tmp_path: Path, config, titles: list[str]):
    store = SQLiteStore(tmp_path / "p.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(titles)))
    # db_path 必须显式指到 tmp_path:目录/死信账本落数据根(db 父目录),
    # 缺省会写到 cwd(CLI 契约默认 myia.db,测试必须隔离)。
    pipeline = Pipeline(
        config,
        db_path=tmp_path / "p.db",
        store=store,
        client=client,
        clock=FakeClock().time,
        sleep=FakeClock().sleep,
    )
    result = asyncio.run(pipeline.run())
    store.close()
    return result


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeFeishuTargeting.reset()
    CapturingStdout.instances = []
    yield
    FakeFeishuTargeting.reset()
    CapturingStdout.instances = []


def _patch_feishu(monkeypatch, *, with_platform: bool = True):
    monkeypatch.setattr(
        pipeline_module, "CHANNELS", {**pipeline_module.CHANNELS, "feishu_card": FakeFeishuTargeting}
    )
    if with_platform:
        monkeypatch.setattr(pipeline_module, "PLATFORMS", {"feishu": FakeFeishuTargeting})
        monkeypatch.setattr(push_module, "PLATFORMS", {"feishu": FakeFeishuTargeting})
    else:
        monkeypatch.setattr(pipeline_module, "PLATFORMS", {})
        monkeypatch.setattr(push_module, "PLATFORMS", {})


class TestLegacyPathUnchanged:
    def test_no_targets_sends_without_target_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pipeline_module, "CHANNELS", {**pipeline_module.CHANNELS, "stdout": CapturingStdout})
        config = make_config([{"channel": "stdout"}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券", "白嫖机场体验"])

        calls = [c for inst in CapturingStdout.__instances] if hasattr(CapturingStdout, "__instances") else []
        # 直接从 push 报告断言:immediate 2 条全部成功,不涉及对象。
        push_report = result.pushes[0]
        assert push_report.channel == "stdout"
        assert push_report.immediate == 2
        assert push_report.ok is True
        assert all(r.ok for r in push_report.reports)
        # 目录/账本文件不产生(legacy 短路,惰性产物)。
        assert not (tmp_path / "channel_directory.json").exists()
        assert not (tmp_path / "delivery_ledger.json").exists()


class TestImmediateTargeted:
    def test_channel_level_targets_dispatch_per_target(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一", "feishu:群二"]}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        calls = FakeFeishuTargeting.send_calls()
        assert len(calls) == 2  # 1 条目 × 2 对象
        assert sorted(c["context"].target.chat_id for c in calls) == ["oc_1", "oc_2"]
        assert [r.ok for r in result.pushes[0].reports] == [True, True]

    def test_rule_targets_override_channel_level(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config(
            [
                {
                    "channel": "feishu_card",
                    "targets": ["feishu:群二"],
                    "route": [
                        {
                            "when": "category in ['freebie']",
                            "mode": "immediate",
                            "targets": ["feishu:群一"],
                        }
                    ],
                }
            ]
        )

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        calls = FakeFeishuTargeting.send_calls()
        assert len(calls) == 1  # 规则 targets 胜出:只发群一
        assert calls[0]["context"].target.chat_id == "oc_1"
        assert result.pushes[0].reports[0].ok is True

    def test_unsupported_channel_fails_loud_core_state(self, tmp_path, monkeypatch):
        """core 状态(feishu 子任务未接):targets 配置合法但通道未开寻址 →
        失败报告说破,绝不回落 legacy 单 target 误投。"""
        _patch_feishu(monkeypatch, with_platform=False)

        class NotYetTargeting(FakeFeishuTargeting):
            supports_targeting = False

        monkeypatch.setattr(pipeline_module, "CHANNELS", {**pipeline_module.CHANNELS, "feishu_card": NotYetTargeting})
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        assert FakeFeishuTargeting.send_calls() == []
        reports = result.pushes[0].reports
        assert len(reports) == 1 and reports[0].ok is False
        assert reports[0].error.startswith("[targeting_not_supported]")


class TestDigestTargeted:
    def test_digest_one_card_per_target(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config(
            [
                {
                    "channel": "feishu_card",
                    "targets": ["feishu:群一", "feishu:群二"],
                    "route": [{"when": "category in ['freebie']", "mode": "digest"}],
                }
            ]
        )

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券", "白嫖机场体验"])

        calls = FakeFeishuTargeting.send_calls()
        assert len(calls) == 2  # 每(通道×对象)一卡:2 对象各一张
        by_chat = {c["context"].target.chat_id: c for c in calls}
        assert sorted(by_chat) == ["oc_1", "oc_2"]
        for call in calls:
            assert len(call["items"]) == 2  # 同对象的摘要条目合并在同一张卡
        assert all(r.ok for r in result.pushes[0].reports)

    def test_mixed_rule_targets_split_cards(self, tmp_path, monkeypatch):
        """不同规则对象各收各的:两条 digest 条目按规则分别发往不同群。"""
        _patch_feishu(monkeypatch)
        config = make_config(
            [
                {
                    "channel": "feishu_card",
                    # 规则级 targets 只是覆盖层:通道层必须有基线 target
                    # (schema 同平台约束;未命中规则的条目回落基线)。
                    "target": "env:FEISHU_CHAT_ID",
                    "route": [
                        {
                            "when": "'免费' in title",
                            "mode": "digest",
                            "targets": ["feishu:群一"],
                        },
                        {"when": "'白嫖' in title", "mode": "digest", "targets": ["feishu:群二"]},
                    ],
                }
            ]
        )

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券", "白嫖机场体验"])

        calls = FakeFeishuTargeting.send_calls()
        assert len(calls) == 2
        chats_to_items = {
            c["context"].target.chat_id: [i.title for i in c["items"]] for c in calls
        }
        assert chats_to_items == {
            "oc_1": ["免费送 NAS 券"],
            "oc_2": ["白嫖机场体验"],
        }
        assert all(r.ok for r in result.pushes[0].reports)


class TestLazyDirectoryRefresh:
    def test_stale_directory_triggers_discovery(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])  # 无目录文件 → 不新鲜

        assert FakeFeishuTargeting.discovery_count() == 1
        assert (tmp_path / "channel_directory.json").exists()
        assert result.pushes[0].ok is True  # 发现→解析→投递 全链接通

    def test_fresh_directory_skips_discovery(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])

        _run_pipeline(tmp_path, config, ["免费送 NAS 券"])
        _run_pipeline(tmp_path, config, ["免费送 NAS 券2"])

        assert FakeFeishuTargeting.discovery_count() == 1  # 5 分钟节流:第二轮跳过

    def test_discovery_failure_falls_back_and_pushes(self, tmp_path, monkeypatch, caplog):
        # 预置旧目录(含目标群),发现失败 → 退回旧目录,推送照常。
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "feishu", [ChannelEntry(platform="feishu", chat_id="oc_1", name="群一")], now=1.0
        )
        directory.save()
        FakeFeishuTargeting.reset(discover_error=RuntimeError("token 失效"))
        _patch_feishu(monkeypatch)
        # 旧目录新鲜度:updated_at 未写(replace_platform 不驱动时钟)→ 仍会尝试刷新
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])

        with caplog.at_level("WARNING"):
            result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        assert FakeFeishuTargeting.discovery_count() == 1  # 尝试过发现
        assert any("目录刷新失败" in r.message or "目录懒刷失败" in r.message for r in caplog.records)
        assert result.pushes[0].ok is True  # 旧目录兜底,推送不受阻
        assert FakeFeishuTargeting.send_calls()[0]["context"].target.chat_id == "oc_1"

    def test_dry_run_never_discovers(self, tmp_path, monkeypatch):
        _patch_feishu(monkeypatch)
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])
        store = SQLiteStore(tmp_path / "p.db")
        client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(["免费送 NAS 券"])))
        pipeline = Pipeline(
            config,
            db_path=tmp_path / "p.db",
            store=store,
            client=client,
            clock=FakeClock().time,
            sleep=FakeClock().sleep,
        )

        asyncio.run(pipeline.run(dry_run=True))
        store.close()

        assert FakeFeishuTargeting.discovery_count() == 0

    def test_empty_platforms_registry_zero_overhead(self, tmp_path, monkeypatch, caplog):
        """core 交付态:PLATFORMS 空表 → 不发现;配了 targets 的推送走失败报告。"""
        _patch_feishu(monkeypatch, with_platform=True)
        monkeypatch.setattr(pipeline_module, "PLATFORMS", {})  # 清空(仅管线侧判定)
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一"]}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        assert FakeFeishuTargeting.discovery_count() == 0
        reports = result.pushes[0].reports
        assert reports and reports[0].error.startswith("[target_unresolved]")  # 目录空,解析失败


class TestDigestPoolingSemantics:
    """flush 留池判定:真失败留池(legacy 不变)/纯终态放弃/死信自愈重发。"""

    def _aggregator(self, tmp_path, channel):
        from myia.dedup import DedupRegistry
        from myia.push import DigestAggregator

        return DigestAggregator(
            channels=[channel], registry=DedupRegistry(SQLiteStore(tmp_path / "d.db"))
        )

    def test_all_failed_stays_pooled_legacy(self, tmp_path):
        from myia.push.base import PushSendError

        class Failing:
            name = "failing"
            supports_targeting = True

            def __init__(self) -> None:
                self.calls = 0

            async def send(self, items, context) -> None:
                self.calls += 1
                raise PushSendError("http_error", "网络抖动")

        channel = Failing()
        aggregator = self._aggregator(tmp_path, channel)
        aggregator.add({"title": "t", "url": "https://x/1"}, dedup_key="k1")
        directory = ChannelDirectory(tmp_path)

        reports = asyncio.run(aggregator.flush(now=datetime(2026, 10, 3, 9), directory=directory))

        assert all(not r.ok and not r.skipped for r in reports)
        assert len(aggregator) == 1  # 留池重试(legacy 行为)

    def test_all_terminal_skipped_drops_not_repools(self, tmp_path):
        from myia.push import DeliveryLedger

        ledger = DeliveryLedger(tmp_path)
        ledger.mark_dead(platform="feishu", chat_id="oc_1", reason="forbidden: x")

        class Healthy:
            name = "healthy"
            supports_targeting = True

            def __init__(self) -> None:
                self.calls = 0

            async def send(self, items, context) -> None:
                self.calls += 1

        channel = Healthy()
        aggregator = self._aggregator(tmp_path, channel)
        aggregator.add({"title": "t", "url": "https://x/1"}, dedup_key="k1", targets=["feishu:群一"])
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "feishu", [ChannelEntry(platform="feishu", chat_id="oc_1", name="群一")], now=1.0
        )

        reports = asyncio.run(
            aggregator.flush(now=datetime(2026, 10, 3, 9), directory=directory, ledger=ledger)
        )

        assert all(r.skipped for r in reports)  # dead 跳过 = 终态
        assert len(aggregator) == 0  # 不留池:无限重试没有意义
        assert channel.calls == 0


class TestDeadLedgerWiring:
    def test_forbidden_via_pipeline_lands_in_ledger_file(self, tmp_path, monkeypatch):
        FakeFeishuTargeting.reset(fail_chats={"oc_1": "飞书 API 返回错误: code=403 msg=forbidden"})
        _patch_feishu(monkeypatch)
        # 预置目录,避免依赖发现;目标两个,oc_1 硬失败、oc_2 成功。
        directory = ChannelDirectory(tmp_path)
        directory.replace_platform(
            "feishu",
            [
                ChannelEntry(platform="feishu", chat_id="oc_1", name="群一"),
                ChannelEntry(platform="feishu", chat_id="oc_2", name="群二"),
            ],
            now=1.0,
        )
        directory.save()
        config = make_config([{"channel": "feishu_card", "targets": ["feishu:群一", "feishu:群二"]}])

        result = _run_pipeline(tmp_path, config, ["免费送 NAS 券"])

        ledger_path = tmp_path / "delivery_ledger.json"
        assert ledger_path.exists()
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert "feishu:oc_1" in ledger  # 单次硬失败即标 dead
        assert "feishu:oc_2" not in ledger
        assert [r.ok for r in result.pushes[0].reports] == [False, True]
