# Architecture & Flows

_Hand-maintained conceptual reference. For exact endpoints see [api.md](api.md);
for the exact schema see [database.md](database.md); for the stack see
[tech-stack.md](tech-stack.md)._

## What it is

A self-hosted, **single-user control plane for AI agents**. It provides a PWA

+ backend for chat, projects, files, terminal, workflows, jobs, schedules, wiki,
artifacts, design, and runner profiles. It does **not** run models itself — it drives
agent CLIs you already own (Claude Code, Codex, Hermes, Pi) over the **Agent
Client Protocol (ACP)**. The work it orchestrates is domain-neutral (content,
ops, research, code alike); the runners it drives today happen to be coding-agent
CLIs.

### Non-goals

+ Not a replacement for the agent CLIs it drives.
+ Not a cloud SaaS (self-hosted by default).
+ Not a multi-user IAM system — one owner, no in-app accounts.
+ Not hardened for untrusted tenants (see [Security boundary](#security-boundary)).

## Product model

```text
Owner ── Profile ── Runner ── Project / Workspace
```

+ **Owner** — the sole user. First run requires setting an owner password; login
  establishes a bearer-token/HttpOnly-cookie session. Network controls remain the
  primary boundary, with application authentication as defense in depth.
+ **Profile** — an agent persona: its runner, an isolated credential home, a default
  model, and system instructions ("soul").
+ **Runner** — the agent CLI a profile drives (Claude Code / Codex / Hermes / Pi),
  resolved by a _runner spec_.
+ **Project** — a scaffolded or linked folder. Chat, terminal, files, wiki, and
  workflows all operate on the project path.

## Component map

```text
                         Browser (React PWA)
                                │  REST + SSE + WebSocket
                                ▼
┌──────────────────────────── FastAPI app (main.py) ────────────────────────────┐
│  routes/*.py   REST handlers (registered via register(app, deps))              │
│  EventHub      fan-out of run/session events → SSE + WS subscribers            │
│  RunWorker     bounded-concurrency background executor for agent runs          │
│  Scheduler     60s loop; materializes due cron jobs                            │
│  AcpManager    one ACP subprocess per (runner, home, cwd)                      │
│  AppManager    per-project dev-server processes  ── PreviewProxy (subdomains)  │
│  Terminal      PTY shell over WebSocket                                         │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 │  sqlite3 (WAL, one connection per thread)
                                 ▼
                         SQLite DB  (see database.md)
                                 │  spawns / talks ACP
                                 ▼
                Agent CLIs: claude-code · codex · hermes · pi
```

Core backend modules: `main.py` (app factory + lifespan), `db.py` (schema +
connections), `migrations.py` (versioned migrations), `worker.py` (run worker),
`acp.py` (ACP manager), `scheduler.py`, `event_hub.py`, `terminal.py`,
`apprunner.py` + `preview_proxy.py`, `image_providers.py` (image backend registry),
`auth_health.py` (cached background auth/readiness
checks for the Home banner), `logging_config.py` (query-token redaction across
Uvicorn HTTP and WebSocket handlers), `run_prompting.py` (prompt framing plus jailed,
bounded vision inputs), and `routes/` (the HTTP surface).

## Runtime / repo split

Source code lives in the repo; **all runtime data lives outside it**, so product
code never mixes with per-install state:

```text
~/.config/proxima/proxima.env                       config
~/.local/share/proxima/proxima.db                   SQLite database
~/.local/share/proxima/workspace                    scaffolded projects
~/.local/share/proxima/hermes-profiles/<owner>/<profile> per-profile agent home
~/.local/share/proxima/backups                      DB snapshots
```

> **Naming note:** `hermes-profiles/` — like the `hermes_home` columns on
> `profiles`/`runs`/`agent_sessions` and the `HERMES_*` env names — is legacy naming
> from the Hermes-first era. Every runner (Claude Code, Codex, Hermes, Pi) stores its
> per-profile credential home there; the mechanism is fully runner-agnostic. The
> schema/paths are intentionally not renamed.

## Server-owned feature gates

```text
PROXIMA_FEATURE_DESIGN_STUDIO ─────────┐
PROXIMA_FEATURE_WORKFLOW_GRAPH=1 ──────┼─> GET /api/config ─> frontend capability map
PROXIMA_FEATURE_REPO_WORKTREES ────────┘─> route/run guards before side effects
```

Design Studio is an active feature behind a server-owned flag: `scripts/dev`
enables it by default; installed instances opt in via `proxima.env` (the flag is
read once at boot). The backend is authoritative: while disabled, requests return
HTTP 503 with the consistent `feature_disabled` payload before creating messages,
writing the database or files, calling providers, spawning processes, or
dispatching collaboration, and the frontend uses the published flags to omit
navigation, deep links, commands, settings, provider health checks, bridge
actions, and agent guidance. Image generation is independent of the flag, and
existing media files—including video files—remain readable as ordinary artifacts.
Video Studio and video generation are not product surfaces.

`PROXIMA_FEATURE_WORKFLOW_GRAPH` gates the graph workflow engine (ADR-0001) and
defaults to **on**, because the graph canvas is the shipped authoring path. It remains
a master recovery switch: setting it to `0` makes graph routes, worker paths, schedules,
and UI inert while leaving legacy linear jobs readable. The pure
`graph.py` boundary already normalizes planner/UI input to canonical edges, rejects
cycles and invalid references, computes deterministic topological/ready sets, validates
node `type`/`trigger_kind`/`profile_id`/`x`/`y` and the entry-point rules (at most one
trigger, no incoming edges), and validates each node's `text` / `json` / `artifact-ref`
output contract (including JSON Schema definitions); it performs no DB, runner, or HTTP
work. It also owns the per-job work-binding tags (Phase-1 slice 3, T1/T2): a node's
`target` names ONE container area (a code area's rel_path or `ops`), `touches_repo` is
always derived from it (an authored value is never trusted), and an ambiguous binding
is a first-class `target_ambiguous`/`target_question` state. `routes/graph.py` checks
targets against the project's registered areas at plan create/edit (422 on an unknown
area) and refuses to start a plan with an unresolved target question (409 carrying the
question) — the target is pinned at slice time precisely so it cannot be discovered at
runtime. The gated `graph_executor.py` adapter resolves any trigger node to the approved
job input without a runner, then dispatches **every** ready node up to
`graph_node_concurrency`, snapshots explicit job/upstream data into a `wf_node` run
against that node's own agent (`profile_id`, else the job's), and creates a fresh hidden
`sessions.job_id` thread per attempt so ACP history cannot leak between nodes — and so
that `claim_run`'s per-session serialization does not stop branches overlapping. It
queues work through `RunWorker`, which is where `run_worker_concurrency` becomes the
real ceiling; it never calls a runner itself. On completion, `graph_advancers.py`
validates and canonicalizes the declared output (JSON Schema for `json`; contained,
existing workspace paths for `artifact-ref`) before a version/run-id guarded state
transition. Invalid/blocked/runner-failed nodes pause the job in `review`; valid nodes
dispatch whatever became ready, while review gates and the final node pause for human
review. Because branches overlap, a paused (`review`) job still accepts results from
nodes already in flight — rejecting them would drop finished work and strand the node —
but only a still-`running` job pulls new work forward. Feature-gated `routes/graph.py` is the human
correction boundary: queued plans can be edited before start; a reviewed node can have
its typed output replaced or be rerun; either action marks every transitive descendant
`stale` and resumes deterministic execution. A gate is approved node-by-node, and a
job reaches `done` only after all nodes are `done` and final approval is explicit.

`PROXIMA_FEATURE_REPO_WORKTREES` gates the repo-job worktree machinery (Phase-1
slice 2, T1) and defaults to **off** until slice 4 ships the diff-review UI. While
off, `worktrees.py` has no callers on the execution path, the `/api/jobs/{id}/diff`
endpoint returns the standard 503 `feature_disabled` payload, and job start/approve/
cwd selection behave exactly as without the feature. See flow 6b.

## Media provider setup

Chat and coding-agent runs stay on ACP (`RunWorker` → `AcpManager` → runner CLI).
Active image generation is deliberately separate and chosen from Settings:

+ **Image generation:** Codex/ChatGPT OAuth, xAI OAuth via Hermes `auth.json`,
  Higgsfield zero-credit CLI, or an OpenAI-compatible endpoint.

The settings APIs store only provider/model/policy plus optional endpoint keys for
OpenAI-compatible image endpoints; OAuth providers read existing local auth stores and
never return tokens to the frontend.

Main-chat image generation is **artifact-first**: `/image` / `/gambar` results appear
as chat result cards and are saved under `artifacts/media/images/`. Studio bridge
actions are omitted while the corresponding feature is disabled. Video Studio and
video-provider modules were removed; rendered video files remain generic playable
artifacts.

## Data model in one breath

`users` (single owner) → `profiles` (personas) and `projects` (folders). Work happens
in a `session` (a chat thread) which accumulates `messages` and spawns `runs` (one
agent turn each); a run emits ordered `events` that stream to the UI. Repeatable work
is a `workflow` (recipe, steps as JSON); one execution is a `job` (frozen step
snapshot + state); a `schedule` fires jobs on cron. Ad-hoc Ops tasks are 1-step
jobs (the old kanban `tasks` table was dropped by migration 17). `agent_sessions`
maps a chat to its per-home ACP session.
A `job` carries an `engine` discriminator: `linear` (the classic `current_step_idx`
and `steps_state` cursor) or `graph` (ADR-0001) — graph jobs keep durable per-node state
in `node_states` instead, and are gated/inert behind `PROXIMA_FEATURE_WORKFLOW_GRAPH`.
A project is additionally a **work container** (Phase-1, T1): `project_areas` rows
record its git-repo subfolders (*code areas*, auto-detected from `.git` with manual
override - `.` means repo-at-root) and its single *ops area* (non-code output space).
A `job` may bind to exactly one area via `target_area_id` (T1); a code-area target
makes it a **repo job**, whose isolated worktree lifecycle lives in `job_worktrees`
(slice 2, gated/inert behind `PROXIMA_FEATURE_REPO_WORKTREES` - see flow 6b).
Artifact scanning still ignores areas; the slicer that sets the binding at slice
time is slice 3.
Full column-level detail: [database.md](database.md).

## Key flows

### 1. Chat turn (the core loop)

Before submission, every project-scoped composer can resolve `@query` through
`GET /api/projects/{slug}/reference-files`. The endpoint returns relative paths only;
it does not read or inline file content. Traversal is capped, skips symlinks and
dependency/build/cache/hidden trees, and suppresses common secret/key filenames.
The shared frontend loader rejects stale project responses and refreshes after file
changes. A selected ordinary file is sent as its relative path. A selected image is
sent as Markdown image-reference syntax: ordinary ACP agents can still open the path,
while `/image` and design flows resolve it again inside the session project jail and
attach bounded image bytes as visual input.

```text
UI  @file picker (path only) ─────────► relative path / explicit image reference
    POST /api/chat/send ─────────────► create session (if new) + user message
    │                                  enqueue a run (status: queued)
    ▼
RunWorker picks up the run (bounded concurrency)
    │  ensures an ACP session for (runner, profile home, project cwd)
    │  sends the prompt to the agent CLI over ACP
    ▼
Agent streams back → events (assistant_delta, tool.start/complete,
    permission.request, artifact, run.completed)
    │  persisted to `events` + fanned out by EventHub
    ▼
UI  subscribes to GET /api/.../events/stream (SSE) and
    WS /api/ws/sessions/{id}  → renders deltas, tool cards, approval cards
    │  approvals: POST /api/runs/{id}/permission   cancel: /runs/{id}/cancel
    ▼
run.completed → assistant message saved (linked via messages.run_id)
```

Runs are per-session serialized and bounded-concurrent globally; a heartbeat +
reaper fail hung runs, and a run timeout (configurable `run_timeout_seconds`, default
900s) cancels stragglers. Completion updates are guarded by the current run state, so
cancel wins over late media, review, collaboration, draft, or graph finalizers. Failures
during pre-ACP setup are finalized immediately rather than waiting for the reaper.

### 2. Per-prompt Brainstorm / Debate

```text
Composer mode chip ── Brainstorm/Debate ──► POST /api/sessions/{id}/runs
                                      │     prompt_mode='brainstorm'|'debate'
                                      ▼
prompt_collaborations parent row + visible parent run
                                      │
                                      ├─ Brainstorm: 2-3 child runs in parallel
                                      │  → live agent cards → synthesis child run
                                      └─ Debate: 2-4 configured rounds
                                         → live round cards → synthesis/judge
                                      ▼
One final assistant message saved on the parent run
```

Brainstorm and Debate are pre-output modes, not message-review sidecars. The
parent run is the only run the chat attaches to as busy/visible; child runs use
`collaboration_id`/`collaboration_role` metadata and hidden `collab_*` kinds so
raw child output does not land in the main transcript. The worker emits
`collaboration.child.*` events for queued/started/delta/completed/failed/cancelled
child states. The frontend reconstructs inline cards from those events, using
agent names as card headers and Debate round labels as secondary metadata. Cards
default collapsed, can expand per card, scroll horizontally on desktop, and stack
on mobile. Brainstorm collects parallel independent ideas and synthesizes overlap,
unique angles, and next steps. Debate runs ordered rounds so later agents can read
and rebut prior positions before a neutral synthesis. Collaboration defaults live
in app settings (`collaboration_brainstorm_agents`, `collaboration_debate_rounds`)
and surface in Settings under Agents & Collaboration. The composer resets to
Normal after send, so there is no global mode toggle.

### 3. Message-level Validate sidecar

```text
Completed assistant message ── Validate ──► message_reviews row
                                      │     + kind='message_review' run
                                      ▼
Reviewer profile (different runner) streams review deltas as normal events
                                      │
                                      ▼
message_review.completed stores verdict, gaps, unanswered-input notes,
revised_content, suggested_next_move, raw transcript
                                      │
                                      ├─ Replace answer: update source message,
                                      │  preserve original + applied_at on review
                                      └─ Ask source to merge: kind='message_review_merge'
                                         updates revised_content in the same sidecar
```

Validate is intentionally a sidecar: review runs do **not** create assistant messages
and do not answer/advance embedded question forms. The frontend filters
`message_review*` runs out of the main chat busy-run restore path, while still using
SSE events to render queued/running/done/failed sidecar state. The explicit mutation
path is `Replace answer`: it overwrites the source assistant message, stores the
original in `message_reviews.source_original_content`, and can restore it. `Ask
source to merge` is still sidecar-only: the source profile produces a better
candidate, then the user decides whether to replace the visible answer.

### 4. Goal loop (multi-step autonomy)

`POST /api/sessions/{id}/goal` sets an objective on the session (`sessions.goal_*`).
After each run the advance hook feeds prior-step context back in and starts the next
run, repeating until the agent reports done/blocked or `goal_max` iterations is hit.
Cancel with `/goal/cancel`.

### 5. Chat → Wiki / Chat → Workflow (distillation)

+ **Wiki:** `POST /.../wiki-note/draft` spawns a run that emits a `wiki.draft` event
  (preview) → `POST /.../wiki-note/commit` writes the markdown into the project's
  `wiki/` and rebuilds the index.
+ **Workflow:** `POST /.../promote-workflow` has an architect agent slice the
  conversation. The legacy linear path emits ordered steps. When
  `PROXIMA_FEATURE_WORKFLOW_GRAPH=1`, it instead emits a normalized typed DAG draft —
  a **runnable plan**, not a template: the frontend materializes it as a queued graph
  job the owner can inspect/edit and start directly (run-first, T2). The architect
  prompt carries the project's registered code areas, and every sliced job arrives
  tagged with its `target` (one code area or `ops`) and derived `touches_repo`; when
  the slicer cannot decide it marks the job ambiguous with a question instead of
  guessing, and the plan refuses to start until the owner picks a target. Saving as a
  reusable Recipe (`POST /api/graph/jobs/{id}/save-template`) is an optional, separate
  act — available before or after the run, from the canvas or from a Tasks plan row.

### 6. Workflow → Job → execution

```text
workflow (recipe: steps JSON + typed {{inputs}})
    │  run / iterate
    ▼
job = frozen snapshot of steps + per-step state (steps_state JSON)
    │  steps run sequentially in ONE ACP session (context carries across steps)
    │  review-gate steps pause → Approve / edit-&-continue
    ▼
job done  →  artifacts surface in the Result view / Artifacts gallery
```

The gated graph sibling freezes `{nodes,edges}` on a job, stores each node attempt in
`node_states`, dispatches every ready node concurrently (bounded by
`graph_node_concurrency`, then by `run_worker_concurrency`) in a fresh hidden ACP
session per attempt, and passes only explicit typed upstream outputs. Each node may run
as its own agent. Plan edits are allowed only while queued; execution therefore always
starts behind a human approval action. Review/correction can edit or rerun a node and
marks every transitive descendant stale before redispatch.

`GraphScreen.tsx` is the gated control plane for this sibling engine. It is a canvas-first,
n8n-style surface — drag nodes, pan/zoom, drag-to-connect, click-to-remove a connection —
built on native SVG, so no graph library is required. One slim bar holds the plan-list
toggle, title, live status and the plan-level actions; the plan list collapses; and the
node inspector is rendered only while a node is selected, so no unused panel holds canvas
width. The workspace is flex rather than grid precisely because those two panels come and
go. `graphLayout.ts` supplies deterministic
topological columns as a *fallback*: a node's hand-placed `x`/`y` wins, and the layout
reports a real bounding box because the canvas is infinite and positions may be
negative. Node positions are part of the graph and are persisted by the same explicit
**Save plan** action as every other plan edit, never written behind the owner's back.
Drag-to-connect is pointer-only, so the inspector's dependency checkboxes remain the
keyboard path to the same edges. The screen allows node/dependency/layout edits only
while queued, and exposes the correction and approval protocol once execution begins.
Saved graph templates are listed and reused only through the gated graph surface;
classic workflow lists and execution remain strictly linear.

The **Tasks screen** (`ActivityScreen.tsx`) is the index of plans + their jobs (T2):
graph plans appear alongside classic linear tasks, and a plan row expands into its
ordered job list — each job showing its name, target badge, touches-repo marker, and
live status (`planProjection.ts` computes the deterministic order and joins
`node_states`). List view and graph view are two projections of the same plan:
branch-less plans read as a plain list, and branching plans offer the read-only
dependency canvas as a toggle (the same `GraphCanvas` component the editor uses —
extracted, not duplicated). Plan rows also carry **Open plan** (to the canvas, where
review actions live) and **Save as Recipe** (the same save-template mechanics).

Ad-hoc single-step work is just a 1-step job (old kanban `tasks` were migrated this
way). Jobs live-poll while running and auto-archive after 30 days.

### 6b. Repo job: worktree → diff review → local merge (slice 2, flag-gated)

Gated behind `PROXIMA_FEATURE_REPO_WORKTREES` (off until slice 4 ships the review
UI; off = flow 6 exactly). A job whose `target_area_id` names a code area is a
**repo job** and never edits the primary tree:

```text
POST /api/jobs/{id}/start
    │  worktrees.py cuts branch proxima/job-<id> from the code area's repo
    │  into <workspace_root>/worktrees/job-<id>   (outside the container;
    │  refuses loudly on dirty repo / detached HEAD / no commits → 409, job
    │  stays queued; crash leftovers cleaned idempotently by job id)
    ▼
RunWorker: the run's cwd = the worktree (missing worktree fails the run
    loudly - never a silent fallback to the primary tree)
    ▼
GET /api/jobs/{id}/diff  →  snapshot outstanding edits onto the job branch,
    then per-file status + unified patch vs base_commit (slice-4 review surface)
    ▼
POST /api/jobs/{id}/approve (final step)  →  guarded local merge --no-ff into
    the branch the worktree was cut from (T1 local-first; no push - remote is T9)
    ├─ success: merge_commit recorded on job_worktrees, worktree + branch torn down
    └─ refusal/conflict: 409, job PARKS in review with the surfaced error;
       worktree kept - resolve, approve again to retry. Never forced.
```

Lifecycle state is one `job_worktrees` row per job
(`active → merging → merged`, with `conflict` and `discarded` as off-ramps);
deleting a job tears its worktree down. Snapshot-then-merge means partial agent
work is durable in the worktree across crashes - the substrate slice 5's
continuation turns (T5) resume in.

**Graph plans reuse this same machinery per job-in-plan (slice 3).** When the flag is
on and a plan has repo jobs (nodes with `touches_repo`), `POST /api/graph/jobs/{id}/start`
resolves their one code-area target to `jobs.target_area_id` and cuts the plan's
worktree before claiming `running` — same loud-refusal ordering as the linear start. A
plan's repo jobs must share ONE code area (Phase-1: one worktree row per job); a
multi-area plan refuses to start with a split-the-plan message. The worker's cwd seam
is node-aware: a `wf_node` run executes in the worktree only when ITS node touches the
repo, while ops siblings run at the project root, where their artifact outputs belong.
The final `POST /api/graph/jobs/{id}/approve` is the merge point, with the identical
guarded-merge/park-in-review contract as the linear approve. Flag off: none of this
runs and target tags are inert metadata.

### 7. Schedule (cron)

`schedules` rows carry a 5-field cron + overlap policy. The scheduler loop wakes each
minute, finds _due_ schedules (matching the current minute, not a backlog), and
materializes a `job` for each — respecting `overlap_policy` (skip / allow).

### 8. Run & Preview app

`POST /api/projects/{slug}/app/start` → `AppManager` launches one owner-confirmed dev
process for the project with a filtered environment. Locally the iframe uses the other
loopback hostname, avoiding host-cookie reuse across ports. When `apps_domain` is
configured, `PreviewProxyMiddleware` serves a `preview-<slug>.<apps_domain>` subdomain
gated by a one-hour, signed `proxima_preview` capability that is unrelated to the owner
API session. HTTP proxy paths remove Cookie/Authorization before forwarding and ignore
upstream `Set-Cookie`; same-origin/generated HTML previews omit `allow-same-origin`.
These are lightweight self-hosted mitigations, not OS isolation of the project process.

### 9. Update check & self-update

```text
VERSION (repo root) → read_local_version() → FastAPI app.version → GET /api/health
                                    │
UpdateManager: every 6h → GET api.github.com/repos/<repo>/releases/latest
                           (never raises — offline/404/hiccup → last_error)
                                    │
   GET /api/update/status · POST /api/update/check · POST /api/update/apply
                                    │
        apply() → detached `scripts/proxima update` (git pull --ff-only,
        rebuild, restart only after a clean build) → update-status.json
        marker in the data dir, reconciled again on the next startup
                                    │
Sidebar pill → release-notes modal → Update now → blocking overlay polls
GET /api/health every 2s until version == target → reload
```

`UpdateManager` (`updates.py`) is the one thing that phones home: an
unauthenticated GitHub Releases GET on a 6-hour timer (first check 60s after
boot), holding only in-memory state (current version, latest release,
`checked_at`, `last_error`) — `PROXIMA_UPDATE_CHECK=0` disables just that
loop (the manual check route still works) and `PROXIMA_UPDATE_REPO` defaults to
`labsiqbal/proxima`; forks can point it at their own repo. `apply()` is Windows-gated (`UpdateUnsupported`
→ manual command) and otherwise `Popen`s the updater detached
(`start_new_session=True`) so its own `systemctl`/`launchd` restart can safely
kill the parent once the new code is on disk; the `update-status.json` marker
self-heals (updater pid gone → `failed`, current version reaches target →
`done`) and is reconciled again at every startup in case a restart interrupted
it mid-flight.

## Runner abstraction

The app never hardcodes one CLI as the boundary. A _runner spec_ maps an installed
CLI to its command/argv, credential home, readiness check, and default-model
behavior. Runs carry a `runner_id`; `default_runner()` resolves env → first _ready_
runner → fallback. Agents emit a **generic event vocabulary** regardless of CLI:

```json
{ "type": "assistant_delta", "text": "..." }
{ "type": "tool.start", "title": "npm run build" }
{ "type": "tool.complete", "status": "completed" }
{ "type": "permission.request", "options": [] }
{ "type": "artifact", "path": "..." }
{ "type": "run.completed" }
```

## Concurrency & reliability

+ **Per-thread SQLite connections** in WAL mode — sync handlers run across FastAPI's
  threadpool, so each thread gets its own connection; writes serialize on SQLite's
  lock + `busy_timeout`.
+ **Bounded run worker** — `run_worker_concurrency` caps parallel agent runs.
+ **Crash recovery** — on startup, runs left `running` by a previous shutdown are
  failed (their in-memory ACP state is gone); orphaned jobs are reaped.
+ **Backups** — versioned migrations `VACUUM INTO` a snapshot before applying; a
  daily timer backs up independently.

## Security boundary

Proxima relies primarily on **external** network access control and adds a single-owner
password/session gate as defense in depth. Authenticated requests act as the owner;
agents run with the OS privileges of the service user. Child environments are filtered
and permissions ask by default, but this is not a filesystem sandbox. Detail + threat model:
[security-boundaries.md](../security-boundaries.md) and
[prompt-injection-hardening.md](../prompt-injection-hardening.md).

## Shell and task/schedule data flow

`App.tsx` remains the single view owner. It owns the Workflows Editor/Scheduled modes and embeds the graph surface under the single Workflows destination. Ops Task Composer creates then starts an ad-hoc job and opens a dedicated `task` view with `#task/<id>` restoration. `execution_policy=guarded` preserves final review; `autonomous` completes the final step without an approval stop. Normal tasks queue the selected profile; `/image` and `/design` reuse the proven media run path and link that run to the job so worker completion advances it to review. Start failure triggers queued-task cleanup; a media link failure preserves and exposes the task ID. Ops project selection updates context directly; the existing chat `selectProject` behavior still selects the latest project chat.

`AppShell` retains the persisted left navigation width/collapse state, mobile drawer, search, account actions, and terminal-compatible content mounting. `App.tsx` tracks the selected workspace plus each workspace's last destination; `Sidebar` renders Ops-specific or Code-specific navigation. Global Projects/Agents/Settings preserve the selected workspace. Terminal is classified as Code-only and remains mount-once/hide; Tasks and task detail are Ops-owned destinations. The removed generic right panel does not affect destination-owned layouts: Design Studio's canvas/Konva internals and dedicated inspector remain unchanged.

Generic frontend refresh loops use one non-overlapping polling hook. It pauses while
the document is hidden and refreshes once when the tab becomes visible, avoiding
background request churn without making active run status stale. Home artifact recents
reuse the bounded project-artifact scanner instead of recursively classifying the whole
tree with a second implementation.

Authentication boot checks setup state, requires set-password or login, and resumes from the HttpOnly `proxima_session` cookie into an in-memory bearer token.
