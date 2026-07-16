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

Workflows appears once in Ops navigation. Its screen owns three modes:

- **Sequential** authors, iterates, and runs ordered recipes. The editor is one window with an **authoring chat on the left** (like Design Studio's chat to a canvas) and the recipe **form on the right**. The chat is a conversation *about* the workflow, pinned to it and kept out of Code (the session list excludes workflow-linked sessions). It does two jobs, disambiguated by prompt mode the way Design does it, not a second box:
  - **Authoring** — typing drives the form. The run carries a fat prompt (mode + recipe schema + the live recipe JSON) while the thread shows only the short text; any `<workflow-recipe>` block the agent returns is parsed straight into the form (full fidelity — steps, rules, skills, review gates), which the owner then Saves. Applied to the *form*, never the database directly: the recipe is on screen, so a background write would leave the editor stale and let the next Save undo the agent's work. A blank draft is seeded (name + one placeholder step) so the chat can open and fill it in. Reopening does not re-apply an old reply over edits made since.
  - **Testing** — a **step outline on the right** (`.wf-outline`) lists the steps with a count and gate markers, and is the workflow's table of contents for a form that gets long: clicking a step jumps the form to it and flashes it. Each outline row carries its own **Run test**, which runs the recipe *through* that step (steps 1..N, inlining the live form so unsaved edits count) and shows the result in the chat. Testing step N runs 1..N because a step's output only means anything with its upstream context; the reply carries no recipe block, so the form is left alone.

  There is no separate Iterate action — opening a recipe *is* opening its authoring chat.
- **Advanced** opens the feature-gated dependency graph for parallel, reviewable orchestration. Where Sequential is chat-and-form, Advanced is **canvas-first**: the canvas is the workspace and everything else yields to it. Node-level actions stay with the node; the plan list collapses; and the node inspector exists only while a node is selected, so an unused panel never holds canvas width.
- **Scheduled** manages real schedule rows for Sequential workflows.

The modes are one tab click apart, so the bar directly under the tabs is **one shared shell**, not a per-mode invention: same height, padding, gap and rule, with the project picker at the left in the shared `Dropdown` (never a native `<select>`) and the mode's actions at the right — Sequential's *New workflow*, Advanced's *Save template / Save plan / Approve plan & start*. Sequential and Advanced express this from a single CSS rule so they cannot drift apart again. No mode repeats its own tab name back at the user in a title block; the tab already said it.

**Scheduled does not yet follow this.** It still opens with a centred `AUTOMATION / Scheduled / Run real workflows…` title block and has no bar, because it has no bar-level controls (schedules are not project-scoped). Aligning it means deciding where its cron explanation lives — an open call, not an oversight.

Schedule inputs mirror each workflow's declared definitions, validate required values, and serialize values by declared input ID. Workflows without declarations may receive an optional `brief`. Cron accepts exactly five fields using numbers, `*`, positive steps, ranges, and comma-separated parts within valid bounds.

Every schedule row offers **Run now**, which fires it immediately and opens the task it spawned. It exists so a schedule can be trusted before it is left alone: the run goes through the scheduler's own spawn, so what executes is what the cron would have executed — same workflow, project, agent profile and stored input — rather than a lookalike. A manual run deliberately does **not** claim the scheduler's minute, so running at 09:00 cannot swallow a real 09:00 tick, and it works on a disabled schedule, since `enabled` governs the tick and trying a schedule out is exactly when it is still switched off. The stored overlap policy is honoured but never silently: a `skip` schedule with a run already in flight reports that instead of appearing to do nothing.

## Global account surfaces

Agents and Settings live in the profile/account menu rather than either workspace sidebar. Runner management is part of Settings → Agents. Project Wiki is part of Settings → Knowledge & Wiki, including files, links, graph, and search.

Projects remain shared application entities. The current implementation still uses one active project across Ops and Code; independent per-workspace project contexts are a separate product decision. Artifacts and Designs remain owned by their Project, not by an Ops or Code mode.

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
