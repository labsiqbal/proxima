"""Build a candidate-only runtime fixture from a migrated clone.

The raw clone is migration evidence only.  It must never be mounted into the
browser candidate because it contains real paths, profiles, sessions and data.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from .candidate_data import CandidateDataError, validate_migrated_clone


class FixtureError(RuntimeError):
    pass


_PRIVATE_TABLES = (
    "auth_sessions", "agent_sessions", "messages", "runs", "sessions", "projects",
    "profiles", "users", "project_areas", "job_worktrees", "artifacts", "artifact_records",
)


def assemble_fixture(migrated_clone: Path, fixture: Path, *, workspace: Path, runner_home: Path) -> Path:
    """Copy schema, purge sensitive rows, and add only candidate-local roots."""
    validate_migrated_clone(migrated_clone)
    if fixture.exists() or fixture.is_symlink() or workspace.exists() or runner_home.exists():
        raise FixtureError("candidate fixture paths must be new")
    fixture.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace.mkdir(mode=0o700, parents=True)
    runner_home.mkdir(mode=0o700, parents=True)
    shutil.copyfile(migrated_clone, fixture)
    conn = sqlite3.connect(fixture)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in _PRIVATE_TABLES:
            if table in tables:
                conn.execute(f'DELETE FROM "{table}"')
        if "users" in tables:
            conn.execute(
                "INSERT INTO users(username, os_user, role) VALUES (?, ?, ?)",
                ("candidate", "candidate", "owner"),
            )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        conn.execute("VACUUM")
    except sqlite3.Error as exc:
        raise FixtureError("candidate fixture assembly failed") from exc
    finally:
        conn.close()
    try:
        validate_migrated_clone(fixture)
    except CandidateDataError as exc:
        raise FixtureError(str(exc)) from exc
    return fixture
