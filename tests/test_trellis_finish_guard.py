"""task.py finish 跨会话防护的回归锁(10-03-docs-truth Q8)。

事故语义(2026-10-03 实证):一个没有自有指针的会话跑 `finish`,会经
single-session fallback 解析到唯一在盘会话文件——**另一会话的**指针——并把
它清掉,静默打断那个并行会话的上下文注入。防护契约(task.py cmd_finish):

- fallback 指针(非本会话自有)无 ``--force`` → 拒绝清、exit 1、点名来源会话;
- ``--force`` → 只按点名的 context key 清那一个会话文件(clear_session_pointer);
- 本会话自有指针 → 无需 ``--force`` 正常清,多窗口下不碰他人;
- 空态 → 「No current task set」、exit 0 不变。

分两层:上半对 common/active_task.py 做纯单元(fallback「唯一文件才猜、
≥2 不猜」、按名精确清、只清自有);下半对 task.py finish 走子进程,在隔离
scratch repo 里用 TRELLIS_CONTEXT_ID 模拟双会话——即归档任务
archive/2026-10/10-03-docs-truth/task.json notes 里手动演示(/tmp scratch +
双会话模拟)的自动化固化。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / ".trellis" / "scripts"
TASK_PY = SCRIPTS_DIR / "task.py"

# 与 conftest 对 src/ 的处理同款:.trellis/scripts 不在包安装面,测试进程需
# 先把它上 sys.path 才能导入 common.active_task(该 import 链纯 stdlib)。
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common.active_task as active_task  # noqa: E402
from common.active_task import (  # noqa: E402
    ActiveTask,
    clear_active_task,
    clear_session_pointer,
    resolve_active_task,
)

TASK_REF = ".trellis/tasks/10-03-demo"

# 子进程环境必须剥干净的会话身份变量:宿主会话的身份若泄漏进 scratch repo,
# fallback 场景会被悄悄变成自有指针场景,测试就锁不住事故路径了。名单从
# active_task 的三张 env 表派生——表即 2026-08-05 平台审计的事实源,表更新时
# 这里自动跟进,不另手抄一份防漂移。
_SESSION_ENV_DENYLIST = {"TRELLIS_CONTEXT_ID", "TRELLIS_DEVELOPER"}
for _platform, _keys in (
    *active_task._ENV_SESSION_KEYS,
    *active_task._ENV_CONVERSATION_KEYS,
    *active_task._ENV_TRANSCRIPT_KEYS,
):
    _SESSION_ENV_DENYLIST.update(_keys)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """隔离的最小 trellis repo:.trellis + 一个 task.json 齐全的任务目录。"""
    repo = tmp_path / "scratch-repo"
    task_dir = repo / TASK_REF
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "demo",
                "title": "finish-guard demo",
                "description": "scratch task for finish guard tests",
                "status": "in_progress",
                "priority": "P2",
                "creator": "pytest",
                "assignee": "pytest",
                "createdAt": "2026-10-03",
            }
        ),
        encoding="utf-8",
    )
    return repo


def sessions_dir(repo: Path) -> Path:
    return repo / ".trellis" / ".runtime" / "sessions"


def write_session_pointer(repo: Path, context_key: str, task_ref: str = TASK_REF) -> Path:
    """按 set_active_task 的在盘形态写一个会话指针文件(current_task 键)。"""
    path = sessions_dir(repo) / f"{context_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"platform": "claude", "current_task": task_ref}),
        encoding="utf-8",
    )
    return path


def run_task_py(repo: Path, *args: str, context_id: str | None) -> tuple[int, str]:
    """以指定会话身份在 scratch repo 里跑真实 task.py,返回 (退出码, 合并输出)。"""
    env = {k: v for k, v in os.environ.items() if k not in _SESSION_ENV_DENYLIST}
    if context_id is not None:
        env["TRELLIS_CONTEXT_ID"] = context_id
    proc = subprocess.run(
        [sys.executable, str(TASK_PY), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 单元层:common/active_task.py —— fallback 解析与按名清除
# ---------------------------------------------------------------------------

def test_source_labels_for_session_fallback_and_none() -> None:
    """source 标签三形态:cmd_finish 的拦截判定与点名输出都依赖它。"""
    assert ActiveTask(TASK_REF, "session", "claude_a").source == "session:claude_a"
    assert (
        ActiveTask(TASK_REF, "session-fallback", "claude_a").source
        == "session-fallback:claude_a"
    )
    assert ActiveTask(None, "none").source == "none"


def test_sole_foreign_pointer_resolves_as_fallback(scratch_repo: Path) -> None:
    """盘上唯一会话文件 = 无身份会话的 fallback 来源,须点名其 context key。"""
    write_session_pointer(scratch_repo, "claude_sessionA")

    # allow_environment_context=False:不看宿主 env/shell-ticket,纯看盘上文件
    active = resolve_active_task(scratch_repo, allow_environment_context=False)

    assert active.task_path == TASK_REF
    assert active.source_type == "session-fallback"
    assert active.context_key == "claude_sessionA"
    assert active.source == "session-fallback:claude_sessionA"


def test_two_session_files_refuse_to_guess(scratch_repo: Path) -> None:
    """≥2 个会话文件时 fallback 必须弃猜(04-21 多会话隔离契约)。"""
    write_session_pointer(scratch_repo, "claude_sessionA")
    write_session_pointer(scratch_repo, "claude_sessionB")

    active = resolve_active_task(scratch_repo, allow_environment_context=False)

    assert active.task_path is None
    assert active.source_type == "none"


def test_zero_session_files_is_empty_state(scratch_repo: Path) -> None:
    active = resolve_active_task(scratch_repo, allow_environment_context=False)
    assert active.task_path is None
    assert active.source_type == "none"


def test_clear_session_pointer_deletes_exactly_the_named_file(scratch_repo: Path) -> None:
    """--force 的清除原语:按名精确清,同盘他人指针不动,清不掉如实报 False。"""
    pointer_a = write_session_pointer(scratch_repo, "claude_sessionA")
    pointer_b = write_session_pointer(scratch_repo, "claude_sessionB")

    assert clear_session_pointer(scratch_repo, "claude_sessionA") is True
    assert not pointer_a.exists()
    assert pointer_b.exists()
    assert clear_session_pointer(scratch_repo, "claude_sessionA") is False
    assert clear_session_pointer(scratch_repo, None) is False


def test_clear_active_task_removes_only_own_pointer(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自有指针的清除路径:经当前会话身份解析,多窗口下不误伤他人。"""
    write_session_pointer(scratch_repo, "claude_sessionA")
    write_session_pointer(scratch_repo, "claude_sessionB")
    monkeypatch.setenv("TRELLIS_CONTEXT_ID", "claude_sessionA")

    previous = clear_active_task(scratch_repo)

    assert previous.task_path == TASK_REF
    assert previous.source == "session:claude_sessionA"
    assert not (sessions_dir(scratch_repo) / "claude_sessionA.json").exists()
    assert (sessions_dir(scratch_repo) / "claude_sessionB.json").exists()


# ---------------------------------------------------------------------------
# 子进程层:task.py finish 防护(隔离 scratch repo + 双会话模拟)
# ---------------------------------------------------------------------------

def test_own_pointer_finish_needs_no_force(scratch_repo: Path) -> None:
    """会话 A start 后自己 finish:正常清、exit 0,不需要 --force。"""
    rc, out = run_task_py(
        scratch_repo,
        "start",
        "10-03-demo",
        "--allow-empty-context",
        context_id="claude_sessionA",
    )
    assert rc == 0, out
    assert "Current task set to" in out
    assert "session:claude_sessionA" in out
    pointer = sessions_dir(scratch_repo) / "claude_sessionA.json"
    assert pointer.is_file()

    rc, out = run_task_py(scratch_repo, "finish", context_id="claude_sessionA")

    assert rc == 0, out
    assert "Cleared current task" in out
    assert "session:claude_sessionA" in out
    assert not pointer.exists()


def test_own_pointer_finish_leaves_other_sessions_alone(scratch_repo: Path) -> None:
    """A 清自有指针时盘上还有 B 的指针:只清 A,B 原样(多窗口共存)。"""
    rc, out = run_task_py(
        scratch_repo,
        "start",
        "10-03-demo",
        "--allow-empty-context",
        context_id="claude_sessionA",
    )
    assert rc == 0, out
    pointer_b = write_session_pointer(scratch_repo, "claude_sessionB")

    rc, out = run_task_py(scratch_repo, "finish", context_id="claude_sessionA")

    assert rc == 0, out
    assert not (sessions_dir(scratch_repo) / "claude_sessionA.json").exists()
    assert pointer_b.exists()


def test_finish_refuses_foreign_fallback_pointer(scratch_repo: Path) -> None:
    """事故场景:会话 B 无自有指针,finish 经 fallback 摸到 A 的指针——
    必须 exit 1、拒绝清、点名来源会话并给 --force 提示,A 的指针一字不动。"""
    rc, out = run_task_py(
        scratch_repo,
        "start",
        "10-03-demo",
        "--allow-empty-context",
        context_id="claude_sessionA",
    )
    assert rc == 0, out
    pointer = sessions_dir(scratch_repo) / "claude_sessionA.json"
    assert pointer.is_file()

    rc, out = run_task_py(scratch_repo, "finish", context_id="claude_sessionB")

    assert rc == 1, out
    assert "Refused" in out
    assert "session-fallback:claude_sessionA" in out
    assert "--force" in out
    assert json.loads(pointer.read_text(encoding="utf-8"))["current_task"] == TASK_REF


def test_finish_force_clears_the_named_session_pointer(scratch_repo: Path) -> None:
    """--force = 显式确认:只清点名的那个会话文件,exit 0。"""
    rc, out = run_task_py(
        scratch_repo,
        "start",
        "10-03-demo",
        "--allow-empty-context",
        context_id="claude_sessionA",
    )
    assert rc == 0, out
    pointer = sessions_dir(scratch_repo) / "claude_sessionA.json"
    assert pointer.is_file()

    rc, out = run_task_py(
        scratch_repo, "finish", "--force", context_id="claude_sessionB"
    )

    assert rc == 0, out
    assert "Cleared current task" in out
    assert "session-fallback:claude_sessionA" in out
    assert not pointer.exists()


def test_finish_empty_state_is_exit_zero(scratch_repo: Path) -> None:
    """空态(无任何会话指针):「No current task set」、exit 0 不变。"""
    rc, out = run_task_py(scratch_repo, "finish", context_id="claude_sessionB")

    assert rc == 0, out
    assert "No current task set" in out
