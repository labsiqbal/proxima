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
schema-validated, filesystem-isolated product tools) +
`master_tool_sanitizer.py` (allowlist-shaped, per-payload result sanitization),
`codex_master_proxy.py` (Codex loopback
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
`image_providers.py` / `video_providers.py` (the media-provider family: image and
video backend registries) over `media_providers.py` (shared base-URL join, endpoint
probe, and error-message shaping),
`auth_health.py` (cached background auth/readiness
checks for the Home banner), `logging_config.py` (query-token redaction across
Uvicorn HTTP and WebSocket handlers), `run_prompting.py` (prompt framing plus jailed,
bounded vision inputs), `platform_support.py` (Linux-first host support catalog
projected by `/api/config` and `/api/health`), and `routes/` (the HTTP surface).

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

## Always-on product surface (no feature flags)

The feature-flag system was removed (prune A2, #129): Design Studio, the graph
workflow engine, repo-job worktrees, and the Master orchestrator are
unconditional parts of the product. There is no `PROXIMA_FEATURE_*`
configuration, no `features.py` registry, no web-side capability map, and
`GET /api/config` no longer advertises a features payload. *Video Studio* - the
editable video project with a timeline - is not a product surface; video
**generation** is (`/video`, #148), and existing media files—including video
files—remain readable as ordinary artifacts.

The graph workflow engine (ADR-0001) is the shipped authoring path; legacy
linear jobs remain readable. The pure
`graph.py` boundary already normalizes planner/UI input to canonical edges, rejects
cycles and invalid references, computes deterministic topological/ready sets, validates
node `type`/`trigger_kind`/`profile_id`/`x`/`y` and the entry-point rules (at most one
trigger, no incoming edges), and validates each node's `text` / `json` / `artifact-ref`
output contract (including JSON Schema definitions). Trigger normalization also owns
the shared manual intake field declaration plus the optional schedule seed
(cron, IANA timezone, overlap, enabled; default Off). Manual intake IDs use a stable
identifier grammar and may declare typed defaults. The same pure boundary resolves a
start payload by validating required, number, and URL values, applying defaults,
omitting blank optional fields, and preserving job-owned values. It performs no DB,
runner, or HTTP
work. It also owns the per-job work-binding tags (Phase-1 slice 3, T1/T2): a node's
`target` names ONE container area (a code area's rel_path or `ops`), `touches_repo` is
always derived from it (an authored value is never trusted), and an ambiguous binding
is a first-class `target_ambiguous`/`target_question` state. `routes/graph.py` checks
targets against the project's registered areas at plan create/edit (422 on an unknown
area); plan start refuses an unresolved target question (409 carrying the question) in
the shared `bind_graph_job_repo_worktree` path, which checks ambiguity before the
project binding — so a project-less ambiguous plan
cannot start silently and the scheduler cannot skip the refuse. The target is pinned at
slice time precisely so it cannot be discovered at runtime. The start route performs
manual intake resolution before the worktree cut and commits the resolved JSON in the
same guarded update that claims `running`; a rejected value or post-claim start failure
leaves the queued job and its original input unchanged. The `graph_executor.py` adapter resolves any trigger node to the approved
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
but only a still-`running` job pulls new work forward. `routes/graph.py` is the human
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
concurrent swap of the project file cannot run unapproved content.
`scripts_library.scan_catalog` also feeds the reuse-awareness
surfaces: the script catalog is injected into every project run preamble
(`wiki_memory.build_run_preamble`) and into the plan slicer's prompt
(`workflows.architect_system`).

The repo-job worktree machinery (Phase-1 slices 2+4, T1) is always on: a job
whose target names a code area gets an isolated worktree, diff review, and a
local merge on approve; an ops-targeted job never touches the machinery. The
reject action is a review verdict, not worktree machinery. See flow 6b.

## Media provider setup

Chat and coding-agent runs stay on ACP (`RunWorker` → `AcpManager` → runner CLI).
Active image generation is deliberately separate and chosen from Settings:

+ **Image generation:** Codex/ChatGPT OAuth, Higgsfield zero-credit CLI, or an
  OpenAI-compatible endpoint (OpenAI, api.linc.id, FAL, xAI, …). The `xai-oauth`
  provider - which borrowed the Grok runner's `grok login` token - was removed as
  unreliable; the gateway covers the same models with an endpoint + key the owner
  controls. A settings row still naming it resolves to the default provider and
  the Settings card says so (`media_settings.unavailable_provider_note`), rather
  than being rewritten behind the owner's back.
+ **Video generation:** an OpenAI-compatible video endpoint (`video_gen`, its own
  settings row so it cannot disturb `image_gen`).

The settings APIs store only provider/model/policy plus optional endpoint keys for
OpenAI-compatible image and video endpoints; OAuth providers read existing local auth
stores and never return tokens to the frontend. Both media families share one base-URL
rule: the stored value is the API **root** (no endpoint path) and the client appends
`/images/generations`, `/videos/generations`, … itself.

Main-chat media generation is **artifact-first**: `/image` / `/gambar` results appear
as chat result cards saved under `artifacts/media/images/`, and `/video` / `/klip`
results under `artifacts/media/videos/`. Studio bridge actions are omitted while the
corresponding feature is disabled. The old *Video Studio* (an editable video project
with a timeline) stays removed - `/video` generates a clip, it does not restore an
editor. Video generation is asynchronous at every provider, so `video_providers`
submits a job and polls it (`POST {base}/videos/generations` → `{request_id}` with
`GET {base}/videos/{id}`, falling back to the OpenAI Sora `POST {base}/videos` +
`/content` shape on 404) inside the same background media run that `/image` uses.

## Schema bootstrap contract

Startup runs `init_db` (applies `db.SCHEMA`, the current-shape declaration) and then
`run_migrations` (the versioned chain). On a *fresh* database SCHEMA creates everything;
on an *existing* one every `CREATE ... IF NOT EXISTS` is a no-op, so its tables stay on
their old shape until the migration chain catches them up. Three consequences shape how
`db.py` and `migrations.py` are written:

- **`SCHEMA_TABLES` may contain nothing but `CREATE TABLE`.** That is the invariant, and
  both other object kinds broke it in turn. It is enforced by a test, and the reasoning
  lives beside the constant in `db.py` so the next migration author meets it.
- **Indexes are applied separately from tables.** An index on a column a pending
  migration has yet to add fails *immediately* with `no such column`, taking startup
  down before anything can repair it — which is exactly what a `UNIQUE INDEX` declared
  on the Inbox ledger's new `item_key` did to the live instance on deploy. SCHEMA's
  indexes are split out alongside the triggers; `apply_schema_indexes` creates the ones
  the current tables can satisfy and *defers* the rest instead of raising (only
  `no such column` / `no such table` is treated that way — any other error is a real
  schema bug and stays loud). `init_db` calls it after the additive column backfill, and
  `run_migrations` calls it again at the end of the chain — including on the
  nothing-pending path, since an index deferred by an earlier boot has no other moment
  to be created.
- **Triggers are applied separately from tables.** SQLite accepts a `CREATE TRIGGER`
  whose body names a column that does not exist, then fails the *next* schema reparse —
  i.e. the first `ALTER TABLE` a migration runs. Installing SCHEMA's triggers wholesale
  onto a legacy database therefore aborted startup before any migration could run.
  `db.SCHEMA` is split into `SCHEMA_TABLES` plus a trigger list;
  `apply_schema_triggers` installs the triggers and
  `prune_unparseable_schema_triggers` sets aside the ones the current tables cannot
  satisfy. `run_migrations` prunes before the chain and calls `restore_schema_triggers`
  after it, so a withheld trigger comes back once its column exists. Both use
  `_schema_reparses` — a rolled-back table rename — to let SQLite itself decide what is
  satisfiable, rather than parsing trigger bodies (they reference other tables through
  aliases, not just `NEW`/`OLD`). Triggers a migration creates itself are never touched.
- **Migrations run under `PRAGMA legacy_alter_table = ON`.** The table rebuilds all use
  the 12-step recipe: rename the old table aside, recreate it under the original name,
  copy, drop. Modern SQLite rewrites *other* tables' `REFERENCES` clauses to follow that
  rename, so the referencing table ends up pointing at the temporary name that is then
  dropped — a dangling foreign key. `PRAGMA foreign_keys = OFF` does not prevent this;
  only legacy rename semantics do, and those are what the migrations were written
  against. `run_migrations` sets the pragma for the chain and restores it afterwards.

Both behaviours only ever mattered on the upgrade path — a fresh install skips the
rebuild migrations entirely — which is why they need explicit coverage here.

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
in `node_states` instead.
A project row is the compatibility persistence record for a **Container**.
`project_areas` records zero or more repo Areas (auto-detected from `.git` with manual
override, where `.` means repo-at-root) and exactly one active Ops Area. The Ops
row's `rel_path` **is the persisted per-project Ops path** (prune C3), picked at
link time: the detected default (an existing `ops/` folder, else the root `.`)
or an owner override (`ops_path` on the link request; recorded with
`source='manual'` so the settle sweep never adopts it away). Every Ops feature
resolves through this row via `ops_root` - nothing assumes a global `ops/`.
Workspace-created Containers (Proxima's own data dir) still scaffold `ops/` and
register `rel_path='ops'`. Code-area auto-detection skips the per-project Ops
subtree, so a repo inside the chosen Ops folder is Ops content, not a code Area.
`project_layout` is the finer **per-project layout map** (prune C4): one row per
well-known area (`wiki`, `artifacts`, `scripts`, `uploads`) holding its
container-root-relative location. `layout_map.py` seeds rows by zero-write
detection from the real tree (standard name under the Ops root first, then the
container root; `source='detected'`), falling back to today's fixed name under
the Ops root (`source='default'`) - at link time, in the boot sweep for
projects linked before the map existed, and lazily on first resolution. The
map is persisted state, not a per-boot scan: a default-position entry survives
its folder's deletion, while an entry outside the Ops root re-detects once its
folder is gone (self-healing after the explicit move migration). Consumers
resolve through `layout_map.project_layout(...)`: wiki read surfaces (chat
note draft/commit, run preamble catalog), the script library (catalog,
approval card, `script_runner` execution), the upload default folder, the
artifact/design scanners, and the preamble's designs list. The automatic
memory writers (`log.md` append via `run_summaries`, `index.md` regeneration
in the worker, run preamble, and wiki-note commit) go through one seam,
`wiki_memory_write_root()`: **adaptive memory writes, default ON** (prune C5,
decision #121) - it returns the project's own detected wiki location, and
None when the per-project toggle is off (`app_settings` key
`project:<id>:memory_writes`, set via `PUT /api/projects/{slug}/memory-writes`)
or the position is unsafe (symlink/non-directory fail-closed; a missing
directory is only created at the default `<ops>/wiki` position).
`GET /api/projects/{slug}/layout` exposes the resolved map plus
`memory_writes.enabled`.
`container_registry` stores a bounded projection of identity and summary read
from the docs the folder already has (prune C5):
`resolve_container_identity()` probes `AGENTS.md`, `README.md`, `HANDOFF.md`
(container root, then Ops root), then a legacy `ops/container.md`; optional
frontmatter is honored, else the first H1 / first body line, and a folder with
no docs is identified by its own name. The projection records the winning
doc (`identity_source`), its source hash, the projection timestamp, and last
known activity - no Proxima frontmatter is required anywhere, and nothing
generates `container.md`. Identity is free text, not a Container type enum.
The file API refreshes the projection immediately when it writes any identity
doc name; linking refreshes it once; a five-second background
cycle catches direct owner edits without adding filesystem work to Fleet requests.
The pin between a project row and its folder is classified rather than
assumed (prune C6): `container_registry.container_binding()` resolves the
stored `path`/`path_identity` pair to `bound`, `missing` (nothing there),
`moved` (a different directory there), or `unavailable`, and rides on every
project payload as `location` (`GET /api/projects/{slug}/location` adds the
stored identity and the offered actions). `container_relocate.py` owns the
re-pin: it previews the move read-only (identity read at the NEW location by
the same `resolve_container_identity()` and compared with the stored
projection, plus Ops-path and code-Area resolution), refuses a non-matching
location with an overridable 409, then - under the Container mutation lock, in
one transaction ending in `validated_area_roots(deep_ops_scan=True)` - updates
`path`/`path_identity`, re-detects an Ops path or layout entries whose folders
broke (`layout_map.rebase_project_layout`), reconciles code Areas, and
refreshes the identity projection. The project id never changes, so history,
records, approvals, and per-project settings are untouched by construction,
and nothing is written into either folder.
`container_ops_migrations` stores the versioned, hash-bound, resumable migration
marker for legacy root-level Ops data.
`file_targets.py` defines the public file identity used after an entry has crossed the
API boundary: `(project slug, authoritative Area kind/id, Area-relative path)`. The
server constructs these targets for merged tree entries, artifact scan results, task
and chat run outputs, and deliverable records. Artifact and record paths are
container-relative real paths (#139): they resolve literally from the validated
container root with the authoritative Area assigned by physical ownership, so a
nested Code Area stays Code-owned; enrichment is per entry, so an unsafe
symlink is omitted without discarding other scan results. File tree traversal,
read/write, mutation, raw/preview,
record presence refresh, and ArtifactViewer use the same resolver, which revalidates
the project/Area relationship before applying `fsapi` realpath jailing. The resolver
then requires the target Area to be the authoritative owner of the resolved path:
the most specific active Area wins, Ops wins a legacy same-root tie with Code, and a
Container target is valid only outside active Areas. Each merged tree child crosses
the active-root realpath jail before ownership is assigned. **Symlinks are never
resolved into a target** (prune C7): a linked entry passes through the enricher as
`type: "symlink"`/`skipped: true` with a reason and no target, so it is visible in
the tree, unopenable, and harmless to its siblings. Merged tree entries switch to an
Ops or Code target as traversal enters that Area, so cross-Area aliases are rejected.
Display names never select a physical root. A path-only request means exactly
what it says on disk (prune #138, decision #121): it resolves from the Container
root and the authoritative Area is assigned by physical ownership - a path
inside the persisted Ops folder gets the Ops Area, everything else its real
owner. Reserved names (`wiki`, `scripts`, `tasks`, ...) carry no routing
meaning, so a repo's own `scripts/` or a root-level `wiki/` is never shadowed.
Rows written under the reroute era (turn-journal entry paths, markdown file
references in chat text) were frozen to their historical Ops-prefixed meaning
once, idempotently, by migration v60.
`target_preview.py` owns preview policy - roughly a hundred lines since prune #140
(ADR-0042). The authenticated `/api/target-preview/{slug}/{kind}/{id}/{path}` entry
validates the locator and answers with the file bytes on Proxima's own origin: no
redirect, no capability token, no preview cookie, no Area hostname. Isolation comes
from the sandbox instead - the artifact viewer frames the response with `sandbox=""`
(passive) or `sandbox="allow-scripts"` (active, never `allow-same-origin`), and the
response repeats it as a CSP `sandbox` directive so the document is opaque-origin
either way. Passive HTML additionally carries `default-src 'none'`; SVG/XHTML/XML
download rather than render. `ActivePreviewConsent` holds the in-memory grant for
active mode, keyed by owner session, Area, and viewer session, written only by the
bearer-authenticated `POST /api/projects/{slug}/preview-mode`; anything unknown or
stale is passive or 403. `PreviewIsolationMiddleware` guards the reverse direction:
framed, non-document requests to Proxima (`Sec-Fetch-Site: same-site`/`cross-site`,
or `Origin: null`) are refused, and app HTML without its own framing policy gets
`frame-ancestors 'none'` plus `X-Frame-Options: DENY`.
`cf_hostnames.py` serializes and verifies apps-domain ingress updates, while
`logging_config.py` redacts preview capabilities before access logging. The complete
admission, cookie, framing, worker, and response-policy contract lives in
[Security boundaries](../security-boundaries.md#canonical-file-preview).
Markdown resources resolve relative to both the source document directory and its
target. A validated target context is reused throughout each tree, record-list, or
message-list request while each path still crosses the realpath jail. Artifact links
whose Area context or individual path cannot be validated are omitted rather than
downgraded to a path-only identity. Design canvas, thumbnail, Moodboard, and export
images with a canonical target, plus SVG pixels on those surfaces, are hydrated from
authenticated raw bytes into managed blob URLs, which are revoked with component
lifetime. Design reply locator fields are treated as untrusted:
an existing image or frame target survives only when both the layer id and source
remain unchanged, and model-supplied targets are otherwise removed. See
[ADR-0029](../adr/0029-canonical-file-targets.md),
[ADR-0030](../adr/0030-area-scoped-artifact-media.md), and
[ADR-0034](../adr/0034-distinct-tls-area-preview-origins.md), with frame admission
extended by [ADR-0035](../adr/0035-frame-bound-area-preview-admission.md) and the
explicit trust transition recorded in
[ADR-0036](../adr/0036-active-file-preview-is-explicit-trusted-mode.md).
`container_activity.py` owns cross-process mutation and process-lifetime leases,
`ops_filesystem.py` owns native no-follow identity primitives, and
`ops_publication.py` owns descriptor-relative migration publication. Those deep
ownership modules do not depend on registry projection; `container_registry.py`
orchestrates them while remaining the canonical root resolver.
A `job` may bind to exactly one area via `target_area_id` (T1); a code-area target
makes it a **repo job**, whose isolated worktree lifecycle lives in `job_worktrees`
(slice 2 - see flow 6b).
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
opt-in (`deep_ops_scan`) and, since prune C7, runs only where content is moved or
created - physical Ops root creation and the move-based legacy migration. Adoption,
the boot settle sweep, settled-layout inspection, link-time Ops choice, Area add,
relocate rebind, graph scope, and every read path skip it: they write nothing, and
`fsapi` refuses to traverse a link on each access anyway, so a stray link in the
owner's real tree is skipped rather than fatal. Best-effort cross-Container aggregations (Home dashboard, deliverable
list) resolve through `try_ops_root`, which returns None for an unavailable or
boundary-invalid Container so one missing folder skips that Container instead of
failing the whole read; direct single-Container access still uses `ops_root` and
stays fail-closed. The intentional repo-at-root plus `ops/` containment is permitted;
the explicit migration (only) adds `/ops/` to the root repo's local git exclude
when it creates `ops/`.

Ops rows at `.` are a fully supported steady state (prune C2) - their root
files simply resolve at their literal paths - and any persisted non-`.` Ops path is
a first-class settled layout (prune C3) that the sweep validates and refreshes -
never "unsupported". **Link and the boot sweep never mutate the folder**:
`settle_container_ops` / `migrate_legacy_ops_containers`
take only zero-write paths - adopt, resume an authorized in-flight move, or
leave the layout byte-identical at `.` with no marker and no Attention. Before
any manifest is considered, the link-time Ops choice is **adopted as-is**
(prune C1/C3): the Ops row flips to the chosen path (the detected `ops/`
default or the owner's `ops_path` override, which also accepts an existing
empty folder), the marker completes with a `mode: "adopted"` manifest carrying
a top-level inventory of the existing content, and no file is moved, generated,
or rewritten - `container.md` is optional and only read if present. The
startup sweep passes no choice: it auto-adopts a populated `ops/` only for
detection-sourced (`source='auto'`) legacy `.` rows, so an explicit owner root
choice persists. Adoption is skipped while a durable `moving`
manifest exists (mid-move content is migration-owned) and stays fail-closed for
a symlinked, non-directory, or unreadable target or one overlapping a repo Area.
`inspect_ops_migration` mirrors the same predicate and reports `retry_action`
(`adopt`/`migrate`/`revalidate`) plus - for a safe `migrate` - `planned_writes`
(`container_doc: move|null`, `git_exclude`), so the migration surface
previews every write exactly and the retry confirmation names the real action.
The move-based path runs **only** through that explicit retry:
it creates a dry-run manifest with content hashes. It includes an existing owner-authored
`container.md` as a byte-preserving move; with no legacy document it plans
nothing for `container.md` (strategy `"none"`, prune C5 - no identity document
is generated; stored pre-C5 manifests with a planned generated document still
execute through the recovery protocol below). It rejects collisions or ambiguous types before
publication. Regular files are linked into authoritative names from opened,
manifest-bound descriptors. Directories are published entry by entry relative to
stable no-follow descriptors. Manifest version 6 records each Proxima-created
destination directory identity before any child is published, and rejects every
unbound existing directory even when it is empty. The legacy name is moved only
after its complete source snapshot is revalidated; a changed name remains untouched
for owner intervention. Generated documents require anonymous same-filesystem
storage and persist device, inode, and expected hash before the first visible
no-clobber recovery link. The exact recovery link remains as a durable anchor; no
cleanup unlinks a re-resolved name. Every manifest entry binds the opened top-level
inode plus descendant file and directory identities, and descriptor-relative
no-follow hashing rejects swaps even when replacement bytes match. A durable
`moving` marker supports restart after each publication phase. Older moving markers
upgrade in memory and are persisted at
the current version only when legacy and physical document state identifies one safe
continuation; ambiguous candidates remain untouched for owner intervention.
Failures open a `container_ops_migration` Attention item and retain the legacy row;
per-Container migration failures are isolated so one unhealthy Container (missing
drive, deleted Area folder) never aborts control-plane startup.
`container_registry.inspect_ops_migration` projects the durable marker, exact stored
Attention reason, `lstat`-based path states, conflicts, active-layout usability, and
retry safety without changing the filesystem or following symlinks. The Project
routes expose that projection for inspection and refresh. The retry route first
requires a safe current projection, then delegates to the hash-bound,
same-filesystem migration routine (`migrate_container_ops`) - the only caller
allowed to plan moves. Immediately before every manifest
application, that boundary rechecks current code-Area ownership plus path type,
symlink, hash, and filesystem constraints, including ownership of the complete
physical Ops root and an exact match for any existing manifest-bound
`ops/container.md`. Migration planning, apply, durable state updates, and short-lived
filesystem mutations share a cross-process per-Container mutation lock. Design,
Moodboard, chat-media publication, uploads, and turn restore use that same
root-resolution boundary. Agent runs, project terminals, and preview apps retain a
shared activity lease for their complete mutation-capable lifetime. The guardian is
a standalone script selected by verified absolute path, launched with isolated
Python import behavior, and changes to a trusted working directory before it adopts
the lease. A detached Linux subreaper sentinel or Windows Job object owns the writer
tree. If a platform cannot prove complete tree exit, Proxima fails closed by
refusing to start the guarded writer. The record binds both the guardian and its
owning API process by PID and process-start identity. A matching live owner is an
active-process conflict and is never signaled. Proven API orphans can be recovered
through the Linux sentinel or the exact unpredictable named Windows Job.
Activity-guarded ACP processes use per-run cache scopes, so one concurrent run
cannot recycle another run's process. Migration and complete
Project purge require bounded exclusive quiescence and return an active-process
reason instead of waiting forever. Explicit owner retry recovers only project-scoped
guardians whose trusted owner, guardian, interpreter, script, and platform control
identities still match.
Upload request bodies are staged before synchronous publication. Area roots are
resolved only after acquiring the appropriate boundary, and late destinations fail
without replacement. See
[ADR-0038](../adr/0038-owner-safe-container-activity-boundaries.md).
Root-repository exclusion traverses `.git/info/exclude` relative to the already-open
Container descriptor. Fresh Windows Container creation opens and identity-binds the
Container handle before creating `ops/`, creates every starter path component
relative to stable no-reparse handles, and uses a relative no-clobber file create,
while unsafe legacy migration remains
fail-closed when an equivalent move primitive is unavailable. A repaired
already-physical layout with open migration Attention
becomes explicitly retryable; the same boundary revalidates it and resolves Attention
without moving content. It does not add merge, overwrite, delete, cross-device move,
symlink-following, or content-authority behavior.
Deliverable records, Wiki, artifacts, Design, scripts, reports, exports, and uploads resolve
through the active Ops row plus the per-project layout map; the file API resolves
literal container-relative paths (prune #138). Recovery reveal actions can opt into
an explicit read-only Container-root inspection target that bypasses Area
validation, so both sides of an in-flight migration stay inspectable.
Only tree and file reads accept that target; write, mkdir, rename, and delete remain
Area-validated operations. The inspection projection declares each root's
inspectability and refusal reason so unavailable or unsafe root actions never
dispatch a read.

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
via `file_missing`. Record paths are container-relative real paths (#139,
migration v61 rewrote legacy Ops-relative rows): presence resolves each record's
canonical target literally from the container root through physical ownership,
and the record scan is container-rooted through the layout map (an artifacts
area outside the Ops root is covered). The registry is surfaced as the Artifacts
**Deliverables tab** with a **History tab** (`missing=1`) for gone-file records
and a badge feed (`GET /api/archive/badges`) that marks the gallery's cards; the
separate Archive destination is gone (prune Part D, #139) and the destination
holding the ledger is Artifacts (ADR-0043, #144). Workspace discovery
does not itself create registry rows. Fed at the one seam every run's outputs pass through
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
`turn_file_journals` stores bounded before-content plus versioned filesystem-root
semantics for paths changed by a Chat turn and cascades with the session;
`attention_items` is the single notification ledger behind both the ephemeral header
feed and the persistent Inbox destination (#158): durable Master, budget, and
permission needs-you items, plus mirrored copies of the review/satpam/script items the
attention API derives from other tables, informational `task_outcome` rows for
terminal Task transitions, and bounded `client_error` rows filed by the browser's own
error surface. `read_at` (seen) and `status` (still needs you) are
independent, and `item_key` is the one public id space shared by native and projected
rows. `notifications.py` owns settling, projection, acknowledgement, and read state;
`master_decisions` stores each non-approval owner question, bounded response contract,
pending/deferred/resolved state, response attribution, and exact links to its
Attention row, requesting Task, origin Master message, Task response message, and
single continuation run. Bare supervisor start-failure Attention rows stay generic
Attention and are not fabricated into this ledger. `job_final_approval_intents`
holds one identity-bound generation while a worktree-backed final approve runs so
merge/push side effects and decision creation stay mutually exclusive without holding
the database lock across Git work; restart reconciliation finalizes a merged live
generation or releases an incomplete one.
Settings under `master.*` hold unattended state, turn/wall/optional-token budgets, and
core-tour completion. Startup asserts one project-unbound Master identity per owner
and refuses ambiguous dual identities or conflicting old/new origin columns. The
migration is transactional and idempotent, and preserves messages, runs,
events, checkpoints, budgets, attention,
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

Migration 35 adds graph state. `GET /api/containers/{slug}/graphs` reads
path-free state and `POST /api/containers/{slug}/graphs/rebuild` accepts only a typed
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
affecting Tasks, Fleet, or Live state. Canonical graphs live under Proxima's
runtime dir (`<workspace_root>/graphs/container-<id>/knowledge|code-area-<id>/`)
- never inside the Container or Area, and no ignore lines are written into the
owner's repos (prune C2). Legacy in-Area `graphify-out/` leftovers are ignored
by freshness fingerprints and knowledge walks.

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

### Artifact viewing flow

`ArtifactViewer.tsx` remains the universal renderer boundary rather than routing
ordinary deliverables through Design Studio. It composes the existing
image/video/PDF/HTML/Markdown/JSON/CSV/text renderers on a single stage. Since #146
it renders **in the main window** (`.av-surface`, no portal, no dialog role, no focus
trap, no Escape close) with a named way back, and since #148 the stage is the only
thing under its bar: the review side panel that used to take a fixed column beside it
is gone, so an artifact - an HTML page above all - is rendered at the window's full
width. Kinds that ARE a page (HTML, PDF) fill the stage edge to edge; pictures
(including a design's artboard) and documents keep the padding that centres them.

`ArtifactsScreen` decides whether an opened artifact reaches the viewer at all:
`components/artifacts/fileKind.ts` owns the taxonomy for both, and its
`opensInEditor` sends markdown and text straight to `DocumentEditor` (the wiki
`WikiNote` markdown editor, or `FileEditor` for other text, over `projectFs`).
The viewer's `onEditSource` is the one-way path to that editor for the kinds that keep
a renderer - CSV, JSON, Mermaid, HTML. Unknown, binary, and directory-like paths bypass
text loading and render the download fallback immediately.

Every renderer uses an artifact's canonical file target when present. Markdown text,
image/video media, PDF/HTML frames, and download links therefore resolve the same Area
identity returned by the server instead of re-deriving a root from a display path.
HTML frames use the Area-stable preview namespace, and Markdown sibling resources
inherit the source document's Area and directory. Chat and task result media,
Iterate and record-preview Markdown, session deletion, and the Design Studio image bridge
retain the same target. Design scene image layers persist the target beside the
source path, and canvas, gallery, Moodboard, record thumbnail, image-frame, and
export renderers pass it to the media resolver. SVG display uses authenticated raw
bytes rather than preview-origin document rendering.

**Removed with the review panel (#148, owner refinement to ADR-0043):** the point
annotations and their browser-local per-`(project, path)` state, the general-feedback
field, the **Add feedback to chat** handoff (producer-session resolution, the seeded
`Composer` draft and its append/keep conflict dialog), and the editor's **Review**
action back into the viewer. No API route backed any of it - the whole loop was
client-side - so the removal is web-only. Feedback now goes through ordinary Chat.

Markdown Mermaid fences and standalone Mermaid files use a lazy renderer. Choosing
**Edit as whiteboard** lazy-loads `@excalidraw/excalidraw` and
`@excalidraw/mermaid-to-excalidraw`, converts supported diagram structures to editable
shapes, and writes only on explicit save through the existing jailed project file API.
Scenes live at deterministic `<mapped artifacts>/whiteboards/*.excalidraw` paths
(`components/artifacts/whiteboard.ts`) and carry the source fingerprint. A mismatch
offers keep-edits versus rebuild-from-source. Excalidraw and Mermaid stay out of the
initial app bundle and load only when their artifact path is used.

```text
ArtifactsScreen (the one router) -> document  -> DocumentEditor
                                 -> otherwise -> ArtifactViewer (full-width stage)
                                                   +-> Edit source -> DocumentEditor
                                                   +-> Mermaid -> Excalidraw -> save scene
```

## Key flows

### 1a. Master delegation and unattended queue

Durable persistence migration always runs, and the Master runtime, supervisor,
routes, navigation, and settings surface are unconditional (the
`feature_master_orchestrator` flag was removed in prune A2). Migration
ambiguity still fails closed.

```text
GET /api/runners/detect
      -> resolve binaries on the server-controlled runtime PATH
      -> apply conformance, including minimum version
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
      -> global Attention surfaces owner decisions inline
      -> non-approval create_attention writes one durable Master decision
      -> Master Decisions and global Attention render the full question and response contract
      -> defer persists the decision but removes it from the global needs-you badge
      -> versioned resolution validates the response and atomically queues one Task continuation
      -> Task reject/delete settles open decisions closed without a continuation run
      -> the requesting Task rejects generic approval while its decision remains unresolved
      -> worktree-backed final approve claims a durable generation before merge/push
      -> decision creation refuses while that generation is live; merge failure releases it
      -> decision projection appends one human-readable defer, resolve, or left-review event
```

There is no agent-to-localhost control plane. The streaming parser rejects malformed,
nested, oversized, duplicate, unknown, and disallowed envelopes with stable errors
written to the Master thread. The broker's closed JSON schemas admit only bounded
product IDs and text. Results never include absolute host or internal graph paths,
runner homes, bearer material, or configuration; `query_context` may include
validated scope-relative citations as provenance. `master_tool_sanitizer.py` is what
keeps that true now that a project is a real folder: every result is reduced to its
declared per-tool shape (undeclared fields are dropped before serialization), host
paths inside declared product text are redacted, and a payload that survives
redaction still carrying one is refused on its own with `unsanitizable_tool_result`
instead of blocking the entire response. Request, result, round, call, and
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
creation, and worker spawn each repeat the server check.

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
creates a `master_budget` attention item, keyed by the budget cycle it stopped so a
later stop notifies again. The item clears itself once Unattended is switched back on,
and can be acknowledged from the header at any time (#157).
Git commit/push/PR remains ordinary job work through the existing BYO environment.
Destructive install administration is not in the unattended allowlist.

Authority is singular: **Master dispatches and prioritizes; satpam alone detects,
steers, or restarts stuck runs.** Master never calls satpam restart machinery.

`MasterProjectionService` projects important Task status, checkpoint, Attention, and
Satpam rows, plus Master decision defer and resolution transitions, into the same
durable Master conversation. One
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
global navigation is Master, Tasks, and Artifacts only. Delegate passes no Work active
project into the Master desk and suppresses project filtering and Work-only escape
paths from its Tasks and Artifacts views, while preserving task and record deep
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
owner restores while Master work is active in the same project. Journal paths are
literal (prune #138): container semantics restore from the Container root, ops
semantics from the Ops root, while holding the Container mutation lock. Rows
recorded under the reroute era were frozen to their historical Ops-prefixed
meaning by migration v60, so an old `wiki/...` entry still cannot recreate a
hidden root-level tree after the folder migration moved that content.

Runs are per-session serialized and bounded-concurrent globally; a heartbeat +
reaper fail hung runs, and a per-turn quota cancels stragglers. The quota
(`run_timeout_seconds`, default 900s) is a first-class **in-app setting** stored in
`app_settings` (Settings → Agents → Turn quota), read per run so it applies on both
entrypoints (`scripts/serve.py` and `uvicorn proxima_api.main:app`) without a
restart; config/env (`PROXIMA_RUN_TIMEOUT_SECONDS`, mirrored on both entrypoints) is
the fallback default. Both entrypoints are side-effect free at import time -
`serve.py` only assembles config at module level and builds the app inside its
`__main__` guard, because multiprocessing "spawn" workers (graph builds)
re-import the `__main__` module; a module-level `create_app()` would re-run
migrations and boot sweeps against the live database from every spawn child
(pinned by `tests/test_serve_entrypoint.py`). Completion updates are guarded by the current run state, so
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
  conversation into a normalized typed DAG draft —
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
job done  →  artifacts surface in the Result view + land as durable deliverable records
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
debounced PATCH that flushes before navigation or promotion; Run stays disabled
until the accepted graph is valid and saved. The inline title can
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
exit so switching views cannot restore a stale graph. `GraphCanvas` measures its SVG
viewport and refits when side panels, metadata, browser size, or graph geometry change
the usable area, without remounting nodes; fit vs manual pan/zoom intent is specified
in [workflow-graph.md](../workflow-graph.md).
The intake editor stages incomplete row edits locally under the last accepted stable
ID and reports dirty/valid state to `GraphScreen`. Polling cannot overwrite or bless a
local graph while an autosave is queued or in flight. The header therefore shows Not
saved after validation or network rejection, offers Retry for a rejected request, and
keeps both Run and Save as Workflow disabled until the accepted graph matches the
screen. Drafts and reusable manual templates share one `RunModal`; only its validated
payload reaches `POST /api/graph/jobs/{id}/start`.
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
Delegate requests the cross-Project projection and renders each Task or plan with its
owning Project in both visible metadata and its accessible name. Work requests the
active Project only and omits that redundant ownership label.

Ad-hoc single-step work is just a 1-step job (old kanban `tasks` were migrated this
way). Jobs live-poll while running and auto-archive after 30 days. A dependency-blocked
Task remains queued but carries its durable reason in list/detail payloads and renders
that reason in `TaskWorkspace`.
Every Task detail renders the owning Project as the primary identity, with identity
label and Area as secondary context. In-app Task opens (Attention and other preserve-work
hash stamps) keep the selected Work Project and lock the deep surface. A full-page
`#task/<id>` reload instead resolves the Task and Project list before mounting
`AppShell`, selects the owner in one state transition, and only then exposes the
Project-bound tool dock. Any mismatch keeps Terminal and Preview unavailable.
Task-linked Design resolves through the Task owning Project without rewriting Work
selection, and the return path restamps preserve-work so ownership stays coherent.

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

A job whose `target_area_id` names a code area is a **repo job** and never
edits the primary tree:

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
POST /api/jobs/{id}/approve (final step)  →  claim job_final_approval_intents
    generation (mutually exclusive with unresolved Master decisions), then
    guarded local merge --no-ff into the branch the worktree was cut from
    (T1 local-first; db lock is not held across Git)
    ├─ success: merge_commit recorded on job_worktrees, worktree + branch torn
    │  down, same generation finalizes the Task to done - then, ONLY if the
    │  code area's push_on_merge toggle is on (T9, slice 11, default off) AND
    │  the repo's remote URL still matches the one pinned at opt-in (audit F3),
    │  a hardened `git push` via the host's own git (credential helpers + hooks
    │  neutralized). A failed or refused push never un-merges and never fails
    │  the approve: push_status='failed' + the exact command output land on the
    │  job_worktrees row and surface as a blocker card with a retry action
    │  (POST /api/jobs/{id}/push, either engine).
    └─ refusal/conflict: 409, generation released, job PARKS in review with the
       surfaced error; worktree kept - resolve, approve again to retry. Never
       forced.
POST /api/jobs/{id}/reject  {reason}  →  the other verdict door (slice 4, either
    engine): job → failed with jobs.rejected_reason recorded; the worktree is
    discarded UNMERGED (teardown, like delete) - the primary
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
Ops. When a plan has repo jobs (nodes with `touches_repo`), `POST /api/graph/jobs/{id}/start`
resolves their one code-area target to `jobs.target_area_id` and cuts the plan's
worktree before claiming `running` — same loud-refusal ordering as the linear start. A
plan's repo jobs must share ONE code area (Phase-1: one worktree row per job); a
multi-area plan refuses to start with a split-the-plan message. For a direct legacy
plan, the worker's cwd seam remains node-aware: a `wf_node` run executes in the
worktree only when its node touches the repo, while Ops siblings use the physical Ops
Area. For a delegated Task, every node uses the selected repo worktree or selected
physical Ops Area.
The final `POST /api/graph/jobs/{id}/approve` is the merge point, with the identical
guarded-merge/park-in-review contract as the linear approve.

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

`schedules` rows carry a 5-field cron, IANA timezone, durable input bindings, independent
On/Off state, and overlap policy. The scheduler loop wakes each minute, projects the UTC
tick into each schedule's timezone, finds _due_ schedules (matching the current local
minute, not a backlog), and materializes a `job` for each - respecting
`overlap_policy` (skip / allow). A scheduled
graph recipe goes through the same `bind_graph_job_repo_worktree` path as manual plan
start (pin `target_area_id`, cut isolated worktree); a refused cut fails the job with
an owner-facing reason instead of running unisolated.

`schedule_policy.py` is the shared trust boundary for create/update, Run now, migration,
and cron spawn. It derives the workflow input contract from the trigger (with the legacy
column as fallback), validates schedule timezones, and resolves required inputs only from
durable automation bindings. An unresolved row can remain Off for configuration, but
enablement returns `schedule_missing_sources`; the scheduler independently refuses any
legacy or drifted enabled row. Schedules inherit the workflow's project. Workflow
availability pauses every schedule without changing their On/Off choices.

Run now calls the same scheduler spawn with no minute claim, so it uses the same project,
profile, graph snapshot, worktree policy, and resolved binding values as cron. The graph
screen uses separate list and exact-job request generations, waits until the returned job
is selected, verifies its owning project, and only then closes the schedule dialog.

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

The app's own port is chosen by the kernel unless the owner pins one. It sits behind
the preview relay and never appears in a browser, so there is nothing to keep stable
about it -- the same reason the relay allocates its listener the same way. Pinning is
for apps that need a fixed port (a registered OAuth callback, say). If an unrelated
process owns a *pinned* candidate before start, start returns a structured
HTTP 409 conflict. If it claims the port after preflight but before the managed app
binds, status becomes the sticky terminal `port_conflict` state and Proxima signals
only its own managed process group. It never reaches, signals, or terminates the
foreign listener. Missing or incomplete procfs evidence fails closed as
`ownership_unknown`. An uncontained child that detaches into another process group is
also ownership-unknown. When a scope is unadopted, Stop tries authenticated recovery
from durable supervisor evidence and otherwise returns HTTP 409 with an
ownership-unknown message instead of claiming success; start refuses a replacement
generation while that authority stays unresolved. Once `AppManager.start` accepts a
launch, it owns the writer-activity effect lease through cancel and failed-spawn
cleanup. Bubblewrap reports the exact launch-specific namespace
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

### 8b. Linux-first platform support boundary

`platform_support.py` owns the machine-readable host catalog. Linux is the
supported daily-driver server platform; macOS and Windows remain experimental.
The existing `/api/config` and `/api/health` routes project the catalog, and
Settings Diagnostics renders the same values instead of maintaining frontend-only
labels.

Host actions enforce the contract before side effects. `scripts/install-user`
accepts Linux only, `scripts/install-macos` accepts macOS only, and
`scripts/install-windows.ps1` accepts Windows only. The Bash lifecycle wrapper
selects systemd on Linux and launchd on experimental macOS, refuses unknown hosts
before manager calls, and never falls through from a missing macOS LaunchAgent to
systemd. Linux Diagnostics alone calls `journalctl`; other host families receive
actionable experimental or unsupported guidance.

`scripts/linux-daily-driver-acceptance` is the release gate for the complete
support claim. It composes temporary HOME/XDG installs, fake service managers, a
real POSIX PTY, temporary SQLite backup/restore targets, loopback preview servers,
and a synthetic HTTPS MagicDNS reverse-proxy request. The acceptance environment
sets Master on. It never targets the installed database,
service, Tailscale state, privileged enrollment, or release custody. The decision
and row-level evidence live in
[ADR-0028](../adr/0028-linux-first-daily-driver-support.md) and the
[acceptance matrix](../linux-daily-driver-acceptance.md).

### 9. Update check

```text
VERSION (repo root) → read_local_version() → FastAPI app.version → GET /api/health
                                    │
UpdateManager: every 6h → GET api.github.com/repos/<repo>/releases/latest
                           (never raises — offline/404/hiccup → last_error)
                                    │
   GET /api/update/status · POST /api/update/check (metadata only)
                            (legacy HTTP and CLI apply paths remain inert)
```

`UpdateManager` (`updates.py`) is the one thing that phones home: an
unauthenticated GitHub Releases GET on a 6-hour timer (first check 60s after
boot), holding only in-memory state (current version, latest release,
`checked_at`, `last_error`) — `PROXIMA_UPDATE_CHECK=0` disables just that
loop (the manual check route still works) and `PROXIMA_UPDATE_REPO` defaults to
`labsiqbal/proxima`; forks can point it at their own repo. `apply()` always
fails closed: updating is a manual `git pull` plus a service restart.

The former safe-self-update pipeline (external updater authority, candidate
sandbox, maintenance fence, ingress leases, `/api/self-updates/*`,
`/api/maintenance`) has been removed entirely: unhooked from the running app,
then deleted from the repo (prune A1). See
[ADR-0041](../adr/0041-updates-are-a-manual-git-pull.md) (and superseded
[ADR-0008](../adr/0008-external-safe-update-authority.md)) for the decision
trail.

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
seed `auth.json` and `config.toml` from `~/.grok`, sync the auth file before a
run (see _Credential sync_ below), and set `GROK_HOME` to keep profile state
isolated. Detection marks Grok ready
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

**Credential sync (single-use OAuth rotation).** Every profile home holds its own
copy of the runner's login (`RunnerSpec.source_dir` → `seed_files` at profile
creation, `refresh_files` on every run; `profile_seed.py`). That fan-out is only
safe if rotation is followed correctly, because the ChatGPT/Codex login rotates
**single-use**: refreshing burns the old refresh token and mints a new pair, so at
any moment exactly one copy on disk is live. A one-way host → profile force-copy
therefore *destroys* credentials - whichever home refreshed first holds the only
live pair, and overwriting it with the host's burnt pair leaves every home
replaying a dead token (`"your refresh token was already used"`), healed only by a
re-login and broken again by the next rotation.

So `sync_agent_credentials()` reconciles **newest wins**, with the host dir as the
hub:

+ **Recency** comes from the credential itself (`last_refresh`) and falls back to
  mtime for opaque files - copies preserve mtime, so the embedded stamp is the
  reliable signal.
+ **Pre-run** (`RunPrompting.refresh_credentials_if_needed`) pulls a newer host
  login into the profile, or publishes a newer profile token to the host. Only a
  changed *profile* copy recycles the cached agent process (it holds the old token
  in memory); publishing leaves the profile untouched, so no recycle is needed.
+ **Post-run** (`RunPrompting.publish_credentials_after_run`, called from
  `execute_run`'s `finally`) pushes a token the runner rotated to during the run
  back to the host, so sibling profiles pick it up on their next run instead of
  presenting the burnt one. Push-only on purpose - the cached process keeps the
  file it is holding.
+ **Identity guard** — a profile copy is published only when it is recognisably the
  same login (same JSON shape, same `account_id`). A profile whose runner was
  switched still carries the other runner's `auth.json`; the host must win there
  and can never be overwritten with a foreign credential.
+ **Single-flight + atomic** — one exclusive `flock` per source dir (lock file in
  the temp dir, never inside a CLI's config dir) serializes concurrent runs;
  writes go to a private `0600` temp file and are `os.replace`d over the
  destination, so a failure can never truncate a credential. A symlinked profile
  credential is written *through* (multi-account setups keep their link).

When a run still fails with a spent refresh token, the pair itself is gone rather
than out of sync, so the error names the concrete recovery (`codex login`) instead
of leaving the owner with CLI-only advice.

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

`App.tsx` remains the single view owner and embeds the graph surface under the single Workflows destination (view id `workflows`). `GraphScreen` owns its remembered Drafts / Workflows / Runs home tabs and focused editor stage. A graph trigger owns the reusable intake contract. The explicit workflow Run action always opens that per-run intake when fields are declared, while schedule execution never opens it and resolves only durable automation bindings. A trigger-authored schedule seeds a real schedule row when the plan becomes reusable; schedule rows never rewrite the trigger contract on read. The template library uses one table: workflow Availability is separate from the joined Automation summary, and each row retains Run and Schedules actions. `ScheduleManager` exposes independent On/Off state, timezone, bindings, overlap, configure, delete, and the exact-job Run now handoff. `routes/graph.py` keeps `workflows.inputs` as a backward-compatible projection while deriving new saves from the trigger; migration 27 moves legacy graph declarations onto their trigger and inserts a no-op trigger for old graphs that had inputs but no entry node. Migration 54 adds schedule timezone and disables unresolved legacy automation.

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

`AppShell` retains the persisted left navigation width/collapse state, mobile drawer, search, Attention, and account actions, and owns the right **`ToolDock`** (Terminal/Files/Preview as overlay panels; browsing left the rail in ADR-0040 and came back to it in #145). There is a single workspace: `Sidebar` renders one flow-ordered navigation (Chat, Master, Tasks, Workflows, Artifacts, Design) and the default landing view is `chat`. Session-kind metadata separately declares global-search visibility: Chat and Design sessions are searchable, while Master's hidden system thread is excluded so structured product-tool calls never leak into owner-facing results. Terminal moved out of the view routing into the ToolDock, which mounts it on first open and then hides rather than unmounts it, preserving PTYs; the Artifacts destination (ADR-0043) renders `ArtifactsScreen`: a gallery of the live artifact scan (`ArtifactThumb` draws designs from `scene.json` through `MiniPreview`, images from the preview endpoint, video as a metadata-only frame) with All / Deliverables / History tabs over the same record ledger (#139), and no tree of its own; Preview reuses `AppRunner`, which since #147 is the CONTROLS half only - it runs and stops the app and keeps a compact status, while the running app itself renders in the Artifacts main window as `AppViewport`, reached through `ToolDock onOpenAppViewport` → `AppShell` → `App openAppViewportInMainWindow` → `ArtifactsScreen` (`pendingApp`), the same seam the file handoff uses, with `lib/runPreview` carrying the reverse request (the viewport's "Run controls" opens the dock's Preview tool). `components/files/appPreview.ts` owns the app frame's origin selection and its two sandbox strings so the ADR-0042 model is stated once for both halves; the record page and the recipe test bench mount the same controls and route their picture to the same viewport, and both are Work-only, since Delegate has no dock to get back to. Files renders `WorkspaceTree` over `projectFs` for the active project, and over `containerInspectionFs` when a `proxima:reveal-file` event names a Container-root path (Ops-migration recovery) - the dock absorbed that inspection panel in #145. Opening a file from the dock does not open a viewer inside the panel: it calls `AppShell onOpenFile` → `App openFileInMainWindow`, which hands the path to `ArtifactsScreen` as `pendingFile` - the one router that decides between the editor and the inline viewer (#146); inspection reads stay in the panel because only the read-only adapter can reach Container-root bytes. The dock accepts an availability boundary from `App`; unavailable Project context closes and hides the entire dock while retaining any already-visited panes for a safe return. Design Studio's canvas/Konva internals and dedicated inspector remain unchanged.

`App.tsx` serializes Work navigation through `lib/workRoute.ts`: mode, project,
background Chat session, primary surface, and focused Workflow or Design identity form
one validated browser-history entry. `WorkChatStateProvider` sits above `AppShell`, so
Work/Delegate and primary-surface changes do not remount Chat state. Its owner-local,
project/session-keyed store preserves the draft, selection, composer mode, safe pending
attachment references, and scroll anchor across reload or installed-PWA restart.
Project availability prunes only deleted project keys; URL resolution selects a
same-project session or an explicit project fallback before exposing saved state.

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

The same rule governs status surfaces generally. `--ui-danger*`, `--ui-warning*`, and
`--ui-success*` are one palette declared in `:root` and re-declared by the dark preset,
which mixes its tints from the hue into `--ui-surface` so they track the dark surface
rather than a pinned hex. Components never branch on theme and never inline a status
colour; a destructive fill that has to *carry* an `--ui-on-accent` label uses
`--ui-danger-fill`, the danger twin of `--ui-accent-fill`, because on a dark surface a
single hue cannot both be readable text and sit under a readable white label.
`apps/web/src/theme.tokens.test.ts` enforces the parity.

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
