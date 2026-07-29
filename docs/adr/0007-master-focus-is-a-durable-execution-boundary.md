# ADR-0007: Master Focus is a durable execution boundary

- Status: Accepted
- Date: 2026-07-29

## Context

The Master keeps one durable conversation while operating across multiple
Containers. Reusing one provider thread or rebuilding history from the whole
conversation would let instructions and model state from an earlier Container
affect a later turn. Browser-only Focus state would also be lost across restart
and could race concurrent sends.

Task and supervision projections arrive after their originating Master turn.
Their Focus attribution must survive deletion of that turn's message or run.

## Decision drivers

- deterministic isolation across Container changes and restarts
- one canonical Master session and chronological thread
- transactional sends and optimistic concurrent Focus changes
- immutable attribution for messages, runs, and delayed projections
- fail-closed behavior for generic run producers and incomplete legacy origins

## Options considered

1. Keep Focus in the browser and reuse the provider thread - simple, but neither
   durable nor isolated.
2. Create one Master session per Container - isolates history, but fragments the
   canonical thread and changes the product identity.
3. Keep one Master session with durable Focus epochs and rebuild every restricted
   turn from its captured epoch - preserves identity and makes isolation explicit.

## Decision

The Master keeps one project-unbound session. `master_focus_state` stores its
optimistically versioned current and pending Focus. A Container Focus owns one
open `master_focus_epochs` row; Fleet mode has no epoch.

Every accepted Master send captures the current epoch on its user message and
run in the same transaction. Explicit cross-Container sends change Focus and
enqueue atomically. Generic session run producers reject Master sessions, and a
database trigger rejects any Master run that is not a Focus-captured Master turn.

Task delegation copies the captured epoch and an explicit captured marker onto
its durable audit row. Delayed Task and supervision projections read that copy,
not the nullable origin-message link. Fleet attribution is represented by a
captured marker with a null epoch, while missing legacy attribution fails closed.

Each restricted runner turn starts with a recycled process and a transcript
rebuilt only from the run's captured epoch. A running turn blocks another send
and permits only one durable pending Focus, which applies after the last active
turn closes.

## Consequences

- Focus changes append durable boundary messages and `master.focus.changed`
  events.
- Container deletion can close the live Focus without erasing historical epoch
  identity.
- Deleting an origin message or run cannot reclassify a later Task projection.
- Legacy Master Tasks without provable Focus capture remain durable but cannot
  produce an unattributed projection.
- Every new Master run producer must use the Master message transaction rather
  than a generic session endpoint.

## Related

- ADR-0005: restricted Master runtime boundary
- ADR-0006: Master context is layered and scoped
- Feature docs: `docs/master-supervision.md`,
  `docs/master-persistence-migration.md`, `docs/reference/architecture.md`
