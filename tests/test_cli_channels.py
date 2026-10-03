"""Tests for ``myia channels`` CLI — 10-03-messaging-feishu 步骤 3。

覆盖:refresh(发现 → 目录合并 → 输出目录表;凭据缺失结构化报错且
保留旧目录;未知平台拒绝)、list(纯读,空目录合法态)、--json 单份
输出契约、与 secret/plugin/feedback 同构的「名词+子动词」家族形态。
发现走真 FeishuCardChannel + httpx.MockTransport(经 PLATFORMS 注册表);
零真实网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from shishi.cli import main
from shishi.push import FeishuCardChannel, PLATFORMS


def _chats_body(items: list[dict], *, has_more: bool = False) -> dict:
    return {"code": 0, "msg": "success", "data": {"items": items, "has_more": has_more}}


@pytest.fixture()
def mock_feishu(monkeypatch, tmp_path):
    """把 FeishuCardChannel 的自管 client 换成 MockTransport(经注册表真身)。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_chats_body([
                {"chat_id": "oc_1", "name": "AI中转站合伙人群"},
                {"chat_id": "oc_dm_9a79", "name": "老板私聊"},
            ]),
        )

    holder = {"handler": handler}

    class _MockedFeishu(FeishuCardChannel):
        """真 FeishuCardChannel,仅自管 HTTP 换 mock;凭据路径保持真实。"""

        def __init__(self, **kwargs) -> None:
            kwargs.setdefault("token", "test-token")
            kwargs["client"] = httpx.AsyncClient(transport=httpx.MockTransport(holder["handler"]))
            super().__init__(**kwargs)

    import shishi.cli as cli_module

    monkeypatch.setattr(cli_module, "PLATFORMS", {"feishu": _MockedFeishu})
    return holder


def _run_cli(argv: list[str], capsys, monkeypatch, tmp_path: Path) -> tuple[int, str, str]:
    monkeypatch.chdir(tmp_path)
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


class TestChannelsRefresh:
    def test_refresh_discovers_and_merges_into_directory(self, tmp_path, monkeypatch, capsys, mock_feishu):
        code, out, err = _run_cli(["channels", "refresh", "feishu", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 0, err

        payload = json.loads(out)
        assert payload["command"] == "channels" and payload["action"] == "refresh"
        assert payload["refreshed"] == {"feishu": 2}
        assert payload["failed"] == []
        assert [(e["chat_id"], e["name"]) for e in payload["platforms"]["feishu"]] == [
            ("oc_1", "AI中转站合伙人群"),
            ("oc_dm_9a79", "老板私聊"),
        ]
        # 目录 JSON 持久化在数据根(--db 父目录)
        saved = json.loads((tmp_path / "channel_directory.json").read_text(encoding="utf-8"))
        assert [e["chat_id"] for e in saved["platforms"]["feishu"]] == ["oc_1", "oc_dm_9a79"]
        assert saved["updated_at"]

    def test_refresh_default_selects_all_registered(self, tmp_path, monkeypatch, capsys, mock_feishu):
        code, out, _ = _run_cli(["channels", "refresh", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 0
        assert json.loads(out)["refreshed"] == {"feishu": 2}

    def test_refresh_human_table_lists_names_and_ids(self, tmp_path, monkeypatch, capsys, mock_feishu):
        code, out, err = _run_cli(["channels", "refresh", "feishu"], capsys, monkeypatch, tmp_path)
        assert code == 0, err
        assert "已刷新 feishu 目录:发现 2 个可达对象" in out
        assert "AI中转站合伙人群 (oc_1)" in out

    def test_refresh_missing_credential_reports_structured_error_keeps_old_bucket(
        self, tmp_path, monkeypatch, capsys
    ):
        """凭据缺失:结构化报错(退出码 1),旧目录保留(prd 需求 2)。"""
        monkeypatch.delenv("FEISHU_BOT_TOKEN", raising=False)
        # 旧目录预置:失败后必须原样保留
        old = json.dumps({
            "updated_at": "2026-10-01T00:00:00",
            "platforms": {"feishu": [
                {"platform": "feishu", "chat_id": "oc_old", "name": "旧群", "type": "group"}
            ]},
        }, ensure_ascii=False)
        (tmp_path / "channel_directory.json").write_text(old, encoding="utf-8")

        code, out, err = _run_cli(["channels", "refresh", "feishu", "--json"], capsys, monkeypatch, tmp_path)

        assert code == 1
        payload = json.loads(out)
        assert payload["error"] == "channels_refresh_failed"
        assert "env_var_missing" in payload["message"] or "FEISHU_BOT_TOKEN" in payload["message"]
        assert payload["failed"][0]["platform"] == "feishu"
        # 旧目录原样保留(发现失败不覆盖)
        saved = json.loads((tmp_path / "channel_directory.json").read_text(encoding="utf-8"))
        assert [e["chat_id"] for e in saved["platforms"]["feishu"]] == ["oc_old"]

    def test_refresh_unknown_platform_rejected(self, tmp_path, monkeypatch, capsys, mock_feishu):
        code, out, _ = _run_cli(["channels", "refresh", "slack", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 1
        assert json.loads(out)["error"] == "channels"
        assert "未知平台" in json.loads(out)["message"]


class TestChannelsList:
    def test_list_reads_persisted_directory(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "channel_directory.json").write_text(
            json.dumps({
                "updated_at": "2026-10-03T08:00:00",
                "platforms": {"feishu": [
                    {"platform": "feishu", "chat_id": "oc_1", "name": "AI中转站合伙人群", "type": "group"}
                ]},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        code, out, _ = _run_cli(["channels", "list"], capsys, monkeypatch, tmp_path)
        assert code == 0
        assert "AI中转站合伙人群 (oc_1)" in out

    def test_list_empty_directory_is_legal_state(self, tmp_path, monkeypatch, capsys):
        code, out, _ = _run_cli(["channels", "list", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 0
        payload = json.loads(out)
        assert payload["platforms"] == {}
        assert payload["updated_at"] is None

    def test_list_json_single_document(self, tmp_path, monkeypatch, capsys, mock_feishu):
        _run_cli(["channels", "refresh", "feishu", "--json"], capsys, monkeypatch, tmp_path)
        capsys.readouterr()
        code, out, err = _run_cli(["channels", "list", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 0
        payload = json.loads(out)  # stdout 单份纯 JSON(AI 消费路径)
        assert payload["action"] == "list"
        assert len(payload["platforms"]["feishu"]) == 2


class TestFamilyShape:
    def test_channels_registered_in_platforms_registry(self):
        assert PLATFORMS.get("feishu") is FeishuCardChannel

    def test_no_subcommand_is_usage_error(self, tmp_path, monkeypatch, capsys):
        code, out, err = _run_cli(["channels"], capsys, monkeypatch, tmp_path)
        assert code == 1


# ---------------------------------------------------------------------------
# W2 平台(10-03-messaging-w2-platforms):无自动发现 = 说明,不是失败
# ---------------------------------------------------------------------------


class TestChannelsRefreshNoDiscovery:
    """ntfy/dingtalk/wecom 的 refresh:报「无自动发现(蓝本事实)」、目录桶
    不动、退出码 0(prd R4:不假装刷新)。"""

    @pytest.mark.parametrize("platform", ["ntfy", "dingtalk", "wecom"])
    def test_no_discovery_platform_reports_and_exits_zero(self, tmp_path, monkeypatch, capsys, platform):
        from shishi.push import DingTalkChannel, NtfyChannel, WecomChannel

        adapters = {
            "ntfy": NtfyChannel,
            "dingtalk": DingTalkChannel,
            "wecom": WecomChannel,
        }
        monkeypatch.setattr(
            "shishi.cli.PLATFORMS", {"feishu": FeishuCardChannel, platform: adapters[platform]}
        )
        # 预置旧桶(别名占位):refresh 不得清掉它。
        (tmp_path / "channel_directory.json").write_text(
            json.dumps(
                {
                    "updated_at": "2026-10-03T00:00:00",
                    "platforms": {platform: [{"platform": platform, "chat_id": "legacy-1", "name": "旧别名"}]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        code, out, err = _run_cli(["channels", "refresh", platform, "--json"], capsys, monkeypatch, tmp_path)

        assert code == 0, err
        payload = json.loads(out)
        assert payload["refreshed"] == {}
        assert payload["failed"] == []
        assert [e["platform"] for e in payload["no_discovery"]] == [platform]
        assert "无自动发现" in payload["no_discovery"][0]["message"]
        # 旧桶原样保留(不假装刷新成空目录)
        assert [e["chat_id"] for e in payload["platforms"][platform]] == ["legacy-1"]

    def test_no_discovery_human_output_prints_explanation(self, tmp_path, monkeypatch, capsys):
        from shishi.push import NtfyChannel

        monkeypatch.setattr("shishi.cli.PLATFORMS", {"ntfy": NtfyChannel})

        code, out, err = _run_cli(["channels", "refresh", "ntfy"], capsys, monkeypatch, tmp_path)

        assert code == 0, err
        assert "ntfy" in out and "无自动发现" in out
        assert "刷新失败" not in err and "被动目录平台" not in out

    def test_mixed_refresh_separates_passive_and_no_discovery(self, tmp_path, monkeypatch, capsys):
        """被动积累(telegram)与无自动发现(ntfy)分桶上报,互不混淆。"""
        from shishi.push import NtfyChannel, TelegramChannel

        monkeypatch.setattr(
            "shishi.cli.PLATFORMS", {"telegram": TelegramChannel, "ntfy": NtfyChannel}
        )
        code, out, err = _run_cli(["channels", "refresh", "--json"], capsys, monkeypatch, tmp_path)
        assert code == 0, err

        payload = json.loads(out)
        assert payload["refreshed"] == {}
        assert payload["passive"] == ["telegram"]
        assert [e["platform"] for e in payload["no_discovery"]] == ["ntfy"]
        assert payload["failed"] == []
