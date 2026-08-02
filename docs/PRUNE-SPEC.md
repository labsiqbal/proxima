# Prune Spec - Proxima to Minimal

- Status: ready to execute (pending owner sign-off)
- Date: 2026-08-02
- Provenance: wayfinder map [#113](https://github.com/labsiqbal/proxima/issues/113); decisions locked in tickets #114-#118, #120-#122. Each section links its source ticket - the detail lives there.

## Goal

Prune Proxima to a minimal single-owner product: governance and dead weight removed, every product feature kept, the chronic error sources fixed, and the project model reworked so Proxima follows the owner's real folders instead of imposing structure.

**Non-goals:** no product feature is deleted (#117); no multi-tenant design ever; the post-prune roadmap (#118: scout tasks, chat decision loop, delivery modes, native methodology concepts) is direction, not part of this prune.

## Inherited constraints

- **Untouchable security core** (#114): realpath jail, Master broker/firewall, script trust, push remote pinning, runner env filtering. No stage may weaken these.
- **Runner-layer seam** stays intact so future model-API runners (direct Anthropic/OpenAI calls) can be added without heart surgery (map: out of scope, constraint inherited).
- **Master runner conformance beyond Codex** is flagged follow-up work, not prune scope (#117).
- Threat model everywhere: single owner via Tailscale.
- Docs are updated in the same commit as the change they describe (owner standing rule).

## Part A - Governance strip (#114, accepted in full in #117)

| # | Item | Scope | Size |
|---|---|---|---|
| A1 | **Delete the safe-updater stack**: `apps/safe_updater/`, `trusted-probes/safe-update/`, `infra/safe-updater/` + its 4 systemd units, `maintenance_status.py`, `routes/self_updates.py`, the ~4,400 lines of safe-update tests, unhook the ingress-lease no-op call sites in 11 modules, rewrite the affected third of `security-boundaries.md`, retire ADR-0008 (mark Superseded, keep the file) | ~10k lines, all inert today (flag off, unenrolled, fail-closed) | ~1.5-2 weeks |
| A2 | **Collapse the feature-flag system**: `design_studio`, `workflow_graph`, `repo_worktrees`, `master_orchestrator` become always-on; delete `features.py`, ~28 gate sites, `apps/web/src/features.ts` mirror, flag tests. If A1 lands first, `safe_self_update` dies with it and zero flags remain | ~400 lines + a mental model | ~2 days |
| A3 | **Delete `docs/locked-repo-policy.md`** (0 code hits); fold its two live paragraphs into `security-boundaries.md` | docs only | hours |
| A4 | **Remove multi-user remnants**: invite 404 surface, `admin_user` role dep, team-mode tests (keep the DB column if dropping it is annoying) | ~200 lines | ~1 day |
| A5 | **Simplify canonical file preview**: passive sandboxed iframe (`sandbox` without `allow-same-origin`) + the existing active-mode owner consent screen; retire the Fetch-Metadata/capability-cookie choreography; mark ADR-0029..0036 Superseded | ~2,500 lines; the only strip touching live behavior | ~1-1.5 weeks |

Fix along the way: `routes/auth.py` docstring drift (says "no expiry", actual TTL is 14 days).

## Part B - Reliability fixes (#115)

| # | Item | Note |
|---|---|---|
| B1 | **Verify the preview Run click** using the STATUS.md resume script (open panel from laptop, click Run, watch for the owner-power dialog, check browser console, grep for `app/start`) | The one live blocker; root still unverified |
| B2 | **Ship the parked 24-file batch** (Files destination ADR-0040, Delegate header, preview auto-port, https-origin fix) the moment B1 is verified | Instantly ships fixes for 5 of the top 12 friction items |
| B3 | **Adopt the systemd user service** (`scripts/install-user`) instead of tmux | Kills the recurring orphan-process "port in use" error permanently |
| B4 | **Global web error surface**: window-level `error` / `unhandledrejection` / failed-dynamic-import handler with a visible toast | Converts the whole "click does nothing" class into diagnosis |
| B5 | **Actionable fail-closed states**: every governance refusal (ownership_unknown, port conflict, conformance rejection) names the next step the owner should take | Governance may refuse; it may never refuse silently |
| B6 | **Persist the owner-power acknowledgement** (currently a component ref that re-asks on every mount; prime suspect for B1) | Small; single-owner appropriate |

## Part C - Project model rework (#120 evidence, #121 decisions)

Principle: **a project is the folder as it exists on disk. Proxima detects and adapts; it never imposes.**

Seven steps, in order (1-2 first - they unblock every real client folder):

1. **Adopt a populated `ops/` as-is**: inventory and register existing content; never require the folder empty; kill the permanent migration attention loop.
2. **Non-mutating link**: no file moves, no `.git/info/exclude` writes at link time; migration only as an explicit, previewed opt-in. Makes the UI promise "Nothing is moved or copied" true.
3. **Per-project Ops path**, picked at link time (default: detected `ops/`, else `.`).
4. **Per-project layout map** for wiki/artifacts/scripts/uploads, seeded by detection from the real tree (today's names as defaults).
5. **Identity from existing docs** (`AGENTS.md`/`README.md`/`HANDOFF.md`); no required Proxima frontmatter. **Memory writes adaptive, default ON**: `log.md`/index written into the project's own detected wiki location; toggleable per project.
6. **Relocate/rebind** a moved or renamed folder (reuse the onboarding picker to re-pin identity).
7. **Symlink policy softened for reads only** (warn-and-skip); writes and migration stay fail-closed.

Plus: **remove reserved-name virtual rerouting** (`wiki`, `scripts`, `tasks`, ... no longer shadow real folders); small migration for legacy Proxima-created projects. Onboarding error paths from #120 part 2 are closed as their owning step lands (the populated-ops loop by step 1, link-time moves by step 2, relocate dead-ends by step 6).

## Part D - UI consolidation (#122)

**Archive and Files merge into one Files destination**: Files browses the real disk; the deliverable ledger becomes a "Deliverables" lens (agent-produced files carry a badge + record panel with lineage, approval, version chain); records whose file is gone live under a "history" filter. The records database and approval flows stay; only the separate navigation destination disappears. One shared ArtifactViewer everywhere (ADR-0040; B2 is the prerequisite).

## Part E - Target workflow (#118, direction recorded for post-prune)

- Decision layer targets **Master chat** (native grilling/qform there, incrementally); interim planning stays in Claude Code + GitHub. Both produce the same artifact: small, sharp delegated Tasks.
- Crew runs AFK and **never asks the owner**; escalations are attention items; a vague ticket is a decision-layer bug.
- Roadmap order: scout task shape, then close the decision loop in Master chat, then delivery modes (direct-PR, then a validation-pipeline mode).
- All absorbed methodology concepts are **rewritten fully native** (no vendoring; upstream skills carry no license).

## Execution order

Each stage ends with: full test suite green (backend + web), CI green, docs updated in the same commits, and a short E2E acceptance check. Never start a stage on a red suite.

- **Stage 0 - Unblock (first, before any strip):** B1 verify Run click, B2 ship the parked batch, B3 systemd adoption. Nothing is deleted while a day of finished work sits uncommitted.
- **Stage 1 - Dead-weight strip:** A1 safe updater, A3 locked-repo doc, A4 multi-user remnants. Tests belonging to deleted code are deleted with it; boundary tests adapted, never skipped.
- **Stage 2 - Flag collapse:** A2 (trivially safe after Stage 1).
- **Stage 3 - Adoption unblock:** C steps 1-2, plus B4 error surface, B5 actionable refusals, B6 ack persistence. E2E: link `wingoh`, `insidevvip`, `BIP` for real - zero moves, zero permanent attention items.
- **Stage 4 - Follow the folder:** C steps 3-5 + reserved-name removal + legacy migration. E2E: BIP's root `wiki/` is the wiki Proxima reads and writes.
- **Stage 5 - Merge surfaces:** Part D (Archive into Files). E2E: every deliverable reachable through Files, approvals intact.
- **Stage 6 - Careful tail:** A5 preview simplification, C steps 6-7. Last because they are the only items touching live security behavior.

Rough total: 5-7 weeks of focused work. Stages 0-2 alone (~2.5 weeks) already remove the daily pain and most of the perceived "ngaco".

## Done means

All stages landed, suite and CI green, the five security-core mechanisms untouched, and the owner's real `_work` folders linked and usable in Proxima without a single imposed file, silent move, or unexplained refusal.
