# MAP · proxima

## What
On-hold self-hosted single-user control plane for delegating work to agent CLIs. FastAPI backend and React PWA coordinate chat, Master delegation, reviewable tasks, workflows, artifacts, projects, and local runtime tools over ACP.

## Open first

| Need | Open |
| --- | --- |
| Hold status, rules, and documentation contract | [AGENTS.md](AGENTS.md) |
| Documentation index | [docs/README.md](docs/README.md) |
| System structure and main flows | [docs/reference/architecture.md](docs/reference/architecture.md) |
| Feature behavior and status | [docs/CAPABILITIES.md](docs/CAPABILITIES.md) |
| Security and runner isolation | [docs/security-boundaries.md](docs/security-boundaries.md), [docs/prompt-injection-hardening.md](docs/prompt-injection-hardening.md) |
| Backend entry and routes | [apps/api/proxima_api/main.py](apps/api/proxima_api/main.py), [apps/api/proxima_api/routes/](apps/api/proxima_api/routes/) |
| Frontend shell and navigation | [apps/web/src/App.tsx](apps/web/src/App.tsx), [apps/web/src/lib/workRoute.ts](apps/web/src/lib/workRoute.ts) |
| Build, test, and service commands | [package.json](package.json), [scripts/proxima](scripts/proxima) |

## Layout
- `apps/api/`: FastAPI control plane, ACP runners, orchestration, jobs/worktrees, workflows, artifacts, previews, auth, DB, routes, and backend tests
- `apps/web/`: React/Vite PWA, API clients, shell state, screens, design tokens, and frontend tests
- `docs/`: maintained architecture/product/security references, generated API/DB docs, evidence, and living roadmap
- `scripts/`: development, build, install, service, smoke, docs generation, browser validation, backup, and recovery commands
- `infra/`: systemd and Tailscale examples
- `templates/`: project/wiki seed content; `bundled-skills/`: shipped masterplan capability

## Edges
- Runner integrations use ACP and bring-your-own CLI login; Proxima ships no models or credentials.
- Runtime data stays under `~/.local/share/proxima/`; private config stays under `~/.config/proxima/`.
- Single-owner auth adds a password/session boundary behind loopback, Tailscale, or Cloudflare Access; it is not tenant isolation.
- Repo jobs use isolated worktrees and local review/merge; pushing is opt-in per repository.
- Route or schema changes require `scripts/gen_docs.py`; feature, flow, and dependency changes update matching durable docs.

## Ignore by default
- `node_modules/`, `apps/web/dist/`
- `apps/api/.venv/`, Python caches, test caches
- External runtime/config directories
- Maintainer-local untracked `docs/STATUS.md`, `docs/wiki/`, `docs/bugfix-log.md`, and `docs/archive/` unless present and current-state context is needed
- Generated `docs/reference/api.md` and `database.md` for hand edits
- `CLAUDE.md` (symlink to `AGENTS.md`)

## Product routes
- Work mode defaults to Chat; selectable views are Chat, Activity/Tasks, Workflows, Artifacts, Design, and Inbox, scoped by query parameters such as `mode`, `view`, `project`, and `session`.
- Delegate mode defaults to Master; selectable views are Master, Activity/Tasks, Artifacts, and Inbox.
- Task permalinks use `#task/<id>`; archived-record permalinks reopen inside Artifacts.
- Auth setup/login gates the single workspace; Settings, Projects, Profiles, Runners, and Wiki are supporting screens in the same PWA shell.
