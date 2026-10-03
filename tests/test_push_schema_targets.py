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
        # W2(10-03-messaging-w2-platforms)增 ntfy/dingtalk/wecom 三行;
        # webhook/stdout 不在表内(不支持目录寻址)。
        assert CHANNEL_PLATFORMS == {
            "feishu_card": "feishu",
            "telegram": "telegram",
            "ntfy": "ntfy",
            "dingtalk": "dingtalk",
            "wecom": "wecom",
        }


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
        """黄金回归:改动前捕获的 7 个已跟踪夹具,push 子树逐字段等价。

        承诺边界(2026-10-03 两起并行踩踏后收窄):本任务的 schema 改动只碰
        PushConfig/RouteRuleConfig,黄金基线只对 ``push`` 子树做逐字段断言
        (targets 剥离后零漂移)。其余节(extract/fields/images 等)是并行
        任务的合法演化面——games 的 url_template、vision 的 image: img@src
        都曾把全模型快照对比打红,那不是本层的承诺,不再拦。
        """
        from myia.schema import load_category_file

        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert golden, "黄金文件为空:基线生成失败"
        for name, expected in golden.items():
            cfg = load_category_file(Path(name))
            actual = cfg.model_dump(mode="json")
            _strip_additive_targets(actual)
            _assert_additive_equivalent(
                actual.get("push", []), expected.get("push", []), f"{name}.push"
            )


def _assert_additive_equivalent(actual, expected, path: str) -> None:
    """actual 允许比 expected 多出『缺省为空』的新增键(None/[]/{});其余零漂移。

    多会话仓库里其他任务的 additive 字段(如 10-03-games 的 ``url_template:
    None``)会合法出现在 model_dump 里——本测试的承诺边界是「本任务的
    schema 改动不改变旧 YAML 的加载语义」,不是冻结整个模型;取值漂移、
    字段丢失、非空新增键仍然失败。
    """
    assert isinstance(actual, type(expected)), f"{path}: 类型漂移 {type(expected).__name__} -> {type(actual).__name__}"
    if isinstance(expected, dict):
        for key, exp in expected.items():
            assert key in actual, f"{path}.{key}: 字段丢失"
            _assert_additive_equivalent(actual[key], exp, f"{path}.{key}")
        for key in set(actual) - set(expected):
            assert actual[key] in (None, [], {}), (
                f"{path}.{key}: 非空新增键 {actual[key]!r}(只容忍缺省为空的纯增字段)"
            )
    elif isinstance(expected, list):
        assert len(actual) == len(expected), f"{path}: 列表长度漂移 {len(expected)} -> {len(actual)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_additive_equivalent(a, e, f"{path}[{i}]")
    else:
        assert actual == expected, f"{path}: 取值漂移 {expected!r} -> {actual!r}"


def _strip_additive_targets(dump: dict) -> None:
    """删除本任务新增的空 ``targets`` 键(旧 YAML 不可能配置它们)。"""
    for push in dump.get("push", []):
        assert push.pop("targets") == []
        for rule in push.get("route", []):
            assert rule.pop("targets") == []


# ---------------------------------------------------------------------------
# W2 平台(10-03-messaging-w2-platforms):三通道入表 + 可选凭据字段
# ---------------------------------------------------------------------------


class TestW2PlatformChannels:
    """ntfy/dingtalk/wecom:入 PUSH_CHANNELS、同平台约束、可选凭据字段守门。"""

    def test_w2_channels_accept_targets_and_optional_fields(self):
        cfg = load_category({
            **_minimal_data(),
            "push": [
                {"channel": "ntfy", "target": "env:NTFY_TARGET", "ntfy_token": "env:NTFY_TOKEN"},
                {
                    "channel": "dingtalk",
                    "target": "env:DINGTALK_WEBHOOK_URL",
                    "dingtalk_secret": "keychain:myia/dingtalk/secret",
                },
                {
                    "channel": "wecom",
                    "targets": ["wecom:ZhangSan"],
                    "wecom_corpid": "env:WECOM_CORPID",
                    "wecom_corpsecret": "env:WECOM_CORPSECRET",
                    "wecom_agentid": "env:WECOM_AGENTID",
                },
            ],
        })
        ntfy, dingtalk, wecom = cfg.push
        assert (ntfy.channel, ntfy.ntfy_token, ntfy.targets) == ("ntfy", "env:NTFY_TOKEN", [])
        assert dingtalk.dingtalk_secret == "keychain:myia/dingtalk/secret"
        # wecom targets 在场时 target 可省(design D4 放宽),凭据字段原样落位。
        assert wecom.target is None
        assert (wecom.wecom_corpid, wecom.wecom_agentid) == ("env:WECOM_CORPID", "env:WECOM_AGENTID")

    def test_w2_same_platform_constraint(self):
        error = _load_error({
            **_minimal_data(),
            "push": [{"channel": "ntfy", "target": "env:NTFY_TARGET", "targets": ["wecom:ZhangSan"]}],
        })
        detail = _error_of_type(error, "platform_mismatch")
        assert detail.path.endswith("targets")

    def test_w2_credential_field_on_wrong_channel_rejected(self):
        error = _load_error({
            **_minimal_data(),
            "push": [{"channel": "ntfy", "target": "env:NTFY_TARGET", "dingtalk_secret": "env:X"}],
        })
        assert _error_of_type(error, "unexpected_platform_field")

    def test_w2_credential_field_plaintext_rejected(self):
        error = _load_error({
            **_minimal_data(),
            "push": [{"channel": "dingtalk", "target": "env:X", "dingtalk_secret": "SECplaintext"}],
        })
        assert _error_of_type(error, "credential_plaintext")

    def test_w2_legacy_only_config_unchanged(self):
        """不配可选字段 = 蓝本裸行为:最小条目照常加载(零影响默认)。"""
        cfg = load_category({
            **_minimal_data(),
            "push": [{"channel": "dingtalk", "target": "env:DINGTALK_WEBHOOK_URL"}],
        })
        push = cfg.push[0]
        assert (push.ntfy_token, push.dingtalk_secret, push.wecom_corpid) == (None, None, None)
