# Proxima Product Vision

Updated: 2026-06-27

Proxima is an opinionated agent harness and workspace OS. It gives AI agents full execution capacity inside a project, while keeping humans in control through visible actions, structured outputs, editable surfaces, memory, and project-scoped ownership.

> **Release scope:** Design Studio references below describe a retained future flow
> that is disabled by default. Image generation remains active; Video Studio is not
> a current product surface.

The product is not just a chat wrapper. Chat is the gateway. The real product is the workspace that turns conversations into editable work products: designs, files, wiki notes, workflows, apps, reports, and artifacts.

## Positioning

Proxima is an AI agent cockpit where agents execute across projects, and humans steer, inspect, edit, organize, reuse, and publish the results.

Technical category:

- Agent harness
- Workspace OS
- Runner-agnostic AI cockpit

Product promise:

- Agents can do real work.
- Outputs are structured and editable.
- Work is owned by the active project.
- Memory evolves with the agent and project.
- Users can always inspect what happened.

## Product Principles

### Chat Is The Gateway

Chat is the most flexible starting point, but it is not where all work should live.

Chat should:

- accept open-ended user intent,
- ask clarifying questions when intent is unclear,
- execute directly when intent is clear,
- show tool cards as process receipts,
- produce result cards for concrete outputs,
- route users into the right surface.

Chat should not become the detailed workspace for every task. Detailed work belongs in Design, Files, Wiki, Workflows, Activity, or Artifacts.

### Active Project Owns All Writes

Every meaningful action happens inside an active project.

Default write behavior:

- Chat in Project A writes outputs to Project A.
- Design created in Project A belongs to Project A.
- Wiki saves from Project A go to Project A.
- Workflow runs in Project A produce Activity and Artifacts in Project A.
- Chat in Main Project writes to Main Project.

Cross-project access is intentionally narrow:

- no implicit cross-project context,
- no cross-project writes by default,
- cross-project reads are allowed only when explicitly requested,
- cross-project read-only access is limited to files and artifacts.

This keeps context clean and prevents project state from blending accidentally.

### Home Is Global

Home is the exception to active-project scoping. Home is a global resume dashboard across projects.

Home should show:

- continue points,
- running/failed/review-needed work,
- recent outputs,
- project shortcuts.

Home is not a setup checklist or a marketing page. It is a cockpit for resuming work.

### Main Project Is The Global Workspace

Proxima has a pinned Main Project above regular projects.

Main Project is for:

- global brainstorming,
- abstract ideas,
- reusable memory,
- long-term user knowledge,
- global/plugin-like workflows,
- product-level thinking outside a specific project.

Regular projects are for project-specific execution.

### Outputs Should Become Structured Surfaces

When an agent creates something concrete, Proxima should route the result to the surface that owns it.

Examples:

- design output opens in Design Studio,
- wiki note opens in Wiki,
- normal project file opens in Files,
- workflow draft opens in Workflow Editor,
- app preview opens in Artifacts,
- generated image/document/app appears in Artifacts.

Chat result cards should be structured by Proxima, not merely written as text by the agent.

### Observability Is The Primary Safety Layer

Proxima is designed for full agent capacity. Inside the active project, agents can edit files, run commands, create outputs, install dependencies, and execute work when user intent is clear.

The product should earn trust by making work visible:

- tool cards in chat,
- Activity for tracked execution,
- Debug logs in Settings,
- result cards,
- clear run status,
- per-project artifacts,
- confirmation for destructive external actions.

Approval is not the default blocker for all work. It is used when intent is ambiguous, external destruction is involved, or the agent needs a decision.

### Memory Evolves

Proxima has evolving memory at two levels:

- Agent memory: global, per agent/profile.
- Project memory: project-specific instructions and wiki.

Agent memory is stored conceptually as `AGENT.md`. It captures durable behavior, preferences, and working style for that agent.

Project instructions are stored as `AGENTS.md`. They capture project-specific rules, conventions, commands, and constraints.

Project wiki is the source of truth for project knowledge. `wiki/index.md` is the living summary and index.

Instruction priority:

1. Proxima preamble
2. Agent Memory / `AGENT.md`
3. Project Instructions / `AGENTS.md`
4. Current user request or workflow step

More specific instructions override broader ones.

### Artifacts Are The Output Library

Artifacts is the universal gallery for outputs in the active project.

Artifacts is not limited to files physically stored under `artifacts/`. The `artifacts/` folder is a default dropzone, but the artifact registry may point to:

- Design Studio scenes,
- generated images,
- PDFs and documents,
- HTML outputs,
- datasets,
- runnable app folders,
- workflow/job outputs.

Artifacts should filter by:

- All
- Design
- Image
- Document
- App
- Data
- Other

### Files Are Raw Workspace Access

Files is a utility surface for inspecting and editing the project filesystem. It should keep quick single-file preview for HTML/Markdown, but app preview and output consumption should live in Artifacts.

### Workflows Are Reusable Processes

Workflows are project-scoped reusable processes. A workflow starts as draft, can be iterated and tested, then published as active.

Main Project can hold global/plugin-like workflows. Regular projects can have local workflows. Project A cannot use Project B workflows directly.

Workflow versioning is required:

- one active version per workflow,
- edits create draft versions,
- users can publish, restore, or discard versions,
- manual runs and schedules can choose a version.

Workflow Iterate is a lab for improving the workflow definition, not for editing one run's final output.

### Activity Tracks Execution

Activity is the per-project execution/history surface.

Always tracked in Activity:

- `/goal`,
- workflow runs,
- scheduled runs.

Agent/harness may also create Activity for longer, multi-step, reviewable, or multi-output work.

Normal chat does not need Activity.

### Tasks Are Not A Primary Surface

Top-level Tasks are likely redundant. Ad-hoc work starts from Chat or `/goal`. Reusable work belongs in Workflows. Execution status belongs in Activity.

If task data remains internally, it should behave as a one-step job, not as a separate top-level product concept.

### Integrations Are Global Connections With Project Bindings

Integrations such as GitHub, Cloudflare, Google, and MCP servers are connected globally, then bound to projects.

Rules:

- connections are global,
- project bindings determine what a project can use,
- agents can only use integrations bound to the active project,
- agents are global and usable across all projects,
- destructive external actions require confirmation.

Runner/agent is not the same as integration. The runner executes. The integration supplies external capability.

### Terminal Is A Global Utility

Terminal remains a global utility, opening in the active project by default. It is a power tool and escape hatch, not a primary product surface.

### Remote Access And Publish Are Separate

Cloudflare/remote access has two different product modes:

- private remote access to Proxima for the owner, including mobile use,
- explicit publish/share of artifacts or apps for portfolio/demo use.

Everything remains private until the user explicitly publishes it.

## Target Navigation

Primary:

```text
Home
Chat
Design
Workflows
Activity
Artifacts
```

Utility/Profile:

```text
Files
Wiki
Projects
Agents
Terminal
Integrations
Remote Access
Settings
Debug Logs
```

## Open Product Details

These are intentionally not finalized yet:

- exact result card visual design,
- exact back-to-chat routing implementation,
- artifact registry schema,
- workflow version UI,
- integration settings layout,
- Main Project final naming,
- artifact/app publish flow,
- stuck/error observability polish.
