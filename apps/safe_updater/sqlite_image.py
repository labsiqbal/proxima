"""Trusted SQLite image operations used only by the disposable promotion fixture.

The controller never raw-copies an active WAL database.  Every image here is
validated, checkpointed, fsynced, and represented by one regular main file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .durability import ensure_durable_directory, fsync_directory


class SqliteImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class SealedImage:
    path: Path
    digest: str


def _regular(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise SqliteImageError("SQLite image is missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SqliteImageError("SQLite image must be a regular file")


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _validate(path: Path) -> None:
    _regular(path)
    try:
        connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise SqliteImageError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise SqliteImageError("SQLite foreign key check failed")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SqliteImageError("SQLite image validation failed") from exc


def checkpoint_truncate(database: Path) -> None:
    """Require a successful truncate checkpoint before a stopped-service backup."""
    _regular(database)
    try:
        connection = sqlite3.connect(f"file:{database}?mode=rw", uri=True)
        try:
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SqliteImageError("SQLite checkpoint failed") from exc
    if row is None or int(row[0]) != 0:
        raise SqliteImageError("SQLite truncate checkpoint is busy")
    wal = database.with_name(database.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise SqliteImageError("SQLite WAL survived truncate checkpoint")


def seal_backup(source: Path, destination: Path) -> SealedImage:
    """Create an independently validated, single-file backup through SQLite's API."""
    _regular(source)
    if destination.exists() or destination.is_symlink():
        raise SqliteImageError("sealed image destination already exists")
    ensure_durable_directory(destination.parent, 0o700)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    except sqlite3.Error as exc:
        raise SqliteImageError("SQLite backup failed") from exc
    finally:
        target_connection.close()
        source_connection.close()
    _validate(destination)
    descriptor = os.open(destination, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(destination.parent)
    return SealedImage(destination, _digest(destination))


def quarantine_sidecars(database: Path, recovery_directory: Path) -> tuple[Path, ...]:
    ensure_durable_directory(recovery_directory, 0o700)
    moved: list[Path] = []
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        _regular(sidecar)
        destination = recovery_directory / f"{database.name}{suffix}"
        if destination.exists() or destination.is_symlink():
            raise SqliteImageError("sidecar recovery destination exists")
        os.replace(sidecar, destination)
        moved.append(destination)
    fsync_directory(recovery_directory)
    fsync_directory(database.parent)
    return tuple(moved)


def replace_from_sealed(image: SealedImage, destination: Path) -> None:
    """Copy a sealed backup through a fresh inode and atomically replace the DB."""
    _regular(image.path)
    ensure_durable_directory(destination.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(name)
    try:
        with image.path.open("rb") as source, os.fdopen(descriptor, "wb", closefd=False) as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.close(descriptor)
        descriptor = -1
        _validate(temporary)
        if _digest(temporary) != image.digest:
            raise SqliteImageError("sealed image digest changed")
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
