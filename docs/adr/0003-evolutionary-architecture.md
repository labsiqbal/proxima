# ADR-0003: Evolutionary architecture — perpetual beta, contributor-driven

- Status: Accepted
- Date: 2026-07-14

## Context

Proxima is a control plane for AI coding agents. The thing it sits on top of — agent and
model capability — changes every few weeks. A product designed to be *finished* against a
fixed spec would be obsolete on arrival: "done" and "current" cannot both hold here.

The intended posture is the opposite of a finished product: Proxima is **not meant to be
"done."** It is a living, open-source project that keeps adapting as the agents it drives
evolve, developed by a mix of **human and AI contributors**. This ADR records that as an
explicit operating model rather than an unstated vibe, because the model dictates concrete
engineering rules that everything else follows.

## Decision drivers

1. **Adapt to fast, external churn** without periodic re-architecture.
2. **Contributor-legible** — a newcomer (human or AI) can contribute correctly from the docs
   alone, without tribal knowledge.
3. **Coherence as it grows** — open, continuous contribution must not degrade into an
   incoherent pile; growth needs guardrails, not gates.

## Options considered

- **Conventional "build-to-spec, then maintain."** Familiar, but assumes a stable target.
  Against a substrate that shifts monthly it guarantees drift and rewrites. Rejected.
- **Evolutionary architecture** (a la *Building Evolutionary Architectures*): design for
  change as the primary requirement — stable core behind seams, change-enabling discipline
  (docs, decision records, fitness gates), and low-friction contribution. Chosen.

## Decision

Adopt **evolutionary architecture** as Proxima's explicit operating model. Its principles are
binding and are the *reason* the other rules exist:

1. **Quarantine churn behind stable seams.** What changes fastest (agent/model/runner
   capability) lives behind a stable interface — the **ACP boundary + runner registry**. What
   must stay solid (data model, orchestration, state, review, security posture) is the **core
   Proxima owns**. Features are built on owned primitives, not on a specific runner's
   volatile features. (This is the general form of the decision in ADR-0001.)
2. **Discipline enables change — more rigor, not less.** Software built to change safely needs
   *stronger* guardrails than software built to freeze. The docs, ADRs, and fitness gates are
   not overhead; they are the machinery that makes continuous change safe.
3. **Docs are part of "done."** Every change ships its documentation in the same PR
   (`AGENTS.md` contract); the *why* is recorded in **ADRs** so decisions aren't re-litigated.
4. **A single scope filter.** Every idea is judged against the five **DNA pillars** and
   anti-goals in `docs/ROADMAP.md`. This keeps growth coherent without gatekeeping people.
5. **Human and AI contributors, one rulebook.** Both are first-class; both follow the DNA
   filter, the documentation set, and the DCO (`CONTRIBUTING.md`). The docs are written to be
   machine-legible precisely so agents can contribute correctly.

## Consequences

**Positive**

- The project can absorb AI/runtime updates by changing only the volatile edge, never the
  owned core — the operational meaning of "future-proof".
- It is contributable by newcomers and agents from the documentation alone.
- Decisions accrete as an auditable trail (superseding ADRs), so the project's evolution is
  legible instead of mysterious.

**Negative / accepted trade-offs**

- **Permanent documentation tax.** Docs-as-done is non-negotiable and never "over"; letting it
  slip is the fastest way to rot an evolutionary project.
- **Upfront investment in seams and primitives** before features feel fast (see ADR-0001).
- **"Never done" requires comfort with permanent work-in-progress** — there is no finish line
  to declare, by design.

## Related

- Informs: ADR-0001 (own the orchestration primitives), ADR-0002 (AGPL commons).
- Governed by: `AGENTS.md` (constitution), `docs/ROADMAP.md` (DNA filter), `CONTRIBUTING.md`.
