"""External maintenance-fence file contract.  Candidate releases never own it."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .durability import ensure_durable_directory, fsync_directory, write_all

FENCE_DIRECTORY_MODE = 0o755
FENCE_FILE_MODE = 0o644


def write(path: Path, run_id: str, phase: str) -> None:
    ensure_durable_directory(path.parent, FENCE_DIRECTORY_MODE)
    path.parent.chmod(FENCE_DIRECTORY_MODE)
    payload = json.dumps(
        {"run_id": run_id, "phase": phase},
        sort_keys=True,
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, FENCE_FILE_MODE)
            else:
                temporary.chmod(FENCE_FILE_MODE)
            write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove(path: Path) -> None:
    """Durably remove a controller-owned fence after a committed outcome only."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("maintenance fence is not a regular file")
    path.unlink()
    fsync_directory(path.parent)
