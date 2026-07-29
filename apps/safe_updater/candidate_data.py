"""Consistent SQLite cloning and clone-only migration helpers."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .sandbox import CandidateSandbox, SandboxError


class CandidateDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloneReport:
    path: Path
    schema_version: int


def _validate(conn: sqlite3.Connection) -> int:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise CandidateDataError("candidate database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise CandidateDataError("candidate database foreign key check failed")
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def clone_live_database(live_database: Path, destination: Path) -> CloneReport:
    """Use SQLite's backup API, never a raw DB/WAL file copy."""
    if not live_database.is_file() or destination.exists() or destination.is_symlink():
        raise CandidateDataError("invalid candidate clone destination")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{live_database}?mode=ro", uri=True)
    clone = sqlite3.connect(destination)
    try:
        source.backup(clone)
        clone.commit()
        version = _validate(clone)
    except sqlite3.Error as exc:
        raise CandidateDataError("candidate database clone failed") from exc
    finally:
        clone.close()
        source.close()
    return CloneReport(destination, version)


def validate_migrated_clone(path: Path) -> CloneReport:
    if not path.is_file() or path.is_symlink():
        raise CandidateDataError("candidate migrated database is missing")
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
    try:
        return CloneReport(path, _validate(conn))
    finally:
        conn.close()


def migrate_clone_in_sandbox(
    clone: Path,
    sandbox: CandidateSandbox,
    argv: Sequence[str],
) -> bytes:
    """Run candidate migration only in the quota-limited, networkless sandbox."""
    if not argv or not clone.is_file() or clone.resolve() != sandbox.database.resolve():
        raise CandidateDataError("candidate migration target is invalid")
    try:
        completed = sandbox.run(argv, cwd=sandbox.release)
    except SandboxError as exc:
        raise CandidateDataError(str(exc)) from exc
    if completed.returncode:
        raise CandidateDataError("candidate migration subprocess failed")
    validate_migrated_clone(clone)
    return completed.stdout or b""
