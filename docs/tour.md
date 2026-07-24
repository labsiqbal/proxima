# Visual tour

This is Proxima as it ships now: a single-user, self-hosted control plane for
hands-on agent work and delegated agent teams. Every screenshot on this page was
captured on 2026-07-24 by driving the **worktree UI** for branch
`fm/proxima-alpha-ui-and-tour-refresh` (isolated `scripts/dev`-style API + Vite
on loopback, not the shared `~/preview/proxima` checkout). The pass used a
disposable owner DB, the starter project after onboarding **Skip**, and the
post-A1/A2 shell (destinations-only left nav; Alpha aligned with Deck chrome).

Feature details live in [CAPABILITIES.md](CAPABILITIES.md). This page focuses on
what an owner sees and how the surfaces connect.

## Live-pass matrix

| Surface | Result | Screenshot |
| --- | --- | --- |
| First-run password | pass (prior main capture; gate UI unchanged) | `first-run-password.png` |
| Onboarding Link tab | pass (prior main capture; folder picker unchanged) | `onboarding-link-folder.png` |
| Onboarding Create tab | skip this commit (Link tab retained; Create exercised live, not filed as a separate shot) | - |
| Core tour (4 steps) | pass | `core-tour-*.png` |
| Chat empty default | pass - no primary-nav New chat; header New chat kept | `deck-chat.png` |
| Chat send / approvals / restore | skip - no live agent turn in this pass | - |
| Alpha empty + Grok backing | pass - post visual parity | `alpha-desk.png` |
| Alpha populated / checkpoint restore | skip - no worker jobs in this pass | - |
| Attention inbox (empty) | pass | `attention-inbox.png` |
| Tasks list / board / New task | pass (empty project honest) | `tasks-*.png`, `task-launcher.png` |
| Recipes home / editor / schedules | pass | `workflows-home.png`, `workflow-blank-canvas.png`, `schedules.png` |
| Projects | pass | `projects.png` |
| Archive registry | pass (empty) | `archive-registry.png` |
| ArtifactViewer v2 deep review | skip - no live artifacts this pass | - |
| Design home | pass | `design-home.png` |
| Design studio canvas | skip - not opened beyond home | - |
| Terminal / Files / Preview rails | pass | `terminal.png`, `files.png`, `preview-rail.png` |
| Search | pass | `search.png` |
| Settings (appearance, Alpha, agents, diagnostics) | pass | `settings-*.png` |
| Help & Tours / Core flow chapter | pass | `help-tours.png`, `help-core-flow.png` |
| Agents profiles + runner picker | pass - Grok listed among ready runners | `agents-profiles.png`, `grok-runner-picker.png` |
| Skills & MCP / bundled masterplan | pass | `skills-mcp.png`, `bundled-masterplan-skill.png` |
| Wiki under Settings | pass | `wiki.png` |

## 1. The workspace

The shell keeps primary destinations on the left, the current surface in the
center, and technical tools on the right. Left nav is **destinations only**:
Chat, Alpha, Tasks, Recipes, Projects, Archive, and feature-gated Design. There
is no primary-nav **New chat** row - a blank session starts from the Chat header
control, the mobile topbar icon, or `/new`.

![Chat in the shared workspace](screenshots/deck-chat.png)

The first post-setup visit offers a four-step core tour. It explains the two ways
to work, hands-on Chat, delegated Alpha work, and the Tasks plus Attention review
loop. The same tour can be replayed later from Settings → Help & Tours.

![Core tour: two ways to work](screenshots/core-tour-work-modes.png)

![Core tour: hands-on Chat](screenshots/core-tour-chat.png)

![Core tour: Alpha delegation](screenshots/core-tour-alpha.png)

![Core tour: Tasks and Attention](screenshots/core-tour-review.png)

## 2. First run

A new install sets one owner password. Proxima is still a single-user tool: the
password is defense in depth behind the owner's loopback, Tailnet, or other
network boundary, not a multi-tenant account system.

![Set the owner password](screenshots/first-run-password.png)

The optional folder step can **Link** an existing workspace, **Create new folder**,
or **Skip for now** to use the starter project. This pass used Skip after reviewing
the Link tab.

![Choose a working folder (Link existing)](screenshots/onboarding-link-folder.png)

## 3. Chat: hands-on work

Chat is the direct path. An empty Chat is the default blank composer - no session
until the first send. Pick an agent, type a prompt or `/` for commands, and use
the header **New chat** action when you want another blank thread.

![Empty Chat with destinations-only nav](screenshots/deck-chat.png)

**Honest boundary:** live agent send, tool approval cards, turn restore, slash
masterplan intake, and `@` artifact mentions were not re-driven in this chrome
refresh pass. Those flows remain shipped; they are not pictured here as fresh
evidence.

## 4. Alpha: delegate and monitor

Alpha is a navigation peer to Chat. Its desk reuses Deck chrome: shared main-pane
ambience, `code-header` style bar, Settings-sized toggle and select, ghost-button
examples, and surface cards without a separate marketing page skin.

This pass selected **Grok** as the backing runner (host reported it ready) and
captured the honest empty desk: capacity 0/3 free, unattended off, empty queue,
empty Attention, empty checkpoints.

![Alpha desk with Grok backing, empty capacity and side rails](screenshots/alpha-desk.png)

The profile runner picker shows readiness for every installed runner.

![Runner picker with installed runners](screenshots/grok-runner-picker.png)

Unattended budgets remain under Settings → Alpha.

![Alpha unattended budgets](screenshots/settings-alpha.png)

## 5. Attention and Tasks

The shell Attention badge opens a global inbox. With no blocked work it states
that nothing needs you.

![The global Attention inbox (empty)](screenshots/attention-inbox.png)

Tasks is the durable execution and review index. An empty project is shown
honestly.

![Tasks list (empty project)](screenshots/tasks-list.png)

![Tasks board](screenshots/tasks-board.png)

**New task** opens a focused launcher with Project, Agent, and Guarded or
Autonomous policy.

![New task launcher](screenshots/task-launcher.png)

## 6. Recipes and scheduled plans

Recipes is the repeatable-work layer.

![Recipes home](screenshots/workflows-home.png)

The editor opens a blank plan canvas with trigger and first step.

![Editable plan canvas](screenshots/workflow-blank-canvas.png)

Scheduled Recipes use five-field cron and an enabled toggle. Empty state is
honest when no Recipe is saved yet.

![Scheduled Recipes](screenshots/schedules.png)

## 7. Archive and Projects

Archive remembers deliverables as durable records. Empty registry:

![Archive registry](screenshots/archive-registry.png)

Projects is a card grid around the active work container.

![Projects](screenshots/projects.png)

## 8. Tool rail

Terminal, Files, and Preview are tools, not destinations. They open over the
current surface and remain scoped to the active project.

![Connected terminal](screenshots/terminal.png)

![File tree and editor](screenshots/files.png)

![Preview rail](screenshots/preview-rail.png)

Global search covers user-facing chats, messages, projects, and designs.

![Global search](screenshots/search.png)

## 9. Design Studio

Design is present only when its server-owned feature flag is on. The home accepts
a brief, format, brand guide, or size template.

![Design home](screenshots/design-home.png)

## 10. Agents, knowledge, settings, and help

Agent profiles choose a ready runner, isolated home, instructions, and detected
skills or MCP servers.

![Agent profiles](screenshots/agents-profiles.png)

![Skills and MCP](screenshots/skills-mcp.png)

![Bundled masterplan skill](screenshots/bundled-masterplan-skill.png)

Knowledge and Wiki stays under Settings rather than adding another primary
destination.

![Knowledge and Wiki](screenshots/wiki.png)

Help and Tours provides the replayable core tour plus feature-aware chapters.

![Help and Tours chapters](screenshots/help-tours.png)

![Core flow help chapter](screenshots/help-core-flow.png)

Account preferences include themes, font choice, and font-size scaling.
Diagnostics keeps update checks, debug logs, and the owner audit trail.

![Appearance settings](screenshots/settings-appearance.png)

![Agents settings](screenshots/settings-agents.png)

![Diagnostics](screenshots/settings-diagnostics.png)

## Live-pass notes

- **Passed:** onboarding path (Skip), core tour replay, destinations-only nav,
  empty Chat default, Alpha desk visual parity with Grok selected, Attention,
  Tasks list/board/launcher, Recipes home/editor/schedules, Projects, Archive
  empty registry, Design home, tool rails, Search, Settings sections, Agents
  profiles, Skills/MCP, Wiki, Help.
- **Skipped (honest):** live Chat agent turns, approvals, turn restore,
  masterplan intake, `@` mentions, Alpha worker dispatch and checkpoint restore,
  populated Tasks review, ArtifactViewer v2 deep review, Design studio canvas
  beyond home, script approval / validate sidecar. Those surfaces still ship;
  this pass prioritized shell/nav + Alpha chrome consistency over replaying
  multi-minute agent runs.
- **Preview source:** worktree UI at branch tip via isolated loopback API
  (`PROXIMA_DEV_ID=alpha-ui-tour`) + Vite; shared `preview-proxima` was **not**
  rewritten (crewmate isolation rule).
- **Nav note:** desktop primary nav no longer includes New chat; Chat header and
  mobile topbar keep a compact blank-session control; `/new` remains.
