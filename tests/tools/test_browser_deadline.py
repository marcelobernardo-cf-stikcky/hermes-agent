"""Regression: browser_exec bounds the WHOLE call, not just the CLI subprocess.

Two holes were proven on this Windows Python before the fix
(scratch/browser-timeout-diagnosis.md):
  1. setup (_route_backend -> real-profile stale close / snapshot / launch)
     ran BEFORE any timeout was established, so a slow setup added unbounded
     wall-clock on top of timeout_s. Observed: a 45s call returned at 420s.
  2. subprocess.run(timeout=) is not bounded on Windows when a descendant
     inherits the captured pipes: run() kills only the direct child then
     joins reader threads forever. Measured: 0.3s requested -> 4.11s.

Both assert on observable behaviour (elapsed time / which timeout the CLI
subprocess receives), never on log text.
"""
from __future__ import annotations

import subprocess
import time

from tools import browser_use_cli as bu


def test_slow_setup_shrinks_the_cli_timeout(monkeypatch):
    """Setup spend must come OUT of timeout_s, not be added on top of it."""
    seen: dict = {}
    setup_cost = 2.0

    def slow_route(env, session, task_id, local, deadline=None):
        assert deadline is not None, "browser_exec must set the deadline BEFORE setup"
        time.sleep(setup_cost)
        return None

    def fake_run(cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(bu, "_find_cli", lambda: ["browser-use"])
    monkeypatch.setattr(bu, "_route_backend", slow_route)
    monkeypatch.setattr(bu.subprocess, "run", fake_run)

    budget = 8
    bu.browser_exec("print(1)", timeout_s=budget)

    # RED (setup outside the budget): timeout == budget, total could reach
    # budget + setup_cost. GREEN: the CLI gets only what setup left over.
    assert seen["timeout"] <= budget - setup_cost + 0.5, (
        f"CLI timeout {seen['timeout']!r} did not absorb the "
        f"{setup_cost}s setup out of the {budget}s budget"
    )


def test_setup_that_ate_the_budget_still_bounds_the_cli_call(monkeypatch):
    """Deliberate design: a spent budget floors at _MIN_TIMEOUT_S, never unbounded.

    The floor exists so a slow-but-SUCCESSFUL setup still gets a workable call
    instead of an instant, confusing timeout. What must not happen is the CLI
    receiving the full timeout_s again (setup billed twice) or no timeout at all.
    """
    seen: dict = {}

    def exhausting_route(env, session, task_id, local, deadline=None):
        while deadline is not None and deadline - time.monotonic() > 0:
            time.sleep(0.05)
        return None

    def fake_run(cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(bu, "_find_cli", lambda: ["browser-use"])
    monkeypatch.setattr(bu, "_route_backend", exhausting_route)
    monkeypatch.setattr(bu.subprocess, "run", fake_run)

    budget = bu._MIN_TIMEOUT_S * 3
    bu.browser_exec("print(1)", timeout_s=budget)

    assert seen["timeout"] is not None, "the CLI must always get a timeout"
    assert seen["timeout"] == bu._MIN_TIMEOUT_S, (
        f"expected the {bu._MIN_TIMEOUT_S}s floor after setup spent the budget, "
        f"got {seen['timeout']!r}"
    )
    assert seen["timeout"] < budget, "setup spend must not be billed twice"


def test_bounded_probe_run_returns_despite_pipe_holding_descendant():
    """The Windows post-timeout join hole: run() blocks, bounded_probe_run does not."""
    from hermes_cli._subprocess_compat import bounded_probe_run

    # Parent exits at once; the child outlives it holding the inherited pipes.
    code = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "stdout=sys.stdout,stderr=sys.stderr);"
        "import time;time.sleep(30)"
    )
    started = time.monotonic()
    result = bounded_probe_run([__import__("sys").executable, "-c", code], timeout=1.0)
    elapsed = time.monotonic() - started

    assert result is None, "a timed-out probe must report failure, not a fake success"
    assert elapsed < 12.0, (
        f"bounded_probe_run took {elapsed:.1f}s for a 1s timeout - the descendant "
        "still blocks the pipe drain"
    )
