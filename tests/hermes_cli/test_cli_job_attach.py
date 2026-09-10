"""CLI entry point must self-attach to a kill-on-close job on Windows.

Contract, not snapshot: an unclean CLI exit (kill/crash/Desktop teardown) runs no
atexit, so without the job object every terminal/pytest/browser child it spawned
survives as an orphan. gateway/run.py and web_server.py already self-attach; this
pins the same guarantee for the CLI, which is the spawner of nearly all of them.
"""

import pytest

import hermes_cli.main as main_mod
import hermes_cli.process_identity as process_identity


def test_cli_main_attaches_to_kill_on_close_job(monkeypatch):
    calls = []
    monkeypatch.setattr(
        process_identity, "attach_self_to_kill_on_close_job",
        lambda: calls.append(True) or True,
    )
    # --help exits via argparse; the attach must already have happened by then.
    monkeypatch.setattr(main_mod.sys, "argv", ["hermes", "--help"])
    with pytest.raises(SystemExit):
        main_mod.main()

    assert calls, "hermes_cli.main.main() must attach to a kill-on-close job object"


def test_job_attach_is_idempotent_and_never_raises():
    # Called on every CLI start; a second call must be a cheap no-op, never a throw.
    first = process_identity.attach_self_to_kill_on_close_job()
    second = process_identity.attach_self_to_kill_on_close_job()
    assert first == second
