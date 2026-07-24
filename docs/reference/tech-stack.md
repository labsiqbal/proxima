# Tech Stack

_Hand-maintained. Verify versions against `apps/api/pyproject.toml` and
`apps/web/package.json` when they change._

Proxima is a two-part app in one repo: a **Python/FastAPI backend** (`apps/api`)
and a **React/TypeScript PWA frontend** (`apps/web`). It is a _control plane_ — it
drives AI coding-agent CLIs you already own over the **Agent Client Protocol (ACP)**;
it ships no model and no credentials of its own.

## Backend — `apps/api`

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python ≥ 3.11 | |
| Web framework | **FastAPI** (`>=0.115`) | REST + WebSocket + SSE |
| ASGI server | **Uvicorn** (`>=0.30`) | entrypoint `proxima_api.main:app` |
| Data validation | **Pydantic v2** (`>=2.8`) | request models in `schemas.py` |
| JSON contracts | **jsonschema** (`>=4.23`) | validates graph-node output schemas before execution |
| HTTP client | **httpx** (`>=0.27`) | outbound calls (image providers, Cloudflare, proxy) |
| WebSockets | **websockets** (`>=16`) | terminal + session event streams |
| Uploads | **python-multipart** | file upload endpoints |
| Runner config parsing | **PyYAML** + **TomlKit** | filter per-profile Hermes YAML and Codex/Grok TOML MCP selections while preserving unrelated settings |
| Database | **SQLite** (stdlib `sqlite3`, WAL mode) | one file per install; no server |
| Package manager | **uv** (`uv.lock`) | `uv run …`, `uv sync` |
| Tests | **pytest** (`>=8.3`) | `apps/api/tests/` |
| Lint | **Ruff** (`>=0.15`) | `F` rules run locally and in CI to catch undefined names and dead imports/locals |

**No ORM** — SQLite is accessed with hand-written SQL through a thin per-thread
connection helper (`db.py`). The schema lives in `db.py` (`SCHEMA`) plus versioned
migrations in `migrations.py`. See [database.md](database.md) for the full schema.

### Key backend concepts

- **ACP runners** (`acp.py`, `runners.py`, `runner_specs.py`) - each supported CLI
  (Claude Code, Codex, Grok, Hermes, Pi) is described by a _runner spec_ (spawn
  argv + credential home + readiness check + wire `protocol`). The app spawns one
  agent subprocess per `(runner, home, cwd)` on demand. Grok speaks ACP natively
  through the official CLI's `grok agent stdio`; **Codex** instead drives the
  owner's own `codex app-server`
  (`codex_appserver.py`) so it always tracks the up-to-date system Codex CLI
  rather than a bundled adapter core (see architecture.md → "Codex runner").
- **Run worker** (`worker.py`) — a bounded-concurrency background worker that
  executes agent runs so one slow run never blocks other chats.
- **Scheduler** (`scheduler.py`) — a 60-second loop that materializes due cron jobs.
- **Event hub** (`event_hub.py`) — fan-out of run/session events to SSE + WebSocket
  subscribers.
- **Terminal** (`terminal.py`) — a PTY-backed shell exposed over WebSocket.
- **App runner + preview proxy** (`apprunner.py`, `preview_proxy.py`) — launch an
  owner-confirmed project dev server with a filtered env and reverse-proxy it using
  preview-only credentials that are stripped before requests reach project code.

## Frontend — `apps/web`

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | **TypeScript** | |
| Framework | **React 19** | single-page app, installable as a **PWA** (`pwa.ts`) |
| Build tool | **Vite** | dev on `127.0.0.1:5177`, `npm run build` → `dist/` |
| State | **Zustand** | lightweight store |
| Code editor | **CodeMirror 6** (`@uiw/react-codemirror` + language packs) | files, artifacts, wiki edit |
| Design canvas | **Konva** / **react-konva** | powers Design Studio (feature-flagged: on in dev, opt-in when installed) |
| Terminal UI | **xterm.js** (`@xterm/xterm` + fit addon) | in-browser terminal |
| Wiki graph | **react-force-graph-2d** | linked-note graph |
| Workflow graph | Native **SVG** + pure topological layout | enabled by default; `PROXIMA_FEATURE_WORKFLOW_GRAPH` remains a recovery switch |
| Artifact diagrams | **Mermaid 11** + **Excalidraw** (`@excalidraw/mermaid-to-excalidraw`) | ArtifactViewer v2 renders Mermaid and lazy-loads an editable whiteboard; saved scenes stay project files |
| Search | **minisearch** | client-side global search |
| Markdown | **react-markdown** + **remark-gfm** | chat + wiki rendering |
| Export | **jspdf**, **jszip** | retained Studio PNG/PDF/zip implementation |

Frontend source layout: `src/screens` (top-level views), `src/components`
(chat / design / files / shell / tasks / terminal / wiki / ui), `src/api` (typed
fetch wrappers to the backend), `src/hooks`, `src/lib`, `src/theme.ts` (6 themes).

## Runtime & operations

- **Database & runtime data** live _outside_ the repo, under
  `~/.local/share/proxima/` (DB, workspace, per-profile agent homes, backups)
  and `~/.config/proxima/proxima.env` (config). See
  [architecture.md](architecture.md#runtime--repo-split).
- **Process management** — runs as a **systemd** user service (see
  `docs/installation.md`). A staging clone can run side-by-side on its own port,
  database, and venv.
- **Deployment profiles** — production uses `proxima.service` on port `8765` for
  `proxima.minarflow.com`; staging uses `proxima-staging.service` on port `8767` for
  `proxima-staging.minarflow.com`. Config and data roots are isolated.
- **Remote access** — the app stays on loopback; expose it through your own
  network gate (Tailscale, or a Cloudflare Tunnel + Cloudflare Access — the
  in-app guide under Settings → Remote Access walks through both). Per-app
  previews can ride a tunnel on `<slug>.<apps_domain>` subdomains.
- **Backups** — a daily `proxima-backup` systemd timer snapshots the DB with
  `VACUUM INTO`; migrations also back up before applying. See [backup.md](../backup.md).

## Repo layout (top level)

```text
apps/api/     FastAPI backend (proxima_api package + tests)
apps/web/     React/Vite PWA frontend
docs/         Documentation (this hub — see docs/README.md)
scripts/      Ops + doc-generation scripts (gen_docs.py)
infra/        Deployment infra (tailscale, tunnel config)
templates/    Project scaffolding templates
```
