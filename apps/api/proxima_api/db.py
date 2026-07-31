from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .directory_handles import (
    directory_identity_for_path,
    unavailable_directory_identity,
)
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
  -- Built-in product identities (Master) are hidden from the normal worker
  -- profile list. NULL remains an owner-created coding profile.
  system_kind TEXT,
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
  path_identity TEXT,
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
  -- BYO remote connector (T9, slice 11): push the merged main line to the
  -- area's own git remote after a repo job's local merge. Explicit per-area
  -- opt-in, DEFAULT OFF; only offered when the area has a detected remote.
  push_on_merge INTEGER NOT NULL DEFAULT 0,
  -- The remote URL the owner opted into, pinned when the toggle is enabled
  -- (audit F3): the push refuses if the repo's own .git/config — writable by
  -- any agent working in the repo — no longer points at this URL.
  push_remote_url TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(project_id, kind, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_project_areas_project ON project_areas(project_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_areas_one_ops ON project_areas(project_id) WHERE kind = 'ops';
CREATE TABLE IF NOT EXISTS container_registry (
  container_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  identity_label TEXT,
  summary TEXT,
  source_hash TEXT,
  indexed_at TEXT,
  last_activity_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_container_registry_activity
  ON container_registry(last_activity_at DESC, container_id);
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
);
CREATE INDEX IF NOT EXISTS idx_container_ops_migrations_status
  ON container_ops_migrations(status, updated_at);
-- Graphify artifacts stay in their exact Container/Area filesystem scope.
-- SQLite stores only operational state and freshness metadata. Knowledge
-- graphs belong to one Container (area_id NULL); Code graphs belong to one
-- registered code Area.
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
  -- Group 10 Code graph lifecycle: last published HEAD, pending merge range,
  -- and why a rebuild was enqueued. Knowledge rows leave these NULL.
  repo_head TEXT,
  pending_base_commit TEXT,
  pending_head_commit TEXT,
  rebuild_reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK(
    (kind = 'knowledge' AND area_id IS NULL)
    OR (kind = 'code' AND area_id IS NOT NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_states_knowledge
  ON graph_states(container_id, kind)
  WHERE kind = 'knowledge' AND area_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_states_code
  ON graph_states(container_id, area_id, kind)
  WHERE kind = 'code' AND area_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_graph_states_container
  ON graph_states(container_id, state, kind, area_id);
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
  SELECT RAISE(ABORT, 'graph state Area is not an active code Area in its Container');
END;
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
  SELECT RAISE(ABORT, 'graph state Area is not an active code Area in its Container');
END;
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
CREATE TABLE IF NOT EXISTS master_message_context (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  focus_mode TEXT NOT NULL CHECK(focus_mode IN ('fleet', 'container')),
  focus_container_id INTEGER,
  target_mode TEXT NOT NULL CHECK(target_mode IN ('auto', 'explicit')),
  target_container_id INTEGER,
  target_area_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK(
    focus_mode = 'container'
    OR (focus_mode = 'fleet' AND focus_container_id IS NULL)
  ),
  CHECK(
    target_mode = 'explicit'
    OR (target_mode = 'auto' AND target_container_id IS NULL AND target_area_id IS NULL)
  )
);
CREATE TRIGGER IF NOT EXISTS master_message_context_immutable
BEFORE UPDATE OF
  focus_mode, focus_container_id, target_mode, target_container_id, target_area_id
ON master_message_context
WHEN NEW.focus_mode IS NOT OLD.focus_mode
  OR NEW.focus_container_id IS NOT OLD.focus_container_id
  OR NEW.target_mode IS NOT OLD.target_mode
  OR NEW.target_container_id IS NOT OLD.target_container_id
  OR NEW.target_area_id IS NOT OLD.target_area_id
BEGIN
  SELECT RAISE(ABORT, 'Master message context is immutable');
END;
CREATE INDEX IF NOT EXISTS idx_master_message_context_focus
  ON master_message_context(focus_container_id, message_id);
CREATE INDEX IF NOT EXISTS idx_master_message_context_target
  ON master_message_context(target_container_id, target_area_id, message_id);
CREATE TABLE IF NOT EXISTS master_focus_epochs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  master_session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  container_id INTEGER NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT,
  version INTEGER NOT NULL,
  CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_master_focus_epoch_open
  ON master_focus_epochs(master_session_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_master_focus_epochs_container
  ON master_focus_epochs(master_session_id, container_id, id);
CREATE TABLE IF NOT EXISTS master_focus_state (
  master_session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  current_epoch_id INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
  pending_container_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  pending_focus INTEGER NOT NULL DEFAULT 0 CHECK(pending_focus IN (0, 1)),
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS message_focus (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  focus_epoch_id INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
  focus_container_id INTEGER,
  subject_container_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_message_focus_epoch
  ON message_focus(focus_epoch_id, message_id);
CREATE INDEX IF NOT EXISTS idx_message_focus_subject
  ON message_focus(subject_container_id, message_id);
CREATE TRIGGER IF NOT EXISTS message_focus_epoch_immutable
BEFORE UPDATE OF
  focus_epoch_id, focus_container_id, subject_container_id
ON message_focus
WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
  OR NEW.focus_container_id IS NOT OLD.focus_container_id
  OR NEW.subject_container_id IS NOT OLD.subject_container_id
BEGIN
  SELECT RAISE(ABORT, 'Message Focus epoch attribution is immutable');
END;
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
  -- Timeout auto-continuation chain (Phase-1 slice 5, T5). A job run that hits
  -- the per-turn quota enqueues a continuation run in the same session (and
  -- worktree, for repo jobs): continued_from_run_id links back to the timed-out
  -- run, continuation_count is this run's ordinal in the chain (0 = original
  -- turn). The chain is capped by run_continuation_limit; slice 12's satpam
  -- reads high counts as a confused-agent signal.
  continued_from_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  continuation_count INTEGER NOT NULL DEFAULT 0,
  focus_epoch_id INTEGER REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
  SELECT RAISE(ABORT, 'Master runs require captured Focus attribution');
END;
CREATE TRIGGER IF NOT EXISTS runs_focus_epoch_immutable
BEFORE UPDATE OF focus_epoch_id ON runs
WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
BEGIN
  SELECT RAISE(ABORT, 'Run Focus epoch attribution is immutable');
END;
CREATE TRIGGER IF NOT EXISTS messages_master_focus_insert
AFTER INSERT ON messages
WHEN NEW.run_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM sessions
    WHERE id = NEW.session_id AND mode = 'master'
  )
BEGIN
  INSERT OR IGNORE INTO message_focus(
    message_id, focus_epoch_id, focus_container_id, subject_container_id
  )
  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
  FROM runs AS run
  LEFT JOIN master_focus_epochs AS epoch ON epoch.id = run.focus_epoch_id
  WHERE run.id = NEW.run_id AND run.session_id = NEW.session_id;
END;
CREATE TRIGGER IF NOT EXISTS messages_master_focus_run_update
AFTER UPDATE OF run_id ON messages
WHEN NEW.run_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM sessions
    WHERE id = NEW.session_id AND mode = 'master'
  )
BEGIN
  INSERT OR IGNORE INTO message_focus(
    message_id, focus_epoch_id, focus_container_id, subject_container_id
  )
  SELECT NEW.id, run.focus_epoch_id, epoch.container_id, NULL
  FROM runs AS run
  LEFT JOIN master_focus_epochs AS epoch ON epoch.id = run.focus_epoch_id
  WHERE run.id = NEW.run_id AND run.session_id = NEW.session_id;
END;
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
  -- Status the workflow held just before it was archived, captured at archive
  -- time so restore can reinstate it (a paused template stays paused). NULL
  -- while not archived and for legacy rows archived before this column existed.
  pre_archive_status TEXT,
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
  projection_revision INTEGER NOT NULL DEFAULT 0
    CHECK (projection_revision >= 0),
  projection_state TEXT NOT NULL DEFAULT 'none' CHECK (
    projection_state IN (
      'none', 'started', 'review', 'completed', 'failed', 'cancelled', 'blocked'
    )
  ),
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
  -- Job -> target-area binding (Phase-1 slice 2, T1): the ONE container area
  -- this job works against, set before it runs. A code-area target makes it a
  -- repo job (isolated worktree + diff review + local merge); an ops-area
  -- target (or NULL, today's jobs) runs exactly as before.
  target_area_id INTEGER REFERENCES project_areas(id) ON DELETE SET NULL,
  -- Durable reason a delegated Task is queued but cannot start yet. NULL means
  -- the Task is ready, already running, or is a legacy non-delegated job.
  blocked_reason TEXT,
  -- Why the owner rejected the job at review (slice 4). Set only by the reject
  -- action (status -> 'failed'); NULL for jobs that failed on their own.
  rejected_reason TEXT,
  -- Non-NULL only when the built-in Master session dispatched this job.
  -- It scopes permission auto-approval, desk ownership, and concurrency.
  origin_master_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT,
  finished_at TEXT,
  archived_at TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_rebuild_intents (
  container_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  intent_version INTEGER NOT NULL DEFAULT 1 CHECK(intent_version > 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
END;
-- Server-owned Task creation audit. jobs remains the Task lifecycle truth; this
-- row records where the Task came from, why it was routed to one exact Area,
-- and enough durable start intent to resume safely after a process crash.
CREATE TABLE IF NOT EXISTS task_delegations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  origin_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  origin_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
  origin_focus_epoch_id INTEGER
    REFERENCES master_focus_epochs(id) ON DELETE RESTRICT,
  origin_focus_captured INTEGER NOT NULL DEFAULT 0
    CHECK (origin_focus_captured IN (0, 1)),
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
);
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
  SELECT RAISE(ABORT, 'Task delegation Focus attribution is invalid');
END;
CREATE TRIGGER IF NOT EXISTS task_delegations_focus_immutable
BEFORE UPDATE OF origin_focus_epoch_id, origin_focus_captured
ON task_delegations
WHEN NEW.origin_focus_epoch_id IS NOT OLD.origin_focus_epoch_id
  OR NEW.origin_focus_captured != OLD.origin_focus_captured
BEGIN
  SELECT RAISE(ABORT, 'Task delegation Focus attribution is immutable');
END;
CREATE INDEX IF NOT EXISTS idx_task_delegations_origin
  ON task_delegations(origin_session_id, origin_message_id);
CREATE INDEX IF NOT EXISTS idx_task_delegations_container
  ON task_delegations(container_id, target_area_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_delegations_start
  ON task_delegations(start_requested, start_state, updated_at);
-- One durable record per logical Master turn and canonical tool envelope.
-- Pending rows make crash replay safe; complete rows make duplicates visible.
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
);
CREATE INDEX IF NOT EXISTS idx_master_tool_calls_session
  ON master_tool_calls(master_session_id, turn_root_run_id, id);
-- Durable chat/event projections of authoritative Task, Attention, checkpoint,
-- supervisor, and Satpam rows. This table is an idempotency/link ledger only:
-- lifecycle truth stays in the referenced product tables.
CREATE TABLE IF NOT EXISTS master_projections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  master_session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  projection_key TEXT NOT NULL CHECK (length(projection_key) BETWEEN 1 AND 300),
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
    source_table IN ('jobs', 'attention_items', 'satpam_interventions')
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
      AND source_table = 'satpam_interventions' AND task_id IS NOT NULL)
    OR
    (projection_type IN (
      'master.attention.required',
      'master.supervisor.outcome',
      'master.satpam.recovery_failed'
    ) AND source_table = 'attention_items')
  ),
  UNIQUE(owner_user_id, projection_key)
);
CREATE INDEX IF NOT EXISTS idx_master_projections_session
  ON master_projections(master_session_id, id);
CREATE INDEX IF NOT EXISTS idx_master_projections_source
  ON master_projections(source_table, source_id, projection_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_master_projections_message
  ON master_projections(message_id) WHERE message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_master_projections_event
  ON master_projections(event_id) WHERE event_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS task_projection_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  projection_epoch INTEGER NOT NULL DEFAULT 0 CHECK (projection_epoch >= 0),
  projection_revision INTEGER NOT NULL DEFAULT 0
    CHECK (projection_revision >= 0),
  task_status TEXT NOT NULL CHECK (
    task_status IN ('queued', 'running', 'review', 'done', 'failed', 'cancelled')
  ),
  mutation TEXT NOT NULL CHECK (length(mutation) BETWEEN 1 AND 80),
  state TEXT NOT NULL DEFAULT 'pending' CHECK (
    state IN ('pending', 'projected', 'failed_attribution', 'superseded')
  ),
  projection_id INTEGER REFERENCES master_projections(id) ON DELETE SET NULL,
  superseded_by_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  failure_code TEXT CHECK (
    failure_code IS NULL OR failure_code IN (
      'focus_attribution_unavailable',
      'projection_scope_unavailable',
      'projection_failed'
    )
  ),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(task_event_id),
  CHECK (
    (state = 'pending' AND projection_id IS NULL
      AND superseded_by_event_id IS NULL
      AND (failure_code IS NULL OR failure_code = 'projection_failed'))
    OR
    (state = 'projected' AND projection_id IS NOT NULL
      AND failure_code IS NULL AND superseded_by_event_id IS NULL)
    OR
    (state = 'failed_attribution' AND projection_id IS NULL
      AND superseded_by_event_id IS NULL
      AND failure_code IN (
        'focus_attribution_unavailable', 'projection_scope_unavailable'
      ))
    OR
    (state = 'superseded' AND projection_id IS NULL
      AND superseded_by_event_id IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_task_projection_outbox_state
  ON task_projection_outbox(state, id);
CREATE TABLE IF NOT EXISTS task_recovery_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  recovery_json TEXT NOT NULL CHECK (
    length(recovery_json) BETWEEN 2 AND 16384
  ),
  state TEXT NOT NULL DEFAULT 'pending' CHECK (
    state IN (
      'pending', 'projected', 'failed_attribution', 'legacy_ordering_gap'
    )
  ),
  master_session_id INTEGER
    REFERENCES sessions(id) ON DELETE SET NULL,
  message_id INTEGER REFERENCES messages(id) ON DELETE RESTRICT,
  event_id INTEGER REFERENCES events(id) ON DELETE RESTRICT,
  ordering_successor_id INTEGER
    REFERENCES task_recovery_outbox(id) ON DELETE CASCADE,
  failure_code TEXT CHECK (
    failure_code IS NULL OR failure_code IN (
      'focus_attribution_unavailable',
      'projection_scope_unavailable',
      'projection_failed'
    )
  ),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(task_event_id),
  CHECK (
    (state = 'pending' AND message_id IS NULL AND event_id IS NULL
      AND ordering_successor_id IS NULL
      AND (failure_code IS NULL OR failure_code = 'projection_failed'))
    OR
    (state = 'projected' AND message_id IS NOT NULL AND event_id IS NOT NULL
      AND ordering_successor_id IS NULL AND failure_code IS NULL)
    OR
    (state = 'failed_attribution' AND message_id IS NULL AND event_id IS NULL
      AND ordering_successor_id IS NULL
      AND failure_code IN (
        'focus_attribution_unavailable', 'projection_scope_unavailable'
      ))
    OR
    (state = 'legacy_ordering_gap' AND message_id IS NULL
      AND event_id IS NULL AND ordering_successor_id IS NOT NULL
      AND ordering_successor_id != id AND failure_code IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_task_recovery_outbox_state
  ON task_recovery_outbox(state, task_event_id);
CREATE TRIGGER IF NOT EXISTS task_recovery_ordering_gap_immutable
BEFORE UPDATE ON task_recovery_outbox
WHEN OLD.state = 'legacy_ordering_gap'
BEGIN
  SELECT RAISE(ABORT, 'legacy recovery ordering gap is immutable');
END;
CREATE TABLE IF NOT EXISTS task_recovery_ordering_gaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  predecessor_outbox_id INTEGER NOT NULL
    REFERENCES task_recovery_outbox(id) ON DELETE CASCADE,
  successor_outbox_id INTEGER NOT NULL
    REFERENCES task_recovery_outbox(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (
    kind IN ('unpublished_predecessor', 'projected_reversal')
  ),
  predecessor_task_event_id INTEGER NOT NULL CHECK (
    predecessor_task_event_id > 0
  ),
  successor_task_event_id INTEGER NOT NULL CHECK (
    successor_task_event_id > predecessor_task_event_id
  ),
  predecessor_publication_event_id INTEGER
    REFERENCES events(id) ON DELETE RESTRICT,
  successor_publication_event_id INTEGER NOT NULL
    REFERENCES events(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(predecessor_outbox_id, successor_outbox_id, kind),
  CHECK (
    (kind = 'unpublished_predecessor'
      AND predecessor_publication_event_id IS NULL)
    OR
    (kind = 'projected_reversal'
      AND predecessor_publication_event_id IS NOT NULL
      AND predecessor_publication_event_id
        > successor_publication_event_id)
  )
);
CREATE INDEX IF NOT EXISTS idx_task_recovery_ordering_gaps_job
  ON task_recovery_ordering_gaps(job_id, id);
CREATE TRIGGER IF NOT EXISTS task_recovery_ordering_gaps_immutable
BEFORE UPDATE ON task_recovery_ordering_gaps
BEGIN
  SELECT RAISE(ABORT, 'legacy recovery ordering gap record is immutable');
END;
CREATE TABLE IF NOT EXISTS task_recovery_corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  successor_outbox_id INTEGER NOT NULL
    REFERENCES task_recovery_outbox(id) ON DELETE CASCADE,
  gap_count INTEGER NOT NULL CHECK (gap_count > 0),
  first_task_event_id INTEGER NOT NULL CHECK (first_task_event_id > 0),
  last_task_event_id INTEGER NOT NULL CHECK (
    last_task_event_id >= first_task_event_id
  ),
  first_successor_task_event_id INTEGER NOT NULL DEFAULT 1 CHECK (
    first_successor_task_event_id > 0
  ),
  last_successor_task_event_id INTEGER NOT NULL DEFAULT 1 CHECK (
    last_successor_task_event_id >= first_successor_task_event_id
  ),
  state TEXT NOT NULL DEFAULT 'pending' CHECK (
    state IN ('pending', 'projected', 'failed_attribution')
  ),
  master_session_id INTEGER
    REFERENCES sessions(id) ON DELETE SET NULL,
  message_id INTEGER REFERENCES messages(id) ON DELETE RESTRICT,
  event_id INTEGER REFERENCES events(id) ON DELETE RESTRICT,
  failure_code TEXT CHECK (
    failure_code IS NULL OR failure_code IN (
      'focus_attribution_unavailable',
      'projection_scope_unavailable',
      'projection_failed'
    )
  ),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(job_id),
  CHECK (
    (state = 'pending' AND message_id IS NULL AND event_id IS NULL
      AND (failure_code IS NULL OR failure_code = 'projection_failed'))
    OR
    (state = 'projected' AND message_id IS NOT NULL AND event_id IS NOT NULL
      AND failure_code IS NULL)
    OR
    (state = 'failed_attribution' AND message_id IS NULL AND event_id IS NULL
      AND failure_code IN (
        'focus_attribution_unavailable', 'projection_scope_unavailable'
      ))
  )
);
CREATE INDEX IF NOT EXISTS idx_task_recovery_corrections_state
  ON task_recovery_corrections(state, id);
-- Cross-Area outcomes are represented as several one-Area Tasks joined by
-- these edges. The recursive trigger makes cycle safety a database invariant,
-- including for writers that do not use TaskDelegationService.
CREATE TABLE IF NOT EXISTS task_dependencies (
  task_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  depends_on_task_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
  required_status TEXT NOT NULL DEFAULT 'done'
    CHECK (required_status IN ('review', 'done')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (task_id, depends_on_task_id),
  CHECK (task_id != depends_on_task_id)
);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_prerequisite
  ON task_dependencies(depends_on_task_id, task_id);
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
END;
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
END;
-- Isolated worktree per repo job (Phase-1 slice 2, T1): where the branch was
-- cut from (repo_path/base_branch/base_commit), where the agent works
-- (worktree_path, outside the container under <workspace_root>/worktrees/),
-- and the merge lifecycle: active -> merging -> merged, with conflict (merge
-- refused/conflicted; job parks in review) and discarded as off-ramps.
-- repo_path is denormalized so crash-leftover cleanup survives area removal.
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
  -- Push-after-merge outcome (T9, slice 11): NULL until a push is attempted;
  -- 'pushed' or 'failed' after. push_error carries the exact failing command
  -- + output for the job-level blocker card; a failed push never un-merges.
  push_status TEXT,
  push_error TEXT,
  push_remote TEXT,
  push_remote_url TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_job_worktrees_status ON job_worktrees(status);
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
  -- Decision-hold (Phase-1 slice 12, T10): a node whose agent surfaced a genuine
  -- open decision parks in 'review' with the question here; the owner's answer is
  -- stored alongside and injected into the node's re-run prompt. contract_failures
  -- counts output-contract validation failures across attempts (a satpam
  -- "confused" signal: repeated invalid output escalates).
  question TEXT,
  answer TEXT,
  contract_failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(job_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_node_states_job ON node_states(job_id, status);
-- Satpam supervision (Phase-1 slice 12, T10). satpam_watch is the watchman's
-- per-chain memory: one row per job session it has evaluated, holding the last
-- continuation turn seen, the durable progress fingerprints it compares turn to
-- turn (worktree diff signature, salvaged-output hash), the consecutive
-- no-progress counters, and a pending steer note for the next continuation.
-- Rows are bookkeeping, deleted freely on restart; interventions are the record.
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
);
-- The durable, owner-visible record of every satpam action (T10 #5: no silent
-- interventions): steer (applied automatically), restart (applied for non-repo
-- work; 'pending' = the repo-job approval card), escalate (pause + plain-language
-- card). One row per action, kept for audit after resolution.
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
);
CREATE INDEX IF NOT EXISTS idx_satpam_interventions_job ON satpam_interventions(job_id, id);
-- One-time script approvals (Phase-1 slice 6, T6): a library script runs as a
-- deterministic plan step only after the owner approved its exact bytes once.
-- One row per (project, scripts/-relative path) holding the approved sha256;
-- editing the script changes its hash, which is what forces re-approval.
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
);
-- Durable deliverable registry (Phase-1 slice 8, T4). One row per deliverable
-- VERSION: the scanner discovers files, this table remembers them - records
-- survive file moves/deletion (file_missing flips, the row stays). Identity is
-- (project, type, path); a new producer at the same identity creates v(n+1)
-- and marks prior versions superseded. status is the ONE approval field with
-- two doors: the job-review approve and the Archive page write the same value.
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
);
CREATE INDEX IF NOT EXISTS idx_artifact_records_project ON artifact_records(project_id, produced_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifact_records_identity ON artifact_records(project_id, path);
CREATE INDEX IF NOT EXISTS idx_artifact_records_job ON artifact_records(job_id);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Master checkpoints are job-scoped JSON state + git/worktree refs. They are
-- not SQLite backups and do not archive an entire project filesystem.
CREATE TABLE IF NOT EXISTS job_checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL,
  git_refs_json TEXT NOT NULL DEFAULT '[]',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_job_checkpoints_job ON job_checkpoints(job_id, created_at DESC);
CREATE TABLE IF NOT EXISTS turn_file_journals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  entries_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_turn_file_journals_session ON turn_file_journals(session_id, id);
CREATE TABLE IF NOT EXISTS attention_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  target_json TEXT NOT NULL DEFAULT '{}',
  inline_ok INTEGER NOT NULL DEFAULT 0,
  actions_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open',
  source_key TEXT UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attention_status ON attention_items(status, created_at DESC);
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


def connect(
    path: str | Path,
    *,
    read_only: bool = False,
    deny_writes: bool = False,
    writes_fenced: Callable[[], bool] | None = None,
) -> sqlite3.Connection:
    db_path = Path(path)
    dynamically_fenced = deny_writes or writes_fenced is not None
    initially_fenced = deny_writes or (
        writes_fenced is not None and writes_fenced()
    )
    connect_kwargs: dict[str, Any] = {
        "check_same_thread": False,
        "isolation_level": None,
        "cached_statements": 0 if dynamically_fenced else 128,
    }
    if read_only:
        if not db_path.is_file():
            raise FileNotFoundError("maintenance database is missing")
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            **connect_kwargs,
        )
    elif initially_fenced:
        if not db_path.is_file():
            raise FileNotFoundError("fenced database is missing")
        conn = sqlite3.connect(
            f"file:{db_path}?mode=rw",
            uri=True,
            **connect_kwargs,
        )
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, **connect_kwargs)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if dynamically_fenced:
        denied = frozenset(
            getattr(sqlite3, name)
            for name in (
                "SQLITE_ALTER_TABLE",
                "SQLITE_ANALYZE",
                "SQLITE_ATTACH",
                "SQLITE_CREATE_INDEX",
                "SQLITE_CREATE_TABLE",
                "SQLITE_CREATE_TEMP_INDEX",
                "SQLITE_CREATE_TEMP_TABLE",
                "SQLITE_CREATE_TEMP_TRIGGER",
                "SQLITE_CREATE_TEMP_VIEW",
                "SQLITE_CREATE_TRIGGER",
                "SQLITE_CREATE_VIEW",
                "SQLITE_CREATE_VTABLE",
                "SQLITE_DELETE",
                "SQLITE_DETACH",
                "SQLITE_DROP_INDEX",
                "SQLITE_DROP_TABLE",
                "SQLITE_DROP_TEMP_INDEX",
                "SQLITE_DROP_TEMP_TABLE",
                "SQLITE_DROP_TEMP_TRIGGER",
                "SQLITE_DROP_TEMP_VIEW",
                "SQLITE_DROP_TRIGGER",
                "SQLITE_DROP_VIEW",
                "SQLITE_DROP_VTABLE",
                "SQLITE_INSERT",
                "SQLITE_PRAGMA",
                "SQLITE_REINDEX",
                "SQLITE_SAVEPOINT",
                "SQLITE_TRANSACTION",
                "SQLITE_UPDATE",
            )
            if hasattr(sqlite3, name)
        )

        def authorize(
            action: int,
            _arg1: str | None,
            _arg2: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if action not in denied:
                return sqlite3.SQLITE_OK
            if deny_writes or (
                writes_fenced is not None and writes_fenced()
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(
            authorize
        )
    if not read_only and not initially_fenced:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            conn.close()
            raise
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


def backfill_project_path_identities(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if table is None:
        return
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    if not {"id", "path", "path_identity"}.issubset(columns):
        return
    for row in conn.execute(
        "SELECT id, path FROM projects "
        "WHERE path_identity IS NULL OR trim(path_identity) = ''"
    ).fetchall():
        try:
            identity = directory_identity_for_path(Path(row["path"]))
        except (OSError, RuntimeError, TypeError, ValueError):
            identity = unavailable_directory_identity(row["path"])
        conn.execute(
            "UPDATE projects SET path_identity = ? WHERE id = ?",
            (identity, row["id"]),
        )


def migrate_existing(conn: sqlite3.Connection) -> None:
    _ensure_message_reviews(conn)
    _ensure_prompt_collaborations(conn)
    _ensure_node_states(conn)
    _add_column(conn, "users", "password_hash", "password_hash TEXT")
    _add_column(conn, "users", "password_set_at", "password_set_at TEXT")
    _add_column(conn, "projects", "visibility", "visibility TEXT NOT NULL DEFAULT 'private'")
    _add_column(conn, "projects", "path_identity", "path_identity TEXT")
    backfill_project_path_identities(conn)
    _add_column(conn, "sessions", "profile_id", "profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL")
    _add_column(conn, "sessions", "visibility", "visibility TEXT NOT NULL DEFAULT 'private'")
    _add_column(conn, "sessions", "mode", "mode TEXT NOT NULL DEFAULT 'chat'")
    _add_column(conn, "sessions", "job_id", "job_id INTEGER")
    _add_column(conn, "sessions", "workflow_id", "workflow_id INTEGER")
    _add_column(conn, "sessions", "manual_title", "manual_title INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "produced_artifacts", "produced_artifacts TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "workflows", "inputs", "inputs TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "workflows", "graph", "graph TEXT")
    _add_column(conn, "workflows", "pre_archive_status", "pre_archive_status TEXT")
    # Graph engine (ADR-0001): additive, coexists with the linear cursor.
    _add_column(conn, "jobs", "engine", "engine TEXT NOT NULL DEFAULT 'linear'")
    _add_column(conn, "jobs", "graph", "graph TEXT")
    _add_column(conn, "runs", "heartbeat_at", "heartbeat_at TEXT")
    _add_column(conn, "profiles", "runner_id", f"runner_id TEXT NOT NULL DEFAULT '{FALLBACK_RUNNER}'")
    _add_column(conn, "profiles", "system_kind", "system_kind TEXT")
    _add_column(conn, "jobs", "blocked_reason", "blocked_reason TEXT")
    job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if {
        "origin_master_session_id",
        "status",
        "created_at",
    }.issubset(job_columns):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_origin_master "
            "ON jobs(origin_master_session_id, status, created_at)"
        )
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
    # Pinned push target (audit F3) - pre-existing opt-ins have no pin, so the
    # push refuses until the owner re-enables the toggle (which pins the URL).
    _add_column(conn, "project_areas", "push_remote_url", "push_remote_url TEXT")
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
