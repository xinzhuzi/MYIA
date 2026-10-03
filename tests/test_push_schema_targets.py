"""Tests for schema ``targets`` fields — 10-03-messaging-core 步骤 3.

覆盖:格式校验(空串拒/去重/元素形态)、同平台约束(通道级与规则级)、
不支持寻址通道拒配、targets 在场时 target 可省、旧 YAML 黄金回归
(黄金集夹具的冻结副本加载结果与黄金 JSON 的 push 子树等价)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myia.schema import CHANNEL_PLATFORMS, LoadError, load_category

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "push_targets_golden_before.json"
# 冻结副本根(10-03-golden-frozen-snapshots):黄金回归只读这里,不读活库
# plugins/*.yaml。副本按黄金 JSON 的键(仓库根相对路径)镜像落位,故
# plugins/stocks.yaml 与 tests/fixtures/stocks.yaml 的重名天然消解。
GOLDEN_DIR = FIXTURES / "push_targets_golden"


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
        # weixin(10-03-messaging-weixin-bridge,可选 Hermes 桥接)再增一行;
        # W3 长尾 22 家(10-03-messaging-w3-longtail)平台名 = 通道名;
        # webhook/stdout 不在表内(不支持目录寻址)。
        assert CHANNEL_PLATFORMS == {
            "feishu_card": "feishu",
            "telegram": "telegram",
            "ntfy": "ntfy",
            "dingtalk": "dingtalk",
            "wecom": "wecom",
            "weixin": "weixin",
            **{name: name for name in W3_LONGTAIL_NAMES},
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
        """黄金回归:黄金集夹具的**冻结副本**,push 子树逐字段等价。

        冻结基线(10-03-golden-frozen-snapshots 治本):比对对象从活体
        ``plugins/*.yaml`` 换成 ``tests/fixtures/push_targets_golden/`` 下
        的只读副本——多会话仓库里,任何会话给自己品类 yaml 加 push
        条目/字段都是合法演进,但活体参与比对就会把本测试打红(2026-10-03
        一天两次:games 的 url_template、vision 的 image: img@src/games
        push 扩列)。冻结后活库演进与本测试彻底解耦,别的任务也不再需要
        顺手"golden 同步"。

        承诺边界(同日收窄,commit 2e7e252):只对 ``push`` 子树做逐字段
        断言(targets 剥离后零漂移);其余节是各任务的合法演化面,不拦。
        additive 空键(None/[]/{})容忍保持——那是 schema 层新增字段的
        合法形态,取值漂移、字段丢失、非空新增键仍然失败。

        升级路径(显式,绝不静默跟随活库)::冻结版与活版漂移过大、经
        评审决定重立基线时::

            uv run --no-sync python tests/regen_push_targets_golden.py --refreeze

        同源自检(不动冻结副本,输出须与仓库内 JSON 逐字节一致)::

            uv run --no-sync python tests/regen_push_targets_golden.py
        """
        from myia.schema import load_category_file

        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert golden, "黄金文件为空:基线生成失败"
        for name, expected in golden.items():
            cfg = load_category_file(GOLDEN_DIR / name)
            actual = cfg.model_dump(mode="json")
            _strip_additive_targets(actual)
            _assert_additive_equivalent(
                actual.get("push", []), expected.get("push", []), f"{name}.push"
            )

    def test_frozen_copies_match_golden_manifest(self):
        """冻结完整性:黄金 JSON 的键与冻结目录内 *.yaml 一一对应。

        黄金集的进出只允许经 ``tests/regen_push_targets_golden.py`` 的
        MANIFEST 显式声明——副本缺失会让黄金回归直接红(load_category_file
        抛错),多余的孤儿副本在这里拦住,防止清单与目录悄悄失配。
        """
        frozen = sorted(
            p.relative_to(GOLDEN_DIR).as_posix() for p in GOLDEN_DIR.rglob("*.yaml")
        )
        golden_keys = sorted(json.loads(GOLDEN.read_text(encoding="utf-8")))
        assert frozen == golden_keys, (
            "冻结副本与黄金清单失配:"
            f" 仅在目录={set(frozen) - set(golden_keys)},"
            f" 仅在清单={set(golden_keys) - set(frozen)}"
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


# ---------------------------------------------------------------------------
# 微信桥接(10-03-messaging-weixin-bridge):入表 + 同平台约束 + bin 路径守门
# ---------------------------------------------------------------------------


class TestWeixinBridgeChannels:
    """weixin:Literal 收录、weixin: 前缀 targets 合法、hermes_bin 宿主守门。"""

    def test_push_channel_vocabulary_accepts_weixin(self):
        from myia.schema import PUSH_CHANNELS

        assert "weixin" in PUSH_CHANNELS
        assert "weixin" in CHANNEL_PLATFORMS

    def test_weixin_targets_and_hermes_bin_load(self):
        """合法形态:targets 直达 peer + 本地 bin 路径(非凭据,原样落位)。"""
        cfg = load_category({
            **_minimal_data(),
            "push": [
                {
                    "channel": "weixin",
                    "targets": ["weixin:peer123@im.wechat", "weixin:家人群"],
                    "weixin_hermes_bin": "/opt/hermes/bin/hermes",
                }
            ],
        })
        push = cfg.push[0]
        assert push.target is None  # targets 在场时 target 可省
        assert push.targets == ["weixin:peer123@im.wechat", "weixin:家人群"]
        # 本地路径不走 env:/keychain: 引用校验(design D1:非凭据)
        assert push.weixin_hermes_bin == "/opt/hermes/bin/hermes"

    def test_weixin_same_platform_constraint(self):
        """weixin 条目混他平台前缀 = platform_mismatch(同表约束自动生效)。"""
        error = _load_error({
            **_minimal_data(),
            "push": [{"channel": "weixin", "targets": ["feishu:某群"]}],
        })
        detail = _error_of_type(error, "platform_mismatch")
        assert detail.path == "$.push[0].targets"
        assert "feishu:某群" in detail.message

    def test_hermes_bin_on_wrong_channel_rejected(self):
        """``weixin_hermes_bin`` 仅 weixin 通道可配(别处即 SchemaValueError)。"""
        error = _load_error({
            **_minimal_data(),
            "push": [
                {"channel": "ntfy", "target": "env:NTFY_TARGET", "weixin_hermes_bin": "/x/hermes"}
            ],
        })
        detail = _error_of_type(error, "unexpected_platform_field")
        assert detail.path == "$.push[0].weixin_hermes_bin"

    def test_weixin_legacy_target_ref_still_valid(self):
        """legacy 单 target 引用路径不因桥接改动变化(引用形态照旧校验)。"""
        cfg = load_category({
            **_minimal_data(),
            "push": [{"channel": "weixin", "target": "env:WEIXIN_PEER_ID"}],
        })
        assert cfg.push[0].target == "env:WEIXIN_PEER_ID"
        assert cfg.push[0].targets == []
        assert cfg.push[0].weixin_hermes_bin is None  # 不配 = 缺省路径


# ---------------------------------------------------------------------------
# W3 长尾 22 家(10-03-messaging-w3-longtail):词表 + targets 校验 + 同平台约束
# ---------------------------------------------------------------------------

#: W3 长尾 22 家通道名;与 myia.push._W3_LONGTAIL_CHANNELS、schema._W3_LONGTAIL
#: 一一对应(集成面注册表专测在 tests/test_push_channels.py)。
W3_LONGTAIL_NAMES = (
    "slack",
    "discord",
    "whatsapp_cloud",
    "line",
    "qqbot",
    "google_chat",
    "teams",
    "msgraph_webhook",
    "matrix",
    "mattermost",
    "irc",
    "simplex",
    "signal",
    "bluebubbles",
    "email",
    "sms",
    "homeassistant",
    "a2a",
    "yuanbao",
    "buzz",
    "photon",
    "raft",
)

#: 每家一条直达形态 targets 样例(形态取自各适配器 parse_direct_ref 的成文
#: 约束;schema 层只校验 ``platform:ref`` 格式,直达命中与否是解析期的事)。
W3_LONGTAIL_TARGET_SAMPLES = {
    "slack": "slack:C0123ABCDEF",
    "discord": "discord:1234567890123456789",
    "whatsapp_cloud": "whatsapp_cloud:8613800138000",
    "line": "line:U1234567890abcdef1234567890abcdef",
    "qqbot": "qqbot:123456789",
    "google_chat": "google_chat:spaces/AAAA1234",
    "teams": "teams:19:meeting_ZGVmYXVsdA==",
    "msgraph_webhook": "msgraph_webhook:19:chats/11111111-2222-3333-4444-555555555555",
    "matrix": "matrix:!roomid:example.com",
    "mattermost": "mattermost:abcdefghijklmnopqrstuvwxyz",
    "irc": "irc:#myia-channel",
    "simplex": "simplex:#+/abc123def456",
    "signal": "signal:+8613800138000",
    "bluebubbles": "bluebubbles:+8613800138000",
    "email": "email:user@example.com",
    "sms": "sms:+8613800138000",
    "homeassistant": "homeassistant:notify.mobile_app_pixel",
    "a2a": "a2a:https://agent.example.com/a2a/v1",
    "yuanbao": "yuanbao:direct:123456",
    "buzz": "buzz:00000000-1111-2222-3333-444444444444",
    "photon": "photon:+8613800138000",
    "raft": "raft:主工作区别名",  # 无直达形态约束(蓝本 chat_id 形态未成文),别名登记
}


class TestW3LongtailSchema:
    """22 家:入 PUSH_CHANNELS 词表、CHANNEL_PLATFORMS 全行、targets 全量加载。"""

    def test_push_channel_vocabulary_contains_all_w3(self):
        from myia.schema import PUSH_CHANNELS

        assert set(W3_LONGTAIL_NAMES) <= set(PUSH_CHANNELS)
        assert len(PUSH_CHANNELS) == 30  # 8 既有 + 22 长尾,计数如实

    def test_channel_platforms_rows_are_identity_for_w3(self):
        for name in W3_LONGTAIL_NAMES:
            assert CHANNEL_PLATFORMS.get(name) == name

    def test_all_w3_targets_specs_load(self):
        """22 家各一条直达形态 targets:全部加载成功、target 可省、spec 原样落位。"""
        data = _minimal_data()
        data["push"] = [
            {"channel": name, "targets": [spec]}
            for name, spec in W3_LONGTAIL_TARGET_SAMPLES.items()
        ]

        cfg = load_category(data)

        assert len(cfg.push) == 22
        for push in cfg.push:
            assert push.target is None  # targets 在场时 target 可省
            assert push.targets == [W3_LONGTAIL_TARGET_SAMPLES[push.channel]]

    def test_w3_cross_platform_targets_rejected(self):
        """同平台约束对 W3 生效:slack 条目混 telegram 前缀 = platform_mismatch。"""
        error = _load_error({
            **_minimal_data(),
            "push": [
                {"channel": "slack", "targets": ["slack:C0123ABCDEF", "telegram:12345"]}
            ],
        })
        detail = _error_of_type(error, "platform_mismatch")
        assert detail.path == "$.push[0].targets"
        assert "telegram:12345" in detail.message

    def test_w3_rule_level_targets_load_and_constrain(self):
        """规则级 targets 同样放行 W3 平台前缀;跨平台同样拒。"""
        cfg = load_category({
            **_minimal_data(),
            "push": [
                {
                    "channel": "matrix",
                    "target": "env:MATRIX_ROOM",
                    "route": [
                        {"when": "score >= 8", "mode": "immediate", "targets": ["matrix:!room:example.com"]}
                    ],
                }
            ],
        })
        assert cfg.push[0].route[0].targets == ["matrix:!room:example.com"]

        error = _load_error({
            **_minimal_data(),
            "push": [
                {
                    "channel": "email",
                    "target": "env:EMAIL_TO",
                    "route": [
                        {"when": "score >= 8", "mode": "immediate", "targets": ["sms:+8613800138000"]}
                    ],
                }
            ],
        })
        _error_of_type(error, "platform_mismatch")
