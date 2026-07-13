# Feature Map

> Per-feature catalog: **what it is · where the code lives · what it touches · what it relates to · status**.
> The "what do I edit when I add/change X" layer on top of the generated
> [`api.md`](api.md) (routes) and [`database.md`](database.md) (schema), and the flow diagrams in
> [`architecture.md`](architecture.md). Feature *descriptions* live in [`../CAPABILITIES.md`](../CAPABILITIES.md);
> this doc adds the systematic code-location + relations + status/flag grid.
>
> Snapshot: refreshed post-v1.0.0 · 2026-07-14 (Tasks feature removed; Design Studio now enabled). An explorable version is published as the **Proxima Feature Map** artifact.

**Status legend** — `active` shipped & wired · `gated` behind a `PROXIMA_FEATURE_*` flag · `dead` code/column present but not reached by live UI · `risk` active but has a known consistency hazard (see notes).

**Layers** — [Core / Kernel](#core--kernel) · [Main Chat surfaces](#main-chat-surfaces) · [Feature modules](#feature-modules) · [Home & Activity cards](#home--activity-cards).

---

## Core / Kernel

The foundation everything leans on. Least allowed to change casually.

| Feature | Status | Backend | Frontend | Tables | Relates to |
|---|---|---|---|---|---|
| Auth & Session Tokens | active | `routes/auth.py`, `route_deps.py` (`current_user`), `main.py` (`/api/preview-auth`) | `api/client.ts`, `App.tsx` | `users`, `auth_sessions` | gates every route, Preview Proxy |
| Health / Config / Feature Flags | active | `main.py` (`/api/health`, `/api/config`), `features.py` | `features.ts`, `api/config.ts` | — | Design/Video Studio gates |
| Projects & FS Linking | active | `routes/projects.py`, `fsapi.py` | `screens/ProjectsScreen.tsx` | `projects` | files, wiki, apps, workflows |
| Profiles / Runners / Commands | active | `routes/profiles.py`, `runners.py`, `runner_specs.py`, `commands.py`, `capabilities.py` | `screens/ProfilesScreen.tsx`, `RunnersScreen.tsx` | `profiles` | runs pick profile→runner→home |
| Sessions & Messages | active | `routes/chat.py` (list/create/patch/delete) | `api/sessions.ts`, `ChatScreen.tsx` | `sessions`, `messages`, `agent_sessions` | runs, ACP, collab, reviews, goal |
| Run Lifecycle (engine) | active | `worker.py` (RunWorker), `acp.py`, `run_prompting/outputs/summaries/advancers/drafts/state.py` | `hooks/useRunStream.ts` | `runs`, `events`, `messages` | EventHub, collab, reviews, goal, workflow |
| **Event Log & Streaming** | **risk** | `event_hub.py`, `worker.add_event`, `routes/chat.py` streams | `useRunStream.ts`, `useEventStream.ts` | `events` | every streaming surface |
| Reaper / Orphan Reclaim | active | `run_reaper.py`, `worker._fail_interrupted`, `provisioning.py` | — | `runs`, `jobs` | run lifecycle, scheduler |
| Audit & Debug | active | `routes/admin.py` | `SettingsScreen.tsx` (Diagnostics) | `audit_log` | run lifecycle, jobs |
| Provisioning & Migrations | active | `provisioning.py`, `migrations.py`, `db.py`, `profile_seed.py` | — | `schema_migrations`, all | startup |

**Notes**
- *Event Log & Streaming* — `events.id` (autoincrement) is the de-facto global cursor; **both SSE and WS now resume from `after_id`** (WS reconnect no longer hardcodes `last_id=0` / replays the whole session). `seq = MAX(seq)+1` is race-safe only by the single-writer + `db_lock` convention.
- *Reaper* — stale-run reaping is time-based (60s heartbeat), but now skips runs the worker reports as actively executing (`_actively_running`), so a genuinely-live long run is no longer false-positive killed.
- *Provisioning & Migrations* — mixed strategy (`SCHEMA` + idempotent `_add_column` each boot). `sessions.job_id/workflow_id` and `messages.run_id` now carry FKs (via table-rebuild migrations); the old `sessions.task_id` column was dropped.

---

## Main Chat surfaces

The primary gate. Everything reachable from a chat. This is the surface that keeps breaking on feature adds — the isolation target.

| Feature | Status | Backend | Frontend | Relates to |
|---|---|---|---|---|
| Composer / input | active | `routes/chat.py` → `create_run` | `components/chat/Composer.tsx`, `ChatScreen.tsx` | core chat/runs |
| File attach / upload | active | `routes/files.py` (upload) | `Composer.tsx`, `api/files.ts` | files, artifacts |
| Slash commands | active | `commands.py`, `/api/commands/catalog` | `Composer.tsx`, `ChatScreen.tsx` (`localCommandReply`) | sessions, projects, runners |
| Chat modes (Normal/Brainstorm/Debate) | active | `routes/chat.py` (`_mode_prompt`) | `Composer.tsx` (`MODES`) | collaborations |
| Brainstorm entry | active | `routes/chat.py` (`_start_prompt_collaboration`), `prompt_collaborations.py` | `ChatThread.tsx` (CollaborationCards) | run lifecycle, profiles |
| Debate entry | active | `routes/chat.py` (debate branch) | `ChatThread.tsx` | collaborations |
| **Interactive Form** (`<question-form>`) | active | `routes/chat.py` (`list_messages` synth step) | `QuestionForm.tsx`, `questionForm.ts`, `ChatThread.tsx` | core chat (clarifying UX) |
| Validate sidecar | active | `routes/chat.py` (message-review routes), `message_reviews.py` | `MessageReviewSidecar.tsx`, `api/messageReviews.ts` | profiles/runners |
| **Cancel run** | **risk** | `routes/chat.py` (`cancel_run`) | `ChatScreen.tsx` (`stopRun`) | jobs/runs, collab |
| Streaming deltas / smooth reveal | active | SSE/WS streams | `ChatThread.tsx` (StreamingBubble) | runs/worker |
| Tool-call activity | active | `routes/chat.py` (`_run_activity`) | `ChatThread.tsx` (ActivityPanel) | runs/worker, jobs |
| Approval / permission cards | active | `routes/chat.py` (`respond_permission`), `worker.resolve_permission` | `ChatThread.tsx` (ApprovalCard) | run lifecycle, ACP |
| Quick-reply buttons | active | — (FE parse) | `ChatThread.tsx` (`parseChoices`) | core chat |
| Result cards / output links | active | `routes/chat.py` (`_merge_session_artifact`) | `ChatThread.tsx` (ResultCards) | artifacts, studios |
| Bridge → Design/Video Studio | Design on (dev) / Video gated | `routes/design.py` (`designs/from-image`), `api/video` | `ChatThread.tsx` | Design/Video Studio |
| Media generation (`/image` `/design` `/video`) | image always on · design on (dev) · video gated | `routes/chat.py` (`_chat_media_kind`), `image_providers.py`, `video_providers.py` | `Composer.tsx` (Generate → Image/Design draft/Video) | Design/Video Studio |
| Distill to wiki | active | `routes/chat.py` (`wiki_note_draft/commit`), `wiki_memory.py` | `ChatScreen.tsx`, `WikiNotePreview.tsx` | wiki |
| Distill to workflow | active | `routes/chat.py` (`promote_workflow`), `workflows.py` | `ConvertToWorkflowButton.tsx` | workflows |
| Workflow iterate / Run recipe | active | `routes/chat.py` (instant_result branch) | `ChatScreen.tsx`, `IterateStage.tsx` | workflows |
| **Goal loop** (`/goal`) | **risk** | `routes/chat.py` (`start_goal`/`cancel_goal`), `goal_loop.py` | `ChatScreen.tsx` (goalBanner) | run lifecycle |
| Model / profile picker | active | `routes/chat.py` (`update_session`) | `ChatScreen.tsx`, `api/sessions.ts` | profiles/runners |
| Session create / list / search | active | `routes/chat.py` (`create_session`/`list_sessions`/`search`) | `ChatScreen.tsx`, `SearchModal.tsx` | projects |
| Retry / edit message | dead | — | — (not in ChatThread) | Validate sidecar |
| Reasoning-token panel | dead | — | — | — |

**Notes**
- *Brainstorm* — synthesis now waits on a **live count of the expected children** (derived from the participant profile_ids), not on `child_run_ids` being fully persisted, so it can no longer fire after a single child.
- *Cancel run* — cancellation now goes through a **guarded status transition** (`state.py` `guarded_transition`, `WHERE status IN allowed_from`), so a cancel can't be silently overwritten back to `done` by the worker.
- *Goal loop* — advance is now an **atomic guarded transition** (no read-then-blind-write), closing the advance-vs-cancel TOCTOU; the scheduler likewise claims a minute atomically.
- *Validate sidecar* — the dead review modes were removed; the schema + route now only carry `validate` (reviewer profile only, no `mode` field).
- *Session list* — `list_sessions` shows only the `sessions.mode` values the **session-kind registry** (`kinds.py`) marks `shown_in_main_chat`, plus a `job_id IS NULL` filter so job threads stay out of the main chat list. (The old `task_id` self-heal is gone with the Tasks feature.)
- `sessions.mode` values: `chat` (main chat) · `design` (Design Studio, feature-gated — enabled by default in dev). Which modes appear in the main chat list is owned by the `kinds.py` registry, not string checks. Workflow-iterate / job threads are distinguished by the `workflow_id` / `job_id` columns, not `mode`, and are excluded from the main chat list. (The old kanban `tasks` table and `sessions.task_id` were removed.)

---

## Feature modules

Larger capabilities that stand as modules. Target state: each touches core only through a contract, never core tables directly.

| Feature | Status | Backend | Frontend | Tables | Relates to |
|---|---|---|---|---|---|
| Workflows & Jobs | active | `routes/work.py`, `workflows.py`, `run_advancers.py` | `WorkflowsScreen.tsx`, `ActivityScreen.tsx` | `workflows`, `jobs`, `sessions` | scheduler, run lifecycle |
| **Cron Scheduling** | **risk** | `routes/work.py`, `scheduler.py`, `main.py` loop | `WorkflowsScreen.tsx` | `schedules`, `jobs`, `workflows` | workflows/jobs |
| Wiki Memory | active | `routes/wiki.py`, `wiki_memory.py`, `run_summaries.py` | `WikiScreen.tsx`, `WikiGraph.tsx` | — (FS wiki) | run lifecycle |
| Files / Tree / Uploads | active | `routes/files.py`, `fsapi.py` | `WorkspaceTree.tsx`, `FileEditor.tsx` | `projects` (+FS) | artifacts, studios, apps |
| App Run & Preview | active | `apprunner.py`, `preview_proxy.py`, `cf_hostnames.py`, `routes/files.py` | `AppRunner.tsx` | `projects` (+in-mem) | preview-auth cookie, Cloudflare |
| In-browser Terminal | active | `terminal.py`, `routes/chat.py` (`/ws/terminal`) | `TerminalTabs.tsx`, `TerminalView.tsx` | — | projects (cwd) |
| **Artifacts** | active | `artifacts.py`, `routes/files.py` | `ArtifactsScreen.tsx` | `messages.output_links` (+FS) | run outputs, studios, apps |
| Design Studio / Image gen | active (Design Studio on by default in dev via `PROXIMA_FEATURE_DESIGN_STUDIO=1`) | `routes/design.py`, `image_providers.py`, `design_scenes.py`, `run_prompting.py` (vision), `acp.py`/`worker.py` (image blocks), `higgsfield.py` | `screens/DesignStudio.tsx`, `components/design/*` (`ColorInput`, `MiniPreview`) | `app_settings` (+FS) | features gate, artifacts, wiki, vision |
| Video Studio / Video gen | gated `PROXIMA_FEATURE_VIDEO=0` | `routes/files.py` (videos/*), `video_providers.py`, `higgsfield.py` | `VideoScreen.tsx`, `vendor/video-studio/` | `app_settings` (+FS) | features gate, Higgsfield |
| Higgsfield Integration | active (opt-in) | `higgsfield.py`, `routes/files.py` (settings/higgsfield) | `SettingsScreen.tsx` | `app_settings` | image/video providers |
| Settings Store | active | `app_settings.py`, `routes/files.py`, `settings.py` | `SettingsScreen.tsx` | `app_settings` | collab, permission, providers |
| Permission Gating | active | `routes/chat.py`, `worker.py`, `acp.py` | `ApprovalCard`, `SettingsScreen` | `app_settings`, `events` | run lifecycle, ACP |
| Self-update | active (`update_check`) | `routes/update.py`, `updates.py`, `main.py` loop | `UpdateModal.tsx`, `useUpdateStatus` | — (marker file) | app version, health |
| Readiness Health Dashboard | active | `auth_health.py`, `routes/chat.py` (dashboard) | `HomeScreen.tsx` (Connections) | `profiles`, `app_settings` | providers, runners |
| Command palette / Search | active | `routes/chat.py` (search) | `SearchModal.tsx`, `api/search.ts` | `sessions`, `messages`, `projects` | sessions |
| PWA / Static serving | active | `frontend_static.py` | `src/pwa.ts`, `public/` | — | tab label |

**Notes**
- *Cron Scheduling* — `last_run_minute` check-and-set is not atomic (lock released in between); safe only because there is a single scheduler task. No DB-level claim.
- *Tasks (removed)* — the kanban Tasks feature (screen, `routes/tasks.py`, the `tasks` table, and `sessions.task_id`) has been **removed**. Ad-hoc single-step work is now a 1-step job, and Jobs/Activity is the only board.
- *Artifacts* — `produced_artifacts`/`output_links` are free-floating JSON blobs pointing at filesystem paths with no artifact table; can reference deleted files, and concurrent read-modify-write can lose updates.
- *App Run & Preview* — `app/start` executes a command string via `bash -lc` under the owner account (see the security audit F-02).
- *Design Studio* — now an **enabled** feature (on by default in dev; the packaged install ships it off — set `PROXIMA_FEATURE_DESIGN_STUDIO=1` in `~/.config/proxima/proxima.env` and restart, as the flag is read once at boot). Its modules still do zero core-table writes; coupling is only the additive `sessions.mode='design'` + `runs.kind` columns. The design agent is asset-aware, feature-aware, and **vision-capable** (capability-gated ACP image blocks), and can edit/compose **multiple images** in the Assets tab (gated on the provider's `referenceImages`). Image generation needs an image provider (default `codex` needs a working `codex login` — it now supports image edit + reference images); editing scenes needs no provider. **Video** remains far more entangled (bulk of `routes/files.py` + vendored editor) and stays off (`PROXIMA_FEATURE_VIDEO=0`).

---

## Home & Activity cards

Every card on the Home ("Command Center") dashboard and the Activity screen. Home = one `GET /api/dashboard` call (polled 5s). Activity = `GET /api/jobs`.

| Card | Status | Data source | Renders in |
|---|---|---|---|
| Home · Headline status flag | active | `/api/dashboard` (pendingApprovals, reviewCount, activeSessions) | `HomeScreen.tsx:108-119` |
| Home · Quick actions (6 buttons) | active | `/api/dashboard` | `HomeScreen.tsx:122-129` |
| Home · Activity log (C1) | active | `/api/dashboard` (recent, activeSessions) | `HomeScreen.tsx:134-146` |
| Home · System readout (C2) — KPI tiles + jobs-by-status bar + health | active | `/api/dashboard` (counts, jobsByStatus, systemHealth) | `HomeScreen.tsx` |
| Home · Connections (C3) | active (video rows gated) | `/api/dashboard` (authHealth.checks) | `HomeScreen.tsx:168-178` |
| Home · Review inbox (C4) | active | `/api/dashboard` (reviewJobs) | `HomeScreen.tsx:180-189` |
| Home · Recent artifacts (C5) | active | `/api/dashboard` (recentArtifacts) | `HomeScreen.tsx:191-201` |
| Home · Up next / schedules (C6) | active | `/api/dashboard` (schedules) | `HomeScreen.tsx:204-214` |
| Home · Workflows (C7) | active | `/api/dashboard` (workflows) | `HomeScreen.tsx:216-225` |
| Home · Projects (C8) | active | `/api/dashboard` (projects) | `HomeScreen.tsx:227-232` |
| Home · runsPerDay time-series | **dead** | `/api/dashboard` returns `runsPerDay` but nothing renders it | — |
| Activity · Mode + status filter | active | `GET /api/jobs?status&include_archived` | `ActivityScreen.tsx:257-267` |
| Activity · Job row / Kanban card | active | `GET /api/jobs`, `GET /api/jobs/{id}` | `ActivityScreen.tsx:270-294` |
| Activity · Job detail (flow + steps + review bar + artifact chips) | active | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/approve` | `ActivityScreen.tsx:136-191` |

**Notes**
- The only data-viz on Home is the C2 jobs-by-status segmented bar. There is no chart/sparkline library; `runsPerDay` is a returned-but-unrendered time series.
- Activity list auto-refreshes every 2.5 s while any job is `queued`/`running`; Job-detail step polls 1.5 s while running.
- Design-open from a Job-detail artifact chip is gated on `designStudioEnabled`.

---

*Maintenance:* when you add/change a feature, update this grid in the same commit (per the doc contract in `CLAUDE.md`), and re-run `scripts/gen_docs.py` if routes/schema changed. Keep `risk` rows until the underlying consistency hazard is fixed.
