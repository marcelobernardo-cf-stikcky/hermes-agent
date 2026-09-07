"""Regression coverage for native Windows LSP executable resolution."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.windows_only
def test_existing_binary_skips_legacy_posix_shim_and_reuses_npm_cmd(tmp_path, monkeypatch):
    """A stale lsp/bin shim must not trigger npm when node_modules has .cmd."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from agent.lsp import install as install_mod

    bin_dir = install_mod.hermes_lsp_bin_dir()
    (bin_dir / "pyright-langserver").write_text("#!/bin/sh\necho stale\n")
    npm_bin = bin_dir.parent / "node_modules" / ".bin"
    npm_bin.mkdir(parents=True, exist_ok=True)
    (npm_bin / "pyright-langserver").write_text("#!/bin/sh\necho shim\n")
    native = npm_bin / "pyright-langserver.cmd"
    native.write_text("@echo off\necho native\n")

    monkeypatch.setattr(install_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        install_mod,
        "_install_npm",
        lambda *_args, **_kwargs: pytest.fail("existing npm installation must be reused"),
    )

    assert install_mod._existing_binary("pyright-langserver") == str(native)
    assert install_mod._do_install("pyright") == str(native)


@pytest.mark.windows_only
def test_install_npm_returns_relative_cmd_wrapper_without_copying(tmp_path, monkeypatch):
    """Keep npm's .cmd beside its node_modules payload so relative paths work."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from agent.lsp import install as install_mod

    def fake_run(cmd, **kwargs):
        npm_bin = install_mod.hermes_lsp_bin_dir().parent / "node_modules" / ".bin"
        npm_bin.mkdir(parents=True, exist_ok=True)
        (npm_bin / "pyright-langserver").write_text("#!/bin/sh\necho shim\n")
        (npm_bin / "pyright-langserver.cmd").write_text("@echo off\necho native\n")
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(install_mod, "find_node_executable", lambda _name: "npm.cmd")
    monkeypatch.setattr(install_mod.subprocess, "run", fake_run)

    native = install_mod.hermes_lsp_bin_dir().parent / "node_modules" / ".bin" / "pyright-langserver.cmd"
    resolved = install_mod._install_npm("pyright", "pyright-langserver")

    assert resolved == str(native)
    assert not (install_mod.hermes_lsp_bin_dir() / native.name).exists()


@pytest.mark.windows_only
def test_link_into_bin_copies_when_symlink_is_unavailable(tmp_path, monkeypatch):
    """The existing Windows-safe copy fallback remains usable for native files."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from agent.lsp import install as install_mod

    target = tmp_path / "source.exe"
    target.write_bytes(b"native executable")

    def fail_symlink(_self, _target):
        raise OSError("symlink unavailable")

    monkeypatch.setattr(type(target), "symlink_to", fail_symlink)
    resolved = install_mod._link_into_bin(target)
    link = install_mod.hermes_lsp_bin_dir() / target.name

    assert resolved == str(link)
    assert link.read_bytes() == target.read_bytes()
