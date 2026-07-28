# ADR-0006: Master context is layered and scoped

- Status: Accepted
- Date: 2026-07-27

## Context

The Master orchestrates work across many Containers. A single fleet-wide graph
would rebuild on every change, go stale quickly, and let one Container's context
bury another's. Live "what is running now" truth also cannot come from a graph
that rebuilds after the fact.

## Decision drivers

- independently fresh slices of durable knowledge
- no cross-Container context burial ("ketiban")
- Live state always current and independent of graph availability
- path-free, budgeted queries with provenance
- local-only structural extraction by default; no cloud semantic egress without
  an explicit future captain policy

## Options considered

1. One fleet-wide Graphify graph - simple query surface, but shared rebuild
   pressure and leakage risk.
2. Scoped layers (Fleet registry, per-Container Knowledge, per-Area Code) with
   Live state from SQLite - more routing intelligence, independent freshness.
3. Always inject full Container trees into the Master prompt - maximal recall,
   unbounded tokens and injection risk.

## Decision

The Master's durable context is **layered and scoped**, not one graph:

1. **Fleet registry** - which Containers exist and their identity/status. Cheap
   structured store, always current.
2. **Per-Container Knowledge graph** - Graphify over that Container's physical
   Ops allowlist only (identity, curated knowledge/decisions, reports, durable
   artifact metadata). At most one Knowledge graph per Container.
3. **Per-repo Code graph** - Graphify over one registered code Area.

The Master routes each need to the relevant layer via a typed context router.
**Live state** (running, blocked, green, status) is always read from SQLite, never
from any graph. Mixed requests call a bounded set of exact layers and never merge
fleet-wide graphs. Focused Knowledge/Code results are scope-checked so another
Container's nodes cannot appear.

Structural extraction is local. Cloud semantic egress stays disabled unless an
explicit future captain policy enables a real adapter; configured credentials
alone never unlock egress.

## Consequences

- The Master must classify which layer to query - that routing is core product
  behavior (Group 11).
- Knowledge builds refuse nested VCS trees, secret-like paths, Task transcripts,
  and legacy Ops roots that still overlap Code Areas.
- Graph absence or staleness degrades context visibility without blocking Tasks
  or Live state.
- Focus epochs and history projection (Slice 5) build on this layering but are
  separate delivery groups.

## Related

- Build map: Master orchestrator Slice 4
- Feature docs: `docs/CAPABILITIES.md`, `docs/reference/architecture.md`
- Security: `docs/security-boundaries.md`
