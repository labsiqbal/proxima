# UI shell and information architecture

This is the durable contract for Proxima's application shell. It describes product routing and lifecycle boundaries; API and database details remain in the generated reference docs.

## Information architecture

There is **one workspace**. The old Ops/Code split is gone — no workspace switcher exists anywhere. The desktop shell has three regions:

- a persisted, collapsible **left navigation** ordered by the flow,
- the **destination work surface** in the center,
- a slim **right tool rail** whose tools open as overlay panels above the current screen.

The left navigation is flow-ordered: **Chat** (hands-on), **Alpha** (delegate and monitor), **Tasks** (watch it run), **Recipes** (keep what worked), then **Projects** and **Archive** (where work lives), plus feature-gated **Design**. Agents and Settings stay in the account menu. The default landing view is Chat.

## Chat — the front door

Chat is the conversational surface where work begins: brainstorm until the scope is clear, then promote the conversation with **Slice into plan**, which drafts a plan (a DAG of jobs) and opens it in the editor. The sidebar always shows **New chat** plus the project-scoped recent chats (and design sessions when the Design feature is on). A new chat starts blank; the database session is created lazily on the first message. `/new` remains available.

A recipe's iteration thread is not an ordinary chat: the nav attributes it to Recipes, and picking Chat while one is open switches to a plain conversation instead.

A file-changing assistant turn carries a **Restore changed paths** control. It first
opens a path impact preview and asks for confirmation; active Alpha work in the same
project adds a warning. The journal belongs to that chat session and disappears with it.

## Alpha

Alpha is a first-class destination, not a Chat tab or Tasks filter. Its header identifies
the built-in system orchestrator and lets the owner choose the backing runner; the desk
itself keeps the counterpart label **Alpha** and does not expose a fake worker profile.
A compact capacity strip always states running/free out of three, queued count, and the
saved unattended budgets. The main column is the Alpha thread plus a guarded delegation
composer; the side column holds active/queued/needs-you jobs and a job-scoped checkpoint
timeline. Idle, loading, failure/retry, populated, and in-flight states all retain the
same geometry. On narrow screens the side column stacks after the thread with no
horizontal scroll.

**Unattended** is a quick pressed toggle on the desk. Off means Alpha never starts work
without an owner turn. On means the server may start already-queued Alpha jobs until the
saved turn/wall budget stops cleanly; numeric limits live under Settings → Alpha and
remain readable on the desk. Satpam, not Alpha, owns stuck-job steer/restart.

## Tasks

Tasks is the durable execution/review index for queued, running, review, done, failed, and archived work — plans and one-off tasks together. Plan rows expand into their ordered job list; branching plans also offer the List↔Graph projection toggle. A repo job (one that worked in an isolated copy of a code area) reviews its **Changes** in place — inside a plan row's expanding body, or on the full-width task page — with approve-and-merge and reject-with-reason as the two verdict doors; per T4 there is no right panel and no popup, and the copy stays jargon-free.

The **New task** launcher lives behind the Tasks screen's `+ New task` button (it is no longer a nav destination of its own). It is a focused launcher with no destination dashboard grid. Its integrated Task Composer splits into two rows by kind. The prompt row carries only *actions*: the Add menu for attachments/image/design, and the start action. A context bar underneath groups the three controls that describe a task's **execution context** — a searchable Project/folder picker (where it runs), Agent (who runs it), and Guarded or Autonomous execution policy (how it is governed). Each context control carries a leading icon inside its own click target and all three share one type scale, so the bar reads as one row of peers rather than three unrelated widgets. `/image` and feature-gated `/design` create real media runs that are linked back to the durable task lifecycle. A created task opens `#task/<id>` with live progress, review, approval, and deliverables. Ordinary start failures clean up the queued task; media link failures preserve and identify the task for inspection.

## Recipes

Recipes is the template library for repeatable work. The screen owns two modes:

- **Editor** is the plan/graph canvas. `PROXIMA_FEATURE_WORKFLOW_GRAPH` defaults on;
  with the recovery switch off the mode explains that the editor is off (the env var
  itself stays out of the UI copy — it is documented here and in installation docs).
  It has an **authoring chat** on the left under the standing rule — typing drives the
  plan on screen, never the database — which hands back a `<workflow-graph>` (nodes +
  edges), so the agent can propose branches rather than a straight line. The chat is
  pinned to the graph job's own session, so reopening a plan resumes its conversation.
  The editor is **canvas-first**: node-level actions stay with the node; the plan list
  collapses; and the node inspector exists only while a node is selected.
- **Scheduled** manages real schedule rows for saved graph templates. A schedule renders
  its input form from the template's declared `{{inputs}}`, and a due tick spawns the
  same `engine='graph'` job a manual run produces.

The **Sequential recipe editor is retired**: a linear recipe is a graph with no branches,
and the canvas authors those too. The linear *engine* remains for pre-existing jobs and
sessions (`IterateStage` is still reachable from an old session carrying `workflow_id`),
but no new linear workflow can be authored.

Schedule inputs mirror each recipe's declared definitions, validate required values, and serialize values by declared input ID. Recipes without declarations may receive an optional `brief`. Cron accepts exactly five fields using numbers, `*`, positive steps, ranges, and comma-separated parts within valid bounds.

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

## Global attention and account surfaces

The shell-level **Attention** badge persists across destinations and polls one unified
shape. Every item is a real button that deep-links to the owning Alpha/Task/plan/Settings
surface. Only server-marked `inline_ok` binary actions render beside the link; diff
review and open-text decisions navigate instead. The popover has loading, empty,
populated, and persistent retryable-error states, closes on Escape/outside click, and
becomes a viewport-bounded sheet on narrow screens.

Agents and Settings live in the profile/account menu rather than the navigation. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge & Wiki, including files, links, graph, and search. Settings also owns Alpha budgets and **Help & Tours**: a replayable four-step core tour plus feature-aware product-map chapters. The first post-setup main UI shows the core tour once; it traps keyboard focus, supports Escape/skip, and stores completion server-side. The **top bar** owns the brand mark (far left), the sidebar collapse toggle, search, Attention, and the account menu; the mobile drawer keeps its own brand copy since the top bar hides below the tablet breakpoint. Global search includes user-facing Chat and Design sessions but excludes Alpha's hidden system thread, so raw product-tool calls and tool-result payloads never become search results.

Projects remain shared application entities: one active project across the app. Archive records and Designs remain owned by their Project.

## Projects

Projects is a **card grid**, not a master/detail pair — a project carries a name and a slug,
which is not enough to earn a permanent detail panel. It reuses the same shell as
the shared list shell (`.tasks-view` + `.tasks-head` + `.wf-grid`/`.wf-card`): search
on the left of the bar, **Add project** on the right, one card per project.

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

Primary screens (Chat, Tasks, Recipes, Projects, Archive, the task workspace, the shell itself) never show the words "runner", "MCP", or "profile", env-var names, raw tool payloads, or raw stack traces. The plain words are **agent** and **tools**. Technical detail belongs to Settings, Agents, and docs. Alpha has one deliberate product-contract exception: its header says **Backing runner** because the owner explicitly chooses Claude/Codex/Grok/Hermes/Pi for the system identity; tool results render as job cards rather than raw JSON.

## Feature gates

Routes, sidebar destinations, session eligibility, search, and deep links must all honor the server feature configuration. A hidden destination must not become reachable through stale state. Gating must not reorder the remaining navigation.

## Responsive and accessibility behavior

The left navigation width persists locally. Its separator supports pointer input and keyboard Arrow keys and exposes vertical separator orientation plus minimum, maximum, and current values. At mobile widths navigation uses a drawer, the tool rail pins to the right edge, and the Task Composer and Alpha controls stack without changing semantics. Account actions use ordinary disclosure/popover semantics. Escape dismisses transient shell overlays (including the tool panel and Attention); the modal core tour traps focus until completed/skipped. Focus indicators use shared tokens, and reduced-motion preferences apply globally.

## Extension points

Add destinations through the existing `View`, feature policy, App routing, Sidebar, and SearchModal boundaries together. Every new destination must declare whether it belongs to the flow navigation or the global account layer; new tools belong on the rail, not in the nav. Destination-specific inspectors remain owned by their destination rather than the application shell.

## Validation

For shell changes, run `npm --prefix apps/web test`, `npm --prefix apps/web run build`, and `git diff --check`. Tests should cover navigation order and feature-off gating, tool-rail open/close with Terminal persistence, asynchronous task success/failure, declared schedule inputs, cron grammar, and keyboard resizing. Browser QA should check authenticated desktop and narrow layouts, focus order, themes, zoom, and reduced motion; if authentication prevents inspection, record that rather than using credentials.
