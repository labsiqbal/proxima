# Database Schema

> **GENERATED FILE - do not edit by hand.** Regenerate with `python3 scripts/gen_docs.py`.


SQLite (WAL mode). 36 tables. Applied migration version: **37**. This is the exact shape a fresh install gets from `init_db` + versioned migrations. Per-install data lives at `~/.local/share/proxima/proxima.db` (outside the repo).


## Tables

[`agent_sessions`](#agent_sessions), [`app_settings`](#app_settings), [`artifact_records`](#artifact_records), [`attention_items`](#attention_items), [`audit_log`](#audit_log), [`auth_sessions`](#auth_sessions), [`container_ops_migrations`](#container_ops_migrations), [`container_registry`](#container_registry), [`events`](#events), [`graph_states`](#graph_states), [`job_checkpoints`](#job_checkpoints), [`job_worktrees`](#job_worktrees), [`jobs`](#jobs), [`knowledge_rebuild_intents`](#knowledge_rebuild_intents), [`master_message_context`](#master_message_context), [`master_projections`](#master_projections), [`master_tool_calls`](#master_tool_calls), [`message_reviews`](#message_reviews), [`messages`](#messages), [`node_states`](#node_states), [`profiles`](#profiles), [`project_areas`](#project_areas), [`projects`](#projects), [`prompt_collaborations`](#prompt_collaborations), [`runs`](#runs), [`satpam_interventions`](#satpam_interventions), [`satpam_watch`](#satpam_watch), [`schedules`](#schedules), [`schema_migrations`](#schema_migrations), [`script_trust`](#script_trust), [`sessions`](#sessions), [`task_delegations`](#task_delegations), [`task_dependencies`](#task_dependencies), [`turn_file_journals`](#turn_file_journals), [`users`](#users), [`workflows`](#workflows)


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

**Indexes:** `idx_artifact_records_job` - (job_id); `idx_artifact_records_identity` - (project_id, path); `idx_artifact_records_project` - (project_id, produced_at)


### attention_items

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `kind` | TEXT | NO |  |  |
| `title` | TEXT | NO |  |  |
| `target_json` | TEXT | NO | `'{}'` |  |
| `inline_ok` | INTEGER | NO | `0` |  |
| `actions_json` | TEXT | NO | `'[]'` |  |
| `status` | TEXT | NO | `'open'` |  |
| `source_key` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `resolved_at` | TEXT | yes |  |  |

**Indexes:** `idx_attention_status` - (status, created_at)


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


### container_ops_migrations

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `container_id` | INTEGER | yes |  | PK → `projects.id` (ON DELETE CASCADE) |
| `migration_version` | INTEGER | NO |  |  |
| `status` | TEXT | NO | `'pending'` |  |
| `manifest_json` | TEXT | yes |  |  |
| `manifest_hash` | TEXT | yes |  |  |
| `last_error` | TEXT | yes |  |  |
| `started_at` | TEXT | yes |  |  |
| `completed_at` | TEXT | yes |  |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_container_ops_migrations_status` - (status, updated_at)


### container_registry

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `container_id` | INTEGER | yes |  | PK → `projects.id` (ON DELETE CASCADE) |
| `identity_label` | TEXT | yes |  |  |
| `summary` | TEXT | yes |  |  |
| `source_hash` | TEXT | yes |  |  |
| `indexed_at` | TEXT | yes |  |  |
| `last_activity_at` | TEXT | yes |  |  |

**Indexes:** `idx_container_registry_activity` - (last_activity_at, container_id)


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

**Indexes:** `idx_events_run_seq` - (run_id, seq); `idx_events_session` - (session_id, id)


### graph_states

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `container_id` | INTEGER | NO |  | → `projects.id` (ON DELETE CASCADE) |
| `area_id` | INTEGER | yes |  | → `project_areas.id` (ON DELETE CASCADE) |
| `kind` | TEXT | NO |  |  |
| `root_path` | TEXT | NO |  |  |
| `graph_path` | TEXT | NO |  |  |
| `source_fingerprint` | TEXT | yes |  |  |
| `graph_sha256` | TEXT | yes |  |  |
| `tool_version` | TEXT | yes |  |  |
| `semantic_backend` | TEXT | NO | `'disabled'` |  |
| `state` | TEXT | NO | `'missing'` |  |
| `generation` | INTEGER | NO | `0` |  |
| `last_success_at` | TEXT | yes |  |  |
| `last_attempt_at` | TEXT | yes |  |  |
| `last_error` | TEXT | yes |  |  |
| `repo_head` | TEXT | yes |  |  |
| `pending_base_commit` | TEXT | yes |  |  |
| `pending_head_commit` | TEXT | yes |  |  |
| `rebuild_reason` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_graph_states_container` - (container_id, state, kind, area_id); `uq_graph_states_code` - UNIQUE (container_id, area_id, kind); `uq_graph_states_knowledge` - UNIQUE (container_id, kind)


### job_checkpoints

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `payload_json` | TEXT | NO |  |  |
| `git_refs_json` | TEXT | NO | `'[]'` |  |
| `pinned` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_job_checkpoints_job` - (job_id, created_at)


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
| `push_status` | TEXT | yes |  |  |
| `push_error` | TEXT | yes |  |  |
| `push_remote` | TEXT | yes |  |  |
| `push_remote_url` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_job_worktrees_status` - (status)


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
| `blocked_reason` | TEXT | yes |  |  |
| `rejected_reason` | TEXT | yes |  |  |
| `origin_master_session_id` | INTEGER | yes |  | → `sessions.id` (ON DELETE SET NULL) |
| `created_by` | INTEGER | yes |  | → `users.id` |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `started_at` | TEXT | yes |  |  |
| `finished_at` | TEXT | yes |  |  |
| `archived_at` | TEXT | yes |  |  |

**Indexes:** `idx_jobs_origin_master` - (origin_master_session_id, status, created_at); `idx_jobs_archived` - (archived_at); `idx_jobs_workflow` - (workflow_id); `idx_jobs_project_status` - (project_id, status, created_at)


### knowledge_rebuild_intents

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `container_id` | INTEGER | yes |  | PK → `projects.id` (ON DELETE CASCADE) |
| `reason` | TEXT | NO |  |  |
| `intent_version` | INTEGER | NO | `1` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


### master_message_context

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `message_id` | INTEGER | yes |  | PK → `messages.id` (ON DELETE CASCADE) |
| `focus_mode` | TEXT | NO |  |  |
| `focus_container_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `target_mode` | TEXT | NO |  |  |
| `target_container_id` | INTEGER | yes |  | → `projects.id` (ON DELETE SET NULL) |
| `target_area_id` | INTEGER | yes |  | → `project_areas.id` (ON DELETE SET NULL) |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_master_message_context_target` - (target_container_id, target_area_id, message_id); `idx_master_message_context_focus` - (focus_container_id, message_id)


### master_projections

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `owner_user_id` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `master_session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `projection_key` | TEXT | NO |  |  |
| `projection_type` | TEXT | NO |  |  |
| `source_table` | TEXT | NO |  |  |
| `source_id` | INTEGER | NO |  |  |
| `task_id` | INTEGER | yes |  | → `jobs.id` (ON DELETE SET NULL) |
| `message_id` | INTEGER | yes |  | → `messages.id` (ON DELETE RESTRICT) |
| `event_id` | INTEGER | yes |  | → `events.id` (ON DELETE RESTRICT) |
| `payload_json` | TEXT | NO | `'{}'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `uq_master_projections_event` - UNIQUE (event_id); `uq_master_projections_message` - UNIQUE (message_id); `uq_master_projections_source_type` - UNIQUE (owner_user_id, source_table, source_id, projection_type); `idx_master_projections_source` - (source_table, source_id, projection_type); `idx_master_projections_session` - (master_session_id, id)


### master_tool_calls

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `master_session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `turn_root_run_id` | INTEGER | NO |  | → `runs.id` (ON DELETE CASCADE) |
| `envelope_hash` | TEXT | NO |  |  |
| `tool_name` | TEXT | NO |  |  |
| `status` | TEXT | NO | `'pending'` |  |
| `result_json` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `completed_at` | TEXT | yes |  |  |

**Indexes:** `idx_master_tool_calls_session` - (master_session_id, turn_root_run_id, id)


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

**Indexes:** `idx_message_reviews_run` - (run_id); `idx_message_reviews_session` - (session_id, id); `idx_message_reviews_source` - (source_message_id, id)


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
| `question` | TEXT | yes |  |  |
| `answer` | TEXT | yes |  |  |
| `contract_failures` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_node_states_job` - (job_id, status)


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
| `system_kind` | TEXT | yes |  |  |
| `is_default` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `capabilities` | TEXT | yes |  |  |

**Indexes:** `idx_profiles_one_master` - UNIQUE (user_id)


### project_areas

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `project_id` | INTEGER | NO |  | → `projects.id` (ON DELETE CASCADE) |
| `kind` | TEXT | NO | `'code'` |  |
| `rel_path` | TEXT | NO |  |  |
| `source` | TEXT | NO | `'auto'` |  |
| `push_on_merge` | INTEGER | NO | `0` |  |
| `push_remote_url` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_project_areas_one_ops` - UNIQUE (project_id); `idx_project_areas_project` - (project_id, kind)


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

**Indexes:** `idx_prompt_collaborations_synthesis` - (synthesis_run_id); `idx_prompt_collaborations_parent` - (parent_run_id); `idx_prompt_collaborations_session` - (session_id, id)


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

**Indexes:** `idx_runs_session` - (session_id, id); `idx_runs_status` - (status, id)


### satpam_interventions

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `node_id` | TEXT | yes |  |  |
| `action` | TEXT | NO |  |  |
| `detection` | TEXT | NO |  |  |
| `status` | TEXT | NO | `'applied'` |  |
| `reason` | TEXT | NO |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `resolved_at` | TEXT | yes |  |  |

**Indexes:** `idx_satpam_interventions_job` - (job_id, id)


### satpam_watch

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `node_id` | TEXT | yes |  |  |
| `last_turn` | INTEGER | NO | `0` |  |
| `diff_signature` | TEXT | yes |  |  |
| `stall_turns` | INTEGER | NO | `0` |  |
| `output_signature` | TEXT | yes |  |  |
| `loop_turns` | INTEGER | NO | `0` |  |
| `steer_count` | INTEGER | NO | `0` |  |
| `steer_pending` | TEXT | yes |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |


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

**Indexes:** `idx_schedules_enabled` - (enabled)


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

**Indexes:** `idx_sessions_one_master` - UNIQUE (owner_user_id); `idx_sessions_project` - (project_id, updated_at); `idx_sessions_owner` - (owner_user_id, updated_at)


### task_delegations

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `origin_session_id` | INTEGER | yes |  | → `sessions.id` (ON DELETE SET NULL) |
| `origin_message_id` | INTEGER | yes |  | → `messages.id` (ON DELETE SET NULL) |
| `container_id` | INTEGER | NO |  | → `projects.id` (ON DELETE RESTRICT) |
| `target_area_id` | INTEGER | NO |  | → `project_areas.id` (ON DELETE RESTRICT) |
| `job_id` | INTEGER | NO |  | → `jobs.id` (ON DELETE CASCADE) |
| `routing_mode` | TEXT | NO |  |  |
| `routing_reason` | TEXT | yes |  |  |
| `created_by` | INTEGER | NO |  | → `users.id` (ON DELETE CASCADE) |
| `idempotency_key` | TEXT | NO |  |  |
| `idempotency_identity` | TEXT | NO |  |  |
| `request_fingerprint` | TEXT | NO |  |  |
| `start_requested` | INTEGER | NO | `0` |  |
| `start_state` | TEXT | NO | `'pending'` |  |
| `blocked_reason` | TEXT | yes |  |  |
| `last_start_error` | TEXT | yes |  |  |
| `start_attempts` | INTEGER | NO | `0` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `start_attempted_at` | TEXT | yes |  |  |
| `started_at` | TEXT | yes |  |  |

**Indexes:** `idx_task_delegations_start` - (start_requested, start_state, updated_at); `idx_task_delegations_container` - (container_id, target_area_id, created_at); `idx_task_delegations_origin` - (origin_session_id, origin_message_id)


### task_dependencies

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `task_id` | INTEGER | NO |  | PK → `jobs.id` (ON DELETE CASCADE) |
| `depends_on_task_id` | INTEGER | NO |  | PK → `jobs.id` (ON DELETE RESTRICT) |
| `required_status` | TEXT | NO | `'done'` |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_task_dependencies_prerequisite` - (depends_on_task_id, task_id)


### turn_file_journals

| Column | Type | Null | Default | Key / FK |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | yes |  | PK |
| `message_id` | INTEGER | NO |  | → `messages.id` (ON DELETE CASCADE) |
| `session_id` | INTEGER | NO |  | → `sessions.id` (ON DELETE CASCADE) |
| `entries_json` | TEXT | NO |  |  |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_turn_file_journals_session` - (session_id, id)


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
| `pre_archive_status` | TEXT | yes |  |  |
| `steps` | TEXT | NO | `'[]'` |  |
| `graph` | TEXT | yes |  |  |
| `inputs` | TEXT | NO | `'[]'` |  |
| `created_by` | INTEGER | yes |  | → `users.id` |
| `created_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |
| `updated_at` | TEXT | NO | `CURRENT_TIMESTAMP` |  |

**Indexes:** `idx_workflows_project` - (project_id, status)


---
_Generated 2026-07-28 15:15 UTC._
