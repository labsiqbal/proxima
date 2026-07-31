# Durable Task delegation

`TaskDelegationService` is the shared server boundary for creating and starting a
scoped Task. It keeps `jobs` as lifecycle truth while adding routing audit,
idempotency, dependency readiness, and restart recovery.

## Contract

A new scoped Task has:

- one authenticated owner
- one Container id
- one active Area id that belongs to that Container
- one non-system Task-agent profile
- guarded or autonomous in-run execution policy
- one caller-owned idempotency key
- optional Recipe input, origin session/message, and prerequisite Tasks
- a captured Focus marker and epoch when the origin is Master

Creation writes the worker session, job, `task_delegations` row, and every
`task_dependencies` edge in one transaction. Start happens after commit. A caller
that wants immediate execution sets durable start intent, so startup recovery can
retry the same Task after a timeout or process stop.

For a Master origin, creation copies the message's immutable Focus epoch onto the
delegation row. Compatibility callers without an origin message capture the
current durable Master Focus inside the same creation transaction. Fleet capture
is an explicit marker with a null epoch. Projection code reads this durable copy,
not the nullable origin-message link. A migrated Task whose deleted legacy origin
cannot prove Focus still passes the ordinary scoped start contract, while later
projection remains fail closed.

## Idempotency

The service hashes owner plus idempotency key into a lookup identity and stores a
fingerprint of the full request. A full replay is checked before mutable Container,
Area, profile, Recipe, or origin rows are revalidated. It returns the original Task
when the fingerprint matches and returns a conflict when the key names different
work.

A batch is all replay or all new. Mixing existing and new keys is rejected rather
than creating a partial second batch. Master derives a stable per-run key for a
mutation envelope when the model omits one, so duplicate envelopes in the same turn
resolve to the same Tasks.

The Master persistence migration preserves `origin_session_id`,
`origin_message_id`, `job_id`, and every Task edge in place. Existing Alpha-origin
rows continue to point at the same session and message primary keys after that
session becomes Master. Restart recovery therefore sees the same committed start
intent and never creates replacement ownership rows.

## Dependencies

Dependencies point from a Task to a prerequisite and require either `review` or
`done`. SQLite rejects self-edges, duplicates, and cycles. The prerequisite foreign
key is restrictive, so deletion cannot erase the reason a dependent is waiting.

A requested but unready dependent remains `queued`. `jobs.blocked_reason` and the
delegation audit explain the exact prerequisite and current state. Completion,
review, failure, cancellation, and owner verdict paths refresh dependents. The
queued-to-running claim remains the concurrency mutex, so repeated notifications do
not create a second run. Failed and cancelled prerequisites produce a stable
`Blocked by prerequisite Task #...` reason rather than an unexplained queue item.

When unattended Master mode is enabled, `MasterSupervisor` asks this service to
start queued work. Blocked Tasks do not consume a supervisor capacity slot, and the
supervisor does not reproduce dependency, Area, worktree, execution, or landing
state machines. Important status changes are projected through
`MasterProjectionService`; `jobs` and this dependency graph remain truth.
Every Master start revalidates its canonical owner, Master session, Container, Area,
worker session, Task-agent profile, and delegation audit. The queued job claim,
Master capacity reservation, first linear run, and optional unattended turn
reservation are serialized with `BEGIN IMMEDIATE`. Graph starts reserve the job
before dispatch and limit all ready branches against the same global Master active
slot count.

## Landing behavior

Execution and landing policies are separate:

- Guarded or autonomous controls the Task-agent's in-run behavior.
- A repo Task runs in its external worktree and always reaches diff review before
  local merge.
- A delegated Ops Task runs in physical `ops/` and finishes directly because its
  changes already landed in place.
- An explicit Recipe review gate may still pause either kind of Task as an in-run
  decision.

Historical project-less Work jobs keep their old scratch behavior and do not receive
a scoped delegation audit.

## Restart recovery

Startup scans committed delegation rows with requested but unreconciled starts.
Linear start claims the job and inserts its first run atomically. Graph recovery also
re-enters the idempotent graph dispatcher when a process stopped after the job became
`running` but before a node run was committed. Existing node claims are reused, and
an all-trigger graph is finalized when no work remains.

Feature-off startup does not instantiate the Master supervisor or projection
service and does not claim Master-owned rows. Enabling the flag later reuses the
same committed Tasks and dependency edges. A preserved legacy queued worker run is
claimed only after its full Master scope and dependency readiness are revalidated,
and its Task is promoted to running in the same transaction as the run claim.

## External mutation reconciliation

`jobs` remains lifecycle truth, but owner actions can change it while no worker run
is active. Review approval/rejection and checkpoint restore append a durable
`job.update` to the Task session inside the same database transaction as the state
change. Projectable review transitions also enqueue `task_projection_outbox` from
that shared event boundary. The Task verdict and durable Master delivery intent
therefore commit together, while projection message/event/ledger delivery and all
stream notifications happen only after commit. A failed delivery remains replayable.
Legacy Tasks without safe Focus attribution keep their Task verdict and retain an
explicit failed-attribution outbox state instead of publishing an unattributed
Master message. Mounted Task detail subscribes to the shared invalidation event;
running polling is not the authority for externally mutable states.

Projection idempotency uses a database-maintained monotonic Task generation that
advances only when the canonical projected state changes. Ordinary linear-step,
graph-node, timestamp, or same-status progress reuses the current generation, while
Running to Review to Running receives three distinct generations. Duplicate
delivery of one transition remains a no-op. Status and recovery outboxes are
processed in Task-event order.

Checkpoint restore also appends its audit record and, for a Master-origin Task, one
bounded `task_recovery_outbox` intent in the restore transaction. Recovery marks
only obsolete unpublished status projections as superseded, linked to the recovery
Task event. Recovery audit intents are append-only and each publishes exactly once
in Task-event order as `master.task.recovered` when it remains normally orderable.
Legacy upgrade gaps are retained in an immutable causal ledger instead of replayed
after a later publication or rewriting an already-projected reversal. At most one
bounded `master.task.recovery_history_corrected` marker per Task summarizes all
known gap counts and Task-event ranges after current-state projection. Each normal
entry records actor, checkpoint, prior/new status, discarded progress, and conflicts
through bounded server-owned summaries rather than arbitrary graph identifiers. A
legacy Task with unavailable Focus still restores and exposes every
failed-attribution repair intent, but publishes no unattributed Master history.
Git preflight completes before the immediate write transaction, then
the restore rereads checkpoint, conflict, job, run, and node state under that lock.
All validation and durable writes complete before a job worktree reset. A post-reset
failure compensates to the original worktree commit and rolls back the database
transaction, so Task, Fleet, history, and the worktree cannot commit contradictory
states.

`scripts/verify_task_reconciliation_browser.py` exercises mounted review approval,
mounted checkpoint restore, and durable recovery history in Chromium. Its three
after screenshots are mandatory and retained under
`/tmp/no-mistakes-evidence/task-reconciliation` unless an explicit evidence
directory is configured. Database, workspace, runner profile, and browser profile
state use a system temporary directory that is removed on interruption.

## Adding another caller

1. Resolve user-facing slugs or selections to database ids without accepting paths.
2. Construct `TaskDelegationRequest` with one exact Container and Area.
3. Require or derive a stable idempotency key for the logical caller action.
4. Use `create_and_start` for one Task or `create_batch` for an atomic DAG.
5. Do not insert sessions, jobs, delegation rows, or dependency edges directly.
6. Surface `DelegatedTask.created`, `started`, and `blocked_reason` honestly.
7. If the caller defers its immediate start attempt, set durable start intent with
   `defer_start=True`.
8. Add an integration test for full replay, partial failure rollback, restart between
   commit and start, and the selected Area's landing behavior.

Status transitions that introduce a new route to `review`, `done`, `failed`, or
`cancelled` must call `prerequisite_changed`. Deletion code must preserve the
restrictive prerequisite contract and return an owner-facing blocker.
