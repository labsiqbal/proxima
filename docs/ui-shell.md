# UI shell and information architecture

This is the durable contract for Proxima's application shell. It describes product routing and lifecycle boundaries; API and database details remain in the generated reference docs.

## Information architecture

There is **one workspace**. The old Ops/Code split is gone — no workspace switcher exists anywhere. The desktop shell has three regions:

- a persisted, collapsible **left navigation** ordered by the flow,
- the **destination work surface** in the center,
- a slim **right tool rail** whose tools open as overlay panels above the current screen.

The left navigation is flow-ordered: **Chat** (hands-on), **Alpha** (delegate and monitor), **Tasks** (watch it run), **Workflows** (keep what worked), then **Archive** (where deliverables live), plus feature-gated **Design**. The **active project** is switched from a text control in the shell top bar (immediately right of Search). Project **management** (list / link / create / remove / container settings) lives under **Settings → Projects**, not primary nav. Agents and Settings stay in the account menu. The default landing view is Chat.

## Chat — the front door

Chat is the conversational surface where work begins: brainstorm until the scope is clear, then promote the conversation with **Slice into plan**, which drafts a plan (a DAG of jobs) and opens it in the editor. The left nav lists destinations only (**Chat**, **Alpha**, **Tasks**, …); it does not carry a separate **New chat** row. A blank session is started from the **Chat** header control (compact icon on the mobile topbar), or via `/new`. The database session is created lazily on the first message; recent chats appear under the nav once a thread exists.

A workflow's iteration thread is not an ordinary chat: the nav attributes it to Workflows, and picking Chat while one is open switches to a plain conversation instead.

A file-changing assistant turn carries a **Restore changed paths** control. It first
opens a path impact preview and asks for confirmation; active Alpha work in the same
project adds a warning. The journal belongs to that chat session and disappears with it.

## Alpha

Alpha is a first-class destination, not a Chat tab or Tasks filter. Its header identifies
the built-in system orchestrator and lets the owner choose the backing runner; the desk
itself keeps the counterpart label **Alpha** and does not expose a fake worker profile.
A compact capacity strip always states running/free out of three, queued count, and the
saved unattended budgets. The main column is the Alpha thread plus the shared **Chat
composer** stack (attach + `@` project file/artifact mentions) wired to Alpha's send
API rather than a normal chat run; project context follows the shell active project
(or an active Alpha job's project). The side column holds active/queued/needs-you jobs
and a job-scoped checkpoint timeline and is **collapsible** (header toggle + reopen
edge control; preference in `localStorage` as `proxima.alpha.sideCollapsed`; mobile
defaults collapsed). Idle, loading, failure/retry, populated, and in-flight states all
retain the same geometry. On narrow screens the side column stacks after the thread
with no horizontal scroll.

**Unattended** is a quick pressed toggle on the desk. Off means Alpha never starts work
without an owner turn. On means the server may start already-queued Alpha jobs until the
saved turn/wall budget stops cleanly; numeric limits live under Settings → Alpha and
remain readable on the desk. Satpam, not Alpha, owns stuck-job steer/restart.

## Tasks

Tasks is the durable execution/review index for queued, running, review, done, failed, and archived work — plans and one-off tasks together. Plan rows expand into their ordered job list; branching plans also offer the List↔Graph projection toggle. A repo job (one that worked in an isolated copy of a code area) reviews its **Changes** in place — inside a plan row's expanding body, or on the full-width task page — with approve-and-merge and reject-with-reason as the two verdict doors; per T4 there is no right panel and no popup, and the copy stays jargon-free.

The **New task** launcher lives behind the Tasks screen's `+ New task` button (it is no longer a nav destination of its own). It is a focused launcher with no destination dashboard grid. Its integrated Task Composer splits into two rows by kind. The prompt row carries only *actions*: the Add menu for attachments/image/design, and the start action. A context bar underneath groups the three controls that describe a task's **execution context** — a searchable Project/folder picker (where it runs), Agent (who runs it), and Guarded or Autonomous execution policy (how it is governed). Each context control carries a leading icon inside its own click target and all three share one type scale, so the bar reads as one row of peers rather than three unrelated widgets. `/image` and feature-gated `/design` create real media runs that are linked back to the durable task lifecycle. A created task opens `#task/<id>` with live progress, review, approval, and deliverables. Ordinary start failures clean up the queued task; media link failures preserve and identify the task for inspection.

## Workflows

Workflows is the template library for repeatable work. One workflow owns one project (the shell project filter on the library home does not rebind an open plan or template). The screen has a browsable home and a focused editor:

- **Home** remembers the last selected **Drafts**, **Workflows**, or **Runs** tab and
  renders each collection as a table so long libraries remain scannable. Workflow rows
  are split into **Manual (on-demand)** and **Otomatis / Scheduled** groups derived from
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
Once visited, **Chat, Alpha, Tasks, Workflows, Archive, and Design** stay mounted in
hidden `surface-pane`s so draft text, open panels, canvas/plan state, and in-flight
runs re-attach when the owner returns in the same browser session. Server work continues
regardless; the client contract is keep-alive / re-attach, not remount-from-zero.

**Teaching empty states:** top-level empties share one grammar — title, what the surface
can do, short tutorial steps, and one primary CTA where it applies (Chat, Alpha, Tasks,
Workflows library context, Archive, Design home). Help/core tour nouns match the primary
loop **Chat → Tasks → Workflows → Archive**, with Alpha as the delegate side path.

**Workflow how-it-runs:** library table rows show Manual or Scheduled badges derived from
real schedule rows (optional short cron text). Schedule forms lock project to the workflow
owner — no free rebinding. Open deliverables from Chat/Tasks/Archive use the same
in-app **ArtifactViewer** for supported types. Unsupported binary or directory-like
paths show a download fallback immediately rather than remaining in a loading state.

## Global attention, running work, and account surfaces

The shell-level **Attention** badge persists across destinations and polls one unified
shape. Every item is a real button that deep-links to the owning Alpha/Task/plan/Settings
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

Agents and Settings live in the profile/account menu rather than the navigation. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge, including files, links, graph, and search. Settings sections are grouped for scan with short title-only nav rows under group eyebrows: **Work setup** (Projects, Agents, Alpha, Knowledge) · **Integrations** (Media, Remote) · **System** (Account, Diagnostics) · **Help**; full hints live on tooltips and aria. Editable panels surface clear save success/error (no silent fail). Help owns a replayable core tour (primary loop + Alpha side path) plus feature-aware product-map chapters. The first post-setup main UI shows the core tour once; it traps keyboard focus, supports Escape/skip, and stores completion server-side. The **top bar** owns the brand mark (far left), the sidebar collapse toggle, search, the **active project** text switcher (immediately right of Search), Running + Attention (status cluster), and the account menu; the mobile topbar carries the same project switcher in the center context slot, and the drawer keeps its own brand copy since the desktop top bar hides below the tablet breakpoint. Global search includes user-facing Chat and Design sessions but excludes Alpha's hidden system thread, so raw product-tool calls and tool-result payloads never become search results.

Projects remain shared application entities: one active project across the app (shell `activeProject`). Surfaces that already filter / default-attach / list by active project (Chat, Alpha, Workflows library, Archive, Design) keep that contract. The header project switcher changes only that shell filter (and the coherent recent chat session for when Chat is opened later) — it does **not** navigate to Chat. Search (and similar intentional open paths) may still open a project's chat. Opening a workflow/plan still uses that workflow's owned project; the header switch does **not** rebind an open workflow instance to another project. Workflows library home has no second project dropdown and does not dump project display names (global switcher only; open-plan header uses a name-free lock icon). The switcher menu offers Rename (alongside Settings → Projects). Archive records and Designs remain owned by their Project.

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

Removal copy must distinguish the two cases, because the API does: a folder outside the
workspace root is only *unlinked* and its real files survive, while a project Proxima
created is deleted from disk. Chats and tasks go in both cases.

## Archive and Design

Archive is the durable deliverable registry (T4): every agent output lands as a record with lineage, ONE approval status (synced with the job-review approve), and a version chain; the combo detail is an expanding row plus a full record page at a permanent `#archive/<project>/<slug>` address - no right panel, no popup. Records survive file moves and deletion. Design is a separate canvas destination whose internals are not part of the shell. Design links are enabled only when the Design Studio feature gate is on; otherwise source artifacts remain available.

## De-jargon rule for primary surfaces

Primary screens (Chat, Tasks, Workflows, Archive, the task workspace, the shell itself) never show the words "runner", "MCP", or "profile", env-var names, raw tool payloads, or raw stack traces. The plain words are **agent** and **tools**. Technical detail belongs to Settings, Agents, and docs. Alpha has one deliberate product-contract exception: its header says **Backing runner** because the owner explicitly chooses Claude/Codex/Grok/Hermes/Pi for the system identity; tool results render as flat timeline text (with plain job links when present), not raw JSON or card chrome.

## Feature gates

Routes, sidebar destinations, session eligibility, search, and deep links must all honor the server feature configuration. A hidden destination must not become reachable through stale state. Gating must not reorder the remaining navigation.

## Responsive and accessibility behavior

The left navigation width persists locally. Its separator supports pointer input and keyboard Arrow keys and exposes vertical separator orientation plus minimum, maximum, and current values. At mobile widths navigation uses a drawer, the tool rail pins to the right edge, and the Task Composer and Alpha controls stack without changing semantics. Account actions use ordinary disclosure/popover semantics. Escape dismisses transient shell overlays (including the tool panel and Attention); the modal core tour traps focus until completed/skipped. Focus indicators use shared tokens, and reduced-motion preferences apply globally.

## Extension points

Add destinations through the existing `View`, feature policy, App routing, Sidebar, and SearchModal boundaries together. Every new destination must declare whether it belongs to the flow navigation or the global account layer; new tools belong on the rail, not in the nav. Destination-specific inspectors remain owned by their destination rather than the application shell.

## Validation

For shell changes, run `npm --prefix apps/web test`, `npm --prefix apps/web run build`, and `git diff --check`. Tests should cover navigation order and feature-off gating, tool-rail open/close with Terminal persistence, asynchronous task success/failure, declared schedule inputs, cron grammar, and keyboard resizing. Browser QA should check authenticated desktop and narrow layouts, focus order, themes, zoom, and reduced motion; if authentication prevents inspection, record that rather than using credentials.
