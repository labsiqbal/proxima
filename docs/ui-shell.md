# UI shell and information architecture

This is the durable contract for Proxima's application shell. It describes product routing and lifecycle boundaries; API and database details remain in the generated reference docs.

## Information architecture

There is **one workspace** with two intentional modes. **Work** is the ordinary
workspace; **Delegate** is the focused Master desk. The old Ops/Code split is gone.
The desktop shell has a persisted, collapsible left navigation and a destination
work surface. Work also adds a slim right tool rail whose tools open as overlays:

- **left navigation** ordered by the mode's flow,
- the **destination work surface** in the center,
- Work-only **right tool rail** overlay panels.

The header-level **Work / Delegate** control is URL durable (`?mode=work` or
`?mode=delegate`) and uses pressed-button semantics. Work navigation is flow-ordered:
**Chat** (hands-on), **Tasks** (watch it run), **Workflows** (keep what worked), then
**Archive** (where deliverables live), plus feature-gated **Design**. The active-project
switcher belongs to the Work sidebar, not global chrome or Master. Project management
(list / link / create / remove / container settings) lives under **Settings → Projects**,
not primary nav. Agents and Settings stay in the Work account menu. The default landing
mode and surface are Work and Chat. Delegate uses the same header and left-panel
geometry but its global navigation is only **Master**, **Tasks**, and **Archive**.

## Chat — the front door

Chat is the conversational surface where work begins: brainstorm until the scope is clear, then promote the conversation with **Slice into plan**, which drafts a plan (a DAG of jobs) and opens it in the editor. The left nav lists destinations only (**Chat**, **Master**, **Tasks**, …); it does not carry a separate **New chat** row. A blank session is started from the **Chat** header control (compact icon on the mobile topbar), or via `/new`. The database session is created lazily on the first message; recent chats appear under the nav once a thread exists.

A workflow's iteration thread is not an ordinary chat: the nav attributes it to Workflows, and picking Chat while one is open switches to a plain conversation instead.

A file-changing assistant turn carries a **Restore changed paths** control. It first
opens a path impact preview and asks for confirmation; active Master work in the same
project adds a warning. The journal belongs to that chat session and disappears with it.

## Master

Master navigation, settings, tours, and deep links are omitted unless the
server-owned `feature_master_orchestrator` flag is enabled. It defaults off
until the
[documented product and installation-specific runner gates](master-integrated-acceptance.md#activation-decision)
pass. Stale local view state cannot bypass this gate.

Delegate presents Master as a first-class desk, not a Chat tab or Tasks filter. It keeps
the shared, persisted sidebar panel but replaces Work navigation with **Master**,
**Tasks**, and **Archive**. Those destinations are global: they do not show a project
switcher, recent chat history, project filter menu, account controls, ordinary Work Chat,
Workflows, Design, search, tool rail, or popup. Tasks and Archive query across projects;
their task and record deep links remain usable without leaving Delegate. Switching back
restores the prior Work surface. Its header identifies
the built-in system orchestrator and lets the owner choose a server-qualified backing runner; the desk
itself keeps the counterpart label **Master** and does not expose a fake worker profile.
A compact capacity strip always states running/free out of three, queued count, and the
saved unattended budgets. One authenticated provider owns the Master session, durable
thread, live SSE cursor, reconnect state, unread count, composer draft/selection,
Focus, target, selected history projection, popup, toast queue, and scroll anchor
across navigation. The full-page home and floating popup are two
views of that provider, so only one composer and one live connection exist. The main
column is the Master thread plus one shared **Chat composer** stack (attach + `@`
project file/artifact mentions) wired to Master's send API rather than a normal
chat run; attachment context comes from an active Master job when one is available,
not a Work project selection. The side column groups queued, running, review/attention, completed,
and failed Master-owned Tasks, then owner decisions and a job-scoped checkpoint
timeline. **Fleet work**, **Decisions**, and **Safety** are independent native
accordions, open by default. Each list reserves a three-row viewport and scrolls
internally for remaining entries, so the desk remains scannable without hiding its
information rail. The Delegate sidebar stays visible at desktop widths; it has no
hide/collapse or resize control. Idle, loading, failure/retry, populated, and
in-flight states retain the same geometry. On narrow screens the side column stacks
after the thread with no horizontal scroll.

Opening a graph plan from the global Tasks index is an explicit transition back to Work,
because its editor belongs to Workflows. Task workspace Design actions are unavailable
while Delegate is active, so neither path embeds a Workflows or Design surface in the
Delegate shell.

The provider uses the existing Master session SSE stream as its only live path.
Reconnect or a detected cursor gap triggers one authoritative reconciliation; there
is no five-second Master polling loop. Its draft, selection, and scroll anchor survive
route changes and browser refresh through owner-keyed session storage; the target
remains an owner-keyed local preference. The header New Task control and Tasks →
New task entry both seed this same composer while Master is enabled. The legacy
standalone Task launcher remains a feature-off compatibility path only. Changing
the shell Container affects attachment and mention context but never silently
changes Master Focus.

The composer defaults to **Let Master route** and can explicitly target a registered
Container. An advanced Area override appears only after a Container is explicit.
Sent messages retain visible target metadata. If an explicit target differs from
Master Focus, the composer warns that sending will change Focus, and the server
records that Focus before enqueueing. The home Focus picker changes only Master
Focus, never the shell Container. The adjacent History picker projects the one
canonical thread into Roving, Fleet, and per-Container folders. Selecting an
available Fleet or Container folder explicitly requests that Focus; Roving and
unavailable historical folders are read-only. **Focus Master here** is the only
bridge from the shell Container into Master Focus.

Master product-tool outcomes appear as concise, collapsed **Master update**
disclosures. The visible line is a human-readable success, failure, or in-progress
summary; opening it exposes linked Tasks, error context, and the bounded raw payload
needed for audit. Raw tool JSON never takes over the conversation by default.

Normal authenticated surfaces show a labeled floating Master trigger with the
`Ctrl`/`Command` + `Shift` + `M` shortcut. The popup persists at the bottom-left or
bottom-right, avoids shell tools and safe areas, and becomes a sheet on narrow
screens. It traps focus, closes with Escape, and returns focus to its trigger.
Auth, onboarding, the full Master home, update application, drawers, search,
account menus, and ToolDock panels suppress it. Durable conversation messages stay
the completion/failure/review/Attention/Satpam truth; named durable transitions may
also show one coalesced, keyboard-dismissible toast that never steals focus. Raw
token, reasoning, and tool deltas never produce toasts.

**Unattended** is a quick pressed toggle on the desk. Off means Master never starts work
without an owner turn. On means the server may start already-queued Master jobs until the
saved turn/wall budget stops cleanly; numeric limits live under Settings → Master and
remain readable on the desk. Satpam, not Master, owns stuck-job steer/restart.

## Tasks

Tasks is the durable execution/review index for queued, running, review, done, failed, and archived work — plans and one-off tasks together. Plan rows expand into their ordered job list; branching plans also offer the List↔Graph projection toggle. A repo job (one that worked in an isolated copy of a code area) reviews its **Changes** in place — inside a plan row's expanding body, or on the full-width task page — with approve-and-merge and reject-with-reason as the two verdict doors; per T4 there is no right panel and no popup, and the copy stays jargon-free.

The **New task** launcher lives behind the Tasks screen's `+ New task` button (it is no longer a nav destination of its own). It is a focused launcher with no destination dashboard grid. Its integrated Task Composer splits into two rows by kind. The prompt row carries only *actions*: the Add menu for attachments/image/design, and the start action. A context bar underneath groups the three controls that describe a task's **execution context** — a searchable Project/folder picker (where it runs), Agent (who runs it), and Guarded or Autonomous execution policy (how it is governed). Each context control carries a leading icon inside its own click target and all three share one type scale, so the bar reads as one row of peers rather than three unrelated widgets. `/image` and feature-gated `/design` create real media runs that are linked back to the durable task lifecycle. A created task opens `#task/<id>` with live progress, review, approval, and deliverables. Ordinary start failures clean up the queued task; media link failures preserve and identify the task for inspection.

## Workflows

Workflows is the template library for repeatable work. One workflow owns one project (the shell project filter on the library home does not rebind an open plan or template). The screen has a browsable home and a focused editor:

- **Home** remembers the last selected **Drafts**, **Workflows**, or **Runs** tab and
  renders each collection as a table so long libraries remain scannable. Workflow rows
  are split into **Manual (on-demand)** and **Scheduled** groups derived from
  real schedule rows. Manual workflows can be edited or run; scheduled workflows can
  be edited, rescheduled, paused, or resumed. The per-row Schedule action opens the
  complete schedule form in a dialog, including create, enable, overlap, input, Run now,
  and delete controls. A manual row also exposes Schedule so its first schedule can be
  created. There is no standalone Scheduled navigation mode.
- **Editor** is the plan/graph canvas. `PROXIMA_FEATURE_WORKFLOW_GRAPH` defaults on;
  with the recovery switch off the mode explains that the editor is off (the env var
  itself stays out of the UI copy — it is documented here and in installation docs).
  It has an **authoring chat** on the left under the standing rule — typing drives the
  plan on screen, never the database — which hands back a `<workflow-graph>` (nodes +
  edges), so the agent can propose branches rather than a straight line. The chat is
  pinned to the graph job's own session, so reopening a plan resumes its conversation.
  The editor is **canvas-first**: node-level actions stay with the node; the plan list
  collapses; and the node inspector exists only while a node is selected.

The **Sequential recipe editor is retired**: a linear recipe is a graph with no branches,
and the canvas authors those too. The linear *engine* remains for pre-existing jobs and
sessions (`IterateStage` is still reachable from an old session carrying `workflow_id`),
but no new linear workflow can be authored.

The library separates active workflows from an **Archived** view. Archive stops
schedules but preserves the workflow's project ownership and frozen past runs. Restore
returns that same workflow to the active library. Permanent deletion is available from
the archived view so it cannot be confused with the reversible action.

Schedule inputs mirror each workflow's declared definitions, validate required values, and serialize values by declared input ID. Project is derived from the saved workflow (locked / display-only on the schedule form). Workflows without declarations may receive an optional `brief`. Cron accepts exactly five fields using numbers, `*`, positive steps, ranges, and comma-separated parts within valid bounds.

Every schedule row offers **Run now**, which fires it immediately and opens the task it spawned. It exists so a schedule can be trusted before it is left alone: the run goes through the scheduler's own spawn, so what executes is what the cron would have executed — same recipe, project, agent profile and stored input — rather than a lookalike. A manual run deliberately does **not** claim the scheduler's minute, and it works on a disabled schedule, since `enabled` governs the tick and trying a schedule out is exactly when it is still switched off. The stored overlap policy is honoured but never silently: a `skip` schedule with a run already in flight reports that instead of appearing to do nothing.

## Right tool rail — Terminal, Files, Preview

Terminal, Files, and Preview are **tools, not destinations**. A slim icon rail on the right edge opens each as an overlay panel (`ToolDock`) above the current screen, in any context, scoped to the active project:

- **Terminal** — the multi-tab PTY terminal. Once opened it stays mounted (hidden when
  the panel closes) so shells survive closing the panel and navigating anywhere.
- **Files** — the shared workspace tree over the project root, with the inline
  CodeMirror editor. Also kept mounted after first open so unsaved edits survive a
  closed panel.
- **Preview** — the Run & Preview dev-server dock (`AppRunner`). Not kept mounted:
  its server is a managed backend process that survives on its own, and unmounting
  stops the status polling. The Archive and the recipe test bench keep their own
  Preview entry points for app-type artifacts.

The rail's bottom gear opens Settings. Escape closes the panel. The rail persists at mobile widths (fixed to the right edge below the mobile top bar), so every tool stays reachable on a phone.

## Chrome Back, project lock, and multitask keep-alive

The shell header (and mobile topbar) always show a **chrome Back** control (Chrome-like):
visible even when disabled. It is **disabled** with an empty deep stack, and **enabled**
when the owner is inside a deep surface. Enabled Back returns to the **origin** surface
where that deep view was entered — not only a hard-coded canonical parent. Deep frames
include task detail, workflow graph editor, archive full record, design canvas, and
settings stack when applicable. In-page Back buttons on those surfaces are removed so
chrome owns the action.

**Deep = project lock:** while a deep surface is open, the header/mobile **Project**
switcher is disabled (locked). On top-level surfaces the switcher stays enabled and
changing project **stays on the current view** (refilters content; does not force Chat).

**Multitask foundation:** primary surfaces must not destroy in-flight UI on leave.
Once visited, **Chat, Master, Tasks, Workflows, Archive, and Design** stay mounted in
hidden `surface-pane`s so draft text, open panels, canvas/plan state, and in-flight
runs re-attach when the owner returns in the same browser session. Server work continues
regardless; the client contract is keep-alive / re-attach, not remount-from-zero.

**Teaching empty states:** top-level empties share one grammar — title, what the surface
can do, short tutorial steps, and one primary CTA where it applies (Chat, Master, Tasks,
Workflows library context, Archive, Design home). Help/core tour nouns match the primary
loop **Chat → Tasks → Workflows → Archive**, with Master as the delegate side path.

**Workflow how-it-runs:** library table rows show Manual or Scheduled badges derived from
real schedule rows (optional short cron text). Schedule forms lock project to the workflow
owner — no free rebinding. Open deliverables from Chat/Tasks/Archive use the same
in-app **ArtifactViewer** for supported types. Unsupported binary or directory-like
paths show a download fallback immediately rather than remaining in a loading state.

## Global attention, running work, and account surfaces

The shell-level **Attention** badge persists across destinations and polls one unified
shape. Every item is a real button that deep-links to the owning Master/Task/plan/Settings
surface. Only server-marked `inline_ok` binary actions render beside the link; diff
review and open-text decisions navigate instead. The popover has loading, empty,
populated, and persistent retryable-error states, closes on Escape/outside click, and
becomes a viewport-bounded sheet on narrow screens.

Next to Attention, a **Running** text pill polls `GET /api/runs/active` plus running jobs
and shows only while work is in flight (`1 task running` / `N tasks running`; mobile may
shorten to `N running`). When the count is zero the control is hidden entirely (quiet
header). The popover lists de-duplicated tasks and chat sessions with deep-links (task
workspace / chat / Tasks index), matching Attention's open/refresh/empty/error affordances.
Attention stays a separate `!` control and remains hidden when empty.

Agents and Settings live in the Work profile/account menu rather than the navigation. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge, including files, links, graph, and search. Settings sections are grouped for scan with short title-only nav rows under group eyebrows: **Work setup** (Projects, Agents, Master, Knowledge) · **Integrations** (Media, Remote) · **System** (Account, Diagnostics) · **Help**; full hints live on tooltips and aria. Editable panels surface clear save success/error (no silent fail). Help owns a replayable core tour (primary loop + Master side path) plus feature-aware product-map chapters. The first post-setup main UI shows the core tour once; it traps keyboard focus, supports Escape/skip, and stores completion server-side. The Work top bar owns the brand mark, mode switch, sidebar collapse toggle, search, Running + Attention, and account menu; its sidebar owns the active-project switcher. On mobile that switcher stays in the Work drawer and the mode control remains in the compact header. Global search includes user-facing Chat and Design sessions but excludes Master's hidden system thread, so raw product-tool calls and tool-result payloads never become search results.

Projects remain shared application entities: one active project across Work (`activeProject`). Work surfaces that already filter / default-attach / list by active project (Chat, Workflows library, Archive, Design) keep that contract. The Work-sidebar project switcher changes only that shell filter (and the coherent recent chat session for when Chat is opened later) - it does **not** navigate to Chat. Search (and similar intentional open paths) may still open a project's chat. Opening a workflow/plan still uses that workflow's owned project; the Work switcher does **not** rebind an open workflow instance to another project. Workflows library home has no second project dropdown and does not dump project display names (open-plan header uses a name-free lock icon). The switcher menu offers Rename (alongside Settings → Projects). Archive records and Designs remain owned by their Project. Delegate has no project selector or project filter: its Tasks and Archive indices are global, while Master Focus and explicit target controls remain its own bounded context.

## Projects

Project **management** is a **card grid** under Settings → Projects (not a primary-nav
destination). A project carries a name and a slug, which is not enough to earn a
permanent detail panel. It reuses the same shell as the shared list shell
(`.tasks-view` + `.tasks-head` + `.wf-grid`/`.wf-card`): search on the left of the bar,
**Add project** on the right, one card per project. Deep links / `view === 'projects'`
open Settings on the Projects section.

A card shows the name and slug, marks the **active** project (the one the rest of the app
is pointed at), and carries its own actions: the card body selects it, **Rename** opens a
prompt dialog, and the hover/focus **×** removes it. Add opens a modal holding both ways in:
create a new project, or point Proxima at a folder on disk - link one you already work
in, or create a new empty one under a parent you pick.

The link/create choice is an ordinary labeled button group. Each button exposes
`aria-pressed`, and both remain in normal Tab order with Enter and Space activation.
Folder and display-name validation publishes one assertive alert only after focus has
moved to the marked corrective target. The alert is the only semantic announcement owner;
the focused target remains invalid without repeating the message as its description.
Every attempt mounts a fresh alert, including an unchanged assistive or keyboard
resubmission while focus stays on the target. Display names are checked against the API's
120-character limit before a link or create request. Structured project-link errors
retain selected `path`/`parent`, child `folder`, and display `name`/`slug` ownership
through the API client. Child-name failures focus the folder-name field, name/slug
failures focus the display-name field, and parent or link-path failures focus a
selected-folder refresh control. Refresh chooses the nearest actually readable ancestor
within the owning allowed root. Resolution and containment fail closed around self or
mutual symlink cycles. If initial browsing fails, a marked retry control is already
mounted and receives focus before the single alert appears. Failed configured roots
retain raw ownership even when expansion fails, valid sibling roots remain available,
and later resolution failure cannot move a selection into a containing root. Browse
responses carry an opaque configured-root ID through each later navigation and
link/create request, preserving symlink-alias ownership after the API returns a canonical
path. Every later request without that ID fails closed.
New-folder validation uses the target filesystem's encoded component-byte limit. The API
then traverses from the verified root one POSIX no-follow descriptor or Windows
no-reparse native handle at a time. It creates under an unguessable staging name, pins
the directory's platform identity, atomically publishes without replacement, persists
that identity with the Project, and verifies the final path through the configured root.
Rollback removes only the pinned directory, including after a rename, and leaves any
replacement untouched. Component or encoding failures return to the folder-name field,
while parent traversal, identity, or location failures return to the selected-folder
control. If no ancestor is readable, the current selection stays explicitly invalid
until the owner retries or chooses another folder.
Later Container filesystem access compares the stored identity and rejects a replacement
at the same path. Readable legacy Project rows receive their current platform identity
at startup; unreachable legacy paths receive a fail-closed unavailable marker.

Removal copy must distinguish the two cases, because the API does: a folder outside the
workspace root is only *unlinked* and its real files survive, while a project Proxima
created is deleted from disk. Chats and tasks go in both cases.

## Archive and Design

Archive is the durable deliverable registry (T4): every agent output lands as a record with lineage, ONE approval status (synced with the job-review approve), and a version chain; the combo detail is an expanding row plus a full record page at a permanent `#archive/<project>/<slug>` address - no right panel, no popup. Records survive file moves and deletion. Design is a separate canvas destination whose internals are not part of the shell. Design links are enabled only when the Design Studio feature gate is on; otherwise source artifacts remain available.

## De-jargon rule for primary surfaces

Primary screens (Chat, Tasks, Workflows, Archive, the task workspace, the shell itself) never show the words "runner", "MCP", or "profile", env-var names, raw tool payloads, or raw stack traces. The plain words are **agent** and **tools**. Technical detail belongs to Settings, Agents, and docs. Master has one deliberate product-contract exception: its header says **Backing runner** because the owner explicitly chooses a server-qualified runner for the system identity; tool results render as flat timeline text (with plain job links when present), not raw JSON or card chrome. The qualification contract is owned by [Runner conformance](runner-conformance.md).

## Feature gates

Routes, sidebar destinations, session eligibility, search, and deep links must all honor the server feature configuration. A hidden destination must not become reachable through stale state. Gating must not reorder the remaining navigation.
The Master gate suppresses the Delegate control, its settings section, help chapter, and
delegation step in the general core tour. A stale `?mode=delegate` URL falls back to Work.

## Responsive and accessibility behavior

The left navigation width persists locally in both modes. Its separator supports pointer input and keyboard Arrow keys and exposes vertical separator orientation plus minimum, maximum, and current values. At mobile widths navigation uses the same focus-managed drawer in both modes; Work's tool rail pins to the right edge, while Delegate keeps its global Master, Tasks, and Archive navigation. The Task Composer and Master controls stack without changing semantics. Account actions use ordinary disclosure/popover semantics in Work. Escape dismisses transient Work overlays (including the tool panel, Attention, and Master popup); modal overlays trap focus until dismissed. Focus indicators use shared tokens, toast live priority matches urgency, and reduced-motion preferences apply globally.

The setup and returning-owner password gates each expose exactly one `main` landmark.
Password fields have stable accessible names and password-manager autocomplete values,
with a hidden, read-only `owner` username field that does not create an account model.
Validation focuses the marked password field before publishing its single assertive
alert. Every validation attempt mounts a fresh alert instance, including an unchanged
repeat while focus stays on the corrective field. That alert is the sole semantic
announcement owner; the invalid field does not duplicate it as an accessible
description. Auth and onboarding text, errors,
primary controls, placeholders, entered values, and focus indicators use central theme
tokens that maintain WCAG AA contrast in every supported theme.

## Extension points

Add destinations through the existing `View`, feature policy, App routing, Sidebar, and SearchModal boundaries together. Every new destination must declare whether it belongs to the flow navigation or the global account layer; new tools belong on the rail, not in the nav. Destination-specific inspectors remain owned by their destination rather than the application shell.

## Validation

For shell changes, run `npm --prefix apps/web test`,
`npm --prefix apps/web run build`, and `git diff --check`. Tests should cover
navigation order and feature-off gating, tool-rail open/close with Terminal
persistence, asynchronous task success/failure, declared schedule inputs, cron
grammar, and keyboard resizing.

`npm --prefix apps/web run test:accessibility` first runs focused project-link API
regressions for corrective ownership, filesystem component-byte limits, encoding
failures, readable-ancestor selection, symlink-cycle handling, explicit no-ancestor
failure, and configured-root jailing. It then runs the password and folder flows in a
disposable real-browser fixture, records accessibility trees, genuine Enter/Space
activation, every supported theme, Lighthouse, and
[screenshot evidence](evidence/auth-onboarding-accessibility/README.md) without
touching live data. The allowlisted child environment redirects every writable Proxima
path into the fixture and disables workers, credential refresh, update checks, preview
relays, and external graph egress. Its theme matrix checks title, subtitle, entered
value, placeholder, error, button, input focus, and button focus styles against the
rendered backgrounds for every canonical theme.

The harness discovers the current root Tailscale Serve entry that proxies to loopback
port 8765. Any `PROXIMA_A11Y_REMOTE_BASE` or
`PROXIMA_A11Y_REMOTE_ADDRESS` override must match that current device and Serve
mapping. The remote browser pass uses a fresh profile for each origin and auto-attaches
to the page plus every related service, shared, and nested worker before they run.
Every session installs Fetch and a Network/WebSocket block before resume. A service
worker without the CDP Network domain remains paused until its served bytes match the
locally audited duplex-free artifact, with Fetch still intercepting every request. One
explicit unauthenticated read-only `/sw.js` GET supplies that proof.
One secured session owns each target's accounting; a secured duplicate is promoted on
detach, while losing the last owner before audited closure closes the target and fails
the pass. Stable target/network IDs deduplicate extra session observations.

Service workers are restricted to same-origin `/sw.js`. Policy remains active through
assertions, screenshots, and page/worker closure. A secure disposable production
origin compares the complete resulting Cache Storage key set with `APP_SHELL` and
accounts for the service-worker artifact-proof GET separately, even when the current
private entry is development-served. For a Vite entry, `/@vite/client` is fulfilled
with an inert no-socket compatibility shim that preserves rendering and stylesheet
loading. Each pass requires exactly one page navigation GET, accounts for every worker
shell GET by target and path, forwards only allowlisted same-origin static assets,
fulfills config, setup status, failed session resume, and the optional inert Vite
client inside the browser fixture, and blocks or rejects every other API, auth,
cross-origin, non-static, and duplex request. Attempted WebSocket connections and
blocked failures are counted without retaining their private URLs; any outbound
handshake or frame fails the audit. It never logs in and retains only a redacted origin
label, pass state, exact request counts, Vite fixture state, and redacted current-device
Serve provenance. Browser QA should also check authenticated desktop and narrow
layouts, zoom, and reduced motion; if remote authentication prevents inspection,
record that rather than using credentials.
