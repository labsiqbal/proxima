"""External maintenance-fence file contract.  Candidate releases never own it."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .durability import ensure_durable_directory, fsync_directory, write_all

FENCE_DIRECTORY_MODE = 0o755
FENCE_FILE_MODE = 0o644


def ingress_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.lock")


def ingress_pending_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.pending")


def prepare_ingress_lock(path: Path) -> Path:
    ensure_durable_directory(path.parent, FENCE_DIRECTORY_MODE)
    lock_path = ingress_lock_path(path)
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            FENCE_FILE_MODE,
        )
    except FileExistsError:
        if lock_path.is_symlink() or not lock_path.is_file():
            raise RuntimeError("maintenance ingress lock is invalid")
        return lock_path
    try:
        write_all(descriptor, b"\0")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)
    return lock_path


@contextmanager
def _exclusive_ingress(path: Path) -> Iterator[None]:
    lock_path = prepare_ingress_lock(path)
    descriptor = os.open(lock_path, os.O_RDWR)
    acquired = False
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            raise RuntimeError("maintenance ingress lock platform unsupported")
        acquired = True
        yield
    finally:
        if acquired and os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif acquired and os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def _write_pending(path: Path) -> Path:
    pending = ingress_pending_path(path)
    descriptor = os.open(
        pending,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        FENCE_FILE_MODE,
    )
    try:
        write_all(descriptor, b"pending\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)
    return pending


def write(path: Path, run_id: str, phase: str) -> None:
    prepare_ingress_lock(path)
    path.parent.chmod(FENCE_DIRECTORY_MODE)
    pending = _write_pending(path)
    payload = json.dumps(
        {"run_id": run_id, "phase": phase},
        sort_keys=True,
    ).encode("utf-8")
    with _exclusive_ingress(path):
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
        pending.unlink()
        fsync_directory(path.parent)


def remove(path: Path) -> None:
    """Durably remove a controller-owned fence after a committed outcome only."""
    with _exclusive_ingress(path):
        changed = False
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("maintenance fence is not a regular file")
            path.unlink()
            changed = True
        pending = ingress_pending_path(path)
        if pending.exists() or pending.is_symlink():
            if pending.is_symlink() or not pending.is_file():
                raise RuntimeError("maintenance ingress pending state is invalid")
            pending.unlink()
            changed = True
        if changed:
            fsync_directory(path.parent)
