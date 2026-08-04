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
with CLI-only advice. A spent ChatGPT/Codex refresh token gets its own next step
(`codex login` on the host), because Proxima already re-syncs every profile's copy
of that login silently (see *Credential sync* below) - reaching that error means
the login itself is gone, not out of date. Hermes readiness (`hermes_status` / `runner_readiness` /
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
**Credential sync (rotation-safe):** each profile home carries its own copy of the
runner's login, seeded from `RunnerSpec.source_dir` and reconciled with the host on
every run (`profile_seed.py`). ChatGPT/Codex refresh tokens are **single-use** - a
refresh burns the old one - so the sync is *newest wins in both directions*, not
host-wins: a host re-login lands in the profile, and a token a profile rotated to is
published back to the host (also post-run, from `execute_run`'s `finally`) so the
other profiles heal silently on their next run instead of replaying a burnt token. A
copy is published only when it is the same login (same JSON shape and `account_id`),
so a profile that was switched to another runner can never overwrite the host's
credentials. The reconcile is single-flight (one `flock` per source dir) and writes
atomically at mode `0600`, and only a changed *profile* copy recycles the cached
agent process, which holds the old token in memory. Details:
[architecture → Credential sync](reference/architecture.md#runner-abstraction).
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

Work Chat state is owner-scoped and keyed by project plus session. Its draft,
selection, Normal/Brainstorm/Debate mode, safe pending attachment references, and
scroll anchor survive Work destination changes, Work/Delegate switches, full reload,
and an installed-PWA restart. Stable Work URLs and native history also retain the
active project and Chat session while Workflows or Design is the primary surface.
Unavailable projects fall back explicitly without reading another project's state.
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

> **Activation:** durable Master identity and compatibility migration are live,
> and the product runtime and UI are always on (the feature-flag system was
> removed in prune A2, #129). Migration is unconditional. Codex
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
region, and becomes a full-height sheet on narrow screens. At rest the trigger also
**clears the surface's composer**: it measures any bottom-docked composer it would
cover and rises above it, so Send is never underneath it at any width (#154). Its modal dialog traps
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
Owner-facing target controls use **Project** terminology. Picker options, the final
send warning, sent-message metadata, and popup chrome lead with the unique Project
name. The identity label and Area remain visible only as secondary context, so two
Projects with the same identity label cannot look like the same destination.

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

- One distinct Code graph state row per registered repo Area (never a Task
  worktree). The canonical graph lives in Proxima's runtime dir at
  `<workspace_root>/graphs/container-<id>/code-area-<area_id>/graph.json`
  (prune C2): builds never create `graphify-out/` or append ignore lines
  inside the repo. Pre-C2 rows pointing inside an Area reset to the runtime
  path on next resolve ("registered graph scope changed") and rebuild there.
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

- At most one Knowledge graph per Container Ops area, canonical at
  `<workspace_root>/graphs/container-<id>/knowledge/graph.json` in Proxima's
  runtime dir (never inside the Container - prune C2), with state in
  `graph_states`.
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
and a durable Master recovery delivery intent together. Delivery appends one
human-readable entry that identifies the owner, checkpoint, prior/restored state,
discarded progress, and conflicting progress without copying worktree paths, Task
titles, or arbitrary graph identifiers. Recovery events use the same 16 KiB
durable-event encoder as Master projections. Missing legacy Focus leaves the Task
restored with an explicit repair state and never publishes unattributed history. All
fallible Git checks finish before the immediate write transaction; conflict, job, run,
and node state are then reread under that lock before any restore write. All fallible
database writes and worktree checks finish before reset; a failure after reset restores
the original worktree commit before the database rollback is returned. Normal project
Chat uses
ACP tool events to trigger a bounded before/after path journal. Assistant replies with
changed files show **Restore N changed paths**; preview lists each path and warns about
active Master work before confirmation. The journal cascades when its session closes.

**Inbox and notifications (#157/#158):** `attention_items` is the one notification
ledger - the Inbox extends it rather than forking a second store beside it. Two
independent axes: `read_at` answers *has the owner seen this* and `status` keeps meaning
*does this still need them*. `GET /api/attention` is the ephemeral header feed (unread
rows only, newest first, `count` = unread); `GET /api/inbox` is the persistent
destination (everything, with `?unread=1`, `limit`, and a `before` cursor).
`POST /api/inbox/{id}/read` toggles one row, `POST /api/inbox/read-all` clears the
badge, and `POST /api/attention/{id}/dismiss` acknowledges a header item - including
navigate-only kinds like Master budget that `/act` refuses. Dismissing is *seen*, not
*done*: the row keeps its open status, its actions, and its place in the Inbox. The
exception is a **pure notice** - a kind with no decision behind it, listed in
`ACKNOWLEDGEABLE_KINDS` (today: `master_budget`) - where acknowledging also resolves
it, so it does not linger on the Master desk's work panel after the owner has seen it.
`POST /api/inbox/client-error` gives a browser-side failure a durable home: the global
error toast is ephemeral by design, so a *new* toast (never a repeat - those already
collapse) files its diagnostic as an informational `client_error` row. The text is
bounded, the row can never carry an action, and the channel is capped per day, so the
browser can file news but never work. The Inbox pages by row id rather than
`created_at`, because a projected Task outcome carries the moment the work finished and
mixing the two orders would let the cursor skip rows.
Acting on an item marks it read too. Items the attention route derives from other
tables (job reviews, node-script trust, satpam restarts) are mirrored into the ledger
under the same `job:`/`script:`/`satpam:` ids, so the Inbox is a strict superset of
everything the header ever showed. Terminal Task transitions are projected as
informational `task_outcome` notifications carrying the failure detail (rejected
reason, failed step, failed node, or run error) in their body, watermarked at the
moment the ledger started listening so an upgrade never replays old history. Work that
the system settles (review approved, decision resolved, restart run) stops counting
toward the badge; informational rows stay unread until the owner reads them. A Master
budget notice clears itself once Unattended is switched back on - the sentence it
carries no longer describes reality - and its `source_key` now names the budget cycle,
so a second stop notifies again instead of being swallowed by the first row.

On the surface: the header popover shows only the unread slice, badges unread, and gives
every row an explicit **Dismiss** plus a footer link to the Inbox; removal is optimistic
so the badge never lags a click, and a failed dismiss falls back to the ordinary
retryable error. The **Inbox** destination lists everything newest-first with an
All / Unread filter, mark-all-read, a per-row read toggle, a Load-older cursor, inline
actions for anything that still needs a decision (a Master decision renders its full
resolve/defer form, not a link away), and the full error detail on the entry so a failed
Task is diagnosable without opening its run. It renders in both Work and Delegate. See [UI shell](ui-shell.md#notifications-the-ephemeral-header-and-the-inbox-destination).

**Attention:** the shell badge calls one `/api/attention` shape spanning simple final
job reviews, complex diff reviews, pending satpam restarts, durable tool permissions,
and Master decision/budget items. Non-decision rows deep-link to their owning
Task/plan/Master/Settings surface. Only rows marked `inline_ok` render actions: simple
non-repo final review, hash-visible script trust, pending satpam restart, and live
permission choices. Diff and Master budget items navigate only. Job-linked rows include the same
canonical run projection used by Workflows and Tasks, so a review-parked failed
graph node reads Failed everywhere. A non-approval Master decision is a dedicated
`master_decisions` record linked to its Attention row, requesting Task, originating
Master message, and canonical Master session. It preserves the full owner prompt and
context, a bounded choice set or bounded free-text contract, pending/deferred/resolved
state, optimistic version, response, actor/time, and the Task message and continuation
run created by resolution. The full form appears both in the Master Decisions
accordion and directly in global Attention. Deferring closes the global badge without
losing the decision from Master, and survives reload or restart. Resolving validates
the current version and response, records the response, queues exactly one Task
continuation, closes Attention, and appends a concise human-readable Master event in
one transaction. If the requesting Task leaves review without an owner answer
(reject, delete, or project delete), every pending or deferred decision is settled
closed without a continuation run and projects one resolved Master event that says
the Task left review. A Task with an unresolved Master decision shows that same
question in its workspace and rejects the generic approval endpoint, while ordinary
approvals keep their existing specialized path. Worktree-backed final approve claims a durable
generation before any merge or push so a concurrent decision cannot land mid-merge;
decision creation refuses while that generation is live, merge failure releases it,
and restart finalizes a merged generation without merging twice. Supervisor start
failures stay bare generic Attention and never become resolvable decision ledger rows.
Errors persist inside the inbox until retried or dismissed.

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
prerequisite-block, Attention, decision-deferred, decision-resolved,
supervisor-outcome, and Satpam messages to the one Master thread.
`master_projections` is an owner-scoped idempotency/link ledger, not a
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
second message or event and isolates failures per authoritative source row. A
database-maintained Task generation advances only when canonical projected state
changes, so ordinary step, node, timestamp, and same-status progress cannot repeat a
lifecycle event while Running to Review to Running remains distinct. Status and
recovery intents process in Task-event order. Checkpoint recovery causally supersedes
only obsolete unpublished status intents before its authoritative recovery event, so
delayed Failed or Done delivery cannot overwrite Queued. Every recovery audit intent
remains append-only. New and still-orderable recovery audits publish exactly once in
that order. An upgrade from the older superseding recovery model records both
unpublished predecessors and already-projected publication reversals in an immutable
per-Task ordering-gap ledger without replaying or rewriting original recovery rows.
Already-delivered legacy correction messages, events, and durable marker rows remain
immutable partial history, with exact links to the gaps each marker covered. Any
still-uncovered gaps are combined into at most one new bounded aggregate correction
marker per Task after the current Task projection, while predecessors without a
published successor return to normal ordered delivery. The v48 compatibility path
stages every delivered marker's original id, links, payload, attempt metadata, and
timestamps before aggregation; v49 restores only from that exact evidence. Older
databases that already lost marker identity retain the bounded published event as an
immutable legacy-loss record instead of receiving invented identity or timestamps.
Deleting a Task, its Task session, or its job source captures the stable Task
session, job, Task-event, and recovery-outbox identities at the job, session, event,
or outbox `BEFORE DELETE` boundary. It records the exact outbox-to-event map, then
tombstones and archives the correction, gap, and coverage rows before live cascades,
preserving their stable ids and surviving Master message/event links. Repeated
boundaries can complete missing legacy tombstone fields and expand captured ranges,
but only `jobs.session_id` or one consistent set of outbox-referenced Task events
can establish Task-session identity. Generic graph-session membership is never used.
If neither source remains, the tombstone keeps `NULL` and an immutable bounded loss
row records the unavailable identity. SSE
reconnect accepts the existing cursor query and `Last-Event-ID`. No projection can approve review,
landing, Attention, or Satpam gates. See
[Master supervision and durable projections](master-supervision.md).
Owner mutations that happen outside a worker run append a transaction-coupled
`job.update` to the Task session. Review completion/failure also enqueues a durable
Master projection outbox row in that same transaction, while checkpoint restore
enqueues its bounded recovery intent. Projection delivery and Task or Master stream
notifications happen only after commit. Delivery failure leaves a replayable pending
row; unavailable legacy Focus becomes explicit failed attribution without rolling
the Task verdict or restore back or weakening attribution. The restore response
and canonical Task/Fleet job payload expose that durable repair state. A mounted Task
workspace consumes this one shared invalidation path for review verdicts and
checkpoint restore instead of waiting for running-only polling. Fleet grouping and
labels consume the same canonical effective status as its run projection.

**Tours:** after setup, the first main-UI visit opens a keyboard-trapped core tour
with five chapters when Master is enabled and four when it is disabled. Completion
is reconciled between the feature-off browser marker and Master settings so enabling
Master does not replay a tour the owner already completed. Settings → Help & Tours
can replay it and launch chapters for Workflows, Projects/tools, Artifacts,
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
a chat session pulls the shell project to match (so Artifacts / @-mentions start on the
conversation's project); an intentional Projects pick still sticks for Tasks and
Artifacts while an older session stays in memory. The chat header always prefers the
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
the screen, so there is no manual Save gate. The save indicator reflects only accepted
server state: local intake edits, pending autosaves, and rejected writes all keep the
draft out of Saved, and Run stays disabled until the complete graph is valid and
persisted. Saving it as a reusable
Workflow is an optional, separate one-click action (before or after the run). Slice-into-plan is
single-flight on the button (double-click cannot start two promote runs), and the
Recipes editor creates at most one graph job per draft object — React Strict Mode
remounts reuse the in-flight create so Tasks does not list two identical queued plans.
The legacy ordered-step path is retained only for existing data.
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
edges carry dependencies. The trigger node owns the shared **intake contract** plus
optional **schedule seed** settings. Intake fields (`text`, `url`, `number`, or `file`)
live on the trigger and power every manual **Run**: each field has a stable identifier,
optional default, and required flag. Rows are created and edited as complete units, so a
transient blank or duplicate ID never reaches persistence. Draft Run and reusable Manual
Run open the same validated dialog, which applies defaults, omits blank optional values,
and refuses missing or type-invalid values before execution. The API repeats that
validation before claiming the job and freezes the resolved values into the job and
trigger output. Each field's `{{id}}` is substituted into node text. Schedule seed
settings (cron, IANA timezone, overlap, enabled, default Off; UI timezone defaults to the
browser zone, API/graph omit defaults to UTC) are independent of intake and of the
Manual/Schedule authoring view - promoting a plan creates a schedule row whenever a seed
object is present (default Off), regardless of `trigger_kind`, but unattended ticks and
**Run now** resolve durable schedule bindings rather than prompting, and never replace the
manual Run path. Existing graph workflows keep the compatibility
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
An empty Plan Chat teaches workflow authoring - nodes, branches, inputs, review
gates, and node tests - instead of reusing main Chat guidance. Opening, closing, or
resizing Plan Chat, workflow metadata, or the selected-node inspector refits the
measured canvas so the whole graph stays visible while preserving deliberate
pan/zoom intent (details in [workflow-graph.md](workflow-graph.md)).
The **Sequential recipe editor is retired** — a linear recipe is a graph with no
branches. The linear engine remains for pre-existing jobs; `IterateStage` is still
reachable from an old session carrying `workflow_id`, but no new linear workflow can be
authored.
The library home remembers its last selected **Drafts**, **Workflows**, or **Runs** tab
and presents each as a table. The Workflows tab is one reusable-workflow table: each row
shows **Availability** (active or paused) separately from the joined **Automation**
summary (schedules on, off, or needing bindings). Every row keeps Edit, manual **Run**
(per-run intake when fields are declared), **Schedules**, availability pause/resume, and
archive. The schedule dialog owns timezone, five-field cron, durable input bindings,
overlap, per-schedule On/Off, Run now, configure, and delete. Trigger-authored schedules
seed cadence when the plan is promoted and never carry manual intake values into
unattended runs.
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
running unisolated against the live code area.
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

Every Task workspace leads with its immutable owning Project and Area, including when
opened from global Attention. A cross-Project open preserves the Work Project instead
of changing it, locks the switcher for the deep surface, states that Work remains on
the prior Project, and exposes a labeled return to the origin surface.
Opening Design from that Task binds the studio filesystem to the Task owning Project
without adopting it as Work; returning to the Task restamps the in-app preserve-work
hash policy so a later cold reload still takes the permalink path.
A reloaded `#task/<id>` permalink resolves Task metadata and its owning Project before
mounting the shell. It then selects and locks that Project in one transition. Until
the Project and Task agree, Project-bound Preview and Terminal tools remain
suppressed, so stale Work context cannot leak into the Task surface.

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
`aria-label`s (`title · Plan · status · progress · age`, and `Project: name` on
Delegate's global projection) so assistive tech does not smash the plan pill into the
title. With the graph feature off, the screen shows classic tasks only, exactly as
before.
Delegate's global List, Board, and Review projections add the owning Project to every
classic Task and plan, both visibly and in the accessible name. Work's Project-scoped
Tasks projection omits that repeated label because the shell context already states
ownership. Linear and graph job payloads both carry `project_name` for this shared
projection.
**Endpoints:** `GET /api/graph/jobs` (+ the linear list above), `POST /api/graph/jobs/{id}/save-template`.

### Repo jobs: isolated worktrees + review + local merge (Phase-1 slices 2+4 - LIVE, on by default)

**Why:** A job that touches a repo must never edit the primary tree directly (T1). It
runs in a safe copy, the owner reviews the before/after diff, and approving merges the
work **locally** into the branch it was cut from - no remote, no GitHub required
(T1 local-first; the optional push-after-merge connector is the next section).
Always on since the flag collapse (prune A2, #129).
**How:** a job may carry `target_area_id` - the ONE container area it works against
(T1: exactly one target; a code-area target = repo job). On start, `worktrees.py` cuts
branch `proxima/job-<id>` from the target code area's repo into a worktree under
`<workspace_root>/worktrees/job-<id>` - outside the container, so scans never see
work-in-progress and the worktree's `.git` file can't register as a code area. The cut
refuses loudly (409, job stays queued) on a dirty repo, detached HEAD, or no commits;
crash leftovers are cleaned idempotently by job id. The run worker
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

**Why:** Recurring agents - daily report, watch-and-summarize - while you sleep.
**How:** `schedules` table + a 60s scheduler loop that materializes only *due* jobs
(own 5-field cron matcher; overlap policy skip/allow). Every schedule stores an IANA
timezone and its cron is evaluated in that local time. Failed step fails the job.
Schedules are unattended and never receive the manual Run dialog's per-run answers.
Required workflow inputs must instead have durable bindings in the schedule. An
unresolved schedule may be saved Off, but create/update refuses to turn it On with
`schedule_missing_sources` until the owner saves a durable binding for every required input. The
scheduler repeats this validation before every spawn, so a legacy or drifted unsafe row
cannot run. Migration 45 adds timezone state, preserves existing cron behavior with the
host's local timezone, aligns schedule ownership with the workflow project, and turns
unsafe legacy schedules Off.

Workflow **Availability** (`active` or paused) and each schedule's **On / Off** state
are independent and visible separately. Every reusable workflow keeps its explicit
manual **Run** action, which collects that run's intake even when schedules exist.
**Run now** fires a schedule on demand and opens the task it spawned, so the owner can
prove a schedule before leaving it to fire unattended. It reuses the tick's own
`_spawn_scheduled_job`, so it exercises the real cron target (workflow, project,
profile, durable bindings, and repo worktree binding) instead of a lookalike; it passes no
minute key, so a manual run cannot claim - and thereby swallow - the scheduler's slot
for that minute. It works on a disabled schedule (`enabled` gates the tick, and trying a
schedule out is exactly when it is still off) and reports an overlap skip as a 409 rather
than silently no-op'ing. For graph jobs, the schedule dialog stays open until the exact
returned job is fetched and selected in the workflow's owning project.
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
The selected Work Project is an owner-keyed browser preference and survives a full
refresh. Boot validates the saved slug against the owner's current Projects. If it
was removed, Work selects an existing private Project and shows an explicit,
dismissible fallback notice naming both the missing and replacement Projects.
**Endpoints:** `GET/POST /api/projects`, `/projects/link` (`mkdir` and `ops_path`
optional, `root_id` required),
`GET /api/fs/dirs` (`root_id` required once a path is selected), `PATCH/DELETE /api/projects/{slug}`,
`GET /api/projects/{slug}/location` and `POST /api/projects/{slug}/rebind`
(relocate a moved folder, prune C6 - see Container Areas below).

### Container Areas and physical Ops storage

**Why:** A Container is not a repo. It holds zero or more **repo Areas** plus exactly
one **Ops Area** for durable non-code work. A repo Area may be a nested folder or `.`
when the Container root is itself a repo. **The Ops root is a per-project path**
(prune C3), picked at link time and persisted on the Ops Area row - there is no
global assumption that Ops lives at `ops/`.

**How:** The compatibility `projects` and `project_areas` tables remain the storage
and foreign-key truth; the Ops row's `rel_path` is the persisted per-project Ops
path and every Ops feature resolves through it (`ops_root`). Workspace-created
Containers (`POST /api/projects`, under
Proxima's own data dir) still scaffold `ops/` with starter dirs, a root
README, and one active Ops row with `rel_path='ops'` - but no
`ops/container.md` (identity comes from existing docs, prune C5). **Linked folders are never written to** (prune C2,
below): the link flow offers the detected default (an existing `ops/` folder ->
`ops`, else the project root -> `.`) in the folder picker's "Ops folder" select;
the owner may override it with any existing subfolder or the root (`ops_path` on
`POST /api/projects/link`, validated as a real non-symlink directory, rejected
with a field-scoped 400 otherwise). A detected default is recorded with
`source='auto'`, an override with `source='manual'` - a manual choice is never
adopted away by the settle sweep. Code-area auto-detection skips the chosen Ops
subtree (a repo inside it is Ops content, not a code Area), and the merged file
tree, `@`-reference index, and path-only file requests all resolve through the
persisted path. `mkdir`-linking creates exactly the empty directory the owner
asked for - nothing inside it (its Ops path is the root). `container_registry.py`
is the canonical
resolver for Container, Area, and Ops roots. It validates realpath containment and
rejects path traversal, duplicate roots, unsafe overlaps, and Container-or-Ops-root
symlinks on every resolution; the recursive scan that rejects every symlink under the
physical Ops root is opt-in (`deep_ops_scan`) and, since prune C7, enforced
fail-closed only where content moves - physical Ops root creation and the move-based
legacy migration. Adoption, the boot settle sweep, registration, and every read skip
it and rely on per-access no-follow resolution instead, so project lists and Home stay
O(1) and a stray link never escapes.
A repo at `.` is the one intentional containment case; the explicit migration
(only) adds `/ops/` to that repo's local git exclude when it creates `ops/`.

`container_activity.py`, `ops_filesystem.py`, and `ops_publication.py` own the
cross-process lease, native identity, and descriptor publication boundaries. They
do not depend on registry projection; `container_registry.py` orchestrates them.

**Link and startup are non-mutating (prune C2).** `POST /api/projects/link` and
the boot sweep run `settle_container_ops` / `migrate_legacy_ops_containers`,
which only ever take zero-write paths: **adoption first (prune C1/C3)** - the
link-time Ops choice (detected default or `ops_path` override; an existing
empty folder qualifies at link time) is adopted exactly as it exists on disk
(the Ops row flips to the chosen path, the durable marker
completes with a `mode: "adopted"` manifest holding a top-level inventory, and
nothing is moved, generated, or rewritten; no `container.md` is created - an
existing one is simply read for the identity/summary projection); resuming a
previously authorized in-flight move (a durable "moving" manifest); and
otherwise **leaving the folder byte-identical** - the Ops row stays at `.`
(its files resolve at their literal root paths), with no marker and no
Attention item. The startup sweep re-validates persisted choices and
auto-adopts a populated `ops/` only for detection-sourced legacy `.` rows;
persisted non-`.` paths are first-class settled layouts, never "unsupported".
Linking therefore never moves top-level `wiki/`, `tasks/`, etc.,
never creates `ops/` or `container.md`, and never appends to `.git/info/exclude`
- the onboarding promise "Nothing is moved or copied" is literally true.
A symlinked, non-directory, or unreadable adoption target, or one overlapping a
repo Area, stays fail-closed with an Attention item. A symlink *inside* the
adopted folder no longer refuses adoption (prune C7, #142): the folder is adopted
as it is and the link shows up as a skipped entry, so a real client folder with a
shared-asset or `node_modules` link no longer lands in the permanent attention
loop (audit #120, verified symlink-free in #131 only because of the old policy).

**Per-project layout map (prune C4).** Where a project keeps its **wiki,
artifacts, scripts, and uploads** is per-project data, not a fixed name:
`layout_map.py` seeds one `project_layout` row per area by **zero-write
detection** from the real tree at link time (the boot sweep and lazy
resolution backfill projects linked before the map existed). Detection looks
for the standard name under the persisted Ops root first, then at the
container root (`source='detected'`); when nothing exists, today's fixed name
under the Ops root is recorded as `source='default'` - so behavior is
unchanged until detection says otherwise. A `.` project keeping `wiki/` at its
root (the BIP case) has that folder detected as its wiki location. Features
resolve those locations through the map instead of hardcoding names: the
wiki-note draft/commit surface, the run preamble's wiki catalog + script
library + designs list, the script library (catalog, approval card, execution),
the upload default folder, and the artifact/design scanners. The persisted map
survives restarts even if a default-position folder disappears; an entry
detected OUTSIDE the Ops root stays authoritative only while its folder exists
and re-detects when it is gone (self-healing after the explicit migration moves
content into Ops). `GET /api/projects/{slug}/layout` reports the map
(`ops_path` + per-area `path`/`source`/`exists`, plus the memory-writes
toggle below).
Since prune #138 the map is also the web client's source of area locations
(`useProjectAreaPaths`): the project Wiki screen browses the mapped wiki
folder, and Design Studio, the Moodboard, and diagram whiteboards live under
the mapped artifacts folder - the moodboard store (`<artifacts>/moodboard`,
container-relative item paths with a read-boundary upgrade for reroute-era
Ops-relative entries), design scenes/assets (`<artifacts>/design`),
chat-generated images (`<artifacts>/media/images`), and whiteboards
(`<artifacts>/whiteboards`). Two sites deliberately keep fixed Ops-relative
names (decided in #138): the **Knowledge-graph allowlist**, because Knowledge
scope is a security boundary (only the Ops workspace may enter Knowledge,
never repo/Container files - and inside the Ops root detection can only ever
produce these exact names, so allowlist and map cannot diverge there). The
artifact record language (`produced_artifacts`, `output_links`,
`artifact_records` rows) speaks **container-relative real paths** since the
Part D ledger rework (#139): the record scan is container-rooted through the
layout map, so an artifacts area outside the Ops root gets both files AND
records in the right real place (the #138 bridge and its
`ops_record_rel` shim are gone; migration v61 rewrote legacy rows).

**Identity from existing docs + adaptive memory writes (prune C5).** A
project's identity (the Fleet's `identity_label` + `summary`) is read from
the docs the folder **already has** - `AGENTS.md`, `README.md`, `HANDOFF.md`
(probed at the container root first, then under the Ops root), with a legacy
`ops/container.md` still honored after them - and no Proxima frontmatter is
required anywhere. A doc's optional frontmatter (`identity`/`title`/`name`,
`summary`/`description`) is honored when present; otherwise the first H1 is
the label and the first body line the summary. A folder with none of these
files links fine and is identified by its own folder name.
`resolve_container_identity` (`container_registry.py`) picks the first doc
that yields anything; the projection records which doc won
(`container_registry.identity_source`, also in the Fleet payload) and
re-indexes only when the source hash changes (a 5-second background cycle,
plus immediately after link and after a Files-API write to any identity doc
name).
Nothing generates `ops/container.md` anymore: fresh workspace Containers
scaffold only starter dirs + README, and the explicit migration plans
`container_doc: "none"` when no legacy root `container.md` exists (stored
pre-C5 manifests with a planned generated document still execute).

Proxima's **automatic memory writes** - the post-run `log.md` append and
`index.md` regeneration - are **adaptive and default ON** (decision #121):
they target the project's own detected wiki location through one seam,
`layout_map.wiki_memory_write_root()` (so BIP-style root `wiki/` folders
receive the log and index directly). A **per-project toggle** turns them off
entirely: `PUT /api/projects/{slug}/memory-writes` (`app_settings` key
`project:<id>:memory_writes`), reported by `GET /api/projects/{slug}/layout`
as `memory_writes.enabled` and surfaced as a checkbox in the project settings
dialog. Writes stay fail-closed: a wiki position occupied by a symlink or
non-directory is never a write target, and a missing directory is only ever
created at the DEFAULT `<ops>/wiki` position - never invented at a detected
non-default location. Memory writes happen only on actual memory events
(post-run log append, index regeneration on run start/end and wiki-note
commit) - never at link or boot.

**Relocate/rebind a moved or renamed folder (prune C6).** Folders get moved
and renamed; the Container root is pinned to its filesystem identity at link
time, so until now that turned every project operation into a boundary error
with no way back (audit #120 part 2, item 6). The binding between a project
record and its folder is now a **classified, actionable state**
(`container_registry.container_binding`, one `lstat` + one directory-handle
open, never a write) carried on every project payload as `location` and served
in full by `GET /api/projects/{slug}/location`:

- `bound` - the folder is where the record says, with the identity it was
  pinned to;
- `missing` - nothing is at the stored path (moved, renamed, deleted);
- `moved` - a *different* directory now sits at the stored path (restored from
  backup, recreated);
- `unavailable` - the path cannot be inspected (permissions, non-directory).

Anything but `bound` is offered with its two actions - **rebind or unlink** -
on the project card in Projects (a "Folder missing" pill, the reason, and a
"Find folder" button); unlinking keeps working with the folder gone.
Re-pinning runs through `POST /api/projects/{slug}/rebind` and reuses the
**onboarding folder picker verbatim** (`FolderLinker` in rebind mode), so the
target is jailed to the configured link roots exactly like a link.
**Identity is confirmed with the C5 machinery**: the docs the folder already
has are read AT the new location and compared with the stored projection
(source hash, else the identity label); the persisted Ops path and every
registered code Area are checked to still resolve there. A mismatch is refused
with a 409 naming both identities (stored vs found) - and, because this is a
single-owner product, the refusal is **overridable**: `confirm: true` re-pins
anyway ("Re-pin anyway" in the dialog).
Rebind is **metadata-only**: not one byte is written into either folder.
Only the record's address changes, so the project keeps its id and therefore
its history, chats, tasks, deliverable records and approvals, its layout map,
its Ops path, and its memory-writes toggle. Entries whose *paths* broke are
re-detected in place at the new location: a persisted Ops folder that is not
there falls back to link-time detection, layout-map entries whose folder is
gone re-detect (`layout_map.rebase_project_layout`), auto code Areas follow the
new tree, and a manual code Area with nothing behind it is dropped and reported
back (it would otherwise keep the Container permanently invalid). The whole
apply runs under the Container mutation lock in one transaction that ends with
a full fail-closed `validated_area_roots` check - a rebind either lands
completely or changes nothing. Re-pinning a healthy project to the path it is
already bound to is a no-op; re-pinning it to the same path after a restore is
how the identity gets re-taken.

**The move-based migration is exclusively an explicit, previewed opt-in** on the
Ops-migration surface (`.../ops-migration/retry`). Its inspection payload reports
`retry_action: "adopt" | "migrate" | "revalidate" | null` plus, for a safe
`migrate`, `planned_writes: { container_doc: "move" | null, git_exclude:
bool }`; together with `legacy_owned_paths` the UI shows **exactly** what would
change - each planned move, whether a legacy root `ops/container.md` is moved,
and whether the root repo's `.git/info/exclude` gains `/ops/` - both in the
validation panel and in the confirm dialog, before anything is touched. The
migration first builds and hashes a dry-run manifest. An owner-authored legacy `container.md` is
hash-bound and moved byte-for-byte; with no legacy document, nothing is planned
for `container.md` at all (strategy `"none"`, prune C5 - the historical
generated-document protocol below remains executable for stored pre-C5
manifests). Atomic no-clobber publication through stable no-follow
directory handles publishes only manifest-bound inodes for known Ops-owned paths.
Regular files are linked from opened descriptors; directories are published entry
by entry. Manifest version 6 persists every Proxima-created destination directory
identity before publishing a child and rejects all unbound existing destinations,
including empty directories. The original legacy name is retained under a
manifest-bound recovery name only after its complete source snapshot is revalidated;
a changed source remains untouched for owner intervention. Generated documents
require anonymous same-filesystem
storage and persist the anonymous inode identity and expected hash before the first
visible recovery link. The exact recovery hardlink remains as a durable anchor, so
retry never infers ownership from a name and cleanup never unlinks a re-resolved
entry. One cross-process per-Container mutation lock serializes the filesystem and
durable marker boundary with supported Area, Files, Design, Moodboard, chat-media,
and turn restore mutations. A separate shared activity lease spans agent runs,
project terminals, and preview apps. A standalone guardian selected by verified
absolute path runs in isolated Python mode from a trusted working directory. A
detached Linux subreaper sentinel or a Windows Job object inherits the lease before
the writer starts and retains it until the complete process tree exits, including
after API shutdown or cancellation. Platforms without a complete tree-proof
primitive refuse guarded Project writers. Guardian records bind both the owning API
process and guardian by PID and process-start identity. Retry reports a matching
live owner as an active-process conflict and never signals it; only an identity-proven
orphan can be recovered through its Linux sentinel or exact named Windows Job.
Activity-guarded ACP processes use per-run cache scopes, so concurrent runs cannot
recycle each other's process. Exclusive migration acquisition is bounded, and
migration and complete Project deletion require that exclusive quiescent lease.
Async uploads finish staging before they acquire the synchronous publication
boundary. The durable marker resumes safely after
interruption. Older markers upgrade only when
the legacy and physical document state is unambiguous; otherwise every candidate is
preserved for owner intervention. Any collision, changed content, unsupported file
type, or ambiguity stops only that Container, opens an owner-visible Attention item,
and leaves the legacy row active. Every resumed marker rechecks current code-Area
ownership across the complete physical Ops root and every path type, symlink, content
hash, and filesystem constraint immediately before applying any remaining move.
Existing generated content must match its manifest exactly, and a late destination
can never be replaced. A repaired physical layout with an open Attention item can use
the same explicit retry boundary to recheck the layout and resolve the item without
moving content. Root-repository exclusion is updated through the same opened
no-follow Container descriptor. Fresh Windows Containers open and identity-bind the
Container before creating `ops/` and every starter component relative to
no-reparse handles, then use relative no-clobber document creation instead of POSIX
descriptor APIs. See
[ADR-0038](adr/0038-owner-safe-container-activity-boundaries.md).
The Attention item links to a durable Project settings detail route. That surface
shows the affected Project, exact stored owner-safe reason, migration phase, legacy
and physical path states, exact physical-root entries, conflicts, and which Ops paths
remain usable. Owners can reveal either side through an explicit Container-root
read-only target, answered by the dock's **Files** tool (#145), and refresh
read-only validation. Recovery inspection has only tree
and file-read operations; its tree removes mutation controls, opens files
read-only in place (Container-root bytes are reachable only through the inspection
adapter, so an inspected file is never handed to a main-window viewer that knows
just the Area-scoped root), and visibly expands and marks directory targets. The
shared tree still keeps an already-dirty buffer mounted and read-only across an
adapter swap and path browses rather than discarding unsaved bytes - that
guarantee now protects the Wiki tree and any surface whose tree owns its editor,
since the dock's ordinary Files tree hands opens to the main window and therefore
holds no buffer of its own. Write returns only with the ordinary Area-validated
adapter. Backend-declared root
inspectability disables unavailable or unsafe reveal actions with an accessible
reason. The dock names the inspected Container and offers one **Close inspection**
action; closing inspection, picking a tool by hand, or changing Projects restores
the ordinary writable boundary. The durable detail route pins the shell to its Project across reload and
refresh; switching Settings sections clears the detail route. Retry stays disabled
until the current layout passes the existing collision, type, hash, symlink, overlap,
and same-filesystem checks; retry then requires confirmation and resumes through the
durable marker. Proxima never auto-merges, overwrites, deletes, follows symlinks,
moves across filesystems, or decides which conflicting content is authoritative.
All Ops consumers resolve through the row, so deliverable records, Wiki, artifacts, Designs,
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
`DELETE /api/projects/{slug}/areas/{area_id}`, `POST /api/projects/{slug}/areas/detect`,
and `GET /api/projects/{slug}/ops-migration`,
`POST /api/projects/{slug}/ops-migration/validate`, and
`POST /api/projects/{slug}/ops-migration/retry`.

## 11. Files & uploads (APIs)

**Why:** Read/write project files safely from every surface that needs them.
**How:** Tree + file read/write (CodeMirror editor), HTML/MD preview, mkdir/rename/delete,
chunk-streamed file upload with collision-safe naming and a configurable 100 MB default
limit, plus an authenticated raw/preview
route (for images and embedded previews). A separate bounded, path-only reference index
powers `@` autocomplete without returning file contents - every listed path is the
real container-relative one (an ops/wiki note is `ops/wiki/...`, never a virtual
`wiki/...`); produced artifacts from the
project artifact scan are merged into the same picker on the client.
Tree entries and produced/record artifacts carry a server-owned file target:
the project slug, authoritative Container Area kind/id, and Area-relative path. Tree,
read/write, raw/preview, file mutation, record presence checks, and ArtifactViewer all
resolve that target through the same jailed resolver. Tree traversal switches to the
authoritative Ops or Code identity when it enters an Area, so direct physical Ops files
stay distinct from same-name Container files. Broken, escaping, or otherwise invalid
tree and artifact entries are omitted individually instead of weakening the jail or
discarding the rest of the response.
**Symlinks are warn-and-skip on reads** (prune C7, #142): Proxima never follows a
link, but a link no longer errors the view it appears in. A listed symlink comes
back as `type: "symlink"` with `skipped: true` and a reason, and no file target -
the tree shows it (greyed, with a "symlink - not followed" badge, no click target),
its siblings list normally, and opening it or anything beneath it is a 400 that
names the symlink. Writes, mkdir, rename, delete, and Ops migration keep the same
no-follow refusal as a hard failure.
**Paths mean exactly what they say on disk** (prune #138, decision #121):
reserved-name virtual rerouting is gone. A path-only request resolves literally
from the Container root - a real folder named `wiki/`, `scripts/`, or `tasks/`
is just that folder, and file browsing shows the real tree (the Ops folder is a
normal directory entry; nothing is overlaid or shadowed). Uploads return
container-relative paths (the mapped uploads folder by default, a literal
folder when an explicit dir is given), and reroute-era rows (turn-journal
paths, markdown refs in chat text) were frozen once to their historical
Ops-prefixed meaning by migration v60.

Session and Task results, deliverable records, ArtifactViewer, Markdown sibling media,
deletion, and Design Studio retain the server target. Design scenes persist image
targets, but agent replies cannot create or replace that trusted metadata. Artifact
lists and chat messages omit links that cannot be assigned a validated target.
Workspace discovery alone does not create deliverable records.

HTML previews render inside a sandboxed iframe on Proxima's own origin - the sandbox
never includes `allow-same-origin`, so the document sits in an opaque origin and cannot
touch the owner's session, the app, or any other Area. Passive (default) means no
scripts at all. The artifact viewer labels the mode and requires an explicit owner
confirmation, bearer-authenticated and scoped to one owner session, Area, and viewer,
before scripts run. The warning states that active content can run scripts and workers,
use the network, and send selected Area data externally, so Proxima provides no
Area-confidentiality guarantee in active mode; the sandbox still holds. Disabling,
closing, changing Areas, logging out, or restarting returns everything to passive, and
a stale active URL fails closed. Executable non-HTML media (SVG, XHTML, XML) downloads
instead of rendering. Because the sandbox is opaque, previews render self-contained
documents - a multi-file site belongs in Run & Preview (§12). Every deployment shape
(loopback, tailnet HTTP, apps domain) behaves identically; no DNS, TLS, or relay
provisioning is involved.
The security contract is owned by
[Security boundaries](security-boundaries.md#canonical-file-preview);
the locator and request flow are detailed in [Architecture](reference/architecture.md)
and [ADR-0042](adr/0042-file-preview-is-a-sandboxed-iframe.md).
These APIs power the **Artifacts destination** (the produced-work gallery in the
left navigation, ADR-0043; opening an artifact takes over that main window -
documents in the editor, everything else in the inline viewer, #146 - plus the
Deliverables/History tabs and the record panel from #139), the **Files** tool in
the right dock (#145 - the real-disk tree browser for the active project, which
also answers the `proxima:reveal-file` event raised by a record's **Reveal in
Files** and by Ops-migration recovery's read-only Container-root reveal), the
**Wiki** tree under Settings → Knowledge, chat attachments, and `@`
file/artifact references - with the in-browser **Terminal** as the raw escape
hatch. Opening a file from the dock browser is a main-window handoff through one
shell seam, except under Container-root inspection, whose bytes only the
read-only inspection adapter can read and which therefore stays in the panel.
Inline New file / New folder / Rename rows share one tree input with an accessible
name (`New file name`, `New folder name`, or `Rename <entry>`) and a create
placeholder (`file-name` / `folder-name`) so the empty field is not a dead unlabeled
box — Enter commits, Escape or empty blur cancels.
**Endpoints:** `/api/projects/{slug}/tree`, `/file`, `/upload`, `/fs/*`, `/raw`,
`/reference-files`, `/artifacts`, `/preview-mode`, `/api/preview/{slug}/{path}`,
`/api/target-preview/{slug}/{kind}/{id}/{path}`.

## 12. Run & Preview app

**Why:** Launch a project's dev server and preview it in-app — from the **Preview**
tool on the right rail, from an app-type artifact, or from the recipe test bench.

**Where it renders (#147, ADR-0043 decision 4):** the two halves live in two places.
The **Run controls** stay in the dock's Preview tool (`AppRunner`): folder, command,
port, Run/Stop, the owner-power consent, the command logs, and every fail-closed
refusal with its next step. The **running app renders in the Artifacts main window**
(`AppViewport`) at full width, with the device presets (Desktop/Tablet/Mobile),
Reload, and Open in new tab. Starting an app opens that viewport automatically -
`AppRunner onOpenViewport` → `ToolDock` → `AppShell onOpenAppViewport` →
`App openAppViewportInMainWindow` → `ArtifactsScreen`, the same shell seam a
handed-off file uses (#146) - and the dock then keeps only a compact status
(Ready/Starting, command, port) with a **Show app** action that brings the viewport
back up. The viewport polls status itself, so a stopped or failed app stops being
framed and says which state it is in, with **Run controls** (a
`proxima:open-run-preview` window event the dock answers) and the way back to the
gallery. The security model is unchanged by the move: the frame's sandbox comes from
`appPreview.ts` (`allow-scripts allow-same-origin allow-forms allow-popups
allow-modals` on an isolated relay/subdomain origin, the same string minus
`allow-same-origin` on the same-origin `/api/appview` fallback), and the origin
selection is the same as before. **Work only:** running an app is owner-power
execution driven from a dock Delegate does not have, and a viewport opened there
could not reach any controls, so Delegate offers neither - an app record's
*Preview app* entry is absent there rather than dead, the rule Reveal in Files
follows (#145).

**How:** `AppManager` runs one owner-confirmed dev process per project with a filtered
environment. The owner-power confirmation is asked once per browser and persisted
(localStorage `proxima.ownerpower.ack`), not re-asked on every panel mount or project
switch; declining persists nothing, so the next Run asks again. In that dialog - as in
every non-destructive confirm - the primary action holds initial focus, so Enter
confirms; only destructive (danger) confirms focus Cancel, and Escape always cancels.
The preview must be served root-relative on its own origin (SPA HTML uses
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
The existing bounded 40-line status buffer survives viewport Reload and explicit Stop,
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

## 13. Image generation, video generation, and Design Studio

**Active:** image generation remains available through `/image` (alias `/gambar`).
It uses the image provider selected in Settings, saves output under the project's
mapped artifacts folder at `<artifacts>/media/images/` (layout map, prune #138;
`ops/artifacts/...` for a detected Ops layout), returns the artifact in the
originating chat, and feeds the
same durable deliverable registry as agent runs (so Image type filters and records list
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

**Clarify-on-thin-brief:** when a `/image`, `/video`, or `/design` command carries almost
no direction (no attached image and fewer than 3 words after the command), the backend does
NOT generate/draft something generic — it replies in the same chat with a compact
`<question-form>` (image: subject/style/aspect; video: subject/style/format; design:
goal/format/audience/mood/copy).
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
Moodboard data lives under the mapped artifacts folder at
`<artifacts>/moodboard/` (layout map, prune #138) and is isolated by project;
item image paths are container-relative real paths, with reroute-era
Ops-relative entries upgraded at the read boundary. On
desktop the left rail (Chat /
Assets / Layers) and the right inspector are drag-resizable (same `useDragWidth`
pattern as Workflows Plan Chat / node inspector); widths persist in
`localStorage` (`proxima.design.leftWidth`, `proxima.design.inspectorWidth`) and
handles hide when a panel is collapsed. Mobile keeps bottom sheets only. Scenes
persist at `<artifacts>/design/<id>/scene.json` in the mapped artifacts folder
(layout map, prune #138) and appear as design records in the Deliverables tab
(and as thumbnails in the Artifacts gallery).
The optional project component library (`<artifacts>/design/_components.json`) is
loaded only when the design root listing already contains that file, so a fresh
project does not probe a missing path. Zoom/Fit and Layers-panel rows expose
explicit accessible names (and keyboard activation on layer rows) so symbol-only
controls are not just symbols. See [DESIGN-STUDIO.md](DESIGN-STUDIO.md) for the
full contract.

Design Studio is always on (the feature-flag system was removed in prune A2, #129).
The old **Video Studio** (editable video projects with an authorable timeline) stays
removed; ordinary video files remain readable and playable as generic artifacts. The
`/video` **generation** command was re-introduced in #148 as the sibling of `/image` -
see "Video generation" below. It generates a clip; it does not restore an editor.

### Video generation (`/video`, alias `/klip`) - LIVE (#148)

**What:** the exact sibling of image generation. `/video <brief>` in chat generates a
clip with the provider selected in **Settings → Media → Video generation**, saves it
under the project's mapped artifacts folder at `<artifacts>/media/videos/`
(`ops/artifacts/...` for a detected Ops layout), returns a **`video-file`** artifact in
the originating chat - the app-wide vocabulary for a playable clip, so the chat result
card gets an inline `<video>` player and the Archive/Artifacts "Video" filter and badge
list it - and feeds the same durable deliverable registry as agent runs. The composer's **Generate** menu offers
Image / Video / Design draft. A thin brief (fewer than 3 words) gets the same
clarify-first `<question-form>` treatment as `/image` (subject / style / format), so a
vague command never spends credits.

**How the code works:** `video_providers.py` mirrors `image_providers.py`; the shared
family plumbing lives in `media_providers.py` (base-URL join, the `GET {base}/models`
"Test connection" probe, and `response_error_detail`, which turns a wrong-base-URL HTML
404 into a sentence instead of pasted markup). `media_settings.resolve_video_gen`
resolves the `video_gen` app-settings row - a **separate row** from `image_gen`, so
saving video can never disturb a working image configuration. `routes/chat.py` runs
`/video` through the same `_start_media_run` background machinery and the same
`_save_chat_media` write seam as `/image`
(heartbeats while the provider works, artifact + assistant message on completion,
provider error text surfaced in chat on failure).

**Client contract (probed against the owner's gateway, not guessed):** the base URL is
the API **root** with no endpoint path (e.g. `https://api.linc.id/v1`); the client
appends the path itself - identical semantics to image generation. Two async job shapes
are supported, tried in order:

1. `POST {base}/videos/generations` → `{"request_id": "…"}`, then
   `GET {base}/videos/{id}` → `202 {"status":"pending","progress":N}` … →
   `200 {"status":"done","video":{"url":…,"duration":8}}`, then the URL is downloaded.
   (Verified live against `api.linc.id` with `xai/grok-imagine-video`.)
2. When (1) answers `404`, the OpenAI **Sora** contract: `POST {base}/videos` →
   `{"id":"video_…","status":"queued"}`, poll `GET {base}/videos/{id}` until
   `completed`, then `GET {base}/videos/{id}/content` for the bytes.

A gateway that answers the submit synchronously (a URL or inline `b64_json`)
short-circuits polling. Polling defaults to 5s intervals with a 900s ceiling, inside the
1800s chat media-run budget. The API key is sent to the provider host only, never to a
CDN download URL.

**Why it is built this way:** video generation is asynchronous everywhere, and gateways
disagree about the submit path, so the client detects the shape instead of forcing the
owner to configure it. Provider families stay separate modules with shared plumbing
rather than one branching module, so a video change cannot regress the image path.

**Base-URL mistakes fail loudly, not cheerfully.** "Test connection" probes
`GET {base}/models` and now requires a JSON answer: a base URL that already contains
the endpoint path still returns 200-ish HTML from the gateway's web app, and reporting
that as "Endpoint reachable" was a false green on the one mistake owners actually make.
Both media families share this (`media_providers.probe_models_endpoint`), so the same
wrong URL now reads "the endpoint returned an HTML page, not JSON (HTTP 404). Check the
base URL: it must be the API root ... with no endpoint path after it." - in the settings
card and, for a real generation, in the chat reply.

**The `/video` command must reach the backend.** `ChatScreen`'s local slash dispatch
answers "Unknown command" for anything it does not recognise, so media commands are
matched by `MEDIA_COMMAND_RE` and fall through to the server. `/video` shipped broken
against that regex once; `ChatScreen.mediaCommands.test.ts` now pins the list against
`_chat_media_kind` + `ALIASES`.

**Endpoints:** `GET/PUT /api/settings/video-gen`, `POST /api/settings/video-gen/test`.
A configured video endpoint also appears as a Home Connections readiness check
(`auth_health`); an unconfigured one stays silent rather than reporting a false fault.

## 14. Deliverables: the durable record ledger, a tab on Artifacts (T4; merged into one destination by prune Part D, #139; that destination is Artifacts per ADR-0043 - LIVE)

**Why:** deliverables used to exist only as a capped (~40 item) mtime scan - no
memory, no approval state, no trace of which job produced a file. The ledger is
a **registry, not a scanner**: the scanner discovers files, the registry remembers
them as durable records that survive file moves and deletion (a missing file flips
`file_missing` on the record; the record stays). Since #139 there is no separate
Archive destination: the ledger is the **Deliverables tab of the Artifacts
destination** (#144). The gallery tab is the live scan (capped, and labelled as such) and carries a
**deliverable badge** on any scanned artifact the ledger knows (`GET /api/archive/badges?project=...` - the
latest record per path); the **History tab** (`GET /api/archive?missing=1`) lists
records whose file no longer exists on disk - records, not phantom files.

**Record language is container-relative real paths (#139, decision #122):**
`artifact_records.path`, `sessions.produced_artifacts`, and
`messages.output_links` name the same paths the gallery and the file APIs use, resolved
literally from the container root with the authoritative Area assigned by
physical ownership. The record scan is container-rooted through the layout map,
so a mapped artifacts area OUTSIDE the Ops root produces records too (#138's
bridge, resolved). Migration v61 rewrote legacy Ops-relative rows once,
idempotently - approvals, lineage, version chains, and slugs untouched.

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
produced; the record panel in Artifacts edits the SAME field for the late/batch/
supersede cases. Never two separate approval states.

**Registry queries replace the item cap:** paginated newest-first list with
project/type/status/date filters, text search, and facet counts; each record has a
permanent per-project address (`/api/archive/{project}/{slug}` — the UI's
`#archive/<project>/<slug>` permalink, which now opens the record panel inside
Artifacts; old bookmarks keep working, as does a bookmarked `?view=files` URL) with same-path version history plus
project-level newer/older record navigation (by produce date, not version).
Inline and full-record previews cover docs (markdown), pages (HTML iframe),
images/video, and **designs** (first artboard via the same `MiniPreview` thumb as
Design gallery, loaded from `scene.json`). Apps and other types still point at Open.
Chat result cards and the iterate Result view keep using the live scan
(`GET /api/projects/{slug}/artifacts`, unchanged).

**Opening an artifact is a main-window surface, not a popup (#146).** Every open
path - a gallery card or row, the dock browser and task file links through the
one shell seam, a chat result card, a record panel's Open, an `#archive/...`
permalink - lands in the same place, and what the file IS decides which surface
answers:

- **A document you write** (markdown, text, source) opens **directly in the
  editor**, editable from the first frame, with no read-only step in front of
  it: markdown in the wiki's markdown editor (its Preview tab one click away),
  anything else text in the CodeMirror file editor (⌘/Ctrl+S). Both confirm
  before discarding unsaved bytes, and their close control is the way back.
- **Everything else** - images, video, PDFs, HTML pages, and the data documents
  whose rendering is the point (CSV tables, JSON trees, Mermaid diagrams) -
  opens in the **inline viewer** below, which keeps every renderer and ←/→
  walking of the Container's other visual artifacts. A **design**
  opens in the Design Studio canvas where there is one (Work) and is drawn from
  its first artboard on the viewer stage where there is not (Delegate).
  Its **Edit source** hands any text-backed artifact to that same editor; there is
  no door back, because a document's editor already reads it (markdown has its own
  Preview tab).

Both name where the way back leads - **← Gallery**, or **← Record** when the
artifact was opened from a deliverable record. The lightbox is gone: nothing
modal stands between the owner and the file, in Work or in Delegate (which has
no dock and no Design Studio, but the same destination and the same behaviour).

**The viewer is the artifact, full width:** the inline viewer keeps the existing
image, video, PDF, Markdown, HTML, JSON, CSV, and text renderers, and gives all of
them the whole main window. HTML uses the passive sandboxed preview from §11 until
the owner enables trusted active mode for that viewer; a page or a PDF fills the
stage edge to edge, so a desktop layout renders at the width it was designed for
instead of being scrolled sideways inside a column. Unknown, binary, and
directory-like paths immediately show the unsupported preview with a download
action instead of an indefinite loading state. The viewer is a named main-window
region with initial focus on its way back, and returning to the gallery restores
focus to the card or row that opened it; it is not modal, so it never closes on
Escape (its own active-preview consent alert still traps focus and owns that key).

The **review side panel was removed at the owner's request (#148)**: the pins,
point annotations, general-feedback field, and the **Add feedback to chat** handoff
into the producing session are all gone, along with the editor's **Review** action
that reached them. Feedback on an artifact is written in Chat like any other
prompt - the artifact can be @-mentioned - and the window the panel used to hold is
spent on the file. Recorded as an owner refinement in ADR-0043.

Mermaid fences in Markdown and standalone `.mmd` / `.mermaid` artifacts render as
rich diagrams. **Edit as whiteboard** converts supported flowchart, sequence, class,
ER, and state diagrams to native editable Excalidraw elements without leaving
Proxima. The owner explicitly saves the scene under `artifacts/whiteboards/*.excalidraw`
(the project's mapped artifacts folder), a deterministic path derived from the source
document and diagram index, so re-editing the same diagram lands on the same file. If
Mermaid source changes after a saved board exists, the viewer asks whether to keep
edits or rebuild from current source.
This is ArtifactViewer functionality, not a Design Studio canvas path.

**Endpoints:** `GET /api/archive`, `GET /api/archive/{slug}/{record_slug}`,
`POST /api/archive/records/{record_id}/status`.

## 15. Wiki + memory (knowledge)

**Why:** Per-project + global knowledge that compounds across sessions.
**How:** Markdown files in each project's own wiki location (layout-map
resolved, prune C4 - e.g. root `wiki/` or `ops/wiki/`); a built index + tree;
global aggregation. The automatic log/index writers follow the same detected
location and honor the per-project memory-writes toggle (prune C5, §"Identity
from existing docs + adaptive memory writes"). Fed by Chat→Wiki (§5). Opened
from **Settings → Knowledge**
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

A theme is only a variable override, so all six presets share one **semantic
palette**: danger, warning, and success each carry a signal hue (dots, borders,
icons), a tint for the panel it fills, a border, and one or two ink steps for the
text on that tint. The five light-surfaced presets (Light, Ocean, Violet, Sunset,
Forest) inherit that palette from `:root` unchanged - overriding it there would
fork the palette rather than theme it. Dark restates every one of those tokens and
derives its tints by mixing the hue *into* `--ui-surface`, so they follow the dark
surface instead of being pinned to a second hex. Before that (#155) the dark preset
carried only `--ui-warning-text`, so every error/warning/success panel - Master
error banners, toasts, app-runner refusal cards, migration messages, review and
job badges, diff lines - kept the light `#fef2f2`-family tints and rendered as
near-white cards on a dark app. `apps/web/src/theme.tokens.test.ts` fails the
build if the dark preset ever drops one of them again.

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
**How:** Settings → Diagnostics → Debug logs calls `GET /api/debug/logs`. On Linux it
runs `journalctl --user -u <unit>` for the configured systemd unit
(`PROXIMA_SERVICE_NAME`, default `proxima` → `proxima.service`) and lists active/stale
runs and orphaned jobs. Empty journals return a `logHint` naming the unit and how to
point `PROXIMA_SERVICE_NAME` at staging/preview units; the panel head uses correct
singular/plural line counts and shows the unit under the description. The Platform
support panel projects the canonical server catalog from `/api/config`: Linux is
supported, while macOS and Windows are experimental. Non-Linux hosts do not attempt
`journalctl`; they return `serviceManager` / `platformSupport` plus
service-manager-specific experimental guidance instead.
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

### Global web error surface (prune B4)

**Why:** The web app's worst failure was silent. A throw inside an event handler, a
promise nobody awaited, a hashed chunk that 404s after a redeploy, or a fetch swallowed
by an empty `catch` all looked identical to the owner: the click did nothing, with no
message to read or report (evidence issue #115).

**How:** `apps/web/src/lib/errorSurface.ts` is a framework-free store fed from three
places — window `error`, window `unhandledrejection` (both installed in `main.tsx`
before the first render, so boot-time throws still report), and the API client. It
renders through `AppErrorToasts`, mounted beside the app root and **outside** the render
error boundary, so it works on the auth gate, in Delegate mode, and after a crash of the
tree it reports on. Toasts are `alert`-live, dismissible, and carry a `Details`
disclosure with the message plus a bounded stack snippet for pasting into a bug report.

- **Stale chunks:** a failed dynamic import is recognised by message and reported as
  "Proxima was updated" with a **Reload Proxima** button — reloading is the actual fix
  when the tab holds an index pointing at chunks the new build no longer serves.
- **API failures:** `api()` reports only transport failures (no response) and 5xx. 4xx —
  validation, governance refusals, not-found — stays owned by the flow that made the
  call, which already renders its own error state, so the global surface never
  duplicates a visible message. Deliberate `AbortError` cancellations, and requests
  killed by a reload or navigation, are never reported. A later successful response
  retires the "could not reach Proxima" toasts (a condition that has ended must not
  keep shouting) while 5xx and code errors stay until dismissed.
- **Storms:** repeats collapse by identity (kind + message, or method+path+status for
  API failures) into one toast with a `×N` repeat count, and at most three toasts are
  ever visible — a render loop cannot bury the app. Known browser noise
  (`ResizeObserver loop`, opaque cross-origin `Script error.`, aborts) is filtered out so
  the surface stays trustworthy.

Backend-side visibility is separate: journald captures server logs (#123/#125).

### Actionable fail-closed refusals (prune B5)

**Why:** The other half of "errors everywhere with unknown causes" was not breakage at
all. Roughly a third of the friction in the #115 audit was Proxima refusing *correctly*
and never saying what to do about it: `ownership_unknown` rendered a Blocked badge and
the words "the listener lacks complete live managed-lineage proof"; a Master conformance
rejection hid inside a closed dropdown until a send 409'd; a symlink write refusal
reached the Files tree as `Error: Failed to write file (400 Bad Request): …`. Every one
of those decisions is right. None of them told the owner the next move.

**How:** `apps/api/proxima_api/refusals.py` is the single registry of owner-facing next
steps, keyed by refusal code. `refusal_message(code, reason)` appends the step to the
single-string convention (`FsError`, `ContainerBoundaryError`, the preview status
`message`); `refusal_detail(code, reason, **extra)` builds the structured convention
(`{"code", "message", "next_step", …}`) used by `HTTPException` details. So every
refusal the owner meets names three things: **what** was refused, **why**, and **the
concrete next step**. `apps/api/tests/test_refusals.py` asserts every registered step is
an imperative sentence, so a new fail-closed state cannot ship with a dead-end message.

Wired refusals: preview port conflict and unverified port ownership and a dead preview
output sink (`apprunner`, plus `AppStatusResponse.next_step`); realpath-jail escape,
symlink traversal, oversized and non-text file opens (`fsapi`, `file_targets`); symlinked or moved
Container root, changed root identity, symlinked or missing Ops folder, missing Ops Area
(`container_registry`, `layout_map`); Master runner conformance (`routes/master.py`,
`master_runtime.py`); and a blocked project purge (`route_deps`).

On the web, `apps/web/src/lib/refusal.ts` renders the contract: `splitRefusal` separates
the diagnosis from the instruction so a screen styles them apart without printing the
step twice, and `refusalText` recovers the server's sentence from the transport wrapper a
thrown client error carries. `AppRunner` gives the next step its own line in every
fail-closed card (and `AppViewport` repeats it where the app itself would be), `WorkspaceTree` shows the refused write's real sentence, `MasterScreen`
raises a visible alert when the backing runner is not eligible, and `api/client.ts` keeps
the instruction last in a flattened structured refusal (and exposes it as
`ApiError.nextStep`).

**What did not change:** not one refusal was softened. This is wording and delivery only.
Refusals a *runner* sees (the Master tool broker, the model-provider proxy) stay terse by
design — telling a possibly prompt-injected agent how to get past a boundary is the
opposite of the goal (see [prompt-injection-hardening.md](prompt-injection-hardening.md)).

Linux daily-driver reliability is release-gated by
`scripts/linux-daily-driver-acceptance`. The executable matrix covers install,
service status/restart/stop, POSIX PTY behavior, online backup and isolated restore,
diagnostics, preview, local and synthetic Tailscale HTTPS entry, and fail-closed
upgrade readiness. All cases use temporary roots, fake managers, or loopback
fixtures. Master is enabled inside the
acceptance process. See
[Linux Daily-Driver Acceptance](linux-daily-driver-acceptance.md) and
[ADR-0028](adr/0028-linux-first-daily-driver-support.md).

## 21. Updates (release check only)

**Why:** Owners should see release availability without letting the running
application promote itself.
**How:** `UpdateManager` may check GitHub release metadata every 6h, but its old
live-checkout apply route and `proxima update` are inert: updating is a manual
`git pull` plus a service restart. The former safe-self-update stack (external
updater authority, candidate sandbox, maintenance fence, ingress leases) was
removed entirely (prune A1, ADR-0041; ADR-0008 superseded).

The update modal only reports availability; manual promotion remains unavailable.

**Endpoints:** `GET /api/update/status`, `POST /api/update/check`,
`POST /api/update/apply` (inert).

---

## Removed (was multi-user, now single-user)

In-app user accounts, roles (`environment_admin`/`member`), multi-user login,
team bootstrap, invite links, project membership/sharing, project
visibility (private/shared), team name. Collaboration model is instead: **everyone
self-hosts their own instance + shares folders/repos.** The runtime model is one
owner with one password/session gate.

Nothing of that surface is left behind (prune A4, #128): the `invites` and
`project_members` tables were dropped by migrations v57/v61, `users.role` by
v62, and the role indirection in the API - the `admin_user` dependency, the
always-true `_can_access` check, and the `_member_project_id` resolver - is
gone. The owner payload (`GET /api/me`, the login/set-password responses)
carries `id`, `username`, and `os_user` only; the project payload no longer
carries a constant `role: "owner"`; and `GET /api/setup/status` returns
`password_set`, `hermes_profiles_root`, and `runners` without the old
`bootstrap_required` / `single_user` / `mode` mode-selector fields (there is no
other mode to select). Auth itself is unchanged: owner password, bearer token
or the HttpOnly `proxima_session` cookie.

## Single-workspace shell ("Deck", T3)

+ **One workspace, no Ops/Code switch.** The header has a URL-durable **Work / Delegate** mode control. Work keeps the flow-ordered destinations Chat, Tasks, Workflows, Artifacts, Design, **Inbox**, and project-scoped recent chats; its sidebar owns the active-project switcher and the top bar does not. Delegate keeps that same persistent, collapsible sidebar and header language, but replaces Work navigation with global Master, Tasks, Artifacts, and Inbox (ADR-0043, replacing the Files destination of ADR-0040: Artifacts is a destination in both modes - a produced-work gallery with All / Deliverables / History tabs, Work-scoped to the active Container and Delegate-global behind a head filter, artifacts open in the main window - documents in the editor, everything else in the inline viewer, #146). It keeps the global header status cluster (`N tasks running` + Needs-you), since watching delegated work is the point of the mode; opening a Work-only target from there switches back to Work first. It has no project selector, project filter menu, ordinary Chat, Workflows, Design, tools, search, or popup surfaces; the account menu stays - it is the only route to Projects, Agents, Settings, and Log out - and each of those entries switches back to Work before opening; its Tasks and Artifacts views query across projects and their task and record deep links remain in Delegate. Opening a graph plan explicitly returns to Work, and Task workspace Design actions remain unavailable in Delegate. There is no primary-nav **New chat** twin and no primary-nav **Projects** row. **Chrome Back** is always visible in **both** modes (disabled without a deep stack) and returns to the origin surface - Delegate reaches deep surfaces of its own, and its record panel has no in-page Back to fall back on (#151); deep views lock the project switcher. Workflows home and open-plan header do not dump project display names (lock is icon + tooltip only). Chat stays mounted when leaving so draft + in-flight run re-attach; Work Chat reload durability is under Chat above. Work/Chat is the default. Agents and Settings live in the Work profile menu; Wiki lives under Settings → Knowledge. Running work is a text pill (`N tasks running`) hidden when idle. Collapsed, the sidebar becomes a **rail** of equal square tiles whose width is derived from the tile size and gutter (#153): the eyebrow drops, the project switcher becomes an initial tile with the full name on its title and popover, destination labels return as hover tooltips, and the update pill keeps its dot - nothing wraps, and active and inactive tiles share one footprint. Collapse and drag-resize are properties of the sidebar, not of a mode, so **both** navigations carry the same toggle, the same handle, and the same rail (#154). See [UI shell](ui-shell.md#collapsed-left-rail).
+ **Chat** is the front door: brainstorm, then **Slice into plan** promotes the conversation into a runnable plan. Its header carries the session and agent; Work-sidebar project context remains outside the conversation. Its **New chat** action clears the active session (mobile topbar keeps a compact icon; `/new` remains a power-user path); the chat remains lazily created on first send.
+ **Master** is the gated delegation/monitoring peer to Chat: one hidden system identity, a schema-validated filesystem-isolated product broker, chat-only runner conformance, three honest worker slots, active queue, needs-you subset, job checkpoints, and an opt-in budgeted unattended toggle. The flag defaults on, and unattended starts stay opt-in behind their own toggle; dynamically conforming Codex 0.145.0 or newer is supported, and every other or unavailable adapter fails closed.
+ **Tasks** is the permanent execution/review index; its `+ New task` button opens the launcher - a single integrated Task Composer with searchable Project/folder context, selected Agent, a combined Add menu for attachments/image/video/design, and Guarded or Autonomous execution policy. It creates a durable ad-hoc job and opens a dedicated hash-addressable task workspace with live progress, review, approval, and deliverables. The linked execution session is not a visible chat conversation.
+ The single **Workflows** destination contains a remembered Drafts / Workflows / Runs library home and the plan Editor (graph canvas). One reusable-workflow table shows workflow Availability separately from the joined schedule summary. Every row retains Edit, manual Run, Schedules, availability pause/resume, and archive actions. The schedule dialog owns timezone, five-field cron, durable input bindings, overlap, per-schedule On/Off, Run now, configure, and delete behavior. The graph is enabled by default; its flag is a recovery switch rather than a hidden experimental mode.
+ **Right tool rail** (`ToolDock`): Terminal, Files, and Preview open as overlay panels above the current screen, project-scoped when Project context is synchronized; the rail and panels stay suppressed during Task permalink resolution or any Task/Work Project mismatch. Browsing left the rail for a destination in ADR-0040 and came back to it in #145, so no navigation offers a Files destination and a bookmarked `?view=files` URL lands on Artifacts. The rail's gear opens Settings and Escape closes the panel. The panel's tab row is its only close affordance - all three tools alike (#161). The rail collapses from the header with the sidebar toggle's mirrored twin, persisted in `proxima.dockCollapsed` and Work-only; collapsing closes an open panel but keeps latched tools running, and a reveal or run-controls request still opens its tool and brings the rail back (#160). At phone width there is no rail at all: the tools open as a full-screen sheet from the mobile top bar, so the surface behind keeps the whole 390px (#156). Terminal and Files stay mounted after first open (shells survive a closed panel, and the tree keeps its place); Preview unmounts because its dev server is a backend process. Agent outputs live on Artifacts (the gallery plus the Deliverables tab, #139/#144); Design remains a separate canvas destination.
+ **De-jargon rule:** primary surfaces say "agent" and "tools" — never "runner", "MCP", "profile", env-var names, or raw stack traces. That detail lives in Settings → Agents and the docs.

Authentication remains single-owner defense in depth: first run sets a password, later requests require a bearer token or `proxima_session` HttpOnly cookie, login establishes the session, and resume restores it. Each invalid attempt focuses the corrective field and mounts one fresh assertive alert, even when the same values are submitted again. The gate keeps one main landmark, password-manager-compatible hidden owner metadata, and token-based text and focus contrast across every canonical theme.
