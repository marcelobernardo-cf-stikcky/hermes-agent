"""A leftover venv built for another Python minor must not be spliced onto sys.path: its compiled
wheels (pydantic_core .cp311.pyd) shadow the interpreter's own and break every pydantic import."""
import sys

from gateway.run import _venv_matches_running_python


def _venv(tmp_path, version):
    (tmp_path / "pyvenv.cfg").write_text(f"home = x\nversion_info = {version}\n", encoding="utf-8")
    return tmp_path


def test_other_minor_is_rejected(tmp_path):
    other = f"{sys.version_info[0]}.{sys.version_info[1] - 1}"
    assert not _venv_matches_running_python(_venv(tmp_path, other))


def test_same_minor_is_accepted(tmp_path):
    same = f"{sys.version_info[0]}.{sys.version_info[1]}.2"
    assert _venv_matches_running_python(_venv(tmp_path, same))


def test_missing_cfg_is_accepted(tmp_path):
    assert _venv_matches_running_python(tmp_path)
