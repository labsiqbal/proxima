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
