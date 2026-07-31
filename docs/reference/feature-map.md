# Feature Map

> Per-feature catalog: **what it is · where the code lives · what it touches · what it relates to · status**.
> The "what do I edit when I add/change X" layer on top of the generated
> [`api.md`](api.md) (routes) and [`database.md`](database.md) (schema), and the flow diagrams in
> [`architecture.md`](architecture.md). Feature *descriptions* live in [`../CAPABILITIES.md`](../CAPABILITIES.md);
> this doc adds the systematic code-location + relations + status/flag grid.
>
> Snapshot: v1.0.0 · 2026-07-12. An explorable version is published as the **Proxima Feature Map** artifact.

**Status legend** — `active` shipped & wired · `gated` behind a `PROXIMA_FEATURE_*` flag · `dead` code/column present but not reached by live UI · `risk` active but has a known consistency hazard (see notes).

**Layers** — [Core / Kernel](#core--kernel) · [Main Chat surfaces](#main-chat-surfaces) · [Feature modules](#feature-modules) · [Home & Activity cards](#home--activity-cards).

---

## Core / Kernel

The foundation everything leans on. Least allowed to change casually.

| Feature | Status | Backend | Frontend | Tables | Relates to |
| --- | --- | --- | --- | --- | --- |
| Auth & Session Tokens | active | `routes/auth.py`, `route_deps.py` (`current_user`), `main.py` (`/api/preview-auth`) | `screens/AuthGate.tsx`, `api/client.ts`, `App.tsx` | `users`, `auth_sessions` | gates every route, Preview Proxy |
| Health / Config / Feature Flags | active | `main.py` (`/api/health`, `/api/config`), `features.py` | `features.ts`, `api/config.ts` | — | Design Studio / graph gates |
| Projects & FS Linking | active | `routes/projects.py`, `project_browse.py`, `directory_handles.py`, `container_registry.py` (path identity), migration 44 | `components/projects/FolderLinker.tsx`, `screens/WorkspaceOnboarding.tsx`, `screens/ProjectsScreen.tsx`, `api/projects.ts` | `projects` (`path_identity`) | files, tasks, wiki, apps, workflows |
| Container Fleet, Areas & physical Ops storage | active | `container_registry.py` (projection, Live-state Fleet query, root resolver + legacy-Ops migration), `routes/containers.py` (public Fleet/detail/Areas), `project_areas.py`, `routes/projects.py` (compatibility readers + Area mutation), migrations 18 + 28 | `api/containers.ts` + canonical `Container` type; current UI remains on the `Project` compatibility type | `projects`, `project_areas`, `container_registry`, `container_ops_migrations`, live joins over `jobs`, `sessions`, `attention_items` | Fleet stays independent of graph availability; scoped graph state has a separate gated route |
| Profiles / Runners / Commands | active | `routes/profiles.py`, `runners.py`, `runner_specs.py`, `commands.py`, `capabilities.py` | `screens/ProfilesScreen.tsx`, `RunnersScreen.tsx` | `profiles` | runs pick profile→runner→home |
| Capability bundle (T8: bundled skills + tool advertisement) | active | `capabilities.py` (`detect_bundled_skills`), `recommended_tools.py`, `routes/profiles.py` (`/api/tools/recommended`), `wiki_memory.py` (discipline pack + tools block), repo `bundled-skills/` | `SettingsScreen.tsx` (RecommendedToolsPanel), `ProfilesScreen.tsx` (bundled group in caps modal) | `profiles.capabilities` (opt-out) | run preamble; live-home = no-op |
| Sessions & Messages | active | `routes/chat.py` (list/create/patch/delete) | `api/sessions.ts`, `ChatScreen.tsx` | `sessions`, `messages`, `agent_sessions` | runs, ACP, collab, reviews, goal |
| Master persistence identity | active (runtime flag defaults off) | `master_persistence.py`, migration 31, `master_runtime.py` provisioning, `routes/master.py` + deprecated `routes/alpha.py` import alias | `MasterScreen.tsx`, `api/master.ts` legacy reader | `profiles.system_kind`, `sessions.mode`, `jobs.origin_master_session_id` | one hidden profile + project-unbound session; in-place Alpha compatibility |
| Run Lifecycle (engine) | active | `worker.py` (RunWorker), `acp.py`, `run_prompting/outputs/summaries/advancers/drafts/state.py` | `hooks/useRunStream.ts` | `runs`, `events`, `messages` | EventHub, collab, reviews, goal, workflow |
| **Event Log & Streaming** | **risk** | `event_hub.py`, `worker.add_event`, `routes/chat.py` streams | `useRunStream.ts`, `useEventStream.ts` | `events` | every streaming surface |
| Reaper / Orphan Reclaim | active | `run_reaper.py`, `worker._fail_interrupted`, `provisioning.py` | — | `runs`, `jobs` | run lifecycle, scheduler |
| Audit & Debug | active | `routes/admin.py` | `SettingsScreen.tsx` (Diagnostics) | `audit_log` | run lifecycle, jobs |
| Provisioning & Migrations | active | `provisioning.py`, `migrations.py`, `db.py`, `profile_seed.py` | — | `schema_migrations`, all | startup |

**Notes**

- *Event Log & Streaming* — `events.id` (autoincrement) is the de-facto global cursor and SSE resumes correctly via `after_id`. But **WS reconnect hardcodes `last_id=0`** → replays the whole session (duplicate transcript), and `worker.add_event` **drops streaming events once a run leaves `running`** (cancel mid-stream loses text). `seq = MAX(seq)+1` is race-safe only by the single-writer + `db_lock` convention.
- *Reaper* — stale-run reaping is purely time-based (60s heartbeat), no lease → a genuinely-live long run can be false-positive killed under CPU/lock contention.
- *Provisioning & Migrations* — mixed strategy (`SCHEMA` + idempotent `_add_column` each boot). Missing FKs on `messages.run_id`, `sessions.task_id/job_id/workflow_id`.

---

## Main Chat surfaces

The primary gate. Everything reachable from a chat. This is the surface that keeps breaking on feature adds — the isolation target.

| Feature | Status | Backend | Frontend | Relates to |
| --- | --- | --- | --- | --- |
| Composer / input + `@` project-file & artifact references | active | `routes/files.py` (`reference-files`, `artifacts`), `fsapi.py`; `routes/chat.py` media vision | `components/chat/Composer.tsx`, `MentionTextarea.tsx`, `useProjectMentionItems.ts` | core chat/runs, projects/files |
| File attach / upload | active | `routes/files.py` (upload) | `Composer.tsx`, `api/files.ts` | files, artifacts |
| Slash commands | active | `commands.py`, `/api/commands/catalog` | `Composer.tsx`, `ChatScreen.tsx` (`localCommandReply` + agent-command pass-through) | sessions, projects, runners |
| Masterplan command (`/masterplan`) | active | `commands.py` (agent-turn expansion), `routes/chat.py` (run routing), `run_prompting.py` + `worker.py` (required `bundled/masterplan` activation) | `Composer.tsx` (catalog palette), `ChatScreen.tsx` (agent-turn pass-through) | sessions, messages, runs, profile capability selection (read-only) |
| Chat modes (Normal/Brainstorm/Debate) | active | `routes/chat.py` + `chat_collaboration.py` | `Composer.tsx` (`MODES`) | collaborations |
| **Brainstorm entry** | **risk** | `routes/chat.py` (`_start_prompt_collaboration`), `prompt_collaborations.py` | `ChatThread.tsx` (CollaborationCards) | run lifecycle, profiles |
| Debate entry | active | `routes/chat.py` (debate branch) | `ChatThread.tsx` | collaborations |
| **Interactive Form** (`<question-form>`) | active | `routes/chat.py` (`list_messages` synth step) | `QuestionForm.tsx`, `questionForm.ts`, `ChatThread.tsx` | core chat (clarifying UX) |
| Validate sidecar | active | `routes/reviews.py`, `message_reviews.py` | `MessageReviewSidecar.tsx`, `api/messageReviews.ts` | profiles/runners |
| **Cancel run** | **risk** | `routes/chat.py` (`cancel_run`) | `ChatScreen.tsx` (`stopRun`) | jobs/runs, collab, tasks |
| Streaming deltas / smooth reveal | active | SSE/WS streams | `ChatThread.tsx` (StreamingBubble) | runs/worker |
| Tool-call activity | active | `routes/chat.py` (`_run_activity`) | `ChatThread.tsx` (ActivityPanel) | runs/worker, jobs |
| Approval / permission cards | active | `routes/chat.py` (`respond_permission`), `worker.resolve_permission` | `ChatThread.tsx` (ApprovalCard), global `AttentionInbox` for job sessions | run lifecycle, ACP, attention |
| Turn file restore | active | `turn_restore.py`, `worker.py` (ACP tool-event journal), `routes/master.py` (preview/restore) | `ChatThread.tsx` (`TurnRestoreButton`) | normal project Chat, session-lifetime retention |
| Quick-reply buttons | active | — (FE parse) | `ChatThread.tsx` (`parseChoices`) | core chat |
| Result cards / output links | active | `routes/chat.py` (`_merge_session_artifact`) | `ChatThread.tsx` (ResultCards) | artifacts, studios |
| Bridge → Design Studio | gated | `api/files` designFromImage | `ChatThread.tsx` | Design Studio |
| Media generation (`/image`) | active | `routes/chat.py` (`_maybe_complete_chat_media`), `image_providers.py` | `Composer.tsx` (Generate) | Design Studio |
| Distill to wiki | active | `routes/chat.py` (`wiki_note_draft/commit`), `wiki_memory.py` | `ChatScreen.tsx`, `WikiNotePreview.tsx` | wiki |
| Distill to workflow | active | `routes/chat.py` (`promote_workflow`), `workflows.py` | `ConvertToWorkflowButton.tsx` | workflows |
| Workflow iterate / Run recipe | active | `routes/chat.py` (instant_result branch) | `ChatScreen.tsx`, `IterateStage.tsx` | workflows |
| **Goal loop** (`/goal`) | **risk** | `routes/chat.py` (`start_goal`/`cancel_goal`), `goal_loop.py` | `ChatScreen.tsx` (goalBanner) | run lifecycle |
| Model / profile picker | active | `routes/chat.py` (`update_session`) | `ChatScreen.tsx`, `api/sessions.ts` | profiles/runners |
| Session create / list / search | active | `routes/chat.py` (`create_session`/`list_sessions`/`search`) | `ChatScreen.tsx`, `SearchModal.tsx` | projects, tasks |
| Retry / edit message | dead | — | — (not in ChatThread) | Validate sidecar |
| Reasoning-token panel | dead | — | — | — |

**Notes**

- *Brainstorm* — setup publishes children before `child_run_ids` is persisted → synthesis can fire after one child; double-writer on `child_run_ids` can be overwritten.
- *Cancel run* — runs on the request thread with no `db_lock`/transaction, races the worker → a cancelled collaboration can keep running and flip back to `done`.
- *Goal loop* — advance-vs-cancel TOCTOU: advancer reads `goal_status='running'` then writes blind, can overwrite a concurrent cancel and spawn one extra turn.
- *Validate sidecar* — schema `mode` allows `validate|brainstorm|debate|compare` but UI only sends `validate`; the other three are dead.
- *Session list* — `list_sessions` self-heals orphan task threads (evidence the `task_id` invariant is expected to break); the `job_id IS NULL` filter means the jobs feature decides what shows in the main chat list.
- `sessions.mode` values: `chat` (main chat, the only mode `list_sessions` returns) · `master` (built-in orchestrator desk, server-created and excluded) · `design` (Design Studio, gated) · `video` is **not** a real mode (schema forbids it; a defensive filter only). Task / workflow-iterate / job threads are distinguished by the `workflow_id` / `job_id` columns, not `mode` - all excluded from the main chat list.

---

## Feature modules

Larger capabilities that stand as modules. Target state: each touches core only through a contract, never core tables directly.

| Feature | Status | Backend | Frontend | Tables | Relates to |
| --- | --- | --- | --- | --- | --- |
| Workflows & Jobs | active | `routes/work.py`, `routes/graph.py`, `workflows.py`, `graph.py`, `graph_executor.py`, `graph_advancers.py`, `run_advancers.py`, `run_projection.py` | `WorkflowsScreen.tsx`, `ActivityScreen.tsx`, `GraphScreen.tsx`, `graphLayout.ts`, `lib/runProjection.ts` | `workflows`, `jobs`, `node_states`, `sessions`, `runs` | scheduler, run lifecycle; one API `run_projection` for status/start/finish/duration across Workflows, Tasks, Attention, and expanded nodes |
| Master desk + restricted product tools | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `master_runtime.py`, `master_tool_broker.py`, `codex_master_proxy.py`, `codex_appserver.py`, `routes/master.py`, `worker.py` | `MasterStateProvider.tsx`, shared `components/master/*`, `MasterScreen.tsx`, `api/master.ts`, Sidebar | `master_tool_calls`, `jobs.origin_master_session_id`, `app_settings`, `audit_log` | one authenticated provider owns the durable session/thread/composer/SSE cursor; Codex 0.145+ chat-only adapter; other runners fail closed |
| Master Focus epochs + runtime isolation | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `master_focus.py`, `routes/master.py`, shared generic-run guard in `route_deps.py`, `run_prompting.py`, `worker.py`, `master_projection.py`, migrations 38-42 | `MasterStateProvider.tsx`, `masterHistory.ts`, `MasterTargetPicker.tsx`, `MasterScreen.tsx`, `api/master.ts`, `masterProjection.ts` | `master_focus_epochs`, `master_focus_state`, `message_focus`, `master_message_context`, `runs.focus_epoch_id`, `task_delegations.origin_focus_epoch_id` | one Master session; optimistic boundary events; one pending Focus; atomic explicit sends; per-turn runner recycle; exact Roving/Fleet/Container history projections from immutable attribution; explicit shell bridge only; durable delayed-projection attribution |
| Master queue supervision + durable Task/Satpam projection | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `master_supervisor.py`, `master_projection.py`, `task_state_events.py`, `run_projection.py`, `event_types.py`, `task_delegation.py`, `satpam.py`, `worker.py`, migrations 45-53, ADR-0027 | `MasterStateProvider.tsx` consumes typed session SSE; shared conversation/work consumers render once; `MasterPopup.tsx` reuses the thread/composer; `MasterToastRegion.tsx` maps named events; `TaskWorkspace.tsx` consumes Task-session `job.update` | `master_projections` plus ordered `task_projection_outbox` / `task_recovery_outbox` and recovery gap/correction/tombstone history linked to authoritative `jobs`, `job_checkpoints`, `attention_items`, `satpam_interventions`, `messages`, `events` | ordered exactly-once projection after commit; recovery-only reconnect/gap repair; Master starts eligible queued work; Satpam alone detects/steers/restarts/escalates |
| Scoped Graphify adapter + graph state | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `graph_context.py`, `routes/graphs.py`, `event_types.py`, migration 35 | existing Master session SSE recognizes graph state events; no graph UI | `graph_states`, `events` | explicit path-free Container/Area rebuild and state read; local structural extraction; atomic last-good generations; semantic egress off |
| Code graph lifecycle | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `code_graph_lifecycle.py`, `graph_context.py`, `graphify_area_mcp.py`, `capabilities.py`, area/merge hooks, migration 36 | background tick drains queue / audit / dirty debounce; repo Task MCP fixed per Area | `graph_states` (+ `repo_head`, pending commits, `rebuild_reason`) | per-Area Code graphs; register + merge + external drift; incremental when safe; last-good preserve; no worktree promotion; MCP `project_path` ignored |
| Knowledge graph lifecycle + context router | gated (`PROXIMA_FEATURE_MASTER_ORCHESTRATOR`, off by default) | `knowledge_graph_lifecycle.py`, `context_router.py`, `graph_context.py` allowlist, `master_tool_broker.query_context`, `routes/projects.py`, migration 37 | Master settings `graph_policy` (local-only); no graph UI | `graph_states`, `knowledge_rebuild_intents`, `jobs` completion trigger | one Knowledge graph per Ops area; allowlisted durable Ops only; debounce/audit/scheduled full rebuild; typed multi-layer router without fleet graph merge; live state independent of graphs |
| Job-scoped checkpoints | active | `job_checkpoints.py`, `task_state_events.py`, `routes/master.py`, `routes/work.py` | `MasterScreen.tsx` checkpoint timeline; `TaskWorkspace.tsx` restore invalidation | `job_checkpoints`, `task_recovery_outbox` | Master jobs, git/worktrees; bounded recovery history; no DB/FS archive |
| Global Attention inbox | active | `routes/master.py`, `worker.py` (permission materialization/close), `run_projection.py` | `AttentionInbox.tsx`, `AppShell.tsx`, `lib/runProjection.ts` | `attention_items` + projected job/satpam rows with canonical `run_projection` | Tasks, Master, satpam, ACP permissions |
| Master core/full tours | active | `routes/master.py` settings state | `CoreTour.tsx`, `SettingsScreen.tsx` Help chapters | `app_settings` | feature-aware shell education |
| Linux-first daily-driver support | active (Linux supported; macOS/Windows experimental) | `platform_support.py`, existing `/api/config` and `/api/health`, `routes/admin.py`, `scripts/install-*`, `scripts/proxima`, `scripts/linux-daily-driver-acceptance` | `SettingsScreen.tsx`, `api/platformSupport.ts` | none | install, systemd lifecycle, PTY, backup/restore, diagnostics, preview, local/Tailscale access, fail-closed upgrade readiness; ADR-0028 |
| Repo-job worktrees + review/merge UI | active (flag `PROXIMA_FEATURE_REPO_WORKTREES` on by default; off = escape hatch) | `worktrees.py`, `routes/work.py` (start/diff/approve/reject/delete), `routes/graph.py` (plan start/approve), `worker.py` (cwd seam), migrations 19+20 | `components/tasks/ChangesReview.tsx` + `diff.ts` (review surface), `ActivityScreen.tsx` (plan expanding row), `TaskWorkspace.tsx` (full-width page), `GraphScreen.tsx` (approve label) | `job_worktrees`, `jobs.target_area_id`, `jobs.rejected_reason`, `project_areas` | work-container areas, run lifecycle, slice-5 continuation, slice-12 satpam (consumes review states) |
| Timeout auto-continuation + turn quota setting | active | `worker.py` (`_continue_after_timeout` + timeout handler), `app_settings.py` (run-timeout helpers), `routes/files.py` (`/api/settings/runs`), `workflows.py` (continuation prompt, slicer sizing rule), migration 21 | `SettingsScreen.tsx` (Turn quota panel), `api/settings.ts` | `runs.continued_from_run_id` / `runs.continuation_count`, `app_settings`, `node_states` (run-id re-attach) | run lifecycle, repo-job worktrees (same-worktree resume), slice-12 satpam (reads continuation counts as a confused signal) |
| Satpam supervision loop + decision-hold | active | `satpam.py` (fleet loop, detection + action ladders), `worker.py` (loop cadence, steer consumption, cap escalation), `graph_advancers.py` (DECISION_NEEDED park, contract-failure strikes, drain rule), `graph_executor.py` (marker instruction + owner-decision prompt), `worktrees.py` (`work_signature`, `recut_job_worktree`), `routes/work.py` (restart approve/dismiss), `routes/graph.py` (node answer), `routes/files.py` (`/api/settings/satpam`), migration 25 | `components/tasks/SatpamCard.tsx` (approval card + watchdog log), `TaskWorkspace.tsx`, `GraphScreen.tsx` (card + decision-answer), `ActivityScreen.tsx` (needs-answer chip), `SettingsScreen.tsx` (Watchdog panel) | `satpam_watch`, `satpam_interventions`, `node_states.question/answer/contract_failures` | run lifecycle, slice-5 continuation (turn boundaries + steer seam), repo-job worktrees (signatures + gated restart), review states |
| **Cron Scheduling** | **risk** | `routes/work.py`, `scheduler.py`, `main.py` loop | `WorkflowsScreen.tsx` | `schedules`, `jobs`, `workflows` | workflows/jobs |
| Tasks (ad-hoc jobs) | active | `routes/work.py`, `task_state_events.py`, `run_projection.py` | `TaskComposer.tsx`, `TaskWorkspace.tsx`, `ActivityScreen.tsx`, `lib/runProjection.ts` | `jobs`, `sessions`, `runs`, `task_projection_outbox` | run lifecycle, review gates; mounted detail invalidates on `job.update` |
| **Durable Task delegation & dependencies** (Master slice 2A) | active | `task_delegation.py` (`TaskDelegationService`), `task_state_events.py`, `routes/work.py` (create/start/approve/reject/delete), `routes/graph.py` (approve), `master_runtime.py` (batch dispatch), `run_advancers.py` + `graph_advancers.py` (`prerequisite_changed` + landing status), `main.py` (`resume_committed` at startup), migrations 29+30 and 45-53, ADR-0027 | `TaskWorkspace.tsx` (blocked-reason banner + external mutation refresh), `types.ts` (`delegation`/`dependencies`) | `task_delegations`, `task_dependencies`, `jobs.blocked_reason`, projection/recovery outboxes | Workflows & Jobs, Master, repo worktrees, Container/Areas, run lifecycle; see [task-delegation.md](../task-delegation.md) |
| Wiki Memory | active | `routes/wiki.py`, `wiki_memory.py`, `run_summaries.py` | `WikiScreen.tsx`, `WikiGraph.tsx` | — (FS wiki) | run lifecycle, tasks |
| Files / Tree / Uploads / reference index | active | `file_targets.py`, `routes/files.py`, `fsapi.py`, `target_preview.py`, `cf_hostnames.py` | `api/files.ts`, `fsAdapter.ts`, `WorkspaceTree.tsx`, `FileEditor.tsx`, `useProjectMentionItems.ts` | `projects`, `project_areas` (+FS) | chat, workflows, studios, artifacts, Archive, apps |
| App Run & Preview | active | `apprunner.py`, `preview_output.py`, `preview_output_broker.py`, `preview_proxy.py`, `cf_hostnames.py`, `routes/files.py` | `AppRunner.tsx` | `projects` (+in-mem) | preview-auth cookie, Cloudflare |
| In-browser Terminal | active | `terminal.py`, `routes/chat.py` (`/ws/terminal`) | `TerminalTabs.tsx`, `TerminalView.tsx` | — | projects (cwd) |
| **Artifacts** (live scan for chat cards / iterate Result) | active | `artifacts.py`, `file_targets.py`, `routes/files.py`, `run_outputs.py`, `worker.py` | `ArtifactViewer.tsx`, `ChatThread.tsx` (ResultCards), `IterateStage.tsx` | `messages.output_links` (+FS) | canonical file targets, run outputs, studios, apps |
| **Archive: durable deliverable registry** (slice 8, T4) | active | `artifact_registry.py`, `file_targets.py`, `routes/archive.py`, `run_outputs.py` (feed seam), `routes/work.py` + `routes/graph.py` (approve sync), migration 23 (seed) | `ArtifactsScreen.tsx`, `ArtifactViewer.tsx` | `artifact_records` | canonical file targets, run outputs, jobs (one status two doors), script steps (`script-output` type) |
| Design Studio / Image gen / Moodboard | gated `PROXIMA_FEATURE_DESIGN_STUDIO` (on in dev, opt-in when installed) | `routes/design.py`, `moodboard.py`, `image_providers.py`, `design_scenes.py`, `higgsfield.py` | `DesignStudio.tsx`, `components/design/*` | `app_settings` (+FS: `artifacts/design`, `artifacts/moodboard`) | features gate, artifacts, wiki/run preamble |
| Higgsfield Integration | active (opt-in) | `higgsfield.py`, `routes/files.py` (settings/higgsfield) | `SettingsScreen.tsx` | `app_settings` | image providers |
| Settings Store | active | `app_settings.py`, `routes/files.py`, `settings.py` | `SettingsScreen.tsx` | `app_settings` | collab, permission, providers |
| Permission Gating | active | `routes/chat.py`, `worker.py`, `acp.py` | `ApprovalCard`, `AttentionInbox`, `SettingsScreen` | `app_settings`, `events`, `attention_items` | ordinary ask; Master denies every native request; Task agents retain guarded/autonomous policy |
| Safe self-update candidate gate + disabled switch fixture | request/run projection gated (`PROXIMA_FEATURE_SAFE_SELF_UPDATE`, off); candidate proof is controller-only; switch model requires an initialized temporary disposable fixture | `apps/safe_updater/{build,candidate,candidate_data,circuit_breaker,controller,evidence,fixture_assembler,locks,probe_runner,sandbox,sqlite_image,state_machine,trusted_probes,write_fence}.py`, `maintenance_status.py`, `process_containment.py`, `acp.py`, `codex_appserver.py`, `worker.py`, `script_runner.py`, `apprunner.py`, `preview_proxy.py`, `terminal.py`, `routes/self_updates.py`, `safe_updates.py`, migration 43, ADR-0008 | `UpdateModal.tsx` (evidence-based timeout/failure copy); no activation UI | `self_update_runs` (projection only) | mandatory Bubblewrap boundary for every candidate command; sealed SQLite image plus real WAL/SHM quarantine and fsynced fixture pointers; fixture-only adapter, native single-flight promotion and recovery snapshots, owner-bound activation reconciliation, rollback-required verdict before restoration, controller-provisioned read-only ingress handle, startup and HTTP/proxy/terminal session drain, cached-runner lifetime admission with verified shutdown, deterministic-script PID containment, dynamically uncached lease-aware SQLite fencing, both-pointer rollback, inert provider readiness, verified terminal descendant shutdown, and fail-closed interruption tests; system adapters unmanaged and no enrollment or activation |
| Readiness Health Dashboard | active | `auth_health.py`, `routes/chat.py` (dashboard) | `HomeScreen.tsx` (Connections) | `profiles`, `app_settings` | providers, runners |
| Command palette / Search | active | `routes/chat.py` (search) | `SearchModal.tsx`, `api/search.ts` | `sessions`, `messages`, `projects` | visible Code sessions, projects |
| PWA / Static serving | active | `frontend_static.py` | `src/pwa.ts`, `public/` | — | tab label |

**Notes**

- *Workflows & Jobs — graph engine (ADR-0001)* — schema, isolated typed dispatch, correction routes, chat architect, and dedicated SVG graph canvas are shipped and enabled by default. Promotion emits a normalized DAG for queued human plan review; the canvas edits nodes/dependencies, explicitly starts execution, polls live node state, and exposes correction/rerun/approval/save-template actions. Corrections mark all transitive descendants `stale` before sequential redispatch. `PROXIMA_FEATURE_WORKFLOW_GRAPH=0` remains a recovery switch that makes graph planning/routes/worker paths inert. Legacy linear rows remain readable.
- *Cron Scheduling* — the per-minute claim is an atomic conditional update; overlapping scheduler ticks cannot claim the same schedule minute twice.
- *Tasks* — ad-hoc work and workflow execution are unified in `jobs`; the old `tasks` table and `sessions.task_id` were removed by migration 17.
- *Artifacts / Archive* — chat cards and the Iterate Result keep path-oriented JSON (`produced_artifacts`/`output_links`, guarded by compare-and-swap) with a server-validated canonical file target on each returned link. The Archive is backed by the durable `artifact_records` registry (slice 8, T4): one row per producer-created deliverable version with lineage, the single two-door approval status, and a `file_missing` flag instead of vanishing records when files move or are deleted. Workspace discovery alone does not create records.
- *App Run & Preview* - `app/start` remains an owner-confirmed `bash -lc` command under the service OS user. Child env is filtered; preview uses isolated relay/subdomain origins, preview-only capabilities, credential-stripping proxies, and opaque same-origin HTML sandboxing. Automatic relays bind one port separately on loopback and Tailscale, and authenticate before target resolution. Requested ports are candidates only; appview, relay, and subdomain connections send protocol bytes only after procfs verifies the connected server socket belongs to a ready managed endpoint. Contained authority additionally requires the launch marker, exact Bubblewrap PID-namespace membership, and positive live lineage for every socket owner; missing proof and reparented uncontained lineage fail closed. A monotonic project generation serializes lifecycle changes. A profile-specific launch/output supervisor owns the app process and bounded stdout in its own packaged service cgroup, serves versioned log deltas and atomic final snapshots, and exposes exact restart-adoption proof. Supervisor setup failure is recoverable and occurs before app spawn. Port conflicts are terminal and foreign listeners remain untouched. This mitigates credential leakage but is not an OS sandbox. See ADR-0014 through ADR-0026.
- *Design Studio* — **cleanest isolation pilot**: its own modules do zero core-table writes; coupling is only the additive `sessions.mode='design'` + `runs.kind` columns. Reactivation = set `PROXIMA_FEATURE_DESIGN_STUDIO=1` in `~/.config/proxima/proxima.env` and **restart the API** (flag read once at boot). Image generation needs an image provider (default `codex` needs a working `codex login`); editing scenes needs no provider. The former Video Studio/editor has been removed rather than retained behind a gate.

---

## Home & Activity cards

With Master enabled, Home is the full-page durable Master thread and authoritative
Master-owned Task work panel. Tasks → New task and the explicit Master destination
open the same mounted surface and shared composer state. One typed session SSE stream
provides live updates; recovery reconciliation is not a primary poll.

With Master disabled, the compatibility `HomeScreen.tsx` remains a minimal greeting
with a **Task Composer** (`TaskComposer.tsx`) and an **attention strip** shown when
`reviewCount > 0` (first review job + jump to Tasks). That compatibility surface polls
`GET /api/dashboard` every 5s. The dashboard payload still returns more than it renders
(counts, recents, `authHealth`, `runsPerDay`).

| Surface | Status | Data source | Renders in |
| --- | --- | --- | --- |
| Home · Master thread + Task work panel | active when Master is enabled | `/api/master/desk`, messages snapshot, typed session SSE | `MasterStateProvider.tsx`, `MasterScreen.tsx` |
| Home · greeting + Task Composer | compatibility when Master is off | props (projects/profiles) | `HomeScreen.tsx` |
| Home · attention strip (review jobs) | compatibility when Master is off | `/api/dashboard` (reviewJobs, reviewCount) | `HomeScreen.tsx` |
| Home · other dashboard fields | **dead** | `/api/dashboard` (counts, recents, authHealth, runsPerDay) | not rendered |
| Tasks list (List / Board / Review) | active | `GET /api/jobs?status&include_archived` | `ActivityScreen.tsx` |
| Task workspace (steps + review bar + artifact chips) | active | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/approve`, Task-session `job.update` | `TaskWorkspace.tsx` |

**Notes**

- Tool-permission requests raised by a non-Master job's hidden session surface in
  the global Attention inbox with Task deep-links and safe inline choices. Master
  sessions deny every runner-native request, while Master-created Task agents use
  their own guarded or autonomous execution policy; product review
  gates remain separate.
- Tasks list auto-refreshes while any job is `queued`/`running` and also refreshes
  on owner mutations that change projected status. The task workspace listens to
  Task-session `job.update` for review and checkpoint restore; running polling is
  only a progress fallback.

---

*Maintenance:* when you add/change a feature, update this grid in the same commit (per the doc contract in `CLAUDE.md`), and re-run `scripts/gen_docs.py` if routes/schema changed. Keep `risk` rows until the underlying consistency hazard is fixed.
