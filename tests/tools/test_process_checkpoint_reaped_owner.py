"""Checkpoint recovery kills a job whose conversation was auto-reaped while its owner was dead.

The live reap (kill_for_session) only reaches jobs in its own registry; a job left by a dead
worker/gateway is re-adopted from processes.json on every boot and was never stopped. Open
conversations, user-closed ones (that keeps background work) and persist_on_release jobs survive.
"""

import json
import sqlite3
import subprocess
import sys
import time

import pytest

from tools.process_registry import ProcessRegistry


def _sleep_proc() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("ended_at, reason, persist, killed", [
    (1.0, "ws_orphan_reap", False, True),
    (None, None, False, False),
    (1.0, "tui_close", False, False),
    (1.0, "ws_orphan_reap", True, False),
])
def test_recover_kills_only_jobs_of_auto_reaped_sessions(tmp_path, monkeypatch, ended_at, reason, persist, killed):
    monkeypatch.setattr("tools.process_registry_checkpoint.get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    with sqlite3.connect(tmp_path / "state.db") as con:
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, ended_at REAL, end_reason TEXT)")
        con.execute("INSERT INTO sessions VALUES ('conv1', ?, ?)", (ended_at, reason))
    proc = _sleep_proc()
    try:
        checkpoint = tmp_path / "procs.json"
        checkpoint.write_text(json.dumps([{
            "session_id": "proc_orphan", "command": "python -m http.server", "pid": proc.pid,
            "pid_scope": "host", "host_start_time": ProcessRegistry._safe_host_start_time(proc.pid),
            "task_id": "t1", "parent_session_id": "conv1", "session_key": "",
            "persist_on_release": persist, "started_at": time.time(),
        }]))
        monkeypatch.setattr("tools.process_registry.CHECKPOINT_PATH", checkpoint)

        recovered = ProcessRegistry().recover_from_checkpoint()

        assert (proc.wait(timeout=10) is not None) if killed else proc.poll() is None
        assert recovered == (0 if killed else 1)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
