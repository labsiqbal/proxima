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
- an Advanced group for feature-gated Workflow Graphs and Video

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

- **Sequential** authors, iterates, and runs ordered recipes.
- **Advanced** opens the feature-gated dependency graph for parallel, reviewable orchestration.
- **Scheduled** manages real schedule rows for Sequential workflows.

Schedule inputs mirror each workflow's declared definitions, validate required values, and serialize values by declared input ID. Workflows without declarations may receive an optional `brief`. Cron accepts exactly five fields using numbers, `*`, positive steps, ranges, and comma-separated parts within valid bounds.

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
