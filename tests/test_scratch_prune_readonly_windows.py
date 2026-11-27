"""A finished git clone in scratch is removed whole on Windows, read-only objects included."""
import os
import stat

import pytest

from hermes_constants_scratch_stuck import rmtree_collecting_permission_errors

pytestmark = pytest.mark.platforms("windows")


def test_rmtree_removes_read_only_files(tmp_path):
    # git marks every object/pack read-only; Windows refuses to unlink those, so the prune left
    # 28 GB of half-removed clones (".git/objects" only) and reported them as undeletable.
    entry = tmp_path / "clone"
    pack = entry / ".git" / "objects" / "pack" / "pack-1.pack"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"x")
    os.chmod(pack, stat.S_IREAD)
    assert rmtree_collecting_permission_errors(entry) == []
    assert not entry.exists()
