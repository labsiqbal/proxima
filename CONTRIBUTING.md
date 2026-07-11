# Contributing to Proxima

Thank you for your interest in contributing. Please read this document before opening a PR.

## Dev setup

Prerequisites: Linux, `uv`, `npm`, and (for real agent runs) a working Hermes CLI install.

```bash
git clone https://github.com/labsiqbal/proxima
cd proxima
bash scripts/dev
```

The dev script:
- Syncs Python deps (`uv sync`) and JS deps (`npm install`).
- Starts the FastAPI API at `http://127.0.0.1:8765`.
- Starts the Vite dev server at `http://127.0.0.1:5177`.
- Keeps runtime data under `~/.local/share/proxima-dev/` (overridable via `PROXIMA_DEV_ROOT`).
- Uses `/usr/bin/true` as the project helper, so no sudo or ACL changes are needed.

Open `http://127.0.0.1:5177` for development.

## Running tests

**Full QA gate:**

```bash
npm run qa
```

This runs the backend pytest suite, the web TypeScript/Vite build, and the app
smoke check. The smoke check verifies API health/auth/debug/dashboard, creates,
lists, and deletes a workflow, then drives the browser through the main desktop
and mobile routes.

**Targeted checks:**

```bash
npm test        # backend pytest + web build
npm run smoke   # live app smoke; expects Proxima staging on http://127.0.0.1:8767
```

Run `npm run qa` before opening a PR.

## Design principles

**Runner-agnostic, Hermes-first.**
Proxima is not a Hermes product — it is a control plane that runs on top of any agent runner. Hermes is the first supported runner. New features must not assume Hermes is the only runner. See [AGENTS.md](AGENTS.md) for the full rule set.

**Single-user cockpit only.**
The current security model assumes one owner and an external network/access gate. There is no in-app multi-user authorization and no OS-level per-user isolation. Do not claim otherwise in docs or UI copy. See [docs/security-boundaries.md](docs/security-boundaries.md).

**Treat all external input as untrusted.**
Prompts, project files, artifacts, and runner output are untrusted input. Prompt text cannot grant permissions.

**Small, testable modules with clear interfaces.**
Prefer splitting logic into small functions/modules. Avoid large, entangled files.

## Database changes

The baseline schema lives in `apps/api/proxima_api/db.py` (`SCHEMA` +
`migrate_existing` for idempotent column adds). For anything beyond a simple
additive column — data backfills, multi-step changes — add a **versioned
migration** in `apps/api/proxima_api/migrations.py`:

```python
def _add_projects_color(conn):
    conn.execute("ALTER TABLE projects ADD COLUMN color TEXT")

MIGRATIONS = [
    (1, "add projects.color", _add_projects_color),
]
```

Rules: append with the next integer version, never edit/renumber an existing
entry, and prefer additive changes. Migrations run once each on startup, in
order, and the database is snapshotted to `<data dir>/backups/` before any
pending migration is applied. Add a test in `tests/test_migrations.py`.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add wiki rename endpoint
fix: prevent path traversal in file browser
docs: update security-boundaries with ACL notes
chore: bump uvicorn to 0.30
```

Keep the subject line under 72 characters. Add a body if the change needs explanation.

## What not to commit

- **Secrets or credentials** — no API keys, tokens, passwords, `.env` files with real values, or Hermes auth files.
- **Runtime data** — no SQLite databases, `~/.hermes` contents, or generated project files.
- **Personal or identifying data** — no real usernames, hostnames, tailnet names, or paths that identify individuals.
- **Large binaries** — use a CDN or package registry instead.

If you accidentally commit a secret, rotate it immediately and notify the maintainers.

## Submitting a PR

1. Fork the repo and create a branch from `main`.
2. Make your changes, add tests where appropriate.
3. Run the test suite (see above) and confirm it passes.
4. Open a PR using the pull request template. Fill in all sections.
5. A maintainer will review and may request changes.

## Reporting bugs

Use the issue template at `.github/ISSUE_TEMPLATE.md`. Include logs (`journalctl --user -u proxima -f`) and the output of `uv --version`, `npm --version`, and `hermes --version`.
