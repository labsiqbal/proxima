# ADR-0027: Durable Task reconciliation protocol

- Status: Accepted
- Date: 2026-07-31

## Context

Task state changes can originate from workers, inline Attention approval,
checkpoint restoration, and supervision. Task detail, Master history, Fleet
grouping, and Attention must converge even when a process stops between the state
change and its projection. Recovery history also has to survive old databases whose
publication order was already incomplete or reversed.

Deletion creates another boundary. Live Task sessions, events, jobs, and outbox rows
can cascade away while delivered Master messages and events remain. Preserving that
history requires exact source identity without treating an arbitrary graph-node
session as the Task session.

## Decision drivers

1. A committed Task transition must have one durable, replayable projection path.
2. Projection delivery must preserve Task-event order and exactly-once identity.
3. Checkpoint recovery history must remain human-readable and append-only.
4. Legacy publication gaps must be contained without rewriting delivered history.
5. Deletion must preserve exact causal identity before foreign-key cascades.
6. Missing historical identity must be reported explicitly, never guessed.

## Options considered

1. Project directly inside every Task mutation. This gives immediate updates but
   couples Task commits to Master attribution and makes crash recovery inconsistent.
2. Recompute Master and Fleet from current Task rows only. This converges status but
   loses ordered human-readable recovery history and cannot explain legacy
   publication reversals.
3. Commit a Task event and projection or recovery outbox intent together, then
   deliver in durable Task-event order. Preserve legacy gaps and deletion evidence
   in immutable ledgers. This adds repair state and migration complexity but keeps
   lifecycle truth and audit history coherent.

## Decision

Every externally mutable Task transition writes its canonical Task event and durable
projection intent in the same database transaction. Projection happens only after
commit. A monotonic status generation suppresses same-status progress while keeping
distinct status cycles, and the Task event identifies each projection-worthy
transition.

Projection and recovery outboxes process strictly by Task-event order. Checkpoint
recovery may supersede only obsolete unpublished status projections. Recovery audit
intents remain append-only and normally publish exactly once. Missing Focus or
attribution produces explicit repair state without rolling back the Task mutation or
publishing unattributed Master history.

Legacy predecessors that can still be ordered return to normal delivery. A
predecessor whose successor already published is retained as an immutable causal
gap and is never replayed after that successor. Existing delivered correction
messages, events, marker rows, and exact gap coverage remain immutable. At most one
new bounded aggregate correction marker per Task summarizes still-uncovered gaps
after the authoritative current-state projection.

Before deleting a Task job, authoritative Task session, referenced Task event, or
recovery outbox, database triggers copy source, gap, correction, and coverage
identity into detached immutable history. `jobs.session_id` and a consistent set of
outbox-referenced Task events are authoritative Task-session provenance. Generic
`sessions.job_id` membership is not. If no authoritative Task-session provenance
survives, the tombstone retains `NULL` and an immutable bounded loss row records the
reason. A later authoritative boundary may complete a partial tombstone, but no
boundary may invent or replace captured identity.

## Consequences

Positive:

- Task, Master, Fleet, Attention, and mounted detail share one replayable lifecycle.
- Crash and retry behavior preserves ordering without duplicate messages or toasts.
- Recovery history remains readable while legacy ordering damage stays explicit.
- Deletion cannot silently detach surviving Master history from its causal sources.

Negative:

- Projection state includes outboxes, repair states, legacy-gap ledgers, correction
  coverage, tombstones, and bounded identity-loss records.
- Legacy databases may retain explicit gaps or missing Task-session identity instead
  of presenting a falsely complete history.
- Migration and deletion paths must preserve stricter immutability and ordering
  invariants than ordinary live Task data.

## Related

- [Durable Task delegation boundary](0004-durable-task-delegation-boundary.md)
- [Master supervision and durable projections](../master-supervision.md)
- [Task delegation recovery contract](../task-delegation.md)
- [Architecture flow](../reference/architecture.md#1d-cross-surface-task-reconciliation)
