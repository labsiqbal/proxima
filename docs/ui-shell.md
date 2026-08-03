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
**Artifacts** (what came out - the gallery of produced work and, through its
Deliverables tab, the record ledger), plus **Design**. The active-project
switcher belongs to the Work sidebar, not global chrome or Master. Project management
(list / link / create / remove / container settings) lives under **Settings → Projects**,
not primary nav. Agents and Settings stay in the Work account menu. The default landing
mode and surface are Work and Chat. Delegate uses the same header and left-panel
geometry but its global navigation is only **Master**, **Tasks**, and **Artifacts**.

Artifacts is a destination in both navigations (ADR-0043, replacing the Files
destination of ADR-0040): Work scopes it to the active Container, Delegate
gathers every Container behind a head filter, and in both an opened artifact
takes over that main window - documents in the editor, everything else in the
inline viewer (#146, see "Artifacts and the deliverable ledger" below). Since prune Part D (#139) the separate Archive destination is
gone: the deliverable ledger lives here as the **Deliverables** and **History**
tabs (see "Artifacts and the deliverable ledger" below). Terminal, **Files**
(the real-disk tree browser), and Preview are the right-rail tools: browsing
came back to the rail in #145, so no navigation offers a Files destination and a
bookmarked `?view=files` URL lands on Artifacts.

Every Work destination has a stable history entry. Its URL records the Work mode,
active project, active Chat session, primary surface, and the open Workflow or Design
identity when one is focused. Full reload, an installed-PWA restart, and native browser
Back/Forward restore that validated context. A missing project or session falls back to
an available project and one of its sessions; it never reuses another project's draft.

## Chat — the front door

Chat is the conversational surface where work begins: brainstorm until the scope is clear, then promote the conversation with **Slice into plan**, which drafts a plan (a DAG of jobs) and opens it in the editor. The left nav lists destinations only (**Chat**, **Master**, **Tasks**, …); it does not carry a separate **New chat** row. A blank session is started from the **Chat** header control (compact icon on the mobile topbar), or via `/new`. The database session is created lazily on the first message; recent chats appear under the nav once a thread exists.

A workflow's iteration thread is not an ordinary chat: the nav attributes it to Workflows, and picking Chat while one is open switches to a plain conversation instead.

One owner-scoped Work Chat provider remains mounted while the shell moves between
Work destinations or Delegate. It stores state per project and session: the unsent
draft, selection, composer mode, safe pending attachment references, and thread scroll
anchor. These values survive reload and an installed-PWA restart. Deleted projects are
pruned explicitly, and a fallback session reads only its own project-scoped state.

A file-changing assistant turn carries a **Restore changed paths** control. It first
opens a path impact preview and asks for confirmation; active Master work in the same
project adds a warning. The journal belongs to that chat session and disappears with it.

## Master

Master navigation, settings, tours, and deep links are always present (the
feature-flag system was removed in prune A2, #129). The header status cluster
(Running tasks + Attention) renders in both modes; its Work-only targets switch
the shell back to Work before opening.

Delegate presents Master as a first-class desk, not a Chat tab or Tasks filter. It keeps
the shared, persisted sidebar panel but replaces Work navigation with **Master**,
**Tasks**, **Artifacts**, and **Inbox**. Those destinations are global: they do not show
a project switcher, recent chat history, project filter menu, ordinary Work Chat,
Workflows, Design, search, tool rail, or popup. The account menu stays - it is the
only route to Projects, Agents, Settings, and Log out - and each of those entries
switches the shell back to Work before opening. Tasks and Artifacts query across projects;
their rows and cards visibly and accessibly name the owning Project, and their task
and record deep links remain usable without leaving Delegate. Switching back
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
not a Work project selection.

The main column is a chat surface with the same anatomy as Work Chat (#152). One
centered reading column of the Work Chat measure carries history, the target
picker, and the composer, so all three share the same left and right edge. At
desktop widths the thread is the desk's only vertical scrollport: history flows
from the top of it and the composer stays anchored below it - it never floats in
the middle of the canvas, and a sparse or empty thread simply leaves the column
top-anchored above an anchored composer. Message shapes follow Work Chat too: the
owner's turn is an accent-tinted bubble, everything Master says sits flat on the
surface, and cards are reserved for the information rail. Every header control -
Focus, History, the live indicator, New Task, the backing-runner select, and the
Unattended toggle - shares one control height so the bar reads as one row.

The side column groups queued, running, review/attention, completed,
and failed Master-owned Tasks, then owner decisions and a job-scoped checkpoint
timeline. **Fleet work**, **Decisions**, and **Safety** are independent native
accordions, open by default, and share one card anatomy: a quiet eyebrow above a
title, with the count and the disclosure chevron aligned on the title's line. The
Task status summary is a compact five-reading row (Queued / Running / Review /
Done / Failed) separated by hairlines, with a zero rendered quietly. An empty
accordion states its emptiness in one quiet line, never a paragraph. Each list
reserves a three-row viewport and scrolls
internally for remaining entries, so the desk remains scannable without hiding its
information rail; the rail itself scrolls independently of the conversation and
never squeezes a card to fit. The Delegate sidebar stays visible at desktop widths; it has no
hide/collapse or resize control. Idle, loading, failure/retry, populated, and
in-flight states retain the same geometry. On narrow screens the side column stacks
after the thread with no horizontal scroll: the desk becomes one scrolling
document, and the thread keeps its own bounded scrollport so the durable scroll
anchor survives.

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

Owner-facing target copy says **Project**. Picker options, the send warning, sent
message metadata, and popup chrome lead with the unique Project name; identity and
Area remain secondary context.

Master product-tool outcomes appear as concise, collapsed **Master update**
disclosures. The visible line is a human-readable success, failure, or in-progress
summary; opening it exposes linked Tasks, error context, and the bounded raw payload
needed for audit. Raw tool JSON never takes over the conversation by default.

Normal authenticated surfaces show a labeled floating Master trigger with the
`Ctrl`/`Command` + `Shift` + `M` shortcut. The popup persists at the bottom-left or
bottom-right, avoids shell tools and safe areas, and becomes a sheet on narrow
screens. It traps focus, closes with Escape, and returns focus to its trigger.

**The trigger yields to the composer** (#154). At rest it measures every element
marked `data-composer-dock` — the shared `Composer`, and Work Chat's whole dock so
its controls row counts too — keeps the ones anchored to the bottom of the
viewport that share its horizontal band, and rises a token's gap above the tallest.
The clearance is published as `--master-popup-clearance` and CSS takes
`max(resting offset, clearance)`, so a surface with no composer keeps the plain
corner and a chat panel in another column never moves it. This is measured rather
than tokenized because a composer's height is content: it grows with the text,
attachments, and its controls row, and any fixed guess is wrong the moment it does.
The alternative shapes were rejected for a reason: docking the trigger into the top
bar has nowhere to go at 390px (Menu, Back, the mode switch, Search, and New chat
already fill that row), and hiding it on composer focus leaves Send covered while
the composer is merely idle - which is how the owner found it.
`triggerClearance.ts` owns the calculation and is unit-tested on rects alone.
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

Delegate's global List, Board, and Review modes show **Project: name** on every Task
and plan and include it in each row or card's accessible name. Work's scoped Tasks
view does not repeat the active Project. Opening a cross-Project Task from Attention or another in-app deep link
keeps the current Work selection, shows and locks the Task Project and Area in a
prominent banner, states which Project Work remains on, and gives desktop chrome a
visible **Back to Tasks** (or matching origin) label.

Reloading a `#task/<id>` permalink first resolves the Task and its owning Project
behind a dedicated loading state, then atomically selects and locks that Project.
The shell does not expose Terminal, Files, or Preview until Task ownership and
the active Project are synchronized. The Task banner always names the Project and Area.

The **New task** launcher lives behind the Tasks screen's `+ New task` button (it is no longer a nav destination of its own). It is a focused launcher with no destination dashboard grid. Its integrated Task Composer splits into two rows by kind. The prompt row carries only *actions*: the Add menu for attachments/image/video/design, and the start action. A context bar underneath groups the three controls that describe a task's **execution context** — a searchable Project/folder picker (where it runs), Agent (who runs it), and Guarded or Autonomous execution policy (how it is governed). Each context control carries a leading icon inside its own click target and all three share one type scale, so the bar reads as one row of peers rather than three unrelated widgets. `/image`, `/video`, and `/design` create real media runs that are linked back to the durable task lifecycle. A created task opens `#task/<id>` with live progress, review, approval, and deliverables. Ordinary start failures clean up the queued task; media link failures preserve and identify the task for inspection.

## Workflows

Workflows is the template library for repeatable work. One workflow owns one project (the shell project filter on the library home does not rebind an open plan or template). The screen has a browsable home and a focused editor:

- **Home** remembers the last selected **Drafts**, **Workflows**, or **Runs** tab and
  renders each collection as a table so long libraries remain scannable. Reusable
  workflows share one table: **Availability** (active or paused) is separate from the
  joined **Automation** summary, and every row keeps Edit, manual **Run**, **Schedules**,
  availability pause/resume, and archive. The per-row Schedules action opens the complete
  schedule form in a dialog, including create, durable bindings, enable, overlap, Run now,
  configure, and delete. There is no standalone Scheduled navigation mode.
- **Editor** is the plan/graph canvas.
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

## Collapsed left rail

Collapsing the sidebar (top-bar toggle, persisted locally) turns it into a
**rail**: one column of equal tiles and nothing else. Collapse is a property of
the sidebar, not of a mode: **Work and Delegate share the toggle, the drag
handle, the persisted width, and this rail** (#154). Delegate used to have no
control at all, even though its column was already being sized by Work's handle
through the same `--left-w`; in the rail its three global destinations become
three tiles exactly like Work's five. Its width is not a chosen
number — it is derived from the tile (`--rail-w = --rail-tile-size + 2 ×
--rail-gutter`), and the same gutter is used between tiles, so the column is
evenly spaced by construction and the shell can never disagree with what is
inside it. Rail tiles are square and use the same radius as the tool rail on the
opposite edge, and as a nav row when the sidebar is expanded: collapsing squares
the footprint, it does not restyle the shape. Active and inactive tiles are the
same size and shape — only the fill changes.

Nothing in the rail is allowed to wrap, so every label steps aside and keeps a
second way to be read (#153):

- the **Work project** eyebrow is dropped (there is no honest one-line form of it
  at rail width),
- the **project switcher** becomes a tile carrying the project's first letter or
  digit, with the full name on the tile's title (hover) and in its popover, which
  still lists every project by name and slug. It is never a name truncated to a
  meaningless letter-and-ellipsis pill. With no active project the tile shows a
  neutral dot rather than a letter borrowed from "Select project",
- **destination labels** return as the hover tooltip beside their tile,
- the **update pill** keeps its dot and moves its text to a title,
- recent chats and their group headers are hidden entirely; expanding brings them
  back.

Nothing else changes: the same destinations, the same project switching, the same
accessible names. The expanded sidebar is untouched by the rail's rules, and so is
the main window: the rail's hide list is **scoped to the sidebar**, because
`.eyebrow` is an app-wide class and an unscoped rule took "CHAT" and "YOUR
ORCHESTRATOR" out of the screen next to it whenever the sidebar happened to be
collapsed (fixed in #154; `Sidebar.rail.test.tsx` locks the scoping).

## Right tool rail — Terminal, Files, Preview

Terminal, Files, and Preview are **tools, not destinations**. A slim icon rail on the right edge opens each as an overlay panel (`ToolDock`) above the current screen, scoped to the active project when Project context is available. Browsing left the rail for a destination in ADR-0040 and came back to it in #145: the destination is Artifacts (ADR-0043), a gallery of produced work, while "where is that file on disk" is the utility you open next to it. Opening one lands in that destination's main window (#146).

During Task permalink resolution and any cross-Project Task mismatch, the entire rail
and panel are suppressed. They return only after the Task owning Project and active
Project agree, preventing Preview from presenting stale Work context.

- **Terminal** — the multi-tab PTY terminal. Once opened it stays mounted (hidden when
  the panel closes) so shells survive closing the panel and navigating anywhere.
- **Files** — the real-disk tree browser for the active project (`WorkspaceTree` over
  the Files API), with the prune's semantics intact: paths mean what they say on disk,
  warn-and-skip symlink markers stay inert, and layout-map targets browse their mapped
  Area. Also latched after first open. Opening a file is a **main-window handoff**: the
  dock never grows a viewer of its own, it calls one shell seam
  (`AppShell onOpenFile` → `App openFileInMainWindow`), and Artifacts decides
  what answers - the editor for a document, the inline viewer for anything else
  (#146). The panel stays open behind it, so browsing keeps its place.
  A `proxima:reveal-file` window event points the browser at a path (`lib/revealFile`
  owns both ends of that contract: the raiser's request and the listener's parse).
  A deliverable record's **Reveal in Files** raises it for the active Container, and
  the tree simply expands to the highlighted row, writable as usual. Ops-migration
  recovery raises it for a **Container-root** path (`rootSide: 'container'`) in the
  Container it is recovering, which need not be the active one. Anything that is not
  "the active project, ordinary side" shows a **detour strip** naming whose files
  these are with one way back (`Close inspection` / `Back to <project>`); a
  Container-root tree is read-only and reads its bytes through the inspection adapter
  in place, because no main-window viewer knows that root side. #145 absorbed the
  transient inspection panel the Artifacts destination carried, so this is the only
  tree in the app besides the Wiki's. A reveal raised while Project tools are
  suppressed is dropped, not queued: there is no tree to point at.
- **Preview** — the Run & Preview **controls** (`AppRunner`): folder, command, port,
  Run and Stop, the owner-power consent, the command logs, and every fail-closed
  refusal with the next step that clears it. Since #147 it does not frame the app -
  the running app renders in the Artifacts main window (see "The running app" below),
  and this panel keeps a compact status (Ready/Starting, command, port) with one
  **Show app** action. Not kept mounted: its server is a managed backend process that
  survives on its own, and unmounting stops the status polling. The deliverable record
  page and the recipe test bench keep their own Preview entry points for app-type
  artifacts, and both route their picture to the same main-window viewport.

**One container, one close affordance** (#161). The panel's tab row owns closing it,
identically for all three tools. `AppRunner` takes `onClose` only from a host that
wraps it in no chrome of its own - the deliverable record page and the iterate stage,
where its header ✕ is the only way out - so inside the dock its header carries the
title and the Ready/Starting badge and nothing else. It used to take one there too,
which put a second ✕ on the row immediately below the tab row's. Terminal's per-tab ✕
is not a second close for the panel: it ends **that shell session** and says so
(`Close Terminal 2`), which is why it stays.

The rail's bottom gear opens Settings. Escape closes the panel unless a modal overlay is open - the topmost overlay owns the key, so a confirm dialog raised over the dock is dismissed first. A handed-off file is not one of those since #146: it opens in the main window behind the panel, and neither surface answers Escape. Picking a tool by hand ends any reveal detour, so Files returns to the active project.

### Putting the dock away

The right dock collapses from the header exactly like the left sidebar (#160). Its
control is the sidebar toggle's mirrored twin (`IconPanelRight`) and sits **next to
it**, so both edges of the shell are put away from the same place; the preference
persists in `proxima.dockCollapsed`. Work only - Delegate has no dock, so it gets no
control for one - and absent while Project tools are suppressed, because a toggle for
a dock that is not rendered is a dead control.

Collapsing is one CSS declaration: `.app-shell.dock-collapsed` sets `--toolrail-w` to
zero, and every width derived from it follows - the grid's third column, the padding
the main pane reserves while a tool is open, the Master popup's clearance, the toast
column. Collapsing also closes an open panel: a panel hanging off a rail that is no
longer there is not a state the owner asked for.

It is a **preference, not a suppression**, and the two behave differently on purpose.
Suppressed (a Task/Project mismatch) there is nothing to point a tool at, so a reveal
is dropped. Collapsed, everything still exists - so a reveal or a run-controls request
opens its tool *and brings the rail back*, which the dock reports through the same
`onOpenChange` the shell already listens to. Latched tools are untouched: terminals
keep running behind a collapsed dock. Escape is unchanged - it closes the panel, never
the dock preference - so #145's precedence rule still holds.

At **phone width there is no rail at all** (#156) - see [Phone width](#phone-width-390px).

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
The enabled desktop Back control includes the origin text, not only an icon.

**Multitask foundation:** primary surfaces must not destroy in-flight UI on leave.
Once visited, **Chat, Master, Tasks, Workflows, Artifacts, and Design** stay mounted in
hidden `surface-pane`s so draft text, open panels, canvas/plan state, and in-flight
runs re-attach when the owner returns in the same browser session. Work Chat reload
durability is owned under Chat above. Server work continues regardless; the client
contract is keep-alive / re-attach, not remount-from-zero.

**A nav click means that destination's own state.** Keep-alive restores what the
owner left there; it must not restore something a *different* destination put on
the surface. Two destinations can hold a focused item, and both leave it when a
nav click asks for the destination itself (`navClickLeavesFocusedItem`):

- **Re-clicking the destination you are already on** returns to its home - the
  Workflows plan list, the Design Studio start screen (or its gallery, when the
  canvas was entered from there). This is the older Workflows rule, now shared.
- **Design also leaves a *visit*.** A design opened from an Artifacts card, a task
  file link, or a chat result is one file another destination sent the owner to
  look at. It was never Design's own state, so clicking **Design** in the nav goes
  to the studio home even when the owner is arriving from elsewhere - and such a
  design is never written to the studio's resume key (`proxima.design.last.<slug>`),
  which is what a cold load reopens. A design opened inside the studio, named by
  the URL, or reached from its chat session is Design's own and is restored
  normally. Without this, opening a design from Artifacts silently became the
  answer to "take me to Design" for the rest of the session (#148).

**Teaching empty states:** top-level empties share one grammar — title, what the surface
can do, short tutorial steps, and one primary CTA where it applies (Chat, Master, Tasks,
Workflows library context, the Artifacts gallery and its Deliverables tab, Design home). Help/core tour
nouns match the primary loop **Chat → Tasks → Workflows → Artifacts**, with Master as the delegate side path.

**Workflow how-it-runs:** library table rows show Availability separately from the joined
Automation summary (schedules on, off, or needing bindings). Schedule forms lock project
to the workflow owner - no free rebinding. Open deliverables from Chat/Tasks/Artifacts land
in the same main-window surfaces Artifacts opens (#146). Unsupported binary or
directory-like paths show a download fallback immediately rather than remaining in a
loading state.

## Notifications: the ephemeral header and the Inbox destination

Notifications have two surfaces over one ledger (#158). The **header** is ephemeral,
like a phone's: it lists only what the owner has not seen, the badge counts exactly
that, and touching an item - opening it, dismissing it, or acting on it - marks it read
so it leaves. Every row also carries an explicit **Dismiss**, which is what makes
navigate-only kinds such as Master budget clearable at all (#157); the footer says
where they go and links straight there. Removal is optimistic, so the badge never lags
a click; a failed dismiss surfaces as the ordinary retryable error and the next poll
restores the row.

**Inbox** is a sidebar destination in **both** Work and Delegate. That is deliberate:
notifications are global - a Task that finished, a Master budget stop, a failed
workflow - and the header that emits them already renders in both modes, so an Inbox in
only one would strand half of them behind a mode switch. It is a destination rather
than a larger popover because most of what the system emits is *reading*, not deciding:
errors with their diagnostic, finished work, budget stops. The header is the
interruption; the Inbox is the record, which is what lets the header be ruthless.

The Inbox lists every notification newest-first with an All / Unread filter, **Mark all
read**, a per-row read toggle, and a **Load older** cursor. Unread is signalled by one
dot and a weight change; severity by one hairline accent and a quiet label, never a
wall of coloured cards. An item that still needs a decision keeps its inline actions
there, so dismissing from the header defers the decision without hiding it. Errors
carry their full detail - the diagnosis and the step that clears it - in the entry
itself, so a failed Task is diagnosable without opening the run.

## Global attention, running work, and account surfaces

The shell-level **Attention** badge persists across destinations and polls one unified
shape. Non-decision rows are real buttons that deep-link to the owning
Master/Task/plan/Settings surface. Only server-marked `inline_ok` binary actions render
beside the link; diff review and Master budget items navigate instead. Non-approval
Master decisions render their full resolve/defer form inline in the inbox (same card as
Master Decisions and the Task workspace). The popover has loading, empty,
populated, and persistent retryable-error states, closes on Escape/outside click, and
becomes a viewport-bounded sheet on narrow screens. It now shows the *unread* slice of
the Inbox ledger rather than every open item, and its badge counts unread rows (#158).

Next to Attention, a **Running** text pill polls `GET /api/runs/active` plus running jobs
and shows only while work is in flight (`1 task running` / `N tasks running`; mobile may
shorten to `N running`). When the count is zero the control is hidden entirely (quiet
header). The popover lists de-duplicated tasks and chat sessions with deep-links (task
workspace / chat / Tasks index), matching Attention's open/refresh/empty/error affordances.
Attention stays a separate `!` control and remains hidden when empty.

**Inbox** closes both navigations - see the notifications section above. Agents and Settings live in the Work profile/account menu rather than the navigation. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge, including files, links, graph, and search. Settings sections are grouped for scan with short title-only nav rows under group eyebrows: **Work setup** (Projects, Agents, Master, Knowledge) · **Integrations** (Media, Remote) · **System** (Account, Diagnostics) · **Help**; full hints live on tooltips and aria. Editable panels surface clear save success/error (no silent fail). Help owns a replayable core tour (primary loop + Master side path) plus feature-aware product-map chapters. The first post-setup main UI shows the core tour once; it traps keyboard focus, supports Escape/skip, and stores completion server-side. The Work top bar owns the brand mark, mode switch, the sidebar and tool-dock collapse toggles (one pair, one per edge), search, Running + Attention, and account menu; its sidebar owns the active-project switcher. On mobile that switcher stays in the Work drawer, the mode control remains in the compact header, and the tool-dock toggle joins it there as the tool sheet's only entry point. Global search includes user-facing Chat and Design sessions but excludes Master's hidden system thread, so raw product-tool calls and tool-result payloads never become search results.

Projects remain shared application entities: one active project across Work (`activeProject`). Work surfaces that already filter / default-attach / list by active project (Chat, Workflows library, Artifacts, and ordinary Design entry) keep that contract. Opening Design from a Task binds the studio to that Task's owning Project without adopting it as the Work selection, and returning to the Task restamps the in-app preserve-work policy. The Work-sidebar project switcher changes only that shell filter (and the coherent recent chat session for when Chat is opened later) - it does **not** navigate to Chat. Search (and similar intentional open paths) may still open a project's chat. Opening a workflow/plan still uses that workflow's owned project; the Work switcher does **not** rebind an open workflow instance to another project. Workflows library home has no second project dropdown and does not dump project display names (open-plan header uses a name-free lock icon). The switcher menu offers Rename (alongside Settings → Projects). Deliverable records and Designs remain owned by their Project. Delegate has no project selector or project filter: its Tasks and Artifacts indices are global, while Master Focus and explicit target controls remain its own bounded context.

A **global error surface** sits below every destination. Uncaught errors, unhandled
promise rejections, failed dynamic imports, and API calls that got no response (or a
5xx) raise a dismissible top-centre toast with a short human message and a `Details`
disclosure carrying the message plus a stack snippet. It uses the same toast card as the
Master notifications column but its own anchor, so the two never collide. A stale-chunk
failure after a redeploy offers **Reload Proxima**. Repeats collapse into one toast with
a `×N` count and at most three are visible at once, and an unreachable-server toast
retires itself once a call succeeds again; 4xx refusals stay with the flow that raised
them, so a screen with its own error state never gets a duplicate toast. The
surface is mounted outside the render error boundary, so it also works on the auth gate,
in Delegate, and after a render crash.

The Work selection persists per owner across a full browser refresh. Boot validates
the saved Project before applying it. A missing saved Project falls back to an
existing private Project and raises a dismissible notice that names the missing and
replacement Projects instead of silently resetting context.

## Projects

Project **management** is a **card grid** under Settings → Projects (not a primary-nav
destination). A project carries a name and a slug, which is not enough to earn a
permanent detail panel. It reuses the same shell as the shared list shell
(`.tasks-view` + `.tasks-head` + `.wf-grid`/`.wf-card`): search on the left of the bar,
**Add project** on the right, one card per project. Deep links / `view === 'projects'`
open Settings on the Projects section.

A card shows the name and slug, marks the **active** project (the one the rest of the app
is pointed at), and carries its own actions: the card body selects it, **Rename** opens a
prompt dialog, and the hover/focus **×** removes it. A project whose folder is no
longer where its record says (moved, renamed, restored, unreadable) is not a card
that fails later: it carries a warning pill (**Folder missing** / **Folder changed** /
**Folder unavailable**), the reason in plain words, and a **Find folder** action
(prune C6). That action opens the relocate dialog, which states the last known
location and embeds the *same* folder picker used for onboarding - browse to where
the folder lives now and re-pin it. Nothing is moved or copied; only the project's
address changes. When the folder's own identity does not match the project's, the
dialog says which identity it found versus expected and offers a deliberate
**Re-pin anyway** override next to the primary action (single owner: a loud warning,
never a wall). The other way out, removing the project, keeps working with the
folder gone. Add opens a modal holding both ways in:
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

An open `container_ops_migration` Attention item routes to
`#settings/projects/<slug>/ops-migration`, switches the active Project when needed,
and preserves that detail route across reload. The same surface is available from
each Project card in Settings. It presents the stored reason, phase, both physical
layouts, exact physical-root entries, conflicts, and remaining usable paths. While
the detail is open, its Project pins the shell scope across session refreshes and the
project switcher is locked. Reveal actions open the Artifacts inspection panel with
explicit Container-root targets for the chosen side through a read-only adapter with no
create, save, rename, or delete controls. Revealed directories expand and receive the active-row
marker. Per-path inspection uses each side's actual file or directory state, while
backend-declared root inspectability keeps missing, symlinked, unavailable, or
unsupported targets disabled with an accessible refusal reason. If a file editor
already holds unsaved project bytes, inspection keeps that buffer mounted and
read-only with a visible retain banner; tree selection can browse the inspection side
without replacing those bytes, and write returns only when the ordinary
Area-validated adapter is restored.
Closing that inspection or changing Projects clears the Container-root target and
restores the ordinary Artifacts gallery. Changing
Projects or leaving the Projects Settings section clears the durable detail hash,
while changing directly between recovery routes clears stale detail data before
loading the next Project. Validation refresh is read-only, and guarded retry remains
disabled until the backend confirms the layout is safe. A repaired already-physical
layout with open Attention can retry the same validation boundary to resolve the item
without moving content. The detail heading receives focus on entry; status changes
use live regions, errors use alerts, and retry exposes its safety rule through
`aria-describedby`.

## Artifacts and the deliverable ledger

Archive and Files merged into ONE destination (prune Part D, #139; decision #122), and that destination is **Artifacts** (ADR-0043): the gallery of what the project produced, with the ledger as tabs on the same surface.

- **All** - the gallery. Designs, images, and video render as **thumbnails** (a design draws its first artboard from `scene.json`); documents, pages, and data render as a **list**. It is a live scan of the Container (`GET /api/projects/{slug}/artifacts`), so it shows what is on disk right now; that scan is capped, and the gallery says so under the last row rather than looking complete - the paginated truth is the Deliverables tab. An artifact the ledger knows carries a **deliverable badge** with its record's approval status; clicking the badge opens the full record.
- **Deliverables** - the durable deliverable registry (T4): every agent output is a record with lineage, ONE approval status (synced with the job-review approve), and a version chain, filterable by type/status/date/search.
- **History** - records whose file no longer exists on disk. They are records, not phantom files: the ledger's survive-deletion property, kept visible.

The combo detail is unchanged: an expanding row plus a full record page at a permanent `#archive/<project>/<slug>` address (the hash format outlives both retired destinations, so old bookmarks keep working - they open the record panel inside Artifacts, and a bookmarked `?view=files` URL lands there too). Record paths are container-relative real paths (#139) - the same paths the gallery shows. Approvals keep their two doors: the record panel and the Tasks review write the SAME status field. Locating a record's file on disk is **Reveal in Files** on the record panel, which raises the reveal event the dock browser answers (#145) - Delegate has no dock, so that action is absent there rather than dead. The record panel's **Open** actions use the same main-window surfaces as the gallery (see "Opening an artifact" below), and an app record's **Preview app** opens the Run controls inside the record with that app's folder prefilled; running from there hands the picture to the main-window viewport, which takes the window over (see "The running app" below) - in Work only, for the same reason Reveal in Files is. Design is a separate canvas destination whose internals are not part of the shell.

### Opening an artifact

Opening an artifact takes over the Artifacts **main window** (#146, ADR-0043
decision 3). There is no lightbox: nothing modal stands between the owner and
the file. Every open path lands here - a gallery card or row, the dock browser
and a task's file links through the one shell seam, a chat result card or
permalink, a record panel's **Open** - and what the file IS decides which
surface answers:

- **Documents you write** - markdown, plain text, source - open **directly in
  the editor**, editable from the first frame. Markdown gets the wiki's markdown
  editor (Edit first, Preview one click away); anything else text gets the
  CodeMirror file editor with ⌘/Ctrl+S. Both confirm before discarding unsaved
  bytes. Opened from Artifacts a markdown document carries **no wiki graph**: an
  artifact anywhere in the Container has no backlinks to show and no
  `[[wikilink]]` namespace to resolve against - that context belongs to the Wiki
  destination. Extensionless documents (`README`, `Dockerfile`, `LICENSE`) count as
  text; a genuinely binary file with no extension is refused by the Files API
  with a readable reason instead of being rendered as noise.
- **Everything else** opens in the **inline viewer** (the same ArtifactViewer,
  now rendered in the main window rather than over it): images, video, PDFs,
  sandboxed HTML pages, and the data documents whose rendering is the point -
  CSV tables, JSON trees, Mermaid diagrams and their whiteboard. ←/→ walks the
  Container's other viewer-bound artifacts; documents are not on that walk,
  since each opens alone in its editor. **Edit source** hands any text-backed
  artifact to the same editor, and the way back from there returns to the
  artifact it came from. The artifact gets the whole window: a page or a PDF
  fills the stage edge to edge so a desktop layout is readable without scrolling
  sideways, while media and documents keep the padding that centres them.
- A **design** is a folder of scene JSON, not a file with bytes. In Work it
  opens in the Design Studio canvas, which is the same main window. Where there
  is no studio - Delegate - the viewer draws its first artboard on the stage,
  the same picture its gallery card shows, rather than an unsupported-file dead
  end. A folder has nothing to download, so that action is absent for it.

Both surfaces name where the way back leads: **← Gallery**, or **← Record** when
the artifact was opened from a deliverable record; returning puts focus back on
the card or row that opened it. That control **leads the toolbar row on both
surfaces** - viewer and editor alike, at every width - because it is the same
back affordance the shell's own chrome Back and every detail header use, and a
back control that moves depending on which surface you are on is one the eye has
to hunt for. It is deliberately *not* a pane's **Close**: the Wiki destination's
note pane and the dock's file editor still trail their actions with one, since
dismissing a pane is not leaving a surface (#159). Neither surface closes on Escape - they are
main-window content, not overlays - and neither traps focus. Escape still
belongs to the layers that ARE overlays, and while one of those is up inside the
viewer (the Mermaid whiteboard, the active-preview consent alert) the viewer
takes the key outright so the same press cannot also close the dock panel behind
it - the precedence rule #145 established.
Changing the shell Container closes an open artifact: the destination refilters
to the new Container, so another project's file cannot keep the window.

**Delegate behaves identically**: it has no dock and no Design Studio, but it
has this destination, the same editor, and the same viewer, because the editor
is an Artifacts surface and the Files API it writes through is not Work-scoped.
Nothing about opening an artifact differs between the two modes.

**No review panel.** The viewer used to carry a fixed side column - pin counts,
point annotations, a general-feedback field, and **Add feedback to chat**, which
seeded the producing session's composer with a path-linked brief. The owner
removed it (#148): it cost every artifact a third of the window, and an HTML page
had to be scrolled sideways to be read at all. Feedback is written in Chat like
any other prompt. The editor's **Review** action went with it, since reaching
those pins was the only thing it did.

### The running app

A running app is the fourth thing this main window can hold (#147, ADR-0043
decision 4), and it is not an artifact: it has no bytes, no record, and no
neighbours to walk, so it is its own surface rather than another branch of the
artifact router. It takes the window whole - an open artifact steps aside for
it, and opening an artifact closes it.

- **The dock runs it, the main window shows it.** The Preview tool keeps every
  control; Run opens the viewport here automatically, so the app the owner just
  started is in front of them without a second click. The dock then shows a
  compact status with **Show app**, which brings this surface back up from
  anywhere.
- **The viewport polls status itself.** It is not handed a snapshot, because the
  dock is not its parent and need not even be open. A ready app is framed at the
  chosen device width (Desktop/Tablet/Mobile) with **Reload** and **Open in new
  tab**; a starting app shows what it is waiting for; a stopped, exited,
  port-conflicted or ownership-unknown app stops being framed and names its state
  rather than leaving a stale page up.
- **It never dead-ends.** Every state carries **Run controls**, which opens the
  dock's Preview tool (a `proxima:open-run-preview` window event the dock
  answers, the mirror of `proxima:reveal-file`), and **← Gallery** back to the
  destination. Like a reveal, that request is *dropped* while Project tools are
  suppressed (Task permalink resolution, cross-Project mismatch): there is no
  dock to open, and the way back to the gallery still works.
- **Run shows the app, from wherever it was started.** That is one rule for all
  three entry points: the dock, a deliverable record's **Preview app**, and the
  recipe test bench. The last two mount the same controls inline with the app's
  folder prefilled, and running from them hands the window to this viewport - so
  those inline controls go away and Stop is reached through **Run controls**.
  It was already true that both took over the panel they sat in; what changed is
  which surface they take over.
- **The security model did not move with the picture.** The frame's sandbox is
  ADR-0042/#140's, unchanged and stated in one place (`appPreview.ts`): an
  isolated origin (the per-app subdomain or the relay port) gets
  `allow-scripts allow-same-origin allow-forms allow-popups allow-modals`, and
  the same-origin `/api/appview` fallback gets that string **without**
  `allow-same-origin`, because that origin holds the owner's session.
- **Work only.** Running an app is owner-power execution driven from a dock
  Delegate does not have, and a viewport opened there could reach no controls, so
  Delegate offers neither half: an app record's **Preview app** entry is absent
  there rather than dead.

## De-jargon rule for primary surfaces

Primary screens (Chat, Tasks, Workflows, Artifacts, the task workspace, the shell itself) never show the words "runner", "MCP", or "profile", env-var names, raw tool payloads, or raw stack traces. The plain words are **agent** and **tools**. Technical detail belongs to Settings, Agents, and docs. Master has one deliberate product-contract exception: its header says **Backing runner** because the owner explicitly chooses a server-qualified runner for the system identity; tool results render as flat timeline text (with plain job links when present), not raw JSON or card chrome. The qualification contract is owned by [Runner conformance](runner-conformance.md).

## Responsive and accessibility behavior

### Phone width (~390px)

The shell's bar row is a **grid**, not an overlay stack: the mobile top bar takes
one cell and the status cluster (Running + Needs-you) takes an auto-sized cell
beside it, collapsing to nothing when both are empty. As a fixed overlay the
cluster covered Search and, with the drawer open, the drawer's own Close button
(#154). The rest of the phone contract:

- **Tool dock** — **there is no rail down here** (#156). A 46px lane is 12% of a
  390px screen, taken permanently from the surface behind it: the Design Studio
  canvas ended at the rail's edge. Nothing is stranded by removing it - the three
  tools *are* the sheet's own tab row, and Settings is in the drawer's account
  menu - so the phone keeps the entry point and drops the lane. The dock's
  toggle moves to the mobile top bar (same glyph as the header's, #160) and
  shows the **sheet** rather than collapsing a rail, the way the same control
  opens the drawer instead of collapsing the sidebar. The sheet reopens on the
  tool last used, and its own close (its ✕, Escape) is the toggle's off state.
  That preference is transient: a phone never writes the desktop rail's
  `proxima.dockCollapsed`, and never reads it either.
  The sheet is the full screen, and the surface behind it reserves nothing -
  reserving the panel's width (74% of the pane, the desktop behaviour) squeezed
  the screen behind into a one-word-per-line strip (#154). The panel's tab row
  scrolls so its Close button keeps its place inside the panel.
- **Artifacts** — the head wraps: title and scope control on one line, the
  All / Deliverables / History tabs on their own scrolling line instead of being
  clipped at the screen edge. A document opens **inside** the main window, with
  the top bar and tool rail still there; the full-screen sheet treatment belongs
  to the Wiki destination, which shares the same `WikiNote` component.
- **Master desk** — keeps the sub-900px single-document stack (#152); the one
  control whose value is a sentence (Backing runner) takes the full row rather
  than truncating, and the popup's thread fills its sheet instead of leaving the
  composer floating in dead space.
- **Composer** — clears the home indicator, and the floating Master trigger
  clears the composer (see [Master](#master)).

### General

The left navigation width persists locally in both modes. Its separator supports pointer input and keyboard Arrow keys and exposes vertical separator orientation plus minimum, maximum, and current values. At mobile widths navigation uses the same focus-managed drawer in both modes; Work's tools open as a sheet from the mobile top bar with no rail beside the content, while Delegate keeps its global Master, Tasks, and Artifacts navigation. The Task Composer and Master controls stack without changing semantics. Account actions use ordinary disclosure/popover semantics in Work. Escape dismisses transient Work overlays (including the tool panel, Attention, and Master popup); modal overlays trap focus until dismissed. Focus indicators use shared tokens, toast live priority matches urgency, and reduced-motion preferences apply globally.

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
persistence, the dock's Files tree and its main-window handoff, asynchronous task success/failure, declared schedule inputs, cron
grammar, keyboard resizing, and durable detail routes reached from Attention.

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
