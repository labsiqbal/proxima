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

Creation writes the worker session, job, `task_delegations` row, and every
`task_dependencies` edge in one transaction. Start happens after commit. A caller
that wants immediate execution sets durable start intent, so startup recovery can
retry the same Task after a timeout or process stop.

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
not create a second run.

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
