"""A child task's worktree starts on top of its parents' branches, not HEAD."""

from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_cli import kanban_db_workspace as kbw


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=T", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


def _branch(repo: Path, name: str, fname: str, body: str) -> None:
    _git(repo, "branch", name, "main")
    wt = repo.parent / name.replace("/", "_")
    _git(repo, "worktree", "add", str(wt), name)
    (wt / fname).write_text(body)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", name)


def test_child_starts_on_merged_parents(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "wt/p1", "a.txt", "a\n")
    _branch(repo, "wt/p2", "b.txt", "b\n")
    target = repo / ".worktrees" / "child"
    kbw._ensure_git_worktree(repo, target, "wt/child", ("wt/p1", "wt/p2", "wt/gone"))
    assert (target / "a.txt").exists() and (target / "b.txt").exists()
    for p in ("wt/p1", "wt/p2"):
        _git(target, "merge-base", "--is-ancestor", p, "HEAD")  # raises if not ancestor


def test_conflicting_parents_fall_back_to_head(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "wt/p1", "x.txt", "1\n")
    _branch(repo, "wt/p2", "x.txt", "2\n")
    target = repo / ".worktrees" / "child"
    kbw._ensure_git_worktree(repo, target, "wt/child", ("wt/p1", "wt/p2"))
    assert _git(target, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")


def test_no_parents_uses_head(tmp_path):
    repo = _repo(tmp_path)
    target = repo / ".worktrees" / "solo"
    kbw._ensure_git_worktree(repo, target, "wt/solo")
    assert _git(target, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")
