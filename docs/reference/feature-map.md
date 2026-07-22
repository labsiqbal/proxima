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
| Auth & Session Tokens | active | `routes/auth.py`, `route_deps.py` (`current_user`), `main.py` (`/api/preview-auth`) | `api/client.ts`, `App.tsx` | `users`, `auth_sessions` | gates every route, Preview Proxy |
| Health / Config / Feature Flags | active | `main.py` (`/api/health`, `/api/config`), `features.py` | `features.ts`, `api/config.ts` | — | Design Studio / graph gates |
| Projects & FS Linking | active | `routes/projects.py`, `fsapi.py` | `screens/ProjectsScreen.tsx` | `projects` | files, tasks, wiki, apps, workflows |
| Work-container areas (data layer) | active | `project_areas.py`, `routes/projects.py` (areas endpoints), migration 18 | (no UI yet; payload carries `code_areas`/`ops_area`) | `project_areas` | repo-job worktrees (slice 2, shipped flag-gated); slicer job→target binding is slice 3 |
| Profiles / Runners / Commands | active | `routes/profiles.py`, `runners.py`, `runner_specs.py`, `commands.py`, `capabilities.py` | `screens/ProfilesScreen.tsx`, `RunnersScreen.tsx` | `profiles` | runs pick profile→runner→home |
| Sessions & Messages | active | `routes/chat.py` (list/create/patch/delete) | `api/sessions.ts`, `ChatScreen.tsx` | `sessions`, `messages`, `agent_sessions` | runs, ACP, collab, reviews, goal |
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
| Composer / input + `@` project-file references | active | `routes/files.py` (`reference-files`), `fsapi.py`; `routes/chat.py` media vision | `components/chat/Composer.tsx`, `MentionTextarea.tsx`, `useProjectMentionItems.ts` | core chat/runs, projects/files |
| File attach / upload | active | `routes/files.py` (upload) | `Composer.tsx`, `api/files.ts` | files, artifacts |
| Slash commands | active | `commands.py`, `/api/commands/catalog` | `Composer.tsx`, `ChatScreen.tsx` (`localCommandReply`) | sessions, projects, runners |
| Chat modes (Normal/Brainstorm/Debate) | active | `routes/chat.py` + `chat_collaboration.py` | `Composer.tsx` (`MODES`) | collaborations |
| **Brainstorm entry** | **risk** | `routes/chat.py` (`_start_prompt_collaboration`), `prompt_collaborations.py` | `ChatThread.tsx` (CollaborationCards) | run lifecycle, profiles |
| Debate entry | active | `routes/chat.py` (debate branch) | `ChatThread.tsx` | collaborations |
| **Interactive Form** (`<question-form>`) | active | `routes/chat.py` (`list_messages` synth step) | `QuestionForm.tsx`, `questionForm.ts`, `ChatThread.tsx` | core chat (clarifying UX) |
| Validate sidecar | active | `routes/reviews.py`, `message_reviews.py` | `MessageReviewSidecar.tsx`, `api/messageReviews.ts` | profiles/runners |
| **Cancel run** | **risk** | `routes/chat.py` (`cancel_run`) | `ChatScreen.tsx` (`stopRun`) | jobs/runs, collab, tasks |
| Streaming deltas / smooth reveal | active | SSE/WS streams | `ChatThread.tsx` (StreamingBubble) | runs/worker |
| Tool-call activity | active | `routes/chat.py` (`_run_activity`) | `ChatThread.tsx` (ActivityPanel) | runs/worker, jobs |
| Approval / permission cards | active | `routes/chat.py` (`respond_permission`), `worker.resolve_permission` | `ChatThread.tsx` (ApprovalCard) | run lifecycle, ACP |
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
- `sessions.mode` values: `chat` (main chat, the only mode `list_sessions` returns) · `design` (Design Studio, gated) · `video` is **not** a real mode (schema forbids it; a defensive filter only). Task / workflow-iterate / job threads are distinguished by the `task_id` / `workflow_id` / `job_id` columns, not `mode` — all excluded from the main chat list.

---

## Feature modules

Larger capabilities that stand as modules. Target state: each touches core only through a contract, never core tables directly.

| Feature | Status | Backend | Frontend | Tables | Relates to |
| --- | --- | --- | --- | --- | --- |
| Workflows & Jobs | active | `routes/work.py`, `routes/graph.py`, `workflows.py`, `graph.py`, `graph_executor.py`, `graph_advancers.py`, `run_advancers.py` | `WorkflowsScreen.tsx`, `ActivityScreen.tsx`, `GraphScreen.tsx`, `graphLayout.ts` | `workflows`, `jobs`, `node_states`, `sessions`, `runs` | scheduler, run lifecycle |
| Repo-job worktrees (flag-gated, off) | gated | `worktrees.py`, `routes/work.py` (start/diff/approve/delete), `worker.py` (cwd seam), migration 19 | (no UI until slice 4; job payload carries `worktree`) | `job_worktrees`, `jobs.target_area_id`, `project_areas` | work-container areas, run lifecycle, slice-4 review UI, slice-5 continuation |
| **Cron Scheduling** | **risk** | `routes/work.py`, `scheduler.py`, `main.py` loop | `WorkflowsScreen.tsx` | `schedules`, `jobs`, `workflows` | workflows/jobs |
| Ops Tasks (ad-hoc jobs) | active | `routes/work.py` | `TaskComposer.tsx`, `TaskWorkspace.tsx`, `ActivityScreen.tsx` | `jobs`, `sessions`, `runs` | run lifecycle, review gates |
| Wiki Memory | active | `routes/wiki.py`, `wiki_memory.py`, `run_summaries.py` | `WikiScreen.tsx`, `WikiGraph.tsx` | — (FS wiki) | run lifecycle, tasks |
| Files / Tree / Uploads / reference index | active | `routes/files.py`, `fsapi.py` | `WorkspaceTree.tsx`, `FileEditor.tsx`, `useProjectMentionItems.ts` | `projects` (+FS) | chat, workflows, studios, artifacts, apps |
| App Run & Preview | active | `apprunner.py`, `preview_proxy.py`, `cf_hostnames.py`, `routes/files.py` | `AppRunner.tsx` | `projects` (+in-mem) | preview-auth cookie, Cloudflare |
| In-browser Terminal | active | `terminal.py`, `routes/chat.py` (`/ws/terminal`) | `TerminalTabs.tsx`, `TerminalView.tsx` | — | projects (cwd) |
| **Artifacts** | active | `artifacts.py`, `routes/files.py` | `ArtifactsScreen.tsx` | `messages.output_links` (+FS) | run outputs, studios, apps |
| Design Studio / Image gen | gated `PROXIMA_FEATURE_DESIGN_STUDIO` (on in dev, opt-in when installed) | `routes/design.py`, `image_providers.py`, `design_scenes.py`, `higgsfield.py` | `DesignStudio.tsx`, `components/design/*` | `app_settings` (+FS) | features gate, artifacts, wiki |
| Higgsfield Integration | active (opt-in) | `higgsfield.py`, `routes/files.py` (settings/higgsfield) | `SettingsScreen.tsx` | `app_settings` | image providers |
| Settings Store | active | `app_settings.py`, `routes/files.py`, `settings.py` | `SettingsScreen.tsx` | `app_settings` | collab, permission, providers |
| Permission Gating | active | `routes/chat.py`, `worker.py`, `acp.py` | `ApprovalCard`, `SettingsScreen` | `app_settings`, `events` | run lifecycle, ACP |
| Self-update | active (`update_check`) | `routes/update.py`, `updates.py`, `main.py` loop | `UpdateModal.tsx`, `useUpdateStatus` | — (marker file) | app version, health |
| Readiness Health Dashboard | active | `auth_health.py`, `routes/chat.py` (dashboard) | `HomeScreen.tsx` (Connections) | `profiles`, `app_settings` | providers, runners |
| Command palette / Search | active | `routes/chat.py` (search) | `SearchModal.tsx`, `api/search.ts` | `sessions`, `messages`, `projects` | visible Code sessions, projects |
| PWA / Static serving | active | `frontend_static.py` | `src/pwa.ts`, `public/` | — | tab label |

**Notes**

- *Workflows & Jobs — graph engine (ADR-0001)* — schema, isolated typed dispatch, correction routes, chat architect, and dedicated SVG graph canvas are shipped and enabled by default. Promotion emits a normalized DAG for queued human plan review; the canvas edits nodes/dependencies, explicitly starts execution, polls live node state, and exposes correction/rerun/approval/save-template actions. Corrections mark all transitive descendants `stale` before sequential redispatch. `PROXIMA_FEATURE_WORKFLOW_GRAPH=0` remains a recovery switch that makes graph planning/routes/worker paths inert. Legacy linear rows remain readable.
- *Cron Scheduling* — the per-minute claim is an atomic conditional update; overlapping scheduler ticks cannot claim the same schedule minute twice.
- *Tasks* — ad-hoc work and workflow execution are unified in `jobs`; the old `tasks` table and `sessions.task_id` were removed by migration 17.
- *Artifacts* — `produced_artifacts`/`output_links` remain filesystem-path JSON rather than a separate artifact table; guarded compare-and-swap updates prevent concurrent lost updates, while deleted files can still leave stale links.
- *App Run & Preview* — `app/start` remains an owner-confirmed `bash -lc` command under the service OS user. Child env is filtered; preview uses isolated loopback/subdomain origins, preview-only capabilities, credential-stripping proxies, and opaque same-origin HTML sandboxing. This mitigates credential leakage but is not an OS sandbox.
- *Design Studio* — **cleanest isolation pilot**: its own modules do zero core-table writes; coupling is only the additive `sessions.mode='design'` + `runs.kind` columns. Reactivation = set `PROXIMA_FEATURE_DESIGN_STUDIO=1` in `~/.config/proxima/proxima.env` and **restart the API** (flag read once at boot). Image generation needs an image provider (default `codex` needs a working `codex login`); editing scenes needs no provider. The former Video Studio/editor has been removed rather than retained behind a gate.

---

## Home & Activity cards

Ops Home (`HomeScreen.tsx`) is deliberately minimal: greeting + **Task Composer**
(`TaskComposer.tsx`) + an **attention strip** shown when `reviewCount > 0`
(first review job + jump to Tasks). It polls `GET /api/dashboard` every 5s.
The dashboard payload still returns more than Home renders (counts, recents,
`authHealth`, `runsPerDay`); those fields are currently unrendered server data.

| Surface | Status | Data source | Renders in |
| --- | --- | --- | --- |
| Home · greeting + Task Composer | active | props (projects/profiles) | `HomeScreen.tsx` |
| Home · attention strip (review jobs) | active | `/api/dashboard` (reviewJobs, reviewCount) | `HomeScreen.tsx` |
| Home · other dashboard fields | **dead** | `/api/dashboard` (counts, recents, authHealth, runsPerDay) | not rendered |
| Tasks list (List / Board / Review) | active | `GET /api/jobs?status&include_archived` | `ActivityScreen.tsx` |
| Task workspace (steps + review bar + artifact chips) | active | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/approve` | `TaskWorkspace.tsx` |

**Notes**

- Tool-permission requests raised by a job's agent run are **not** surfaced in
  `TaskWorkspace` — with auto-approve off, a Guarded task can sit "Running…" on a
  hidden `approval.request` until the run times out. Approve them from the run's
  chat surface or via `POST /api/runs/{id}/permission`; a task-side approval card
  is an open gap.
- Tasks list auto-refreshes while any job is `queued`/`running`; the task
  workspace polls while running.

---

*Maintenance:* when you add/change a feature, update this grid in the same commit (per the doc contract in `CLAUDE.md`), and re-run `scripts/gen_docs.py` if routes/schema changed. Keep `risk` rows until the underlying consistency hazard is fixed.
