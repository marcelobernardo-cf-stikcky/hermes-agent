"""Regression: the real-profile snapshot cannot hang on a contended copy dir."""
from __future__ import annotations

import sqlite3

import pytest

from hermes_cli import browser_connect as bc


def test_backup_deadline_guard_raises_once_the_budget_is_gone():
    """`Connection.backup()` retries forever on SQLITE_BUSY; only `progress` can stop it."""
    guard = bc._backup_deadline_guard(budget_s=-1.0)  # already expired
    with pytest.raises(TimeoutError):
        guard(0, 5, 10)


def test_backup_deadline_guard_is_quiet_inside_the_budget():
    guard = bc._backup_deadline_guard(budget_s=30.0)
    assert guard(0, 5, 10) is None


def test_copy_auth_file_steps_the_backup_with_a_deadline(tmp_path, monkeypatch):
    """A STEPPED backup is what makes the guard reachable: an unstepped
    `backup(out)` runs to completion inside one C call, so `progress` never
    fires and no deadline can interrupt the SQLITE_BUSY retry loop.

    `sqlite3.Connection` is an immutable type (cannot be monkeypatched), so the
    spy rides in through `factory=`, which is the supported hook.
    """
    src = tmp_path / "Cookies"
    con = sqlite3.connect(src)
    con.execute("create table t (a)")
    con.commit()
    con.close()

    seen: list[dict] = []

    class _Spy(sqlite3.Connection):
        def backup(self, target, **kwargs):  # type: ignore[override]
            seen.append(kwargs)
            return super().backup(target, **kwargs)

    real_connect = sqlite3.connect

    def spy_connect(database, **kw):
        kw.setdefault("factory", _Spy)
        return real_connect(database, **kw)

    monkeypatch.setattr(bc.sqlite3, "connect", spy_connect)
    assert bc._copy_auth_file(str(src), str(tmp_path / "out" / "Cookies")) is True
    assert seen, "backup() was never called"
    assert seen[0].get("pages") == bc._BACKUP_PAGES_PER_STEP, (
        "unstepped backup cannot be interrupted")
    assert callable(seen[0].get("progress")), "no deadline callback on the backup"


def test_close_copy_dir_browser_only_targets_the_given_dir(monkeypatch):
    """Safety: it must reap Hermes' copy-dir browser, never the user's own."""
    asked = []

    def fake_holders(path):
        asked.append(path)
        return iter(())

    monkeypatch.setattr(bc, "_processes_holding_profile", fake_holders)
    assert bc._close_copy_dir_browser(r"C:\hermes\browser-profile\chrome") == 0
    assert asked == [r"C:\hermes\browser-profile\chrome"]
