"""Tests for myia.cli — exit-code contract, --json/--dry-run/--loop, stubs.

Covers PRD 10-01-v01-cli-basic acceptance criteria:

- ``myia run <yaml> --dry-run --json`` 输出单份可被 json/jq 解析的结果;
- 配置错误退出码 1,错误信息含字段路径(与 schema 层打通);
- 退出码语义 0/1/2/3 全覆盖(供 agent 与 CI 判断);
- ``--help`` 输出清晰;``list``/``test``/``init`` 等留位子命令结构化提示。

Pipeline is replaced by a fake (constructor + run/run_forever recorded) —
the CLI layer is tested in isolation; the real pipeline contract is covered
by tests/test_pipeline.py and the real-process e2e in the task log.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

import myia.cli as cli_module
from myia.cli import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_PARTIAL, build_parser, main
from myia.pipeline import ChannelPushReport, RunResult, StageReport
from myia.schema import load_category

VALID_YAML = """
id: demo
name: 演示品类
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    extract:
      type: json_path
      fields:
        title: "$[*].title"
        url: "$[*].url"
push:
  - channel: stdout
"""

PLAINTEXT_YAML = """
id: demo
name: 演示品类
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    headers:
      Cookie: "sid=123456"
    extract:
      type: json_path
      fields:
        title: "$[*].title"
        url: "$[*].url"
push:
  - channel: stdout
"""


# ---------------------------------------------------------------------------
# Fakes: the CLI layer sees a canned RunResult, no I/O
# ---------------------------------------------------------------------------


def make_result(status: str = "success", **overrides: Any) -> RunResult:
    """A real RunResult dataclass with minimal canned content."""
    fields: dict[str, Any] = {
        "category": "demo",
        "category_name": "演示品类",
        "run_id": 1,
        "started_at": datetime.now(timezone.utc),
        "finished_at": datetime.now(timezone.utc),
        "status": status,
        "stages": [StageReport(name=name, status="ok") for name in
                   ("fetch", "classify", "dedup", "analyze", "push")],
        "sources": [],
        "items": [],
        "pushes": [ChannelPushReport(channel="stdout")],
    }
    fields.update(overrides)
    return RunResult(**fields)


class FakePipeline:
    """Records constructor/run/run_forever calls; returns a canned result."""

    last_instance: "FakePipeline | None" = None

    def __init__(self, config: Any, **kwargs: Any) -> None:
        self.config = config
        self.kwargs = kwargs
        self.run_calls: list[dict[str, Any]] = []
        self.forever_calls: list[dict[str, Any]] = []
        self.closed = False
        self.result: RunResult = make_result()
        FakePipeline.last_instance = self

    async def run(self, *, dry_run: bool = False) -> RunResult:
        self.run_calls.append({"dry_run": dry_run})
        return replace(self.result, dry_run=dry_run)  # CLI 的开关如实反映到输出

    async def run_forever(self, *, dry_run: bool = False) -> int:
        self.forever_calls.append({"dry_run": dry_run})
        return 3

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> type[FakePipeline]:
    """Swap the CLI's Pipeline for the fake and hand the config loader a win."""
    config = load_category(
        {
            "id": "demo",
            "name": "演示品类",
            "schedule": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "sources": [
                {
                    "name": "api",
                    "engine": "direct_api",
                    "url": "https://api.demo.local/list",
                    "extract": {"type": "json_path", "fields": {"title": "$[*]", "url": "$[*]"}},
                }
            ],
            "push": [{"channel": "stdout"}],
        }
    )
    monkeypatch.setattr(cli_module, "load_category_file", lambda path: config)
    monkeypatch.setattr(cli_module, "Pipeline", FakePipeline)
    return FakePipeline


# ---------------------------------------------------------------------------
# Exit code 0: success / dry-run / loop
# ---------------------------------------------------------------------------


def test_run_success_exit_zero_and_json_parseable(fake_pipeline, capsys):
    """--json:单份 JSON,可被 json/jq 解析;成功退出码 0。"""
    code = main(["run", "demo.yaml", "--json"])

    assert code == EXIT_OK
    out = capsys.readouterr().out
    payload = json.loads(out)  # jq 等价:单份 JSON 文档
    assert payload["status"] == "success"
    assert payload["category"] == "demo"
    assert [stage["name"] for stage in payload["stages"]] == [
        "fetch", "classify", "dedup", "analyze", "push",
    ]
    instance = fake_pipeline.last_instance
    assert instance is not None and instance.run_calls == [{"dry_run": False}]
    assert instance.closed is True  # 资源已释放


def test_run_human_output_exit_zero(fake_pipeline, capsys):
    """默认人类可读输出;与 --json 同一信息另一种皮。"""
    code = main(["run", "demo.yaml"])

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "MYIA run:" in out
    assert "状态:success" in out
    assert "fetch:ok" in out


def test_run_dry_run_flag_forwarded(fake_pipeline, capsys):
    """--dry-run 透传给 pipeline(全链执行但不推送)。"""
    code = main(["run", "demo.yaml", "--dry-run", "--json"])

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    instance = fake_pipeline.last_instance
    assert instance is not None and instance.run_calls == [{"dry_run": True}]


def test_run_loop_calls_run_forever(fake_pipeline, capsys):
    """--loop:常驻调度(run_forever),干净退出码 0。"""
    code = main(["run", "demo.yaml", "--loop"])

    assert code == EXIT_OK
    instance = fake_pipeline.last_instance
    assert instance is not None
    assert instance.forever_calls == [{"dry_run": False}]
    assert instance.run_calls == []


def test_run_keyboard_interrupt_exits_zero(fake_pipeline, capsys):
    """常驻模式 Ctrl-C:干净退出,退出码 0。"""
    instance = fake_pipeline.last_instance

    async def interrupted(*, dry_run: bool = False):
        raise KeyboardInterrupt

    assert instance is not None
    instance.run_forever = interrupted  # type: ignore[method-assign]
    assert main(["run", "demo.yaml", "--loop"]) == EXIT_OK


# ---------------------------------------------------------------------------
# Exit codes 2 / 3: fetch-all-failed / partial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [("failed", 2), ("partial", 3)],
)
def test_run_maps_status_to_exit_code(fake_pipeline, capsys, status, expected):
    """failed→2(采集全部失败)、partial→3(部分失败)。"""
    fake_pipeline.last_instance = None
    original = FakePipeline.__init__

    def init_with_status(self, config, **kwargs):
        original(self, config, **kwargs)
        self.result = make_result(status=status)

    fake_pipeline.__init__ = init_with_status  # type: ignore[method-assign]
    try:
        code = main(["run", "demo.yaml", "--json"])
    finally:
        fake_pipeline.__init__ = original  # type: ignore[method-assign]
    assert code == expected
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_exit_code_constants_are_pinned():
    """退出码常量即 CLI 契约,不可漂移。"""
    from myia.cli import EXIT_FETCH_ALL_FAILED

    assert (EXIT_OK, EXIT_CONFIG_ERROR, EXIT_FETCH_ALL_FAILED, EXIT_PARTIAL) == (0, 1, 2, 3)


# ---------------------------------------------------------------------------
# Exit code 1: config errors carry field paths
# ---------------------------------------------------------------------------


def test_config_error_missing_file_exit_one(capsys):
    """文件不存在:退出码 1,结构化 file_not_found。"""
    code = main(["run", "no-such-plugin.yaml", "--json"])

    assert code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "config"
    assert payload["errors"][0]["error_type"] == "file_not_found"


def test_config_error_unknown_field_carries_field_path(tmp_path, capsys):
    """未知字段 fail-fast:错误含字段路径(与 schema 层打通)。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(VALID_YAML + "\nnope: 未知字段\n", encoding="utf-8")
    code = main(["run", str(bad), "--json"])

    assert code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["path"] == "$.nope"
    assert payload["errors"][0]["error_type"] == "unknown_field"


def test_config_error_plaintext_credential_exit_one(tmp_path, capsys):
    """明文凭据:退出码 1,字段路径指向具体 header(schema 拒载,零网络)。"""
    bad = tmp_path / "plaintext.yaml"
    bad.write_text(PLAINTEXT_YAML, encoding="utf-8")
    assert "Cookie" in bad.read_text(encoding="utf-8")  # 夹具本身先自检
    code = main(["run", str(bad), "--json"])

    assert code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["error_type"] == "credential_plaintext"
    assert payload["errors"][0]["path"] == "$.sources[0].headers.Cookie"


def test_config_error_human_output_goes_to_stderr(tmp_path, capsys):
    """人类可读模式:配置错误走 stderr,含字段路径。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(VALID_YAML + "\nnope: 未知字段\n", encoding="utf-8")
    code = main(["run", str(bad)])

    assert code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "$.nope" in captured.err


def test_pipeline_config_error_exit_one(fake_pipeline, capsys, monkeypatch):
    """Pipeline 构造期的配置错误(route 规则等)也归退出码 1。"""
    real_config = load_category(
        {
            "id": "demo",
            "name": "演示品类",
            "schedule": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "sources": [{"name": "api", "url": "https://api.demo.local/list"}],
        }
    )
    monkeypatch.setattr(cli_module, "load_category_file", lambda path: real_config)

    class BrokenPipeline:
        def __init__(self, config, **kwargs):
            raise ValueError("push[].route 配置非法")

    monkeypatch.setattr(cli_module, "Pipeline", BrokenPipeline)
    code = main(["run", "demo.yaml", "--json"])

    assert code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["error_type"] == "config_error"
    assert "push[].route" in payload["errors"][0]["message"]


# ---------------------------------------------------------------------------
# Usage errors & help & version
# ---------------------------------------------------------------------------


def test_unknown_command_exits_one(capsys):
    """未知子命令:用法错误归退出码 1(不用 argparse 的 2,避免撞码)。"""
    assert main(["bogus"]) == EXIT_CONFIG_ERROR
    assert "usage" in capsys.readouterr().err


def test_once_and_loop_mutually_exclusive(capsys):
    """--once 与 --loop 互斥:用法错误退出码 1。"""
    assert main(["run", "demo.yaml", "--once", "--loop"]) == EXIT_CONFIG_ERROR


def test_no_command_prints_help_exit_zero(capsys):
    """无子命令:打印帮助,退出码 0。"""
    assert main([]) == EXIT_OK
    assert "usage" in capsys.readouterr().out


def test_version_flag(capsys):
    """--version:打印版本号,退出码 0(SystemExit 传播)。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "myia 0.1.0" in capsys.readouterr().out


def test_help_documents_run_and_exit_codes(capsys):
    """--help 输出清晰:run 子命令与退出码契约可见(文档也是 AI 的输入)。"""
    parser = build_parser()
    help_text = parser.format_help()
    assert "run" in help_text
    assert "--dry-run" in help_text
    assert "--loop" in help_text
    assert "退出码" in help_text


def test_run_help_mentions_flags(capsys):
    """run 子命令的 --help 列出全部开关。"""
    parser = build_parser()
    run_parser = next(
        action for action in parser._subparsers._group_actions[0].choices.values()  # type: ignore[union-attr]
        if action.prog.endswith("run")
    )
    help_text = run_parser.format_help()
    for flag in ("--once", "--loop", "--dry-run", "--json", "--db"):
        assert flag in help_text


# ---------------------------------------------------------------------------
# Stub commands(仍留位的子命令;list/test/init/doctor 已在 v0.2 实装,
# 见 test_cli_full.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["add-source", "dashboard"])
def test_stub_commands_not_implemented(command, capsys):
    """留位子命令:结构化 not_implemented 提示,退出码 1。"""
    code = main([command])

    assert code == EXIT_CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "not_implemented"
    assert payload["command"] == command
    assert payload["scheduled_version"] == "v0.2"
