# Proxima — Capability Map

What's built, why it exists, and how it works. A reference for understanding what
this cockpit is actually capable of. (Derived from the code, not aspirational.)

> **Where do I edit when I add/change a feature?** See the companion
> [reference/feature-map.md](reference/feature-map.md) — a per-feature grid of
> code locations (backend + frontend), tables/events touched, relations, and
> status/flag. This doc explains *what & why*; that one maps *where*.

> **Model:** single-user cockpit. One owner, no in-app accounts. Network controls
> remain the primary access boundary, with a first-run owner password and authenticated
> bearer-token or HttpOnly-cookie sessions as defense in depth. Runtime data lives
> outside the repo (`~/.local/share/proxima/`).

---

## 1. Agents & runners (bring-your-own-agent)

**Why:** Proxima drives the AI agents you already own (Claude Code, Codex,
Grok, Hermes, and Pi) over ACP - no baked-in model.
**How:** `runner_specs.py` defines each runner's spawn argv + credential home +
wire `protocol`. The worker (`worker.py`) starts one agent subprocess per (runner,
home, cwd) on demand; `runner_spec(run.runner_id)` makes it runner-agnostic.
Most runners speak ACP. Grok uses the official CLI's native
`grok agent stdio` endpoint, with `~/.grok/auth.json` and `config.toml` seeded
into each profile's `GROK_HOME`; no third-party adapter is involved. Its readiness
requires both the `grok` binary and a non-empty JSON auth file, so an installed but
logged-out CLI appears as not ready with `grok login` guidance. The **Codex** runner
drives the owner's own `codex app-server` (`codex_appserver.py`, same call surface)
so it tracks the current system Codex CLI instead of a stale bundled adapter core -
newer models like `gpt-5.6-sol` work as soon as `codex update` has them. Default
resolves via `default_runner()` (env -> first *ready* runner -> fallback).
Failed runs surface owner-facing reasons via `format_rpc_error` (API) and
`formatRunError` (chat/UI): JSON-RPC dumps like Hermes
`{code, message: "Internal error", data: {details: "No LLM provider…"}}` become the
plain details line in the error bubble (and in stored `runs.error`), not a raw dict.
Known Hermes auth/provider failures (no LLM provider, expired/revoked OAuth,
`hermes setup`/`hermes model` guidance) also get a Proxima next step - pick another
agent from the Agents menu, or re-authenticate Hermes - so owners are not stuck
with CLI-only advice. Hermes readiness (`hermes_status` / `runner_readiness` /
Home auth health) treats `auth.json` `last_auth_error.relogin_required` as not
ready, instead of green-lighting a home that only has stale credential files.
The chat and Home task Agents menus badge each profile from that map (`not ready`
vs the runner display name), Settings → Agents runner pickers show
`ready` / `not ready`, and the Settings → Agents runner grid chips each installed
runner as `Ready` / `Not ready` (with the auth hint under the card when auth
failed) instead of a blanket `Runnable` - so the banner, profile pickers, and
runner cards all agree when Hermes login is expired. On Chat, the Agents dropdown
locks while the open session has active work (`busyRun` from `useRunStream` —
local send, goal-loop next turn, or reconnect restore of a still-running session),
with title/aria "Agent locked while a run is in progress"; it unlocks when the
session is clean so a mid-run agent switch cannot race `setSessionProfile`.
Agent/app/script subprocesses share `subprocess_env` / `augmented_path`: common
user-local bins are appended, and when the host has `python3` but no `python`, a
workspace-local shim (`$PROXIMA_WORKSPACE_ROOT/shims/python` → python3) is
prepended so plan steps and scripts that still call `python` do not die with
command-not-found on Debian/Ubuntu-style hosts.
**Endpoints:** `GET /api/runners/detect` (installed/ready status).

## 2. Profiles (agent personas)

**Why:** Each profile = an agent persona with its own runner, isolated credential
home, default model, and system instructions ("soul").
**How:** `profiles` table (one owner, many profiles). `claude_live_home` mode points
claude-code at the real `~/.claude` so it inherits your skills/plugins/rules/memory.
**Skills & MCP (auto-detect per runtime):** each profile shows the skills + MCP servers
its runner actually has on this host. Detection is a **multi-root OS-aware scan**
(`capabilities.py`): the runner's own config dir (`RunnerSpec.source_dir` + skill
subpath), extra roots from an OS×runner path table, shared registries such as
`~/.agents/skills` (and Windows `%USERPROFILE%` / `%APPDATA%` equivalents), plus
owner-configured **custom skill roots** (global Proxima setting under Settings →
Agents, not per-profile). Results are unioned and deduped by skill id (first root
wins). Invalid custom paths are skipped with a warning - never crash. Detection is
**cached**; automatic refresh on cold start, opening Skills & MCP, or profile/runner
change; manual **Rescan** button and `POST /api/runners/{id}/capabilities/rescan`
(or `?rescan=1`) bust the cache. The user checks which to enable per profile
(`profiles.capabilities` JSON; NULL = inherit all). Enabled skills are symlinked into
the profile home and its MCP config is filtered to the selection for Claude
(`.claude.json`), Codex/Grok (`config.toml`), and Hermes (`config.yaml`). Selection
is re-applied idempotently before each run. Pi still uses its runner-global home;
`claude_live_home` profiles likewise use the host config directly. **MCP stays
enable/disable only** - never promoted to slash commands.
**Endpoints:** `GET/POST /api/profiles`, `PATCH/DELETE /api/profiles/{id}`,
`GET /api/runners/{id}/capabilities`, `POST /api/runners/{id}/capabilities/rescan`,
`GET/PUT /api/settings/skill-roots`, `GET /api/tools/recommended`.

### Baked-in capability bundle (Phase-1 slice 9, T8 - LIVE)

**Why:** batteries-included instructions/skills, BYO credentials and binaries: every
Proxima user gets a curated skill set and work-discipline defaults without installing
anything, while Proxima never ships or manages binaries.
**How - three layers:**
- **Discipline pack (runner-agnostic, always on):** `GENERAL_GUIDE` in
  `wiki_memory.py` carries a compact "Work discipline" section (evidence-first,
  slice small, self-review before done, keep wiki memory current, prefer existing
  scripts) in every session preamble. Lean by design - a section, not a dump.
- **Bundled skills (runner-native, opt-out-able):** `bundled-skills/` at the repo
  root is a SECOND source in the existing skill-symlink mechanism
  (`capabilities.py:detect_bundled_skills`). Content-pluggable: any folder there
  with a `SKILL.md` is a skill - no list in code. Bundled skills get ids
  `bundled/<name>`, are symlinked into every profile home whose runner has a skills
  dir, and opt out per profile through the same `profiles.capabilities` selection
  JSON as host skills. First content: the owner's **masterplan** skill (vendored
  from labsiqbal/masterplan, MIT - see `bundled-skills/masterplan/PROVENANCE.md`).
  Live-home claude profiles are untouched (nothing seeded or symlinked - the user's
  real `~/.claude` rules). Config: `bundled_skills_dir` /
  `PROXIMA_BUNDLED_SKILLS_DIR` (default `<repo>/bundled-skills`).
- **Tools detect-and-advertise (never vendored):** `bundled-skills/recommended-tools.json`
  lists recommended host CLIs (markitdown, lavish-axi, gh, optional **headroom** /
  **headroom-ai** - data, not code). `recommended_tools.py` probes PATH at run
  setup (primary `bin` or optional `alts`); PRESENT tools get a one-liner in the
  run preamble. Missing tools surface only as a quiet hint in Settings → Agents
  ("Recommended tools" panel) and never block a run. Headroom is optional best
  practice for host-side token savings - Proxima works without it; if a headroom
  MCP server is installed on the host it appears under profile MCP enable/disable
  like any other MCP (no wrap-by-default spawn).

### Skill slash commands (`/` palette)

**Why:** enabled skills deserve the same visible front door as `/masterplan`, without
turning MCP into slash commands.
**How:** for the active profile, each **enabled** skill becomes a Proxima command
in the slash catalog (group **Skills**). Naming uses the skill leaf
(`/grill-with-docs`); reserved built-in Proxima commands always win name collisions
(skill becomes `/group-leaf`). `/masterplan` stays first-class for the bundled
skill. Optional freeform args work like masterplan (`/skill freeform…`). Invoking
a skill slash queues an `agent_turn` whose prompt requires that skill id; the
worker force-activates the skill for the run when needed. Composer loads the
catalog with `profile_id` so switching profile/runner refreshes the list from the
detection cache. The Chat `/` popover lists every prefix match and scrolls with a
~4-row viewport (`SLASH_COMMAND_LIST_VIEWPORT_ROWS` / `--slash-list-viewport-height`)
- same short-list product language as `@` mentions, not a hard cap of four.
**Endpoints:** `GET /api/commands/catalog?profile_id=…`, `POST /api/commands/execute`
(with optional `profile_id`).

## 3. Chat (the core loop)

**Why:** Talk to an agent; it streams back, runs tools, asks for approvals.
**How:** A `session` holds messages; a `run` is one agent turn driving the ACP
session. The worker is bounded-concurrent (`run_worker_concurrency`) so one slow run
never blocks other chats. Tool permissions ask the owner by default; auto-approve is
an explicit Settings opt-in. Streaming uses SSE (`/events/stream`) + WebSocket.
An empty thread (`ChatEmpty` via shared `CompactTeachingEmpty`) stays sparse by
default - title, one lead line, short tooltip hints, and a **How it works** dialog
for the fuller tutorial - so the composer remains the primary CTA rather than a wall
of teaching copy. With messages, the log is **top-anchored** under the header
(`.thread` `justify-content: flex-start`; no messenger-style pin-to-composer).
The keep-alive flex chain (`main-pane` → `surface-pane` → `chat-stage` → `thread`) bounds height with
`min-height: 0` and keeps **`.thread` as the only vertical scrollport**
(`overflow-y: auto`) so long chats scroll with wheel/trackpad. Short threads
that fit the viewport do not force a bottom jump; overflowing sessions open on
latest and follow the bottom while the owner stays near it
(`threadScroll` / `ChatThread`). Master's empty desk and Design Studio's start home use the same
progressive-disclosure pattern (no capability list or numbered steps on the default
surface; Design home does not print the project display name - the shell switcher is
enough).
**Endpoints:** `POST /api/chat/send`, `/api/sessions/{id}/runs`, `/messages`,
`/events/stream`, `WS /api/ws/sessions/{id}`, `/runs/{id}/cancel`, `/runs/{id}/permission`.

### First-class masterplan planning (`/masterplan`)

**Why:** the bundled masterplan methodology has a visible front door in Chat instead
of requiring users to know that a natural-language skill exists.
**How:** `/masterplan <idea>` is published in the server command catalog under
Planning, so the shared composer slash palette lists it with the same accessible
name spacing as other commands. The chat route preserves the typed command as the
visible user message, but queues a real `kind='masterplan'` agent turn whose internal
prompt explicitly requires `bundled/masterplan` and passes the freeform idea into
Phase 1. A bare `/masterplan` still starts the turn and tells the skill to ask for the
idea first. Immediately before execution the worker adds that bundled skill to an
explicit profile skill subset without rewriting the profile's saved opt-out choices.
Once a session has started a `kind='masterplan'` run, the worker keeps the skill active
for that session's ordinary chat follow-up turns so clarification and review cannot
prune it mid-methodology. Starting the command in a session with a blocked goal cancels
that old goal, preventing the next clarification reply from accidentally resuming goal
mode instead of continuing the masterplan. The skill remains the methodology and writes
its normal Markdown / HTML package folder artifacts; this command does not open Design
Studio. Ordinary natural-language masterplan requests continue to work through skill
discovery.

### Project-file and artifact references (`@`)

Every project-scoped prompt surface shares the same mention picker: Code chat, the Ops
Task Composer, workflow authoring chat and graph fields, Design Home, Design chat, and
Graph Home. Typing `@` filters a merged index of project files **and produced
artifacts** (designs, images, pages, docs, apps, videos, and other deliverables under
the conventional artifact roots) and inserts the selected reference at the caret;
arrow keys plus Enter/Tab work as well as the mouse. Ordinary files and non-image
artifacts become project-relative paths, which project-scoped runners can open from
their working directory. Images become `![name](path)` references, so `/image`
providers and design-agent vision receive the selected pixels instead of only a
filename. Produced artifacts carry a short kind badge (Design, Image, Doc, …) so they
are visually distinct from plain source files; artifacts are ranked ahead of the file
tree when the query is empty. The popup viewport shows four ranked matches at once;
additional matches remain available by scrolling, while typing more of the path or
title narrows the full bounded index.

The picker never expands text-file contents into the prompt. Its authenticated
`GET /api/projects/{slug}/reference-files` index is bounded and project-jailed, skips
symlinks, dependency/build/cache and hidden directories, and omits common secret/key
files. Produced artifacts come from the existing
`GET /api/projects/{slug}/artifacts` scan (year-long window, scanner-capped) and are
merged client-side by `useProjectMentionItems` so every Composer / `MentionTextarea`
surface stays on one pipeline. Vision loading re-validates paths against the session
project and accepts only bounded image files (10 files, 8 MB each, 32 MB total).

### Per-prompt Brainstorm / Debate modes

> **Status:** the `Brainstorm`/`Debate` chips are shown only in **Code chat**. The Ops
> Task Composer and the Design chat omit collaboration modes (tasks and design
> sessions are single-agent).

**Why:** Run a prompt through multiple agents before the answer lands in the
main chat, instead of validating a completed answer afterward.
**How:** The composer offers per-prompt `Normal`, `Brainstorm`, and `Debate`
chips. `Brainstorm` and `Debate` are sent as `prompt_mode` on
`/api/sessions/{id}/runs`; the user message is stored as typed (no mode prefix —
the cards and result title show the mode) and one parent busy run is shown in
the chat. `Brainstorm` fans out to the configured 2–3 profile-specific child
runs in parallel, then queues a synthesis pass. `Debate` runs the configured
2–4 rounds before a neutral synthesis/judge pass. While child agents work, the
chat shows inline cards labelled with the actual agent/profile name and
round/lane: collapsed by default (a cycling "thinking" shimmer while an agent
works, a 2-line preview when done), click to expand one at a time. Brainstorm
stacks cards vertically; Debate alternates them left/right per speaker so
rounds read as a conversation. Leading runner banners and skills catalogs (e.g.
Pi's `pi v…` plus a Skills path list) are stripped from **main chat bubbles**
(display and newly stored answers), collab card bodies/previews, stored child
outputs, and synthesis prompts so owners see the answer, not the path list —
including compact `## Skills - /path` dumps, plain multiline `Skills\n/path`
catalogs from Pi ACP, and answers that start with bold/plain prose rather than
a `##` heading. The synthesis is NOT a card — it streams into the parent bubble like a
normal reply and lands as the final assistant message (synthesis only; per-agent
detail lives in the cards). Child runs still do not save ordinary assistant
messages; the card history replays from events.
Settings groups these defaults under **Agents**. The mode
resets after send, so there is no global Meeting Mode toggle.

### Message-level Validate sidecar

**Why:** Ask a different runner/profile to pressure-test a completed assistant reply
without polluting or advancing the main chat branch.
**How:** `Validate` creates a `message_reviews` sidecar row attached to the source
assistant message, queues a `kind='message_review'` run, streams review deltas to the
inline sidecar, then stores a structured verdict, gaps/risks, unanswered-input notes,
revised content, and suggested next move. The sidecar offers an `Auto` reviewer choice
(defaulting to a different runner) plus a local profile picker override. It can be
minimized to a compact summary (the head control keeps a spaced accessible name
such as `Validate, Pi · needs_work · 5 gaps. Expand`). `Replace answer` overwrites
the original assistant message while preserving the original in the review row
for `Restore original`.
`Ask source to merge` queues a `kind='message_review_merge'` run by the source profile
and writes its result back into the same sidecar, not as a normal chat message. Review
output is never saved as a normal assistant message unless explicitly replacing the
source answer. Brainstorm/Debate are intentionally not sidecar actions; they
live in the composer before a prompt is submitted.
**Endpoints:** `GET/POST /api/messages/{message_id}/reviews`,
`POST /api/message-reviews/{review_id}/replace-answer`,
`POST /api/message-reviews/{review_id}/restore-original`,
`POST /api/message-reviews/{review_id}/ask-original`.

## 3b. Master orchestration, restore safety, and Attention

**Why:** the owner can either work hands-on in Chat or delegate an outcome to a
built-in orchestrator without manually composing every worker task.

> **Activation:** durable Master identity and compatibility migration are live.
> The product runtime and UI are behind the server-owned
> `feature_master_orchestrator` gate, which defaults off. Migration is
> unconditional and safe with the flag in either state, while feature-off
> startup and unrelated routes do not provision a Master runner home. Codex
> app-server 0.145.0 or newer is the one supported production Master adapter.
> Every other adapter still fails closed before a turn starts. Runner discovery
> publishes the static chat-only declaration plus dynamic host eligibility and
> its reason. The Master selector enables only dynamically eligible adapters; a
> legacy or unavailable backing runner remains a disabled explanatory state.
> During external maintenance or any ingress-unavailable transition, discovery
> keeps its read-only binary projection but skips process-backed conformance and
> marks Master eligibility unavailable. Settings, message creation, and worker
> spawn retain their authoritative checks.

**Master identity and desk:** when the feature is enabled, the authenticated Master
entry point creates or reuses exactly one hidden
`profiles.system_kind='master'` system identity and one project-unbound
`sessions.mode='master'` thread for the owner. The hidden profile never appears in Agents or ordinary
Chat history; Settings/desk runner selection creates or reuses the matching system
home while the UI counterpart stays named Master. The desk reuses Chat's shared
composer for delegation (attach + `@` project mentions; submit still hits
`/api/master/messages`). A successful send returns the canonical persisted user
message with its durable id, so the provider can replace the pending row without
polling or letting the streamed reply sort ahead of its prompt. The work side panel
keeps independent, default-open Fleet work, Decisions, and Safety accordions. Each
list has a three-entry scroll viewport, so the entire panel stays available without
a hide/collapse preference. One authenticated `MasterStateProvider` above `AppShell` owns the
canonical desk/session, ordered thread, active turn, durable event cursor, one SSE
connection, reconnect reconciliation, unread count, composer draft and selection,
Focus, per-message target, popup state, transient notifications, Fleet registry,
and stable scroll/panel state. The full-page Master home and floating popup consume
that same interface without shadow stores, duplicate composers, or a second live
connection. Moving between those views preserves the draft, target, Focus, active
run, ordered thread, and scroll anchor. Owner-keyed session storage restores the
draft, selection, and scroll anchor after a browser refresh, while the existing
owner-keyed target preference remains durable.
Logout, owner/token transition, onboarding, feature-off, and update application
abort stale work and close the old stream. Refresh state is keyed by owner and never
crosses an owner transition.

The popup is available from normal authenticated shell surfaces through a labeled
floating trigger and `Ctrl`/`Command` + `Shift` + `M`. It can persist at either
bottom corner, avoids the tool dock, drawers, mobile chrome, safe areas, and toast
region, and becomes a full-height sheet on narrow screens. Its modal dialog traps
keyboard focus, closes with Escape, and returns focus to the trigger. Opening the
full Master home closes only the presentation layer.

The shared composer defaults to **Let Master route**. The owner may choose an
explicit Container and, in an advanced row, an optional Area override from the
registered Fleet. Master home also has an independent Fleet/Container Focus picker;
changing it never changes the shell's active Container. If an explicit message
target differs from Focus, the UI announces the Focus change and the API records
the new Focus before it enqueues the turn. `master_message_context` durably binds
the Focus and target ids to the user message. The restricted prompt and
`MasterToolBroker` then enforce explicit routing, or keep automatic routing inside
a Container Focus. When either an explicit target or Container Focus pins only
the Container, `query_context` may select an exact registered Area in that
Container but rejects an Area owned by another Container. An explicitly pinned
Area remains authoritative. Sent messages display that durable routing metadata.

The existing Master-session SSE stream is the only live path. It resumes from the
durable cursor, deduplicates replay, ignores raw delta events, and applies typed
Task/review/Attention/Satpam projections to the thread and work panel once. A
bounded authoritative desk/messages/events reconciliation runs only after a
disconnect, reconnect, detected sequence gap, malformed event, or explicit retry. It
does not restore the former five-second Master desk poll. The SSE generator flushes
an initial comment so an idle healthy connection becomes Live immediately while
retaining the same cursor and event contract. The first desk response supplies a
constant-size durable `event_cursor` barrier before the final desk/message snapshots;
bootstrap opens the stream at that barrier without fetching the full event history,
so neither a delayed first event nor an in-flight snapshot can lose state.

Durable chat projections remain the notification source of truth. Relevant named
projection events may also produce one short-lived shell toast. Progress is
coalesced by stable Task source, terminal transitions are deduplicated by durable
message id, and raw token, reasoning, and tool deltas never toast. Polite results
use status live semantics, failures and owner-attention transitions use alert
semantics, dismissal is keyboard reachable, and toasts never move focus. Optional
desktop notifications for background tabs use the same bounded summaries.

The home renders queued, running, review/attention, completed, and failed
Master-owned Tasks, the needs-you subset, job-scoped checkpoint timeline, and honest
capacity (`running / configured max`, free slots, queued). Loading, empty,
disconnected/retrying, error, populated, sending/thinking, and multi-Task states are
explicit on desktop and mobile. A compact New Task control seeds the one shared
composer rather than starting a parallel launcher flow. The empty home is sparse by
default (`CompactTeachingEmpty`: title, one lead, tooltip chips, **How it works**)
so the Delegate composer stays the primary CTA.

**Tool-result readability:** Master product-tool outcomes are compact collapsed
disclosures in the durable conversation. Their summary uses plain product language
for success, failure, and incomplete streaming results; linked Tasks and a bounded
raw payload remain available only after explicit expansion, preserving auditability
without letting JSON dominate the desk.

**Restricted runtime and product tools:** the centralized runner contract requires
an explicit `master_chat_only` capability. A conforming Master receives one dedicated
managed runner home and an empty read-only non-source scratch. It receives no
Container, Area, repo, Ops, source, runtime, config, ordinary profile home, path, or
credential. Its stored capability selection is exactly
`{"skills":[],"mcp":[]}` and is reapplied strictly on each run and runner switch.
Every runner-native permission request and native tool event is rejected. Codex
uses empty execution environments plus a private loopback provider firewall that
replaces its complete tool carrier with exact server-owned broker schemas and
replaces runner-generated developer context with a fixed filesystem-isolated
policy. The firewall rejects schema drift, encoded or ambiguous transport,
redirects, and oversized responses before releasing provider bytes. Codex's
carrier-free HTTP fallback is accepted only after exact dynamic schemas are
attested on the same ephemeral thread. Host paths and bearer material stay out of
model input. See
[Runner conformance](runner-conformance.md) for the adapter matrix.

Codex Master calls native dynamic Proxima functions; the compatibility harness
uses structured `<proxima-tool>{name,arguments}</proxima-tool>` calls.
`master_runtime.py` parses compatibility envelopes safely, and `MasterToolBroker`
validates every argument against a closed JSON schema before invoking a
server-owned handler in the API process. Supported reads are `list_containers`,
`get_container`, `get_live_state`, `list_tasks`, `list_task_agents`, and
`list_recipes`. Supported mutations are `delegate_tasks`, `start_tasks`, and
`create_attention`.
`query_context` routes Fleet / Live / Knowledge / Code layers through the scoped
context router (Group 11) with budgets and provenance. No tool accepts filesystem
path inputs or returns absolute host paths, credentials, runner homes,
configuration, or arbitrary tool input. `query_context` citations intentionally
carry validated paths relative to the selected Ops or Code Area scope.

Group 9 supplies the host-path-free graph state boundary beneath that tool.
With Master enabled, authenticated owners can read exact Container and Area graph
state and request one explicit rebuild. The adapter pins Graphify `0.9.28`, resolves
all roots from registered Container and Area identities, excludes nested Areas from
a root-repository Code graph, and enforces server ceilings for build/query time,
depth, tokens, result count, and graph bytes. Every internal query result carries
exact scope, generation, freshness, citations, and provenance.

Group 10 adds the **Code graph lifecycle** on top of that adapter:

- One distinct Code graph state row and canonical
  `<repo-area>/graphify-out/graph.json` per registered repo Area (never a Task
  worktree). Generated output is gitignored by default.
- Initial full build is enqueued when a code Area is registered (create, link,
  detect, or manual add).
- A successful Proxima Task merge marks **only that Area's** Code graph `stale`
  immediately, then enqueues a rebuild (incremental when the base→merge range is
  safe; full rebuild for unknown history, force-push, tool-version mismatch,
  manifest mismatch, or failed incremental).
- External canonical HEAD or tracked-source fingerprint drift is detected by a
  scheduled audit that only walks already-registered Code graph Areas.
- Stable working-tree dirty tracked changes are debounced and then enqueued.
- Failed, interrupted, ENOSPC, malformed, or incomplete rebuilds preserve
  last-good bytes and leave Tasks / SQLite Live state unaffected. Canonical
  metadata reads and last-good preservation use descriptor snapshots bounded by
  `graph_max_bytes`; the prior generation is copied and replaced atomically
  without buffering it in the API process. A fsynced publication journal records
  the prior and replacement digests before replacement. Graph-state finalization
  commits its update and final read in one transaction. An ambiguous commit result
  is accepted only after the writer has left its transaction and an independent
  read-only SQLite connection plus the bounded canonical hash both match the
  replacement. This preserves any queued follow-up rebuild; unresolved outcomes
  leave the journal for locked reconciliation. Failure transitions only own rows
  still in `building`.
- Repo Task-agent homes receive one server-managed `proxima-code-graph` MCP entry
  fixed to exactly their selected Area; arbitrary `project_path` is ignored.
  The Master does not inherit this MCP entry.

Group 11 adds the **Knowledge graph lifecycle** and the **typed context router**:

- At most one Knowledge graph per Container Ops area at
  `<container>/ops/graphify-out/graph.json`, with state in `graph_states`.
- Builds read only the resolved Ops allowlist: `container.md`, `design.md`,
  curated `wiki/**/*.md` (not `index.md` / `log.md`), `reports/**` text docs, and
  durable artifact metadata named `METADATA.md` or `*.meta.json` under
  `artifacts/`. Other artifact files, Repo Areas, secrets, caches, graph outputs,
  Task transcripts, scripts, uploads, exports, and runtime data are excluded.
  Symlinks and nested VCS trees are skipped. Query-time citation validation
  re-resolves each selected source and rejects a directory that gained a VCS
  marker after publication. Other active Container roots in the owner's fleet
  are excluded when nested beneath any selected graph scope. Directory traversal
  is lazy and independently caps visited entries and directories, including
  excluded or unsupported content that never becomes a source.
- Container create/link enqueues the initial Knowledge build. The same database
  transaction that finishes an Ops Task writes **only that Container's** durable
  Knowledge rebuild intent. A background tick drains the outbox, marks the graph
  stale, and queues filesystem work. Debounce ticks compare cheap allowlisted file
  metadata markers and hash file contents only after a marker changes. Startup
  and scheduled audits still verify full content fingerprints and tool drift; a
  scheduled full rebuild re-walks registered Knowledge graphs only.
- Failed, interrupted, ENOSPC, malformed, or incomplete Knowledge rebuilds keep
  last-good bytes. Tasks and SQLite Live state never depend on graph availability.
- `query_context` routes through `context_router`: Fleet questions to the Fleet
  registry; running/green/successful/done/cancelled/blocked/status to SQLite Live
  state; Container facts and decisions to one Knowledge graph; and code structure
  and impact to one Code graph. Focused status questions filter in SQLite before
  applying the result limit, with green/successful/completed mapped to `done`.
  Blocked/stuck includes both explicit `blocked` jobs and dependency-blocked
  `queued` jobs whose `blocked_reason` is set.
  Mixed requests call only the needed layers with budgets and never merge
  fleet-wide graphs. Focused graph results cannot include another Container's
  nodes. Durable explicit targets and Container Focus override model scope; either
  form may permit an exact owned Area when only the Container is pinned, while an
  explicitly pinned Area remains authoritative and cross-Container Areas are
  rejected. Unmatched focused questions use Knowledge; unmatched fleet questions
  use Fleet and Live. Every graph layer keeps generation, freshness, scope-relative
  citations, and provenance.
- Local-only structural extraction is the default and is visible in Master
  settings (`graph_policy`), graph state `semantic_backend`, rebuild logs, and
  docs. Cloud semantic egress stays off unless an explicit future captain policy
  enables a real adapter; configured cloud credentials never unlock egress.

**Focus epochs and prompt isolation:** Master begins in fleet mode with no Focus
epoch. `master_focus_state` durably records the current Focus plus one pending
Fleet or Container Focus, while immutable `message_focus` and
`runs.focus_epoch_id` capture the
epoch that actually owned each user turn, response, tool result, and projection.
An idle Focus change uses an optimistic version check, closes and opens epochs,
adds a boundary message, and emits `master.focus.changed`. A running turn can
only record one pending Focus; sends return 409 until it closes, then the pending
Focus applies exactly once. Explicit cross-Container sends change Focus and
enqueue in the same transaction. Generic session run producers reject the Master
session, and the database refuses any non-Master run kind or mismatched epoch there.
Task delegations copy the captured epoch before the tool result returns, so delayed
Task and supervision projections retain their original Focus after an origin
message or run is deleted. Migration-era Tasks without provable attribution remain
startable after scope validation but unprojectable, and their projection failure
does not starve later reconciliation candidates. The restricted Master runner
process is rebuilt for every Master turn and its durable history is limited to the
captured epoch, so prior Container ACP/model context cannot cross a boundary.
The Master home projects the one canonical roving thread into `Roving thread`,
`Fleet history`, and per-Container folders without creating or copying a
session. A Container folder contains only its immutable Focus segments plus
asynchronous system updates whose durable subject is that Container; Fleet
requires positive Fleet attribution and excludes Container-subject updates. Focus
boundaries, focused segments, system updates, and specialized tool-result rows are
visibly labelled. Historical Container and target ids are append-only facts rather
than foreign keys that null on deletion, so an unavailable Container folder remains
selectable without leaking its messages into Fleet. Selecting an available Fleet or
Container history folder explicitly changes durable Focus, while selecting the
roving thread or an unavailable historical folder is read-only. The shell Container
remains independent: `Focus Master here` is an explicit bridge, never an implicit
shell-selection side effect. Pending Focus, explicit-target Focus effects, and Fleet
mode remain visible in both shared home and popup state. See
[ADR-0007](adr/0007-master-focus-is-a-durable-execution-boundary.md).

The shared provider bootstraps Focus and its optimistic version from the Master
desk, writes picker changes through the durable Focus endpoint, and consumes
`master.focus.changed` on the existing session stream. Focus is never restored
from local storage. Deleting an idle focused Container closes its epoch and moves
Master safely onward while retaining immutable message Focus, subject, target,
Area, and epoch ids; deletion is refused before filesystem changes while that
Master turn is active.

**Endpoints:** `GET /api/containers/{slug}/graphs`,
`POST /api/containers/{slug}/graphs/rebuild`,
`GET /api/settings/master` (includes `graph_policy`), and
`PUT /api/master/focus` (versioned current or pending Focus).

`delegate_tasks` and `start_tasks` call `TaskDelegationService`; they do not create
or start jobs directly. A Master batch may name client-local Task keys and
dependency keys; all Tasks and dependency edges commit atomically, cycles fail
without partial rows, and repeated envelopes return the same Tasks. Per-turn
`master_tool_calls` records bind an envelope hash to its root turn, so duplicates
and crash retries stay idempotent. Requests, individual results, result rounds,
calls per round, tool rounds, and total turn output are capped. Malformed, unknown,
disallowed, oversized, and duplicate calls become stable visible chat errors, never
partially truncated hidden actions.

Every success or failure is written to the thread and supplied to a bounded Master
continuation, so a product read can inform the next in-process call without an HTTP
control loop. Fresh restricted Codex threads receive a bounded transcript rebuilt
from durable Master messages, preserving history across restarts and runner
switches. The Master worker default is three slots and its claim query refuses work
above the configured limit; extra runs remain queued and capacity counts each queued
worker run, including parallel graph branches. Existing job capabilities are
unchanged, including commit/push/PR through the owner's BYO `git`/`gh` environment.

**Permission separation:** a Master turn never auto-approves runner-native
permissions. A Master-created Task-agent keeps its own guarded or autonomous
execution policy, and repo landing review remains independent of that choice.
Autonomous Tasks may use the existing scoped approval path; guarded Tasks do not.
Ordinary Chat continues to honor the install's separate Settings toggle. A
non-Master job permission request becomes a durable
`permission_job` Attention item and closes when its choice reaches the live ACP
process, preventing the old hidden-session 300-second dead end.

**Checkpoints and Chat restore:** `job_checkpoints` stores one job's row/node/run
state plus git SHA/worktree refs - no full SQLite backup and no project zip. Unpinned
retention is FIFO 30, restore previews its impact, requires confirmation, and refuses
running/later same-project conflicts or a dirty job-owned worktree. A main-checkout SHA
is evidence only and is never reset; only an existing job worktree is restorable.
Restore commits the job, node/run rollback, Task-session `job.update`, audit metadata,
and a durable human-readable Master recovery entry together. The entry identifies the
owner, checkpoint, prior/restored state, discarded progress, and conflicting progress
without copying worktree paths, Task titles, or arbitrary graph identifiers. Recovery
events use the same 16 KiB durable-event encoder as Master projections. All fallible
database writes and worktree checks finish before reset; a failure after reset restores
the original worktree commit before the database rollback is returned.
Normal project Chat uses
ACP tool events to trigger a bounded before/after path journal. Assistant replies with
changed files show **Restore N changed paths**; preview lists each path and warns about
active Master work before confirmation. The journal cascades when its session closes.

**Attention:** the shell badge calls one `/api/attention` shape spanning simple final
job reviews, complex diff reviews, pending satpam restarts, durable tool permissions,
and Master decision/budget items. Every row deep-links to its owning Task/plan/Master/
Settings surface. Only rows marked `inline_ok` render actions: simple non-repo final
review, hash-visible script trust, pending satpam restart, and live permission choices.
Diff and open-text Master items navigate only. Errors persist inside the inbox until
retried/dismissed. Job-linked rows include the same canonical run projection used by
Workflows and Tasks, so a review-parked failed graph node reads Failed everywhere.

**Running work:** a sibling shell control next to Attention polls `GET /api/runs/active`
and running jobs, badges a count when work is in flight, and deep-links each row to
the task workspace or chat (with a Tasks index shortcut).

**Unattended:** the desk toggle is opt-in. `MasterSupervisor` starts only already-queued
Master jobs; it never dispatches work while off and never participates in stuck-run
recovery. Saved turn (1-200) and wall-clock (5 minutes-24 hours) budgets apply on the
next tick. The optional token value is stored/readable, but current ACP runner events
do not expose usage, so turn + wall-clock are the enforced caps. Exhaustion turns the
mode off cleanly and creates an `master_budget` Attention row. Unattended runs only
start already-queued Master Tasks, each following its own Guarded or Autonomous
execution policy for ACP approval, plus normal BYO push/PR capability; destructive
product admin is not in its handler set. `master_max_parallel` limits queued plus
running Task-agent runs, and dependency-blocked rows do not starve later eligible
Tasks. Start and worker claims revalidate the owner, Master session, Container, Area,
Task-agent, delegation audit, and dependency graph. Immediate database transactions
reserve capacity and unattended turns across processes; graph branches share that
same global cap. A process-local tick mutex avoids redundant same-process work.
Satpam remains the sole steer/restart authority.

**Durable Task and supervision projection:** `MasterProjectionService` appends
concise Task start, review-ready, completion, failure, cancellation, stable
prerequisite-block, Attention, supervisor-outcome, and Satpam messages to the one
Master thread. `master_projections` is an owner-scoped idempotency/link ledger, not a
second lifecycle ledger: jobs, runs, checkpoints, Attention, node state, and Satpam
rows remain authoritative. Each projection also emits one named event on the
existing session SSE stream with stable source, Task, Container, Area, checkpoint,
intervention, message, projection, toast, captured Focus, and subject keys. Live
projection rendering uses that transactionally committed attribution rather than
guessing from current browser Focus. Raw token, reasoning, and tool deltas are never
copied, and the matching ledger/event payload is bounded to 16 KiB.
Server-owned projection summaries do not copy Task titles, runner errors,
permission commands, Attention text, Satpam reasons, paths, or credentials.
Projection message, event, and ledger links commit atomically; strict startup
validation rejects incomplete, cross-owner, malformed, or mismatched source/type
state. Restart reconciliation safely retries missing projections without creating a
second message or event and isolates failures per authoritative source row. SSE
reconnect accepts the existing cursor query and `Last-Event-ID`. No projection can approve review,
landing, Attention, or Satpam gates. See
[Master supervision and durable projections](master-supervision.md).
Owner mutations that happen outside a worker run append a transaction-coupled
`job.update` to the Task session. Review completion/failure also writes its durable
Master projection in that same transaction and defers Task and Master stream
notifications until commit. A mounted Task workspace consumes this one shared
invalidation path for review verdicts and checkpoint restore instead of waiting for
running-only polling.

**Tours:** after setup, the first main-UI visit opens a keyboard-trapped core tour
with five chapters when Master is enabled and four when it is disabled. Completion
is reconciled between the feature-off browser marker and Master settings so enabling
Master does not replay a tour the owner already completed. Settings → Help & Tours
can replay it and launch chapters for Workflows, Projects/tools, Archive,
feature-aware Design, Agents, remote/safety, and Settings.

**Endpoints:** `GET /api/master/desk`, `POST /api/master/messages`,
`GET/PUT /api/settings/master`, `GET /api/attention`,
`POST /api/attention/{id}/act`, job checkpoint list/preview/restore/pin routes, and
`GET/POST /api/chat/messages/{id}/restore-turn`.

For one compatibility release, deprecated `/api/alpha/desk`,
`/api/alpha/messages`, and `/api/settings/alpha` aliases read and mutate the
same Master records. Legacy payload readers accept Alpha ownership keys, while
canonical responses expose only Master naming and
`origin_master_session_id`. See
[Master persistence migration](master-persistence-migration.md).

## 4. Goal loop (multi-step autonomy)

**Why:** Give a goal; the agent keeps advancing across turns until done.
**How:** `/goal` sets an objective; the advance hook carries prior-step context.
Phase-1 note (T5): for repo work the plan/job path with timeout auto-continuation
(§8 Long work) supersedes the goal loop as the long-run mechanism; goal mode remains
a chat-side feature as-is (its timeout behavior is unchanged by slice 5).
**Endpoints:** `POST /api/sessions/{id}/goal`, `/goal/cancel`.

## 5. Chat → Wiki (knowledge continuity)

**Why:** Distill a conversation into a durable wiki note.
**How:** `wiki-note/draft` spawns a run that produces a `wiki.draft` event → preview
→ `wiki-note/commit` writes the markdown into **that chat session's project wiki** +
rebuilds the wiki index. After approve, the chat shows an in-app status line with
the saved path (desktop notifications stay background-only). Opening or switching
a chat session pulls the shell project to match (so Files / @-mentions start on the
conversation's project); an intentional Projects pick still sticks for Tasks/Files/
Archive while an older session stays in memory. The chat header always prefers the
open session's project over a desynced shell pick.
**Endpoints:** `POST /api/sessions/{id}/wiki-note/draft`, `/wiki-note/commit`.

## 6. Chat → Plan (slice a goal into runnable jobs)

**Why:** Turn a conversation into a **directly runnable plan** — a DAG of jobs — not
just a saved recipe (run-first, recipe-later: T2).
**How:** `promote-workflow` has an architect agent slice the chat. The graph path is
enabled by default and emits a normalized `{nodes,edges}` DAG with typed outputs,
review gates, and **per-job work bindings** (Phase-1 slice 3, T1/T2): the prompt
carries the project's registered code areas, and every job is tagged with one `target`
(a code area or `ops`) plus the derived `touches_repo` marker — an unclear binding is
marked ambiguous with a question for the owner instead of a guess. The slicer is
explicitly instructed to size each job to complete within ONE turn quota (T5 slice 5:
continuation is the safety net, not the plan). The draft lands as
a queued plan the owner reviews/edits and starts directly. Its graph and click-to-edit
title autosave through a debounced queued-plan PATCH, including a flush before leaving
the screen or starting, so there is no manual Save gate. Saving it as a reusable
Workflow is an optional, separate one-click action (before or after the run). Slice-into-plan is
single-flight on the button (double-click cannot start two promote runs), and the
Recipes editor creates at most one graph job per draft object — React Strict Mode
remounts reuse the in-flight create so Tasks does not list two identical queued plans.
The feature flag remains an owner recovery switch; the legacy ordered-step path is
retained only for existing data.
**Endpoints:** `POST /api/sessions/{id}/promote-workflow`.

## 7. Workflows (plans worth repeating) + schedules

**Why:** Codify a repeatable multi-step process the agent can execute — with branches,
per-node agents and review gates, not just a straight line. A saved template (Workflow)
is the **optional promotion of a plan** (run-first, workflow-later): plans run without
one, and "Save as Workflow" works in one click before or after the run from the canvas,
or from a Tasks plan row. Category and description stay optional secondary metadata
and never gate promotion. **One workflow = one project** - open plans/templates use the owned
`project_slug`; the shell project filter does not rebind mid-edit/run.
**How:** authored on the **graph canvas** (see §Graph workflow engine): nodes carry
`instruction`, `expected_output`, `rules`, an optional per-node agent and review gate;
edges carry dependencies. The trigger node owns **Manual / Scheduled** mode. Manual
triggers declare their intake fields (`text`, `url`, `number`, or `file`) in the node
inspector; each field's `{{id}}` is asked for at run time and substituted into node
text. Scheduled triggers own cron, overlap, and enabled settings instead and start with
no human intake prompt. Existing graph workflows keep the compatibility
`workflows.inputs` projection, which is migrated and hydrated onto the trigger so old
templates and `{{id}}` references continue to work. An **authoring chat** (Plan Chat) beside the
canvas emits `<workflow-graph>` blocks that are applied to the plan on screen, never
the database. The panel **reflows** within its drag width (240–620px,
`proxima.graph.chatWidth`) so long prose and tool chips wrap without a horizontal
scrollbar; **open state is persisted per plan** (`proxima.graph.chatOpen.<jobId>`) and
auto-opens when that plan's authoring session still has an active run. Reopening mid-
or post-generate **restores** the live run (or applies a completed graph block once)
via `useRunStream.restore` — leave Workflows and return without a false error or stale
canvas. **Start chat** and a node's **Test in chat** share one open path onto the plan's
session (concurrent clicks await the same load) so the panel cannot stick on
Opening…; a missing session surfaces an error instead of a silent idle card.
The **Sequential recipe editor is retired** — a linear recipe is a graph with no
branches. The linear engine remains for pre-existing jobs; `IterateStage` is still
reachable from an old session carrying `workflow_id`, but no new linear workflow can be
authored.
The library home remembers its last selected **Drafts**, **Workflows**, or **Runs** tab
and presents each as a table. The Workflows tab derives **Manual (on-demand)** and
**Scheduled** groups from the trigger mode, with legacy schedule rows as a compatibility
fallback. Manual rows expose Run and ask for the trigger's intake fields. Scheduled rows
show cadence and pause/resume controls, with Run now and schedule maintenance available
in the schedule dialog. Changing the trigger to Scheduled creates its cadence when the
plan is promoted; it never carries manual intake values.
The Runs table consumes the API's canonical `run_projection` for effective status,
start, finish, and duration. API timestamp fields are timezone-aware UTC ISO strings,
so a new run cannot inherit the browser's local offset or disagree with Tasks and
Attention about a failed node parked in durable review.
The library has separate active and archived views. Archiving stops schedules and
removes the workflow from the active library without changing its owned project or
past runs, and records the pre-archive status in `workflows.pre_archive_status`.
Restoring reinstates that saved status, so a workflow archived while paused (`draft`)
returns paused and its schedules stay stopped, while an active one returns active
(legacy rows with no saved status restore to active). Permanent deletion is available
only from the archived view. `GET /api/graph/templates` hides archived rows by default
and accepts `include_archived=true` for lifecycle management.
**Schedules** target saved graph templates: a due tick (or **Run now**) spawns the same
`engine='graph'` job a manual create + start produces — including repo isolation
(`target_area_id` + worktree cut via the shared `bind_graph_job_repo_worktree` path).
A refused cut fails the scheduled job in place with an owner-facing reason rather than
running unisolated against the live code area. With the graph feature flag off, a graph
schedule is skipped with a logged warning rather than left as a job nothing will advance.
**Endpoints:** graph routes (§Graph workflow engine), `GET/POST /api/schedules`,
`POST /api/schedules/{id}/run`; legacy linear rows keep `GET/PATCH /api/workflows/{id}`.

## 8. Tasks / jobs (executions)

**Why:** Every execution — a workflow run or an ad-hoc 1-step task — as one trackable
pipeline.
**How:** classic `engine='linear'` jobs use a frozen step snapshot and run
sequentially in one ACP session (context carries free). Graph jobs (**plans**) share
the job lifecycle but keep per-node state in `node_states` and are listed via the
graph API. The API adds one effective run projection and normalizes timestamp fields
before responses. Tasks, Workflows, Attention, mounted Task detail, and expanded plan
nodes consume that contract. Mounted Task detail also listens to durable `job.update`
events for owner mutations; running polling remains only a progress fallback.
Auto-archive happens after 30 days. Old kanban tasks were migrated to 1-step jobs.
**Endpoints:** `POST /api/jobs`, `/jobs/{id}/start`, `/jobs/{id}/link-run`, `/approve`, `GET /api/jobs[...]`.

### Durable Task delegation and dependency readiness

**Why:** Work, Home quick Task, Master, and future orchestration callers must not
implement slightly different session/job/start transactions. A timeout or restart must
not create duplicate work, and cross-Area outcomes need explicit Task edges rather than
hidden multi-Area execution.

**How:** `TaskDelegationService.create_and_start` is the scoped server boundary. It
validates the owner, one Container, one active Area in that Container, and a normal
Task-agent profile. It stores execution policy separately from landing behavior,
supports a linear or graph Recipe with input, and writes the worker session, `jobs`
row, `task_delegations` audit, and `task_dependencies` edges in one transaction. The
transaction commits before start. Durable `start_requested` and idempotency identity
let startup or a repeated request resume the same Task safely. Full replay is resolved
before revalidating mutable referenced rows, so an archived Container or removed
Task-agent cannot turn a previously successful create into a false "not found."

Project-scoped compatibility requests that omit `target_area_id` resolve to the
Container's physical Ops Area. Historical project-less API jobs remain scratch jobs
and cannot produce a scoped delegation audit. Master batches accept client-local Task
and dependency keys. Duplicate edges, self-dependencies, cycles, cross-owner Tasks,
cross-Container Areas, and prerequisites already in a terminal failure state are
rejected before commit. SQLite triggers also reject dependency cycles from non-service
writers.

A requested downstream Task stays `queued` with `jobs.blocked_reason` and a durable
delegation `start_state='blocked'` until every prerequisite reaches `review` or `done`
as required. Failure and cancellation produce an explicit prerequisite reason.
Prerequisite transitions retry ready dependents; the guarded queued-to-running claim
ensures one run is created even when notifications or retries repeat. A prerequisite
cannot be deleted while dependents exist, including across Containers. The Task
workspace renders the stored reason. Startup also reconciles a graph Task interrupted
after its `running` claim but before its first node run was committed.

### Tasks screen = plans + their jobs (Phase-1 slice 3, T2)

**Why:** One index of everything running or awaiting the owner — a sliced plan and a
one-off task are the same idea at different sizes.
**How:** the Tasks screen lists graph plans alongside classic tasks. A plan row
expands into its **ordered job list** (name, target badge, touches-repo marker, live
status); an unanswered target question shows as a `where?` chip and the plan cannot
start until it is answered. **List and graph are two projections of one plan**:
branch-less plans render as a plain list, branching plans offer the read-only
dependency canvas as a toggle (the editor's own `GraphCanvas`, reused). Plan rows
carry **Open plan** (the canvas, where review acts live) and **Save as Workflow**
(promotes the plan's graph to a reusable template via the existing save mechanics).
**Board** columns are Queued → Running → Review → Done → **Failed** so a failed plan
stays visible without switching to List → Failed; list/board cards use spaced
`aria-label`s (`title · Plan · status · progress · age`) so assistive tech does not
smash the plan pill into the title. With the graph feature off, the screen shows
classic tasks only, exactly as before.
**Endpoints:** `GET /api/graph/jobs` (+ the linear list above), `POST /api/graph/jobs/{id}/save-template`.

### Repo jobs: isolated worktrees + review + local merge (Phase-1 slices 2+4 - LIVE, on by default)

**Why:** A job that touches a repo must never edit the primary tree directly (T1). It
runs in a safe copy, the owner reviews the before/after diff, and approving merges the
work **locally** into the branch it was cut from - no remote, no GitHub required
(T1 local-first; the optional push-after-merge connector is the next section). **On by
default since slice 4 shipped the
review UI**; `feature_repo_worktrees` (`PROXIMA_FEATURE_REPO_WORKTREES`) remains the
owner's escape hatch - while off, job behavior is exactly as above.
**How:** a job may carry `target_area_id` - the ONE container area it works against
(T1: exactly one target; a code-area target = repo job). On start, `worktrees.py` cuts
branch `proxima/job-<id>` from the target code area's repo into a worktree under
`<workspace_root>/worktrees/job-<id>` - outside the container, so scans never see
work-in-progress and the worktree's `.git` file can't register as a code area. The cut
refuses loudly (409, job stays queued) on a dirty repo, detached HEAD, or no commits;
crash leftovers are cleaned idempotently by job id. With the flag on, the run worker
sets the run's cwd to the active worktree (a missing worktree fails the run loudly -
never a silent fallback to the primary tree). Diff and merge operate on commits:
outstanding edits are snapshotted onto the job branch first, so partial work also
survives crashes (feeds T5 continuation, slice 5). Snapshot commits drop runtime
cache/bytecode (`__pycache__`, `*.pyc`, pytest/mypy/ruff caches, …) even when the
target repo has no `.gitignore`, and the review diff hides those paths if an older
snapshot already committed them. Final approve = guarded `--no-ff`
merge: refuses a dirty repo or switched-away base branch, aborts on conflicts and
parks the job in `review` with the surfaced error (worktree kept; approve again after
resolving) - never forced. Success records `merge_commit` on the job's worktree row
(`job_worktrees`) and tears the worktree + branch down; deleting a job also tears its
worktree down.
For delegated Tasks, Guarded or Autonomous controls in-run permissions but never
changes landing policy. Delegated Ops Tasks finish directly in physical `ops/` because
their work already landed in place; every repo Task stops in `review` for its diff and
local-merge verdict. An explicit Recipe review gate remains an in-run decision.
**Endpoints:** `GET /api/jobs/{id}/diff` (per-file status + unified patch; also
readable after the merge), `POST /api/jobs/{id}/approve` (merge point),
`POST /api/jobs/{id}/reject` (see below), `POST /api/jobs` (`target_area_id`). Job
payloads carry a `worktree` object (branch, base, status
`active/merging/merged/conflict/discarded`, merge_commit, error) and, after a
rejection, `rejected_reason`.

**Graph plans (slice 3):** the same machinery wired per job-in-plan. Legacy graph
plans retain their node-level repo/Ops placement. A delegated graph Recipe instead
inherits its Task's one exact Area for every node and rejects a Recipe whose explicit
repo target disagrees with that Area. With the flag on,
starting a plan with repo jobs pins their single code-area target to the job row and
cuts the worktree before the plan claims running (multi-area plans refuse to start -
Phase-1 is one worktree per plan); direct legacy plans run a node in the worktree only
when that node touches the repo, while delegated Tasks run every node in the selected
repo worktree or physical Ops Area. The plan's final
approve is the merge point. Flag off: target tags are inert metadata and plans run
exactly as before.

**Tool approvals during job/plan runs:** a non-Master job's hidden-session ACP
permission request is materialized as a global `permission_job` Attention row with
safe inline allow/deny actions and a Task deep-link; delivering either choice closes
the row. A Master-created Task follows its stored execution policy: Autonomous may
auto-approve ACP tool prompts, Guarded may not, and diff/plan review gates remain
owner-controlled product decisions.

**Review surface (slice 4):** the captain-facing half, following T4's ratified detail
language - the diff opens in an **expanding row** (a plan row's expanded body on the
Tasks screen) and on the **full-width task page** (`TaskWorkspace`); never a right
panel, never a modal. One shared component (`components/tasks/ChangesReview.tsx`)
fetches `GET /api/jobs/{id}/diff` and renders the per-file list (statuses in plain
words) plus the unified change; UI copy is de-jargonized ("isolated copy", "changes" -
git nouns stay in dev docs). Two verdict doors: **Approve & merge changes** invokes the
engine's approve (the slice-2 guarded merge; a conflict surfaces as a plain
needs-attention banner with the server's reason and the job parks in review for a
retry), and **Reject…** demands a one-line reason, then `POST /api/jobs/{id}/reject`
(either engine) marks the job `failed` with `jobs.rejected_reason` recorded and tears
the worktree down unmerged - the project never sees the discarded change. When the
diff has **no file changes** (agent stopped because the baseline already matched, or
the step made no edits), both the Tasks expand surface and the plan-canvas header door
reframe to **Accept & close** / **Reject & close** with copy that says the project stays
as it is - same approve endpoint, no fake "merge changes" promise. After a real
merge the row shows what landed (base branch + merge commit) and keeps the change
readable; a no-op close (merge sha equals base) keeps **Closed with no file changes**
wording instead. The plan canvas header Approve door shows a success notice and keeps a
durable landing line (plus push outcome when applicable) so a
reopened Done plan still says where the work landed. Slice 12's satpam consumes these
same review states.

### Repo-remote connector: BYO push-after-merge (Phase-1 slice 11, T9 - LIVE)

**Why:** the local-first merge is the default posture, but a repo that lives on
GitHub/GitLab/self-hosted eventually needs the merged work on its remote. T9 (captain
ratified) graduates the merge without changing the review model: push happens AFTER
the local approve+merge, and only for code areas that explicitly opted in.
PR-as-review-surface stays out of Phase 1 - the review surface remains singular,
in-app.
**How - BYO to the letter (standing decision #9):** every remote operation shells out
to the host's **own `git`** (`repo_remote.py`; non-interactive - terminal prompts and
ssh asking for input are disabled, so a push that cannot authenticate fails with git's
message instead of hanging). Proxima never brokers auth, stores no tokens, ships no
OAuth flow; if the host's git can push, the connector works. **Per-area opt-in,
auto-offered:** the container settings (Projects screen → Settings) list each code
area with its detected remote (prefer `origin`); an area WITH a remote gets a "push
after merge" toggle (`project_areas.push_on_merge`), **default OFF** - no remote, no
toggle, and nothing is ever pushed without the explicit opt-in. Enabling the toggle
**pins the remote URL** into `project_areas.push_remote_url` (audit F3): the push
refuses if the repo's own `.git/config` - writable by any agent working in the repo -
no longer matches the pinned URL (re-enable the toggle to approve a changed URL).
**Lifecycle:** when a repo job's (or plan's) final approve lands the local merge and
the target area's toggle is on, Proxima runs
`git -c credential.helper= -c core.hooksPath=/dev/null push <remote> <base_branch>`
from the area's repo - the `-c` overrides neutralize agent-planted credential helpers
and pre-push hooks, so nothing in repo config can execute in the API process at push
time (auth stays ambient ssh/BatchMode).
**Failure semantics:** a failed push (diverged remote, auth expiry, network) NEVER
un-merges - the job stays done-and-merged locally; the worktree row records
`push_status='failed'` plus the exact command + git's own output in `push_error`, and
the review surface shows a job-level blocker card with a **Retry push** action.
**GitHub-first, not GitHub-only:** plain `git push` covers any remote; when the remote
URL is GitHub the surfaced info is enriched with the parsed repo web link and whether
the host's `gh` is signed in (informational only - no hard `gh` dependency).
**Endpoints:** `GET /api/projects/{slug}/areas` + `/areas/detect` (each code area now
carries `push_on_merge` + its detected `remote`), `PATCH /api/projects/{slug}/areas/{id}`
(the toggle; enabling requires a detected remote), `POST /api/jobs/{id}/push` (retry,
either engine). Worktree payloads carry `push_status/push_error/push_remote/push_web_url`.

### Long work: timeout auto-continuation (Phase-1 slice 5, T5 - LIVE)

**Why:** A single agent turn is hard-capped by the turn quota. Before slice 5 a job
turn that hit the cap was killed and the job failed (or a goal silently stalled) even
though the work was mid-flight (T5). Long work must survive the per-turn cap.
**How:** when a **job run** (linear step or plan/graph node) hits the quota
(`asyncio.TimeoutError`), the worker salvages the streamed text as before, then
enqueues a **continuation run in the SAME session** - the persistent ACP session
carries the agent's full context - and, for repo jobs, the same worktree (cwd binds to
the job, so file edits persist). The continuation prompt is a **genuine resume**
("inspect the current state of your work and continue from where it stopped"), never a
re-brief. Graph nodes stay `running` and are re-attached to the continuation run
(guarded `running→running` run-id swap), so advancers accept its result as the same
attempt. The chain is capped at **`run_continuation_limit` (config, default 5) per
turn chain**; at the cap the stop is honest and loud: the run/job fails with a
plain-language reason (split the job or raise the quota) and a **plan pauses for
review** - a timed-out job never sits in limbo. Chains are durable on the run rows
(`runs.continued_from_run_id`, `runs.continuation_count`): slice 12's satpam reads a
high continuation count as a confused-agent signal and owns the restart-clean
decision - discarding a worktree is **never** an automatic timeout response.
Chat, goal-mode, collaboration, and review runs keep their pre-slice-5 timeout
behavior unchanged.
**Turn quota (first-class):** `run_timeout_seconds` is an **in-app setting**
(Settings → Agents → Turn quota, stored in `app_settings`, default 900s, bounds
60-7200s). Because it is DB-backed it takes effect on BOTH entrypoints -
`scripts/serve.py` and plain `uvicorn proxima_api.main:app` - and the env overrides
(`PROXIMA_RUN_TIMEOUT_SECONDS`, `PROXIMA_RUN_CONTINUATION_LIMIT`) are now mirrored on
both as fallback defaults. The plan slicer is instructed to size every job to fit ONE
turn quota - continuation is the safety net, not the plan.
**Endpoints:** `GET/PUT /api/settings/runs`.

### Deterministic script steps (Phase-1 slice 6, T6 - LIVE)

**Why:** repeated mechanical work (fetch, convert, check, publish) should not cost an
agent turn every time. A plan step that needs no judgment can be a saved script - fast,
free, and exactly reproducible (T6; ADR-0001's Phase-3 deterministic nodes pulled
forward in minimal form).
**How:** one new node kind, `script`, on the graph engine (not an n8n palette): the
node names a script inside the Container's physical **`ops/scripts/` folder** plus CLI args,
and executes as a **subprocess** - exec array, never a shell string - with the
Ops Area as cwd and a minimal environment (no server env). I/O contract: args
(`{{var}}` fills from the workflow input) + one JSON object on stdin
(`{"job_input": …, "upstream": […]}` - the graph engine's existing typed hand-off);
stdout is the node output, validated against the node's `output_kind`/`output_schema`
like any agent node; exit code decides success/failure. Script runs queue through the
ordinary runs table (`kind='wf_script_node'`), so they share the dispatch budget, turn
quota, heartbeats, and crash reaping - but never touch a runner/ACP, and never
auto-continue.
**Trust = content-hash binding (captain's decision):** a script's first run - or any
run after its bytes changed - blocks with a one-time approval ask (the plan pauses in
review; the node inspector shows **Approve script & run**). The approval card renders
the script's **actual content + sha256** (fetched together from
`GET .../nodes/{node_id}/script`), and the approve request echoes that hash back -
the server refuses with 409 if the file changed after review, so the owner approves
bytes, never a filename (audit F4). Approving records the sha256 in `script_trust`;
unchanged trusted scripts then run with **no per-run approval** - that is the whole
deterministic + free payoff. At run time the hashed bytes execute from a **private
temp copy** taken at hash time, so a concurrent edit of the project file after the
trust check cannot change what runs. Approvals and blocks are visible in the step's
timeline (`script.approval.required`, `script.trust.approved`) and the audit log.
When the external maintenance boundary is configured, the script executes inside
the shared PID containment and retains ingress admission until its namespace has
exited, including detached descendants.
**Reuse awareness:** agents write and maintain the scripts as ordinary job output,
each starting with a header comment block (`# Description:` / `# Inputs:` /
`# Outputs:`). Proxima auto-scans `scripts/` into a catalog (name + one-line
description) injected into every project run preamble alongside the wiki catalog,
with the instruction to prefer reusing/extending an existing script. The plan slicer
is given the same catalog and may emit script jobs - but only for scripts that exist.
**UI:** script nodes render distinctly (dotted outline, `⚡ scripts/<command>` in the
mono face, last output line on the card and Tasks list row); the canvas has a
**+ Script** tool and the inspector edits command/args/contract/gate.
**Endpoints:** `GET /api/graph/jobs/{id}/nodes/{node_id}/script` (content + sha256
for the approval card), `POST /api/graph/jobs/{id}/nodes/{node_id}/approve-script`
(body carries `expected_sha256`; 409 on mismatch).

### Satpam supervision loop (Phase-1 slice 12, T10 - LIVE)

**Why:** the heartbeat/reaper catches DEAD runs; nothing caught an agent that is alive
but unproductive - burning continuation turns while producing no repo change, repeating
itself, or guessing at a decision that belongs to the owner (T10; firstmate/watcher
supervision patterns ported to Proxima's primitives, captain ratified).
**How - ONE fleet-level loop** (`satpam.py`, a sibling of `RunReaper` on the worker
loop's cadence, self-paced by its Settings interval): each sweep evaluates every
running job's active **continuation chain** (slice 5's `continuation_count` is the
turn boundary - one evaluation per continuation turn, not per token) against DURABLE
signals only - DB rows and worktree diff signatures, never the agent's stream, and
never an LLM call. Fail-quiet by contract: a supervision error is logged and swallowed,
never crashing the worker or a run.
**Detection ladder:** *dead* - heartbeat/reaper, unchanged (the satpam consumes its
outcome); *stalled* - a repo chain whose continuation turns leave the worktree
signature (`worktrees.work_signature`: branch head + uncommitted status + tracked diff
+ untracked stats) unchanged N turns in a row; *looping* - consecutive turns whose
salvaged output + repo state hash identically N turns in a row; *confused* - the
continuation cap (slice 5) or repeated output-contract validation failure
(`node_states.contract_failures`, second strike escalates).
**Action ladder (automation line, captain ratified):** (a) **steer** - one corrective
prompt into the job's next continuation turn (amended in place on the queued run, or
held in `satpam_watch.steer_pending` for the next one): AUTOMATIC, logged; (b)
**restart-clean** - cancel the stuck attempt and re-run fresh (a new node session for
plans; step one with fresh context for classic tasks): AUTOMATIC only for NON-repo
work; for repo work it is a **pending approval card** in Tasks (restart discards the
worktree - destructive, owner-gated; approve re-cuts fresh from the repo's current
HEAD and re-runs the plan's repo slice); (c) **pause + escalate** - the chain is
cancelled and the job parks in review with a plain-language what-happened record.
Steer→restart→escalate is per stuck episode; a restart that did not help escalates
rather than thrashing.
**Decision-hold (T10 #4):** the node prompt defines a structured output-contract
marker - a reply starting **`DECISION_NEEDED: <question>`** - for a genuine open owner
decision. The node parks in the existing `review` state with the question on
`node_states.question`; its dependents hold on their own (their dependency never
reaches `done`) while **independent DAG branches keep dispatching**; only when they
drain does the plan park. Answering (`…/answer`, usable while the plan RUNS) stores
`node_states.answer` and re-runs the node with the decision in its prompt.
**No silent interventions (T10 #5):** every action is a `satpam_interventions` row
(shown as the task's "Watchdog log" in TaskWorkspace and on the Recipes canvas, with
the pending-restart approval card on top; each log row has a spaced accessible name
so detection + reason do not smash together) plus a `satpam.*` timeline event
(`satpam.steered` / `satpam.restart.queued` / `satpam.restarted` / `satpam.escalated`).
Thresholds are a Settings panel (Agents → Watchdog): N no-progress turns (default 2)
and the sweep cadence (default 60s), bounds-checked in `app_settings`.
For Master-owned Tasks, the same intervention id is projected once into the durable
Master thread and its existing session SSE stream. A failed approved repo restart
leaves the original pending gate intact, materializes one
`satpam_recovery_failed` Attention row keyed by intervention id, and projects one
failure event even if delivery or approval is retried. Master adds no detector,
restart, or escalation loop of its own.
**Endpoints:** `GET/PUT /api/settings/satpam`,
`POST /api/jobs/{id}/satpam/{intervention_id}/approve|dismiss`,
`POST /api/graph/jobs/{id}/nodes/{node_id}/answer`.

## 9. Schedules (cron)

**Why:** Recurring agents — daily report, watch-and-summarize — while you sleep.
**How:** `schedules` table + a 60s scheduler loop that materializes only *due* jobs
(own 5-field cron matcher; overlap policy skip/allow). Failed step fails the job.
**Run now** fires a schedule on demand and opens the task it spawned, so the owner can
prove a schedule before leaving it to fire unattended. It reuses the tick's own
`_spawn_scheduled_job`, so it exercises the real cron target (workflow, project,
profile, stored input, and repo worktree binding) instead of a lookalike; it passes no
minute key, so a manual run cannot claim — and thereby swallow — the scheduler's slot
for that minute. It works on a disabled schedule (`enabled` gates the tick, and trying a
schedule out is exactly when it is still off) and reports an overlap skip as a 409 rather
than silently no-op'ing.
**Endpoints:** `POST/GET/PATCH/DELETE /api/schedules[...]`, `POST /api/schedules/{id}/run`.

## 10. Projects (workspaces)

**Why:** Scope agents to a folder - your real code, not a sandbox.
**How:** `projects` table. Three ways in: (1) scaffold a project under the data dir
(`POST /api/projects`), (2) **link an existing folder** on disk, or (3) **create a new
empty folder** under a browsable parent and register it - both (2) and (3) go through
`POST /api/projects/link` (jailed to configured link roots; (3) sets `mkdir: true`).
Chat/terminal/files all operate on the project path. The active Work project is set from
the Work-sidebar switcher. Management UI is a card grid under **Settings → Projects**
(one card per project: select, Rename, remove), with add flows behind one **Add project**
modal - a project holds a name and a slug, which does not earn a detail panel. The shared
`FolderLinker` component covers link + create-on-disk (mode toggle). Removal
distinguishes what the API actually does: a linked/created-on-disk folder is unlinked
and its real files stay; a Proxima-scaffolded project is deleted from disk. On
**first run**, right after setting a password, an onboarding step
(`screens/WorkspaceOnboarding.tsx`, reusing `FolderLinker`) offers link, create-new-folder,
or skip (starter project under the data dir). The two folder choices are mutually
exclusive pressed buttons with ordinary Tab traversal and Enter/Space activation.
Validation exposes one assertive alert, marks the corrective target invalid, and returns
focus there before the alert is published. The alert is the only semantic announcement
owner, so the focused target does not duplicate the message as its description. Every
invalid submission mounts a fresh single alert, including an unchanged repeat while the
target remains focused.
If the initial folder listing is unavailable, the chooser renders and focuses a marked
retry control before publishing the same single alert; keyboard retry keeps that control
as the correction target until a readable folder is selected.
Display names are checked against the API's 120-character limit before submission. The
project-link error contract distinguishes the selected `path`/`parent`, child `folder`,
and display `name`/`slug` targets. Parent and link-path failures focus a selected-folder
refresh control, child-name failures focus the folder field, and name/slug failures
focus the display-name field. Browsing selects the nearest actually readable ancestor
inside the configured root, skips self-referential or mutual symlink cycles, and never
uses an unresolved path for containment. Every configured root keeps its raw identity
plus optional lexical and resolved identities, so one unexpandable root does not disable
valid siblings and a retained selection stays bound to its original owning root. Later
resolution failure cannot fall back to a containing root. Each browse response carries
an opaque configured-root ID, and every later navigation plus link/create request
must send that ID back. Canonical paths from symlink-root aliases therefore retain their
original owner; any later request without an ID fails closed. If no allowed ancestor
is readable, the chooser retains its selection and explicit invalid state instead of
reporting an empty success.
New folder names are validated against the target filesystem's encoded component-byte
limit. The API opens the verified allowed root and each parent component separately,
using no-follow directory descriptors on POSIX and native no-reparse directory handles
on Windows. Creation starts under an unguessable staging name relative to the retained
parent, pins the created directory's platform identity, and atomically publishes it
without replacing an existing entry. The expected identity is stored with the Project
and rechecked through the configured root before success. Rollback removes only that
pinned directory, even if it was renamed, and never removes a replacement. Component
and encoding failures stay owned by the folder field, while parent traversal, identity,
and location failures remain owned by the selected-folder control. Later Container
filesystem resolution also compares the stored identity and rejects path replacement.
Startup backfills readable legacy Project rows with their current platform identity;
an unreachable legacy path receives a fail-closed unavailable marker instead of silently
opting out of later identity checks.
**Endpoints:** `GET/POST /api/projects`, `/projects/link` (`mkdir` optional, `root_id` required),
`GET /api/fs/dirs` (`root_id` required once a path is selected), `PATCH/DELETE /api/projects/{slug}`.

### Container Areas and physical Ops storage

**Why:** A Container is not a repo. It holds zero or more **repo Areas** plus exactly
one **Ops Area** for durable non-code work. A repo Area may be a nested folder or `.`
when the Container root is itself a repo. The Ops Area is physically rooted at
`ops/`, so Ops-owned files cannot be confused with a sibling repo.

**How:** The compatibility `projects` and `project_areas` tables remain the storage
and foreign-key truth. New Containers create `ops/`, `ops/container.md`, and exactly
one active Ops row with `rel_path='ops'`. `container_registry.py` is the canonical
resolver for Container, Area, and Ops roots. It validates realpath containment and
rejects path traversal, duplicate roots, unsafe overlaps, and Container-or-Ops-root
symlinks on every resolution; the recursive scan that rejects every symlink under the
physical Ops root is opt-in (`deep_ops_scan`) and enforced fail-closed at Ops
creation, migration, Area mutation, and Area-sensitive execution, keeping project
lists and Home O(1) while per-access realpath jailing still blocks symlink escapes.
A repo at `.` is the one intentional containment case; its local git exclude keeps
`/ops/` out of that repo.

Existing Containers whose Ops row is `.` migrate at startup. The migration first
builds and hashes a dry-run manifest, then uses atomic same-filesystem moves for only
known Ops-owned paths. Its durable marker resumes safely after interruption. Any
collision, changed content, unsupported file type, or ambiguity stops only that
Container, opens an owner-visible Attention item, and leaves the legacy row active.
All Ops consumers resolve through the row, so Archive, Wiki, artifacts, Designs,
scripts, reports, exports, and uploads continue to use root-level legacy paths until
that Container migrates cleanly. `container_registry` caches the bounded identity and
summary projection from `ops/container.md`, together with its full source hash,
indexed timestamp, and last known activity. Identity is deliberately free text and
summary is capped at 500 characters. A file API write refreshes the row immediately;
a five-second background cycle deterministically catches direct edits. Refresh work
is outside the request path.

Repo identification remains hybrid: `project_areas.py` auto-detects `.git` folders
at bounded depth and supports manual overrides and excluded tombstones. Project
payloads retain the compatibility `code_areas` and `ops_area` fields.

The Container-facing Fleet API joins the registry with Live state directly in
SQLite: running and queued Task counts, open Attention count, last activity, Area
inventory, and current registry, Area, and Ops migration health. The Fleet health
field remains `null` and never reads graph files; gated graph state is available
through the separate scoped graph endpoint. The Fleet list uses one SQLite statement
for any Fleet size and performs no per-Container filesystem scan.
Container detail is owner-scoped, and its Areas endpoint applies the canonical
Container boundary validation before returning target choices.

Existing `/api/projects` readers are a one-release compatibility alias. They reuse
the Fleet and Area query functions while preserving the historical payload consumed
by current Master and frontend surfaces. Internal tables and foreign keys keep
`projects` and `project_id`; public schemas, new routes, and frontend boundary types
use Container terminology.

**Endpoints:** `GET /api/containers`, `GET /api/containers/{slug}`,
`GET /api/containers/{slug}/areas`, compatibility `GET /api/projects` and
`GET /api/projects/{slug}`, plus `GET/POST /api/projects/{slug}/areas`,
`DELETE /api/projects/{slug}/areas/{area_id}`, `POST /api/projects/{slug}/areas/detect`.

## 11. Files & uploads (APIs)

**Why:** Read/write project files safely from every surface that needs them.
**How:** Tree + file read/write (CodeMirror editor), HTML/MD preview, mkdir/rename/delete,
chunk-streamed file upload with collision-safe naming and a configurable 100 MB default
limit, plus an authenticated raw/preview
route (for images and embedded previews). A separate bounded, path-only reference index
powers `@` autocomplete without returning file contents; produced artifacts from the
project artifact scan are merged into the same picker on the client.
Historical virtual paths such as `wiki/...`, `artifacts/...`, `scripts/...`, and
`uploads/...` remain stable at the API boundary. The server maps those paths to the
canonical Ops root, while repo files continue to resolve from the Container root.
These APIs power the **Files tool** on the right rail (the project tree + inline
editor as an overlay panel, any context), the **Archive**'s record viewer
view, the **Wiki** tree under Settings → Knowledge, chat attachments, and `@`
file/artifact references — with the in-browser **Terminal** as the raw escape hatch.
Inline New file / New folder / Rename rows share one tree input with an accessible
name (`New file name`, `New folder name`, or `Rename <entry>`) and a create
placeholder (`file-name` / `folder-name`) so the empty field is not a dead unlabeled
box — Enter commits, Escape or empty blur cancels.
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/reference-files`, `/artifacts`, `/api/preview/{slug}/{path}`.

## 12. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app — from the **Preview**
tool on the right rail, from an app-type artifact, or from the recipe test bench.
**How:** `AppManager` runs one owner-confirmed dev process per project with a filtered
environment. The preview must be served root-relative on its own origin (SPA HTML uses
absolute asset paths and HMR opens a WebSocket to the page origin), so each vantage gets
one: local and remote preview use the app's **preview relay port** on the Proxima host
(reported as `preview_port` in app status; bind interface via
`PROXIMA_PREVIEW_BIND`, default `auto` = the same port on loopback plus the tailnet
interface when present, otherwise loopback only, never `0.0.0.0`; `off` disables) or,
with an apps domain configured, the
`preview-<slug>.<apps_domain>` subdomain. Relay and subdomain proxy share one engine:
HTTP + WebSocket forwarding, Host rewritten to the local dev port (Vite-style
allowed-host checks pass), gated by a short-lived preview-only cookie — never the owner
API token. Authentication completes before target resolution or procfs scanning, and
the proxies strip cookies/auth before forwarding and strip upstream
`Set-Cookie`. Same-origin fallback and generated HTML use an opaque iframe sandbox.
This is credential-leak mitigation, not OS/container isolation; the command still runs
as the Proxima service user. The relay only guards its own port: detected-app
suggestions bind `127.0.0.1`, `HOST=127.0.0.1` is defaulted into the dev-server env,
and app status reports `broad_bind` (surfaced as a UI warning) when the dev server is
found listening beyond loopback - that port is LAN/tailnet-reachable with no auth.
The selected port is only a candidate. App status uses the structured
`stopped | starting | ready | port_conflict | ownership_unknown | exited` contract,
and appview, relay, and preview-subdomain paths open a connection and verify its
server-side socket before sending HTTP or WebSocket bytes. A pre-existing listener
returns a structured
conflict without stopping or signaling it. Linux procfs verification also closes the
post-preflight bind race: if an unrelated listener wins, the managed command is
signaled by its recorded process group, the foreign listener remains untouched, and
the conflict stays visible with logs, Stop, retry, and change-port actions. Unavailable
procfs evidence and uncontained detached descendants fail closed as
`ownership_unknown`. For a contained launch, every socket owner must carry the
launch marker and match the exact launch-specific PID namespace reported by
Bubblewrap, and retain positive live process-group or ancestry evidence to the
managed leader. Marker plus namespace without live lineage never grants
authority. The managed process and stdout are registered immediately after
spawn while containment proof completes asynchronously, and preview stays fail
closed until that proof is available. Cancellation cleanup is registered in a
manager-owned task before the request returns and is reconciled at shutdown, so
repeated request cancellation cannot abandon the provisional process. Each project
has a monotonic lifecycle generation. An immediate retry waits for cancellation
cleanup of the prior generation, and cleanup can mutate only its own generation.
A start with no listener after 15 seconds shows an actionable prolonged-start warning
with Stop and logs instead of an infinite spinner. When a command self-exits (short
script, crash, or non-server entry point), status keeps
a sticky `exited` + `exit_code` payload across polls so Run & Preview can show Finished
vs Failed with the log and a next-step hint instead of a silent bare dump. Logs remain
toggleable in stopped, starting, ready, conflict, ownership-unknown, and exited states.
The existing bounded 40-line status buffer survives preview Reload and explicit Stop,
so stopped/retry feedback shows the most recent command output, including terminal
shutdown lines drained before the stopped snapshot. The exited relay
returns HTTP 503 until Stop or the next start releases or replaces that listener.
A launch-time supervisor starts the app and owns stdout, keeps the complete-line ring
and partial-line byte tail bounded, and drains all currently available bytes before
returning an atomic final snapshot. Routine polling transfers only versioned line
deltas. If a detached child keeps stdout open, the supervisor continues fixed-size
reads until EOF after the API disconnects and stays alive until its managed app cgroup
is empty. Packaged Linux installs give each app a
delegated, launch-specific cgroup beneath its profile-specific socket-activated
supervisor outside the API cgroup. Broker unit teardown targets only the broker
process, while Stop may signal processes still proven inside the exact app cgroup.
Production and staging use separate sockets, protocol identities, state roots, and
checkout executables. A durable pending generation exists before supervisor creation,
then atomically gains broker and app identity. Startup and shutdown reconcile project
generations concurrently under aggregate deadlines. A restarted API adopts only exact
durable supervisor, process, app cgroup, profile, protocol, and lineage evidence;
anything incomplete remains ownership-unknown and is not signaled. Stop on an
unadopted scope attempts authenticated reconnect/cleanup when durable evidence
allows it; otherwise it returns a non-success recoverable ownership-unknown
result and keeps blocking replacement generations until that scope resolves.
Start hands the ingress effect lease to `AppManager` and keeps it held through
cancel or failed-spawn cleanup until terminal authenticated disposal. Unit upgrades scan
same-user procfs first and refuse while an older protocol process or a pre-protocol
preview identified by API lineage or service-cgroup membership remains live.
Supported Windows hosts use detached breakaway supervisors. If durable ownership is
unavailable, start fails before app spawn with a recoverable
`output_sink_unavailable` stopped state. Stop retains the last available log, and a
later supervisor disconnect preserves fail-closed authority.

**Endpoints:** `/api/projects/{slug}/app/start|stop|status`, `/apps`.

## 13. Image generation and Design Studio

**Active:** image generation remains available through `/image` (alias `/gambar`).
It uses the image provider selected in Settings, saves output under
`artifacts/media/images/`, returns the artifact in the originating chat, and feeds the
same durable Archive registry as agent runs (so Image type filters and records list
new media, not only the chat result card / fallback viewer). Chat Created-outputs
cards expose a spaced `Open Image, <title>` accessible name. Images
attached to the message or explicitly selected through `@` (rendered as
`![name](path)` markdown by the composer) are used
as reference/source images when the selected provider advertises `imageEdit` — the
first attachment is the primary source and the rest are passed as `extra_images` when
the provider also supports `referenceImages`; the reference markdown is stripped from
the prompt so the model gets clean instructions. If the provider is text-to-image only,
the attachments are ignored and the reply says so. Existing image and media files remain
readable through the normal artifact/file surfaces.

**Clarify-on-thin-brief:** when a `/image` or `/design` command carries almost no
direction (no attached image and fewer than 3 words after the command), the backend does
NOT generate/draft something generic — it replies in the same chat with a compact
`<question-form>` (image: subject/style/aspect; design: goal/format/audience/mood/copy).
The form carries a `submit-as` attribute, so answering re-issues the original command with
the answers as an enriched brief, and the same media path runs again — now with enough to
act on. A brief that already has ≥3 words (or an attached image) skips the form and runs
immediately. Implemented in `routes/chat.py` (`_media_brief_is_thin`, `_MEDIA_BRIEF_FORMS`,
`_complete_media_ask`); the frontend prepends `submit-as` on submit
(`questionForm.ts` / `QuestionForm.tsx`).

These synchronous media completions (the clarify form and design draft cards)
finish inside the POST and emit their run events before the client can subscribe to the
stream, so `ChatScreen` treats a `status: "completed"` create-run response specially: it
loads the assistant reply directly instead of waiting on the stream — otherwise the
composer would sit stuck on the "Simmering…" thinking indicator.

**Design Studio (active, server-gated):** an AI-assisted canvas where the agent
drafts **editable layered scenes** (text stays real text) and the human refines them
directly. The Design home is sparse by default (title, one lead, tooltip chips,
**How it works** for the fuller path) so the brief/Generate control stays primary;
it does not dump the project display name (shell switcher). The home takes a brief
(Graphic / Slide deck / Mobile app / Website) or a size template (Instagram
post/story/carousel, X post, poster, …) and opens a linked **design session**: the
agent replies with a `<design-scene>` block the Konva canvas applies live. The studio offers select/move/resize with a full inspector
(text, fonts, fills/gradients, artboard presets), Layers/Assets panels, a
selection-aware chat, undo/redo + version history, multi-image reference inputs, an
eyedropper, a per-project brand guide (`design.md`, generatable from reference
URLs/images), a per-project Moodboard for curated URL previews and screenshots,
and Export (PNG/JPG/PDF/HTML). Moodboard cards support notes, tags, search,
tag filtering, edit/delete/open actions, paste/drag/upload input, and a
**Use as reference** selection. Selected card metadata enters the existing
design-run preamble and local cached OG images/screenshots are attached as vision;
preview-fetch failures save graceful fallback cards instead of breaking the board.
Moodboard data lives under `artifacts/moodboard/` and is isolated by project. On
desktop the left rail (Chat /
Assets / Layers) and the right inspector are drag-resizable (same `useDragWidth`
pattern as Workflows Plan Chat / node inspector); widths persist in
`localStorage` (`proxima.design.leftWidth`, `proxima.design.inspectorWidth`) and
handles hide when a panel is collapsed. Mobile keeps bottom sheets only. Scenes
persist at `artifacts/design/<id>/scene.json` and appear as design records in the
Archive.
The optional project component library (`artifacts/design/_components.json`) is
loaded only when the design root listing already contains that file, so a fresh
project does not probe a missing path. Zoom/Fit and Layers-panel rows expose
explicit accessible names (and keyboard activation on layer rows) so symbol-only
controls are not just symbols. See [DESIGN-STUDIO.md](DESIGN-STUDIO.md) for the
full contract.

The server-owned flag `PROXIMA_FEATURE_DESIGN_STUDIO` gates it: on by default,
with `proxima.env` as the owner opt-out (read at boot).
`GET /api/config` publishes the effective flag. When disabled, the frontend omits its
navigation, deep links, commands, settings, provider health checks, artifact bridge
actions, and agent guidance, and backend guards return HTTP 503 with the
`feature_disabled` payload before message creation, database writes, provider calls,
file writes, subprocesses, or collaboration dispatch.
Video Studio, editable video projects, and the `/video` generation surface were removed;
ordinary video files remain readable and playable as generic artifacts.

## 14. Archive: durable deliverable registry (Phase-1 slice 8, T4 - LIVE)

**Why:** deliverables used to exist only as a capped (~40 item) mtime scan - no
memory, no approval state, no trace of which job produced a file. The Archive is now
a **registry, not a scanner**: the scanner discovers files, the registry remembers
them as durable records that survive file moves and deletion (a missing file flips
`file_missing` on the record; the record stays).

**How:** every finished run (agent, script, or chat media such as `/image` and
Design Studio drafts) feeds its output links into
`artifact_records` (module `artifact_registry.py`): one row per deliverable
**version** with name, type (scanner types + `script-output` for generic files a
script step produced), project + path, size, produced date, lineage
(session → job/node → run), approval status, and a version chain. Identity is
(project, type, path): a new producer at the same identity creates v(n+1) and
automatically marks prior versions superseded; re-scans by the same run (or later
steps of the same still-draft job) refresh in place, so feeding is idempotent.
Migration 23 seeds the registry from the current scanner output so existing
projects' artifacts appear as draft records on upgrade.

**Approval is ONE status with two doors (synced):** `draft / review / approved /
superseded`. Approving a job in its Tasks review auto-approves the records that job
produced; the Archive page edits the SAME field for the late/batch/supersede cases.
Never two separate approval states.

**Registry queries replace the item cap:** paginated newest-first list with
project/type/status/date filters, text search, and facet counts; each record has a
permanent per-project address (`/api/archive/{project}/{slug}` — the UI's
`#archive/<project>/<slug>` permalink) with same-path version history plus
project-level newer/older record navigation (by produce date, not version).
Inline and full-record previews cover docs (markdown), pages (HTML iframe),
images/video, and **designs** (first artboard via the same `MiniPreview` thumb as
Design gallery, loaded from `scene.json`). Apps and other types still point at Open.
Chat result cards and the iterate Result view keep using the live scan
(`GET /api/projects/{slug}/artifacts`, unchanged).

**Native rich review (ArtifactViewer v2):** opening an ordinary artifact keeps the
existing image, video, PDF, Markdown, HTML, JSON, CSV, and text renderers, but wraps
them in one review workspace. The owner can pin numbered notes directly onto the
rendered artifact, add overall feedback, and choose **Add feedback to chat**. Review
notes are browser-local until that action; Proxima then opens the artifact's producing
chat session and places an editable, path-linked review brief in the normal composer.
Sending it uses the existing chat/run flow, so there is no Lavish poll, external URL,
or second feedback service in the happy path. Unknown, binary, and directory-like
paths immediately show the unsupported preview with a download action instead of an
indefinite loading state.

Mermaid fences in Markdown and standalone `.mmd` / `.mermaid` artifacts render as
rich diagrams. **Edit as whiteboard** converts supported flowchart, sequence, class,
ER, and state diagrams to native editable Excalidraw elements without leaving
Proxima. The owner explicitly saves the scene under `artifacts/whiteboards/*.excalidraw`;
that project-relative path is included in chat feedback so the same agent can inspect
both the source artifact and the edited board. If Mermaid source changes after a saved
board exists, the viewer asks whether to keep edits or rebuild from current source.
This is ArtifactViewer functionality, not a Design Studio canvas path.

**Endpoints:** `GET /api/archive`, `GET /api/archive/{slug}/{record_slug}`,
`POST /api/archive/records/{record_id}/status`.

## 15. Wiki + memory (knowledge)

**Why:** Per-project + global knowledge that compounds across sessions.
**How:** Markdown files under each project's `wiki/`; a built index + tree; global
aggregation. Fed by Chat→Wiki (§5). Opened from **Settings → Knowledge**
(Files / Graph / Search). Preview renders `[[wikilinks]]`; existing targets open
the note, and **missing (red) links create the note on click** (title heading stub,
open in edit beside the current note when nested) so owners are never stuck on a
dead link.
**Endpoints:** `/api/projects/{slug}/wiki/all`, `/api/wiki/all`, `/tree`, `/file`, `/fs/*`.

## 16. Terminal

**Why:** A real shell in the cockpit, scoped to the project.
**How:** `terminal.py` over `WS /api/ws/terminal`. Opened from the **Terminal** tool
on the right rail; once opened it stays mounted so shells survive closing the panel.

## 17. Command palette (quick commands)

**Why:** A catalog of quick slash-style commands runnable from chat.
**How:** `commands.py` — `command_catalog()` lists built-ins plus **enabled skills**
for the active profile as skill agent turns; `execute_command()` runs one. MCP is
not listed. Detection cache backs skill entries; rescan refreshes after installs.
**Endpoints:** `GET /api/commands/catalog`, `POST /api/commands/execute`.

> Note: an earlier *advisory command-policy classifier* (`POST /api/policy/command/check`)
> was **removed** — it never gated real agent/tool execution (the agent runs its own
> shell inside the runner CLI, not through this API), so it gave a false impression of a
> guard. The real access boundary is network reachability (single-user). See
> [security-boundaries.md](security-boundaries.md).

## 18. New task launcher + search

**Why:** Delegate a one-off task, and see what needs you. The app itself lands on
**Chat** — the launcher opens from the Tasks screen's `+ New task`. This standalone
launcher is the feature-off compatibility path; when Master is enabled, `+ New task`
seeds the shared Master composer on the full-page Master home instead (see the Shell
data flow in [architecture.md](reference/architecture.md)).
**How:** The launcher is deliberately minimal — a greeting, the **Task Composer**
(project + agent + Guarded/Autonomous policy), and an **attention strip** when
jobs are waiting in review (jump to the first, or open Tasks). **Start task**
creates the durable job then always opens its hash-addressable task workspace
(errors stay on the launcher with the brief restored); the composer re-arms its
mounted flag on each mount so React Strict Mode cleanup cannot swallow that
navigation or the failure alert. It polls
`GET /api/dashboard` every 5s; the dashboard payload also carries `authHealth` —
cached background checks (`auth_health.py`, 60s TTL, never on the request path)
of the selected image provider plus every runner referenced by a profile —
though the current Home renders only the review-attention data. Global **Search**
(magnifier in the top bar / Ctrl+K) covers chats, messages, projects, and designs.
Chat hits include `mode` + project so a Design Studio session (excluded from the
main chat list) opens in Studio on click instead of silently closing the modal;
ordinary chats still open in Chat and switch project when needed. Search visibility
is declared by the session-kind registry: user-facing Chat and Design content is
searchable, while Master's hidden system thread is not. This keeps structured Master
product-tool calls and tool-result payloads out of the owner-facing search surface.
The search field is labeled for assistive tech, and each result exposes a short
spaced `aria-label` (title · project / role · snippet) so screen readers do not hear
full markdown bodies.
**Endpoints:** `GET /api/dashboard`, `/api/runs/active`, `GET /api/search`.

## 18b. Account preferences (password, appearance, notifications)

**Why:** Owner security and desktop alerts without babysitting a tab.
**How:** Settings → Account. Password change fields are named and
labeled (with a visually hidden username for password-manager heuristics). Desktop
notifications use the browser Notification API while the tab is backgrounded; the
toggle shows **On** / **Off**, or **Blocked** with an alert when the browser has
denied permission for this site (so a click never fails silently). Re-enable by
allowing notifications in the browser site settings, then try the toggle again.
The left Settings section list is title-only under group eyebrows (Work setup ·
Integrations · System · Help); full section hints live on the `title` tooltip and
spaced accessible names (`Account. Account, appearance and notifications`) so
title+hint never smash. The active section keeps `aria-current="page"`. Theme
swatches are a labeled group with `aria-pressed` and `Sunset, selected`-style
names; the font-size slider reports its value in pixels. Agent permissions uses a
pressed toggle with an explicit On/Ask label.

## 19. Audit log

**Why:** An activity trail of meaningful actions.
**How:** Settings → Diagnostics → Audit log opens a filterable modal of recent
entries (`GET /api/audit`). Master job creation, budget/toggle changes, and
checkpoint/turn restores produce explicit `master.*` / `chat.turn.restore` entries;
pure Master reads do not. File/tree/app actions stay project-scoped with a
`path` metadata field; settings tests and saves (image generation, Higgsfield)
use `target_type=settings` and store flat JSON metadata (provider, status, …) —
never a double-encoded string under `path`. The modal pretty-prints metadata
(`provider: codex · status: ok`) and still unwraps older double-encoded rows.
**Endpoints:** `GET /api/audit`. (Roles/users management removed in single-user.)

## 19b. Diagnostics debug logs

**Why:** Owners need the service journal plus stuck-run state without SSHing to the host.
**How:** Settings → Diagnostics → Debug logs calls `GET /api/debug/logs`, which runs
`journalctl --user -u <unit>` for the configured systemd unit (`PROXIMA_SERVICE_NAME`,
default `proxima` → `proxima.service`), and lists active/stale runs and orphaned jobs.
Empty journals return a `logHint` naming the unit and how to point `PROXIMA_SERVICE_NAME`
at staging/preview units; the panel head uses correct singular/plural line counts and
shows the unit under the description.
**Endpoints:** `GET /api/debug/logs`, `POST /api/debug/reap-orphaned-jobs`.

## 20. Reliability (cross-cutting)

Heartbeat/reaper for hung runs, the satpam supervision loop for alive-but-unproductive
ones (see §8 Satpam), per-session serialization, graceful shutdown, output
salvage, orphaned-run cleanup, a per-turn quota (`run_timeout_seconds` — an in-app
setting, default 900s; see §8 Long work) with cancel-on-timeout plus capped
auto-continuation for job runs, and daily DB backup (`proxima-backup` timer with
`VACUUM INTO`). Setup failures are finalized immediately instead of waiting for the
reaper. Run completion is status-guarded: cancellation cannot be overwritten by a late
media result, message-review result, collaboration synthesis, draft, or graph update.

## 21. Updates (version check + disabled safe-update fixtures)

**Why:** Owners should see release availability without letting the running
application or candidate code promote itself.
**How:** `UpdateManager` may check GitHub release metadata every 6h, but its old
live-checkout apply route and `proxima update` are inert. The safe-update request
and run-status projections are behind the server-enforced
`feature_safe_self_update` flag, default off in both production entrypoints. A
managed external updater, not candidate code or the app database, owns the
append-only fsynced journal, native single-flight lock, immutable releases, release
pointers, maintenance fence, backups, service configuration, and recovery verdict.
Signed release manifests bind an exact regular-file set. Unsigned local provenance
also binds normalized file modes and safe in-tree file-symlink targets, which are
materialized as regular files. Both paths bind the canonical Python/web lockfiles
and produce an immutable verified-file-set result. Publication consumes that result
rather than deriving new trust from mutable candidate bytes, copies through pinned
directory descriptors into fresh controller-owned inodes, rechecks trusted staging,
and then renames it atomically. Directory creation fsyncs every new directory and
its parent; unsupported durability or pinned traversal fails closed. The app exposes
only authenticated owner projections and a package-local read-only maintenance
client. Fence status is nonsecret and controller-owned with read-only application
access. Submission consults the external single-flight authority before reconciling
SQLite projections. systemd, launchd, and unmanaged adapters fail closed until the
[adapter qualification matrix](adding-safe-updater-adapter.md), candidate proof,
and rollback fault testing pass. The authority decision is recorded in
[ADR-0008](adr/0008-external-safe-update-authority.md).

The update modal treats external recovery evidence as authoritative: timeout and
failure states do not claim that either release is healthy, and manual promotion
remains unavailable.

Group 15 adds a pre-switch gate only. Controller-owned code copies verified source
and the offline cache into disposable candidate storage, then runs every fixed
build, test, type, documentation, migration, server, and browser command inside a
Bubblewrap user, mount, PID, and network namespace with bounded resources, output,
time, and process cleanup. Only candidate-local writable mounts are visible. The
exact post-build tree is rehashed, published into fresh release inodes, and frozen
before probing. SQLite's backup API creates the raw clone; a fixed sandbox command
migrates only its dedicated clone directory, and the controller requires the
complete `schema_migrations` ledger through its policy-pinned expected version.
The browser fixture is a fresh copy of migrated schema with synthetic owner,
session, and project rows only, plus separate workspace and runner-home paths.
Candidate-mode startup refuses migrations and background writers. The separately
installed, hash-pinned probe suite starts the frozen release and requires API
identity, version, authenticated maintenance, SSE, served-static-asset, complete
asset-manifest, and headless-browser scenario results. The Master scenario asserts
the modal popup and Home bridge, labeled controls, dynamic runner eligibility, and
the absence of any enabled unqualified runner against a trusted fixture. The
[safe-update boundary](security-boundaries.md#safe-update-boundary) owns the
fixture's isolation and refusal requirements. Evidence includes fixed
build logs, migration proof, fixture metadata, assets, and probe results; its files
and directories are frozen, and recovery rehashes the journal-pinned bundle before
reporting a run safe.

Group 16 adds a disabled disposable-fixture A/B transaction harness. It accepts
only the in-memory disposable service adapter, an explicitly initialized temporary
fixture root, canonical `status/fence.json`, and disjoint role-confined live and
staged database paths. The harness exercises fencing and ingress drain, service
stop and restart, WAL/SHM handling, sealed database images, both-pointer rollback,
read-only and writable probes, process-lifetime containment, and interruption
recovery. The detailed sequence is owned by the [architecture
flow](reference/architecture.md#9-update-check-and-candidate-gate-plus-disabled-switch-fixture);
the rollback, breaker, and maintenance invariants are owned by the [safe-update
security boundary](security-boundaries.md#safe-update-boundary). The harness cannot
enroll an updater, control a real service, switch a live release, replace live data,
or remove a production fence. Activation remains unavailable.

**Endpoints:** `GET /api/update/status`, `POST /api/update/check`,
`POST /api/update/apply` (inert),
`GET /api/maintenance`, `GET /api/self-updates/capability`, `POST /api/self-updates`,
`GET /api/self-updates/{id}`, `POST /api/self-updates/{id}/recovery-status`.

---

## Removed (was multi-user, now single-user)

In-app user accounts, roles (`environment_admin`/`member`), multi-user login,
team bootstrap, invite links, project membership/sharing, project
visibility (private/shared), team name. Collaboration model is instead: **everyone
self-hosts their own instance + shares folders/repos.** The runtime model is one
owner with one password/session gate; legacy invite/member tables have been dropped.

## Single-workspace shell ("Deck", T3)

+ **One workspace, no Ops/Code switch.** The header has a URL-durable **Work / Delegate** mode control. Work keeps the flow-ordered destinations Chat, Tasks, Workflows, Archive, gated Design, and project-scoped recent chats; its sidebar owns the active-project switcher and the top bar does not. Delegate keeps that same persistent, collapsible sidebar and header language, but replaces Work navigation with global Master, Tasks, and Archive. It has no project selector, project filter menu, ordinary Chat, Workflows, Design, tools, search, popup, or account surfaces; its Tasks and Archive views query across projects and their task and record deep links remain in Delegate. Opening a graph plan explicitly returns to Work, and Task workspace Design actions remain unavailable in Delegate. There is no primary-nav **New chat** twin and no primary-nav **Projects** row. **Chrome Back** is always visible in Work (disabled without a deep stack) and returns to the origin surface; deep views lock the project switcher. Workflows home and open-plan header do not dump project display names (lock is icon + tooltip only). Chat stays mounted when leaving so draft + in-flight run re-attach in-session. Work/Chat is the default. Agents and Settings live in the Work profile menu; Wiki lives under Settings → Knowledge. Running work is a text pill (`N tasks running`) hidden when idle. Server feature flags remain authoritative; a disabled Master flag removes Delegate and makes a stale Delegate URL fall back to Work.
+ **Chat** is the front door: brainstorm, then **Slice into plan** promotes the conversation into a runnable plan. Its header carries the session and agent; Work-sidebar project context remains outside the conversation. Its **New chat** action clears the active session (mobile topbar keeps a compact icon; `/new` remains a power-user path); the chat remains lazily created on first send.
+ **Master** is the gated delegation/monitoring peer to Chat: one hidden system identity, a schema-validated filesystem-isolated product broker, chat-only runner conformance, three honest worker slots, active queue, needs-you subset, job checkpoints, and an opt-in budgeted unattended toggle. The flag defaults off; dynamically conforming Codex 0.145.0 or newer is supported, and every other or unavailable adapter fails closed.
+ **Tasks** is the permanent execution/review index; its `+ New task` button opens the launcher - a single integrated Task Composer with searchable Project/folder context, selected Agent, a combined Add menu for attachments/image/design, and Guarded or Autonomous execution policy. It creates a durable ad-hoc job and opens a dedicated hash-addressable task workspace with live progress, review, approval, and deliverables. The linked execution session is not a visible chat conversation.
+ The single **Workflows** destination contains a remembered Drafts / Workflows / Runs library home and the plan Editor (graph canvas). The Workflows table splits Manual from Scheduled rows using real schedule data. Scheduling lives in the row dialog rather than a separate mode while retaining five-field cron, overlap, enabled, Run now, and delete behavior. The graph is enabled by default; its flag is a recovery switch rather than a hidden experimental mode.
+ **Right tool rail** (`ToolDock`): Terminal, Files, and Preview open as overlay panels above the current screen, project-scoped, in any context; the rail's gear opens Settings and Escape closes the panel. Terminal and Files stay mounted after first open (shells and unsaved edits survive a closed panel); Preview unmounts because its dev server is a backend process. The Archive remains the destination for agent outputs; Design remains a separate feature-gated canvas, with artifact source fallback when disabled.
+ **De-jargon rule:** primary surfaces say "agent" and "tools" — never "runner", "MCP", "profile", env-var names, or raw stack traces. That detail lives in Settings → Agents and the docs.

Authentication remains single-owner defense in depth: first run sets a password, later requests require a bearer token or `proxima_session` HttpOnly cookie, login establishes the session, and resume restores it. Each invalid attempt focuses the corrective field and mounts one fresh assertive alert, even when the same values are submitted again. The gate keeps one main landmark, password-manager-compatible hidden owner metadata, and token-based text and focus contrast across every canonical theme.
