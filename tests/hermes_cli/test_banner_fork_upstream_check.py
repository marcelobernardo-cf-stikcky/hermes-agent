"""A fork's update check must compare against ``upstream``, not its own ``origin``.

Real git repos, no mocks: a fork's ``origin`` is the user's OWN repo and never advances on
its own, so an origin-only compare reports "up to date" forever while the official repo
moves on. A local fork sat 659 commits behind with a silent badge because of this.
"""

import subprocess

import pytest

from hermes_cli import banner


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _commit(repo, message):
    (repo / f"{message}.txt").write_text(message, encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", message, cwd=repo)


@pytest.fixture
def fork_setup(tmp_path):
    """A fork whose origin is frozen while upstream gains 2 commits."""
    official = tmp_path / "official"
    official.mkdir()
    _git("init", "-b", "main", cwd=official)
    _commit(official, "base")

    fork_origin = tmp_path / "fork-origin"
    _git("clone", "--bare", str(official), str(fork_origin), cwd=tmp_path)

    checkout = tmp_path / "checkout"
    _git("clone", str(fork_origin), str(checkout), cwd=tmp_path)
    _git("remote", "add", "upstream", str(official), cwd=checkout)

    # Upstream moves on; the fork's origin stays frozen.
    _commit(official, "new1")
    _commit(official, "new2")
    return checkout


def test_fork_check_counts_upstream_commits(fork_setup):
    assert banner._check_via_local_git(fork_setup) == 2


def test_fork_check_is_zero_when_no_upstream_remote(fork_setup):
    """Without an ``upstream`` remote the check falls back to origin (frozen -> 0).

    This is the pre-fix behaviour and the bug being guarded: it must only happen
    when there is genuinely no upstream to compare against.
    """
    _git("remote", "remove", "upstream", cwd=fork_setup)
    assert banner._check_via_local_git(fork_setup) == 0


def test_non_fork_still_uses_origin(tmp_path):
    """A plain (non-fork) checkout has no ``upstream``; origin is the real source."""
    official = tmp_path / "official"
    official.mkdir()
    _git("init", "-b", "main", cwd=official)
    _commit(official, "base")

    checkout = tmp_path / "checkout"
    _git("clone", str(official), str(checkout), cwd=tmp_path)
    _commit(official, "new1")

    assert banner._check_via_local_git(checkout) == 1
