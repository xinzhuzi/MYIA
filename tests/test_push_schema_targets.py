"""Tests for schema ``targets`` fields — 10-03-messaging-core 步骤 3.

覆盖:格式校验(空串拒/去重/元素形态)、同平台约束(通道级与规则级)、
不支持寻址通道拒配、targets 在场时 target 可省、旧 YAML 黄金回归
(改动前 7 个已跟踪夹具的加载结果逐字节等价)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myia.schema import CHANNEL_PLATFORMS, LoadError, load_category

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "push_targets_golden_before.json"


def _minimal_data() -> dict:
    return {
        "id": "demo",
        "name": "Demo",
        "schedule": "0 9 * * *",
        "sources": [{"name": "example", "url": "https://example.com/list?page={page}"}],
    }


def _load_error(data) -> LoadError:
    with pytest.raises(LoadError) as excinfo:
        load_category(data)
    return excinfo.value


def _error_of_type(load_error: LoadError, error_type: str):
    matches = [d for d in load_error.errors if d.error_type == error_type]
    assert matches, f"expected error_type={error_type!r}, got {load_error.errors}"
    return matches[0]


class TestChannelPlatformMap:
    def test_builtin_literal_map(self):
        # design D4:内置字面表 feishu_card→feishu、telegram→telegram;
        # webhook/stdout 不在表内(不支持目录寻址)。
        assert CHANNEL_PLATFORMS == {"feishu_card": "feishu", "telegram": "telegram"}


class TestTargetsFormat:
    def test_valid_targets_load(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "feishu_card",
                "targets": ["feishu:AI中转站合伙人群", "feishu:oc_xxx1"],
            }
        ]

        cfg = load_category(data)

        push = cfg.push[0]
        assert push.target is None  # targets 在场时 target 可省
        assert push.targets == ["feishu:AI中转站合伙人群", "feishu:oc_xxx1"]

    @pytest.mark.parametrize(
        "bad_element",
        ["", "   ", "feishu", "feishu:", ":x", "Feishu:群", "fei-shu:群"],
    )
    def test_bad_element_rejected(self, bad_element):
        data = _minimal_data()
        data["push"] = [{"channel": "feishu_card", "targets": [bad_element]}]

        detail = _error_of_type(_load_error(data), "invalid_target_spec")
        assert detail.path == "$.push[0].targets"

    def test_non_string_element_rejected_by_type_guard(self):
        # 非字符串元素在进入字段校验器前就被 pydantic 类型守卫拒
        # (结构化 string_type,同为加载期拒绝)。
        data = _minimal_data()
        data["push"] = [{"channel": "feishu_card", "targets": [42]}]

        detail = _error_of_type(_load_error(data), "string_type")
        assert detail.path == "$.push[0].targets[0]"

    def test_duplicates_deduped_preserving_order(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "feishu_card",
                "targets": ["feishu:a群", "feishu:b群", "feishu:a群"],
            }
        ]

        cfg = load_category(data)

        assert cfg.push[0].targets == ["feishu:a群", "feishu:b群"]

    def test_rule_targets_format_checked(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "feishu_card",
                "target": "env:FEISHU_CHAT_ID",
                "route": [{"when": "score >= 8", "mode": "immediate", "targets": ["feishu"]}],
            }
        ]

        _error_of_type(_load_error(data), "invalid_target_spec")


class TestSamePlatformConstraint:
    def test_cross_platform_element_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "feishu_card",
                "targets": ["feishu:群", "telegram:12345"],  # feishu 条目混 telegram
            }
        ]

        detail = _error_of_type(_load_error(data), "platform_mismatch")
        assert detail.path == "$.push[0].targets"
        assert "telegram:12345" in detail.message

    def test_rule_targets_cross_platform_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "telegram",
                "target": "env:TG_CHAT_ID",
                "route": [
                    {
                        "when": "score >= 8",
                        "mode": "immediate",
                        "targets": ["telegram:123", "feishu:群"],
                    }
                ],
            }
        ]

        detail = _error_of_type(_load_error(data), "platform_mismatch")
        assert detail.path == "$.push[0].route[0].targets"

    def test_same_platform_rule_targets_accepted(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "telegram",
                "target": "env:TG_CHAT_ID",
                "route": [
                    {"when": "score >= 8", "mode": "immediate", "targets": ["telegram:123"]}
                ],
            }
        ]

        assert load_category(data).push[0].route[0].targets == ["telegram:123"]


class TestUnsupportedChannels:
    def test_webhook_targets_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {"channel": "webhook", "target": "env:HOOK_URL", "targets": ["feishu:群"]}
        ]

        detail = _error_of_type(_load_error(data), "targeting_not_supported")
        assert detail.path == "$.push[0].targets"

    def test_stdout_targets_rejected(self):
        data = _minimal_data()
        data["push"] = [{"channel": "stdout", "targets": ["feishu:群"]}]

        _error_of_type(_load_error(data), "targeting_not_supported")

    def test_stdout_rule_targets_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "stdout",
                "route": [{"when": "score >= 8", "mode": "immediate", "targets": ["feishu:群"]}],
            }
        ]

        detail = _error_of_type(_load_error(data), "targeting_not_supported")
        assert detail.path == "$.push[0].route[0].targets"

    def test_webhook_rule_targets_rejected(self):
        data = _minimal_data()
        data["push"] = [
            {
                "channel": "webhook",
                "target": "env:HOOK_URL",
                "route": [{"when": "score >= 8", "mode": "digest", "targets": ["telegram:x"]}],
            }
        ]

        _error_of_type(_load_error(data), "targeting_not_supported")


class TestMissingTargetRelaxation:
    def test_targets_only_entry_without_target_accepted(self):
        data = _minimal_data()
        data["push"] = [{"channel": "feishu_card", "targets": ["feishu:群"]}]

        cfg = load_category(data)

        assert cfg.push[0].target is None
        assert cfg.push[0].targets == ["feishu:群"]

    def test_neither_target_nor_targets_rejected(self):
        data = _minimal_data()
        data["push"] = [{"channel": "feishu_card"}]

        detail = _error_of_type(_load_error(data), "missing_target")
        assert detail.path == "$.push[0].target"
        assert "targets" in detail.message  # 错误信息同时指出两条出路

    def test_legacy_target_alone_still_valid(self):
        data = _minimal_data()
        data["push"] = [{"channel": "feishu_card", "target": "env:FEISHU_CHAT_ID"}]

        cfg = load_category(data)

        assert cfg.push[0].target == "env:FEISHU_CHAT_ID"
        assert cfg.push[0].targets == []


class TestGoldenRegression:
    def test_legacy_yaml_load_byte_identical_to_before(self):
        """黄金回归:改动前捕获的 7 个已跟踪夹具,除新增空 ``targets`` 字段外
        加载结果逐字段等价。

        黄金文件在 schema 改动前生成(model_dump 快照);夹具都不含 targets
        → 新字段只能是空列表缺省,其余任何字段/取值/结构的变化都判失败
        (向后兼容承诺的机器验证)。注:PRD 措辞「逐字节等价」按 additive
        字段语义执行——model_dump 必然多出 ``targets: []``,除此之外零漂移。
        """
        from myia.schema import load_category_file

        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert golden, "黄金文件为空:基线生成失败"
        for name, expected in golden.items():
            cfg = load_category_file(Path(name))
            actual = cfg.model_dump(mode="json")
            _strip_additive_targets(actual)
            assert actual == expected, f"{name} 加载结果与改动前不等价"


def _strip_additive_targets(dump: dict) -> None:
    """删除本任务新增的空 ``targets`` 键(旧 YAML 不可能配置它们)。"""
    for push in dump.get("push", []):
        assert push.pop("targets") == []
        for rule in push.get("route", []):
            assert rule.pop("targets") == []
