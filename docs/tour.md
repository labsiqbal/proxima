# Visual tour

Every major surface of Proxima, captured from a real instance (v1.0.0, default
*Sunset* theme, demo project `atelier-notes`). Feature descriptions live in
[CAPABILITIES.md](CAPABILITIES.md); this page is the "what does it look like" layer.

## First run

Set the owner password, then optionally link a real folder as your first project.

![Set a password](screenshots/first-run-password.png)

![Pick your working folder](screenshots/onboarding-link-folder.png)

## Ops workspace

**Home** is a task launcher: describe an outcome, pick project + agent +
execution policy (Guarded / Autonomous), and go. Tasks needing your attention
surface right below.

![Ops home](screenshots/ops-home.png)

A **Guarded** task pauses at a review gate — the output is editable before you
approve it as done. Artifacts produced by the task are linked as chips.

![Task review gate](screenshots/task-review.png)

## Workflows (graphs)

The Workflows home: describe a process and the agent draws the graph, or start
from a blank canvas. Drafts, reusable templates, and run history live here.

![Workflows home](screenshots/workflows-home.png)

The authoring chat edits the plan on the canvas (never the database) — save the
plan explicitly, then approve it to start:

![Drafting a graph](screenshots/workflow-graph-draft.png)

Each node has an inspector: instruction, expected output, rules, its own agent,
a typed output contract (`text` / `json` / `artifact-ref`), a review-gate
checkbox, and dependency checkboxes.

![Node inspector](screenshots/workflow-node-inspector.png)

A running graph pauses at node review gates. You can **correct the output** (all
transitive descendants are marked stale and rerun) or rerun the node itself:

![Node review + correction](screenshots/workflow-node-review.png)

![Finished run](screenshots/workflow-graph-run.png)

**Schedules** run saved templates on five-field cron, with overlap policy and a
"Run now" that uses the real scheduler spawn path:

![Schedules](screenshots/schedules.png)

## Code workspace

Chat with streaming, tool-activity cards, and interactive approval cards
(auto-approve is an explicit Settings opt-in):

![Approval card](screenshots/chat-approval.png)

![A completed exchange](screenshots/chat-code.png)

**Brainstorm** fans a prompt out to parallel agent lanes and synthesizes;
**Debate** alternates rounds before a judge pass:

![Brainstorm](screenshots/brainstorm.png)

**Validate** asks a *different* runner to pressure-test a finished answer — with
a structured verdict, gaps/risks, and a revised version you can apply:

![Validate sidecar](screenshots/validate-sidecar.png)

`/image` generates images through your configured provider; results are saved
as project artifacts and can open in Design Studio:

![Image generation](screenshots/image-generation.png)

A real PTY terminal, scoped to the project:

![Terminal](screenshots/terminal.png)

Global search covers chats, messages, projects, and designs:

![Search](screenshots/search.png)

## Design Studio

The Design home takes a brief (Graphic / Slide deck / Mobile app / Website) or a
size template, and can generate a per-project brand guide from reference URLs
and images:

![Design home](screenshots/design-home.png)

The agent replies with an editable layered scene that the canvas applies live —
text stays text, shapes stay shapes:

![Design Studio](screenshots/design-studio.png)

Select any layer for direct manipulation with a full inspector; the studio chat
is selection-aware. Export as PNG/JPG/PDF/HTML.

![Layer inspector](screenshots/design-studio-inspector.png)

## Artifacts

The project's output gallery — visual artifacts up top, files/apps/documents
below, filterable by type, with type-aware viewers:

![Artifacts gallery](screenshots/artifacts-gallery.png)

![Markdown viewer](screenshots/artifact-viewer.png)

**Run & Preview** launches a project app (owner-confirmed) and previews it
behind a credential-stripping proxy:

![App preview](screenshots/app-preview.png)

## Projects, agents, settings

![Projects](screenshots/projects.png)

Agent profiles: per-profile runner, isolated credential home, instructions, and
skills/MCP selection detected from the runner's own host config:

![Agent profiles](screenshots/agents-profiles.png)

![Skills & MCP manager](screenshots/skills-mcp.png)

![Runner detection](screenshots/settings-runners.png)

Wiki notes with `[[links]]`, backlinks, and a graph view live under Settings →
Knowledge & Wiki:

![Wiki](screenshots/wiki.png)

Appearance (six themes, font + size), and Diagnostics (updates / debug logs /
audit log):

![Appearance](screenshots/settings-appearance.png)

![Diagnostics](screenshots/settings-diagnostics.png)

Dark theme:

![Dark theme](screenshots/ops-home-dark.png)
