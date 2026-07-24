# Proxima

Your self-hosted **cockpit for delegating real work to the AI agents you
already own**: think it through in chat, let AI slice it into reviewable jobs,
run them safely, and keep the good runs as repeatable recipes - code, content,
and ops, all on **your own machine**. Bring your own agent CLI: **Claude Code,
Codex, Grok, Hermes, or Pi**. Reach it from any browser, or your phone via Tailscale.

![One workspace: Chat is the front door](docs/screenshots/deck-chat.png)

## Current highlights

- **Alpha orchestration desk:** delegate an outcome to a built-in orchestrator,
  choose its backing runner, and monitor three worker slots, queued work,
  Attention decisions, and job-scoped checkpoints.
- **Native Grok runner:** use the official Grok Build CLI over its native ACP
  endpoint, with the same ready/not-ready status shown for every installed runner.
- **First-class `/masterplan`:** start the bundled masterplan methodology from
  Chat's slash palette and continue its interactive intake in the same session.
- **ArtifactViewer v2:** review ordinary artifacts in Proxima with point
  annotations, rendered Mermaid, editable Excalidraw conversion, and an editable
  feedback handoff to the producing Chat.

![Alpha backed by Grok](docs/screenshots/alpha-desk.png)

![Native artifact review](docs/screenshots/artifact-review-annotation.png)

**Take the complete [visual tour](docs/tour.md)** for every primary surface and
honest live-pass notes.

## What it is

A workspace you self-host as a background service and open in a browser (or
install as a PWA on your phone). FastAPI backend + React PWA. It's a **control
plane**: it drives agents over the Agent Client Protocol (ACP), so you plug in
whichever agent CLI you already use and log into — Proxima ships **no model and
no credentials**. The work it orchestrates is domain-neutral: content, ops,
research, and code all flow through the same chats, plans, reviews, and
archive records.

One workspace, organized around two paths into durable work: **Chat or Alpha → Tasks → Recipes**:

- **Chat** is the hands-on front door - think it through with one agent, then
  *Slice into plan*: the AI turns the conversation into a runnable plan of
  reviewable jobs. File-changing turns carry a previewable session-scoped restore.
- **Alpha** is the delegation desk - give the built-in orchestrator an outcome and
  it dispatches Autonomous worker jobs through the agents you already own, up to
  three at once, with the rest queued honestly.
- **Tasks** holds the resulting plans and one-off jobs with their review gates.
  A job that touches a repo runs in an isolated copy of the code and comes back
  as a diff you review and merge locally - nothing leaves your machine.
- **Recipes** keeps the plans worth repeating, on demand or on a schedule.
- **Projects** holds the work itself, **Archive** keeps every deliverable as a
  durable record, and the technical tools - a real in-browser terminal, a file
  tree, and live app preview - sit on a right rail, one click away in any
  context.

## Features

- **Single-user cockpit** — set one owner password on first run, then use a
  persistent owner session. Run it for yourself, see your work organized, never
  lose context.
- **Alpha orchestration desk** - a built-in system persona backed by the runner
  you choose, with in-process Proxima tools, live worker capacity, active queue,
  needs-you links, and job-scoped checkpoints. Alpha and its workers auto-approve
  ACP tool prompts without changing the global permission setting. Optional
  unattended mode starts only queued work and stops at saved turn and wall-clock
  budgets; code jobs may still commit, push, or open PRs through your existing
  `git`/`gh` setup. Satpam alone owns stuck-job recovery.
- **Global Attention inbox** - review gates, tool permissions, satpam restart asks,
  and Alpha decisions collect in one shell badge. Safe binary actions can run
  inline; complex diffs and open questions always deep-link to their owning surface.
- **Tasks with review gates** — describe an outcome, pick an agent and a
  **Guarded** or **Autonomous** policy; the task runs as a durable job and
  pauses for your review before it counts as done.

  ![Task review gate](docs/screenshots/task-review.png)
- **Repo jobs: diff review + local merge** - a job aimed at a code area runs in
  an isolated git worktree; you review its changes in place and approve to
  merge, or reject with a reason. The merge is local by default - Proxima never
  pushes unless you flip a per-repo "push after merge" toggle (off by default),
  and then it pushes with your machine's own `git`: bring-your-own credentials,
  no tokens stored, no OAuth.

- **Full-power chat** — streaming responses, tool-activity cards, slash
  commands, session continuity. Agent approval/permission prompts render as
  clickable cards; point the Claude Code runner at your live config and the
  agent inherits your real skills, plugins, rules, MCP servers, and memory.

  ![Chat with a tool-approval card](docs/screenshots/chat-approval.png)
- **Plans & Recipes** — describe a process and an agent draws the plan as a DAG
  on an n8n-style canvas; nodes carry typed output contracts (`text` / `json` /
  `artifact-ref`), per-node agents, and review gates. Correct one node's output
  and every dependent node reruns deterministically. Plans are **run-first**:
  slice a good chat into a plan with one click and run it - saving it as a
  Recipe (and putting it on cron) is an optional step after it proves out.

  ![Editable plan canvas](docs/screenshots/workflow-blank-canvas.png)
- **Script steps** - a plan node can be a deterministic script instead of an
  agent turn. Scripts live in the project's `scripts/` folder; the first run
  pauses on a one-time approval card showing the script's exact content and
  sha256, and those approved bytes are trusted until the file changes - so
  repeated non-AI work costs nothing and can't change under you silently.

- **Long work survives** - agent turns get a configurable time budget, and a
  turn that times out mid-task auto-continues with its real context (up to 5
  genuine resumes) before stopping honestly and pausing the plan for you.
- **A watchdog over running jobs** - a supervision loop (the *satpam*) checks
  every continuation turn for real progress from durable signals only (repo
  changes, non-repeating output; no extra AI calls). A stuck job gets one
  corrective nudge, then a clean restart - automatic only for non-repo work,
  while restarting a repo job always asks you first - and a job that surfaces a
  genuine open decision pauses just that branch with the question while
  independent branches keep running. Every intervention is visible in the
  task's log; nothing happens silently.
- **Multi-agent collaboration** — per-prompt **Brainstorm** (parallel idea
  lanes + synthesis) and **Debate** (alternating rounds + judge), plus a
  **Validate** sidecar where a different runner pressure-tests a finished
  answer and can replace it.

- **Design Studio** — the agent drafts **editable layered designs** (text stays
  real text) from a brief; refine them on a Konva canvas with a full inspector,
  selection-aware chat, per-project brand guide, and PNG/JPG/PDF/HTML export.

  ![Design Studio](docs/screenshots/design-studio-inspector.png)
- **Image generation** — `/image` in chat via Codex/ChatGPT OAuth, xAI,
  Higgsfield, or any OpenAI-compatible endpoint; results land in the Archive
  and can open in Design Studio.
- **Archive + native artifact review** - every agent deliverable becomes a
  durable record with lineage (chat → task → file), one approval status synced
  with task review, a version chain, and a permanent link; records survive file
  moves and deletion. ArtifactViewer v2 adds point annotations, rendered Mermaid,
  editable Excalidraw conversion, and an editable feedback draft returned to the
  producing Chat. **Run & Preview** remains available for dev servers behind a
  credential-stripping proxy.

  ![Mermaid review in ArtifactViewer v2](docs/screenshots/artifact-review-mermaid.png)
- **In-browser terminal** — a real PTY shell scoped to the project. Work in the
  shell from anywhere, no SSH.
- **Use a folder on disk** — register a folder you already have as a project, or
  create a new empty one under a parent you pick; chat/terminal/files operate on it.
  Removing such a project only unlinks it (the real files stay).
- **Goal loop** — `/goal` keeps the agent iterating until done or blocked.
- **Wiki + knowledge** — per-project linked notes (`[[Note]]`) with an
  auto-updating graph; distill any chat into a wiki note.
- **Multi-runner profiles** - each agent profile picks a runner (Claude Code,
  Codex, Grok, Hermes, Pi) with an isolated credential home, its own instructions,
  and per-profile skills/MCP selection detected from your host.
- **Baked-in capability bundle** — every profile ships with
  [`bundled-skills/`](bundled-skills/README.md) (opt-out per profile), starting
  with the [masterplan](https://github.com/labsiqbal/masterplan) skill (MIT).
  Type `/masterplan <idea>` in Chat to run that methodology as a first-class
  planning turn and produce its execution-ready package; natural-language asks
  still invoke the same skill. The bundle also includes a distilled
  work-discipline preamble and detect-and-advertise for recommended host CLIs
  (binaries stay bring-your-own).
- **Schedules** — five-field cron for saved Recipes, with overlap
  policy and a "Run now" that exercises the real spawn path.
- **Self-update, audit log, themes & PWA** — one-click update from GitHub
  Releases, an audit trail of meaningful actions, six themes, installable on
  desktop or phone.

Video Studio and video generation were removed; ordinary video files still play
as generic artifacts.

**More screenshots:** [docs/tour.md](docs/tour.md) — a visual tour of every
surface.

## Requirements

- **Linux, macOS, or Windows** (no Docker required)
- [`uv`](https://docs.astral.sh/uv/) and Node.js / `npm`
- At least one **agent CLI** installed + logged in: Claude Code, Codex, Grok, Hermes, or Pi

See [docs/installation.md](docs/installation.md) for per-OS steps.

## Quickstart

```bash
git clone https://github.com/labsiqbal/proxima
cd proxima
bash scripts/install-user
```

Open `http://127.0.0.1:8765`.

Configuration (e.g. in `~/.config/proxima/proxima.env`):

```bash
export PROXIMA_SINGLE_USER_NAME=owner   # optional owner name used on first run
export PROXIMA_CLAUDE_LIVE_HOME=1       # claude-code runner uses your live ~/.claude
export PROXIMA_LINK_ROOTS="$HOME"       # roots you may browse + link folders from
export PROXIMA_FEATURE_DESIGN_STUDIO=1  # Design Studio (on by default; 0 disables)
```

Manage the service:

```bash
systemctl --user status proxima
systemctl --user restart proxima
```

## Bring your own agent

Proxima ships **no credentials**. Each profile picks a runner (Claude Code, Codex,
Grok, Hermes, or Pi). Log into that agent's own CLI the way you normally would -
Proxima uses your existing login and **never asks for or stores provider passwords**.
The Grok runner drives the official CLI through native ACP (`grok agent stdio`);
see [installation](docs/installation.md#grok-build-cli) for install and login steps.
With `PROXIMA_CLAUDE_LIVE_HOME=1`, the Claude Code runner points at your live
`~/.claude`, so all your installed skills, plugins, rules and MCP servers come along.

## Security / trust model

Proxima runs agents with your user's full privileges - you are the trust
boundary. Keep it behind your own network gate (loopback or Tailnet) and never
expose it to untrusted users. See
[docs/security-boundaries.md](docs/security-boundaries.md).

## Tailscale / phone access

```bash
sudo tailscale set --operator=$(whoami)   # one-time
sudo tailscale serve --bg 8765
```

Open your HTTPS MagicDNS URL on any device and install the PWA.

## Repository layout

```text
apps/api/        FastAPI backend (chat, runners/ACP, terminal, files, wiki, jobs)
apps/web/        React/Vite PWA
bundled-skills/  the shipped capability bundle (skills + recommended-tools list)
infra/scripts/   optional host/project helper scripts
infra/systemd/   service templates
docs/            architecture, install, security, backup docs
scripts/         build/dev/install/backup wrappers
templates/       project workspace templates
```

## Docs

**📖 [Documentation hub](docs/README.md)** — the single entry point to everything
(reference, guides, logs). Highlights:

- [Visual tour](docs/tour.md) — screenshots of every surface
- [Tech stack](docs/reference/tech-stack.md) · [Architecture & flows](docs/reference/architecture.md)
- [API reference](docs/reference/api.md) · [Database schema](docs/reference/database.md) _(both auto-generated from code)_
- [Capabilities / feature map](docs/CAPABILITIES.md)
- [Design Studio](docs/DESIGN-STUDIO.md)
- [Installation](docs/installation.md) · [Security boundaries](docs/security-boundaries.md) · [Backup & recovery](docs/backup.md)

## Contributing

Humans **and** AI agents are welcome. Proxima is *not meant to be "done"* — it evolves as the
agents it drives evolve. See [CONTRIBUTING.md](CONTRIBUTING.md) (DNA filter, the documentation
set, DCO sign-off — no CLA) and the [Architecture Decision Records](docs/adr/) for the *why*.

## License

[GNU AGPL-3.0-or-later](LICENSE). Proxima is a pure commons: you may self-host, modify, and
even run it as a service — but any derivative, **including a hosted/SaaS one**, must keep its
source open (AGPL §13). It cannot be closed or captured. Reasoning:
[ADR-0002](docs/adr/0002-license-agpl.md).
