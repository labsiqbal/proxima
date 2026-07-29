"""Build a fresh candidate-only database from migrated schema."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .candidate_data import CandidateDataError, validate_migrated_clone


class FixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class FixtureResult:
    path: Path
    auth_token: str
    session_id: int
    schema_version: int


_SCHEMA_TYPES = ("table", "index", "trigger", "view")


def _schema_statements(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
        "WHEN 'trigger' THEN 2 ELSE 3 END, name"
    ).fetchall()
    result: list[str] = []
    for kind, _name, sql in rows:
        if kind not in _SCHEMA_TYPES or not isinstance(sql, str) or not sql.strip():
            raise FixtureError("candidate schema contains an unsupported object")
        result.append(sql)
    if not result:
        raise FixtureError("candidate schema is empty")
    return result


def assemble_fixture(
    migrated_clone: Path,
    fixture: Path,
    *,
    workspace: Path,
    runner_home: Path,
    expected_version: int,
) -> FixtureResult:
    migrated = validate_migrated_clone(migrated_clone, expected_version)
    if fixture.exists() or fixture.is_symlink() or workspace.exists() or runner_home.exists():
        raise FixtureError("candidate fixture paths must be new")
    fixture.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace.mkdir(mode=0o700, parents=True)
    runner_home.mkdir(mode=0o700, parents=True)
    source = sqlite3.connect(f"file:{migrated_clone}?mode=ro", uri=True)
    destination = sqlite3.connect(fixture)
    token = secrets.token_urlsafe(32)
    try:
        statements = _schema_statements(source)
        for statement in statements:
            destination.execute(statement)
        destination.commit()
        destination.execute("PRAGMA foreign_keys=ON")
        ledger = source.execute(
            "SELECT version, description FROM schema_migrations ORDER BY version"
        ).fetchall()
        destination.executemany(
            "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
            [
                (int(version), str(description), "1970-01-01T00:00:00+00:00")
                for version, description in ledger
            ],
        )
        user = destination.execute(
            "INSERT INTO users(username, os_user, role, password_hash, password_set_at) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (
                "candidate",
                "candidate",
                "owner",
                "candidate-probe-password-is-not-a-live-secret",
                "1970-01-01T00:00:00+00:00",
            ),
        ).fetchone()
        if user is None:
            raise FixtureError("candidate owner could not be created")
        user_id = int(user[0])
        destination.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?, ?, NULL)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id),
        )
        project = destination.execute(
            "INSERT INTO projects(slug, name, path, owner_user_id, visibility) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            ("candidate", "Candidate", str(workspace), user_id, "private"),
        ).fetchone()
        if project is None:
            raise FixtureError("candidate project could not be created")
        session = destination.execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, mode) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            ("Candidate probe", int(project[0]), user_id, "chat"),
        ).fetchone()
        if session is None:
            raise FixtureError("candidate probe session could not be created")
        destination.commit()
        if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise FixtureError("candidate fixture foreign key check failed")
        destination.execute("VACUUM")
        session_id = int(session[0])
    except (sqlite3.Error, CandidateDataError) as exc:
        raise FixtureError("candidate fixture assembly failed") from exc
    finally:
        destination.close()
        source.close()
    try:
        validate_migrated_clone(fixture, expected_version)
    except CandidateDataError as exc:
        raise FixtureError(str(exc)) from exc
    return FixtureResult(fixture, token, session_id, migrated.schema_version)
