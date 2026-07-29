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

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner_specs import FALLBACK_RUNNER

# (version, human description, apply function[, opts]).
# opts is an optional 4th element, e.g. {"no_auto_tx": True} for a migration that
# manages its own transaction (a table rebuild needing PRAGMA foreign_keys=OFF).
Migration = tuple

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


def _add_messages_run_id_fk(conn: sqlite3.Connection) -> None:
    """Rebuild `messages` so run_id becomes a real FK -> runs(id) ON DELETE SET NULL
    (it was a bare INTEGER that could dangle a deleted run). SQLite can't ALTER ADD
    CONSTRAINT, so recreate + copy using the create-new/copy/drop-old/rename-new
    order with foreign_keys OFF (outside a txn — this migration is no_auto_tx), which
    preserves the inbound FKs from message_reviews / prompt_collaborations and never
    fires a cascade. Idempotent: skips if run_id already has an FK."""
    if any(r[3] == "run_id" for r in conn.execute("PRAGMA foreign_key_list(messages)").fetchall()):
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if not {"id", "session_id", "role", "content", "author", "run_id", "output_links", "created_at"}.issubset(cols):
        return  # not the full production shape yet (e.g. a minimal test fixture)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            CREATE TABLE _messages_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              author TEXT,
              run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
              output_links TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO _messages_new(id, session_id, role, content, author, run_id, output_links, created_at) "
            "SELECT id, session_id, role, content, author, run_id, output_links, created_at FROM messages"
        )
        conn.execute("DROP TABLE messages")
        conn.execute("ALTER TABLE _messages_new RENAME TO messages")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"messages FK rebuild introduced violations: {[tuple(v) for v in violations]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.execute("PRAGMA foreign_keys=ON")


def _add_sessions_pointer_fks(conn: sqlite3.Connection) -> None:
    """Rebuild `sessions` so task_id/job_id/workflow_id become real FKs (ON DELETE
    SET NULL) instead of bare INTEGERs that dangle at a deleted task/job/workflow.
    Dangling values that already exist are nulled first (that's the whole point —
    they could dangle before), then the FK is enforced. Same safe rebuild order as
    migration 15. Idempotent + guarded against minimal fixtures."""
    if any(r[3] == "task_id" for r in conn.execute("PRAGMA foreign_key_list(sessions)").fetchall()):
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    full = {
        "id", "title", "project_id", "owner_user_id", "profile_id", "runner_id", "visibility",
        "mode", "task_id", "job_id", "workflow_id", "manual_title", "created_at", "updated_at",
        "produced_artifacts", "goal_text", "goal_status", "goal_iteration", "goal_max",
    }
    if not full.issubset(cols):
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        # Null pre-existing dangling pointers so the new FK doesn't reject real data.
        conn.execute("UPDATE sessions SET task_id = NULL WHERE task_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.id = sessions.task_id)")
        conn.execute("UPDATE sessions SET job_id = NULL WHERE job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.id = sessions.job_id)")
        conn.execute("UPDATE sessions SET workflow_id = NULL WHERE workflow_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM workflows WHERE workflows.id = sessions.workflow_id)")
        conn.execute(
            f"""
            CREATE TABLE _sessions_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
              owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
              runner_id TEXT NOT NULL DEFAULT '{FALLBACK_RUNNER}',
              visibility TEXT NOT NULL DEFAULT 'private',
              mode TEXT NOT NULL DEFAULT 'chat',
              task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
              job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
              workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
              manual_title INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              produced_artifacts TEXT NOT NULL DEFAULT '[]',
              goal_text TEXT,
              goal_status TEXT,
              goal_iteration INTEGER NOT NULL DEFAULT 0,
              goal_max INTEGER NOT NULL DEFAULT 20
            )
            """
        )
        _scols = ("id, title, project_id, owner_user_id, profile_id, runner_id, visibility, mode, "
                  "task_id, job_id, workflow_id, manual_title, created_at, updated_at, produced_artifacts, "
                  "goal_text, goal_status, goal_iteration, goal_max")
        conn.execute(f"INSERT INTO _sessions_new({_scols}) SELECT {_scols} FROM sessions")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE _sessions_new RENAME TO sessions")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, updated_at)")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"sessions FK rebuild introduced violations: {[tuple(v) for v in violations]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.execute("PRAGMA foreign_keys=ON")


def _drop_tasks_feature(conn: sqlite3.Connection) -> None:
    """Merge tasks into jobs: rebuild sessions WITHOUT the task_id column/FK, then
    drop the tasks table. Same safe rebuild order + foreign_keys OFF as migration 16
    (keeps the job_id/workflow_id FKs, recreates indexes, asserts fk_check clean).
    Idempotent: skips once task_id is gone; guarded against minimal fixtures."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "task_id" not in cols:
        conn.execute("DROP TABLE IF EXISTS tasks")
        return
    keep = {
        "id", "title", "project_id", "owner_user_id", "profile_id", "runner_id", "visibility",
        "mode", "job_id", "workflow_id", "manual_title", "created_at", "updated_at",
        "produced_artifacts", "goal_text", "goal_status", "goal_iteration", "goal_max",
    }
    if not keep.issubset(cols):
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            f"""
            CREATE TABLE _sessions_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
              owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
              runner_id TEXT NOT NULL DEFAULT '{FALLBACK_RUNNER}',
              visibility TEXT NOT NULL DEFAULT 'private',
              mode TEXT NOT NULL DEFAULT 'chat',
              job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
              workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
              manual_title INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              produced_artifacts TEXT NOT NULL DEFAULT '[]',
              goal_text TEXT,
              goal_status TEXT,
              goal_iteration INTEGER NOT NULL DEFAULT 0,
              goal_max INTEGER NOT NULL DEFAULT 20
            )
            """
        )
        _c = ("id, title, project_id, owner_user_id, profile_id, runner_id, visibility, mode, "
              "job_id, workflow_id, manual_title, created_at, updated_at, produced_artifacts, "
              "goal_text, goal_status, goal_iteration, goal_max")
        conn.execute(f"INSERT INTO _sessions_new({_c}) SELECT {_c} FROM sessions")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE _sessions_new RENAME TO sessions")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, updated_at)")
        conn.execute("DROP TABLE IF EXISTS tasks")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"drop-tasks rebuild introduced violations: {[tuple(v) for v in violations]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.execute("PRAGMA foreign_keys=ON")


def _add_project_areas(conn: sqlite3.Connection) -> None:
    """Container model, Phase-1 slice 1 (T1): create `project_areas` and wrap
    every existing flat project in place as a work container.

    Schema shape - a table of rows, not a JSON column on projects, because
    areas are individually addressable: the manual-override API adds/removes
    one area at a time, `UNIQUE(project_id, kind, rel_path)` gives duplicate
    protection and the partial unique index enforces exactly-one-ops in the DB
    rather than in code, and later slices (worktree-per-repo-job, the slicer's
    job→target binding) can reference an area row by id with FK integrity.
    `ON DELETE CASCADE` keeps areas from outliving their project.

    Migration behavior (the spec's Migration note, binding): the existing
    `projects.path` folder becomes the container root; if it is itself a git
    repo it registers as the sole code area (`.`); the conventional
    artifacts/ reports/ exports/ wiki/ subdirs continue as the ops area
    (rel_path `.`). No file moves; a project with no detected repo simply has
    zero code areas. A path that is missing on this machine detects nothing
    and can be re-detected on demand later.
    """
    from .project_areas import ensure_ops_area, sync_code_areas

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_areas (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL DEFAULT 'code',
          rel_path TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'auto',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(project_id, kind, rel_path)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_areas_project ON project_areas(project_id, kind)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_project_areas_one_ops ON project_areas(project_id) WHERE kind = 'ops'")
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if not {"id", "path"}.issubset(cols):
        return  # minimal test fixture, nothing to wrap
    for row in conn.execute("SELECT id, path FROM projects").fetchall():
        ensure_ops_area(conn, row["id"], rel_path=".")
        sync_code_areas(conn, row["id"], row["path"], validate=False)


def _add_repo_job_worktrees(conn: sqlite3.Connection) -> None:
    """Worktree machinery for repo jobs, Phase-1 slice 2 (T1): bind a job to
    its target container area and track its isolated worktree.

    Two additive pieces, both inert until ``feature_repo_worktrees`` is on:

    - ``jobs.target_area_id`` - the ONE area (T1: exactly one target) the job
      works against, set before it runs. Pointing at a code area is what makes
      it a repo job; ops-target and NULL-target jobs behave exactly as today.
      ``ON DELETE SET NULL`` so removing an area never breaks job history.
    - ``job_worktrees`` - one row per repo job recording where its branch was
      cut from (repo_path/base_branch/base_commit), where the agent works
      (worktree_path - outside the container, under
      ``<workspace_root>/worktrees/``), and the merge lifecycle
      (active/merging/merged/conflict/discarded). A table, not job columns,
      because the lifecycle is its own state machine with its own guarded
      transitions, and slices 4-5 (review UI, continuation) read it as a unit.
      ``UNIQUE(job_id)`` pins one worktree per job; ``ON DELETE CASCADE``
      keeps rows from outliving their job (disk cleanup happens in the job
      delete path, keyed by job id, so crash leftovers are removable even
      without the row).
    """
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "target_area_id" not in cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN target_area_id INTEGER REFERENCES project_areas(id) ON DELETE SET NULL"
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_worktrees (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
          area_id INTEGER REFERENCES project_areas(id) ON DELETE SET NULL,
          repo_path TEXT NOT NULL,
          worktree_path TEXT NOT NULL,
          branch TEXT NOT NULL,
          base_branch TEXT NOT NULL,
          base_commit TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          merge_commit TEXT,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_worktrees_status ON job_worktrees(status)")


def _add_jobs_rejected_reason(conn: sqlite3.Connection) -> None:
    """Reject path for the review surface, Phase-1 slice 4 (T1): rejecting a
    job at review marks it failed and must leave a durable why. A job column
    (not an event) because it is the job's terminal review verdict - the
    Tasks screen and slice 12's satpam read it straight off the job row."""
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "rejected_reason" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN rejected_reason TEXT")


def _add_runs_continuation(conn: sqlite3.Connection) -> None:
    """Timeout auto-continuation chain, Phase-1 slice 5 (T5): a job run that hits
    the per-turn quota enqueues a continuation run instead of only failing.

    Two additive ``runs`` columns:

    - ``continued_from_run_id`` - the timed-out run this run resumes; the chain
      is the durable trace slice 12's satpam reads (repeated continuations =
      confused-agent signal).
    - ``continuation_count`` - this run's ordinal in its chain (0 = original
      turn). The timeout handler stops continuing when it reaches
      ``run_continuation_limit`` and fails the job loudly instead.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "continued_from_run_id" not in cols:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN continued_from_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL"
        )
    if "continuation_count" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN continuation_count INTEGER NOT NULL DEFAULT 0")


def _add_script_trust(conn: sqlite3.Connection) -> None:
    """Hash-bound script approvals, Phase-1 slice 6 (T6): a deterministic
    script step runs only after the owner approved its exact content once.
    The approved sha256 per (project, script) lives here; a content change
    means a hash mismatch and the next run blocks for re-approval. Mirrors
    the CREATE TABLE in db.py so fresh installs and migrated ones agree."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS script_trust (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          rel_path TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(project_id, rel_path)
        )
        """
    )


def _add_artifact_registry(conn: sqlite3.Connection) -> None:
    """Durable deliverable registry, Phase-1 slice 8 (T4): create
    `artifact_records` and seed it from the current scanner output.

    Registry, not scanner (captain-ratified decision 1): the mtime scan
    discovers files but forgets them; this table is the durable record - one
    row per deliverable version with lineage (session -> job/node -> run),
    the single approval status both doors write, and the version chain.
    Mirrors the CREATE TABLE in db.py so fresh installs and migrated ones
    agree.

    Seed behavior: every existing project's scanner output (uncapped-ish,
    cap=1000) becomes v1 draft records with produced_at from file mtime, so
    upgrading owners see their existing artifacts as records immediately. A
    project path missing on this machine seeds nothing and loses nothing -
    records appear when runs produce new output.
    """
    from .artifact_registry import seed_project
    from .container_registry import ops_root

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          slug TEXT NOT NULL,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          path TEXT NOT NULL,
          size INTEGER,
          status TEXT NOT NULL DEFAULT 'draft',
          approved_at TEXT,
          version INTEGER NOT NULL DEFAULT 1,
          superseded_by INTEGER REFERENCES artifact_records(id) ON DELETE SET NULL,
          session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
          job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
          node_id TEXT,
          run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          file_missing INTEGER NOT NULL DEFAULT 0,
          produced_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(project_id, slug)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_records_project ON artifact_records(project_id, produced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_records_identity ON artifact_records(project_id, path)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_records_job ON artifact_records(job_id)")
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if not {"id", "path"}.issubset(cols):
        return  # minimal test fixture, nothing to seed
    for prow in conn.execute("SELECT id, path FROM projects").fetchall():
        if not prow["path"]:
            continue
        try:
            seed_project(conn, int(prow["id"]), ops_root(conn, prow))
        except Exception:
            # Best-effort per project: an unreadable path must not block the
            # upgrade; the registry fills in as new runs produce output.
            import logging

            logging.getLogger("proxima.migrations").exception(
                "artifact registry seed failed for project %s (non-fatal)", prow["id"]
            )


def _add_repo_remote_push(conn: sqlite3.Connection) -> None:
    """BYO repo-remote connector, Phase-1 slice 11 (T9): per-area
    push-after-merge opt-in + the push outcome on the job's worktree row.

    - ``project_areas.push_on_merge`` - the per-code-area toggle, DEFAULT OFF
      (T9: local-only stays the posture; a remote-less area never even offers
      the toggle). An area column, not app config, because the decision is
      per repo and the approve paths read it with the area row they already
      have.
    - ``job_worktrees.push_status/push_error/push_remote/push_remote_url`` -
      the outcome of the one push a merged repo job may make. On the worktree
      row (not the job) because push is a step of the same merge lifecycle
      the row already tracks, and the review UI reads that row as a unit.
      ``push_error`` keeps the exact failing command + output for the
      job-level blocker card; a failed push never un-merges (the job row is
      untouched - done stays done).
    """
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_areas'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(project_areas)").fetchall()}
        if "push_on_merge" not in cols:
            conn.execute("ALTER TABLE project_areas ADD COLUMN push_on_merge INTEGER NOT NULL DEFAULT 0")
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_worktrees'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_worktrees)").fetchall()}
        for col in ("push_status", "push_error", "push_remote", "push_remote_url"):
            if col not in cols:
                conn.execute(f"ALTER TABLE job_worktrees ADD COLUMN {col} TEXT")


def _add_satpam_supervision(conn: sqlite3.Connection) -> None:
    """Satpam supervision loop, Phase-1 slice 12 (T10): one fleet-level watchman
    over all running jobs, reading durable signals only.

    - ``satpam_watch`` - the watchman's per-chain memory: last continuation turn
      evaluated, the progress fingerprints it compares turn to turn (worktree
      diff signature, salvaged-output hash), consecutive no-progress counters,
      and a pending steer note for the next continuation turn.
    - ``satpam_interventions`` - the owner-visible record of every action
      (steer / restart / escalate; no silent interventions), including the
      'pending' repo-job restart that waits for in-app approval.
    - ``node_states.question/answer`` - decision-hold: a node whose agent
      surfaced a genuine open decision parks in review with the question; the
      owner's answer is injected into the node's re-run.
    - ``node_states.contract_failures`` - output-contract validation failures
      across attempts; repeated failure is a 'confused' escalation signal.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS satpam_watch (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          node_id TEXT,
          last_turn INTEGER NOT NULL DEFAULT 0,
          diff_signature TEXT,
          stall_turns INTEGER NOT NULL DEFAULT 0,
          output_signature TEXT,
          loop_turns INTEGER NOT NULL DEFAULT 0,
          steer_count INTEGER NOT NULL DEFAULT 0,
          steer_pending TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS satpam_interventions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          node_id TEXT,
          action TEXT NOT NULL,
          detection TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'applied',
          reason TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_satpam_interventions_job ON satpam_interventions(job_id, id)"
    )
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_states'").fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(node_states)").fetchall()}
        if "question" not in cols:
            conn.execute("ALTER TABLE node_states ADD COLUMN question TEXT")
        if "answer" not in cols:
            conn.execute("ALTER TABLE node_states ADD COLUMN answer TEXT")
        if "contract_failures" not in cols:
            conn.execute("ALTER TABLE node_states ADD COLUMN contract_failures INTEGER NOT NULL DEFAULT 0")


def _add_alpha_foundation(conn: sqlite3.Connection) -> None:
    """Alpha system identity, job ownership, scoped checkpoints, turn journals,
    and durable attention items. All additions are nullable/new-table changes."""
    table_names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "profiles" in table_names:
        profile_cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "system_kind" not in profile_cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN system_kind TEXT")
    if "jobs" in table_names:
        job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if (
            "alpha_session_id" not in job_cols
            and "origin_master_session_id" not in job_cols
        ):
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN alpha_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL"
            )
            job_cols.add("alpha_session_id")
        if {"alpha_session_id", "status", "created_at"}.issubset(job_cols):
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_alpha ON jobs(alpha_session_id, status, created_at)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS job_checkpoints ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, "
            "payload_json TEXT NOT NULL, git_refs_json TEXT NOT NULL DEFAULT '[]', "
            "pinned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_checkpoints_job ON job_checkpoints(job_id, created_at DESC)")
    if {"messages", "sessions"}.issubset(table_names):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS turn_file_journals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE, "
            "session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, "
            "entries_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turn_file_journals_session ON turn_file_journals(session_id, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attention_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL, "
        "target_json TEXT NOT NULL DEFAULT '{}', inline_ok INTEGER NOT NULL DEFAULT 0, "
        "actions_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open', "
        "source_key TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attention_status ON attention_items(status, created_at DESC)")


def _move_workflow_inputs_to_trigger(conn: sqlite3.Connection) -> None:
    """Backfill legacy graph-template inputs into their entry node.

    The ``workflows.inputs`` column remains for old clients and RunModal, but new
    authoring reads and writes the same declaration on the trigger. Graphs without
    a trigger receive a no-op trigger connected to every former root so their
    execution order and existing placeholders remain intact.
    """
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "workflows" not in tables:
        return
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()
    }
    if not {"id", "graph", "inputs"}.issubset(columns):
        return
    rows = conn.execute(
        "SELECT id, graph, inputs FROM workflows WHERE graph IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            graph = json.loads(row["graph"])
            inputs = json.loads(row["inputs"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            continue
        if not isinstance(inputs, list):
            inputs = []
        nodes = graph["nodes"]
        trigger: dict[str, Any] | None = next(
            (
                node
                for node in nodes
                if isinstance(node, dict) and node.get("type") == "trigger"
            ),
            None,
        )
        if trigger is None and inputs:
            node_ids = {
                str(node.get("id"))
                for node in nodes
                if isinstance(node, dict) and node.get("id") is not None
            }
            trigger_id = "trigger"
            suffix = 2
            while trigger_id in node_ids:
                trigger_id = f"trigger-{suffix}"
                suffix += 1
            edges = graph.get("edges")
            if not isinstance(edges, list):
                edges = []
            incoming = {
                str(edge.get("to", edge.get("target")))
                for edge in edges
                if isinstance(edge, dict)
            }
            roots = [
                str(node["id"])
                for node in nodes
                if isinstance(node, dict)
                and node.get("id") is not None
                and str(node["id"]) not in incoming
                and not node.get("depends_on")
            ]
            trigger = {
                "id": trigger_id,
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "instruction": "",
                "output_kind": "json",
            }
            nodes.insert(0, trigger)
            graph["edges"] = [
                {"from": trigger_id, "to": root} for root in roots
            ] + edges
        if trigger is None or "inputs" in trigger:
            continue
        trigger["inputs"] = inputs
        conn.execute(
            "UPDATE workflows SET graph = ? WHERE id = ?",
            (json.dumps(graph, ensure_ascii=False), row["id"]),
        )


def _add_container_foundation(conn: sqlite3.Connection) -> None:
    """Container registry and resumable physical Ops migration state."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS container_registry (
          container_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
          identity_label TEXT,
          summary TEXT,
          source_hash TEXT,
          indexed_at TEXT,
          last_activity_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_container_registry_activity "
        "ON container_registry(last_activity_at DESC, container_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS container_ops_migrations (
          container_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
          migration_version INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          manifest_json TEXT,
          manifest_hash TEXT,
          last_error TEXT,
          started_at TEXT,
          completed_at TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_container_ops_migrations_status "
        "ON container_ops_migrations(status, updated_at)"
    )


def _add_task_delegation_contracts(conn: sqlite3.Connection) -> None:
    """Durable, idempotent one-Area Task delegation and dependency DAG edges."""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required_tables = {
        "users",
        "projects",
        "project_areas",
        "sessions",
        "messages",
        "jobs",
    }
    if not required_tables.issubset(tables):
        return
    job_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "blocked_reason" not in job_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN blocked_reason TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_delegations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          origin_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
          origin_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
          container_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
          target_area_id INTEGER NOT NULL REFERENCES project_areas(id) ON DELETE RESTRICT,
          job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
          routing_mode TEXT NOT NULL CHECK (routing_mode IN ('explicit', 'auto')),
          routing_reason TEXT,
          created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          idempotency_key TEXT NOT NULL,
          idempotency_identity TEXT NOT NULL UNIQUE,
          request_fingerprint TEXT NOT NULL,
          start_requested INTEGER NOT NULL DEFAULT 0 CHECK (start_requested IN (0, 1)),
          start_state TEXT NOT NULL DEFAULT 'pending'
            CHECK (start_state IN ('pending', 'blocked', 'starting', 'started', 'failed')),
          blocked_reason TEXT,
          last_start_error TEXT,
          start_attempts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          start_attempted_at TEXT,
          started_at TEXT,
          UNIQUE(created_by, idempotency_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_delegations_origin "
        "ON task_delegations(origin_session_id, origin_message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_delegations_container "
        "ON task_delegations(container_id, target_area_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_delegations_start "
        "ON task_delegations(start_requested, start_state, updated_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_dependencies (
          task_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          depends_on_task_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          required_status TEXT NOT NULL DEFAULT 'done'
            CHECK (required_status IN ('review', 'done')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (task_id, depends_on_task_id),
          CHECK (task_id != depends_on_task_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_dependencies_prerequisite "
        "ON task_dependencies(depends_on_task_id, task_id)"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS task_dependencies_no_cycle
        BEFORE INSERT ON task_dependencies
        BEGIN
          SELECT RAISE(ABORT, 'task dependency cycle')
          WHERE EXISTS (
            WITH RECURSIVE prerequisites(task_id) AS (
              SELECT NEW.depends_on_task_id
              UNION
              SELECT dependency.depends_on_task_id
              FROM task_dependencies AS dependency
              JOIN prerequisites
                ON dependency.task_id = prerequisites.task_id
            )
            SELECT 1 FROM prerequisites WHERE task_id = NEW.task_id
          );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS task_dependencies_no_cycle_update
        BEFORE UPDATE OF task_id, depends_on_task_id ON task_dependencies
        BEGIN
          SELECT RAISE(ABORT, 'task dependency cycle')
          WHERE NEW.task_id = NEW.depends_on_task_id OR EXISTS (
            WITH RECURSIVE prerequisites(task_id) AS (
              SELECT NEW.depends_on_task_id
              UNION
              SELECT dependency.depends_on_task_id
              FROM task_dependencies AS dependency
              JOIN prerequisites
                ON dependency.task_id = prerequisites.task_id
              WHERE NOT (
                dependency.task_id = OLD.task_id
                AND dependency.depends_on_task_id = OLD.depends_on_task_id
              )
            )
            SELECT 1 FROM prerequisites WHERE task_id = NEW.task_id
          );
        END
        """
    )


def _protect_task_prerequisites_from_deletion(
    conn: sqlite3.Connection,
) -> None:
    """Rebuild dependency edges so a prerequisite cannot disappear silently.

    ``task_id`` remains cascading because deleting a dependent Task should
    remove its outgoing requirements. ``depends_on_task_id`` is restrictive:
    callers must remove dependents first, preserving the durable explanation
    for why a queued Task may or may not start.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'task_dependencies'"
    ).fetchone():
        return
    prerequisite_fk = next(
        (
            row
            for row in conn.execute(
                "PRAGMA foreign_key_list(task_dependencies)"
            ).fetchall()
            if row[3] == "depends_on_task_id"
        ),
        None,
    )
    if prerequisite_fk is not None and str(prerequisite_fk[6]).upper() in {
        "RESTRICT",
        "NO ACTION",
    }:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            CREATE TABLE _task_dependencies_new (
              task_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              depends_on_task_id INTEGER NOT NULL
                REFERENCES jobs(id) ON DELETE RESTRICT,
              required_status TEXT NOT NULL DEFAULT 'done'
                CHECK (required_status IN ('review', 'done')),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (task_id, depends_on_task_id),
              CHECK (task_id != depends_on_task_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO _task_dependencies_new("
            "task_id, depends_on_task_id, required_status, created_at, updated_at"
            ") SELECT task_id, depends_on_task_id, required_status, "
            "created_at, updated_at FROM task_dependencies"
        )
        conn.execute("DROP TABLE task_dependencies")
        conn.execute(
            "ALTER TABLE _task_dependencies_new RENAME TO task_dependencies"
        )
        conn.execute(
            "CREATE INDEX idx_task_dependencies_prerequisite "
            "ON task_dependencies(depends_on_task_id, task_id)"
        )
        conn.execute(
            """
            CREATE TRIGGER task_dependencies_no_cycle
            BEFORE INSERT ON task_dependencies
            BEGIN
              SELECT RAISE(ABORT, 'task dependency cycle')
              WHERE EXISTS (
                WITH RECURSIVE prerequisites(task_id) AS (
                  SELECT NEW.depends_on_task_id
                  UNION
                  SELECT dependency.depends_on_task_id
                  FROM task_dependencies AS dependency
                  JOIN prerequisites
                    ON dependency.task_id = prerequisites.task_id
                )
                SELECT 1 FROM prerequisites WHERE task_id = NEW.task_id
              );
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER task_dependencies_no_cycle_update
            BEFORE UPDATE OF task_id, depends_on_task_id ON task_dependencies
            BEGIN
              SELECT RAISE(ABORT, 'task dependency cycle')
              WHERE NEW.task_id = NEW.depends_on_task_id OR EXISTS (
                WITH RECURSIVE prerequisites(task_id) AS (
                  SELECT NEW.depends_on_task_id
                  UNION
                  SELECT dependency.depends_on_task_id
                  FROM task_dependencies AS dependency
                  JOIN prerequisites
                    ON dependency.task_id = prerequisites.task_id
                  WHERE NOT (
                    dependency.task_id = OLD.task_id
                    AND dependency.depends_on_task_id = OLD.depends_on_task_id
                  )
                )
                SELECT 1 FROM prerequisites WHERE task_id = NEW.task_id
              );
            END
            """
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "task dependency FK rebuild introduced violations: "
                f"{[tuple(row) for row in violations]}"
            )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise
    conn.execute("PRAGMA foreign_keys=ON")


def _migrate_alpha_identity_to_master(conn: sqlite3.Connection) -> None:
    from .master_persistence import migrate_master_persistence

    migrate_master_persistence(conn)


def _add_master_tool_call_ledger(conn: sqlite3.Connection) -> None:
    """Durable per-turn idempotency for schema-validated Master tools."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_tool_calls (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          master_session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          turn_root_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          envelope_hash TEXT NOT NULL,
          tool_name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'complete')),
          result_json TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          UNIQUE(turn_root_run_id, envelope_hash)
        )
        """
    )
    expected_columns = {
        "id",
        "master_session_id",
        "turn_root_run_id",
        "envelope_hash",
        "tool_name",
        "status",
        "result_json",
        "created_at",
        "completed_at",
    }
    actual_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(master_tool_calls)"
        ).fetchall()
    }
    if actual_columns != expected_columns:
        from .master_persistence import MasterPersistenceError

        raise MasterPersistenceError(
            "Master tool-call ledger schema is incomplete"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_tool_calls_session "
        "ON master_tool_calls(master_session_id, turn_root_run_id, id)"
    )
    from .master_persistence import assert_master_tool_ledger

    assert_master_tool_ledger(conn)


def _add_master_projection_ledger(conn: sqlite3.Connection) -> None:
    """Exactly-once links from authoritative state to Master chat and SSE."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    required_tables = {
        "users",
        "sessions",
        "messages",
        "events",
        "jobs",
        "attention_items",
        "satpam_interventions",
    }
    if not required_tables.issubset(tables):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_projections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          master_session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          projection_key TEXT NOT NULL
            CHECK (length(projection_key) BETWEEN 1 AND 300),
          projection_type TEXT NOT NULL CHECK (projection_type IN (
            'master.task.started',
            'master.task.review_ready',
            'master.task.completed',
            'master.task.failed',
            'master.task.cancelled',
            'master.task.blocked',
            'master.attention.required',
            'master.supervisor.outcome',
            'master.satpam.steered',
            'master.satpam.restart_queued',
            'master.satpam.restarted',
            'master.satpam.recovery_failed',
            'master.satpam.escalated'
          )),
          source_table TEXT NOT NULL CHECK (
            source_table IN (
              'jobs', 'attention_items', 'satpam_interventions'
            )
          ),
          source_id INTEGER NOT NULL CHECK (source_id > 0),
          task_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
          message_id INTEGER REFERENCES messages(id) ON DELETE RESTRICT,
          event_id INTEGER REFERENCES events(id) ON DELETE RESTRICT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (
            (projection_type LIKE 'master.task.%'
              AND source_table = 'jobs' AND task_id = source_id)
            OR
            (projection_type LIKE 'master.satpam.%'
              AND projection_type != 'master.satpam.recovery_failed'
              AND source_table = 'satpam_interventions'
              AND task_id IS NOT NULL)
            OR
            (projection_type IN (
              'master.attention.required',
              'master.supervisor.outcome',
              'master.satpam.recovery_failed'
            ) AND source_table = 'attention_items')
          ),
          UNIQUE(owner_user_id, projection_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_projections_session "
        "ON master_projections(master_session_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_projections_source "
        "ON master_projections(source_table, source_id, projection_type)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_projections_source_type "
        "ON master_projections("
        "owner_user_id, source_table, source_id, projection_type)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_projections_message "
        "ON master_projections(message_id) WHERE message_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_projections_event "
        "ON master_projections(event_id) WHERE event_id IS NOT NULL"
    )
    expected_columns = {
        "id",
        "owner_user_id",
        "master_session_id",
        "projection_key",
        "projection_type",
        "source_table",
        "source_id",
        "task_id",
        "message_id",
        "event_id",
        "payload_json",
        "created_at",
        "updated_at",
    }
    actual_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(master_projections)"
        ).fetchall()
    }
    if actual_columns != expected_columns:
        raise RuntimeError("Master projection ledger schema is incomplete")
    from .master_projection import assert_master_projection_ledger

    assert_master_projection_ledger(conn)


def _add_master_message_context(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_message_context (
          message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
          focus_mode TEXT NOT NULL CHECK(focus_mode IN ('fleet', 'container')),
          focus_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          target_mode TEXT NOT NULL CHECK(target_mode IN ('auto', 'explicit')),
          target_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          target_area_id INTEGER REFERENCES project_areas(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(
            focus_mode = 'container'
            OR (focus_mode = 'fleet' AND focus_container_id IS NULL)
          ),
          CHECK(
            target_mode = 'explicit'
            OR (target_mode = 'auto' AND target_container_id IS NULL AND target_area_id IS NULL)
          )
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_message_context_focus "
        "ON master_message_context(focus_container_id, message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_message_context_target "
        "ON master_message_context(target_container_id, target_area_id, message_id)"
    )


def _add_master_focus_epochs(conn: sqlite3.Connection) -> None:
    """Add durable Focus state without fabricating epochs for legacy history."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    if "focus_epoch_id" not in columns:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN focus_epoch_id "
            "INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_focus_epochs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          master_session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          container_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ended_at TEXT,
          version INTEGER NOT NULL,
          CHECK(ended_at IS NULL OR ended_at >= started_at)
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_focus_epoch_open "
        "ON master_focus_epochs(master_session_id) WHERE ended_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_master_focus_epochs_container "
        "ON master_focus_epochs(master_session_id, container_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_focus_state (
          master_session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          current_epoch_id INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
          pending_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          version INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_focus (
          message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
          focus_epoch_id INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
          focus_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
          subject_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_focus_epoch "
        "ON message_focus(focus_epoch_id, message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_focus_subject "
        "ON message_focus(subject_container_id, message_id)"
    )
    # Older Master turns did not capture a Focus epoch.  Mark them explicitly
    # as fleet-attributed rather than guessing epoch boundaries from client state.
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    message_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if {"id", "mode"} <= session_columns:
        if "session_id" in message_columns:
            conn.execute(
                "INSERT OR IGNORE INTO message_focus(message_id, focus_epoch_id, focus_container_id) "
                "SELECT m.id, NULL, NULL FROM messages m JOIN sessions s ON s.id = m.session_id "
                "WHERE s.mode = 'master'"
            )
        conn.execute(
            "INSERT OR IGNORE INTO master_focus_state("
            "master_session_id, current_epoch_id, pending_container_id, version"
            ") SELECT id, NULL, NULL, 0 FROM sessions WHERE mode = 'master'"
        )


def _harden_master_focus_contracts(conn: sqlite3.Connection) -> None:
    """Preserve epoch identity and enforce attribution at message persistence."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    required = {
        "master_focus_epochs",
        "master_focus_state",
        "message_focus",
        "messages",
        "runs",
        "sessions",
    }
    if not required.issubset(tables):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        state_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(master_focus_state)"
            ).fetchall()
        }
        if "pending_focus" not in state_columns:
            conn.execute(
                "ALTER TABLE master_focus_state ADD COLUMN pending_focus "
                "INTEGER NOT NULL DEFAULT 0 CHECK(pending_focus IN (0, 1))"
            )
        conn.execute(
            "UPDATE master_focus_state SET pending_focus = 1 "
            "WHERE pending_container_id IS NOT NULL "
            "OR (current_epoch_id IS NOT NULL AND version > COALESCE(("
            "SELECT version FROM master_focus_epochs "
            "WHERE id = current_epoch_id"
            "), version))"
        )
        conn.execute(
            "UPDATE master_focus_state SET pending_container_id = NULL, "
            "pending_focus = 0 WHERE pending_container_id = ("
            "SELECT container_id FROM master_focus_epochs "
            "WHERE id = current_epoch_id"
            ")"
        )

        container_fk = next(
            (
                row
                for row in conn.execute(
                    "PRAGMA foreign_key_list(master_focus_epochs)"
                ).fetchall()
                if str(row[3]) == "container_id"
            ),
            None,
        )
        if container_fk is not None:
            conn.execute(
                """
                CREATE TABLE master_focus_epochs_new (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  master_session_id INTEGER NOT NULL
                    REFERENCES sessions(id) ON DELETE CASCADE,
                  container_id INTEGER NOT NULL,
                  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  ended_at TEXT,
                  version INTEGER NOT NULL,
                  CHECK(ended_at IS NULL OR ended_at >= started_at)
                )
                """
            )
            conn.execute(
                "INSERT INTO master_focus_epochs_new("
                "id, master_session_id, container_id, started_at, ended_at, version"
                ") SELECT id, master_session_id, container_id, started_at, "
                "ended_at, version FROM master_focus_epochs"
            )
            conn.execute("DROP TABLE master_focus_epochs")
            conn.execute(
                "ALTER TABLE master_focus_epochs_new "
                "RENAME TO master_focus_epochs"
            )

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_focus_epoch_open "
            "ON master_focus_epochs(master_session_id) WHERE ended_at IS NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_master_focus_epochs_container "
            "ON master_focus_epochs(master_session_id, container_id, id)"
        )
        message_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        run_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        session_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if (
            {"id", "session_id", "run_id"} <= message_columns
            and {"id", "session_id", "focus_epoch_id"} <= run_columns
            and {"id", "mode"} <= session_columns
        ):
            conn.execute(
                "INSERT OR IGNORE INTO message_focus("
                "message_id, focus_epoch_id, focus_container_id, "
                "subject_container_id"
                ") SELECT message.id, run.focus_epoch_id, "
                "epoch.container_id, NULL "
                "FROM messages AS message "
                "JOIN sessions AS session ON session.id = message.session_id "
                "JOIN runs AS run ON run.id = message.run_id "
                "LEFT JOIN master_focus_epochs AS epoch "
                "ON epoch.id = run.focus_epoch_id "
                "WHERE session.mode = 'master' "
                "AND run.session_id = message.session_id"
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_master_focus_insert
                AFTER INSERT ON messages
                WHEN NEW.run_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM sessions
                    WHERE id = NEW.session_id AND mode = 'master'
                  )
                BEGIN
                  INSERT OR IGNORE INTO message_focus(
                    message_id, focus_epoch_id, focus_container_id,
                    subject_container_id
                  )
                  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
                  FROM runs AS run
                  LEFT JOIN master_focus_epochs AS epoch
                    ON epoch.id = run.focus_epoch_id
                  WHERE run.id = NEW.run_id
                    AND run.session_id = NEW.session_id;
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_master_focus_run_update
                AFTER UPDATE OF run_id ON messages
                WHEN NEW.run_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM sessions
                    WHERE id = NEW.session_id AND mode = 'master'
                  )
                BEGIN
                  INSERT OR IGNORE INTO message_focus(
                    message_id, focus_epoch_id, focus_container_id,
                    subject_container_id
                  )
                  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
                  FROM runs AS run
                  LEFT JOIN master_focus_epochs AS epoch
                    ON epoch.id = run.focus_epoch_id
                  WHERE run.id = NEW.run_id
                    AND run.session_id = NEW.session_id;
                END
                """
            )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _add_master_focus_persistence_boundaries(
    conn: sqlite3.Connection,
) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    run_focus_tables = {
        "runs",
        "sessions",
        "master_focus_epochs",
        "master_focus_state",
    }
    if run_focus_tables.issubset(tables):
        run_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        session_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        epoch_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(master_focus_epochs)"
            ).fetchall()
        }
        state_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(master_focus_state)"
            ).fetchall()
        }
        has_run_focus_shape = (
            {"session_id", "kind", "project_id", "focus_epoch_id"}
            <= run_columns
            and {"id", "mode"} <= session_columns
            and {"id", "master_session_id"} <= epoch_columns
            and {"master_session_id", "current_epoch_id"} <= state_columns
        )
    else:
        has_run_focus_shape = False
    if has_run_focus_shape:
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS runs_master_focus_insert
            BEFORE INSERT ON runs
            WHEN EXISTS (
              SELECT 1 FROM sessions
              WHERE id = NEW.session_id AND mode = 'master'
            )
            AND (
              (NEW.kind != 'master' AND NEW.kind NOT LIKE 'master_tool_%')
              OR NEW.project_id IS NOT NULL
              OR NOT EXISTS (
                SELECT 1 FROM master_focus_state
                WHERE master_session_id = NEW.session_id
                  AND current_epoch_id IS NEW.focus_epoch_id
              )
              OR (
                NEW.focus_epoch_id IS NOT NULL
                AND NOT EXISTS (
                  SELECT 1 FROM master_focus_epochs
                  WHERE id = NEW.focus_epoch_id
                    AND master_session_id = NEW.session_id
                )
              )
            )
            BEGIN
              SELECT RAISE(
                ABORT,
                'Master runs require captured Focus attribution'
              );
            END
            """
        )
    if not {
        "task_delegations",
        "sessions",
        "messages",
        "message_focus",
        "master_focus_epochs",
    }.issubset(tables):
        return
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(task_delegations)"
        ).fetchall()
    }
    if "origin_focus_epoch_id" not in columns:
        conn.execute(
            "ALTER TABLE task_delegations ADD COLUMN "
            "origin_focus_epoch_id INTEGER "
            "REFERENCES master_focus_epochs(id) ON DELETE RESTRICT"
        )
    if "origin_focus_captured" not in columns:
        conn.execute(
            "ALTER TABLE task_delegations ADD COLUMN "
            "origin_focus_captured INTEGER NOT NULL DEFAULT 0 "
            "CHECK(origin_focus_captured IN (0, 1))"
        )
    conn.execute(
        "DROP TRIGGER IF EXISTS task_delegations_focus_immutable"
    )
    conn.execute(
        "UPDATE task_delegations SET "
        "origin_focus_epoch_id = ("
        "  SELECT focus.focus_epoch_id "
        "  FROM message_focus AS focus "
        "  JOIN messages AS message ON message.id = focus.message_id "
        "  WHERE focus.message_id = task_delegations.origin_message_id "
        "    AND message.session_id = task_delegations.origin_session_id"
        "), origin_focus_captured = 1 "
        "WHERE origin_focus_captured = 0 "
        "AND EXISTS ("
        "  SELECT 1 FROM sessions "
        "  WHERE id = task_delegations.origin_session_id "
        "    AND mode = 'master'"
        ") "
        "AND EXISTS ("
        "  SELECT 1 FROM message_focus AS focus "
        "  JOIN messages AS message ON message.id = focus.message_id "
        "  WHERE focus.message_id = task_delegations.origin_message_id "
        "    AND message.session_id = task_delegations.origin_session_id"
        ")"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS task_delegations_master_focus_insert
        BEFORE INSERT ON task_delegations
        WHEN (
          EXISTS (
            SELECT 1 FROM sessions
            WHERE id = NEW.origin_session_id AND mode = 'master'
          )
          AND (
            NEW.origin_focus_captured != 1
            OR (
              NEW.origin_focus_epoch_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM master_focus_epochs
                WHERE id = NEW.origin_focus_epoch_id
                  AND master_session_id = NEW.origin_session_id
              )
            )
          )
        )
        OR (
          NEW.origin_focus_captured = 1
          AND NOT EXISTS (
            SELECT 1 FROM sessions
            WHERE id = NEW.origin_session_id AND mode = 'master'
          )
        )
        OR (
          NEW.origin_focus_captured = 0
          AND NEW.origin_focus_epoch_id IS NOT NULL
        )
        BEGIN
          SELECT RAISE(
            ABORT,
            'Task delegation Focus attribution is invalid'
          );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS task_delegations_focus_immutable
        BEFORE UPDATE OF origin_focus_epoch_id, origin_focus_captured
        ON task_delegations
        WHEN NEW.origin_focus_epoch_id IS NOT OLD.origin_focus_epoch_id
          OR NEW.origin_focus_captured != OLD.origin_focus_captured
        BEGIN
          SELECT RAISE(
            ABORT,
            'Task delegation Focus attribution is immutable'
          );
        END
        """
    )


def _freeze_master_focus_attribution(conn: sqlite3.Connection) -> None:
    """Make captured message and run epoch identity append-only."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "message_focus" in tables:
        message_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(message_focus)"
            ).fetchall()
        }
        if "focus_epoch_id" in message_columns:
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS message_focus_epoch_immutable
                BEFORE UPDATE OF focus_epoch_id ON message_focus
                WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'Message Focus epoch attribution is immutable'
                  );
                END
                """
            )
    if "runs" in tables:
        run_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "focus_epoch_id" in run_columns:
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS runs_focus_epoch_immutable
                BEFORE UPDATE OF focus_epoch_id ON runs
                WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'Run Focus epoch attribution is immutable'
                  );
                END
                """
            )


def _add_self_update_runs(conn: sqlite3.Connection) -> None:
    """Owner-visible mirror only.  The external journal remains authoritative."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS self_update_runs (
          id TEXT PRIMARY KEY,
          origin_job_id TEXT,
          base_commit TEXT NOT NULL,
          candidate_commit TEXT NOT NULL,
          previous_release_id TEXT,
          candidate_release_id TEXT,
          previous_schema_version INTEGER,
          candidate_schema_version INTEGER,
          phase TEXT NOT NULL,
          status TEXT NOT NULL,
          journal_digest TEXT,
          journal_ref TEXT,
          evidence_summary TEXT NOT NULL DEFAULT '{}',
          failure_class TEXT,
          rollback_status TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_self_update_runs_status ON self_update_runs(status, created_at DESC)")


def _preserve_master_history_scope(conn: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    has_focus = {"message_focus", "messages"}.issubset(tables)
    has_context = {"master_message_context", "messages"}.issubset(tables)
    focus_foreign_tables = {
        str(row[2])
        for row in conn.execute(
            "PRAGMA foreign_key_list(message_focus)"
        ).fetchall()
    } if has_focus else set()
    context_foreign_tables = {
        str(row[2])
        for row in conn.execute(
            "PRAGMA foreign_key_list(master_message_context)"
        ).fetchall()
    } if has_context else set()
    rebuild_focus = has_focus and "projects" in focus_foreign_tables
    rebuild_context = has_context and bool(
        {"projects", "project_areas"} & context_foreign_tables
    )
    if not has_focus and not has_context:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if rebuild_focus:
            conn.execute("DROP TRIGGER IF EXISTS messages_master_focus_insert")
            conn.execute(
                "DROP TRIGGER IF EXISTS messages_master_focus_run_update"
            )
            focus_container = "focus.focus_container_id"
            if "master_focus_epochs" in tables:
                focus_container = (
                    "COALESCE(focus.focus_container_id, epoch.container_id)"
                )
            subject_container = "focus.subject_container_id"
            if "master_projections" in tables:
                subject_container = (
                    "COALESCE(focus.subject_container_id, "
                    "CAST(json_extract(projection.payload_json, "
                    "'$.subject_container_id') AS INTEGER), "
                    "CAST(json_extract(projection.payload_json, "
                    "'$.container_id') AS INTEGER))"
                )
            epoch_join = (
                "LEFT JOIN master_focus_epochs AS epoch "
                "ON epoch.id = focus.focus_epoch_id "
                if "master_focus_epochs" in tables
                else ""
            )
            projection_join = (
                "LEFT JOIN master_projections AS projection "
                "ON projection.message_id = focus.message_id "
                if "master_projections" in tables
                else ""
            )
            conn.execute(
                """
                CREATE TABLE message_focus_new (
                  message_id INTEGER PRIMARY KEY
                    REFERENCES messages(id) ON DELETE CASCADE,
                  focus_epoch_id INTEGER
                    REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
                  focus_container_id INTEGER,
                  subject_container_id INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO message_focus_new("
                "message_id, focus_epoch_id, focus_container_id, "
                "subject_container_id"
                ") SELECT focus.message_id, focus.focus_epoch_id, "
                f"{focus_container}, {subject_container} "
                "FROM message_focus AS focus "
                f"{epoch_join}{projection_join}"
            )
            conn.execute("DROP TABLE message_focus")
            conn.execute(
                "ALTER TABLE message_focus_new RENAME TO message_focus"
            )
            conn.execute(
                "CREATE INDEX idx_message_focus_epoch "
                "ON message_focus(focus_epoch_id, message_id)"
            )
            conn.execute(
                "CREATE INDEX idx_message_focus_subject "
                "ON message_focus(subject_container_id, message_id)"
            )

        if rebuild_context:
            focus_join = (
                "LEFT JOIN message_focus AS focus "
                "ON focus.message_id = context.message_id "
                if has_focus
                else ""
            )
            focus_container = (
                "COALESCE(context.focus_container_id, "
                "focus.focus_container_id)"
                if has_focus
                else "context.focus_container_id"
            )
            target_container = (
                "CASE WHEN context.target_mode = 'explicit' THEN "
                "COALESCE(context.target_container_id, "
                "focus.focus_container_id) ELSE NULL END"
                if has_focus
                else "context.target_container_id"
            )
            conn.execute(
                """
                CREATE TABLE master_message_context_new (
                  message_id INTEGER PRIMARY KEY
                    REFERENCES messages(id) ON DELETE CASCADE,
                  focus_mode TEXT NOT NULL
                    CHECK(focus_mode IN ('fleet', 'container')),
                  focus_container_id INTEGER,
                  target_mode TEXT NOT NULL
                    CHECK(target_mode IN ('auto', 'explicit')),
                  target_container_id INTEGER,
                  target_area_id INTEGER,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  CHECK(
                    focus_mode = 'container'
                    OR (
                      focus_mode = 'fleet'
                      AND focus_container_id IS NULL
                    )
                  ),
                  CHECK(
                    target_mode = 'explicit'
                    OR (
                      target_mode = 'auto'
                      AND target_container_id IS NULL
                      AND target_area_id IS NULL
                    )
                  )
                )
                """
            )
            conn.execute(
                "INSERT INTO master_message_context_new("
                "message_id, focus_mode, focus_container_id, target_mode, "
                "target_container_id, target_area_id, created_at"
                ") SELECT context.message_id, context.focus_mode, "
                f"{focus_container}, context.target_mode, "
                f"{target_container}, context.target_area_id, "
                "context.created_at FROM master_message_context AS context "
                f"{focus_join}"
            )
            conn.execute("DROP TABLE master_message_context")
            conn.execute(
                "ALTER TABLE master_message_context_new "
                "RENAME TO master_message_context"
            )
            conn.execute(
                "CREATE INDEX idx_master_message_context_focus "
                "ON master_message_context(focus_container_id, message_id)"
            )
            conn.execute(
                "CREATE INDEX idx_master_message_context_target "
                "ON master_message_context("
                "target_container_id, target_area_id, message_id)"
            )
        if has_focus:
            conn.execute(
                "DROP TRIGGER IF EXISTS message_focus_epoch_immutable"
            )
            conn.execute(
                """
                CREATE TRIGGER message_focus_epoch_immutable
                BEFORE UPDATE OF
                  focus_epoch_id, focus_container_id, subject_container_id
                ON message_focus
                WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
                  OR NEW.focus_container_id IS NOT OLD.focus_container_id
                  OR NEW.subject_container_id IS NOT OLD.subject_container_id
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'Message Focus epoch attribution is immutable'
                  );
                END
                """
            )
        if has_context:
            conn.execute(
                "DROP TRIGGER IF EXISTS master_message_context_immutable"
            )
            conn.execute(
                """
                CREATE TRIGGER master_message_context_immutable
                BEFORE UPDATE OF
                  focus_mode, focus_container_id, target_mode,
                  target_container_id, target_area_id
                ON master_message_context
                WHEN NEW.focus_mode IS NOT OLD.focus_mode
                  OR NEW.focus_container_id IS NOT OLD.focus_container_id
                  OR NEW.target_mode IS NOT OLD.target_mode
                  OR NEW.target_container_id IS NOT OLD.target_container_id
                  OR NEW.target_area_id IS NOT OLD.target_area_id
                BEGIN
                  SELECT RAISE(
                    ABORT,
                    'Master message context is immutable'
                  );
                END
                """
            )
        if rebuild_focus and {
            "runs",
            "sessions",
            "master_focus_epochs",
        }.issubset(tables):
            conn.execute(
                """
                CREATE TRIGGER messages_master_focus_insert
                AFTER INSERT ON messages
                WHEN NEW.run_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM sessions
                    WHERE id = NEW.session_id AND mode = 'master'
                  )
                BEGIN
                  INSERT OR IGNORE INTO message_focus(
                    message_id, focus_epoch_id, focus_container_id,
                    subject_container_id
                  )
                  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
                  FROM runs AS run
                  LEFT JOIN master_focus_epochs AS epoch
                    ON epoch.id = run.focus_epoch_id
                  WHERE run.id = NEW.run_id
                    AND run.session_id = NEW.session_id;
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER messages_master_focus_run_update
                AFTER UPDATE OF run_id ON messages
                WHEN NEW.run_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM sessions
                    WHERE id = NEW.session_id AND mode = 'master'
                  )
                BEGIN
                  INSERT OR IGNORE INTO message_focus(
                    message_id, focus_epoch_id, focus_container_id,
                    subject_container_id
                  )
                  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
                  FROM runs AS run
                  LEFT JOIN master_focus_epochs AS epoch
                    ON epoch.id = run.focus_epoch_id
                  WHERE run.id = NEW.run_id
                    AND run.session_id = NEW.session_id;
                END
                """
            )
        if {
            "master_projections",
            "events",
            "message_focus",
        }.issubset(tables):
            conn.execute(
                """
                UPDATE master_projections
                SET payload_json = json_set(
                  payload_json,
                  '$.focus_epoch_id',
                  (
                    SELECT focus.focus_epoch_id
                    FROM message_focus AS focus
                    WHERE focus.message_id = master_projections.message_id
                  ),
                  '$.focus_container_id',
                  (
                    SELECT focus.focus_container_id
                    FROM message_focus AS focus
                    WHERE focus.message_id = master_projections.message_id
                  ),
                  '$.subject_container_id',
                  (
                    SELECT focus.subject_container_id
                    FROM message_focus AS focus
                    WHERE focus.message_id = master_projections.message_id
                  )
                )
                WHERE message_id IN (
                  SELECT message_id FROM message_focus
                )
                """
            )
            conn.execute(
                """
                UPDATE events
                SET payload = (
                  SELECT projection.payload_json
                  FROM master_projections AS projection
                  WHERE projection.event_id = events.id
                )
                WHERE id IN (
                  SELECT event_id
                  FROM master_projections
                  WHERE event_id IS NOT NULL
                )
                """
            )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _add_graph_states(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_states (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          container_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          area_id INTEGER REFERENCES project_areas(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('knowledge', 'code')),
          root_path TEXT NOT NULL,
          graph_path TEXT NOT NULL,
          source_fingerprint TEXT,
          graph_sha256 TEXT,
          tool_version TEXT,
          semantic_backend TEXT NOT NULL DEFAULT 'disabled',
          state TEXT NOT NULL DEFAULT 'missing'
            CHECK(state IN ('missing', 'queued', 'building', 'fresh', 'stale', 'failed')),
          generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
          last_success_at TEXT,
          last_attempt_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(
            (kind = 'knowledge' AND area_id IS NULL)
            OR (kind = 'code' AND area_id IS NOT NULL)
          )
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_states_knowledge "
        "ON graph_states(container_id, kind) "
        "WHERE kind = 'knowledge' AND area_id IS NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_states_code "
        "ON graph_states(container_id, area_id, kind) "
        "WHERE kind = 'code' AND area_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_graph_states_container "
        "ON graph_states(container_id, state, kind, area_id)"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS graph_states_area_scope_insert
        BEFORE INSERT ON graph_states
        WHEN NEW.area_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM project_areas area
          WHERE area.id = NEW.area_id
            AND area.project_id = NEW.container_id
            AND area.kind = 'code'
            AND area.source != 'excluded'
        )
        BEGIN
          SELECT RAISE(
            ABORT,
            'graph state Area is not an active code Area in its Container'
          );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS graph_states_area_scope_update
        BEFORE UPDATE OF container_id, area_id, kind ON graph_states
        WHEN NEW.area_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM project_areas area
          WHERE area.id = NEW.area_id
            AND area.project_id = NEW.container_id
            AND area.kind = 'code'
            AND area.source != 'excluded'
        )
        BEGIN
          SELECT RAISE(
            ABORT,
            'graph state Area is not an active code Area in its Container'
          );
        END
        """
    )


def _add_code_graph_lifecycle_columns(conn: sqlite3.Connection) -> None:
    """Group 10: HEAD tracking, pending merge range, and rebuild reason."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(graph_states)").fetchall()
    }
    if "repo_head" not in columns:
        conn.execute("ALTER TABLE graph_states ADD COLUMN repo_head TEXT")
    if "pending_base_commit" not in columns:
        conn.execute("ALTER TABLE graph_states ADD COLUMN pending_base_commit TEXT")
    if "pending_head_commit" not in columns:
        conn.execute("ALTER TABLE graph_states ADD COLUMN pending_head_commit TEXT")
    if "rebuild_reason" not in columns:
        conn.execute("ALTER TABLE graph_states ADD COLUMN rebuild_reason TEXT")


def _add_knowledge_rebuild_outbox(conn: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if not {"jobs", "projects", "project_areas"}.issubset(tables):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_rebuild_intents (
          container_id INTEGER PRIMARY KEY
            REFERENCES projects(id) ON DELETE CASCADE,
          reason TEXT NOT NULL,
          intent_version INTEGER NOT NULL DEFAULT 1
            CHECK(intent_version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS jobs_ops_done_knowledge_rebuild
        AFTER UPDATE OF status ON jobs
        WHEN OLD.status != 'done'
          AND NEW.status = 'done'
          AND NEW.project_id IS NOT NULL
          AND (
            NEW.target_area_id IS NULL
            OR EXISTS (
              SELECT 1 FROM project_areas area
              WHERE area.id = NEW.target_area_id
                AND area.project_id = NEW.project_id
                AND area.kind = 'ops'
                AND area.source != 'excluded'
            )
          )
        BEGIN
          INSERT INTO knowledge_rebuild_intents(container_id, reason)
          VALUES (NEW.project_id, 'ops_task_done')
          ON CONFLICT(container_id) DO UPDATE SET
            reason = excluded.reason,
            intent_version = knowledge_rebuild_intents.intent_version + 1,
            updated_at = CURRENT_TIMESTAMP;
        END
        """
    )


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
    (15, "add FK messages.run_id -> runs(id) ON DELETE SET NULL (table rebuild)", _add_messages_run_id_fk, {"no_auto_tx": True}),
    (16, "add FKs sessions.task_id/job_id/workflow_id (table rebuild)", _add_sessions_pointer_fks, {"no_auto_tx": True}),
    (17, "merge tasks into jobs: drop sessions.task_id + tasks table (rebuild)", _drop_tasks_feature, {"no_auto_tx": True}),
    (18, "add project_areas: wrap existing projects as work containers (T1)", _add_project_areas),
    (19, "add jobs.target_area_id + job_worktrees: worktree machinery for repo jobs (T1 slice 2)", _add_repo_job_worktrees),
    (20, "add jobs.rejected_reason: reject-at-review verdict for the review surface (slice 4)", _add_jobs_rejected_reason),
    (21, "add runs.continued_from_run_id + continuation_count: timeout auto-continuation chain (T5 slice 5)", _add_runs_continuation),
    (22, "add script_trust: hash-bound one-time approvals for deterministic script steps (T6 slice 6)", _add_script_trust),
    (23, "add artifact_records: durable deliverable registry seeded from the scanner (T4 slice 8)", _add_artifact_registry),
    (24, "add project_areas.push_on_merge + job_worktrees push outcome: BYO repo-remote connector (T9 slice 11)", _add_repo_remote_push),
    (25, "add satpam_watch + satpam_interventions + node_states decision-hold/contract columns: supervision loop (T10 slice 12)", _add_satpam_supervision),
    (26, "add Alpha identity, job ownership, checkpoints, turn journals, and attention inbox", _add_alpha_foundation),
    (27, "move graph workflow inputs onto trigger nodes", _move_workflow_inputs_to_trigger),
    (28, "add Container registry and durable physical Ops migration state", _add_container_foundation),
    (29, "add durable one-Area Task delegations and dependency DAG contracts", _add_task_delegation_contracts),
    (
        30,
        "protect durable Task prerequisites from silent deletion",
        _protect_task_prerequisites_from_deletion,
        {"no_auto_tx": True},
    ),
    (
        31,
        "migrate durable Alpha identity and Task ownership links to Master",
        _migrate_alpha_identity_to_master,
    ),
    (
        32,
        "add durable per-turn Master product-tool idempotency ledger",
        _add_master_tool_call_ledger,
    ),
    (
        33,
        "add durable Master Task and supervision projection ledger",
        _add_master_projection_ledger,
    ),
    (
        34,
        "add durable Master Focus and target context per owner message",
        _add_master_message_context,
    ),
    (
        35,
        "add scoped Graphify operational state and freshness contract",
        _add_graph_states,
    ),
    (
        36,
        "add Code graph lifecycle HEAD and rebuild queue columns",
        _add_code_graph_lifecycle_columns,
    ),
    (
        37,
        "add durable Ops completion Knowledge rebuild outbox",
        _add_knowledge_rebuild_outbox,
    ),
    (
        38,
        "add Master Focus epochs, state, immutable message attribution, and run epoch capture",
        _add_master_focus_epochs,
    ),
    (
        39,
        "preserve Master Focus epoch identity and enforce message attribution",
        _harden_master_focus_contracts,
        {"no_auto_tx": True},
    ),
    (
        40,
        "enforce Master run isolation and preserve Task projection Focus",
        _add_master_focus_persistence_boundaries,
    ),
    (
        41,
        "make captured Master message and run Focus epochs immutable",
        _freeze_master_focus_attribution,
    ),
    (
        42,
        "preserve immutable Master history scope after Container deletion",
        _preserve_master_history_scope,
        {"no_auto_tx": True},
    ),
    (43, "add external safe-update owner projection", _add_self_update_runs),
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
    for entry in pending:
        version, description, apply = entry[0], entry[1], entry[2]
        opts = entry[3] if len(entry) > 3 else {}
        if opts.get("no_auto_tx"):
            # The migration manages its own transaction (e.g. a table rebuild that
            # needs PRAGMA foreign_keys=OFF, which is a no-op inside a transaction).
            # It runs in autocommit; we record the version after it returns. Such a
            # migration MUST be idempotent so a crash before the version is recorded
            # is safe to re-run.
            apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, iso_now()),
            )
            # On a default-isolation connection this INSERT implicitly opened a
            # transaction; commit it so the next migration's explicit BEGIN works.
            if conn.in_transaction:
                conn.commit()
        else:
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
