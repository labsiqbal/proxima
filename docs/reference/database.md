# Database Schema

> **GENERATED FILE — do not edit by hand.** Regenerate with `python3 scripts/gen_docs.py`.


SQLite (WAL mode). 22 tables. Applied migration version: **23**. This is the exact shape a fresh install gets from `init_db` + versioned migrations. Per-install data lives at `~/.local/share/proxima/proxima.db` (outside the repo).


## Tables

[`agent_sessions`](#agent_sessions), [`app_settings`](#app_settings), [`artifact_records`](#artifact_records), [`audit_log`](#audit_log), [`auth_sessions`](#auth_sessions), [`events`](#events), [`job_worktrees`](#job_worktrees), [`jobs`](#jobs), [`message_reviews`](#message_reviews), [`messages`](#messages), [`node_states`](#node_states), [`profiles`](#profiles), [`project_areas`](#project_areas), [`projects`](#projects), [`prompt_collaborations`](#prompt_collaborations), [`runs`](#runs), [`schedules`](#schedules), [`schema_migrations`](#schema_migrations), [`script_trust`](#script_trust), [`sessions`](#sessions), [`users`](#users), [`workflows`](#workflows)


### agent_sessions

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `session_id` | INTEGER | NO |  | PK → `sessions.id` (ON DELETE CASCADE) |
| `hermes_home` | TEXT | NO |  | PK |
| `acp_session_id` | TEXT | NO |  |  |


### app_settings

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `key` | TEXT | yes |  | PK |
| `value` | TEXT | NO |  |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### artifact_records

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | NO |  | → `projects.id` (ON DELETE CASCADE) |
| `slug` | TEXT | NO |  |  |
| `name` | TEXT | NO |  |  |
| `type` | TEXT | NO |  |  |
| `path` | TEXT | NO |  |  |
| `size` | INTEGER | yes |  |  |
| `status` | TEXT | NO | `'draft'` |  |
| `approved_at` | TEXT | yes |  |  |
| `version` | INTEGER | NO | `1` |  |
| `superseded_by` | INTEGER | yes |  | → `artifact_records.id` (ON DELETE SET NULL) |
| `session_id` | INTEGER | yes |  | → `sessions.id` (ON DELETE SET NULL) |
| `job_id` | INTEGER | yes |  | → `jobs.id` (ON DELETE SET NULL) |
| `node_id` | TEXT | yes |  |  |
| `run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `file_missing` | INTEGER | NO | `0` |  |
| `produced_at` | TEXT | NO |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_artifact_records_job` — (job_id); `idx_artifact_records_identity` — (project_id, path); `idx_artifact_records_project` — (project_id, produced_at)


### audit_log

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `actor_user_id` | INTEGER | yes |  | → `users.id` |
| `action` | TEXT | NO |  |  |
| `target_type` | TEXT | NO |  |  |
| `target_id` | TEXT | NO |  |  |
| `metadata` | TEXT | NO | `'{}'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### auth_sessions

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `token_hash` | TEXT | yes |  | PK |
| `user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `expires_at` | TEXT | yes |  |  |
| `revoked_at` | TEXT | yes |  |  |


### events

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE CASCADE) |
| `session_id` | INTEGER | yes |  | → `sessions.id` (ON DELETE CASCADE) |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `seq` | INTEGER | NO |  |  |
| `type` | TEXT | NO |  |  |
| `payload` | TEXT | NO | `'{}'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_events_run_seq` — (run_id, seq); `idx_events_session` — (session_id, id)


### job_worktrees

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `area_id` | INTEGER | yes |  | → `project_areas.id` (ON DELETE SET NULL) |
| `repo_path` | TEXT | NO |  |  |
| `worktree_path` | TEXT | NO |  |  |
| `branch` | TEXT | NO |  |  |
| `base_branch` | TEXT | NO |  |  |
| `base_commit` | TEXT | NO |  |  |
| `status` | TEXT | NO | `'active'` |  |
| `merge_commit` | TEXT | yes |  |  |
| `error` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_job_worktrees_status` — (status)


### jobs

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `workflow_id` | INTEGER | yes |  | → `workflows.id` (ON DELETE SET NULL) |
| `session_id` | INTEGER | yes |  | → `sessions.id` (ON DELETE SET NULL) |
| `title` | TEXT | NO | `''` |  |
| `status` | TEXT | NO | `'queued'` |  |
| `current_step_idx` | INTEGER | NO | `0` |  |
| `input` | TEXT | yes |  |  |
| `steps_state` | TEXT | NO | `'[]'` |  |
| `engine` | TEXT | NO | `'linear'` |  |
| `graph` | TEXT | yes |  |  |
| `schedule_id` | INTEGER | yes |  |  |
| `target_area_id` | INTEGER | yes |  | → `project_areas.id` (ON DELETE SET NULL) |
| `rejected_reason` | TEXT | yes |  |  |
| `created_by` | INTEGER | yes |  | → `users.id` |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `started_at` | TEXT | yes |  |  |
| `finished_at` | TEXT | yes |  |  |
| `archived_at` | TEXT | yes |  |  |

**Indexes:** `idx_jobs_archived` — (archived_at); `idx_jobs_workflow` — (workflow_id); `idx_jobs_project_status` — (project_id, status, created_at)


### message_reviews

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `source_message_id` | INTEGER | NO |  | → `messages.id` (ON DELETE CASCADE) |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `mode` | TEXT | NO | `'validate'` |  |
| `status` | TEXT | NO | `'queued'` |  |
| `source_runner` | TEXT | yes |  |  |
| `source_profile_id` | INTEGER | yes |  | → `profiles.id` (ON DELETE SET NULL) |
| `reviewer_profile_id` | INTEGER | yes |  | → `profiles.id` (ON DELETE SET NULL) |
| `reviewer_profiles` | TEXT | NO | `'[]'` |  |
| `verdict` | TEXT | yes |  |  |
| `gaps` | TEXT | NO | `'[]'` |  |
| `depends_on_input` | TEXT | NO | `'[]'` |  |
| `revised_content` | TEXT | yes |  |  |
| `suggested_next_move` | TEXT | yes |  |  |
| `raw_transcript` | TEXT | yes |  |  |
| `merge_transcript` | TEXT | yes |  |  |
| `source_original_content` | TEXT | yes |  |  |
| `applied_at` | TEXT | yes |  |  |
| `error` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_message_reviews_run` — (run_id); `idx_message_reviews_session` — (session_id, id); `idx_message_reviews_source` — (source_message_id, id)


### messages

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `role` | TEXT | NO |  |  |
| `content` | TEXT | NO |  |  |
| `author` | TEXT | yes |  |  |
| `run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `output_links` | TEXT | NO | `'[]'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### node_states

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `node_id` | TEXT | NO |  |  |
| `status` | TEXT | NO | `'pending'` |  |
| `run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `inputs` | TEXT | yes |  |  |
| `output_kind` | TEXT | yes |  |  |
| `output` | TEXT | yes |  |  |
| `checkpoint` | TEXT | yes |  |  |
| `error` | TEXT | yes |  |  |
| `version` | INTEGER | NO | `0` |  |
| `started_at` | TEXT | yes |  |  |
| `finished_at` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_node_states_job` — (job_id, status)


### profiles

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `slug` | TEXT | NO |  |  |
| `name` | TEXT | NO |  |  |
| `hermes_home` | TEXT | NO |  |  |
| `runner_id` | TEXT | NO | `'claude-code'` |  |
| `default_model` | TEXT | yes |  |  |
| `instructions` | TEXT | yes |  |  |
| `is_default` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `capabilities` | TEXT | yes |  |  |


### project_areas

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | NO |  | → `projects.id` (ON DELETE CASCADE) |
| `kind` | TEXT | NO | `'code'` |  |
| `rel_path` | TEXT | NO |  |  |
| `source` | TEXT | NO | `'auto'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_project_areas_one_ops` — UNIQUE (project_id); `idx_project_areas_project` — (project_id, kind)


### projects

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `slug` | TEXT | NO |  |  |
| `name` | TEXT | NO |  |  |
| `path` | TEXT | NO |  |  |
| `owner_user_id` | INTEGER | NO |  | → `users.id` |
| `visibility` | TEXT | NO | `'private'` |  |
| `archived_at` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### prompt_collaborations

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `parent_run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `mode` | TEXT | NO |  |  |
| `status` | TEXT | NO | `'queued'` |  |
| `prompt` | TEXT | NO |  |  |
| `profile_ids` | TEXT | NO | `'[]'` |  |
| `child_run_ids` | TEXT | NO | `'[]'` |  |
| `child_outputs` | TEXT | NO | `'[]'` |  |
| `synthesis_run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `final_message_id` | INTEGER | yes |  | → `messages.id` (ON DELETE SET NULL) |
| `error` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_prompt_collaborations_synthesis` — (synthesis_run_id); `idx_prompt_collaborations_parent` — (parent_run_id); `idx_prompt_collaborations_session` — (session_id, id)


### runs

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `profile_id` | INTEGER | yes |  | → `profiles.id` (ON DELETE SET NULL) |
| `runner_id` | TEXT | NO | `'claude-code'` |  |
| `kind` | TEXT | NO | `'chat'` |  |
| `status` | TEXT | NO | `'queued'` |  |
| `prompt` | TEXT | NO |  |  |
| `model` | TEXT | yes |  |  |
| `hermes_home` | TEXT | yes |  |  |
| `collaboration_id` | INTEGER | yes |  |  |
| `collaboration_role` | TEXT | yes |  |  |
| `pid` | INTEGER | yes |  |  |
| `started_at` | TEXT | yes |  |  |
| `finished_at` | TEXT | yes |  |  |
| `heartbeat_at` | TEXT | yes |  |  |
| `error` | TEXT | yes |  |  |
| `continued_from_run_id` | INTEGER | yes |  | → `runs.id` (ON DELETE SET NULL) |
| `continuation_count` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_runs_session` — (session_id, id); `idx_runs_status` — (status, id)


### schedules

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `workflow_id` | INTEGER | yes |  | → `workflows.id` (ON DELETE CASCADE) |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `cron` | TEXT | NO |  |  |
| `input` | TEXT | yes |  |  |
| `overlap_policy` | TEXT | NO | `'skip'` |  |
| `enabled` | INTEGER | NO | `1` |  |
| `last_run_minute` | TEXT | yes |  |  |
| `last_tick_at` | TEXT | yes |  |  |
| `created_by` | INTEGER | yes |  | → `users.id` |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_schedules_enabled` — (enabled)


### schema_migrations

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `version` | INTEGER | yes |  | PK |
| `description` | TEXT | yes |  |  |
| `applied_at` | TEXT | NO |  |  |


### script_trust

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | NO |  | → `projects.id` (ON DELETE CASCADE) |
| `rel_path` | TEXT | NO |  |  |
| `content_hash` | TEXT | NO |  |  |
| `approved_by` | INTEGER | yes |  | → `users.id` (ON DELETE SET NULL) |
| `approved_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### sessions

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `title` | TEXT | NO |  |  |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `owner_user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `profile_id` | INTEGER | yes |  | → `profiles.id` (ON DELETE SET NULL) |
| `runner_id` | TEXT | NO | `'claude-code'` |  |
| `visibility` | TEXT | NO | `'private'` |  |
| `mode` | TEXT | NO | `'chat'` |  |
| `job_id` | INTEGER | yes |  | → `jobs.id` (ON DELETE SET NULL) |
| `workflow_id` | INTEGER | yes |  | → `workflows.id` (ON DELETE SET NULL) |
| `manual_title` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `produced_artifacts` | TEXT | NO | `'[]'` |  |
| `goal_text` | TEXT | yes |  |  |
| `goal_status` | TEXT | yes |  |  |
| `goal_iteration` | INTEGER | NO | `0` |  |
| `goal_max` | INTEGER | NO | `20` |  |

**Indexes:** `idx_sessions_project` — (project_id, updated_at); `idx_sessions_owner` — (owner_user_id, updated_at)


### users

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `username` | TEXT | NO |  |  |
| `os_user` | TEXT | NO |  |  |
| `role` | TEXT | NO | `'member'` |  |
| `password_hash` | TEXT | yes |  |  |
| `password_set_at` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### workflows

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `name` | TEXT | NO |  |  |
| `description` | TEXT | NO | `''` |  |
| `category` | TEXT | NO | `'other'` |  |
| `status` | TEXT | NO | `'active'` |  |
| `steps` | TEXT | NO | `'[]'` |  |
| `graph` | TEXT | yes |  |  |
| `inputs` | TEXT | NO | `'[]'` |  |
| `created_by` | INTEGER | yes |  | → `users.id` |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_workflows_project` — (project_id, status)


---
_Generated 2026-07-22 07:45 UTC._
