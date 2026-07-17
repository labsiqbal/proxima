# UI shell and information architecture

This is the durable contract for Proxima's application shell. It describes product routing and lifecycle boundaries; API and database details remain in the generated reference docs.

## Information architecture

The desktop shell has a persisted, collapsible left navigation rail and a destination work surface. There is no generic right panel. **Ops and Code are separate workspace contexts inside the same application window**, and the left sidebar adapts to the selected workspace.

Each workspace remembers its last destination. Switching workspaces restores that destination rather than resetting the current job, chat, project, terminal, filters, or scroll state. Global destinations such as Projects, Agents, and Settings do not redefine the selected workspace.

## Ops workspace

Ops is for orchestration and deliverables. Its sidebar contains:

- New task
- Tasks
- Projects
- Workflows
- Artifacts
- feature-gated Design
- an Advanced group for feature-gated Video

Workflow Graphs are **not** a sidebar destination — they are the Advanced mode inside Workflows (see below). The sidebar's Advanced group holds Video alone.

Tasks is the durable execution/review index for queued, running, review, done, failed, and archived work. Ops Home and workflow runs open the same task workspace rather than a generic Activity subview.

Ops Home is a focused launcher with no destination dashboard grid. Its integrated Task Composer splits into two rows by kind. The prompt row carries only *actions*: the Add menu for attachments/image/design, and the start action. A context bar underneath groups the three controls that describe a task's **execution context** — a searchable Project/folder picker (where it runs), Agent (who runs it), and Guarded or Autonomous execution policy (how it is governed). Each context control carries a leading icon inside its own click target and all three share one type scale, so the bar reads as one row of peers rather than three unrelated widgets. The Agent selector belongs to that group, not beside the start action. Code-only Normal/Brainstorm/Debate controls are absent. `/image` and feature-gated `/design` create real media runs that are linked back to the durable task lifecycle. A created task opens `#task/<id>` with live progress, review, approval, and deliverables. Ordinary start failures clean up the queued task; media link failures preserve and identify the task for inspection.

## Code workspace

Code is for direct ACP sessions. Its sidebar contains:

- New session
- Projects
- Terminal
- project-scoped recent sessions

Terminal belongs only to Code and remains mounted after its first visit so PTY processes survive navigation and workspace switches. Code restores its previous chat or Terminal view when selected. New session starts blank, `/new` remains available, and the database session is created lazily on first message.

## Workflows and schedules

The **top bar** owns the brand mark and the Ops/Code workspace switcher (brand at the
far left), so collapsing the sidebar never takes away who you are or where you can go.
The mobile drawer keeps its own copy — the top bar hides below the tablet breakpoint.

Workflows appears once in Ops navigation. Its screen owns two modes:

- **Editor** is the workflow graph canvas (feature-gated behind
  `PROXIMA_FEATURE_WORKFLOW_GRAPH`; with the flag off the mode explains how to enable it).
  It has an **authoring chat** on the left under the standing rule — typing drives the
  plan on screen, never the database — which hands back a `<workflow-graph>` (nodes +
  edges), so the agent can propose branches rather than a straight line. The chat is
  pinned to the graph job's own session, so reopening a plan resumes its conversation.
  The editor is **canvas-first**: node-level actions stay with the node; the plan list
  collapses; and the node inspector exists only while a node is selected, so an unused
  panel never holds canvas width.
- **Scheduled** manages real schedule rows for saved graph templates. A schedule renders
  its input form from the template's declared `{{inputs}}`, and a due tick spawns the
  same `engine='graph'` job a manual run produces.

The **Sequential recipe editor is retired**: a linear recipe is a graph with no branches,
and the canvas authors those too. The linear *engine* remains for pre-existing jobs and
sessions (`IterateStage` is still reachable from an old session carrying `workflow_id`),
but no new linear workflow can be authored. The retirement was deliberate and owner-driven;
node parity landed first (per-node `expected_output`/`rules`, `{{var}}` substitution,
declared template inputs), then the scheduler bridge, so nothing only Sequential could do
was lost.

Schedule inputs mirror each workflow's declared definitions, validate required values, and serialize values by declared input ID. Workflows without declarations may receive an optional `brief`. Cron accepts exactly five fields using numbers, `*`, positive steps, ranges, and comma-separated parts within valid bounds.

Every schedule row offers **Run now**, which fires it immediately and opens the task it spawned. It exists so a schedule can be trusted before it is left alone: the run goes through the scheduler's own spawn, so what executes is what the cron would have executed — same workflow, project, agent profile and stored input — rather than a lookalike. A manual run deliberately does **not** claim the scheduler's minute, so running at 09:00 cannot swallow a real 09:00 tick, and it works on a disabled schedule, since `enabled` governs the tick and trying a schedule out is exactly when it is still switched off. The stored overlap policy is honoured but never silently: a `skip` schedule with a run already in flight reports that instead of appearing to do nothing.

## Global account surfaces

Agents and Settings live in the profile/account menu rather than either workspace sidebar. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge & Wiki, including files, links, graph, and search.

Projects remain shared application entities. The current implementation still uses one active project across Ops and Code; independent per-workspace project contexts are a separate product decision. Artifacts and Designs remain owned by their Project, not by an Ops or Code mode.

## Projects

Projects is a **card grid**, not a master/detail pair — a project carries a name and a slug,
which is not enough to earn a permanent detail panel. It reuses the same shell as
Workflows' Sequential mode (`.tasks-view` + `.tasks-head` + `.wf-grid`/`.wf-card`): search
on the left of the bar, **Add project** on the right, one card per project.

A card shows the name and slug, marks the **active** project (the one the rest of the app
is pointed at), and carries its own actions: the card body selects it, **Rename** opens a
prompt dialog, and the hover/focus **×** removes it. Add opens a modal holding both ways in:
create a new project, or link a folder you already work in.

Removal copy must distinguish the two cases, because the API does: a folder outside the
workspace root is only *unlinked* and its real files survive, while a project Proxima
created is deleted from disk. Chats and tasks go in both cases.

## Artifacts and Design

Artifacts is the durable destination for agent outputs and project files. Design is a separate canvas destination whose internals are not part of the shell. Design links are enabled only when the Design Studio feature gate is on; otherwise source artifacts remain available. The same separation applies to gated Video and Workflow Graph destinations.

## Feature gates

Routes, sidebar destinations, session eligibility, search, and deep links must all honor the server feature configuration. A hidden destination must not become reachable through stale state. Gating must not reorder the remaining workspace navigation.

## Responsive and accessibility behavior

The left navigation width persists locally. Its separator supports pointer input and keyboard Arrow keys and exposes vertical separator orientation plus minimum, maximum, and current values. At mobile widths navigation uses a drawer and the Task Composer context controls stack without changing task semantics. Account actions use ordinary disclosure/popover semantics. Escape dismisses transient shell overlays, focus indicators use shared tokens, and reduced-motion preferences apply globally.

## Extension points

Add destinations through the existing `View`, feature policy, App routing, workspace classification, Sidebar, and SearchModal boundaries together. Every new destination must declare whether it belongs to Ops, Code, or the global account layer. Destination-specific inspectors remain owned by their destination rather than the application shell.

## Validation

For shell changes, run `npm --prefix apps/web test`, `npm --prefix apps/web run build`, and `git diff --check`. Tests should cover workspace restoration, adaptive sidebar membership, feature-off ordering, asynchronous task success/failure, Terminal persistence, declared schedule inputs, cron grammar, and keyboard resizing. Browser QA should check authenticated desktop and narrow layouts, focus order, themes, zoom, and reduced motion; if authentication prevents inspection, record that rather than using credentials.
