"""Tests for shishi.push.targets — 对象解析(蓝本移植自 Hermes send_message_targets).

覆盖任务 10-03-messaging-core 步骤 2:四路径(直达 id/@username 钩子 →
目录精确 id → 精确名 → 唯一前缀)+ 多义/未命中结构化错误(内嵌候选,
MYIA 偏离注记)+ 安全(无 eval)。
"""

from __future__ import annotations

import re

import pytest

from shishi.push.directory import ChannelDirectory, ChannelEntry
from shishi.push.targets import (
    RESOLVED_DIRECT,
    RESOLVED_DIRECTORY_ID,
    RESOLVED_DIRECTORY_NAME,
    RESOLVED_DIRECTORY_PREFIX,
    ChannelTarget,
    TargetResolveError,
    parse_spec,
    resolve_all,
    resolve_target,
)


class FakeFeishuPlatform:
    """直达解析钩子测试替身(feishu 子任务将提供真实实现)。"""

    @staticmethod
    def parse_direct_ref(ref: str) -> ChannelTarget | None:
        if re.fullmatch(r"(?:oc|ou|on|chat|open)_[-A-Za-z0-9]+", ref):
            return ChannelTarget(platform="feishu", chat_id=ref)
        return None


PLATFORMS = {"feishu": FakeFeishuPlatform}


def _directory(tmp_path, entries: list[ChannelEntry]) -> ChannelDirectory:
    directory = ChannelDirectory(tmp_path)
    directory.replace_platform("feishu", entries, now=1.0)
    return directory


@pytest.fixture()
def populated(tmp_path):
    # chat_id 不用 oc_ 前缀形态,让「目录精确 id」路径不被直达钩子抢先。
    return _directory(
        tmp_path,
        [
            ChannelEntry(platform="feishu", chat_id="g1", name="AI中转站合伙人群"),
            ChannelEntry(platform="feishu", chat_id="g2", name="服务器折扣群"),
            ChannelEntry(platform="feishu", chat_id="g3", name="Server-English"),
            ChannelEntry(platform="feishu", chat_id="dm1", name="张三", type="dm"),
            ChannelEntry(platform="feishu", chat_id="g4", name="AI资讯群"),
        ],
    )


class TestParseSpec:
    def test_valid_spec(self):
        assert parse_spec("feishu:群名") == ("feishu", "群名")
        assert parse_spec(" telegram:12345 ") == ("telegram", "12345")
        assert parse_spec("telegram:@user_name") == ("telegram", "@user_name")

    @pytest.mark.parametrize("bad", ["", "feishu", "feishu:", "群名", "Feishu:x", "fei-shu:x", ":x"])
    def test_bad_format_rejected(self, bad):
        with pytest.raises(TargetResolveError) as excinfo:
            parse_spec(bad)
        assert excinfo.value.reason == "bad_format"


class TestDirectRefHook:
    def test_explicit_feishu_id_bypasses_directory(self, populated):
        target = resolve_target("feishu:oc_zzznotindir", populated, platforms=PLATFORMS)

        assert target.chat_id == "oc_zzznotindir"
        assert target.resolved_from == RESOLVED_DIRECT

    def test_hook_miss_falls_through_to_directory(self, populated):
        # 钩子只认 oc_/ou_/… 前缀;群名走目录。
        target = resolve_target("feishu:AI中转站合伙人群", populated, platforms=PLATFORMS)

        assert target.chat_id == "g1"
        assert target.resolved_from == RESOLVED_DIRECTORY_NAME

    def test_no_registry_still_resolves_directory(self, populated):
        target = resolve_target("feishu:g1", populated, platforms={})

        assert target.resolved_from == RESOLVED_DIRECTORY_ID

    def test_hook_returning_bare_tuple_is_upgraded(self, tmp_path):
        class TupleHookPlatform:
            @staticmethod
            def parse_direct_ref(ref: str):
                return (f"oc_{ref}", "th1") if ref.isdigit() else None

        directory = _directory(tmp_path, [])
        target = resolve_target("feishu:42", directory, platforms={"feishu": TupleHookPlatform})

        assert (target.chat_id, target.thread_id) == ("oc_42", "th1")


class TestDirectoryResolution:
    def test_exact_chat_id_case_sensitive(self, populated):
        target = resolve_target("feishu:g2", populated, platforms=PLATFORMS)

        assert (target.chat_id, target.name, target.resolved_from) == (
            "g2",
            "服务器折扣群",
            RESOLVED_DIRECTORY_ID,
        )

    def test_exact_name_case_insensitive(self, populated):
        target = resolve_target("feishu:ai中转站合伙人群", populated, platforms=PLATFORMS)

        assert target.chat_id == "g1"
        assert target.resolved_from == RESOLVED_DIRECTORY_NAME

    def test_exact_name_with_hash_prefix_normalized(self, populated):
        target = resolve_target("feishu:#AI中转站合伙人群", populated, platforms=PLATFORMS)

        assert target.chat_id == "g1"  # Hermes # 前缀规范化的等价物

    def test_unique_prefix(self, populated):
        target = resolve_target("feishu:服务器", populated, platforms=PLATFORMS)

        assert target.chat_id == "g2"
        assert target.resolved_from == RESOLVED_DIRECTORY_PREFIX

    def test_prefix_across_case_and_ascii(self, populated):
        target = resolve_target("feishu:server", populated, platforms=PLATFORMS)

        assert target.chat_id == "g3"


class TestResolveErrors:
    def test_ambiguous_prefix_lists_candidates(self, populated):
        # "AI" 前缀同时命中 AI中转站合伙人群 / AI资讯群 → 多义即未命中。
        with pytest.raises(TargetResolveError) as excinfo:
            resolve_target("feishu:AI", populated, platforms=PLATFORMS)

        error = excinfo.value
        assert error.reason == "ambiguous_prefix"
        assert error.platform == "feishu"
        labels = " ".join(error.candidates)
        assert "AI中转站合伙人群 (g1)" in labels and "AI资讯群 (g4)" in labels

    def test_miss_embeds_candidates(self, populated):
        with pytest.raises(TargetResolveError) as excinfo:
            resolve_target("feishu:不存在的群", populated, platforms=PLATFORMS)

        error = excinfo.value
        assert error.reason == "unresolvable"
        assert len(error.candidates) == 5  # 全量候选内嵌(MYIA 偏离注记)
        assert "AI中转站合伙人群 (g1)" in error.candidates
        assert "不存在的群" in str(error)

    def test_empty_platform_bucket_reports_unknown_platform(self, tmp_path):
        directory = _directory(tmp_path, [])

        with pytest.raises(TargetResolveError) as excinfo:
            resolve_target("feishu:任意名", directory, platforms={})

        assert excinfo.value.reason == "unknown_platform"

    def test_target_key_shape(self):
        target = ChannelTarget(platform="Feishu", chat_id=" oc_1 ", name="群")

        assert target.key == "feishu:oc_1"


class TestResolveAll:
    def test_partial_failure_returns_targets_and_errors(self, populated):
        targets, errors = resolve_all(
            ["feishu:AI中转站合伙人群", "feishu:没有这个群", "feishu:oc_directid"],
            populated,
            platforms=PLATFORMS,
        )

        assert [t.chat_id for t in targets] == ["g1", "oc_directid"]
        assert len(errors) == 1
        assert errors[0].reason == "unresolvable"

    def test_empty_specs(self, populated):
        assert resolve_all([], populated, platforms=PLATFORMS) == ([], [])


class TestSafety:
    def test_no_eval_dangerous_looking_names_just_fail(self, populated):
        """安全验收:spec 只做字符串匹配,任何「代码样」名称都不执行、仅未命中。"""
        with pytest.raises(TargetResolveError):
            resolve_target(
                "feishu:__import__('os').system('echo pwned')", populated, platforms=PLATFORMS
            )

        assert populated.find("feishu", "g1").name == "AI中转站合伙人群"  # 目录未被触碰
