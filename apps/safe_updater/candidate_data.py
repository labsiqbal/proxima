"""Consistent SQLite cloning and mandatory clone-only migration."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .sandbox import CandidateSandbox, SandboxError


class CandidateDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloneReport:
    path: Path
    schema_version: int
    migration_versions: tuple[int, ...]


@dataclass(frozen=True)
class MigrationReport:
    clone: CloneReport
    output: bytes


_MIGRATION_SCRIPT = """
import json
import os
from proxima_api.db import connect
from proxima_api.migrations import MIGRATIONS, run_migrations
path = os.environ["PROXIMA_DB_PATH"]
connection = connect(path)
try:
    applied = run_migrations(connection, path)
    expected = max(entry[0] for entry in MIGRATIONS)
finally:
    connection.close()
print(json.dumps({"applied": applied, "candidate_expected_version": expected}, sort_keys=True))
""".strip()

MIGRATION_COMMAND = (".venv/bin/python", "-c", _MIGRATION_SCRIPT)


def _migration_versions(conn: sqlite3.Connection) -> tuple[int, ...]:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if table is None:
        return ()
    return tuple(
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    )


def _validate(conn: sqlite3.Connection, path: Path) -> CloneReport:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise CandidateDataError("candidate database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise CandidateDataError("candidate database foreign key check failed")
    versions = _migration_versions(conn)
    return CloneReport(path, versions[-1] if versions else 0, versions)


def clone_live_database(live_database: Path, destination: Path) -> CloneReport:
    if not live_database.is_file() or destination.exists() or destination.is_symlink():
        raise CandidateDataError("invalid candidate clone destination")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{live_database}?mode=ro", uri=True)
    clone = sqlite3.connect(destination)
    try:
        source.backup(clone)
        clone.commit()
        report = _validate(clone, destination)
    except sqlite3.Error as exc:
        raise CandidateDataError("candidate database clone failed") from exc
    finally:
        clone.close()
        source.close()
    return report


def validate_migrated_clone(path: Path, expected_version: int) -> CloneReport:
    if (
        not path.is_file()
        or path.is_symlink()
        or expected_version < 1
    ):
        raise CandidateDataError("candidate migrated database is missing")
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
    try:
        report = _validate(conn, path)
    except sqlite3.Error as exc:
        raise CandidateDataError("candidate migrated database validation failed") from exc
    finally:
        conn.close()
    expected = tuple(range(1, expected_version + 1))
    if report.migration_versions != expected:
        raise CandidateDataError("candidate migration ledger is incomplete")
    return report


def migrate_clone_in_sandbox(
    clone: Path,
    sandbox: CandidateSandbox,
    expected_version: int,
) -> MigrationReport:
    if (
        not clone.is_file()
        or clone.resolve() != sandbox.database.resolve()
        or clone.parent.resolve() == sandbox.release.resolve()
    ):
        raise CandidateDataError("candidate migration target is invalid")
    try:
        completed = sandbox.run(
            MIGRATION_COMMAND,
            cwd=sandbox.release / "apps" / "api",
            writable_paths=(clone.parent, sandbox.runner_home),
            read_only_paths=(sandbox.release,),
            timeout=300,
        )
    except SandboxError as exc:
        raise CandidateDataError(str(exc)) from exc
    if completed.returncode:
        raise CandidateDataError("candidate migration subprocess failed")
    output = completed.stdout or b""
    try:
        value = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateDataError("candidate migration report is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"applied", "candidate_expected_version"}
        or not isinstance(value["candidate_expected_version"], int)
        or isinstance(value["candidate_expected_version"], bool)
        or value["candidate_expected_version"] != expected_version
        or not isinstance(value["applied"], list)
        or any(
            not isinstance(version, int) or isinstance(version, bool) or version < 1
            for version in value["applied"]
        )
    ):
        raise CandidateDataError("candidate migration version differs from policy")
    report = validate_migrated_clone(clone, expected_version)
    return MigrationReport(report, output)
