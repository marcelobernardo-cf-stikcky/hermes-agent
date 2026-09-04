"""Behavioral coverage for profile-scoped Kanban skill preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_diagnostics as kd


@pytest.fixture
def profile_homes(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    profiles = root / "profiles"
    assignee_home = profiles / "exhibitionist"
    assignee_home.mkdir(parents=True)
    (assignee_home / "skills").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    kb.init_db()
    return root, assignee_home


def _skill(home: Path, name: str) -> None:
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\nUse this skill.\n",
        encoding="utf-8",
    )


def test_preflight_uses_assignee_home_not_default_home(profile_homes):
    root, assignee_home = profile_homes
    _skill(root, "only-in-default")

    assert kb._unknown_profile_skills("exhibitionist", ["only-in-default"]) == [
        "only-in-default"
    ]
    assert not (assignee_home / "skills" / "only-in-default").exists()


def test_create_task_rejects_missing_skill_before_insert(profile_homes):
    with kb.connect() as conn:
        with pytest.raises(ValueError, match="not available in assignee profile"):
            kb.create_task(
                conn,
                title="must not be created",
                assignee="exhibitionist",
                skills=["missing-skill"],
            )
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_dispatch_skips_missing_skill_without_run_or_failure(profile_homes):
    root, assignee_home = profile_homes
    _skill(assignee_home, "present-then-removed")
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="preflight me",
            assignee="exhibitionist",
            skills=["present-then-removed"],
        )
        (assignee_home / "skills" / "present-then-removed" / "SKILL.md").unlink()

        result = kb.dispatch_once(conn, spawn_fn=lambda *_args: pytest.fail("spawned"))
        task = kb.get_task(conn, task_id)

        assert result.skipped_unknown_skill == [(task_id, ["present-then-removed"])]
        assert task is not None and task.status == "ready"
        assert kb.list_runs(conn, task_id) == []
        assert task.consecutive_failures == 0


def test_existing_assignee_skill_spawns_normally(profile_homes):
    _, assignee_home = profile_homes
    _skill(assignee_home, "worker-skill")
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="spawn me",
            assignee="exhibitionist",
            skills=["worker-skill"],
        )
        result = kb.dispatch_once(conn, spawn_fn=lambda *_args: 12345)

        assert [entry[0] for entry in result.spawned] == [task_id]
        task = kb.get_task(conn, task_id)
        assert task is not None and task.status == "running"
        assert len(kb.list_runs(conn, task_id)) == 1


def test_unknown_skill_is_visible_in_diagnostics(profile_homes):
    root, _ = profile_homes
    _skill(root, "default-only")
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="diagnose me",
            assignee="exhibitionist",
            skills=[],
        )
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET skills = ? WHERE id = ?",
                (json.dumps(["default-only"]), task_id),
            )
        task = kb.get_task(conn, task_id)

        diagnostics = kd.compute_task_diagnostics(task, [], [], now=100)

    unknown = [diag for diag in diagnostics if diag.kind == "unknown_skill"]
    assert len(unknown) == 1
    assert unknown[0].data["missing_skills"] == ["default-only"]
    assert unknown[0].data["assignee"] == "exhibitionist"
