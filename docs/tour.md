# Visual tour

This is Proxima as it ships now: a single-user, self-hosted control plane for
hands-on agent work and delegated agent teams. It is under **active development**
(expect roughness; see [installation](installation.md#what-to-expect)). Every
screenshot on this page was captured on 2026-07-24 in one uniform pass on branch
`fm/proxima-screenshot-uniform-refresh` (HEAD of main after #37 orchestrator UI parity
and destinations-only nav). Capture used a disposable owner DB and isolated
`scripts/dev`-style API + Vite on loopback at a fixed viewport of **1440×1000**,
sidebar expanded, Light theme, starter project after onboarding **Skip**.

All primary tour shots share the same Deck chrome: destinations-only left nav,
shared main-pane
ambience, and tool rails on the right. Older multi-era PNGs were deleted and
replaced. The old orchestrator screenshots used the Alpha compatibility name and
are intentionally not presented as current Master evidence. On 2026-07-27 the
renamed Master desk was re-driven against the production bundle at desktop and
390x844 mobile viewports with no page overflow or visible Alpha copy.

Feature details live in [CAPABILITIES.md](CAPABILITIES.md). This page focuses on
what an owner sees and how the surfaces connect.

## Live-pass matrix

| Surface | Result | Screenshot |
| --- | --- | --- |
| First-run password | pass | `first-run-password.png` |
| Onboarding Link tab | pass (folder names redacted where personal) | `onboarding-link-folder.png` |
| Onboarding Create tab | pass | `onboarding-create-folder.png` |
| Core tour (4 steps) | pass | `core-tour-*.png` |
| Chat empty default | pass - no primary-nav New chat; header New chat kept | `deck-chat.png` |
| Chat send / approvals / restore | skip - no live agent turn in this pass | - |
| Master empty | pass - production-bundle browser smoke, feature enabled | - |
| Master runner eligibility | pass - replayable candidate browser assertion enables only server-qualified choices | - |
| Master populated / checkpoint restore | skip - no worker jobs in this pass | - |
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
| Settings (appearance, agents, diagnostics) | pass | `settings-*.png` |
| Help & Tours / Core flow chapter | pass | `help-tours.png`, `help-core-flow.png` |
| Agents profiles | pass | `agents-profiles.png` |
| Skills & MCP / bundled masterplan | pass | `skills-mcp.png`, `bundled-masterplan-skill.png` |
| Wiki under Settings | pass | `wiki.png` |
| Mobile shell | skip - tour does not claim mobile shots | - |

## 1. The workspace

The shell keeps primary destinations on the left, the current surface in the
center, and technical tools on the right. Left nav is **destinations only**:
Chat, Tasks, Workflows, Archive, and feature-gated Design. Master appears between
Chat and Tasks only when its server gate is enabled. There
is no primary-nav **New chat** row - a blank session starts from the Chat header
control, the mobile topbar icon, or `/new`.

![Chat in the shared workspace](screenshots/deck-chat.png)

The first post-setup visit offers a keyboard-trapped core tour. By default it is
four steps - the primary loop, hands-on Chat, Tasks and Workflows, and Archive;
a fifth **delegated Master work** step appears only when the Master feature is
enabled (see [CAPABILITIES.md](CAPABILITIES.md)). The same tour can be replayed
later from Settings → Help & Tours.

![Core tour: two ways to work](screenshots/core-tour-work-modes.png)

![Core tour: hands-on Chat](screenshots/core-tour-chat.png)

![Core tour: Tasks and Attention](screenshots/core-tour-review.png)

## 2. First run

A new install sets one owner password. Proxima is still a single-user tool: the
password is defense in depth behind the owner's loopback, Tailnet, or other
network boundary, not a multi-tenant account system.

![Set the owner password](screenshots/first-run-password.png)

The optional folder step can **Link** an existing workspace, **Create new folder**,
or **Skip for now** to use the starter project. This pass captured both Link and
Create tabs, then used Skip for the rest of the tour.

![Choose a working folder (Link existing)](screenshots/onboarding-link-folder.png)

![Create a new empty folder](screenshots/onboarding-create-folder.png)

## 3. Chat: hands-on work

Chat is the direct path. An empty Chat is sparse by default (title + short lead,
tooltip hints, **How it works** for the fuller path) - no session until the first
send. Master empty and Design home use the same progressive-disclosure pattern.
Pick an agent, type a prompt or `/` for commands, and use the header
**New chat** action when you want another blank thread.

![Empty Chat with destinations-only nav](screenshots/deck-chat.png)

**Honest boundary:** live agent send, tool approval cards, turn restore, slash
masterplan intake, and `@` artifact mentions were not re-driven in this chrome
refresh pass. Those flows remain shipped; they are not pictured here as fresh
evidence.

## 4. Master: delegate and monitor

Master is a navigation peer to Chat. Its desk reuses Deck chrome: shared main-pane
ambience, `code-header` style bar, Settings-sized toggle and select, ghost-button
examples, and surface cards without a separate marketing page skin.

The current production-bundle smoke captured the honest empty state in the live
accessibility tree: capacity 0/3 free, unattended off, empty queue, empty
Attention, and empty checkpoints. No refreshed screenshot is claimed here.

The Master runner picker enables only server-qualified choices. Its replayable
browser evidence is recorded in
[Master orchestrator integrated acceptance](master-integrated-acceptance.md#reproducible-browser-evidence).

Unattended budgets remain under Settings → Master when the feature is enabled.

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

Design is present only when its server-owned feature flag is on. The home is
sparse by default (title, short lead, tooltip chips, **How it works**) and
accepts a brief, format, brand guide, or size template without printing the
project display name (shell switcher).

![Design home](screenshots/design-home.png)

## 10. Agents, knowledge, settings, and help

Agent profiles choose a ready runner, isolated home, instructions, and detected
skills or MCP servers. Skills are multi-root scanned (runner home, shared
registries, optional custom roots under Settings → Agents); enabled skills also
appear as `/skill-name` entries in the Chat slash palette. MCP remains
enable/disable only. Optional host tools such as `headroom` show under
Recommended tools when present on PATH.

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

- **Chrome standard:** 1440×1000, Light theme, expanded sidebar, destinations-only
  left nav, post-#37 Deck shell on every primary shot. No mix of old solid Master
  marketing empty state with current Master desk.
- **Passed:** first-run password, onboarding Link + Create tabs, core tour (4
  steps), empty Chat, Master desk plus replayable qualified-runner picker evidence, Attention,
  Tasks list/board/launcher, Recipes home/editor/schedules, Projects, Archive
  empty registry, Design home, tool rails, Search, Settings sections, Agents
  profiles, Skills/MCP + masterplan, Wiki, Help.
- **Skipped (honest):** live Chat agent turns, approvals, turn restore,
  masterplan intake UI in Chat, `@` mentions, Master worker dispatch and
  checkpoint restore, populated Tasks review, ArtifactViewer v2 deep review,
  Design studio canvas beyond home, mobile shell. Those surfaces still ship;
  this pass prioritized shell/nav uniformity over multi-minute agent runs.
- **Redaction:** personal host paths and usernames in folder pickers and agent
  home lines were neutralized to `/home/owner/…` style placeholders before
  capture where needed.
