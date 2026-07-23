# Development tools guide

This document is the source of truth for future Claude Code/Codex/Grok/Hermes/Pi sessions that develop Proxima.

## Product context

Proxima is a single-user, runner-agnostic PWA cockpit. It runs on a Linux server/workstation and provides one owner with projects, runner profiles, async agent runs, event streaming, files, terminal, workflows, and PWA access over localhost/Tailscale/Cloudflare Access.

Current intentional boundary:

```text
OS/server admin boundary: physical/SSH/AnyDesk access to the machine
Proxima app boundary: one owner, projects, sessions, profiles, and network-gated API access
Runner boundary: runner subprocess gets selected profile home and project cwd
```

Proxima does **not** currently provide OS-level isolation or multi-user app authorization.
The external network gate is primary; after first run, the single-owner password and
session provide defense-in-depth. Treat an authenticated app session as full owner access.

## Useful commands

From repo root:

```bash
bash scripts/dev
bash scripts/build
bash scripts/proxima init-config
bash scripts/proxima doctor
bash scripts/proxima serve
```

Verification:

```bash
cd apps/api && .venv/bin/ruff check proxima_api tests
cd apps/api && .venv/bin/python -m pytest -q tests
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
```

Current local environment note:

```bash
cd apps/api && uv run python -m pytest -q
npm --prefix apps/web run build
```

Ruff's `F` rules keep undefined names, unused imports, and unused locals from
returning. Use `python -m pytest`; plain `uv run pytest` is known to fail to spawn
in this environment.
The root `pyrightconfig.json` points Python language servers at `apps/api` and its
`.venv`, so the nested `proxima_api` package resolves when the repo root is opened.

Packaged local serve:

```bash
bash scripts/proxima init-config
bash scripts/proxima build
bash scripts/proxima serve
```

## Runtime paths

User-local install:

```text
~/.config/proxima/proxima.env
~/.local/share/proxima/proxima.db
~/.local/share/proxima/workspace
~/.local/share/proxima/hermes-profiles/<username>/<profile>
```

System install:

```text
/opt/proxima
/etc/proxima/proxima.env
/var/lib/proxima/proxima.db
/var/lib/proxima/workspace
/var/lib/proxima/hermes-profiles/<username>/<profile>
```

## Code ownership

Core files:

```text
apps/api/proxima_api/main.py          app factory / route wiring + lifespan (~257 lines)
apps/api/proxima_api/routes/*.py      HTTP + WebSocket handlers (chat, files, work, …)
apps/api/proxima_api/route_deps.py    shared route dependencies (current_user, etc.)
apps/api/proxima_api/worker.py        run orchestration (RunWorker); run_*.py helpers
apps/api/proxima_api/scheduler.py     cron scheduler loop
apps/api/proxima_api/acp.py           ACP subprocess manager (agent runners)
apps/api/proxima_api/frontend_static.py  static PWA serving
apps/api/proxima_api/db.py            SQLite schema + migrate_existing
apps/api/proxima_api/migrations.py    versioned migrations
apps/api/proxima_api/auth.py          password/token helpers
apps/api/proxima_api/settings.py      config/path helpers
apps/web/src/App.tsx                       app state and screen routing
apps/web/src/screens/*                     UI screens
apps/web/src/api/*                         typed API calls
scripts/*                                  install/dev/build wrappers
```

## Rules for coding agents

When a future agent edits this repo:

1. Read this file and `docs/security-boundaries.md` first for security-sensitive work.
2. Do not treat single-user app access as OS-level isolation.
3. Do not expose arbitrary filesystem browsing beyond the configured owner/link-root model.
4. Do not let client-supplied paths decide source/runtime/project access.
5. Keep all runner integrations behind Proxima policy checks.
6. Run API tests and web build after changes.
7. Never print or commit secrets, tokens, `.env`, DB files, or Hermes profile contents.

## Parallel AI development sessions

Separate AI sessions are safe only when their write boundaries are isolated. Two
agents editing or testing the same working tree can observe a file between patches;
that produces misleading import errors, test hangs, or a valid change being
overwritten even when both agents are individually correct.

Use one Git worktree and branch per independent session:

```bash
git worktree add ../proxima-agent-a -b agent/a
git worktree add ../proxima-agent-b -b agent/b
```

Give each worktree its own runtime config, database, workspace root, ports, and
frontend dev-server port. Merge one reviewed branch at a time, regenerate docs when
routes/schema changed, then run the complete lint/test/build gate from a stable tree.

`scripts/dev` automates the runtime and port isolation when given a unique ID:

```bash
PROXIMA_DEV_ID=agent-a bash scripts/dev
PROXIMA_DEV_ID=agent-b bash scripts/dev
```

An explicit `PROXIMA_DEV_ROOT`, `PROXIMA_PORT`, or `PROXIMA_WEB_PORT` still overrides
the derived value. A worktree/branch remains required because the ID isolates runtime,
not source-file writes.

Several cooperating sub-agents may share one tree only when one coordinator assigns
non-overlapping files and waits for all writers to stop before authoritative tests.
Treat tests run during concurrent edits as advisory, not a release result. Never let
multiple sessions share a production database or run automated Git cleanup/reset on
another session's changes.

## Adding new features

Every new feature should answer:

- Which owner/project/profile/session does it belong to?
- Does it read source code, project files, runtime files, or secrets?
- Can a prompt injection influence it?
- Is the action audited?
- Is it owner-only, runner-internal, or behind an explicit external access gate?
