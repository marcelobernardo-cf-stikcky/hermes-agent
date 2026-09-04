"""Iteration-budget timeout parks the card for the orchestrator.

Contract (t_c0923819): a worker that exhausts N/N iterations must not
return to ``ready`` (the dispatcher would respawn it). It stays
``blocked`` with a ``timed_out`` event carrying ``retry_status=blocked``
and ``timeout_reason=iteration_budget``. Crash / spawn_failed / wall-clock
timeout still retry on the same rung until ``failure_limit``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _claimed(conn, title="iteration budget job", **create_kwargs):
    tid = kb.create_task(conn, title=title, assignee="worker", **create_kwargs)
    claimed = kb.claim_task(conn, tid)
    assert claimed is not None
    return tid


def _iteration_timeout(conn, tid, *, failure_limit=2):
    return kb._record_task_failure(
        conn,
        tid,
        error="Iteration budget exhausted (90/90) — task could not complete "
        "within the allowed iterations",
        outcome="timed_out",
        failure_limit=failure_limit,
        release_claim=True,
        end_run=True,
        hold=True,
        event_payload_extra={
            "timeout_reason": "iteration_budget",
            "budget_used": 90,
            "budget_max": 90,
        },
    )


def test_iteration_budget_timeout_parks_blocked_with_no_respawn(
    kanban_home, all_assignees_spawnable,
):
    """Worker exhausts N/N iterations → blocked, 0 automatic respawn."""
    conn = kb.connect()
    try:
        tid = _claimed(conn)
        tripped = _iteration_timeout(conn, tid, failure_limit=2)
        assert tripped is False

        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.consecutive_failures == 1
        assert task.claim_lock is None
        assert task.worker_pid is None
        assert task.current_run_id is None

        timed_out = next(e for e in kb.list_events(conn, tid) if e.kind == "timed_out")
        assert timed_out.payload["retry_status"] == "blocked"
        assert timed_out.payload["timeout_reason"] == "iteration_budget"
        assert timed_out.payload["budget_used"] == 90
        assert timed_out.payload["budget_max"] == 90

        assert kb.recompute_ready(conn) == 0
        assert kb.get_task(conn, tid).status == "blocked"

        result = kb.dispatch_once(conn, spawn_fn=lambda *a, **k: 4242)
        assert not any(row[0] == tid for row in result.spawned)
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()


def test_iteration_budget_timeout_still_counts_toward_breaker(kanban_home):
    """Crash then iteration-budget timeout still trips the circuit breaker."""
    conn = kb.connect()
    try:
        tid = _claimed(conn)
        crashed = kb._record_task_failure(
            conn, tid, "pid 991111 exited with code 1",
            outcome="crashed", failure_limit=2,
            release_claim=True, end_run=True,
        )
        assert crashed is False
        after_crash = kb.get_task(conn, tid)
        assert after_crash.status == "ready"
        assert after_crash.consecutive_failures == 1

        claimed = kb.claim_task(conn, tid)
        assert claimed is not None
        tripped = _iteration_timeout(conn, tid, failure_limit=2)
        assert tripped is True
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.consecutive_failures == 2
        gave_up = next(e for e in kb.list_events(conn, tid) if e.kind == "gave_up")
        assert gave_up.payload["retry_status"] == "blocked"
        assert gave_up.payload["trigger_outcome"] == "timed_out"
        assert kb.recompute_ready(conn) == 0
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()


def test_isolated_crash_still_retries(kanban_home, all_assignees_spawnable):
    """A single crash returns to ready and the dispatcher may respawn."""
    conn = kb.connect()
    try:
        tid = _claimed(conn)
        crashed = kb._record_task_failure(
            conn, tid, "pid 991112 exited with code 1",
            outcome="crashed", failure_limit=2,
            release_claim=True, end_run=True,
        )
        assert crashed is False
        task = kb.get_task(conn, tid)
        assert task.status == "ready"
        assert task.consecutive_failures == 1

        result = kb.dispatch_once(conn, spawn_fn=lambda *a, **k: 4243)
        assert any(row[0] == tid for row in result.spawned)
        assert kb.get_task(conn, tid).status == "running"
    finally:
        conn.close()


def test_wall_clock_timeout_retries_and_labels_reason(kanban_home, monkeypatch):
    """max_runtime SIGTERM still requeues; the event names the wall-clock reason."""
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)
    killed = []

    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn, title="wall clock job", assignee="worker", max_runtime_seconds=1,
        )
        kb.claim_task(conn, tid)
        kb._set_worker_pid(conn, tid, os.getpid())
        old_started = int(time.time()) - 30
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET started_at = ? WHERE id = ?", (old_started, tid),
            )
            conn.execute(
                "UPDATE task_runs SET started_at = ? "
                "WHERE id = (SELECT current_run_id FROM tasks WHERE id = ?)",
                (old_started, tid),
            )

        timed_out = kb.enforce_max_runtime(
            conn, signal_fn=lambda pid, sig: killed.append((pid, sig)),
        )
        assert tid in timed_out
        task = kb.get_task(conn, tid)
        assert task.status == "ready"
        assert task.consecutive_failures == 1

        ev = next(e for e in kb.list_events(conn, tid) if e.kind == "timed_out")
        assert ev.payload["retry_status"] == "ready"
        assert ev.payload["timeout_reason"] == "wall_clock"
        assert ev.payload["limit_seconds"] == 1
    finally:
        conn.close()


def test_record_kanban_budget_exhausted_holds_real_card(kanban_home):
    """The worker-side bridge parks the live card, not just the kernel helper."""
    import logging

    from agent.turn_finalizer import _record_kanban_budget_exhausted

    conn = kb.connect()
    try:
        tid = _claimed(conn)
    finally:
        conn.close()

    _record_kanban_budget_exhausted(
        tid, 90, 90, logging.getLogger("test.iteration_budget_hold"),
    )

    conn = kb.connect()
    try:
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.consecutive_failures == 1
        ev = next(e for e in kb.list_events(conn, tid) if e.kind == "timed_out")
        assert ev.payload["retry_status"] == "blocked"
        assert ev.payload["timeout_reason"] == "iteration_budget"
        assert kb.recompute_ready(conn) == 0
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()
