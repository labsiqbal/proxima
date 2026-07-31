# Architecture & Flows

_Hand-maintained conceptual reference. For exact endpoints see [api.md](api.md);
for the exact schema see [database.md](database.md); for the stack see
[tech-stack.md](tech-stack.md)._

## What it is

A self-hosted, **single-user control plane for AI agents**. It provides a PWA

+ backend for chat, projects, files, terminal, workflows, jobs, schedules, wiki,
artifacts, design, and runner profiles. It does **not** run models itself — it drives
agent CLIs you already own (Claude Code, Codex, Grok, Hermes, Pi) over the **Agent
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
  model, and system instructions ("soul"). Master uses a hidden `system_kind='master'`
  profile so the owner can change its backing runner without creating a normal worker
  persona; its thread is a `sessions.mode='master'` session excluded from Chat history.
+ **Runner** - the agent CLI a profile drives (Claude Code / Codex / Grok / Hermes / Pi),
  resolved by a _runner spec_.
+ **Project** - a scaffolded, linked, or newly-created-on-disk folder. Chat,
  terminal, files, wiki, and workflows all operate on the project path.

## Component map

```text
                         Browser (React PWA)
                                │  REST + SSE + WebSocket
                                ▼
┌──────────────────────────── FastAPI app (main.py) ────────────────────────────┐
│  routes/*.py   REST handlers (registered via register(app, deps))              │
│  EventHub      fan-out of run/session events → SSE + WS subscribers            │
│  RunWorker     bounded-concurrency background executor for agent runs          │
│  TaskDelegationService  one-Area Task create, dependency, idempotent start      │
│  MasterToolBroker  typed, schema-validated, filesystem-isolated product tools    │
│  GraphContextService  scoped, bounded Graphify generations and query results     │
│  CodeGraphLifecycle  Code rebuild queue, audit, dirty debounce (never worktree) │
│  Master runtime chat-only conformance, read-only scratch, deny native tools      │
│  MasterSupervisor budgeted unattended queue starter (no stuck-run authority)    │
│  Scheduler     60s loop; materializes due cron jobs                            │
│  AcpManager    one ACP subprocess per (runner, home, cwd)                      │
│  AppManager    project generations + supervisors ── PreviewProxy (subdomains) │
│                                                  ── PreviewRelay (per-app port)│
│  Terminal      PTY shell over WebSocket                                         │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 │  sqlite3 (WAL, one connection per thread)
                                 ▼
                         SQLite DB  (see database.md)
                                 │  spawns / talks ACP
                                 ▼
                Agent CLIs: claude-code · codex · grok · hermes · pi
```

Core backend modules: `main.py` (app factory + lifespan), `db.py` (schema +
connections), `migrations.py` (versioned migrations), `worker.py` (run worker),
`run_reaper.py` (dead-run watchdog) + `satpam.py` (its sibling: the slice-12
supervision loop over alive-but-unproductive jobs), `master_runtime.py` (system
identity + restricted chat-only Master runtime), `master_tool_broker.py` (typed,
schema-validated, filesystem-isolated product tools), `codex_master_proxy.py` (Codex loopback
provider firewall), `master_supervisor.py` (budgeted unattended
queue starter), `graph_context.py` (scoped Graphify adapter),
`code_graph_lifecycle.py` (Code rebuild queue/audit/debounce) +
`graphify_area_mcp.py` (fixed-Area Task MCP proxy), `job_checkpoints.py`,
`task_state_events.py` (transaction-coupled Task invalidation and recovery history),
`run_projection.py` (timezone-aware API timestamps and effective run lifecycle),
`turn_restore.py`,
`acp.py` (ACP manager), `scheduler.py`, `event_hub.py`, `terminal.py`,
`apprunner.py` + `preview_proxy.py` + `preview_output.py` (project generations,
proxy, reconnectable supervisor client, and delta log protocol) with
`preview_output_broker.py` as the per-app launch/output supervisor,
`image_providers.py` (image backend registry),
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
> from the Hermes-first era. Every runner (Claude Code, Codex, Grok, Hermes, Pi) stores its
> per-profile credential home there; the mechanism is fully runner-agnostic. The
> schema/paths are intentionally not renamed.

## Server-owned feature gates

```text
PROXIMA_FEATURE_DESIGN_STUDIO ─────────┐
PROXIMA_FEATURE_WORKFLOW_GRAPH=1 ──────┼─> GET /api/config ─> frontend capability map
PROXIMA_FEATURE_REPO_WORKTREES=1 ──────┘─> route/run guards before side effects
```

Design Studio is a shipped feature behind a server-owned flag, on by default;
owners can disable it via `proxima.env` (the flag is read once at boot). The
backend is authoritative: while disabled, requests return
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
output contract (including JSON Schema definitions). Trigger normalization also owns
the manual intake field declaration or the scheduled cron, overlap, and enabled
settings. It performs no DB, runner, or HTTP
work. It also owns the per-job work-binding tags (Phase-1 slice 3, T1/T2): a node's
`target` names ONE container area (a code area's rel_path or `ops`), `touches_repo` is
always derived from it (an authored value is never trusted), and an ambiguous binding
is a first-class `target_ambiguous`/`target_question` state. `routes/graph.py` checks
targets against the project's registered areas at plan create/edit (422 on an unknown
area); plan start refuses an unresolved target question (409 carrying the question) in
the shared `bind_graph_job_repo_worktree` path, which checks ambiguity before the
`feature_repo_worktrees` gate and the project binding — so a project-less ambiguous plan
cannot start silently and the scheduler cannot skip the refuse. The target is pinned at
slice time precisely so it cannot be discovered at runtime. The gated `graph_executor.py` adapter resolves any trigger node to the approved
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

**Script nodes (Phase-1 slice 6, T6):** a third node kind, `script`, is the
deterministic step - it runs a saved script from the Container's physical `ops/scripts/`
folder with no LLM. `graph_executor.py` dispatches it through the same runs queue as
a `wf_script_node` run (same budget, quota, heartbeats, reaping); `RunWorker`
branches on the kind and hands it to `script_runner.py`, which executes the script
as a subprocess (exec array, the physical Ops Area as cwd, minimal env), feeds it the typed
hand-off as JSON on stdin plus `{{var}}`-substituted CLI args, and validates stdout
against the node's output contract through the ordinary `graph_advancers.py` path.
Execution is gated by hash-bound trust (`script_trust`, `scripts_library.py`): an
unapproved or changed script blocks the node with a `script_approval_required` error;
the approval card fetches content + sha256 together (`GET …/nodes/{node_id}/script`)
and the one-time `POST …/approve-script` approval echoes that hash (409 if the file
changed after review — audit F4), records the sha256, and reruns the step. The
runner hashes and executes the same in-memory bytes via a private temp copy, so a
concurrent swap of the project file cannot run unapproved content. When an
external maintenance boundary is configured, the private copy executes inside the
shared PID containment and its process-lifetime ingress lease is released only
after the namespace exits. `scripts_library.scan_catalog` also feeds the reuse-awareness
surfaces: the script catalog is injected into every project run preamble
(`wiki_memory.build_run_preamble`) and into the plan slicer's prompt
(`workflows.architect_system`).

`PROXIMA_FEATURE_REPO_WORKTREES` gates the repo-job worktree machinery (Phase-1
slices 2+4, T1) and defaults to **on** since slice 4 shipped the diff-review UI;
it remains the owner's escape hatch. While off, `worktrees.py` has no callers on
the execution path, the `/api/jobs/{id}/diff` endpoint returns the standard 503
`feature_disabled` payload, and job start/approve/cwd selection behave exactly as
without the feature (the reject action still works - it is a review verdict, not
worktree machinery). See flow 6b.

## Media provider setup

Chat and coding-agent runs stay on ACP (`RunWorker` → `AcpManager` → runner CLI).
Active image generation is deliberately separate and chosen from Settings:

+ **Image generation:** Codex/ChatGPT OAuth, xAI OAuth via the Grok runner
  (`grok login` → `~/.grok/auth.json` or `$GROK_HOME/auth.json`), Higgsfield
  zero-credit CLI, or an OpenAI-compatible endpoint.

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
snapshot + state); a `schedule` fires jobs on cron. Ad-hoc tasks are 1-step
jobs (the old kanban `tasks` table was dropped by migration 17). `agent_sessions`
maps a chat to its per-home ACP session.
A `job` carries an `engine` discriminator: `linear` (the classic `current_step_idx`
and `steps_state` cursor) or `graph` (ADR-0001) — graph jobs keep durable per-node state
in `node_states` instead, and are gated/inert behind `PROXIMA_FEATURE_WORKFLOW_GRAPH`.
A project row is the compatibility persistence record for a **Container**.
`project_areas` records zero or more repo Areas (auto-detected from `.git` with manual
override, where `.` means repo-at-root) and exactly one active Ops Area. Fresh
Containers use the physical `ops/` folder and an Ops row with `rel_path='ops'`.
`container_registry` stores a bounded projection of identity and summary from
`ops/container.md`, its full source hash, the projection timestamp, and last known
activity. Identity is free text, not a Container type enum. The file API refreshes
the projection immediately when it writes `container.md`; a five-second background
cycle catches direct owner edits without adding filesystem work to Fleet requests.
`container_ops_migrations` stores the versioned, hash-bound, resumable migration
marker for legacy root-level Ops data.
A `job` may bind to exactly one area via `target_area_id` (T1); a code-area target
makes it a **repo job**, whose isolated worktree lifecycle lives in `job_worktrees`
(slice 2, gated/inert behind `PROXIMA_FEATURE_REPO_WORKTREES` - see flow 6b).
Scoped Work, Home, Master, and future orchestration creation share
`TaskDelegationService`. `task_delegations` is the one-to-one origin, routing,
idempotency, durable-start, and captured Master Focus audit for a job.
`task_dependencies` stores explicit
Task-to-prerequisite edges with a required `review` or `done` status. A unique pair,
self-edge check, and recursive insert/update triggers make the stored graph
cycle-safe. The prerequisite foreign key is restrictive, so deleting a Task or
Container cannot silently erase an edge that another Task still needs.
`jobs.blocked_reason` is the visible reason a requested Task remains queued.
Historical project-less Work API jobs remain an unscoped compatibility path.
A code area with a detected git remote may opt into push-after-merge via
`project_areas.push_on_merge` (T9, slice 11, default off); enabling pins the remote
URL into `project_areas.push_remote_url` (audit F3) and the push refuses on a
mismatch with the repo's current `.git/config`. `repo_remote.py` shells
out to the host's own `git`/`gh` (BYO - no brokered auth, no stored tokens; the push
neutralizes repo-config credential helpers and hooks via `-c` overrides) and the
push outcome lands on the `job_worktrees` row (`push_status/push_error/...`).
`container_registry.py` is the only physical root resolver. Every active Area must
resolve inside its Container after realpath resolution. Duplicate roots, unsafe
overlap, escape, and Container-or-Ops-root symlinks are rejected on every
resolution; the full recursive scan that rejects any symlink inside physical Ops is
opt-in (`deep_ops_scan`) and runs at the fail-closed boundaries - Ops creation,
legacy migration, Area mutation, and Area-sensitive execution - so hot read paths
(project lists, Home, file resolution) stay O(1) and lean on per-access realpath
jailing instead. Best-effort cross-Container aggregations (Home dashboard, Archive
list) resolve through `try_ops_root`, which returns None for an unavailable or
boundary-invalid Container so one missing folder skips that Container instead of
failing the whole read; direct single-Container access still uses `ops_root` and
stays fail-closed. The intentional repo-at-root plus `ops/` containment is permitted
and `/ops/` is added to the root repo's local git exclude.

Legacy Ops rows at `.` remain usable until migration succeeds. Startup creates a
dry-run manifest with content hashes, rejects collisions or ambiguous types before
moving anything, and atomically renames only known Ops-owned paths on the same
filesystem. A durable `moving` marker supports restart after any completed rename.
Failures open a `container_ops_migration` Attention item and retain the legacy row;
per-Container migration failures are isolated so one unhealthy Container (missing
drive, deleted Area folder) never aborts control-plane startup.
Archive, Wiki, artifacts, Design, scripts, reports, exports, uploads, and the virtual
file API all resolve through the active Ops row.

The authenticated public Fleet boundary uses Container terminology:
`GET /api/containers`, `GET /api/containers/{slug}`, and
`GET /api/containers/{slug}/areas`. List and detail read registry metadata plus
running and queued Task counts, open Attention counts, last activity, Area
inventory, and the health indicators available before graph delivery. Graph
freshness remains `null` on Fleet so Live state never depends on graph availability;
the separate authenticated graph route exposes scoped state behind the Master
feature boundary. A single SQLite statement aggregates every Fleet list row through
grouped CTEs, so the statement count stays constant as the Fleet grows and no graph
or per-Container file is read. Container detail uses the same owner-scoped query.
The Areas route then applies the canonical realpath and overlap validation before
returning targetable Areas.

The existing `/api/projects` readers remain a one-release compatibility surface.
They render the historical `projects`, `code_areas`, and `ops_area` payload from the
same registry and Area query functions. Persistence and foreign keys retain
`projects` and `project_id`, avoiding a table rename cascade while public schemas
and routes use Container names.
Deliverables are durable records (Phase-1 slice 8, T4): `artifact_records` holds one
row per deliverable **version** - identity (project, type, path), lineage
(session → job/node → run), the single approval status (`draft/review/approved/
superseded`) both approval doors write, an automatic version chain
(new producer at the same identity ⇒ v(n+1), prior versions superseded), and a
permanent per-project slug. The scanner (`artifacts.py`) only discovers; the
registry (`artifact_registry.py`) remembers - records survive file moves/deletion
via `file_missing`. Fed at the one seam every run's outputs pass through
(`run_outputs.save_assistant_message`); seeded from the scanner by migration 23.
Migration 26 introduced the original orchestrator foundation. Migration 31
converts that durable identity in place to Master: `profiles.system_kind='alpha'`
becomes `master`, `sessions.mode='alpha'` becomes `master`, and
`jobs.alpha_session_id` becomes `jobs.origin_master_session_id` without changing
primary keys or ownership links. The profile kind hides the
system identity from worker pickers; `jobs.origin_master_session_id` scopes desk ownership
and `master_max_parallel` capacity claiming, while each Task's execution policy controls ACP approval;
`master_tool_calls` is the durable per-turn product-envelope replay ledger;
`graph_states` stores one Knowledge row per Container and one Code row per exact
code Area, including generation, state, fingerprints, Graphify version, freshness,
failure metadata, and Code lifecycle fields (`repo_head`, pending merge range,
`rebuild_reason`). Its internal roots and canonical graph paths never appear in
public payloads; `knowledge_rebuild_intents` is the per-Container outbox written
by the database in the same transaction that completes an Ops Task;
`job_checkpoints` stores job-row/node/run
state plus git/worktree refs (never a DB backup or filesystem zip);
`turn_file_journals` stores bounded before-content for paths changed by a Chat turn
and cascades with the session; `attention_items` stores durable Master, budget, and
permission needs-you items while review/satpam items are projected into the same API.
Settings under `master.*` hold unattended state, turn/wall/optional-token budgets, and
core-tour completion. Startup asserts one project-unbound Master identity per owner
and refuses ambiguous dual identities or conflicting old/new origin columns. The
migration is transactional and idempotent, runs regardless of the runtime feature
flag, and preserves messages, runs, events, checkpoints, budgets, attention,
delegations, and Task ownership. Deprecated Alpha routes and legacy payload readers
project the same rows for one compatibility release. Stored payload normalization
is ownership-scoped: unrelated Alpha-named business fields in ordinary jobs,
attention, events, and audit records are never rewritten.
Job API payloads also normalize stored `*_at` values to UTC-aware ISO and attach one
`run_projection` containing effective status, start, finish, and duration. A failed
child overrides a nonterminal review parent for presentation without rewriting the
durable recovery state. Workflows, Tasks, Attention, Task detail, and expanded nodes
therefore read one lifecycle contract. The canonical payload boundary hydrates
linear steps or graph node state directly from the database, and Activity status
filters use the same effective-state rule as the returned badges.
Supervision (Phase-1 slice 12, T10) adds two tables: `satpam_watch` (the watchman's
per-chain memory - last continuation turn evaluated, progress fingerprints,
no-progress counters, a pending steer note) and `satpam_interventions` (the
owner-visible record of every steer/restart/escalate, including the pending repo
restart awaiting approval); decision-hold rides on `node_states`
(`question`/`answer`/`contract_failures`, migration 25).
Full column-level detail: [database.md](database.md).

### Scoped graph state and Graphify adapter

Migration 35 adds graph state independently of graph availability. When
`feature_master_orchestrator` is off, the authenticated graph routes reject use and
no build starts. When enabled, `GET /api/containers/{slug}/graphs` reads path-free
state and `POST /api/containers/{slug}/graphs/rebuild` accepts only a typed
`knowledge` or `code` scope plus an optional registered Area id. Callers cannot
provide a command, filesystem path, MCP project path, depth, timeout, result limit,
or model setting.

`GraphContextService` resolves Knowledge to the Container's physical Ops boundary
and Code to one exact active code Area after canonical symlink resolution. A
root-repository Code scope excludes every nested registered Area, including Ops.
Source discovery rejects symlinks, traversal, escaped roots, incomplete walks, and
scope changes during a build. Every graph scope excludes other active Container
roots in the owner's fleet when they are nested beneath the selected scope. Task
worktree paths cannot be promoted as canonical graph roots. Graphify `0.9.28` runs
as a local Python library in a killable worker with server ceilings for time and
output bytes. Structural Code extraction and the Ops Knowledge allowlist
(container identity, curated wiki/decisions, reports, and durable artifact
metadata) are local-structural; semantic model egress defaults off.
Knowledge discovery iterates lazily and caps both visited entries and visited
directories, so unsupported content still consumes a bounded walk budget.

Each build writes to a same-filesystem temporary generation directory. Proxima
validates the complete JSON shape, exact scope metadata, source citations, edge
provenance, source fingerprint, graph size, and resolved source containment before
an atomic canonical replacement. A killed, incomplete, malformed, or wrong-scope
generation cannot replace the canonical graph. A successful replacement preserves
the previous bytes as `graph.last-good.json`; a confirmed pre-commit database
finalization failure restores those prior bytes. Before canonical replacement, a
fsynced publication journal records both the prior and replacement digests.
Graph-state finalization runs its update and final read in one explicit
transaction. If the commit outcome is ambiguous, publication accepts it only after
the writer is out of its transaction and an independent read-only SQLite
connection plus the bounded canonical digest both match the replacement. It then
returns the committed `fresh` or `queued` row as success. This preserves a queued
follow-up rebuild for the lifecycle drain. Unresolved outcomes leave canonical
and journal bytes untouched for the next locked query or rebuild to reconcile.
Generic rebuild failure handling only transitions a row that is still `building`,
so it cannot overwrite a committed `fresh` or `queued` outcome.
Canonical digest checks and last-good copying use descriptor snapshots bounded by
`graph_max_bytes`, and publication never buffers the prior graph in the API
process. Missing Graphify records an explicit `missing` state. A failed build
records `failed` unless a newer durable intent keeps it `queued`, without
affecting Tasks, Fleet, or Live state. Generated `graphify-out/` artifacts are
treated as ignored build outputs inside the Area path unless a future project
policy opts into version control.

#### Code graph lifecycle (Group 10)

Migration 36 adds Code lifecycle columns on `graph_states`.
`CodeGraphLifecycle` owns Code graph freshness only:

| Trigger | Action |
|---|---|
| repo Area registered | ensure state row + enqueue full build |
| repo Task merged | mark that Area `stale` immediately, enqueue incremental when safe |
| external HEAD / tracked fingerprint | scheduled audit marks stale + enqueues full rebuild |
| stable dirty tracked working tree | debounce, then mark stale + enqueue |
| tool-version / history / incremental failure | full rebuild |

Incremental rebuilds use changed-file extraction plus Graphify `build_merge` when
the recorded base is an ancestor of HEAD and tool versions match; otherwise a full
rebuild runs. Rebuild workers never publish from a Task worktree. A background tick
(when Master is enabled) drains the `queued` Code rows, runs the dirty-tree debounce,
and periodically audits only already-registered Code graph Areas so unrelated
Containers are not scanned.

Repo Task-agent capability activation injects a server-managed
`proxima-code-graph` MCP entry (`graphify_area_mcp`) fixed to that Task's selected
Area graph path. The proxy ignores arbitrary `project_path`. Master capability
activation remains empty and never receives this entry.

#### Knowledge graph lifecycle and context router (Group 11)

`KnowledgeGraphLifecycle` owns at most one Knowledge graph per Container Ops area.
Sources are an Ops-root allowlist only: `container.md`, `design.md`, curated wiki
notes, reports, and durable artifact metadata named `METADATA.md` or
`*.meta.json` under `artifacts/`. Other artifact files, secret-like names,
symlinks, nested repos, `graphify-out`, Task transcripts, scripts, uploads,
exports, caches, and binary media never enter the walk.

| Trigger | Action |
|---|---|
| Container create / link | ensure Knowledge state + enqueue full build |
| Ops Task finishes | transactionally write that Container's rebuild outbox intent |
| owner edits allowlisted Ops files | cheap metadata marker gates a full fingerprint and debounce |
| startup + scheduled audit | fingerprint / tool / missing-graph drift on registered rows only |
| scheduled full rebuild | re-queue every registered Knowledge graph |

Builds remain local-structural (`semantic_backend=local-structural`). The
`graph_semantic_egress_enabled` opt-in is visible in settings, state, and logs, but
cloud extraction is still refused until a future adapter ships. Failed builds keep
last-good bytes. The background lifecycle drains durable rebuild intents into the
graph queue before doing filesystem discovery and rebuild work. A crash after the
Task reaches `done` can delay a rebuild but cannot lose its intent.

`ContextRouter` implements Master `query_context` (ADR-6):

| Intent | Source |
|---|---|
| fleet / which Containers | Fleet registry |
| running / green / successful / completed / cancelled / blocked / status | SQLite Live state |
| facts / decisions about a Container | that Container's Knowledge graph |
| code structure / impact | one named Code graph Area |

Mixed requests call a bounded set of exact layers and never merge fleet-wide graphs.
Focused Knowledge/Code results are scope-checked so another Container's nodes cannot
appear. Durable explicit targets and Container Focus are authoritative over
model-supplied scope. When an explicit target or Container Focus pins only the
Container, the broker accepts an exact registered Area owned by that Container and
rejects cross-Container Areas; an explicitly pinned Area remains authoritative.
Focused Live status terms are mapped to job statuses and filtered before the result
limit; green, successful, and completed mean `done`, while blocked/stuck includes
explicit `blocked` jobs plus `queued` jobs with a non-null `blocked_reason`.
Unmatched focused questions use Knowledge, while unmatched fleet questions use
Fleet and Live. Live state remains correct when every graph is missing or stale.
Knowledge citations are re-resolved at query time, including ancestor VCS-marker
checks, so a source that became part of a nested repository after publication is
refused. There is no public graph query route; the Master broker returns validated
Ops/Area-relative citation paths but never absolute host or internal graph paths.

Focus is a durable context-isolation boundary. Fleet mode has no epoch; a
Container Focus has one open `master_focus_epochs` row and a versioned
`master_focus_state` record. Focus transitions are optimistic, append a boundary
message and `master.focus.changed` event, and stamp the captured epoch on the
user message, run, response, tool result, and Master projection. While a turn is
active the only allowed Focus mutation is one durable pending Focus, applied
once after the last turn closes. An explicit Container send performs transition
and enqueue atomically. Generic session run producers reject the Master session,
with a database trigger as the persistence backstop. Task delegation copies the
captured epoch onto its durable audit row, so later Task and supervision
projections retain attribution after the origin message or run is deleted.
Uncaptured migration-era Tasks remain executable after scoped ownership validation
but unprojectable, and one such row cannot stop reconciliation of later sources.
Every restricted Master turn recycles its ACP process and rebuilds history solely
from its captured epoch, preventing old Container context from surviving in runner
or model caches. The shared frontend projects the existing ordered Master message
ids into Roving, Fleet, and Container histories without copying a session. A
Container projection includes Focus-attributed segments and messages whose
immutable subject is that Container; Fleet requires positive Fleet attribution and
excludes those subject messages. Message Focus, subject, target, and Area ids survive
Container deletion, and the unavailable historical folder remains selectable.
History folder changes for available Containers deliberately request durable Focus,
while unavailable folders are read-only and shell Container selection is independent
unless the owner explicitly chooses `Focus Master here`.
See [ADR-0007](../adr/0007-master-focus-is-a-durable-execution-boundary.md).

### Native artifact review flow

`ArtifactViewer.tsx` remains the universal renderer boundary rather than routing
ordinary deliverables through Design Studio. Its v2 workspace composes the existing
image/video/PDF/HTML/Markdown/JSON/CSV/text renderers with a normalized point-annotation
layer and review panel. Unsaved review notes live browser-side per `(project, path)`;
unknown, binary, and directory-like paths bypass text loading and render the download
fallback immediately.

**Add feedback to chat** resolves the record's existing `session_id` (or the chat that
opened the artifact), returns to that session, and seeds the ordinary `Composer` with
path-linked feedback. The user can edit and send it through `POST /api/sessions/{id}/runs`
like any other prompt. A successful handoff first validates that both the producing
session and its project remain available, then closes Artifact Review, selects that
scoped project and Chat, and focuses the seeded composer. A missing producer or project
leaves the review open with an actionable error. Composer drafts are isolated per
session and per project's new-chat scope, so cross-project navigation preserves the
source draft. If the producing chat already has unsent text, an explicit dialog offers
to append the feedback while preserving both drafts or keep the current draft unchanged.
No external polling process or review URL is involved.

Artifact Review is a modal dialog with a screen-reader name and description. It moves
initial focus to its close control, traps Tab and Shift+Tab, closes with Escape, and
restores the element that opened it after an ordinary close. The successful Chat
handoff deliberately transfers focus to the composer instead of restoring the trigger.

Markdown Mermaid fences and standalone Mermaid files use a lazy renderer. Choosing
**Edit as whiteboard** lazy-loads `@excalidraw/excalidraw` and
`@excalidraw/mermaid-to-excalidraw`, converts supported diagram structures to editable
shapes, and writes only on explicit save through the existing jailed project file API.
Scenes live at deterministic `artifacts/whiteboards/*.excalidraw` paths and carry the
source fingerprint. A mismatch offers keep-edits versus rebuild-from-source; saving
adds the scene path to the chat review draft. Excalidraw and Mermaid stay out of the
initial app bundle and load only when their artifact path is used.

```text
ArtifactViewer render -> point notes / general note -> Add feedback to chat
        |                                           -> producing Proxima session
        +-> Mermaid preview -> Excalidraw edit -> save project scene -> feedback path
```

## Key flows

### 1a. Master delegation and unattended queue

Durable persistence migration always runs. Identity provisioning, the runtime, supervisor,
routes, navigation, and settings surface require
`feature_master_orchestrator`, which defaults off. With the flag off, startup and
unrelated authenticated routes do not provision a Master runner home, queued Master
turns and Master-owned Task runs remain queued, and Master operational failures
cannot break unrelated routes. Migration ambiguity still fails closed.

```text
GET /api/runners/detect
      -> resolve binaries on the server-controlled runtime PATH
      -> with ingress admission, apply conformance, including minimum version
      -> when fenced or not admitted, skip process probes and fail eligibility closed
      -> publish masterChatOnly + masterEligible + masterUnavailableReason
      -> Master selector enables only masterEligible=true
      -> settings and runtime repeat conformance before mutation or spawn

Master nav -> GET /api/master/desk -> ensure hidden Master profile + mode='master' session
      -> reject selected runner unless its spec proves master_chat_only
owner message -> queued Master chat-only run
      -> validate owner-scoped Focus + auto/explicit Container/Area target
      -> store message + master_message_context + run + Focus in one transaction
      -> dedicated managed home + empty read-only non-source scratch
      -> strictly reapply {"skills":[],"mcp":[]}; deny permissions/native tools
      -> Codex firewall replaces all tools and developer context with server-owned policy
      -> assistant calls a native dynamic Proxima product function
      -> schema validation + per-turn call ledger
      -> MasterToolBroker executes a bounded filesystem-isolated handler in process
      -> trusted bounded result returns through app-server dynamic dispatch
      -> delegate_tasks/start_tasks call TaskDelegationService
      -> atomic jobs + delegation audits + dependency edges
      -> isolated worktree cut when needed -> job-scoped checkpoint -> runs queue
      -> RunWorker claims at most 3 Master runs; every excess worker run stays visibly queued
      -> MasterProjectionService appends concise thread messages + typed session events
      -> one authenticated MasterStateProvider resumes the durable cursor
      -> Master home + popup share thread, composer, Focus, target, active run, and scroll
      -> named durable transitions may coalesce into one focus-neutral shell toast
      -> global Attention deep-links owner decisions
```

There is no agent-to-localhost control plane. The streaming parser rejects malformed,
nested, oversized, duplicate, unknown, and disallowed envelopes with stable errors
written to the Master thread. The broker's closed JSON schemas admit only bounded
product IDs and text. Results never include absolute host or internal graph paths,
runner homes, bearer material, or configuration; `query_context` may include
validated scope-relative citations as provenance. Request, result, round, call, and
aggregate output caps fail before a truncated envelope can become a hidden action. The
`master_tool_calls` ledger binds each envelope hash to the durable root turn; mutation
idempotency is derived from that identity.

Master itself has no auto-approval path. Every runner-native permission request is
denied, and every native tool event fails the turn. Master-created Tasks preserve
their own Guarded or Autonomous execution policy. Autonomous may use the existing
scoped approval path, Guarded may not, and repo Tasks always stop at landing review.
The owner's global auto-approve setting for ordinary Chat remains unchanged.

`RunnerSpec.master_chat_only` is the centralized conformance declaration. Routes
reject an unsupported runner before creating a message or run, and the worker checks
again before process spawn. Codex app-server 0.145.0 or newer is the one conforming
production adapter; all other production adapters fail closed. See
[runner-conformance.md](../runner-conformance.md).

`GET /api/runners/detect` also evaluates that conformance against the server's
controlled runtime path and publishes `masterEligible` with an exact
`masterUnavailableReason`. The Master selector enables only dynamically eligible
entries. An unavailable stored selection remains visible only as a disabled
explanation. The browser result is advisory presentation data: settings, message
creation, and worker spawn each repeat the server check. During pending or active
external maintenance, and while exclusive ingress remains held during fence
removal, runner discovery retains read-only binary detection but skips
process-backed conformance and reports Master ineligible with a maintenance
reason. This applies to both the runner endpoint and the dashboard projection;
probes resume only after ingress admission resumes.

Codex conformance has two pre-turn gates: strict version parsing and a behavioral
app-server handshake that registers the exact server-owned dynamic schemas on an
ephemeral thread. The private loopback firewall then accepts only its secret
Responses route, reconstructs the attested broker carrier, and fully buffers bounded
identity-encoded provider responses. It does not release partial oversized or
redirected responses to the runner.

Interactive Master is quiet until asked. The desk can enable unattended mode; the
`MasterSupervisor` then starts already-queued Master jobs within turn and wall-clock
budgets and the configured `master_max_parallel` active-run limit. Dependency-blocked
rows do not consume a start slot. Each start revalidates the canonical owner,
project-unbound Master session, active Container, exact Area, worker session,
Task-agent profile, delegation audit, and prerequisite state. Immediate SQLite
transactions reserve job capacity and unattended turns across server processes.
Running jobs between their job claim and first run commit count as reservations, and
ready graph branches share the same global limit. The optional token value is stored
and shown, but current ACP events expose no usage counter, so turn + wall-clock are
the enforced Master budgets today. Budget exhaustion disables unattended mode and
creates a `master_budget` attention item.
Git commit/push/PR remains ordinary job work through the existing BYO environment.
Destructive install administration is not in the unattended allowlist.

Authority is singular: **Master dispatches and prioritizes; satpam alone detects,
steers, or restarts stuck runs.** Master never calls satpam restart machinery.

`MasterProjectionService` projects important Task status, checkpoint, Attention, and
Satpam rows into the same durable Master conversation. One
`master_projections` row links one concise `messages` row and one named Master-session
event to the authoritative source row. Unique owner-scoped projection keys make
retry, reconnect, and restart reconciliation idempotent. Cross-surface Task mutation,
outbox ordering, recovery history, and deletion identity live in
[1d. Cross-surface Task reconciliation](#1d-cross-surface-task-reconciliation).
See [master-supervision.md](../master-supervision.md).

The authenticated application mounts exactly one `MasterStateProvider` above
`AppShell`. It owns the canonical Master desk/session, ordered messages, active turn,
resume cursor, one `EventSource`, reconnect reconciliation, unread state, composer
draft/selection, Focus, target, Fleet data, popup state, transient toasts, and stable
scroll state. `MasterScreen` and `MasterPopup` are view-only consumers and
never mount their own stream, store, polling loop, or draft owner. The hidden home
does not render a composer while another surface is active.
When the Master feature is enabled, Delegate keeps the shared AppShell sidebar and
its focus-managed mobile drawer behavior. Its desktop navigation is fixed alongside
the desk without hide/collapse or resize controls. Its distinct
global navigation is Master, Tasks, and Archive only. Delegate passes no Work active
project into the Master desk and suppresses project filtering and Work-only escape
paths from its Tasks and Archive views, while preserving task and archive-record deep
links in the same mode.
Fleet work, Decisions, and Safety are independent native accordions, open by
default, with each underlying list constrained to a three-entry internal scroll
viewport. Durable product-tool result messages render as collapsed, human-readable
Master-update disclosures. Explicit expansion reveals linked Tasks, failure context,
and a bounded raw envelope for audit; raw JSON is never the default conversation
surface, including while a result is still streaming.
Owner-keyed session storage restores draft, selection, and scroll after browser
refresh; the target remains an owner-keyed local preference. Neither store restores
server-owned Focus.
Lifecycle generations plus abort controllers reject late owner/token/session
responses, close replaced streams, and keep React StrictMode from creating two live
connections or duplicate UI submissions. Projection and final-message events are
deduplicated by durable ids and ordered by the server cursor. Raw message,
reasoning, and tool deltas advance cursor/sequence tracking only and are never
rendered. Bootstrap reads the desk's constant-size durable `event_cursor` barrier
before the final authoritative desk/message snapshots and opens the stream there
without fetching the full event history. A successful message
submission returns its canonical persisted user message so the pending row gains a
durable id before a streamed reply can reorder it. Desk/messages/events
reconciliation is bounded and recovery-only after reconnect, cursor gap, malformed
event, or explicit retry, not a primary poll. The SSE stream
emits an immediate comment on an idle connection so browser `EventSource.onopen`
reports the healthy transport without waiting for the keepalive interval.

Each accepted owner message gets one `master_message_context` row plus immutable
`message_focus` attribution. The API validates
every referenced Container against the authenticated owner and every Area against
that Container. Explicit targeting changes the durable current Focus to the target
Container inside the same transaction that inserts the message and run; the run
captures that epoch before it can execute. The run's stored prompt remains the raw
owner text; trusted routing ids are appended only while building the restricted
runner prompt. The runner process is recycled and its bounded durable history is
filtered to that captured epoch before every Master turn. `MasterToolBroker` overrides model
Container/Area arguments for explicit targets and confines automatic routing to a
Container Focus. Fleet Focus discards model-supplied Container and Area graph
scope, so automatic routing can read only Fleet and Live layers. Deleting an idle
focused Container first closes its epoch and transitions durable Focus; the
historical epoch, message Focus, subject, target, and Area keep their immutable
numeric identities. An active Master turn refuses deletion before any filesystem
change.

The shell popup is available only on ordinary authenticated surfaces. Auth,
onboarding, the full Master home, update application, drawers, search, account
menus, and tool panels suppress it. It persists a left/right corner preference and
uses tokenized collision offsets for sidebar, ToolDock, mobile chrome, toast region,
and system safe areas. The desktop presentation is a modal dialog with a focus trap,
Escape close, and trigger focus restoration; narrow viewports use a sheet. The
toast region maps only named projection events that carry a durable message id.
Stable source keys coalesce Task progress, terminal source keys prevent duplicate
completion toasts, and raw delta events are ignored. Toasts use polite or assertive
live regions without stealing focus and preserve the existing optional background
desktop notification path.

### 1d. Cross-surface Task reconciliation

Externally mutable Task transitions share one authoritative projection path. A
database-maintained Task generation advances only when the canonical projected state
changes. Ordinary step, node, timestamp, and same-status progress reuse the current
key, while transitions such as Running to Review to Running receive distinct keys.
Review verdict transactions write the Task invalidation and `task_projection_outbox`
intent together; projection delivery happens only after commit and remains replayable
if it fails. Per-Task delivery follows durable Task-event order. Checkpoint restore
records a bounded `task_recovery_outbox` intent and marks only obsolete unpublished
status intents as causally superseded before emitting the authoritative Queued
recovery event. Recovery audit intents remain append-only. New and still-orderable
audits publish exactly once in Task-event order. Missing legacy Focus leaves each
restore as a failed-attribution repair row without rolling back Task restoration or
publishing unattributed history. Projection schema upgrades retain unpublished
predecessors and already-projected publication reversals in an immutable per-Task
ordering-gap ledger without replaying or rewriting original recovery rows. Delivered
legacy partial correction markers, messages, and events remain immutable, and an exact
coverage ledger links them to their causal gaps. One active per-Task aggregate intent
summarizes only still-uncovered bounded gap counts and predecessor/successor
Task-event ranges, and emits at most one new history marker after ordered outboxes
and the canonical current Task projection are settled. Still-orderable predecessors
remain on the normal recovery path.
The v48 compatibility migration stages every delivered marker row and its exact
coverage before aggregation. V49 restores only from that evidence and records
bounded legacy identity loss for databases already damaged before staging existed.
Source deletion enters one capture path from `BEFORE DELETE` triggers on the job,
authoritative Task session, Task event, and recovery outbox. It preserves stable
job, event, and outbox identities plus the exact outbox-to-event map, copies marker,
gap, and coverage rows into immutable history tables keyed by their original ids,
and writes or safely completes the Task-source tombstone. Task-session identity
comes only from `jobs.session_id` or one consistent set of outbox-referenced Task
events, never generic graph-session membership. If neither survives, it remains
`NULL` and an immutable bounded loss row records why. Only then may the live
cascades proceed; later boundaries cannot rewrite captured identity.
Job API payloads also attach one `run_projection` and normalize timestamps so Tasks,
Workflows, Attention, mounted Task detail, Fleet, and expanded nodes read the same
effective lifecycle. Mounted Task detail consumes Task-session `job.update` for owner
mutations outside a worker run.
The rationale, alternatives, ordering rules, legacy containment, correction-marker
trade-offs, and authoritative deletion identity are recorded in
[ADR-0027](../adr/0027-durable-task-reconciliation-protocol.md).
Projection message, event, and ledger rows then commit together. Startup validates
their strict owner, source/type, foreign-key, index, complete-link, and bounded payload
contract. Raw streaming deltas are never projected. Each named event carries the same
captured Focus and subject attribution committed with its message, so the live
projection cannot drift before canonical reconciliation. Server-owned summaries omit
Task titles, runner errors, permission commands, Attention text, Satpam reasons, paths,
and credentials. The existing session SSE cursor accepts both `after_id` and
`Last-Event-ID`. See [master-supervision.md](../master-supervision.md) and
[task-delegation.md](../task-delegation.md).

### 1. Chat turn (the core loop)

Before submission, every project-scoped composer can resolve `@query` through a merged
index of `GET /api/projects/{slug}/reference-files` (path-only file tree) and
`GET /api/projects/{slug}/artifacts` (typed produced deliverables carrying title +
kind). Neither endpoint reads or inlines file content. Reference-file traversal is
capped, skips symlinks and dependency/build/cache/hidden trees, and suppresses common
secret/key filenames. The shared frontend loader (`useProjectMentionItems`) merges the
two (artifacts ranked first, winning path collisions), rejects stale project responses,
and refreshes after file changes. A selected ordinary file is sent as its relative path. A selected image is
sent as Markdown image-reference syntax: ordinary ACP agents can still open the path,
while `/image` and design flows resolve it again inside the session project jail and
attach bounded image bytes as visual input.

First-class method commands also enter through this run seam. In particular,
`/masterplan <idea>` stays visible in the transcript as typed, while
`commands.agent_turn_for_command` expands the queued prompt into an explicit
`bundled/masterplan` methodology instruction and tags the run `kind='masterplan'`.
Before ACP setup, the worker checks whether the session has had that run kind and
includes the bundled skill in an otherwise explicit profile capability subset; saved
profile choices are unchanged. This session-scoped requirement keeps the skill active
for ordinary chat turns that answer the methodology's clarification and review gates.
Starting this method command also cancels a blocked goal on the same session, so a later
clarification reply cannot resume stale goal instructions. A bare command asks the agent
to collect the idea first, and natural-language skill invocation remains available.

```text
UI  @ picker (files + artifacts) ─────► relative path / explicit image reference
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
    └─ if an ACP tool event occurred and bounded file comparison found changes:
       turn_file_journals row -> preview paths -> confirmed restore-turn endpoint
```

Normal project Chat snapshots bounded eligible files at the turn boundary and uses ACP
tool events as the journal trigger. Only changed paths and their pre-turn bytes are
persisted; dependency/build/cache/git/media paths and oversized files are skipped.
The journal lives for the session, previews every impacted path, and warns before an
owner restores while Master work is active in the same project.

Runs are per-session serialized and bounded-concurrent globally; a heartbeat +
reaper fail hung runs, and a per-turn quota cancels stragglers. The quota
(`run_timeout_seconds`, default 900s) is a first-class **in-app setting** stored in
`app_settings` (Settings → Agents → Turn quota), read per run so it applies on both
entrypoints (`scripts/serve.py` and `uvicorn proxima_api.main:app`) without a
restart; config/env (`PROXIMA_RUN_TIMEOUT_SECONDS`, mirrored on both entrypoints) is
the fallback default. Completion updates are guarded by the current run state, so
cancel wins over late media, review, collaboration, draft, or graph finalizers. Failures
during pre-ACP setup are finalized immediately rather than waiting for the reaper.

**Timeout auto-continuation (Phase-1 slice 5, T5):** when a *job* run (linear step or
plan node) hits the quota, the worker salvages the streamed text, marks the run failed,
and enqueues a **continuation run in the same session** — the persistent ACP session
keeps the agent's context, and a repo job's cwd re-binds to the same worktree so file
edits persist. The prompt is a genuine resume ("inspect the current state of your work,
continue from where it stopped"). A graph node stays `running` and is re-attached to
the continuation via a guarded `running→running` run-id swap in `node_states`. The
chain (`runs.continued_from_run_id` / `runs.continuation_count`) is capped by
`run_continuation_limit` (config, default 5); at the cap the job fails loudly with a
plain-language reason and a plan pauses for review — never a silent stall. Chat,
goal, collaboration, and review runs keep the plain fail-on-timeout path. The
satpam (slice 12, flow 6c) reads continuation counts as a confused-agent signal
and records the cap as an escalation; restart-clean (worktree discard) stays a
supervisor/owner decision, never automatic.

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
and surface in Settings under Agents. The composer resets to
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
  reusable Workflow (`POST /api/graph/jobs/{id}/save-template`) is an optional, separate
  act, available before or after the run, from the canvas or from a Tasks plan row.

### 6. Workflow → Job → execution

```text
workflow (recipe: steps JSON + typed {{inputs}})
    │  run / iterate
    ▼
job = frozen snapshot of steps + per-step state (steps_state JSON)
    │  steps run sequentially in ONE ACP session (context carries across steps)
    │  review-gate steps pause → Approve / edit-&-continue
    ▼
job done  →  artifacts surface in the Result view + land as durable Archive records
             (registry feed; approving the job auto-approves its records - T4)
```

Scoped Task creation enters through one server-owned path:

```text
Work / Home / Master / future Master caller
    -> TaskDelegationService.create_and_start
    -> validate owner + one Container + one Area + Task-agent + Recipe
    -> one transaction:
         worker session + job + task_delegations + task_dependencies
    -> commit durable start_requested and idempotency identity
    -> retryable start:
         unmet prerequisite -> queued + explicit blocked_reason
         repo Area -> existing external worktree and review/local merge
         Ops Area -> existing physical ops/ execution
    -> MasterProjectionService:
         concise Master message + typed event + source-link idempotency row
```

The service accepts a batch of client-local Task keys and dependency keys. It inserts
all jobs first, resolves edges second, and commits only if the whole DAG is valid.
Repeated idempotency identities return the original jobs before mutable references are
revalidated. Startup retries committed start intents, so interruption after commit but
before start cannot duplicate a Task. A graph start also reconciles the narrower crash
gap where the job reached `running` before the graph dispatcher committed a node run.
When a prerequisite reaches its required state, the dependent is retried through the
same guarded queued-to-running claim; repeated notifications create no second run.
Deleting a prerequisite is refused until its dependents are deleted.

The gated graph sibling freezes `{nodes,edges}` on a job, stores each node attempt in
`node_states`, dispatches every ready node concurrently (bounded by
`graph_node_concurrency`, then by `run_worker_concurrency`) in a fresh hidden ACP
session per attempt, and passes only explicit typed upstream outputs. Each node may run
as its own agent. Plan graph edits are allowed only while queued and autosave through a
debounced PATCH that flushes before navigation, promotion, or Run. The inline title can
be renamed at any stage. Run remains an explicit owner action without reusing approval
language; final result approval remains the review gate. Review/correction can edit or rerun a node and
marks every transitive descendant stale before redispatch.

`GraphScreen.tsx` is the gated control plane for this sibling engine. It is a canvas-first,
n8n-style surface — drag nodes, pan/zoom, drag-to-connect, click-to-remove a connection —
built on native SVG, so no graph library is required. One slim bar holds the inline
title, live plan status, and passive save status; the draft footer contains exactly Run
and one-click Save as Workflow actions; and the
node inspector is rendered only while a node is selected, so no unused panel holds canvas
width. The workspace is flex rather than grid precisely because those two panels come and
go. `graphLayout.ts` supplies deterministic
topological columns as a *fallback*: a node's hand-placed `x`/`y` wins, and the layout
reports a real bounding box because the canvas is infinite and positions may be
negative. Node positions are part of the graph and are persisted by the same explicit
autosave as every other queued plan edit. Pending debounce work is flushed on editor
exit so switching views cannot restore a stale graph.
Drag-to-connect is pointer-only, so the inspector's dependency checkboxes remain the
keyboard path to the same edges. The screen allows node/dependency/layout edits only
while queued, and exposes the correction and approval protocol once execution begins.
Saved graph templates are listed and reused only through the gated graph surface;
classic workflow lists and execution remain strictly linear.
The graph template library loads active and archived rows with
`GET /api/graph/templates?include_archived=true`, then projects them into separate
views. Archive and restore use the existing status mutation, so the row keeps the same
`project_id`, graph, and run lineage. Archiving snapshots the prior status into
`workflows.pre_archive_status`; restore reinstates and clears it, so a paused (`draft`)
workflow returns paused (legacy rows with no snapshot restore to active). Archived rows
do not schedule or appear in the default list API; permanent deletion is exposed only
from the archived view.

The **Tasks screen** (`ActivityScreen.tsx`) is the index of plans + their jobs (T2):
graph plans appear alongside classic linear tasks, and a plan row expands into its
ordered job list — each job showing its name, target badge, touches-repo marker, and
live status (`planProjection.ts` computes the deterministic order and joins
`node_states`). List view and graph view are two projections of the same plan:
branch-less plans read as a plain list, and branching plans offer the read-only
dependency canvas as a toggle (the same `GraphCanvas` component the editor uses —
extracted, not duplicated). Plan rows also carry **Open plan** (to the canvas, where
review actions live) and **Save as Workflow** (the same save-template mechanics).

Ad-hoc single-step work is just a 1-step job (old kanban `tasks` were migrated this
way). Jobs live-poll while running and auto-archive after 30 days. A dependency-blocked
Task remains queued but carries its durable reason in list/detail payloads and renders
that reason in `TaskWorkspace`.

Before a Master worker run is enqueued, `job_checkpoints.create_checkpoint` records
only that job's restorable columns, node states, existing run ids, and target repository
SHA / worktree identifiers. A repo job cuts its isolated worktree first so the checkpoint
can restore that worktree; the worker still has not started. The 31st unpinned row
evicts the oldest unpinned row; pinned
rows are excluded. Restore has a separate impact-preview route, requires explicit
confirmation, refuses while a job in the project is running or a later project job
could depend on the current refs, and preflights a restorable worktree for dirt and a
valid commit before changing database state. After preflight it acquires an immediate
write transaction and rereads the checkpoint, conflicts, current job, runs, and node
state before any mutation. It removes only post-checkpoint runs and restores the one
job/node set transactionally. Main-checkout SHAs are reference-only; only an existing
job-owned worktree can be hard-reset, so unrelated project work is never rewound. It
never VACUUMs SQLite and never archives a project tree.

### 6b. Repo job: worktree → diff review → local merge (slices 2+4, live)

Gated behind `PROXIMA_FEATURE_REPO_WORKTREES` (on by default since slice 4 shipped
the review UI; off is the escape hatch = flow 6 exactly). A job whose
`target_area_id` names a code area is a **repo job** and never edits the primary
tree:

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
GET /api/jobs/{id}/diff  →  snapshot outstanding edits onto the job branch
    (runtime cache/bytecode like __pycache__/*.pyc is dropped from the
    checkpoint so a missing .gitignore cannot pollute review or merge),
    then per-file status + unified patch vs base_commit (slice-4 review surface;
    the same noise paths are omitted from the rendered file list/patch)
    ▼
POST /api/jobs/{id}/approve (final step)  →  guarded local merge --no-ff into
    the branch the worktree was cut from (T1 local-first)
    ├─ success: merge_commit recorded on job_worktrees, worktree + branch torn
    │  down - then, ONLY if the code area's push_on_merge toggle is on (T9,
    │  slice 11, default off) AND the repo's remote URL still matches the one
    │  pinned at opt-in (audit F3), a hardened `git push` via the host's own
    │  git (credential helpers + hooks neutralized). A failed or refused push
    │  never un-merges and never fails the
    │  approve: push_status='failed' + the exact command output land on the
    │  job_worktrees row and surface as a blocker card with a retry action
    │  (POST /api/jobs/{id}/push, either engine).
    └─ refusal/conflict: 409, job PARKS in review with the surfaced error;
       worktree kept - resolve, approve again to retry. Never forced.
POST /api/jobs/{id}/reject  {reason}  →  the other verdict door (slice 4, either
    engine): job → failed with jobs.rejected_reason recorded; the worktree is
    discarded UNMERGED (flag-independent teardown, like delete) - the primary
    tree never sees the change. A blank reason is refused (422).
```

Lifecycle state is one `job_worktrees` row per job
(`active → merging → merged`, with `conflict` and `discarded` as off-ramps);
deleting a job tears its worktree down. Snapshot-then-merge means partial agent
work is durable in the worktree across crashes - the substrate slice 5's
continuation turns (T5) resume in.

**The review surface (slice 4)** renders this flow captain-side, in T4's ratified
detail language (expanding row + full-width page; no side panel, no popup):
`components/tasks/ChangesReview.tsx` is the one shared surface, mounted in a plan
row's expanded body on the Tasks screen (approve = `POST /api/graph/jobs/{id}/approve`,
held while any plan job still awaits its own node review) and on the full-width task
page (`TaskWorkspace`, approve = `POST /api/jobs/{id}/approve`). It shows the per-file
list and unified change from `GET /api/jobs/{id}/diff`, keeps the merged result
readable afterwards, surfaces a conflict as a plain needs-attention banner (job parked
in review, retry offered), and gates the reject door behind a required one-line
reason. UI copy is de-jargonized ("isolated copy", "changes"); the satpam (slice 12,
flow 6c) consumes these same review states.

**Graph plans reuse this same machinery per job-in-plan (slice 3).** Direct legacy
plans keep the existing node-aware placement. Delegated graph Recipes inherit the
Task's exact Area for every node, so one Task never crosses from a repo worktree into
Ops. When the flag is
on and a plan has repo jobs (nodes with `touches_repo`), `POST /api/graph/jobs/{id}/start`
resolves their one code-area target to `jobs.target_area_id` and cuts the plan's
worktree before claiming `running` — same loud-refusal ordering as the linear start. A
plan's repo jobs must share ONE code area (Phase-1: one worktree row per job); a
multi-area plan refuses to start with a split-the-plan message. For a direct legacy
plan, the worker's cwd seam remains node-aware: a `wf_node` run executes in the
worktree only when its node touches the repo, while Ops siblings use the physical Ops
Area. For a delegated Task, every node uses the selected repo worktree or selected
physical Ops Area.
The final `POST /api/graph/jobs/{id}/approve` is the merge point, with the identical
guarded-merge/park-in-review contract as the linear approve. Flag off: none of this
runs and target tags are inert metadata.

### 6c. Satpam supervision loop (slice 12, T10, live)

```text
worker.loop() ──every sweep (satpam_check_seconds, Settings)──► Satpam.tick()
     │ (reaper cadence sibling: reaper owns DEAD runs, satpam owns alive-but-stuck)
     ▼
running jobs' continuation chains (runs.continuation_count > last evaluated turn)
     │  durable signals ONLY: worktree signature · salvaged-output hash · counters
     ▼
detection: stalled (no repo change ×N) · looping (identical output ×N)
           confused (continuation cap · repeated contract failure)
     │
     ├─ a. STEER (automatic, logged) ──► corrective note into the NEXT continuation
     ├─ b. RESTART-CLEAN ─ non-repo: automatic (fresh session/step-one re-run)
     │                     repo: PENDING approval card ──owner──► discard worktree,
     │                     re-cut from HEAD, re-run the plan's repo slice
     └─ c. PAUSE + ESCALATE ──► chain cancelled, job parks in review w/ plain reason
     every action: satpam_interventions row + satpam.* timeline event (no silence)
```

One fleet-level loop, hosted in `worker.loop()` next to the reaper gate and
self-paced by its Settings cadence — the seam mirrors firstmate's single watcher
and adds no per-job processes. It never reads an agent stream and never calls an
LLM; evaluation happens once per continuation turn (slice 5's chain ordinals are
the turn boundary), so a job that finishes inside its first turn is never even
read. Fail-quiet by contract: any internal error logs and the sweep moves on.
For a Master-owned Task, each durable intervention also produces at most one
Master-thread projection and one corresponding Master-session event. A failed
owner-approved repo restart remains pending, creates one stable
`satpam_recovery_failed` Attention row, and projects that failure once. These
projections do not alter Satpam policy or grant control authority.
**Decision-hold (T10 #4):** the node prompt defines the `DECISION_NEEDED: <question>`
output-contract marker. The graph advancer parks such a node in the existing
`review` state with the question on `node_states.question` while the JOB stays
`running`: independent DAG branches keep dispatching (the one-parked-node-freezes-
the-plan rule is relaxed exactly here), dependents hold naturally because their
dependency never reaches `done`, and when the independents drain the plan parks.
`POST /api/graph/jobs/{id}/nodes/{node_id}/answer` (usable while the plan runs)
stores the answer, re-runs the node with the decision in its prompt, and resumes.

### 7. Schedule (cron)

`schedules` rows carry a 5-field cron + overlap policy. The scheduler loop wakes each
minute, finds _due_ schedules (matching the current minute, not a backlog), and
materializes a `job` for each — respecting `overlap_policy` (skip / allow). A scheduled
graph recipe goes through the same `bind_graph_job_repo_worktree` path as manual plan
start (pin `target_area_id`, cut isolated worktree); a refused cut fails the job with
an owner-facing reason instead of running unisolated.

### 8. Run & Preview app

`POST /api/projects/{slug}/app/start` → `AppManager` launches one owner-confirmed dev
process for the project with a filtered environment. The requested port is only a
candidate. Status is a discriminated state:
`stopped | starting | ready | port_conflict | ownership_unknown | exited`.
Only `ready` carries a proxy target. An uncontained launch requires Linux procfs
evidence that every listener socket belongs to its managed process group. A contained
launch instead requires every socket owner to match the exact launch-specific PID
namespace and lineage marker reported at start and retain positive live process-group
or ancestry evidence. Appview, relay, and preview subdomain paths repeat the applicable
proof on a fresh server-side socket before sending HTTP or WebSocket bytes. Starting,
conflict, ownership-unknown, and exited states return a non-proxy response. An existing
relay remains available to return that safe response until Stop releases it.

If an unrelated process owns the candidate before start, start returns a structured
HTTP 409 conflict. If it claims the port after preflight but before the managed app
binds, status becomes the sticky terminal `port_conflict` state and Proxima signals
only its own managed process group. It never reaches, signals, or terminates the
foreign listener. Missing or incomplete procfs evidence fails closed as
`ownership_unknown`. An uncontained child that detaches into another process group is
also ownership-unknown. When a scope is unadopted, Stop tries authenticated recovery
from durable supervisor evidence and otherwise returns HTTP 409 with an
ownership-unknown message instead of claiming success; start refuses a replacement
generation while that authority stays unresolved. Once `AppManager.start` accepts a
launch, it owns the ingress effect lease through cancel and failed-spawn cleanup. Bubblewrap reports the exact launch-specific namespace
identity at start; every contained socket owner must match it and carry the ephemeral
launch marker. The marker alone never grants authority. It keeps an observed
descendant that is later reparented ownership-unknown instead of becoming a
foreign-port conflict.

A preview only works served
root-relative on its own origin (absolute asset paths, HMR WebSocket to the page
origin), so `PreviewRelayManager` starts a per-app listener on the Proxima host for
local and remote browsers
(`preview_port` in app status; interface via `preview_bind_host` /
`PROXIMA_PREVIEW_BIND`, default `auto` = one shared port bound separately on loopback
and the Tailscale interface when present, otherwise loopback only - never `0.0.0.0`
unless set explicitly; `off` disables) - the app's own
origin by port. The relay guards only its own port: the dev server itself is defaulted
onto loopback (suggested commands bind `127.0.0.1`, `HOST=127.0.0.1` in the child env)
and app status flags `broad_bind` when its port is found listening beyond loopback,
because that listener is LAN/tailnet-reachable with no auth. A self-exit is reaped into
a sticky `{exited, exit_code, log, command}` status (kept until the next start) so the
UI can distinguish Finished vs Failed after short-lived commands; its relay returns
HTTP 503 until Stop or the next start. A start that has no listener after 15 seconds
becomes a prolonged-start warning with Stop and log controls instead of spinning
forever. The bounded 40-line status buffer and Logs toggle remain available while
starting, ready, conflicted, exited, or stopped. Reloading the preview does not close
the log panel, and explicit Stop awaits stdout draining so the most recent buffer,
including terminal shutdown output, remains available for the stopped/retry
state. A launch-time supervisor creates the process and owns its child pipe, so the
API never has to transfer process or output ownership. It drains all currently
available bytes before returning the final snapshot. Complete
lines use a bounded ring and the pending partial line has its own fixed byte bound, so
newline-free streams cannot grow memory without limit. Periodic status polling uses
a version and completed-line cursor, so an unchanged log returns constant-size
metadata instead of retransmitting the ring. If an uncontained child keeps stdout
open, the supervisor continues fixed-size reads after the API disconnects and exits
only after EOF and an empty managed app cgroup. Packaged Linux installs create a
delegated, launch-specific app cgroup beneath
each socket-activated profile supervisor outside the API cgroup. The broker remains in
the unit root and process-only unit teardown cannot signal an app that escaped its
managed cgroup. A durable pending record precedes supervisor creation; atomic
broker-attached and app-attached phases bind the profile, protocol, supervisor
PID/start time, app PID/start time, app cgroup, and lineage. Restart adoption requires
every field to match. Startup and shutdown reconcile app generations concurrently
within aggregate service deadlines. Unit migrations scan same-user procfs first and
refuse while an older protocol process or a pre-protocol preview identified by API
lineage or service-cgroup membership remains live. Windows uses a detached breakaway supervisor
when supported. Supervisor setup failure occurs
before app spawn and produces a recoverable `output_sink_unavailable` stopped state;
a later disconnect retains fail-closed authority instead of claiming an unverified
stop.
Conflict feedback keeps the candidate port visible with Stop, retry, and
change-port actions. When
`apps_domain` is configured, `PreviewProxyMiddleware` instead serves a
`preview-<slug>.<apps_domain>` subdomain. Both share one proxy engine
(`preview_proxy.py`): HTTP + WebSocket forwarding with Host rewritten to
`127.0.0.1:<dev port>`, gated by a one-hour, signed `proxima_preview` capability that
is unrelated to the owner API session (minted host-scoped by `POST /api/preview-auth`,
so the browser also sends it to relay ports because cookies ignore ports). Capability
authentication runs before target resolution or procfs ownership work. Proxy paths remove
Cookie/Authorization before forwarding and ignore upstream `Set-Cookie`;
same-origin/generated HTML previews omit `allow-same-origin`. These are lightweight
self-hosted mitigations, not OS isolation of the project process.
See ADR-0014 through ADR-0026 for the focused binding, authentication, authority,
cleanup, framing, supervision, restart-adoption, and profile-isolation decisions.

### 9. Update check and candidate gate plus disabled switch fixture

```text
VERSION (repo root) → read_local_version() → FastAPI app.version → GET /api/health
                                    │
UpdateManager: every 6h → GET api.github.com/repos/<repo>/releases/latest
                           (never raises — offline/404/hiccup → last_error)
                                    │
   GET /api/update/status · POST /api/update/check (metadata only)

App integration
  ├─ GET /api/maintenance → authenticated read-only external-fence projection
  └─ feature_safe_self_update (default off) → authenticated request/run projection
                                                  │
       external root-owned controller → native lock + fsynced hash-chained journal
                                                  │
       exact manifest/provenance tree → immutable releases / external fence / adapter
                            (legacy HTTP and CLI apply paths remain inert)
```

`UpdateManager` (`updates.py`) is the one thing that phones home: an
unauthenticated GitHub Releases GET on a 6-hour timer (first check 60s after
boot), holding only in-memory state (current version, latest release,
`checked_at`, `last_error`) — `PROXIMA_UPDATE_CHECK=0` disables just that
loop (the manual check route still works) and `PROXIMA_UPDATE_REPO` defaults to
`labsiqbal/proxima`; forks can point it at their own repo. `apply()` now always
fails closed. Group 14 adds the feature-gated `/api/self-updates/*` request and
run-status projection plus the always-available authenticated, read-only
`/api/maintenance` fence projection. `self_update_runs` is an owner-visible mirror,
never the source of promotion truth. Both production entrypoints read
`PROXIMA_FEATURE_SAFE_SELF_UPDATE` and the optional absolute
`PROXIMA_SAFE_UPDATE_FENCE_PATH`; the flag defaults off and a configured fence is
read-only application status.

The external updater owns exact signed regular-file manifest verification, canonical
Python/web lockfile digests, local provenance revalidation, native POSIX/Windows
single-flight locking, platform-selected directory durability, the journal, and the
recovery decision. Local provenance additionally binds normalized file modes and
safe in-tree file-symlink targets; authenticated-source copying materializes their
target bytes into fresh regular files. Signed or local verification returns an
immutable verified file set bound to the candidate commit and, for signed releases,
the release identifier. Release publication accepts only that result, traverses
candidates from pinned directory descriptors, copies content into fresh
controller-owned staging inodes, revalidates digests and normalized modes, freezes
the tree, and atomically renames it inside the trusted releases directory. Source
ownership, ancestor substitutions, symlinks, and hardlinks cannot carry into the
published release. Every created trusted directory and its parent are durably
flushed.

The authenticated request projection asks the external authority first. A newly
accepted external run supersedes stale local requested/in-progress rows; a local row
never vetoes submission. The API package reads the nonsecret root-owned fence without
importing repository-only controller code. Its dedicated parent and file are
searchable/readable but not writable by the application identity. A nonterminal
journal continues to own single-flight after the kernel lock is released. Missing,
truncated, unterminated, unreadable, or hostile-path journals produce
`do_not_start_any_release`, including through the machine-readable recovery CLI.
systemd, launchd, and unmanaged adapters remain unmanaged until the qualification
matrix in
[`adding-safe-updater-adapter.md`](../adding-safe-updater-adapter.md) passes. See
[ADR-0008](../adr/0008-external-safe-update-authority.md).

Before the fixture-only fence and switch exercise, Group 15's controller-only
candidate gate reverifies local provenance in trusted controller code. Git checks
and every candidate-controlled command run inside the same mandatory Bubblewrap
execution boundary. The boundary exposes only read-only system inputs and
phase-specific candidate-local writable mounts, removes network egress, uses a
namespace identity without host privileges, applies resource and output ceilings,
and kills the complete process group on timeout. A fixed offline
build/test/type/doc manifest runs in a disposable writable tree. The controller
then rehashes that post-build tree, copies it to fresh release inodes, freezes it,
and runs probes only from that frozen release.

SQLite's backup API creates a clone in its own writable directory. A fixed migration
entrypoint can modify only that clone, and the controller requires an exact,
contiguous `schema_migrations` ledger through the policy-pinned expected version.
The served fixture is created from migrated schema, not copied live rows: only
synthetic owner, authentication, project, and session data are inserted, with
separate candidate workspace and runner-home paths. Candidate-mode startup skips
schema mutation and every background writer. The separately installed, hash-pinned
probe suite starts the candidate inside a loopback-only namespace and requires API
identity, version, authenticated maintenance, SSE, served static assets, the complete
asset digest, and every trusted headless-browser scenario. Its Master scenario
asserts the accessible popup and Home bridge, labeled controls, server-derived
runner eligibility, and the absence of enabled unqualified choices. The runner
fixture crosses only the trusted auxiliary-tool boundary owned by
[Security Boundaries](../security-boundaries.md#safe-update-boundary). The frozen evidence tree
contains build logs, migration and fixture proof, identities, and probe results.
Recovery revalidates its journal-pinned digest and file set. Sandboxed candidate
commands cannot reach the journal, active or last-good pointers, fence, backups, or
production paths. After independently revalidating the evidence, the controller
appends its digest to the accepted-run journal as `candidate_staged`.

Group 16 supplies a disabled transaction model for explicitly initialized
disposable fixture roots beneath the system temporary directory only. It fixes the
fence at canonical `status/fence.json`, confines live and staged databases to
disjoint role directories, and holds the native single-flight lock. The fixture
fences mutating HTTP, preview and terminal WebSocket ingress, and SQLite writes;
pauses and drains the fixture service; verifies a truncate WAL checkpoint; seals
and validates backup images; quarantines WAL/SHM sidecars; changes fixture pointers;
runs read-only and writable proofs; and commits last-good only after the writable
proof.

Every exception before a successfully returned `last_good_committed` append
persists a rollback-required breaker verdict before restoring the sealed backup and
both previous fixture pointers, proving the previous fixture service, and
finalizing the breaker state. A full or partial unacknowledged journal write
latches fail closed after rollback, and the persisted breaker verdict takes
precedence over a valid-looking journal tail. Recovery also rejects a safe
candidate discard when owner-bound pending or active fence state was created before
`write_fenced` was acknowledged. A failure after the acknowledged last-good
boundary resumes the committed candidate or latches the breaker without rolling
back candidate data.

Maintenance activation durably publishes owner-bound pending state before taking
the exclusive side of a controller-provisioned cross-process ingress lock. An
interrupted activation cannot be adopted or cleared by a later run. The application
opens that lock read-only and never provisions controller status state. Startup
initialization, admitted HTTP requests, active agent and deterministic script runs,
project-app and preview proxies, and terminal sessions hold shared leases through
their possible effects. New mutating work fails closed while already admitted work
drains before the fence is published. Runner, script, project-app, and terminal
processes use PID namespaces under the configured boundary. Cached runners retain
lifetime admission between turns, and pending activation stops and positively
verifies them before releasing those leases. Script leases release only after
namespace exit, preventing detached descendants from outliving the drain.

Maintenance startup opens SQLite read-only, creates no PATH compatibility shim, and
starts no background writers. Connections configured for dynamic fencing disable
statement caching, allow database effects already covered by an ingress lease to
finish, and skip write-capable WAL setup; ordinary connections retain caching and
skip fence checks for read-only opcodes. Fenced reads avoid profile, wiki, relay,
provider-readiness, and legacy process-reaping mutations, while normal first-use
wiki reads still provision `index.md` under an ingress lease and the read-only
authenticated `/auth/resume` projection remains available. The only runnable
adapter is `DisposableServiceAdapter`; systemd and launchd remain unmanaged and
inert. No production pointer, fence, database, service, workspace, runner home, or
release can be touched.

## Runner abstraction

The app never hardcodes one CLI as the boundary. A _runner spec_ maps an installed
CLI to its command/argv, credential home, readiness check, wire `protocol`, and
default-model behavior. Runs carry a `runner_id`; `default_runner()` resolves env →
first _ready_ runner → fallback. Agents emit a **generic event vocabulary**
regardless of CLI:

```json
{ "type": "assistant_delta", "text": "..." }
{ "type": "tool.start", "title": "npm run build" }
{ "type": "tool.complete", "status": "completed" }
{ "type": "permission.request", "options": [] }
{ "type": "artifact", "path": "..." }
{ "type": "run.completed" }
```

**Grok runner (native ACP).** Grok's spec spawns the owner's official Grok Build
CLI as `grok agent stdio`. The CLI speaks ACP directly, so Proxima uses the normal
persistent `AcpProcess` path without an npm or editor adapter. New profile homes
seed `auth.json` and `config.toml` from `~/.grok`, refresh the auth file before a
run, and set `GROK_HOME` to keep profile state isolated. Detection marks Grok ready
only when both the binary and a non-empty JSON auth file are present; operators log
in with `grok login` or `grok login --device-auth`.

**Codex runner (native app-server, not the Zed ACP adapter).** Most runners speak
ACP through a persistent subprocess (`acp.py`, `AcpProcess`). Codex is the
exception: its spec sets `protocol="codex-app-server"` and spawns the owner's own
`codex app-server` (stdio JSON-RPC), driven by `codex_appserver.CodexAppServerProcess`
- a drop-in with the same call surface (`new_session`/`load_session`/`prompt`/
`cancel`/…) that `AcpManager` returns for that spec. Its `thread`/`turn` events are
translated into the same generic vocabulary above. This exists because
`@zed-industries/codex-acp` statically bundles its own Codex core, which lags
releases: the ChatGPT backend then rejects newer models (e.g. `gpt-5.6-sol`) against
it with a misleading _"requires a newer version of Codex"_ even when the owner's
`codex` CLI runs them fine, and the adapter offers no way to point at an external
Codex. Driving the system CLI directly keeps the runner current with every Codex
release; if that CLI is genuinely behind, the surfaced error now says so honestly
and points at `codex update`.

**Capability bundle (Phase-1 slice 9, T8).** Profile homes get skills from TWO
sources through one symlink mechanism (`capabilities.py`): the runner's own host
config dir, and Proxima's shipped `bundled-skills/` (content-pluggable - any folder
with a `SKILL.md` is a skill, ids namespaced `bundled/<name>`, per-profile opt-out via
the same `profiles.capabilities` selection JSON; first content: the vendored MIT
masterplan skill). Live-home claude profiles are exempt - nothing is seeded or
symlinked into the real `~/.claude`. The bundle also carries
`recommended-tools.json`: `recommended_tools.py` probes PATH at run setup and the
run preamble advertises the present CLIs one line each (detect-and-advertise;
binaries are always BYO), while Settings quietly hints at missing ones. The preamble
itself (`wiki_memory.GENERAL_GUIDE`) ships a distilled work-discipline pack
(evidence-first, small slices, self-review, wiki currency, script reuse) for every
runner.

## Concurrency & reliability

+ **Per-thread SQLite connections** in WAL mode — sync handlers run across FastAPI's
  threadpool, so each thread gets its own connection; writes serialize on SQLite's
  lock + `busy_timeout`.
+ **Bounded run worker** — `run_worker_concurrency` caps parallel agent runs.
+ **Crash recovery** — on startup, runs left `running` by a previous shutdown are
  failed (their in-memory ACP state is gone); orphaned jobs are reaped.
+ **Supervision** — the satpam loop (flow 6c) catches alive-but-unproductive jobs
  from durable signals on the reaper's cadence sibling; fail-quiet, no LLM calls.
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

`App.tsx` remains the single view owner and embeds the graph surface under the single Workflows destination (view id `workflows`). `GraphScreen` owns its remembered Drafts / Workflows / Runs home tabs and focused editor stage. A graph trigger owns the Manual / Scheduled choice: Manual exposes intake fields and feeds the Run modal, while Scheduled exposes cadence settings and promotes them to a schedule with no intake payload. The template list uses this trigger mode to split Manual from Scheduled tables, with existing schedule rows retained as a compatibility fallback, and mounts the schedule manager in a per-row dialog. `routes/graph.py` keeps `workflows.inputs` as a backward-compatible projection while deriving new saves from the trigger; migration 27 moves legacy graph declarations onto their trigger and inserts a no-op trigger for old graphs that had inputs but no entry node.

When Master is enabled, Tasks → `+ New task` opens the full Master home through the
compatibility `home` view and seeds the shared Master composer. The explicit
`master` view opens the same mounted surface and provider state. When Master is off,
the legacy Task Composer remains behind view id `home`; it creates then starts an
ad-hoc job and opens a dedicated `task` view with `#task/<id>` restoration.
`execution_policy=guarded` preserves final review; `autonomous` completes the final
step without an approval stop. Normal legacy-launcher tasks queue the selected
profile; `/image` and `/design` reuse the proven media run path and link that run to
the job so worker completion advances it to review. Start failure triggers
queued-task cleanup; a media link failure preserves and exposes the task ID.
Launcher project selection updates context directly. The shell header
ProjectSwitcher uses `setActiveProjectOnly` (active project + recent chat session
for coherence) and **stays on the current view**; only intentional open paths
(Search project pick, etc.) call `selectProject` to open Chat.

`AppShell` retains the persisted left navigation width/collapse state, mobile drawer, search, Attention, and account actions, and owns the right **`ToolDock`** (Terminal/Files/Preview as overlay panels). There is a single workspace: `Sidebar` renders one flow-ordered navigation (Chat, Master, Tasks, Workflows, Archive, gated Design) and the default landing view is `chat`. Session-kind metadata separately declares global-search visibility: Chat and Design sessions are searchable, while Master's hidden system thread is excluded so structured product-tool calls never leak into owner-facing results. Terminal moved out of the view routing into the ToolDock, which mounts it on first open and then hides rather than unmounts it, preserving PTYs; Files reuses `WorkspaceTree`+`FileEditor` over `projectFs`, and Preview reuses `AppRunner`. Design Studio's canvas/Konva internals and dedicated inspector remain unchanged.

Design Studio owns a Canvas / Brand Guide / Moodboard section menu. Moodboard reads and
writes a project-local `artifacts/moodboard/items.json` store through gated routes in
`routes/design.py`; cached OG images and owner-uploaded screenshots live beside it under
`artifacts/moodboard/images/`. `moodboard.py` bounds link HTML/image downloads, blocks
private/local targets, and turns network failures into fallback card metadata. When the
owner marks a card **Use as reference**, `run_prompting.py` reuses the
`wiki_memory.build_run_preamble()` context seam and merges local Moodboard images into
the established vision attachment marker for design sessions.

Generic frontend refresh loops use one non-overlapping polling hook. It pauses while
the document is hidden and refreshes once when the tab becomes visible, avoiding
background request churn without making active run status stale. Home artifact recents
reuse the bounded project-artifact scanner instead of recursively classifying the whole
tree with a second implementation. Master is deliberately outside this generic
polling path: its provider uses the existing session SSE stream plus recovery-only
reconciliation.

Authentication boot checks setup state, requires set-password or login, and resumes from
the HttpOnly `proxima_session` cookie into an in-memory bearer token. The password gate
uses one main landmark and supplies hidden, read-only `owner` identity metadata for
password-manager compatibility without adding accounts. Validation exposes one
fresh assertive alert for each invalid attempt after focus moves to the marked and
invalid corrective password field. That alert is the single semantic announcement owner;
the focused field does not repeat the message as an accessible description. Repeating
unchanged invalid values remounts the single alert while focus remains on the field. Gate
title, subtitle, entered value,
placeholder, error, button, and input/button focus styles use theme tokens that meet
WCAG AA across every canonical preset.

Project link/create failures use structured API details carrying the owning request
field. The fetch client preserves FastAPI validation locations and explicit
selected `path`/`parent`, child `folder`, and display `name`/`slug` ownership.
`FolderLinker` maps parent and link-path failures to a focusable selected-folder refresh
control, child-name failures to the folder input, and name/slug failures to the
display-name input before focusing and publishing the single alert. Every repeated
attempt remounts that sole announcement owner. Initial browse failure renders and focuses
a marked retry control before its alert is published. Directory browsing tests
readability, uses one fail-closed resolution and containment boundary, skips symlink
cycles, climbs only to the nearest readable ancestor within the owning configured root,
and returns a structured path error while retaining the current invalid selection when
no allowed ancestor is readable. Configured roots retain raw identity even when lexical
expansion or resolution fails, continue valid siblings, and bind recovery to the
original owning root. Every browse response carries an opaque configured-root ID through
later browse and link/create requests, so a canonical path returned for a symlink alias
cannot switch to a containing root. Every later request with no ID fails
closed. Create-on-disk opens the verified root and traverses each parent component with
POSIX no-follow directory descriptors or Windows no-reparse native handles. It creates
an unguessable staging entry relative to the retained parent, pins its platform identity,
and publishes it atomically without clobbering a destination. The Project stores that
expected identity, and success requires the published path to resolve back to the same
identity through the configured root. Rollback follows the retained identity if another
process renames it and never deletes a replacement, preserving component versus parent
error ownership when the filesystem changes concurrently. `container_registry.py`
compares the persisted identity on later Container filesystem resolution and rejects a
replacement at the same path. Startup captures the current identity for readable legacy
Project roots and writes a fail-closed unavailable marker for legacy paths that cannot be
opened, so an old row cannot bypass the identity boundary.

The retained browser audit runs the production bundle with allowlisted child
environments, disposable runtime/profile roots, and background/live-service features
disabled. Its real Tailscale entry check correlates the origin to the current device root
Serve handler and uses a fresh browser profile for each origin. Browser-level CDP
auto-attachment pauses the page plus every related service, shared, and nested worker
until a bounded traffic policy is ready. Every duplicate session is secured before
resume; one session owns target accounting, and a surviving secured session is promoted
if the owner detaches. Losing the last owner before audited closure closes the target
and fails the pass. Page and worker sessions install Fetch plus Network/WebSocket
blocking. If a service-worker target does not expose the CDP Network domain, it remains
paused until its served bytes match the locally audited duplex-free source; Fetch
interception remains active for every request. The production service worker must be
same-origin `/sw.js`. One explicit unauthenticated read-only GET proves those bytes,
and artifact drift fails closed before execution is trusted.
A secure disposable production fixture compares the complete resulting Cache Storage
key set with `APP_SHELL` and accounts for the worker artifact-proof GET separately before
the same boundary checks the current private entry. If that entry is
development-served, the audit fulfills `/@vite/client` with an inert compatibility shim
that preserves module and style loading without opening HMR duplex traffic. Interception
remains active through DOM checks, screenshots, and page/worker shutdown. The audit
forwards and accounts for only allowlisted static asset GETs across those targets.
Config, setup status, failed session resume, and the optional inert Vite client are
fulfilled inside the browser fixture; every other API, auth, cross-origin, non-static,
and duplex request is blocked or fails the audit. All CDP policy installation and
shutdown waits are bounded. The report persists only exact per-target and per-path
request counts, the Vite fixture state, redacted WebSocket totals, a redacted pass label,
and current-device Serve provenance, never the private origin or address.
