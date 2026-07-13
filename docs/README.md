# Proxima — Documentation Hub

**Start here.** This is the single entry point to every doc. It separates the
**durable reference** (the source of truth — kept accurate) from **living logs**
(session notes, snapshots) and **historical** point-in-time docs (design plans,
audits — kept for context, not maintained).

> **One-line what-it-is:** a self-hosted, single-user **control plane for AI coding
> agents** — a FastAPI backend + React PWA that drives Claude Code / Codex / Gemini /
> Hermes over ACP. It ships no model and no credentials of its own.

---

## 📘 Reference — the source of truth

Keep these accurate. If the code changes, update the matching doc (the API and
Database docs update themselves — see [Keeping docs fresh](#keeping-docs-fresh)).

| Doc | What it answers | Maintained |
| --- | --- | --- |
| [reference/tech-stack.md](reference/tech-stack.md) | What is it built with? (backend, frontend, runtime, ops) | by hand |
| [reference/architecture.md](reference/architecture.md) | How is it structured, and how do the main flows work? | by hand |
| [reference/api.md](reference/api.md) | Every HTTP/WebSocket endpoint | **generated** |
| [reference/database.md](reference/database.md) | Every table, column, index | **generated** |
| [CAPABILITIES.md](CAPABILITIES.md) | What every feature does + why (code-derived feature map) | by hand |
| [reference/feature-map.md](reference/feature-map.md) | Per-feature grid: where the code lives, tables/events touched, relations, status/flag | by hand |
| [product/vision.md](product/vision.md) · [product/core-flows.md](product/core-flows.md) | Product direction + the intended user flows | by hand |
| [security-boundaries.md](security-boundaries.md) · [prompt-injection-hardening.md](prompt-injection-hardening.md) | Threat model + hardening | by hand |
| [DESIGN-STUDIO.md](DESIGN-STUDIO.md) | Design Studio blueprint (enabled; on by default in dev, off in the packaged install) | by hand |

**Operations & contributing:** [installation.md](installation.md) ·
[backup.md](backup.md) · [development-tools.md](development-tools.md) ·
[locked-repo-policy.md](locked-repo-policy.md) · [RELEASING.md](RELEASING.md) ·
[../CONTRIBUTING.md](../CONTRIBUTING.md)

**Root-level intros (audience-facing):** [../README.md](../README.md) (users) ·
[../PRODUCT.md](../PRODUCT.md) (product pitch) · [../QUICKSTART.md](../QUICKSTART.md)
(get running) · [../DESIGN.md](../DESIGN.md) (design language) ·
[../AGENTS.md](../AGENTS.md) (rules for agents working in this repo).

## 📝 Living logs — current state, not reference

These change constantly and describe *now*, not the durable design. Read for the
latest state; don't treat as spec.

| Doc | Role |
| --- | --- |
| [ROADMAP.md](ROADMAP.md) | What's next / planned. |
| `STATUS.md` · `bugfix-log.md` · `wiki/` | Maintainer-local living logs (untracked; present on maintainer machines only). |

## 🧭 Pending design

- **Meeting Mode** — cross-model critique loop, approved & parked on branch
  `feat/meeting-mode` (not in `main`). Its design note lives in `docs/plans/`, which is
  **git-ignored** (internal planning docs are kept out of the public repo); consult it
  locally before reviving that feature.

## 🗄️ Historical

Frozen audit/history docs live in the maintainer-local `archive/` folder
(untracked; maintainer machines only).

---

## Keeping docs fresh

The two reference docs that go stale fastest — **API** and **Database** — are
generated straight from the code, so they can't drift:

```bash
# from the repo root, after changing routes or the DB schema:
python3 scripts/gen_docs.py
# (or, if the bare python can't import the app package:)
apps/api/.venv/bin/python scripts/gen_docs.py
```

This rewrites `reference/api.md` (parsed from the route decorators) and
`reference/database.md` (introspected from a throwaway DB built with the app's own
`init_db` + migrations). Both files are marked **GENERATED — do not edit by hand**.

For the hand-maintained docs, the rule of thumb: **change the code and its doc in the
same commit.** When you add a feature, update [CAPABILITIES.md](CAPABILITIES.md); when
you change a flow or component, update [reference/architecture.md](reference/architecture.md);
when you change a dependency, update [reference/tech-stack.md](reference/tech-stack.md).

## Doc map at a glance

```text
docs/
├── README.md              ← you are here (the hub / single entry point)
├── reference/             ← SOURCE OF TRUTH (keep accurate)
│   ├── tech-stack.md          hand-maintained
│   ├── architecture.md        hand-maintained (structure + flows)
│   ├── api.md                 GENERATED  ← scripts/gen_docs.py
│   └── database.md            GENERATED  ← scripts/gen_docs.py
├── CAPABILITIES.md        ← feature map (source of truth for "what does X do")
├── product/               ← vision + core flows
├── *.md (ops/dev/security)← installation, backup, developing, security…
├── plans/                 ← pending design (meeting-mode)
└── ROADMAP.md             ← what's next (living)
```
