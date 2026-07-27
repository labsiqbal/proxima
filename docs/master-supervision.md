# Master supervision and durable projections

Master supervision has two separate authorities:

- `MasterSupervisor` may start eligible queued Master Tasks while unattended mode
  is enabled and capacity and budget remain.
- Satpam alone detects stuck work, steers it, restarts it, or escalates it.

There is no Master progress detector or recovery loop.

## Queue-start contract

The supervisor is instantiated only when `feature_master_orchestrator` is enabled.
The flag defaults off. A defensive feature check also makes a manually retained
instance inert after the flag is disabled.

Each tick:

1. Resolves the canonical project-unbound `sessions.mode='master'` row.
2. Enforces the configured `master_max_parallel`
   (`PROXIMA_MASTER_MAX_PARALLEL`, default 3) active-run limit and unattended turn
   and wall-clock budgets.
3. Reads only queued Tasks whose `created_by` matches that Master session owner.
4. Calls `TaskDelegationService.start`, which owns dependency readiness, exact
   Container and Area binding, worktree policy, and the transactional
   queued-to-running claim.
5. Skips blocked Tasks without consuming a start slot, so an earlier blocked row
   cannot starve later eligible work.

Failed or cancelled prerequisites leave the dependent queued with a durable
`jobs.blocked_reason`. Cycles are rejected by the delegation service and SQLite
triggers before a supervisor tick can see them. A process-local nonblocking tick
mutex plus the database claim prevents duplicate starts.

## Projection boundary

`MasterProjectionService` is the only writer for asynchronous Task and supervision
messages in the durable Master thread. It reads existing authoritative rows:

- `jobs`, `runs`, `node_states`, `job_checkpoints`
- `attention_items`
- `satpam_interventions`

It writes:

- one concise `messages` row authored by Master
- one named event on the existing Master session stream
- one `master_projections` idempotency/link row

`master_projections` is not lifecycle truth. Its unique owner and projection key
links a message and event to a source table and row. Reconciliation after restart
can safely retry because an existing key produces no second message or event.
Raw token, reasoning, and tool delta events are never projected.

Review-ready payloads include the stable Task, Container, Area, and latest
checkpoint ids. Attention and Satpam payloads include their source row ids and a
stable `toast_key`, so a later shared frontend provider can update the Tasks board
and show one transient toast without another polling endpoint.

## Session event contract

Task events:

- `master.task.started`
- `master.task.review_ready`
- `master.task.completed`
- `master.task.failed`
- `master.task.cancelled`
- `master.task.blocked`

Supervision events:

- `master.attention.required`
- `master.supervisor.outcome`
- `master.satpam.steered`
- `master.satpam.restart_queued`
- `master.satpam.restarted`
- `master.satpam.recovery_failed`
- `master.satpam.escalated`

These are named events on
`GET /api/sessions/{master_session_id}/events/stream`. The existing global
`events.id` cursor remains the resume key. Reconnect with the last received id
delivers only later rows; replay from an earlier cursor returns the same one event
per projection and creates no new durable data.

Events and projected chat messages report state only. They do not approve review,
landing, restart, or Attention gates and are never accepted as control input.

## Compatibility

`alpha_supervisor.py` is a deliberate import alias to `MasterSupervisor` for one
compatibility release. It has no separate loop or state. Migrated Alpha messages
remain in the same session primary key after that session becomes Master, and new
projections append chronologically to that thread.
