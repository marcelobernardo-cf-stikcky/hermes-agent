"""The fork's closed-app updater (update-when-closed.bat + .ps1), run through real cmd.exe.

The first live run died at "[1/4]" because cmd.exe ate PowerShell's ``%`` alias inside a
-Command string; only the refusal paths had been tested. This runs the whole .bat with the
mutating steps defused, so a cmd/PowerShell quoting break fails here, not on the user's box.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "scripts" / "desktop-update"
POWERSHELL = shutil.which("powershell")

# Real step -> inert echo. Each must match, or the rehearsal would run a real step.
DEFUSE = {
    "-Op StopServeLoop": "-Op StopServeLoop -DryRun",
    "-Op CloseHermes -GraceSeconds %GRACE_SECONDS%": "-Op CloseHermes -GraceSeconds %GRACE_SECONDS% -DryRun",
    'git -C "%INSTALL_ROOT%" tag -f': "echo DRY tag",
    'git -C "%INSTALL_ROOT%" merge --ff-only': "echo DRY merge",
    '"%HERMES_CLI%" update': "echo DRY updater",
    'start "" "%DESKTOP_EXE%"': "echo DRY start desktop",
    'start "" wscript.exe': "echo DRY restart serve-loop",
    "timeout /t 20 >nul": "rem",
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.mark.platforms("windows")
def test_closed_app_bat_reaches_every_step(tmp_path: Path) -> None:
    if not POWERSHELL:
        pytest.skip("PowerShell is not installed")
    install = tmp_path / "hermes" / "hermes-agent"
    install.mkdir(parents=True)
    _git(install, "init", "-q", "-b", "local/delegated-kanban-guard")
    _git(install, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
    _git(install, "branch", "prepared")
    _git(install, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "live")
    _git(install, "checkout", "-q", "prepared")
    _git(install, "merge", "-q", "local/delegated-kanban-guard")  # prepared = live + 0, a fast-forward
    _git(install, "checkout", "-q", "local/delegated-kanban-guard")
    exe = install / "apps" / "desktop" / "release" / "win-unpacked" / "Hermes.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")  # the relaunch is guarded by `if exist`
    (install.parent / "bin").mkdir()
    (install.parent / "bin" / "hermes.exe").write_bytes(b"")  # so is the CLI

    bat = (SRC / "update-when-closed.bat").read_bytes().decode()
    assert "\n" not in bat.replace("\r\n", ""), "cmd.exe needs CRLF line endings"
    for real, inert in DEFUSE.items():
        assert real in bat, f"step drifted, rehearsal would run it for real: {real}"
        bat = bat.replace(real, inert)
    shutil.copy(SRC / "update-when-closed.ps1", tmp_path)
    (tmp_path / "rehearsal.bat").write_bytes(bat.encode())

    out = subprocess.run(
        ["cmd.exe", "/d", "/c", str(tmp_path / "rehearsal.bat"), "prepared"],
        input="\n", capture_output=True, text=True, encoding="cp850", errors="replace", timeout=120,
        env={**os.environ, "HERMES_HOME": str(install.parent)},
    )
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    assert "[ERRO]" not in text and "ParserError" not in text, text
    for marker in ("[1/4]", "fecharia:", "[2/4]", "DRY merge", "[3/4]", "DRY updater", "[4/4]", "DRY start desktop"):
        assert marker in text, f"{marker!r} missing:\n{text}"


@pytest.mark.platforms("windows")
def test_closed_app_bat_refuses_a_non_fast_forward(tmp_path: Path) -> None:
    install = tmp_path / "hermes" / "hermes-agent"
    install.mkdir(parents=True)
    _git(install, "init", "-q", "-b", "local/delegated-kanban-guard")
    _git(install, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "live")
    _git(install, "checkout", "-q", "--orphan", "other")
    _git(install, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "other")
    _git(install, "checkout", "-q", "local/delegated-kanban-guard")
    (install.parent / "bin").mkdir()
    (install.parent / "bin" / "hermes.exe").write_bytes(b"")
    shutil.copy(SRC / "update-when-closed.bat", tmp_path)
    shutil.copy(SRC / "update-when-closed.ps1", tmp_path)

    out = subprocess.run(
        ["cmd.exe", "/d", "/c", str(tmp_path / "update-when-closed.bat"), "other"],
        input="\n", capture_output=True, text=True, encoding="cp850", errors="replace", timeout=60,
        env={**os.environ, "HERMES_HOME": str(install.parent)},
    )
    assert out.returncode == 3, out.stdout
    assert "nao e fast-forward" in out.stdout
