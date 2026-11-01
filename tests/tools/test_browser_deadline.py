"""Regression: a probe subprocess stays bounded when a descendant holds its pipes.

subprocess.run(timeout=) is not bounded on Windows when a descendant inherits
the captured pipes: run() kills only the direct child then joins reader threads
forever. Measured: 0.3s requested -> 4.11s.

The fork's setup-deadline tests (_route_backend(deadline=)) were retired after
the upstream merge: upstream bounds each setup step itself (SQLite auth backup
deadline, 58d6d5223a) and browser_exec kills the CLI's whole process group on
timeout (_run_cli_killing_process_group, #106244).
"""
from __future__ import annotations

import time


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
