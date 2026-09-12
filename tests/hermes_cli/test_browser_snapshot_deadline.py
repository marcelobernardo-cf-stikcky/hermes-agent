"""Regression: the real-profile snapshot cannot hang on a contended copy dir."""
from __future__ import annotations

import sqlite3

import pytest

from hermes_cli import browser_connect as bc


def test_backup_is_stepped_with_a_deadline_callback(tmp_path, monkeypatch):
    """`Connection.backup()` retries forever on SQLITE_BUSY, and only the `progress`
    callback can break that loop — but it fires once per STEP, so an unstepped
    `backup(out)` runs to completion inside one C call and no deadline is reachable.
    Both halves (a `pages` step size AND a callable `progress`) are the contract.

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
    assert isinstance(seen[0].get("pages"), int) and seen[0]["pages"] > 0, (
        "unstepped backup cannot be interrupted")
    progress = seen[0].get("progress")
    assert callable(progress), "no deadline callback on the backup"
    # The callback is the deadline: past its budget it must raise out of backup().
    monkeypatch.setattr(bc.time, "monotonic", lambda: float("inf"))
    with pytest.raises(TimeoutError):
        progress(0, 5, 10)


def test_close_copy_dir_browser_only_targets_the_given_dir(monkeypatch):
    """Safety: it must reap Hermes' copy-dir browser, never the user's own."""
    asked = []

    def fake_holders(path):
        asked.append(path)
        return iter(())

    monkeypatch.setattr(bc, "_processes_holding_profile", fake_holders)
    assert bc._close_copy_dir_browser(r"C:\hermes\browser-profile\chrome") == 0
    assert asked == [r"C:\hermes\browser-profile\chrome"]
