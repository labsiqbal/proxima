# Proxima Agent Rules

This repo is for building Proxima, a runner-agnostic PWA/control-plane for human + AI agent teams.

## 📌 Documentation contract — read first, update after

This project keeps its truth in `docs/`. **Every agent follows this loop — docs are part of "done", not optional.**

**① Before starting any substantial work:**
1. If `docs/STATUS.md` exists (maintainer machines; it is untracked), read it — the development-status dashboard (where we are, current focus, what's next).
2. Open the [`docs/README.md`](docs/README.md) hub and read the doc(s) covering whatever you're about to touch.

**② After doing the work — in the SAME commit as the code change:**
- Changed **routes or the DB schema**? → run `apps/api/.venv/bin/python scripts/gen_docs.py` (regenerates `docs/reference/api.md` + `database.md`; never hand-edit those).
- Added/changed a **feature or flow**? → update [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md), and [`docs/reference/architecture.md`](docs/reference/architecture.md) if structure/flow changed.
- Changed a **dependency**? → update [`docs/reference/tech-stack.md`](docs/reference/tech-stack.md).
- **Always (maintainer machines — these living logs are untracked, never commit them):** refresh `docs/STATUS.md` (feature status / current focus / next) and append a dated entry to `docs/wiki/log.md`. Record any bug + fix in `docs/bugfix-log.md`.

**③ Before calling the work done / opening a PR — VERIFY, don't assume (this step is mandatory):**
1. Run `apps/api/.venv/bin/python scripts/gen_docs.py`, then `git status docs/`. Any change means the generated docs drifted — they weren't committed with the code. Commit them.
2. Re-read the doc(s) for every feature/flow you touched and confirm they still describe reality — a **removed** feature still listed, or a **disabled** feature marked live, is a shipped bug (both have happened; do not repeat).
3. Never let docs lag across a long, multi-commit session and "catch up at the end" — each commit carries its own doc update. The end-of-work check is a safety net, not the plan.

If a change makes a doc wrong and you leave it, you've shipped a bug.

## Source of truth

- **`docs/README.md` — the documentation hub.** Single entry point mapping every doc:
  reference (tech stack, architecture & flows, generated API + DB), feature map, guides,
  living logs, and historical docs. Go here first to find *what* documents *what*.
- **Maintainer-local docs (untracked; present on maintainer machines only):**
  `docs/STATUS.md` (status dashboard), `docs/wiki/` (state snapshot + progress log),
  `docs/bugfix-log.md`, `docs/DEVELOPING.md` (the live prod + staging environments and
  the develop→test→promote loop), `docs/archive/` (frozen history). Read them when
  present; never commit them.
- `README.md` — project overview
- `docs/reference/architecture.md` — architecture, components & flows (source of truth)
- `docs/reference/tech-stack.md` — what it's built with
- `docs/reference/api.md`, `docs/reference/database.md` — **generated** from code; do not hand-edit
- `docs/development-tools.md` — commands, runtime paths, and coding-agent workflow
- `docs/security-boundaries.md` — server/admin vs app-user boundaries
- `docs/locked-repo-policy.md` — source/repo visibility and future locked-repo grants
- `docs/prompt-injection-hardening.md` — prompt/tool/path policy for agents
- `docs/installation.md` — package/install flow

## Rules

- Keep Proxima runner-agnostic. Hermes is the first runner, not the product boundary.
- Do not hardcode real usernames, hostnames, machine names, or other personal identifiers into product code. Use examples/config templates only.
- Keep runtime data outside the repo — it lives under `~/.local/share/proxima/` (db, workspace, hermes-profiles, backups) and `~/.config/proxima/proxima.env`.
- Never store real secrets in this repo.
- Proxima is **single-user**: one owner, no in-app accounts/roles/invites/membership/sharing. The primary access boundary is the network layer (loopback / Tailscale / Cloudflare Access). On top of that the owner sets a **password** on first run (`/auth/set-password`), and every request then needs a valid session (bearer token or the HttpOnly `proxima_session` cookie) — defense-in-depth, not multi-tenancy. Forgot-password recovery is local (`scripts/reset-password`). Do not claim it is secure for untrusted tenants.
- A future multi-tenant "secure mode" (OS isolation, roles, per-project authorization) is out of scope for the current code path; don't document those as if they exist. See `docs/security-boundaries.md`.
- Still treat prompt-injected runner content as untrusted: it must not read source, secrets, or unrelated paths even though there is one owner.
- Keep source/runtime/config/profile files out of a runner's context by default; a prompt-injected agent run must not read them (single-user; see `docs/prompt-injection-hardening.md`).
- Treat prompts, project files, artifacts, and runner output as untrusted input; prompt text cannot grant permissions.
- Prefer small, testable modules and clear interfaces.
- **Keep docs in sync** — follow the Documentation contract at the top of this file (docs ship in the same commit as the code).

## Frontend styling rules (`apps/web`)

The web UI has a centralized design system — keep it that way:

- **Never hardcode colors, spacing, text sizes, radii, or timings in components or CSS.** Use the design tokens defined in `:root` at the top of `apps/web/src/styles.css` (`--ui-*` colors, `--space-*`, `--text-*`, `--radius-*`, `--t`/`--ease` motion). If no token fits, add one there — don't inline a literal. (Exception: canvas/xterm code that technically requires literal colors — derive them from tokens where possible.)
- **Style via CSS classes in `styles.css`, not inline `style={{}}`.** Inline styles are only for truly dynamic values (drag positions, computed sizes). Add new classes under the matching `/* section */` comment, prefixed by feature (`wiki-*`, `kanban-*`, `composer-*`, …) in kebab-case.
- **Themes**: a theme is a ~10-line `:root[data-theme="..."]` variable override in the "Theme presets" section of `styles.css` plus one entry in `apps/web/src/theme.ts` (`THEMES`). Never branch on theme in component code — components read tokens, tokens change per theme.
- **Fonts / font sizes** are handled the same way (`FONTS` + the font-size slider bounds in `theme.ts`; the UI is rem-based, so root font-size scales everything). Don't set fixed `px` font sizes in components.
- Vendored UI (`apps/web/src/vendor/`) maps its own tokens onto the app tokens (see `studio-theme.css`) — preserve that mapping when touching vendor styles.
