# Architecture & Flows

_Hand-maintained conceptual reference. For exact endpoints see [api.md](api.md);
for the exact schema see [database.md](database.md); for the stack see
[tech-stack.md](tech-stack.md)._

## What it is

A self-hosted, **single-user control plane for AI coding agents**. It provides a PWA

+ backend for chat, projects, files, terminal, workflows, jobs, schedules, wiki,
artifacts, and runner profiles. It does **not** run models itself — it drives agent
CLIs you already own (Claude Code, Codex, Gemini CLI, Hermes) over the **Agent
Client Protocol (ACP)**.

### Non-goals

+ Not a replacement for the agent CLIs it drives.
+ Not a cloud SaaS (self-hosted by default).
+ Not a multi-user IAM system — one owner, no in-app accounts.
+ Not hardened for untrusted tenants (see [Security boundary](#security-boundary)).

## Product model

```text
Owner ── Profile ── Runner ── Project / Workspace
```

+ **Owner** — the sole user. Auto-created on first request; the SPA signs in via
  `POST /auth/auto` (no password). The access gate is the _network_, not a login.
+ **Profile** — an agent persona: its runner, an isolated credential home, a default
  model, and system instructions ("soul").
+ **Runner** — the agent CLI a profile drives (Claude Code / Codex / Gemini / Hermes),
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
                Agent CLIs: claude-code · codex · gemini · hermes
```

Core backend modules: `main.py` (app factory + lifespan), `db.py` (schema +
connections), `migrations.py` (versioned migrations), `worker.py` (run worker),
`acp.py` (ACP manager), `scheduler.py`, `event_hub.py`, `terminal.py`,
`apprunner.py` + `preview_proxy.py`, `image_providers.py` / `video_providers.py`
(media backend registries), `auth_health.py` (cached background auth/readiness
checks for the Home banner), `logging_config.py` (query-token redaction across
Uvicorn HTTP and WebSocket handlers), and `routes/` (the HTTP surface).

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

## Server-owned feature gates

```text
PROXIMA_FEATURE_VIDEO=0 ───────────────┐
PROXIMA_FEATURE_DESIGN_STUDIO=0 ───────┤
PROXIMA_FEATURE_WORKFLOW_GRAPH=0 ──────┼─> GET /api/config ─> frontend capability map
                                       └─> route/run guards before side effects
```

Video and Design Studio are retained in source but disabled by default. The backend
is authoritative: disabled requests return HTTP 503 with the consistent
`feature_disabled` payload before creating messages, writing the database or files,
calling providers, spawning processes, or dispatching collaboration. The frontend
uses the published flags to omit navigation, deep links, commands, settings,
provider health checks, bridge actions, and agent guidance. Image generation remains
enabled, and existing media files remain readable as ordinary artifacts.

`PROXIMA_FEATURE_WORKFLOW_GRAPH` gates the new graph workflow engine (ADR-0001).
It is the **master safety switch** for that engine: its schema (`jobs.engine`,
`jobs.graph`, `workflows.graph`, the `node_states` table) and the flag exist, but the
engine is **inert** until the flag is turned on — no graph routes, worker path, or UI
run while it is off, so the classic linear job engine is unaffected. The pure
`graph.py` boundary already normalizes planner/UI input to canonical edges, rejects
cycles and invalid references, computes deterministic topological/ready sets, and
validates each node's `text` / `json` / `artifact-ref` output contract (including
JSON Schema definitions); it performs no DB, runner, or HTTP work. The gated
`graph_executor.py` adapter then selects the deterministic ready set with Phase-1
concurrency fixed at one, snapshots explicit job/upstream data into a `wf_node` run,
and creates a fresh hidden `sessions.job_id` thread per attempt so ACP history cannot
leak between nodes. It queues work through `RunWorker`; it never calls a runner itself.
On completion, `graph_advancers.py` validates and canonicalizes the declared output
(JSON Schema for `json`; contained, existing workspace paths for `artifact-ref`) before
a version/run-id guarded state transition. Invalid/blocked/runner-failed nodes pause the
job in `review`; valid nodes dispatch the next ready node, while review gates and the
final node also pause for human review. Feature-gated `routes/graph.py` is the human
correction boundary: queued plans can be edited before start; a reviewed node can have
its typed output replaced or be rerun; either action marks every transitive descendant
`stale` and resumes deterministic execution. A gate is approved node-by-node, and a
job reaches `done` only after all nodes are `done` and final approval is explicit.

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
actions are omitted while the corresponding feature is disabled. Video provider and
Studio modules remain dormant behind the feature gates so they can return without
source recovery.

## Data model in one breath

`users` (single owner) → `profiles` (personas) and `projects` (folders). Work happens
in a `session` (a chat thread) which accumulates `messages` and spawns `runs` (one
agent turn each); a run emits ordered `events` that stream to the UI. Repeatable work
is a `workflow` (recipe, steps as JSON); one execution is a `job` (frozen step
snapshot + state); a `schedule` fires jobs on cron. `tasks` are a kanban surface
unified onto the jobs model. `agent_sessions` maps a chat to its per-home ACP session.
A `job` carries an `engine` discriminator: `linear` (the classic `current_step_idx`
and `steps_state` cursor) or `graph` (ADR-0001) — graph jobs keep durable per-node state
in `node_states` instead, and are gated/inert behind `PROXIMA_FEATURE_WORKFLOW_GRAPH`.
Full column-level detail: [database.md](database.md).

## Key flows

### 1. Chat turn (the core loop)

```text
UI  POST /api/chat/send ─────────────► create session (if new) + user message
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
600s) cancels stragglers.

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
default expanded, can collapse per card, scroll horizontally on desktop, and stack
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
+ **Workflow:** `POST /.../promote-workflow` has an architect agent decompose the
  conversation into ordered steps → a new `workflow` row.

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

Ad-hoc single-step work is just a 1-step job (old kanban `tasks` were migrated this
way). Jobs live-poll while running and auto-archive after 30 days.

### 7. Schedule (cron)

`schedules` rows carry a 5-field cron + overlap policy. The scheduler loop wakes each
minute, finds _due_ schedules (matching the current minute, not a backlog), and
materializes a `job` for each — respecting `overlap_policy` (skip / allow).

### 8. Run & Preview app

`POST /api/projects/{slug}/app/start` → `AppManager` launches one dev process for the
project. `PreviewProxyMiddleware` reverse-proxies it — locally (authed) or, when
`apps_domain` is configured, on a `<slug>.<apps_domain>` subdomain gated by the
`proxima_preview` cookie so it can be iframed.

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
+ **Bounded run worker** — `max_concurrent_runs` caps parallel agent runs.
+ **Crash recovery** — on startup, runs left `running` by a previous shutdown are
  failed (their in-memory ACP state is gone); orphaned jobs are reaped.
+ **Backups** — versioned migrations `VACUUM INTO` a snapshot before applying; a
  daily timer backs up independently.

## Security boundary

Proxima assumes **external** network access control. Anyone who reaches the API
is treated as the owner; agents run with the OS privileges of the service user. There
is no in-app authz beyond `/auth/auto`. Detail + threat model:
[security-boundaries.md](../security-boundaries.md) and
[prompt-injection-hardening.md](../prompt-injection-hardening.md).
