# Contributing to Proxima

Thank you for your interest in contributing. Please read this document before opening a PR.

Proxima is **not meant to be "done"** — it's a self-hosted control plane that evolves as the
agents it drives evolve. Contributions from humans **and** AI agents are welcome, as long as
they keep the project coherent. By contributing you agree your work is licensed under
[**AGPL-3.0-or-later**](LICENSE) and you certify it under the [DCO](#sign-your-work-dco).

## Does it belong? (the DNA filter)

Before building anything substantial, run the idea through the **five DNA pillars** and the
**anti-goals** in [`docs/ROADMAP.md`](docs/ROADMAP.md). If a change doesn't strengthen a
pillar — or chases an anti-goal — it won't be merged, however well-built. Open an issue to
discuss direction first. The *why* behind existing architecture lives in
[`docs/adr/`](docs/adr/); read the relevant ADR before changing a settled decision, and don't
re-litigate one.

## Dev setup

Prerequisites: Linux, `uv`, `npm`, and (for real agent runs) an authenticated supported CLI: Claude Code, Codex, Grok, Hermes, or Pi.

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

## Documentation is part of "done"

Docs ship in the **same PR** as the code (the contract at the top of
[`AGENTS.md`](AGENTS.md)). Depending on what you changed, produce:

| You changed… | Ship this |
|---|---|
| **An architectural decision** (subsystem, execution model, a dependency, a policy) | An **[ADR](docs/adr/README.md)** — numbered, same PR |
| **A feature or flow** | Update [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) + a feature doc; update [`docs/reference/architecture.md`](docs/reference/architecture.md) if structure/flow changed |
| **A feature meant to grow** (e.g. a new node/provider type) | An **extension playbook** — "how to add another one" |
| **Routes or the DB schema** | `apps/api/.venv/bin/python scripts/gen_docs.py` (regenerates `api.md` + `database.md` — never hand-edit those) |
| **A dependency** | Update [`docs/reference/tech-stack.md`](docs/reference/tech-stack.md) |

This repo's own [ADRs](docs/adr/) and feature docs are the worked example — mirror them. If a
change makes a doc wrong and you leave it, you've shipped a bug.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add wiki rename endpoint
fix: prevent path traversal in file browser
docs: update security-boundaries with ACL notes
chore: bump uvicorn to 0.30
```

Keep the subject line under 72 characters. Add a body if the change needs explanation.

### Sign your work (DCO)

Every commit must be signed off, certifying you have the right to submit it under the
project's license — the [Developer Certificate of Origin](https://developercertificate.org/):

```bash
git commit -s -m "feat: ..."   # appends: Signed-off-by: Your Name <you@example.com>
```

There is **no CLA**: Proxima is a pure commons — every contributor, maintainers included,
plays by the same AGPL rules. The DCO sign-off is the only ceremony.

## AI-agent contributors

Agents are first-class contributors here. If you're an agent (or driving one): everything
above is binding — the DNA filter, `AGENTS.md`, the documentation set, and the DCO. Sign off
your commits and additionally attribute the model, e.g.
`Co-Authored-By: <model> <noreply@…>`. The docs exist so you can contribute *correctly
without asking*: read the ADRs and `AGENTS.md`, follow the documentation set, and when a
decision is genuinely new, write an ADR rather than guessing silently.

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
