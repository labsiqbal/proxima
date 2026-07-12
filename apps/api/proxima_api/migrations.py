"""Versioned database migrations.

The baseline schema (``SCHEMA`` + ``migrate_existing`` in ``db.py``) is applied
idempotently on every startup and covers simple additive column changes. This
module adds **versioned** migrations for anything beyond that (data backfills,
multi-step changes) with three guarantees:

- **Run once, in order** — each migration is recorded in ``schema_migrations``
  and never re-applied.
- **Backed up first** — before any pending migration runs, the database file is
  snapshotted to ``<db dir>/backups/`` via ``VACUUM INTO`` (a consistent
  single-file copy, WAL included). Existing data is never dropped.
- **Atomic** — each migration runs in its own transaction; a failure rolls back
  and leaves the recorded version unchanged.

To add a migration: append a ``(version, description, apply_fn)`` tuple to
``MIGRATIONS`` with the next integer version. Never edit or renumber an existing
entry. Prefer additive changes (``ADD COLUMN``, ``CREATE TABLE``).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .runner_specs import FALLBACK_RUNNER

# (version, human description, apply function)
Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]

def _add_messages_author(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "author" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN author TEXT")


def _add_profiles_runner_id(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "runner_id" not in cols:
        conn.execute(f"ALTER TABLE profiles ADD COLUMN runner_id TEXT NOT NULL DEFAULT '{FALLBACK_RUNNER}'")


def _add_messages_run_id(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "run_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN run_id INTEGER")


def _add_runs_kind(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "kind" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")


def _rename_private_projects_to_personal(conn: sqlite3.Connection) -> None:
    # The auto-provisioned personal project was labelled "<user> (private)", which
    # read like a sharing setting. Relabel it "<user> (personal)" so it clearly
    # reads as the user's own space. Visibility (the actual access control) is a
    # separate column and is untouched.
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
        return
    conn.execute(
        "UPDATE projects SET name = REPLACE(name, ' (private)', ' (personal)') WHERE name LIKE '% (private)'"
    )


# Ordered list of versioned migrations. Append future schema/data changes here.
def _add_profiles_instructions(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "instructions" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN instructions TEXT")


def _add_sessions_goal(conn: sqlite3.Connection) -> None:
    """Autonomous goal loop: a session can pursue a goal across many turns until
    the agent reports it done/blocked or the iteration cap is hit."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "goal_text" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN goal_text TEXT")
    if "goal_status" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN goal_status TEXT")
    if "goal_iteration" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN goal_iteration INTEGER NOT NULL DEFAULT 0")
    if "goal_max" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN goal_max INTEGER NOT NULL DEFAULT 20")


def _add_sessions_manual_title(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "manual_title" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN manual_title INTEGER NOT NULL DEFAULT 0")


def _drop_invites_table(conn: sqlite3.Connection) -> None:
    # The invites table was the multi-user account-creation surface. Single-user
    # mode closed those routes (they 404) and nothing reads/writes the table, so
    # it is dead weight. DROP IF EXISTS is a no-op on fresh installs.
    conn.execute("DROP TABLE IF EXISTS invites")


def _drop_project_members_table(conn: sqlite3.Connection) -> None:
    # project_members was legacy multi-user sharing plumbing. Single-user access
    # is now owner_user_id-scoped and nothing reads/writes membership rows.
    conn.execute("DROP TABLE IF EXISTS project_members")


def _add_message_reviews_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          mode TEXT NOT NULL DEFAULT 'validate',
          status TEXT NOT NULL DEFAULT 'queued',
          source_runner TEXT,
          source_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
          reviewer_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
          reviewer_profiles TEXT NOT NULL DEFAULT '[]',
          verdict TEXT,
          gaps TEXT NOT NULL DEFAULT '[]',
          depends_on_input TEXT NOT NULL DEFAULT '[]',
          revised_content TEXT,
          suggested_next_move TEXT,
          raw_transcript TEXT,
          merge_transcript TEXT,
          source_original_content TEXT,
          applied_at TEXT,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_message_reviews_source ON message_reviews(source_message_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_message_reviews_session ON message_reviews(session_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_message_reviews_run ON message_reviews(run_id)")


def _add_message_review_apply_fields(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_reviews)").fetchall()}
    if "merge_transcript" not in cols:
        conn.execute("ALTER TABLE message_reviews ADD COLUMN merge_transcript TEXT")
    if "source_original_content" not in cols:
        conn.execute("ALTER TABLE message_reviews ADD COLUMN source_original_content TEXT")
    if "applied_at" not in cols:
        conn.execute("ALTER TABLE message_reviews ADD COLUMN applied_at TEXT")


def _add_prompt_collaborations(conn: sqlite3.Connection) -> None:
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "collaboration_id" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN collaboration_id INTEGER")
    if "collaboration_role" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN collaboration_role TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_collaborations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          parent_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          prompt TEXT NOT NULL,
          profile_ids TEXT NOT NULL DEFAULT '[]',
          child_run_ids TEXT NOT NULL DEFAULT '[]',
          child_outputs TEXT NOT NULL DEFAULT '[]',
          synthesis_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          final_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_session ON prompt_collaborations(session_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_parent ON prompt_collaborations(parent_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_synthesis ON prompt_collaborations(synthesis_run_id)")


def _drop_sessions_acp_session_id(conn: sqlite3.Connection) -> None:
    # Dead single-value column. The authoritative store is the agent_sessions
    # table (one ACP session PER home), so this legacy column is never read or
    # written by live code — a stale value here would look authoritative to a
    # future reader. Drop it. (SQLite >= 3.35 supports DROP COLUMN.)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "acp_session_id" in cols:
        conn.execute("ALTER TABLE sessions DROP COLUMN acp_session_id")


MIGRATIONS: list[Migration] = [
    (1, "add messages.author (chat sender / agent name)", _add_messages_author),
    (2, "add profiles.runner_id", _add_profiles_runner_id),
    (3, "add messages.run_id (links assistant message to its run)", _add_messages_run_id),
    (4, "add runs.kind (chat | wiki_draft)", _add_runs_kind),
    (5, "relabel '<user> (private)' personal projects to '(personal)'", _rename_private_projects_to_personal),
    (6, "add profiles.instructions (per-profile agent instructions / soul)", _add_profiles_instructions),
    (7, "add sessions.goal_* (autonomous goal loop)", _add_sessions_goal),
    (8, "add sessions.manual_title (protect user-renamed chats from auto-title)", _add_sessions_manual_title),
    (9, "drop dead invites table (single-user: invite routes are 404)", _drop_invites_table),
    (10, "drop dead project_members table (single-user owner scope)", _drop_project_members_table),
    (11, "add message_reviews table (Validate sidecar reviews)", _add_message_reviews_table),
    (12, "add message review apply/merge fields", _add_message_review_apply_fields),
    (13, "add prompt collaborations for multi-agent modes", _add_prompt_collaborations),
    (14, "drop dead sessions.acp_session_id (agent_sessions is authoritative)", _drop_sessions_acp_session_id),
]


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT NOT NULL)"
    )


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_table(conn)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _backup(conn: sqlite3.Connection, db_path: str, from_v: int, to_v: int) -> Path | None:
    """Snapshot the DB before migrating. Returns the backup path (or None for an
    in-memory / not-yet-created DB, where there is nothing to back up)."""
    src = Path(db_path)
    if not src.exists():
        return None
    backups = src.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backups / f"{src.stem}.pre-migration-v{from_v}-to-v{to_v}-{stamp}.db"
    # VACUUM INTO writes a consistent single-file snapshot (folds in the WAL).
    conn.execute("VACUUM INTO ?", (str(target),))
    return target


def run_migrations(
    conn: sqlite3.Connection,
    db_path: str | None = None,
    migrations: list[Migration] | None = None,
) -> list[int]:
    """Apply pending migrations once each, in version order. Backs up the DB
    file (when ``db_path`` points to a real file) before applying anything.
    Returns the list of versions applied this call."""
    from .auth import iso_now

    migs = sorted(migrations if migrations is not None else MIGRATIONS, key=lambda m: m[0])
    cur = current_version(conn)
    pending = [m for m in migs if m[0] > cur]
    if not pending:
        return []

    if db_path:
        _backup(conn, db_path, cur, pending[-1][0])

    applied: list[int] = []
    for version, description, apply in pending:
        conn.execute("BEGIN")
        try:
            apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, iso_now()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
