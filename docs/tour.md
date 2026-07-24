# Visual tour

This is Proxima as it ships now: a single-user, self-hosted control plane for
hands-on agent work and delegated agent teams. Every screenshot on this page was
captured on 2026-07-24 by driving a live preview refreshed to current `main`.
The tour used a disposable owner project and real Claude Code and Grok turns.

Feature details live in [CAPABILITIES.md](CAPABILITIES.md). This page focuses on
what an owner sees and how the surfaces connect.

## 1. The workspace

The shell keeps primary work on the left, the current surface in the center, and
technical tools on the right. The flow is **Chat or Alpha, then Tasks, then
Recipes**. Projects, Archive, and the feature-gated Design Studio sit beside that
flow. Attention and account actions stay available from every destination.

![Chat in the shared workspace](screenshots/deck-chat.png)

The first post-setup visit offers a four-step core tour. It explains the two ways
to work, hands-on Chat, delegated Alpha work, and the Tasks plus Attention review
loop. The same tour can be replayed later from Settings.

![Core tour: two ways to work](screenshots/core-tour-work-modes.png)

![Core tour: hands-on Chat](screenshots/core-tour-chat.png)

![Core tour: Alpha delegation](screenshots/core-tour-alpha.png)

![Core tour: Tasks and Attention](screenshots/core-tour-review.png)

## 2. First run

A new install sets one owner password. Proxima is still a single-user tool: the
password is defense in depth behind the owner's loopback, Tailnet, or other
network boundary, not a multi-tenant account system.

![Set the owner password](screenshots/first-run-password.png)

The optional folder step can link an existing workspace, create a folder, or use
the starter project. The screenshot blurs host-specific folder and account names.

![Choose a working folder](screenshots/onboarding-link-folder.png)

## 3. Chat: hands-on work

Chat is the direct path. Pick an agent, send a prompt, inspect tool activity, and
approve sensitive operations when the global permission setting is set to ask.
This live turn created a Markdown Mermaid diagram and an Excalidraw file.

![A completed Chat turn with produced artifacts](screenshots/chat-code.png)

With permissions set to ask, each write is visible in the turn's activity and is
approved separately. Choosing **Always Allow** is explicit; this pass used the
one-time **Allow** action.

![Tool activity and approval history](screenshots/chat-approval.png)

A file-changing turn gets a session-scoped restore control. Proxima previews the
changed paths before applying anything and refuses restores that could conflict
with later work in the same project.

![Review the paths affected by turn restore](screenshots/chat-turn-restore.png)

Typing `/` opens the server command catalog. `/masterplan` is a first-class
Planning command, not a hidden prompt convention.

![The slash palette with masterplan](screenshots/masterplan-command.png)

A bare `/masterplan` starts the bundled methodology and asks for the idea and its
current state before research or planning. Follow-up turns keep the skill active
for that session.

![Interactive masterplan intake](screenshots/masterplan-intake.png)

Typing `@` opens the shared project reference picker. Produced artifacts are
ranked with files and carry kind labels such as File, Doc, Design, and Image.
The picker inserts paths or image Markdown without expanding file contents into
the prompt.

![Project files and artifacts in the mention picker](screenshots/artifact-mentions.png)

## 4. Alpha: delegate and monitor

Alpha is a navigation peer to Chat. It is a built-in orchestrator with its own
hidden system identity, backed by the runner the owner chooses. The desk keeps
live capacity, the queue, needs-you decisions, unattended state, saved budgets,
and job-scoped checkpoints in one view.

This live pass selected **Grok**, which the host reported as ready, and asked Alpha
to create one verification artifact. The Grok turn made two separate one-job
dispatch calls despite the explicit request for one, so two real Autonomous jobs
completed and two checkpoints were recorded. The screenshot and this note preserve
that behavior honestly rather than presenting it as a single dispatch.

![Alpha desk backed by Grok, with capacity, Attention, and checkpoints](screenshots/alpha-desk.png)

The profile runner picker shows readiness for every installed runner. In this
capture Claude Code, Codex, Grok, and Pi were ready; Hermes was installed but
needed re-authentication.

![Runner picker with Grok ready](screenshots/grok-runner-picker.png)

Capacity is capped at three running Alpha children. Extra worker runs remain
queued, and the strip reports running, free, and queued counts separately. When
work is complete, the active queue returns to zero while the checkpoint timeline
remains.

A checkpoint restore is deliberately job-scoped. In this pass, Proxima refused a
restore because later same-project work existed. That refusal is the expected
safety boundary: it did not reset the project or silently overwrite newer jobs.

![Checkpoint timeline and a conflicting-work refusal](screenshots/alpha-checkpoint-restore.png)

Unattended mode remained off throughout the tour. Its turn and wall-clock budgets
are visible in Settings; a token budget is optional and only applies when the
backing runner reports usage.

![Alpha unattended budgets](screenshots/settings-alpha.png)

## 5. Attention and Tasks

Alpha created a real needs-you decision. The shell badge opened the global
Attention inbox, and the same decision appeared on the Alpha desk. Complex work
links back to its owning surface; only server-marked safe binary actions can run
inline.

![The global Attention inbox](screenshots/attention-inbox.png)

Tasks is the durable execution and review index. It combines one-off tasks and
plans, with filters for queued, running, review, done, failed, cancelled, and
archived work.

![Tasks list with completed Alpha jobs](screenshots/tasks-list.png)

The same work can be projected as a board without creating a second task model.

![Tasks board](screenshots/tasks-board.png)

**New task** opens a focused launcher. The brief is paired with Project, Agent,
and Guarded or Autonomous execution policy.

![New task launcher](screenshots/task-launcher.png)

A live Guarded task paused at its review gate. The generated result was readable
before **Approve to Done** made the task final.

![Guarded task review gate](screenshots/task-review.png)

Alpha-spawned jobs are ordinary durable Tasks. Their full workspace includes the
brief, run result, and linked output artifacts.

![Completed Alpha child task](screenshots/task-alpha-complete.png)

**Honest boundary for this capture:** the disposable starter project was not a git
code area and had no saved script recipe. Repo diff review and hash-bound script
trust are shipped and test-covered, but they were not triggered in this live pass.
The tour therefore does not reuse older screenshots to imply fresh live evidence.

## 6. Recipes and scheduled plans

Recipes is the repeatable-work layer. Its home separates editable drafts, saved
Recipes, and frozen run history. A plan can start from a brief or a blank canvas.

![Recipes home](screenshots/workflows-home.png)

The blank-canvas path opens the graph editor with a trigger and first step. Nodes
can be agent or script steps, carry typed output contracts, choose agents, and add
review gates. Branches can run in parallel once approved.

![Editable plan canvas](screenshots/workflow-blank-canvas.png)

Saved Recipes can be scheduled with five-field cron, overlap policy, declared
inputs, and an enabled toggle. This project had no saved Recipe yet, so the form
shows its honest empty state.

![Scheduled Recipes](screenshots/schedules.png)

## 7. Archive and ArtifactViewer v2

Archive remembers deliverables as durable records. It keeps project, type, status,
version, producing task or chat, date, and permanent address even when the live
file later moves or disappears.

![Archive registry with versioned outputs](screenshots/archive-registry.png)

A full record exposes preview, status, approval, versions, file location, and the
lineage back to its producing Chat or Task.

![A permanent Archive record](screenshots/archive-record.png)

ArtifactViewer v2 wraps the type-aware renderer in a native review workspace.
Markdown Mermaid fences render as diagrams inside Proxima, with annotation and
feedback controls beside the content.

![Mermaid rendered in ArtifactViewer v2](screenshots/artifact-review-mermaid.png)

Annotations are numbered points tied to review notes. They remain browser-local
until the owner chooses **Add feedback to chat**.

![A point annotation on a text artifact](screenshots/artifact-review-annotation.png)

**Edit as whiteboard** converts supported Mermaid diagrams to editable Excalidraw
elements without leaving Proxima. Saving is explicit and creates a project-relative
`.excalidraw` artifact.

![Mermaid converted to an editable Excalidraw whiteboard](screenshots/artifact-review-whiteboard.png)

Feedback handoff opens the artifact's producing Chat with an editable, path-linked
draft. Nothing is sent until the owner reviews and submits it.

![Artifact feedback handed to the producing Chat](screenshots/artifact-feedback-handoff.png)

## 8. Projects and the tool rail

Projects is a card grid around one active work container. Linking an external
folder does not move its files; removing it only unlinks the folder.

![Projects](screenshots/projects.png)

Code areas define where repo jobs may work in isolated copies. The starter project
had no git repository, so its dialog offered a scan or the explicit option to use
the whole project folder.

![Code-area setup](screenshots/container-settings.png)

Terminal, Files, and Preview are tools, not destinations. They open over the
current surface and remain scoped to the active project.

The Terminal is a real connected PTY. Its session remains mounted when the panel
closes so shell state survives navigation.

![Connected terminal](screenshots/terminal.png)

Files combines the project tree with an editor. The sample artifacts created in
Chat are visible immediately.

![File tree and editor](screenshots/files.png)

Preview detects common app entry points or accepts an owner-confirmed command. The
starter project had no app, so this capture shows the real no-app state instead of
a fabricated running preview.

![Preview rail with no app detected](screenshots/preview-rail.png)

Global search covers user-facing chats, messages, projects, and designs. Alpha's
hidden system thread and raw product-tool payloads are deliberately excluded.

![Global search](screenshots/search.png)

## 9. Design Studio

Design is present only when its server-owned feature flag is on. The home accepts
a brief, format, brand guide, or size template.

![Design home](screenshots/design-home.png)

A template opens an editable layered scene. Chat and assets stay on the left,
the canvas stays central, and the selected layer's inspector stays on the right.

![Design Studio canvas](screenshots/design-studio.png)

Selecting a text layer exposes typography, fill, opacity, alignment, spacing, and
effect controls while keeping the AI chat selection-aware.

![Selected text layer inspector](screenshots/design-studio-inspector.png)

## 10. Agents, knowledge, settings, and help

Agent profiles choose a ready runner, isolated home, instructions, and detected
skills or MCP servers. The bundled section shows that masterplan ships with
Proxima even when the host also has its own copy.

![Bundled masterplan in Skills and MCP](screenshots/bundled-masterplan-skill.png)

Knowledge and Wiki keeps project notes, wikilinks, backlinks, and graph navigation
under Settings rather than adding another primary destination.

![Knowledge and Wiki](screenshots/wiki.png)

Help and Tours provides the replayable core tour plus eight feature-aware chapters.
The Core flow chapter connects Chat, Alpha, Tasks, Attention, and restore safety.

![Help and Tours chapters](screenshots/help-tours.png)

![Core flow help chapter](screenshots/help-core-flow.png)

Account preferences include six themes, font choice, and font-size scaling. The
Diagnostics section keeps update checks, debug logs, and the owner audit trail.

![Appearance settings](screenshots/settings-appearance.png)

![Diagnostics](screenshots/settings-diagnostics.png)

## Live-pass notes

- **Passed:** setup, core tour, Chat send and approvals, turn restore preview and
  restore, `/masterplan`, `@` references, Alpha with Grok, real worker dispatch,
  capacity, checkpoints, unattended-off budgets, Attention, Tasks list/board and
  Guarded review, Recipes, Archive, annotation, Mermaid, Excalidraw, feedback
  handoff, Projects, Terminal, Files, Preview empty state, Design, Agents,
  Settings, Help, Wiki, and search.
- **Skipped with evidence stated above:** repo diff review, script trust, and a
  running app preview. The disposable project had no git code area, saved script
  recipe, or app entry point.
- **Observed:** Grok emitted two one-job dispatch calls for a prompt that requested
  exactly one. Both jobs completed; unattended remained off.
