from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .profile_seed import seed_hermes_home
from .runner_specs import FALLBACK_RUNNER

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  os_user TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',
  password_hash TEXT,
  password_set_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  hermes_home TEXT NOT NULL,
  runner_id TEXT NOT NULL DEFAULT '__DEFAULT_RUNNER__',
  default_model TEXT,
  instructions TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, slug)
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  owner_user_id INTEGER NOT NULL REFERENCES users(id),
  visibility TEXT NOT NULL DEFAULT 'private',
  archived_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Work-container areas (Phase-1 slice 1, T1): a project holds zero-or-more
-- code areas (rel_path of a git-repo subfolder; '.' = repo at root) and
-- exactly one ops area (non-code output space). source: 'auto' (detected),
-- 'manual' (owner-registered, never clobbered by re-detection), 'excluded'
-- (tombstone left by removal so re-detection can't resurrect the area).
CREATE TABLE IF NOT EXISTS project_areas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'code',
  rel_path TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'auto',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(project_id, kind, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_project_areas_project ON project_areas(project_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_areas_one_ops ON project_areas(project_id) WHERE kind = 'ops';
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
  runner_id TEXT NOT NULL DEFAULT '__DEFAULT_RUNNER__',
  visibility TEXT NOT NULL DEFAULT 'private',
  mode TEXT NOT NULL DEFAULT 'chat',
  job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
  manual_title INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  author TEXT,
  run_id INTEGER,
  output_links TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
  runner_id TEXT NOT NULL DEFAULT '__DEFAULT_RUNNER__',
  kind TEXT NOT NULL DEFAULT 'chat',
  status TEXT NOT NULL DEFAULT 'queued',
  prompt TEXT NOT NULL,
  model TEXT,
  hermes_home TEXT,
  collaboration_id INTEGER,
  collaboration_role TEXT,
  pid INTEGER,
  started_at TEXT,
  finished_at TEXT,
  heartbeat_at TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, seq)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Workflows = reusable recipes (definition). Steps live as a JSON array so a
-- recipe is edited/snapshotted as one unit.
CREATE TABLE IF NOT EXISTS workflows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'other',
  status TEXT NOT NULL DEFAULT 'active',
  steps TEXT NOT NULL DEFAULT '[]',
  -- Optional graph definition {nodes,edges} for the new orchestration engine
  -- (ADR-0001). NULL = linear recipe (steps only), the classic engine.
  graph TEXT,
  inputs TEXT NOT NULL DEFAULT '[]',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Jobs = one execution (a workflow run, or an ad-hoc 1-step task). steps_state is
-- a frozen snapshot of the recipe steps plus per-step execution state.
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  current_step_idx INTEGER NOT NULL DEFAULT 0,
  input TEXT,
  steps_state TEXT NOT NULL DEFAULT '[]',
  -- Execution engine discriminator (ADR-0001). 'linear' = the classic
  -- current_step_idx/steps_state cursor; 'graph' = node/edge engine whose
  -- per-node state lives in node_states (steps_state stays '[]'). The two
  -- engines coexist; linear jobs are untouched by the graph path.
  engine TEXT NOT NULL DEFAULT 'linear',
  -- Frozen {nodes,edges} snapshot for graph jobs (NULL for linear).
  graph TEXT,
  schedule_id INTEGER,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT,
  finished_at TEXT,
  archived_at TEXT
);
-- Schedules = a first-class recurring trigger for a workflow (cron). The scheduler
-- materializes only due jobs (not a backlog); spawned jobs carry schedule_id.
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  cron TEXT NOT NULL,
  input TEXT,
  overlap_policy TEXT NOT NULL DEFAULT 'skip',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_minute TEXT,
  last_tick_at TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status ON jobs(project_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_workflow ON jobs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_jobs_archived ON jobs(archived_at);
CREATE INDEX IF NOT EXISTS idx_workflows_project ON workflows(project_id, status);
-- Durable per-node state for graph jobs (ADR-0001 primitive #2). One row per
-- (job, node): the node's own status, the run it dispatched, its resolved
-- inputs, its validated typed output, and a version for guarded transitions.
-- Replaces the linear steps_state cursor for engine='graph' jobs.
CREATE TABLE IF NOT EXISTS node_states (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  inputs TEXT,
  output_kind TEXT,
  output TEXT,
  checkpoint TEXT,
  error TEXT,
  version INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(job_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_node_states_job ON node_states(job_id, status);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- An ACP session belongs to the agent HOME that created it, so a shared thread
-- needs one ACP session PER home (per collaborator), not a single shared id.
CREATE TABLE IF NOT EXISTS agent_sessions (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  hermes_home TEXT NOT NULL,
  acp_session_id TEXT NOT NULL,
  PRIMARY KEY (session_id, hermes_home)
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_message_reviews_source ON message_reviews(source_message_id, id);
CREATE INDEX IF NOT EXISTS idx_message_reviews_session ON message_reviews(session_id, id);
CREATE INDEX IF NOT EXISTS idx_message_reviews_run ON message_reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_session ON prompt_collaborations(session_id, id);
CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_parent ON prompt_collaborations(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_collaborations_synthesis ON prompt_collaborations(synthesis_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, id);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);
""".replace("__DEFAULT_RUNNER__", FALLBACK_RUNNER)


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _ensure_prompt_collaborations(conn: sqlite3.Connection) -> None:
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


def _ensure_message_reviews(conn: sqlite3.Connection) -> None:
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


def _ensure_node_states(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_states (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          node_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          inputs TEXT,
          output_kind TEXT,
          output TEXT,
          checkpoint TEXT,
          error TEXT,
          version INTEGER NOT NULL DEFAULT 0,
          started_at TEXT,
          finished_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(job_id, node_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_node_states_job ON node_states(job_id, status)")


def migrate_existing(conn: sqlite3.Connection) -> None:
    _ensure_message_reviews(conn)
    _ensure_prompt_collaborations(conn)
    _ensure_node_states(conn)
    _add_column(conn, "users", "password_hash", "password_hash TEXT")
    _add_column(conn, "users", "password_set_at", "password_set_at TEXT")
    _add_column(conn, "projects", "visibility", "visibility TEXT NOT NULL DEFAULT 'private'")
    _add_column(conn, "sessions", "profile_id", "profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL")
    _add_column(conn, "sessions", "visibility", "visibility TEXT NOT NULL DEFAULT 'private'")
    _add_column(conn, "sessions", "mode", "mode TEXT NOT NULL DEFAULT 'chat'")
    _add_column(conn, "sessions", "job_id", "job_id INTEGER")
    _add_column(conn, "sessions", "workflow_id", "workflow_id INTEGER")
    _add_column(conn, "sessions", "manual_title", "manual_title INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "produced_artifacts", "produced_artifacts TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "workflows", "inputs", "inputs TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "workflows", "graph", "graph TEXT")
    # Graph engine (ADR-0001): additive, coexists with the linear cursor.
    _add_column(conn, "jobs", "engine", "engine TEXT NOT NULL DEFAULT 'linear'")
    _add_column(conn, "jobs", "graph", "graph TEXT")
    _add_column(conn, "runs", "heartbeat_at", "heartbeat_at TEXT")
    _add_column(conn, "profiles", "runner_id", f"runner_id TEXT NOT NULL DEFAULT '{FALLBACK_RUNNER}'")
    # Per-profile skill/MCP selection (JSON: {"skills":[ids],"mcp":[names]}).
    # NULL = inherit ALL detected for the runner (best default: host skills just work).
    _add_column(conn, "profiles", "capabilities", "capabilities TEXT")
    _add_column(conn, "messages", "author", "author TEXT")
    _add_column(conn, "messages", "run_id", "run_id INTEGER")
    _add_column(conn, "messages", "output_links", "output_links TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "runs", "kind", "kind TEXT NOT NULL DEFAULT 'chat'")
    _add_column(conn, "runs", "collaboration_id", "collaboration_id INTEGER")
    _add_column(conn, "runs", "collaboration_role", "collaboration_role TEXT")
    _add_column(conn, "message_reviews", "merge_transcript", "merge_transcript TEXT")
    _add_column(conn, "message_reviews", "source_original_content", "source_original_content TEXT")
    _add_column(conn, "message_reviews", "applied_at", "applied_at TEXT")
    _cleanup_orphan_agent_sessions(conn)


def _cleanup_orphan_agent_sessions(conn: sqlite3.Connection) -> int:
    """Remove stale ACP mappings left behind by older cleanup paths.

    The table has ON DELETE CASCADE now, but existing installs can already carry
    orphan rows from before that lifecycle was reliable. Leaving them violates
    PRAGMA foreign_key_check and can point a future agent load at a deleted chat.
    """
    if "agent_sessions" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return 0
    cur = conn.execute(
        "DELETE FROM agent_sessions "
        "WHERE NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.id = agent_sessions.session_id)"
    )
    return int(cur.rowcount or 0)


def init_db(conn: sqlite3.Connection, seed_users: list[dict[str, str]] | None = None, hermes_home_factory: Any | None = None, source_hermes_home: str | None = None) -> None:
    conn.executescript(SCHEMA)
    migrate_existing(conn)
    from .auth import hash_password, iso_now

    for user in seed_users or []:
        # Password-less by default (single-user owner is created without a password;
        # they set one via the setup flow). Only seed a hash if one is explicitly given.
        password_hash = user.get("password_hash") or (hash_password(user["password"]) if user.get("password") else None)
        conn.execute(
            """
            INSERT OR IGNORE INTO users(username, os_user, role, password_hash, password_set_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user["username"],
                user.get("os_user") or user["username"],
                user.get("role") or "member",
                password_hash,
                iso_now() if password_hash else None,
            ),
        )
        row = conn.execute("SELECT * FROM users WHERE username = ?", (user["username"],)).fetchone()
        if row and hermes_home_factory:
            exists = conn.execute("SELECT id FROM profiles WHERE user_id = ?", (row["id"],)).fetchone()
            if not exists:
                home = hermes_home_factory(row["username"], "default")
                Path(home).mkdir(parents=True, exist_ok=True)
                _source = Path(source_hermes_home) if source_hermes_home else Path(os.path.expanduser("~/.hermes"))
                seed_hermes_home(_source, Path(home))
                conn.execute(
                    "INSERT INTO profiles(user_id, slug, name, hermes_home, is_default) VALUES (?, 'default', 'Default', ?, 1)",
                    (row["id"], str(home)),
                )
