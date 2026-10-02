"""Tests for ``myia skill install|path`` — the Agent Skill install path (task 10-02-v11).

PRD 10-02-v11-skill-install 验收的四类子行为,全部在临时目录完成:

1. **安装** — 复制 SKILL.md 到 agent 技能根下的 ``myia/SKILL.md``
   (claude/cursor/zcode/agents 四类 + ``--path`` 自定义);
2. **覆盖** — 目标已存在默认结构化拒绝(退出码 1,code=target_exists),
   ``--force`` 才覆盖(overwritten=true 如实回填);
3. **拒绝** — 未知 agent 用法错误、源缺失(skill_source_not_found)都结构化;
4. **--json 契约** — 每条命令 stdout 恰好一份 JSON(可被 json/jq 解析)。

隔离红线:**绝不碰真实家目录** —— autouse fixture 把 HOME/USERPROFILE 指向
tmp_path,所有断言只看临时目录下的产物。``--link`` 模式额外验证符号链接
指向仓库源 SKILL.md(只读引用,不写仓库)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myia.cli import EXIT_CONFIG_ERROR, EXIT_OK, main

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 被安装的源:仓库 canonical skill/SKILL.md(test_skill_doc.py 锁定不漂移)。
SOURCE_SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"

#: PRD 点名的三类 + agents 兜底,与 cli.SKILL_AGENT_DIRS 同一集合。
EXPECTED_AGENTS = ("agents", "claude", "cursor", "zcode")


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME/USERPROFILE to a temp dir — no test touches the real home.

    cli 用 ``Path(...).expanduser()`` 解析技能根,POSIX 跟随 HOME(Windows
    跟随 USERPROFILE),两边都指过去;同时清掉 MYIA_SKILL_SOURCE,保证探测
    走仓库布局这条默认路径。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MYIA_SKILL_SOURCE", raising=False)
    return home


def run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    """Run the CLI, assert exit code, parse the single stdout JSON document."""
    code = main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)  # json.loads 可解析 ⇔ jq 可解析(同一子集)


def run_plain(argv: list[str], capsys: pytest.CaptureFixture[str]) -> int:
    """Run the CLI and discard its output(多次调用时防止 stdout 跨调用串档)."""
    code = main(argv)
    capsys.readouterr()
    return code


# ---------------------------------------------------------------------------
# myia skill path
# ---------------------------------------------------------------------------


def test_skill_path_reports_source_and_all_agents(capsys):
    """path --json:源位置 + 每类 agent 的推荐路径,装前 installed=false。"""
    code, payload = run_json(["skill", "path", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["command"] == "skill"
    assert payload["action"] == "path"
    assert payload["source"]["found"] is True
    assert payload["source"]["path"] == str(SOURCE_SKILL_MD)
    agents = {row["agent"]: row for row in payload["agents"]}
    assert set(agents) == set(EXPECTED_AGENTS)
    for agent, row in agents.items():
        assert row["skill_file"] == str(Path.home() / f".{agent}" / "skills" / "myia" / "SKILL.md")
        assert row["installed"] is False  # HOME 已指向临时目录:全新状态
    assert "install" in payload["hint"]


def test_skill_path_reflects_installed_state(capsys):
    """装完后 path 如实翻转 installed/matches_source(--link 时 is_link=true)。"""
    assert run_plain(["skill", "install", "--agent", "claude", "--json"], capsys) == EXIT_OK
    code, payload = run_json(["skill", "path", "--json"], capsys)
    assert code == EXIT_OK
    claude = next(row for row in payload["agents"] if row["agent"] == "claude")
    assert claude["installed"] is True
    assert claude["is_link"] is False
    assert claude["matches_source"] is True  # 副本内容与源一致(同文件内容判定走 resolve 前提)
    cursor = next(row for row in payload["agents"] if row["agent"] == "cursor")
    assert cursor["installed"] is False  # 其他 agent 不受影响


def test_skill_path_human_mode_mentions_agent_paths(capsys):
    """人类模式同一信息:源位置 + 各 agent 路径 + 安装状态。"""
    code = main(["skill", "path"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "MYIA skill path" in out
    for agent in EXPECTED_AGENTS:
        assert agent in out


# ---------------------------------------------------------------------------
# myia skill install — 四类子行为(PRD 验收)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["claude", "cursor", "zcode", "agents"])
def test_install_copy_reaches_agent_default_dir(agent, capsys):
    """安装(副本):落到 <HOME>/.<agent>/skills/myia/SKILL.md,内容与源一致。"""
    code, payload = run_json(["skill", "install", "--agent", agent, "--json"], capsys)
    assert code == EXIT_OK
    assert payload["command"] == "skill"
    assert payload["action"] == "install"
    assert payload["agent"] == agent
    assert payload["mode"] == "copy"
    assert payload["overwritten"] is False
    target = Path.home() / f".{agent}" / "skills" / "myia" / "SKILL.md"
    assert payload["target"] == str(target)
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == SOURCE_SKILL_MD.read_text(encoding="utf-8")
    # 隔离红线:产物必须落在临时 HOME 下,真实家目录零写入
    assert str(target).startswith(str(Path.home()))


def test_install_refuses_existing_target_structured(capsys):
    """已存在默认结构化拒绝(退出码 1),目标文件原样保留。"""
    assert run_plain(["skill", "install", "--agent", "claude", "--json"], capsys) == EXIT_OK
    target = Path.home() / ".claude" / "skills" / "myia" / "SKILL.md"
    target.write_text("TAMPERED", encoding="utf-8")  # 模拟旧版本/被改动的已装副本
    code, payload = run_json(["skill", "install", "--agent", "claude", "--json"], capsys)
    assert code == EXIT_CONFIG_ERROR
    assert payload["error"] == "skill_install"
    assert payload["code"] == "target_exists"
    assert "SKILL.md" in payload["message"]
    assert target.read_text(encoding="utf-8") == "TAMPERED"  # 拒绝 = 零副作用


def test_install_force_overwrites_existing(capsys):
    """--force 才允许覆盖:内容回到源版本,overwritten=true 如实回填。"""
    assert run_plain(["skill", "install", "--agent", "claude", "--json"], capsys) == EXIT_OK
    target = Path.home() / ".claude" / "skills" / "myia" / "SKILL.md"
    target.write_text("TAMPERED", encoding="utf-8")
    code, payload = run_json(["skill", "install", "--agent", "claude", "--force", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["forced"] is True
    assert payload["overwritten"] is True
    assert target.read_text(encoding="utf-8") == SOURCE_SKILL_MD.read_text(encoding="utf-8")


def test_install_force_copy_replaces_symlink_without_touching_target(capsys, fake_home):
    """copy+force 遇符号链接目标:链接被替换为普通文件,链接指向的文件零改动。

    Regression(v1.1 评审):copy 分支此前直接 ``shutil.copy2`` 沿符号链接
    写入 —— --force 会把链接指向的文件(可在安装目录之外,dotfiles/stow
    场景)整体写穿,授权范围明明只有 <技能根>/myia/SKILL.md。
    """
    victim = fake_home / "victim" / "notes.txt"
    victim.parent.mkdir()
    victim.write_text("KEEP ME", encoding="utf-8")
    target_dir = Path.home() / ".claude" / "skills" / "myia"
    target_dir.mkdir(parents=True)
    target = target_dir / "SKILL.md"
    target.symlink_to(victim)

    code, payload = run_json(["skill", "install", "--agent", "claude", "--force", "--json"], capsys)

    assert code == EXIT_OK
    assert payload["mode"] == "copy"
    assert payload["overwritten"] is True
    assert target.is_file() and not target.is_symlink()  # 链接被实体文件替换
    assert target.read_text(encoding="utf-8") == SOURCE_SKILL_MD.read_text(encoding="utf-8")
    assert victim.read_text(encoding="utf-8") == "KEEP ME"  # 链接目标零改动


def test_install_copy_replaces_dangling_symlink_without_force(capsys, fake_home):
    """悬空链接视为未完成安装:无 --force 也允许补装(与缺 SKILL.md 同语义)。"""
    target_dir = Path.home() / ".zcode" / "skills" / "myia"
    target_dir.mkdir(parents=True)
    target = target_dir / "SKILL.md"
    target.symlink_to(fake_home / "gone" / "removed.md")  # 悬空:指向不存在的文件

    code, payload = run_json(["skill", "install", "--agent", "zcode", "--json"], capsys)

    assert code == EXIT_OK
    assert payload["mode"] == "copy"
    assert target.is_file() and not target.is_symlink()
    assert target.read_text(encoding="utf-8") == SOURCE_SKILL_MD.read_text(encoding="utf-8")


def test_install_link_creates_symlink_to_source(capsys):
    """--link:目标是指向仓库源 SKILL.md 的符号链接。"""
    code, payload = run_json(["skill", "install", "--agent", "zcode", "--link", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["mode"] == "link"
    target = Path.home() / ".zcode" / "skills" / "myia" / "SKILL.md"
    assert target.is_symlink()
    assert target.resolve() == SOURCE_SKILL_MD.resolve()


def test_install_link_force_replaces_existing_copy(capsys):
    """先副本后 --link --force:真实文件被替换成符号链接。"""
    assert run_plain(["skill", "install", "--agent", "claude", "--json"], capsys) == EXIT_OK
    target = Path.home() / ".claude" / "skills" / "myia" / "SKILL.md"
    assert not target.is_symlink()
    code, payload = run_json(["skill", "install", "--agent", "claude", "--link", "--force", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["mode"] == "link"
    assert target.is_symlink()


def test_install_custom_path_wins_over_agent(capsys):
    """--path 自定义目录优先于 --agent 探测;agent 仍如实记录。"""
    custom = Path.home() / "somewhere" / "skills" / "myia"
    code, payload = run_json(
        ["skill", "install", "--agent", "claude", "--path", str(custom), "--json"], capsys
    )
    assert code == EXIT_OK
    assert payload["target"] == str(custom / "SKILL.md")
    assert (custom / "SKILL.md").is_file()
    assert not (Path.home() / ".claude" / "skills").exists()  # 探测路径未被写入


def test_install_without_agent_probes_existing_root(capsys):
    """缺省 --agent:探测本机已存在的技能根;一个都没有时退到 claude。"""
    (Path.home() / ".zcode" / "skills").mkdir(parents=True)
    code, payload = run_json(["skill", "install", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["agent"] == "zcode"


def test_install_without_agent_defaults_to_claude_on_clean_home(capsys):
    """全新 HOME(无任何技能根):按 claude 兜底,不猜其他目录。"""
    code, payload = run_json(["skill", "install", "--json"], capsys)
    assert code == EXIT_OK
    assert payload["agent"] == "claude"


# ---------------------------------------------------------------------------
# 结构化拒绝(退出码 1)
# ---------------------------------------------------------------------------


def test_install_unknown_agent_is_structured_usage_error(capsys):
    """未知 agent:argparse 用法错误 → 结构化 error=usage,退出码 1。"""
    code = main(["skill", "install", "--agent", "nope", "--json"])
    captured = capsys.readouterr()
    assert code == EXIT_CONFIG_ERROR
    usage = json.loads(captured.err)  # 用法错误走 stderr(main 的 _UsageError 分支)
    assert usage["error"] == "usage"
    assert "--agent" in usage["message"]
    assert captured.out == ""


def test_install_missing_source_structured(capsys, monkeypatch, tmp_path):
    """源缺失(MYIA_SKILL_SOURCE 指向不存在文件):结构化拒绝且零写入。

    显式指定不静默回退到仓库布局 —— 否则会装上另一份文件。
    """
    monkeypatch.setenv("MYIA_SKILL_SOURCE", str(tmp_path / "nope" / "SKILL.md"))
    code, payload = run_json(["skill", "install", "--agent", "claude", "--json"], capsys)
    assert code == EXIT_CONFIG_ERROR
    assert payload["error"] == "skill_source"
    assert payload["code"] == "skill_source_not_found"
    assert not (Path.home() / ".claude").exists()


def test_install_never_writes_outside_temp_home(capsys, fake_home):
    """全部 agent 安装后,HOME 下只出现各 agent 的技能根(零越界写入)。"""
    for agent in EXPECTED_AGENTS:
        assert main(["skill", "install", "--agent", agent, "--json"]) == EXIT_OK
        capsys.readouterr()
    home_entries = {entry.name for entry in fake_home.iterdir()}
    assert home_entries == {f".{agent}" for agent in EXPECTED_AGENTS}
    for agent in EXPECTED_AGENTS:
        assert (fake_home / f".{agent}" / "skills" / "myia" / "SKILL.md").is_file()
