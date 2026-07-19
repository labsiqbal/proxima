# Proxima

Your self-hosted **cockpit for AI coding agents** — chat, workflows, an in-browser
terminal, files with live preview, and a wiki, all in one private space that runs on
**your own machine**. Bring your own agent: **Claude Code, Codex, Hermes, or Pi**. Reach it from any browser, or your phone via Tailscale.

## What it is

A workspace you self-host as a background service and open in a browser (or
install as a PWA on your phone). FastAPI backend + React PWA. It's a **control
plane**: it drives agents over the Agent Client Protocol (ACP), so you plug in
whichever agent CLI you already use and log into — Proxima ships **no credentials**.

## Features

- **Single-user cockpit** — set one owner password on first run, then use a
  persistent owner session. Run it for yourself, see your work organized, never
  lose context.
- **Full-power chat** — point the Claude Code runner at your live config and the
  agent inherits your real skills, plugins, rules, MCP servers, and memory.
  Streaming responses, tool-activity cards, slash commands, session continuity.
- **In-browser terminal** — a real PTY shell scoped to the project, right in the
  app. Work in the shell from anywhere, no SSH.
- **Interactive cards** — agent approval/permission prompts render as clickable
  cards; choice lists become quick-reply buttons.
- **Link existing folders** — register any folder on disk as a project, so your
  existing work connects to the cockpit (chat/terminal/files operate on it).
  Removing a linked project only unlinks it — your real files stay.
- **Workflows & schedules** — promote a good chat into a repeatable workflow, run
  it on a cron schedule, and follow every run in the activity feed.
- **Files** — per-project browser with edit + live HTML/Markdown **preview**, plus
  **Run & Preview** for dev servers.
- **Wiki** — linked notes (`[[Note]]`) with an auto-updating graph.
- **Multi-runner** — pick a runner per profile: Claude Code, Codex, Hermes, or
  Pi. Credentials auto-seed from the host.
- **Themes & PWA** — six themes; installable on desktop or phone.

Image generation remains available. Design Studio remains in source but is
temporarily disabled by default; Video Studio and video generation were removed.
Ordinary video files still play as generic artifacts.

## Requirements

- **Linux, macOS, or Windows** (no Docker required)
- [`uv`](https://docs.astral.sh/uv/) and Node.js / `npm`
- At least one **agent CLI** installed + logged in: Claude Code, Codex, Hermes, or Pi

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
export PROXIMA_FEATURE_DESIGN_STUDIO=0   # temporarily disabled
```

Manage the service:

```bash
systemctl --user status proxima
systemctl --user restart proxima
```

## Bring your own agent

Proxima ships **no credentials**. Each profile picks a runner (Claude Code, Codex,
Hermes, or Pi). Log into that agent's own CLI the way you normally would —
Proxima uses your existing login and **never asks for or stores provider passwords**.
With `PROXIMA_CLAUDE_LIVE_HOME=1`, the Claude Code runner points at your live
`~/.claude`, so all your installed skills, plugins, rules and MCP servers come along.

## Security / trust model

Proxima is a **single-user cockpit**. Keep the primary access gate at the network
layer (loopback, Tailnet, Cloudflare Access, or equivalent). On first run the owner
also sets a password; its session is defense-in-depth, not a multi-user permission
system. Agents run with the **same privileges as the server process** — they can
read/write files and run tools on the host.

Do **not** expose Proxima to untrusted users without a real external access gate
and OS/container isolation. See [docs/security-boundaries.md](docs/security-boundaries.md).

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
infra/scripts/   optional host/project helper scripts
infra/systemd/   service templates
docs/            architecture, install, security, backup docs
scripts/         build/dev/install/backup wrappers
templates/       project workspace templates
```

## Docs

**📖 [Documentation hub](docs/README.md)** — the single entry point to everything
(reference, guides, logs). Highlights:

- [Tech stack](docs/reference/tech-stack.md) · [Architecture & flows](docs/reference/architecture.md)
- [API reference](docs/reference/api.md) · [Database schema](docs/reference/database.md) _(both auto-generated from code)_
- [Capabilities / feature map](docs/CAPABILITIES.md)
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
