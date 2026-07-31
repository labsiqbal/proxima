# Architecture Decision Records (ADRs)

An **ADR** records **one significant technical decision** — the context, the options
weighed, the choice made, and its consequences. Feature docs explain *how* the system
works; ADRs explain *why* it is built the way it is.

They exist so that anyone who joins later — a human contributor, or an AI agent opening
this repo — can understand the reasoning behind the architecture **without re-litigating
settled decisions or accidentally breaking an intentional constraint.**

## Rules

- **One decision per record.** Keep it focused.
- **Numbered + append-only.** `NNNN-short-title.md`, sequential. Once a decision is
  `Accepted`, the file is **not rewritten**. If the decision changes, write a **new** ADR
  that *supersedes* the old one and update both `Status` lines. The trail of superseded
  ADRs is the project's decision history — that history is a feature, not clutter.
- **Impersonal.** ADRs record decisions, never who knew what. Write "we chose X because
  Y", never "I didn't realise Z". They make the project look considered; they are not a
  competence log.
- **Part of "done."** A change that alters architecture, adds a subsystem, picks a
  dependency, or sets a policy (licensing, security posture, an execution model) ships with
  an ADR in the **same PR** — same rule as the documentation contract in `AGENTS.md`.

## Status values

`Proposed` → under review · `Accepted` → in force · `Superseded by ADR-NNNN` · `Deprecated`.

## Template

```markdown
# ADR-NNNN: <short decision title>

- Status: Proposed | Accepted | Superseded by ADR-XXXX
- Date: YYYY-MM-DD

## Context
The problem and the constraints that force a decision.

## Decision drivers
The requirements/values the decision is judged against.

## Options considered
1. Option — pros / cons.
2. …

## Decision
What we chose, stated plainly, and the core principle behind it.

## Consequences
Positive, negative, and the trade-offs we knowingly accept.

## Related
Supersedes / superseded-by / links to feature docs.
```

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-workflow-execution-model.md) | Workflow execution model — own the orchestration primitives | Accepted |
| [0002](0002-license-agpl.md) | License — AGPL-3.0-or-later, pure commons (DCO, no CLA) | Accepted |
| [0003](0003-evolutionary-architecture.md) | Evolutionary architecture — perpetual beta, contributor-driven | Accepted |
| [0004](0004-durable-task-delegation-boundary.md) | Durable Task delegation is one server-owned boundary | Accepted |
| [0005](0005-restricted-master-runtime-boundary.md) | Restricted Master runtime boundary | Accepted |
| [0006](0006-master-context-is-layered-and-scoped.md) | Master context is layered and scoped | Accepted |
| [0007](0007-master-focus-is-a-durable-execution-boundary.md) | Master Focus is a durable execution boundary | Accepted |
| [0008](0008-external-safe-update-authority.md) | Safe update authority stays outside candidate releases | Accepted |
| [0009](0009-one-durable-master-interface-state.md) | One durable Master interface state | Accepted |
| [0010](0010-preview-authority-requires-verified-connections.md) | Preview authority requires verified managed connections | Superseded by ADR-0011 |
| [0011](0011-preview-containment-membership-and-detached-output.md) | Preview containment membership and detached output | Superseded by ADR-0012 |
| [0012](0012-exact-containment-proof-gates-preview-authority.md) | Exact containment proof gates preview authority | Superseded by ADR-0016 |
| [0013](0013-detached-preview-output-uses-os-sink-helpers.md) | Detached preview output uses OS sink helpers | Superseded by ADR-0018 |
| [0014](0014-automatic-preview-relay-binds-explicit-interfaces.md) | Automatic preview relay binds explicit interfaces | Accepted |
| [0015](0015-preview-authentication-precedes-target-resolution.md) | Preview authentication precedes target resolution | Accepted |
| [0016](0016-live-containment-lineage-gates-preview-authority.md) | Live containment lineage gates preview authority | Accepted |
| [0017](0017-manager-owned-provisional-preview-cleanup.md) | Manager-owned provisional preview cleanup | Superseded by ADR-0020 |
| [0018](0018-preview-status-log-framing-is-bounded.md) | Preview status log framing is bounded | Accepted |
| [0019](0019-launch-time-broker-owns-preview-output.md) | Launch-time broker owns preview output | Superseded by ADR-0021 |
| [0020](0020-preview-lifecycles-use-project-generations.md) | Preview lifecycles use project generations | Superseded by ADR-0024 |
| [0021](0021-preview-supervisors-own-app-scopes.md) | Preview supervisors own app scopes | Superseded by ADR-0025 |
| [0022](0022-preview-log-polling-uses-versioned-deltas.md) | Preview log polling uses versioned deltas | Accepted |
| [0023](0023-preview-supervisor-profiles-are-isolated.md) | Preview supervisor profiles are isolated | Accepted |
| [0024](0024-preview-generations-use-durable-launch-phases.md) | Preview generations use durable launch phases | Accepted |
| [0025](0025-preview-apps-use-launch-specific-cgroups.md) | Preview apps use launch-specific cgroups | Accepted |
| [0026](0026-preview-supervision-upgrades-require-a-drained-legacy-generation.md) | Preview supervision upgrades require a drained legacy generation | Accepted |
| [0027](0027-durable-task-reconciliation-protocol.md) | Durable Task reconciliation protocol | Accepted |
| [0028](0028-linux-first-daily-driver-support.md) | Linux-first daily-driver support | Accepted |
| [0029](0029-canonical-file-targets.md) | Canonical file targets preserve Area identity | Accepted |
| [0030](0030-area-scoped-artifact-media.md) | Area-scoped artifact media and preview routing | Accepted |
| [0031](0031-sandboxed-target-preview-resources.md) | Targeted HTML previews use a sandboxed resource scope | Superseded by ADR-0032 |
| [0032](0032-area-bound-file-preview-origins.md) | Canonical file previews use Area-bound capability origins | Superseded by ADR-0033 |
| [0033](0033-capability-scoped-file-preview-gateways.md) | File previews use capability-scoped execution boundaries | Superseded by ADR-0034 |
| [0034](0034-distinct-tls-area-preview-origins.md) | Active file previews require distinct TLS Area origins | Accepted |
| [0035](0035-frame-bound-area-preview-admission.md) | Area preview admission is frame-bound | Accepted |
