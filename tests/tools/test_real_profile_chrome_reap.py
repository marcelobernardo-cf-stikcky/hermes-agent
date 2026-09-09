"""Real-profile Chrome must survive its owner's crash only until the next sweep.

Regression: ``_real_profile_chrome_procs`` was in-memory only, so a hermes process that died
without running atexit leaked its headless Chrome forever (agent-browser merely ATTACHES to it,
so no other reaper ever touched it). Twelve such processes were found alive on a workstation.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from tools import browser_tool_real_profile as rp


@pytest.fixture
def chrome_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _sleeper(copy_dir):
    """A process whose cmdline carries the profile-copy flag, like the real Chrome launch."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             f"--user-data-dir={copy_dir}"])


def test_dead_owner_chrome_is_reaped(chrome_home):
    copy_dir = str(chrome_home / "browser-profile" / "chrome")
    proc = _sleeper(copy_dir)
    try:
        rp._record_real_profile_chrome(proc.pid, copy_dir)
        # Rewrite the record under a PID that cannot be alive: the owner "crashed".
        record_path = next(rp._chrome_state_dir().glob("*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["owner_pid"] = 2 ** 31 - 1
        record_path.write_text(json.dumps(record), encoding="utf-8")

        assert rp.reap_orphaned_real_profile_chrome() == 1
        assert proc.wait(timeout=10) is not None
        assert not list(rp._chrome_state_dir().glob("*.json")), "record must not outlive the process"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_live_owner_chrome_is_left_alone(chrome_home, monkeypatch):
    """The owner is THIS live process and still tracks the pid — reaping it would kill a
    browser someone is using. Mirrors the launch path: in-memory handle + on-disk record."""
    from tools.browser_tool_origin import origin_module as _origin
    copy_dir = str(chrome_home / "browser-profile" / "chrome")
    proc = _sleeper(copy_dir)
    try:
        monkeypatch.setattr(_origin(), "_real_profile_chrome_procs", [proc])
        rp._record_real_profile_chrome(proc.pid, copy_dir)
        assert rp.reap_orphaned_real_profile_chrome() == 0
        time.sleep(0.2)
        assert proc.poll() is None, "a live owner's chrome must survive the sweep"
    finally:
        proc.kill()


def test_own_untracked_chrome_is_reaped(chrome_home, monkeypatch):
    """Our own record with the handle GONE from memory is a leak, even while a sibling
    launch is still tracked — the sweep is per-pid, not "is any chrome alive"."""
    from tools.browser_tool_origin import origin_module as _origin
    copy_dir = str(chrome_home / "browser-profile" / "chrome")
    sibling, leaked = _sleeper(copy_dir), _sleeper(copy_dir)
    try:
        monkeypatch.setattr(_origin(), "_real_profile_chrome_procs", [sibling])
        rp._record_real_profile_chrome(leaked.pid, copy_dir)
        assert rp.reap_orphaned_real_profile_chrome() == 1
        assert leaked.wait(timeout=10) is not None
        assert sibling.poll() is None, "the tracked sibling must survive"
    finally:
        for p in (sibling, leaked):
            if p.poll() is None:
                p.kill()


def test_recycled_pid_is_not_killed(chrome_home):
    """A PID reused by an unrelated process must never be tree-killed (start_time + cmdline gate)."""
    copy_dir = str(chrome_home / "browser-profile" / "chrome")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        rp._record_real_profile_chrome(bystander.pid, copy_dir)  # records the real start_time
        record_path = next(rp._chrome_state_dir().glob("*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["owner_pid"] = 2 ** 31 - 1  # owner dead -> candidate for reaping
        record_path.write_text(json.dumps(record), encoding="utf-8")

        # The bystander's cmdline lacks --user-data-dir=<copy_dir>, so identity check fails.
        assert rp.reap_orphaned_real_profile_chrome() == 0
        time.sleep(0.2)
        assert bystander.poll() is None, "unverified PID must not be signalled"
    finally:
        bystander.kill()


def test_terminate_clears_only_our_records(chrome_home):
    """atexit teardown drops this PID's records, never another live owner's."""
    copy_dir = str(chrome_home / "browser-profile" / "chrome")
    rp._record_real_profile_chrome(os.getpid(), copy_dir)
    foreign = rp._chrome_state_dir() / "chrome-999999.json"
    foreign.write_text(json.dumps({"pid": 1, "owner_pid": 999999, "copy_dir": copy_dir}), encoding="utf-8")

    rp._terminate_real_profile_chrome()

    remaining = {p.name for p in rp._chrome_state_dir().glob("*.json")}
    assert remaining == {foreign.name}
