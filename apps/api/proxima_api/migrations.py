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
        ensure_ops_area(conn, row["id"])
        sync_code_areas(conn, row["id"], row["path"])


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
